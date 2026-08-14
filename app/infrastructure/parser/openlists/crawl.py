# app/infrastructure/parser/openlists/crawl.py
"""
HTTP-слой движка: скачать страницу и найти на ней ссылки на списки.

Отдельно от разбора, потому что разбор тестируется офлайн на сохранённых
страницах, а сюда без сети идти незачем. Все запросы уходят через общий
`app.infrastructure.http.urlopen` — там правильные корневые сертификаты.
"""
from __future__ import annotations

import gzip
import json
import re
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Sequence

from app.infrastructure.http import urlopen

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


class Fetched:
    """
    Ответ сервера в удобном виде.

    Байты хранятся как есть, а текст получается из них по требованию: списки
    приходят не только страницами, но и файлами Excel, а их декодировать в
    строку нельзя — из zip-архива получится мусор.
    """

    def __init__(self, url: str, raw: bytes, content_type: str = "",
                 charset: str | None = None) -> None:
        self.url = url
        self.raw = raw
        self.content_type = content_type
        self._charset = charset
        self._text: str | None = None

    @property
    def body(self) -> str:
        """Тело как текст. Для двоичных файлов бессмысленно — см. is_binary."""
        if self._text is None:
            self._text = self.raw.decode(self._charset or _guess_charset(self.raw), errors="replace")
        return self._text

    @property
    def is_binary(self) -> bool:
        """Excel, PDF и прочее, что текстом не читается."""
        return sniff_binary(self.raw) is not None

    @property
    def is_json(self) -> bool:
        if self.is_binary:
            return False
        if "json" in self.content_type.lower():
            return True
        return self.body.lstrip()[:1] in ("{", "[")

    def json(self) -> Any:
        return json.loads(self.body)


#: Сигнатуры файлов, которые приходят вместо страницы.
_MAGIC = (
    (b"PK\x03\x04", "xlsx"),                    # zip: xlsx, ods, docx
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "xls"),  # старый бинарный Excel (OLE2)
    (b"%PDF", "pdf"),
)


def sniff_binary(raw: bytes) -> str | None:
    """Что за файл пришёл: 'xlsx' | 'xls' | 'pdf' | None (значит текст)."""
    head = (raw or b"")[:8]
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    return None


def fetch(url: str, timeout: int = 60, data: bytes | None = None) -> Fetched:
    """GET (или POST, если передано `data`) с нашими заголовками."""
    request = urllib.request.Request(url, data=data, headers=dict(_HEADERS))
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        encoding = (response.headers.get("Content-Encoding") or "").lower()
        # Некоторые сайты вузов отдают gzip даже там, где urllib его не ждёт.
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        charset = response.headers.get_content_charset()
        content_type = response.headers.get("Content-Type", "")
    return Fetched(url=url, raw=raw, content_type=content_type, charset=charset)


def _guess_charset(raw: bytes) -> str:
    """
    Кодировка, когда сервер её не назвал.

    Часть вузовских страниц до сих пор в windows-1251, и молча раскодировать
    их как utf-8 — значит получить «ÐÐ¸Ð½Ð°Ð¼Ð¸ÐºÐ°» вместо заголовков и не
    узнать ни одной колонки.
    """
    head = raw[:2048].lower()
    m = re.search(rb"charset=['\"]?([\w-]+)", head)
    if m:
        return m.group(1).decode("ascii", errors="replace")
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "windows-1251"


@dataclass(frozen=True)
class Link:
    """Ссылка вместе с разделом, в котором она стоит."""
    url: str
    text: str
    #: Заголовки над ссылкой, от внешнего к ближнему: «Москва Очная форма
    #: обучения Списки зарегистрированных абитуриентов». Без этого ссылка
    #: «Скачать в формате XLS» неотличима от такой же ссылки соседнего кампуса.
    context: str = ""

    @property
    def haystack(self) -> str:
        """Всё, по чему можно опознать ссылку, одной строкой."""
        return f"{self.context} {self.text} {urllib.parse.unquote(self.url)}"


#: Уровень заголовка → его место в стопке. Жирный текст и <summary> считаем
#: самым глубоким уровнем: на страницах приёма подраздел («Очная форма
#: обучения») сплошь и рядом выделен именно так, а не тегом заголовка, и без
#: этого ссылки очной и очно-заочной формы неотличимы.
_HEADINGS = {
    "h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6,
    "b": 7, "strong": 7, "summary": 7, "caption": 7, "legend": 7,
}


class _LinkCollector(HTMLParser):
    """
    Собирает ссылки и помнит, под какими заголовками каждая из них оказалась.

    Заголовки держатся стопкой по уровням: новый h2 сбрасывает всё, что глубже,
    ровно как это читает человек. Страницы, где раздел выделен не заголовком, а
    жирным текстом, тоже попадают в контекст — через буфер последнего текста.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._heading_level: int | None = None
        self._heading_text: list[str] = []
        self._headings: dict[int, str] = {}
        self._recent: list[str] = []

    # ── контекст ───────────────────────────────────────────────────────────
    def _context(self) -> str:
        parts = [self._headings[level] for level in sorted(self._headings) if self._headings[level]]
        recent = _squeeze(" ".join(self._recent))[-160:]
        return _squeeze(" ".join([*parts, recent]))

    def handle_starttag(self, tag, attrs):
        # Жирный текст ВНУТРИ ссылки — часть её подписи, а не подраздел.
        if tag in _HEADINGS and self._href is None:
            self._heading_level = _HEADINGS[tag]
            self._heading_text = []
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._text = []

    def handle_endtag(self, tag):
        if tag in _HEADINGS and self._heading_level is not None:
            level = self._heading_level
            heading = _squeeze("".join(self._heading_text))
            # Пустой «заголовок» (жирный пробел, иконка) раздел не открывает.
            if heading:
                self._headings[level] = heading
                for deeper in [lvl for lvl in self._headings if lvl > level]:
                    self._headings.pop(deeper)
                self._recent = []
            self._heading_level = None
        elif tag == "a" and self._href:
            self.links.append(Link(
                url=self._href,
                text=_squeeze("".join(self._text)),
                context=self._context(),
            ))
            self._href, self._text = None, []

    def handle_data(self, data):
        if self._heading_level is not None:
            self._heading_text.append(data)
        elif self._href is not None:
            self._text.append(data)
        elif data.strip():
            self._recent.append(data)
            if len(self._recent) > 40:
                del self._recent[:-40]


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def find_links(
    html: str,
    base_url: str,
    pattern: str | None = None,
    required: Sequence[str] = (),
    excluded: Sequence[str] = (),
) -> list[Link]:
    """
    Ссылки со страницы, отобранные по адресу, подписи и разделу.

    `pattern`  — что вообще считать нужной ссылкой: регулярка по адресу ИЛИ по
                 подписи. У одних вузов список узнаётся по адресу (/rating/,
                 .xlsx), у других — только по тексту («Рейтинговый список»).
    `required` — каждая из регулярок обязана найтись в разделе, подписи или
                 адресе. Так отбираются нужные кампусы и форма обучения.
    `excluded` — ни одна не должна найтись: очно-заочные, платные, чужие
                 кампусы.
    """
    collector = _LinkCollector()
    try:
        collector.feed(html or "")
        collector.close()
    except Exception:  # noqa: BLE001 — битая разметка не повод терять найденное
        pass

    rx = re.compile(pattern, re.I) if pattern else None
    need = [re.compile(p, re.I) for p in required]
    deny = [re.compile(p, re.I) for p in excluded]

    out: list[Link] = []
    seen: set[str] = set()
    for link in collector.links:
        if link.url.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urllib.parse.urljoin(base_url, link.url)
        if absolute in seen:
            continue
        if rx and not (rx.search(absolute) or rx.search(link.text)):
            continue

        found = Link(url=absolute, text=link.text, context=link.context)
        haystack = found.haystack
        if any(p.search(haystack) for p in deny):
            continue
        if need and not all(p.search(haystack) for p in need):
            continue

        seen.add(absolute)
        out.append(found)
    return out
