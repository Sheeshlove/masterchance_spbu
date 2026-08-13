# app/infrastructure/parser/openlists/tables.py
"""
HTML → таблицы, без предположений о вёрстке конкретного вуза.

Нужен не «весь DOM», а ровно три вещи на таблицу: заголовки колонок, строки и
текст, который стоял перед ней. Последнее важнее, чем кажется: название
образовательной программы и число мест почти нигде не лежат в самой таблице —
они в заголовке над ней («38.04.02 Менеджмент, очная форма, бюджет, 25 мест»).

Разбор идёт потоковым HTMLParser из стандартной библиотеки: сторонних
зависимостей у проекта на парсинг нет, и заводить их ради этого не стоит.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: Сколько символов текста перед таблицей запоминаем. Заголовок программы с
#: направлением и формой обучения укладывается с запасом, а лишнее только
#: мешает регуляркам.
_PREAMBLE_CHARS = 400

#: Теги, содержимое которых — не текст страницы.
_SILENT = {"script", "style", "noscript", "template", "svg"}

#: Теги, после которых текст точно продолжается с новой мысли.
_BREAKS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


@dataclass
class HtmlTable:
    """Одна таблица со страницы."""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    #: Текст непосредственно перед таблицей (заголовок, подпись, «Мест: 25»).
    preamble: str = ""
    #: Всё, что шло перед таблицей от начала страницы, — запасной источник
    #: названия, когда над таблицей ничего нет.
    page_title: str = ""


@dataclass
class _Pending:
    """Таблица, разбор которой ещё не закончен."""
    preamble: str
    rows: list[list[str]] = field(default_factory=list)
    header_flags: list[bool] = field(default_factory=list)
    row: list[str] = field(default_factory=list)
    row_has_th: bool = False


class _TableCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[_Pending] = []
        self._cell: list[str] | None = None
        self._silent = 0
        self._flow: list[str] = []      # текст вне таблиц
        self._in_title = False
        self.page_title = ""
        self.tables: list[HtmlTable] = []

    # ── служебное ──────────────────────────────────────────────────────────
    def _flow_tail(self) -> str:
        text = _squeeze(" ".join(self._flow))
        return text[-_PREAMBLE_CHARS:].strip()

    def handle_starttag(self, tag, attrs):
        if tag in _SILENT:
            self._silent += 1
            return
        if self._silent:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "table":
            self._stack.append(_Pending(preamble=self._flow_tail()))
            self._flow.clear()
        elif tag == "tr" and self._stack:
            self._stack[-1].row = []
            self._stack[-1].row_has_th = False
        elif tag in ("td", "th") and self._stack:
            self._cell = []
            if tag == "th":
                self._stack[-1].row_has_th = True
        elif tag in _BREAKS:
            self._append_text(" ")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self._append_text(" ")

    def handle_endtag(self, tag):
        if tag in _SILENT:
            self._silent = max(0, self._silent - 1)
            return
        if self._silent:
            return
        if tag == "title":
            self._in_title = False
        elif tag in ("td", "th"):
            if self._cell is not None and self._stack:
                self._stack[-1].row.append(_squeeze("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._stack:
            pending = self._stack[-1]
            if any(cell for cell in pending.row):
                pending.rows.append(pending.row)
                pending.header_flags.append(pending.row_has_th)
            pending.row = []
            pending.row_has_th = False
        elif tag == "table" and self._stack:
            self.tables.append(_finalize(self._stack.pop(), self.page_title))
            # Текст после вложенной таблицы относится уже к внешней таблице.
            self._flow.clear()

    def handle_data(self, data):
        if self._silent:
            return
        if self._in_title:
            self.page_title = _squeeze(self.page_title + " " + data)
            return
        self._append_text(data)

    def _append_text(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif not self._stack:
            self._flow.append(data)


def _squeeze(text: str) -> str:
    """Схлопнуть пробелы и неразрывные пробелы — иначе не совпадёт ни один заголовок."""
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _finalize(pending: _Pending, page_title: str) -> HtmlTable:
    """
    Строки таблицы → заголовки + данные.

    Заголовком считается первая строка на <th>. Если <th> нет вовсе (а так
    делают часто), заголовок — первая строка: в рейтинговых списках она
    подписывает колонки, а не содержит абитуриента.
    """
    rows = pending.rows
    if not rows:
        return HtmlTable(preamble=pending.preamble, page_title=page_title)

    header_idx = next((i for i, is_th in enumerate(pending.header_flags) if is_th), 0)
    return HtmlTable(
        headers=rows[header_idx],
        rows=rows[header_idx + 1:],
        preamble=pending.preamble,
        page_title=page_title,
    )


def extract_tables(html: str) -> list[HtmlTable]:
    """Все таблицы страницы, в порядке появления."""
    collector = _TableCollector()
    try:
        collector.feed(html or "")
        collector.close()
    except Exception:  # noqa: BLE001 — битую разметку разбираем «сколько получилось»
        pass
    return collector.tables
