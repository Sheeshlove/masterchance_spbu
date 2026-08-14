# app/infrastructure/parser/openlists/seats.py
"""
Сколько мест на программе — из сводки вуза.

Зачем отдельно: в самом рейтинговом списке числа мест может не быть вовсе. У
ВШЭ его там нет — список отвечает только на вопрос «кто подал», а КЦП живёт в
соседнем файле «Статистика поданных заявлений». Без него шанс посчитать не на
чем: конкурс — это отношение людей к местам.

Сводка устроена деревом: кампус → «Всего очная форма обучения» → «Направление
"01.04.02 …"» → строки программ. Нас интересуют листья с их бюджетными
местами; итоговые строки («Всего», «Итого по кампусу», «Количество
зарегистрированных абитуриентов») — не программы и в справочник не идут.

Соответствие со списком — по кампусу и названию программы. Названия совпадают
дословно, но нормализуются на всякий случай: регистр, кавычки, пробелы.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.config.logger import logger
from app.domain.universities import _normalize_name
from app.infrastructure.parser.openlists.records import UNKNOWN_SPECIALITY, to_int

#: Строка-раздел: «Направление "01.04.02 Прикладная математика и информатика"».
_DIRECTION_RX = re.compile(r'направлени\w*\s*[«"]?\s*(\d{2}\.\d{2}\.\d{2})', re.I)

#: Итоги и служебные строки: программами не являются.
_TOTAL_RX = re.compile(
    r"^(всего|итого|количество зарегистрированных|конкурс)\b|^ниу вшэ\b|^москва$", re.I
)

#: Колонка с бюджетными местами. Целевые и платные — другие конкурсы, их
#: колонки называются почти так же, поэтому проверяем и на что НЕ похоже.
_SEATS_RX = re.compile(r"(количество мест|мест[а-я]*\b|кцп)", re.I)
_NOT_SEATS_RX = re.compile(r"целев|оплат|платн|договор|заявлен", re.I)

#: Кампусы, которые различаются как отдельные конкурсы.
CAMPUS_RX = re.compile(
    r"(москва|московск\w+|санкт-петербург\w*|нижн\w+ новгород\w*|перм\w+)", re.I
)

_CAMPUS_NAMES = {
    "москва": "Москва", "московски": "Москва",
    "санкт-петербург": "Санкт-Петербург",
    "нижни": "Нижний Новгород", "перм": "Пермь",
}


def parse_campus(text: str) -> str:
    """«НИУ ВШЭ - Санкт-Петербург» → «Санкт-Петербург». Не нашли — пустая строка."""
    match = CAMPUS_RX.search(text or "")
    if not match:
        return ""
    found = match.group(1).lower()
    for prefix, name in _CAMPUS_NAMES.items():
        if found.startswith(prefix):
            return name
    return match.group(1).capitalize()


@dataclass(frozen=True)
class ProgramSeats:
    """Одна строка справочника мест."""
    campus: str
    program_name: str
    speciality_code: str
    places: int


def seats_key(campus: str, program_name: str) -> tuple[str, str]:
    """Ключ справочника: кампус + нормализованное название программы."""
    return (_normalize_name(campus), _normalize_name(program_name))


def _header_row(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows[:20]):
        joined = " ".join(row).lower()
        if "конкурс" in joined and _SEATS_RX.search(joined):
            return index
    return None


def _seats_column(header: list[str]) -> int | None:
    for index, cell in enumerate(header):
        name = re.sub(r"\s+", " ", (cell or "").replace("\xa0", " "))
        if _SEATS_RX.search(name) and not _NOT_SEATS_RX.search(name):
            return index
    return None


def parse_seats_table(rows: list[list[str]], campus: str = "") -> list[ProgramSeats]:
    """
    Строки сводки → справочник мест по программам.

    Пустой список — это не ошибка: то же самое вернётся для любого файла,
    который сводкой не является.
    """
    header_index = _header_row(rows)
    if header_index is None:
        return []
    header = rows[header_index]
    column = _seats_column(header)
    if column is None:
        return []

    campus = campus or _campus_from_rows(rows[header_index + 1: header_index + 4])
    out: list[ProgramSeats] = []
    direction = UNKNOWN_SPECIALITY

    for row in rows[header_index + 1:]:
        name = (row[0] if row else "").strip()
        if not name:
            continue

        found = _DIRECTION_RX.search(name)
        if found:
            direction = found.group(1)
            continue
        if _TOTAL_RX.search(name) or CAMPUS_RX.fullmatch(name):
            continue

        places = to_int(row[column]) if column < len(row) else 0
        # «-» в колонке мест означает, что бюджетного набора на программе нет.
        # Такую программу мы всё равно оставляем со значением 0: конкурса нет,
        # и сайт честно скажет, что шанс считать не на чем.
        out.append(ProgramSeats(
            campus=campus,
            program_name=name,
            speciality_code=direction,
            places=places,
        ))
    return out


def _campus_from_rows(rows: list[list[str]]) -> str:
    for row in rows:
        campus = parse_campus(" ".join(row))
        if campus:
            return campus
    return ""


def build_seats_map(tables: list[tuple[str, list[list[str]]]]) -> dict[tuple[str, str], ProgramSeats]:
    """Сводки (лист, строки) → {(кампус, название): места}."""
    catalogue: dict[tuple[str, str], ProgramSeats] = {}
    for sheet_name, rows in tables:
        # Имя листа у ВШЭ — «на 12.08.2026», кампус в нём не назван; его
        # ищем в самих строках.
        for item in parse_seats_table(rows, campus=parse_campus(sheet_name)):
            catalogue.setdefault(seats_key(item.campus, item.program_name), item)
    if catalogue:
        logger.info("Справочник мест: программ %d", len(catalogue))
    return catalogue
