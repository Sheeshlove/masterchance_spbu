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
    # Служебное: в заявку не сохраняется, по нему отсеиваются платные строки.
    "funding",
)

#: Персональные данные и служебные колонки: распознаём, чтобы гарантированно
#: не утащить в базу.
#:
#: Отдельная история — целевая квота. В выгрузке ВШЭ рядом с «Приоритет
#: бюджетного места» стоит «Приоритет целевого места», а рядом с «Сумма
#: конкурсных баллов» — «Сумма конкурсных баллов в рамках квоты на целевые
#: места». Обе подходят под общие правила и, будучи прочитанными, затирают
#: настоящие значения пустотой. Целевые места — отдельный конкурс с отдельными
#: местами, нам он не нужен ни колонкой, ни строкой (см. QUOTA_RX).
_IGNORED = (
    r"фамилия", r"\bфио\b", r"\bимя\b", r"отчество", r"полное имя",
    r"дата рожд", r"^№$", r"^n$", r"^п/п$", r"^№ п/п$", r"^место$", r"^позиция$",
    r"целев",
)

#: (регулярка по нормализованному заголовку, поле, точность). Порядок значим:
#: «сумма конкурсных баллов» должна сработать раньше общего «сумма баллов».
#:
#: Точность решает спор, когда на одно поле претендуют две колонки. В выгрузке
#: ВШЭ есть и «Рег. номер», и «Уникальный код поступающего» — оба похожи на
#: идентификатор, но код поступающего это тот, что напечатан в списках и
#: который вводит человек, а регистрационный номер — внутренний. Побеждает
#: колонка с большей точностью, при равной — первая слева.
_RULES: tuple[tuple[str, str, int], ...] = (
    # ── идентификатор поступающего ──────────────────────────────────────────
    (r"уникальн\w* код", "applicant_id", 100),
    (r"код поступающ", "applicant_id", 95),
    (r"\bснилс\b", "applicant_id", 90),
    (r"индивидуальн\w* номер", "applicant_id", 80),
    (r"номер (заявлени|личного дела|поступающего)", "applicant_id", 70),
    (r"(рег\w*|регистрационн\w*) номер", "applicant_id", 60),
    (r"идентификатор", "applicant_id", 40),
    (r"^код$", "applicant_id", 30),
    (r"^id$", "applicant_id", 20),

    # ── баллы ───────────────────────────────────────────────────────────────
    # Частное раньше общего: «сумма баллов за вступительные испытания» и
    # «сумма баллов за индивидуальные достижения» иначе оба уедут в total_score
    # по правилу «сумма баллов», и конкурсный балл окажется баллом за экзамен.
    (r"(сумма|сумм\w*) баллов за (вступительн|ви\b)", "vi_score", 90),
    (r"балл\w* за вступительн\w* испытани", "vi_score", 85),
    (r"^вступительны\w* испытани", "vi_score", 80),
    (r"^ви$", "vi_score", 70),
    (r"индивидуальн\w* достижени", "id_achievements", 90),
    (r"^ид$", "id_achievements", 70),
    (r"^ида$", "id_achievements", 70),
    (r"конкурсн\w* балл", "total_score", 95),
    (r"сумма конкурсных", "total_score", 95),
    (r"итогов\w* (балл|сумм)", "total_score", 85),
    (r"общ\w* (балл|сумм)", "total_score", 80),
    (r"(сумма|всего) баллов", "total_score", 70),
    (r"^итого", "total_score", 60),

    # ── прочее ──────────────────────────────────────────────────────────────
    (r"приоритет", "priority", 60),
    (r"соглас", "consent", 90),
    (r"оригинал", "consent", 70),
    # Источник финансирования: сам в заявку не попадает, но по нему отсеиваются
    # платные строки, если вуз печатает бюджет и договор одной таблицей.
    (r"(основа|вид|источник|форма) (обучения|финансировани|места|оплаты)", "funding", 60),
    (r"финансировани", "funding", 55),
    # Забрал документы — из конкурса выбыл; такую строку в заявки не берём.
    (r"возврат документов|отзыв (заявлени|документов)|забрал документы", "withdrawn", 60),
    (r"(статус|состояние|примечание|основание)", "review_status", 50),
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
_COMPILED = tuple((re.compile(rx), field, rank) for rx, field, rank in _RULES)


def normalize_header(header: str) -> str:
    """Заголовок → сравнимая форма: без переносов, регистра, сносок и «ё»."""
    text = (header or "").replace("\xa0", " ").lower().replace("ё", "е")
    text = re.sub(r"\*+", " ", text)          # сноски «Согласие*»
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .:;")


def field_and_rank(header: str) -> tuple[str | None, int]:
    """Заголовок → (поле, точность). Точность нужна при споре двух колонок."""
    name = normalize_header(header)
    if not name or _IGNORED_RX.search(name):
        return None, 0

    # «Балл за ВИ №1 / №2» — предметные баллы, их номер важен.
    if re.search(r"балл\w* за ви|балл\w* за вступительн", name):
        num = re.search(r"№\s*(\d+)", name)
        if num:
            idx = int(num.group(1))
            return (f"subject{idx}_score", 95) if idx in (1, 2) else (None, 0)

    for rx, field, rank in _COMPILED:
        if rx.search(name):
            return field, rank
    return None, 0


def field_for_header(header: str) -> str | None:
    """
    Заголовок колонки → поле Application либо None.

    None означает «эта колонка нам не нужна» — и для ФИО, и для номера по
    порядку, и для целевой квоты, и для всего незнакомого.
    """
    return field_and_rank(header)[0]


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
    """
    Заголовки таблицы → список полей той же длины (None — колонка не нужна).

    Если на одно поле претендуют несколько колонок, остаётся самая точная —
    остальные гасятся. Иначе значение последней из них затирает настоящее:
    так пустая «Сумма конкурсных баллов в рамках квоты на целевые места»
    обнуляла конкурсный балл, а «Приоритет целевого места» — приоритет.
    """
    ranked = [field_and_rank(h) for h in headers]

    best: dict[str, tuple[int, int]] = {}      # поле → (точность, номер колонки)
    for index, (field, rank) in enumerate(ranked):
        if not field:
            continue
        if field not in best or rank > best[field][0]:
            best[field] = (rank, index)

    return [
        field if field and best[field][1] == index else None
        for index, (field, _rank) in enumerate(ranked)
    ]


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
