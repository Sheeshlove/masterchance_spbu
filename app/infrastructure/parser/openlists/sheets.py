# app/infrastructure/parser/openlists/sheets.py
"""
Списки, приходящие файлом: Excel, CSV, «xls», который на самом деле HTML.

ВШЭ публикует конкурсные списки не страницей, а ссылками на файлы XLS по
каждой программе (`priem45.hse.ru/magstats.html`), и без чтения таких файлов
источник пуст. Дальше файл превращается в те же таблицы, что и страница, и
разбирается тем же кодом — колонки узнаются по заголовкам (columns.py).

Почему без сторонней библиотеки: `.xlsx` — это zip с XML внутри, а всё, что
нам нужно, — прочитать ячейки как строки. Ни формул, ни форматов, ни дат мы
не считаем, поэтому zipfile и ElementTree из стандартной библиотеки хватает,
а зависимость в проекте, который принципиально парсит стандартными
средствами, не появляется.

Чего файл может не быть:
  • старый бинарный `.xls` (OLE2) — читается только через xlrd, и если её нет,
    источник честно говорит об этом в логе, а не молчит;
  • PDF — не поддерживается вовсе.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.etree import ElementTree

from app.config.logger import logger
from app.infrastructure.parser.openlists.crawl import Fetched, sniff_binary
from app.infrastructure.parser.openlists.tables import HtmlTable, extract_tables

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

#: Сколько первых строк листа считать «шапкой» — из них берутся название
#: программы и число мест, если они написаны над таблицей.
_PREAMBLE_ROWS = 12


def _column_index(ref: str) -> int:
    """'C7' → 2. Буквенный адрес столбца, потому что пустые ячейки не пишутся."""
    letters = re.match(r"[A-Za-z]+", ref or "")
    if not letters:
        return 0
    index = 0
    for char in letters.group().upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(raw)
    # Строка может быть разбита на куски с разным оформлением (<r><t>…),
    # поэтому склеиваем все <t> внутри одного <si>.
    return ["".join(node.text or "" for node in si.iter(f"{_NS}t")) for si in root]


def _sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(имя листа, путь внутри архива)] в порядке вкладок книги."""
    names = set(archive.namelist())
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ElementTree.ParseError):
        # Книга нестандартная — берём листы как есть, по порядку файлов.
        return [(f"Лист {i}", path) for i, path in enumerate(
            sorted(n for n in names if n.startswith("xl/worksheets/sheet")), start=1)]

    targets = {
        rel.get("Id"): rel.get("Target", "")
        for rel in rels.iter(f"{_REL_NS}Relationship")
    }
    out: list[tuple[str, str]] = []
    for sheet in workbook.iter(f"{_NS}sheet"):
        path = _normalize_target(targets.get(sheet.get(f"{_DOC_REL}id"), ""))
        if path in names:
            out.append((sheet.get("name") or "", path))
    return out


def _normalize_target(target: str) -> str:
    """
    Ссылку из rels — к пути внутри архива.

    Один и тот же лист записывают тремя способами: 'worksheets/sheet1.xml'
    (относительно xl/), 'xl/worksheets/sheet1.xml' и '/xl/worksheets/sheet1.xml'
    (от корня пакета — так пишет openpyxl). Разбирать нужно все три, иначе
    книга «без листов» окажется просто книгой от другого редактора.
    """
    target = (target or "").strip()
    if not target:
        return ""
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return f"xl/{target}"


def _sheet_rows(archive: zipfile.ZipFile, path: str, strings: list[str]) -> list[list[str]]:
    root = ElementTree.fromstring(archive.read(path))
    rows: list[list[str]] = []
    for row in root.iter(f"{_NS}row"):
        cells: list[str] = []
        for cell in row.iter(f"{_NS}c"):
            index = _column_index(cell.get("r", ""))
            kind = cell.get("t")
            if kind == "s":
                value_node = cell.find(f"{_NS}v")
                position = int(value_node.text) if value_node is not None and value_node.text else -1
                value = strings[position] if 0 <= position < len(strings) else ""
            elif kind == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(f"{_NS}t"))
            else:
                value_node = cell.find(f"{_NS}v")
                value = value_node.text if value_node is not None and value_node.text else ""
            # Пустые ячейки в файл не записываются — восстанавливаем по адресу,
            # иначе колонки разъедутся и заголовки перестанут совпадать со строками.
            while len(cells) < index:
                cells.append("")
            cells.append(re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip())
        rows.append(cells)
    return rows


def _rows_to_table(name: str, rows: list[list[str]]) -> HtmlTable | None:
    """
    Строки листа → таблица с заголовком.

    Шапка в вузовских выгрузках почти всегда есть: несколько строк с названием
    программы, формой обучения и числом мест, и только потом заголовки колонок.
    Заголовком считаем первую строку, где больше одной заполненной ячейки и
    есть хоть одно слово, — всё, что выше, уходит в preamble.
    """
    from app.infrastructure.parser.openlists.columns import looks_like_ranking, map_headers

    header_index = None
    for index, row in enumerate(rows[:_PREAMBLE_ROWS + 20]):
        filled = [cell for cell in row if cell]
        if len(filled) < 2:
            continue
        if looks_like_ranking(map_headers(row)):
            header_index = index
            break
    if header_index is None:
        return None

    preamble = " ".join(
        cell for row in rows[:header_index] for cell in row if cell
    )
    return HtmlTable(
        headers=rows[header_index],
        rows=[row for row in rows[header_index + 1:] if any(cell for cell in row)],
        preamble=preamble,
        page_title=name,
    )


def read_xlsx(data: bytes) -> list[HtmlTable]:
    """Файл .xlsx → по таблице на лист (листы без списка пропускаются)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return []

    tables: list[HtmlTable] = []
    with archive:
        strings = _shared_strings(archive)
        for name, path in _sheet_paths(archive):
            try:
                rows = _sheet_rows(archive, path, strings)
            except (KeyError, ElementTree.ParseError) as exc:
                logger.warning("Лист %s не читается: %s", name, exc)
                continue
            table = _rows_to_table(name, rows)
            if table:
                tables.append(table)
    return tables


def read_csv(text: str) -> list[HtmlTable]:
    """CSV/TSV → одна таблица. Разделитель определяется по первой строке."""
    sample = text[:4096]
    delimiter = max(";,\t", key=sample.count) if sample else ","
    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
        if any(cell.strip() for cell in row)
    ]
    table = _rows_to_table("", rows)
    return [table] if table else []


def tables_from(page: Fetched) -> list[HtmlTable]:
    """
    Ответ сервера → таблицы, чем бы он ни оказался.

    Отдельный случай — файл с расширением .xls, внутри которого лежит обычный
    HTML: так выгружает половина вузовских систем, и по сигнатуре он от
    страницы не отличается.
    """
    kind = sniff_binary(page.raw)

    if kind == "xlsx":
        return read_xlsx(page.raw)

    if kind == "xls":
        return _read_legacy_xls(page)

    if kind == "pdf":
        logger.warning(
            "Список опубликован в PDF (%s) — такой формат не разбирается. "
            "Если это единственная публикация вуза, списки по нему собрать нельзя.",
            page.url,
        )
        return []

    text = page.body
    if "<table" in text.lower() or "<html" in text.lower():
        return extract_tables(text)
    if page.url.lower().endswith((".csv", ".tsv")) or text.count(";") + text.count(",") > 10:
        return read_csv(text)
    return extract_tables(text)


def _read_legacy_xls(page: Fetched) -> list[HtmlTable]:
    """Старый бинарный .xls — только если в окружении есть xlrd."""
    try:
        import xlrd  # noqa: PLC0415 — необязательная зависимость
    except ImportError:
        logger.warning(
            "Список выгружен в старом формате .xls (%s). Чтобы его читать, "
            "поставьте xlrd: pip install xlrd", page.url,
        )
        return []

    try:
        book = xlrd.open_workbook(file_contents=page.raw)
    except Exception as exc:  # noqa: BLE001 — библиотека сторонняя
        logger.warning("Файл .xls не читается (%s): %s", page.url, exc)
        return []

    tables: list[HtmlTable] = []
    for sheet in book.sheets():
        rows = [
            [str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)]
            for r in range(sheet.nrows)
        ]
        table = _rows_to_table(sheet.name, rows)
        if table:
            tables.append(table)
    return tables
