# app/infrastructure/parser/openlists/columns.py
"""
Заголовок колонки → наше поле.

Состав колонок в рейтинговых списках задан Порядком приёма, а вот формулировки
у каждого вуза свои: «Уникальный код поступающего», «Индивидуальный номер»,
«СНИЛС», «Идентификатор». Здесь собраны синонимы, встречающиеся у шести
источников, — так один разбор работает на всех, и добавление вуза чаще всего
означает добавление синонима, а не нового парсера.

Отдельно про ФИО. Некоторые вузы печатают в списках фамилию. Мы её не берём —
никогда, ни в каком виде: сервис обещает, что связать код с человеком не может,
и это обещание должно держаться кодом, а не намерением. Такие колонки
распознаются и явно отбрасываются (см. _IGNORED).
"""
from __future__ import annotations

import re

#: Поля Application, которые умеет заполнять движок.
FIELDS = (
    "applicant_id",
    "total_score",
    "vi_score",
    "subject1_score",
    "subject2_score",
    "id_achievements",
    "priority",
    "consent",
    "review_status",
)

#: Персональные данные и служебные колонки: распознаём, чтобы гарантированно
#: не утащить в базу.
_IGNORED = (
    r"фамилия", r"\bфио\b", r"\bимя\b", r"отчество", r"полное имя",
    r"дата рожд", r"^№$", r"^n$", r"^п/п$", r"^№ п/п$", r"^место$", r"^позиция$",
)

#: (регулярка по нормализованному заголовку, поле). Порядок значим: «сумма
#: конкурсных баллов» должна сработать раньше общего «сумма баллов».
_RULES: tuple[tuple[str, str], ...] = (
    # ── идентификатор поступающего ──────────────────────────────────────────
    (r"уникальн\w* код", "applicant_id"),
    (r"код поступающ", "applicant_id"),
    (r"\bснилс\b", "applicant_id"),
    (r"индивидуальн\w* номер", "applicant_id"),
    (r"(рег\w*|регистрационн\w*) номер", "applicant_id"),
    (r"номер (заявлени|личного дела|поступающего)", "applicant_id"),
    (r"идентификатор", "applicant_id"),
    (r"^код$", "applicant_id"),
    (r"^id$", "applicant_id"),

    # ── баллы ───────────────────────────────────────────────────────────────
    # Частное раньше общего: «сумма баллов за вступительные испытания» и
    # «сумма баллов за индивидуальные достижения» иначе оба уедут в total_score
    # по правилу «сумма баллов», и конкурсный балл окажется баллом за экзамен.
    (r"(сумма|сумм\w*) баллов за (вступительн|ви\b)", "vi_score"),
    (r"балл\w* за вступительн\w* испытани", "vi_score"),
    (r"^вступительны\w* испытани", "vi_score"),
    (r"^ви$", "vi_score"),
    (r"индивидуальн\w* достижени", "id_achievements"),
    (r"^ид$", "id_achievements"),
    (r"^ида$", "id_achievements"),
    (r"конкурсн\w* балл", "total_score"),
    (r"сумма конкурсных", "total_score"),
    (r"итогов\w* (балл|сумм)", "total_score"),
    (r"общ\w* (балл|сумм)", "total_score"),
    (r"(сумма|всего) баллов", "total_score"),
    (r"^итого", "total_score"),

    # ── прочее ──────────────────────────────────────────────────────────────
    (r"приоритет", "priority"),
    (r"соглас", "consent"),
    (r"оригинал", "consent"),
    (r"(статус|состояние|примечание|основание)", "review_status"),
)

#: Латинские ключи JSON-ответов. Отдельно от русских правил: сопоставлять их
#: регуляркой по подстроке слишком рискованно («id» есть в «program_id»),
#: поэтому здесь только точные имена ключей.
_JSON_KEYS: dict[str, str] = {
    "snils": "applicant_id",
    "unique_code": "applicant_id",
    "uniquecode": "applicant_id",
    "unique_id": "applicant_id",
    "applicant_code": "applicant_id",
    "abiturient_code": "applicant_id",
    "case_number": "applicant_id",
    "code": "applicant_id",
    "id": "applicant_id",
    "total_score": "total_score",
    "total_scores": "total_score",
    "total": "total_score",
    "sum_mark": "total_score",
    "summary_score": "total_score",
    "competitive_score": "total_score",
    "exam_score": "vi_score",
    "exams_score": "vi_score",
    "entrance_score": "vi_score",
    "achievements_score": "id_achievements",
    "achievement": "id_achievements",
    "individual_achievements": "id_achievements",
    "priority": "priority",
    "agreement": "consent",
    "consent": "consent",
    "original": "consent",
    "original_document": "consent",
    "status": "review_status",
    "state": "review_status",
}

_IGNORED_RX = re.compile("|".join(_IGNORED))
_COMPILED = tuple((re.compile(rx), field) for rx, field in _RULES)


def normalize_header(header: str) -> str:
    """Заголовок → сравнимая форма: без переносов, регистра, сносок и «ё»."""
    text = (header or "").replace("\xa0", " ").lower().replace("ё", "е")
    text = re.sub(r"\*+", " ", text)          # сноски «Согласие*»
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .:;")


def field_for_header(header: str) -> str | None:
    """
    Заголовок колонки → поле Application либо None.

    None означает «эта колонка нам не нужна» — и для ФИО, и для номера по
    порядку, и для всего незнакомого.
    """
    name = normalize_header(header)
    if not name or _IGNORED_RX.search(name):
        return None

    # «Балл за ВИ №1 / №2» — предметные баллы, их номер важен.
    if re.search(r"балл\w* за ви|балл\w* за вступительн", name):
        num = re.search(r"№\s*(\d+)", name)
        if num:
            idx = int(num.group(1))
            return f"subject{idx}_score" if idx in (1, 2) else None

    for rx, field in _COMPILED:
        if rx.search(name):
            return field
    return None


def field_for_key(key: str) -> str | None:
    """Ключ JSON-объекта → поле Application либо None."""
    raw = (key or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in _JSON_KEYS:
        return _JSON_KEYS[lowered]
    # Ключ по-русски — те же правила, что и для заголовков таблицы.
    if re.search(r"[а-яё]", lowered):
        return field_for_header(raw)
    # camelCase / snake_case: разбиваем и пробуем ещё раз по точному имени.
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", raw).lower().replace("-", "_")
    return _JSON_KEYS.get(snake)


def map_headers(headers: list[str]) -> list[str | None]:
    """Заголовки таблицы → список полей той же длины (None — колонка не нужна)."""
    return [field_for_header(h) for h in headers]


def looks_like_ranking(fields: list[str | None]) -> bool:
    """
    Похоже ли это на рейтинговый список?

    Требуем идентификатор поступающего и хотя бы один признак конкурса. Без
    этого таблица — это навигация, расписание или «сколько мест по формам
    обучения», и строить по ней заявки нельзя.
    """
    present = {f for f in fields if f}
    if "applicant_id" not in present:
        return False
    return bool(present & {"total_score", "vi_score", "priority", "consent", "id_achievements"})
