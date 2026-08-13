# app/infrastructure/parser/openlists/records.py
"""
Таблица или JSON-массив → наши доменные записи.

Здесь же — вытаскивание того, чего в самих строках нет: названия
образовательной программы, кода направления, формы обучения и числа мест.
Всё это вузы печатают в заголовке над списком, поэтому разбирается оно
регулярками по тексту, а не по разметке.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

from app.domain.models import Application, SubmissionStats
from app.infrastructure.parser.openlists.columns import (
    field_for_header,
    field_for_key,
    looks_like_ranking,
    map_headers,
)
from app.infrastructure.parser.openlists.tables import HtmlTable

#: Код направления подготовки магистратуры: 38.04.02, 01.04.02 и т. п.
#: Третья пара цифр «04» и есть признак магистратуры — бакалавриат идёт с «03».
SPECIALITY_RX = re.compile(r"\b(\d{2}\.04\.\d{2})\b")

#: Направление не указано — но department_code обязателен (это внешний ключ и
#: он же exam_id в Монте-Карло). Общая заглушка на вуз лучше, чем пропуск
#: программы: конкурс от этого не смешивается, программы всё равно разные.
UNKNOWN_SPECIALITY = "00.04.00"

_PLACES_RX = re.compile(
    r"(?:количество\s+)?(?:бюджетных\s+)?мест[а-я]*\s*(?:\([^)]*\))?\s*[:—–-]?\s*(\d{1,4})"
    r"|(?:кцп|контрольные цифры приема|контрольные цифры приёма)\s*[:—–-]?\s*(\d{1,4})"
    r"|(\d{1,4})\s+(?:бюджетн\w+\s+)?мест",
    re.I,
)

_FORM_RX = re.compile(r"(очно-заочн\w+|заочн\w+|очн\w+)\s*(?:форма|формы)?", re.I)

_CONSENT_YES = {"да", "+", "есть", "подано", "подана", "имеется", "true", "1", "✓", "v", "yes"}
_CONSENT_NO = {"нет", "-", "—", "–", "", "false", "0", "не подано", "отсутствует", "no"}


@dataclass
class ProgramFacts:
    """Что удалось узнать о самом конкурсе, а не о его участниках."""
    program_name: str
    speciality_code: str
    education_form: str
    num_places: int


def to_int(value: Any, default: int = 0) -> int:
    """
    Первое целое в значении. «96,5» → 96, «—» → default.

    Дробные баллы округляются вниз намеренно: дальше всё считается в int16
    (Монте-Карло), а половина балла на исход конкурса не влияет.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"-?\d+", str(value or "").replace(" ", ""))
    return int(m.group()) if m else default


def parse_consent(value: Any) -> bool:
    """«Да» / «+» / «Оригинал» → True. Пусто, «Нет», «—» → False."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower().replace("ё", "е")
    if text in _CONSENT_YES:
        return True
    if text in _CONSENT_NO:
        return False
    # Свободная формулировка: «согласие подано», «оригинал документа».
    return bool(re.search(r"подан|оригинал|соглас|принят", text)) and not re.search(r"не\s+подан|отозв", text)


def parse_places(text: str) -> int:
    """Число мест из заголовка над списком. 0 — если вуз его не напечатал."""
    m = _PLACES_RX.search(text or "")
    if not m:
        return 0
    return next((int(g) for g in m.groups() if g), 0)


def parse_education_form(text: str) -> str:
    m = _FORM_RX.search(text or "")
    return m.group(1).lower() if m else ""


def parse_speciality(text: str) -> str:
    m = SPECIALITY_RX.search(text or "")
    return m.group(1) if m else ""


def clean_program_name(text: str, fallback: str = "") -> str:
    """
    Заголовок над таблицей → название образовательной программы.

    Из «Направление 38.04.02 Менеджмент. Образовательная программа
    «Маркетинг», очная форма обучения, бюджет, 25 мест» нужно «Маркетинг».
    Разметки, по которой это можно взять надёжно, у вузов нет, поэтому режем
    по тем самым словам, которыми они это подписывают.
    """
    text = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()
    if not text:
        return fallback.strip()

    # Название программы обычно идёт после слова-указателя; берём последний
    # такой указатель — он ближе всего к самому названию.
    marker = None
    for m in re.finditer(
        r"(образовательн\w+ программ\w+|программ\w+ магистратуры|магистерск\w+ программ\w+)\s*:?\s*",
        text, re.I,
    ):
        marker = m
    if marker:
        text = text[marker.end():]

    # Кавычки — самый надёжный признак границ названия.
    quoted = re.search(r"[«\"]([^»\"]{3,120})[»\"]", text)
    if quoted:
        return quoted.group(1).strip()

    # Иначе отрезаем всё служебное: код направления, форму, финансирование, места.
    # \b в начале обязателен: без него «рыночная аналитика» режется по «очная»,
    # спрятавшемуся внутри слова.
    text = SPECIALITY_RX.sub(" ", text)
    text = re.split(
        r"(?:,|\.|\||—|–)?\s*\b(?:очн\w+|заочн\w+|очно-заочн\w+|бюджет\w*|контракт\w*|"
        r"платн\w+|мест[а-я]*\s*[:—–-]?\s*\d|количество мест|кцп|списки|список)\b",
        text, maxsplit=1, flags=re.I,
    )[0]
    text = text.strip(" -–—:.,;«»\"")
    return text or fallback.strip()


def program_facts(source_text: str, fallback_name: str = "") -> ProgramFacts:
    """Заголовок/подпись списка → всё, что мы знаем о конкурсе."""
    return ProgramFacts(
        program_name=clean_program_name(source_text, fallback_name),
        speciality_code=parse_speciality(source_text) or parse_speciality(fallback_name) or UNKNOWN_SPECIALITY,
        education_form=parse_education_form(source_text),
        num_places=parse_places(source_text),
    )


# ── строки → заявки ────────────────────────────────────────────────────────
def _application(rec: dict[str, Any], program_code: str) -> Application | None:
    applicant_id = str(rec.get("applicant_id") or "").strip()
    if not applicant_id or not re.search(r"\w", applicant_id):
        return None

    vi = to_int(rec.get("vi_score"))
    subject1 = to_int(rec.get("subject1_score"))
    subject2 = to_int(rec.get("subject2_score"))
    id_ach = to_int(rec.get("id_achievements"))
    total = to_int(rec.get("total_score"))

    # Не все вузы печатают все три колонки. Восстанавливаем недостающее из
    # того, что есть: Монте-Карло считает конкурсный балл как vi + ИД, и если
    # vi оставить нулём при известном итоге, человек пойдёт в конкурс с нулём.
    if not vi:
        vi = max(total - id_ach, 0) if total else subject1 + subject2
    if not total:
        total = vi + id_ach

    return Application(
        program_code=program_code,
        applicant_id=applicant_id,
        total_score=total,
        vi_score=vi,
        subject1_score=subject1,
        subject2_score=subject2,
        id_achievements=id_ach,
        target_id_achievements=0,
        priority=to_int(rec.get("priority"), default=1),
        consent=parse_consent(rec.get("consent")),
        review_status=str(rec.get("review_status") or "").strip(),
    )


def _dedupe(applications: Iterable[Application]) -> list[Application]:
    """
    Одна заявка на человека в списке.

    Строка может повториться (у ИТМО, например, одна и та же заявка приходит в
    общем конкурсе и в конкурсе по квоте). В базе ключ — (программа,
    абитуриент), и дубль в bulk-upsert валит вставку целиком.
    """
    by_id: dict[str, Application] = {}
    for app in applications:
        current = by_id.get(app.applicant_id)
        # Оставляем строку с большим баллом: она соответствует основному конкурсу.
        if current is None or app.total_score > current.total_score:
            by_id[app.applicant_id] = app
    return list(by_id.values())


def table_to_applications(table: HtmlTable, program_code: str) -> list[Application]:
    """Таблица → заявки. Пусто, если таблица не похожа на рейтинговый список."""
    fields = map_headers(table.headers)
    if not looks_like_ranking(fields):
        return []

    apps: list[Application] = []
    for row in table.rows:
        rec = {f: v for f, v in zip(fields, row) if f}
        app = _application(rec, program_code)
        if app:
            apps.append(app)
    return _dedupe(apps)


def json_rows_to_applications(rows: Sequence[dict], program_code: str) -> list[Application]:
    """Массив объектов из JSON-ответа → заявки."""
    apps: list[Application] = []
    for row in rows:
        rec: dict[str, Any] = {}
        for key, value in row.items():
            field = field_for_key(key)
            # Первое совпадение выигрывает: у API часто есть и 'code', и 'id',
            # и второй — идентификатор строки, а не человека.
            if field and field not in rec:
                rec[field] = value
        app = _application(rec, program_code)
        if app:
            apps.append(app)
    return _dedupe(apps)


def looks_like_json_ranking(rows: Sequence[dict]) -> bool:
    """Есть ли в массиве объектов те же обязательные колонки."""
    if not rows:
        return False
    fields = {field_for_key(k) for k in rows[0].keys()}
    return looks_like_ranking(list(fields))


def make_stats(program_code: str, num_places: int, applications: Sequence[Application],
               generated_at: datetime) -> SubmissionStats:
    return SubmissionStats(
        program_code=program_code,
        num_places=num_places,
        num_applications=len(applications),
        generated_at=generated_at,
    )


def header_fields(headers: Sequence[str]) -> list[str | None]:
    """Публичная обёртка для диагностики: какие колонки мы узнали."""
    return [field_for_header(h) for h in headers]


# ── когда список сформирован ───────────────────────────────────────────────
_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

#: Слова, которыми вузы подписывают дату публикации. Дата без такой подписи
#: почти всегда чужая (срок подачи, дата приказа), и брать её нельзя.
_WHEN_RX = re.compile(
    r"(?:по состоянию на|сформирован\w*|актуальн\w* на|обновлен\w*|дата формирования|"
    r"последнее обновление)\D{0,20}?"
    r"(\d{1,2})[.\s]+(\d{1,2}|[а-яё]+)[.\s]+(\d{4})(?:\D{0,12}?(\d{1,2}):(\d{2}))?",
    re.I,
)


def parse_generated_at(text: str) -> datetime | None:
    """
    «по состоянию на 05.08.2026 16:00» → datetime (МСК, tz-naive).

    None, если дату публикации найти не удалось: вызывающий подставит текущее
    время. Соврать здесь опаснее, чем не знать, — по этой дате пользователю
    показывается «последнее обновление данных».
    """
    m = _WHEN_RX.search(re.sub(r"\s+", " ", text or ""))
    if not m:
        return None
    day, month_raw, year, hour, minute = m.groups()
    month = (
        int(month_raw) if month_raw.isdigit()
        else _RU_MONTHS.get(month_raw.lower().replace("ё", "е"), 0)
    )
    if not 1 <= month <= 12:
        return None
    try:
        return datetime(int(year), month, int(day), int(hour or 0), int(minute or 0))
    except ValueError:
        return None
