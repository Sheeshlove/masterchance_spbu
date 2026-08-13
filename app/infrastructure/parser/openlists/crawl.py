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
from html.parser import HTMLParser
from typing import Any

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
    """Ответ сервера в удобном виде: текст плюс распознанный JSON."""

    def __init__(self, url: str, body: str, content_type: str = "") -> None:
        self.url = url
        self.body = body
        self.content_type = content_type

    @property
    def is_json(self) -> bool:
        if "json" in self.content_type.lower():
            return True
        head = self.body.lstrip()[:1]
        return head in ("{", "[")

    def json(self) -> Any:
        return json.loads(self.body)


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
    text = raw.decode(charset or _guess_charset(raw), errors="replace")
    return Fetched(url=url, body=text, content_type=content_type)


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


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []   # (href, текст ссылки)
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text = []

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.links.append((self._href, text))
            self._href, self._text = None, []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)


def find_links(html: str, base_url: str, pattern: str | None = None) -> list[tuple[str, str]]:
    """
    Ссылки со страницы: [(абсолютный url, текст)].

    `pattern` — регулярка по url ИЛИ по тексту ссылки: у одних вузов список
    узнаётся по адресу (/rating/, /spiski/), у других — только по подписи
    («Списки поступающих», «Рейтинговый список»).
    """
    collector = _LinkCollector()
    try:
        collector.feed(html or "")
        collector.close()
    except Exception:  # noqa: BLE001 — битая разметка не повод терять найденное
        pass

    rx = re.compile(pattern, re.I) if pattern else None
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href, text in collector.links:
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urllib.parse.urljoin(base_url, href)
        if absolute in seen:
            continue
        if rx and not (rx.search(absolute) or rx.search(text)):
            continue
        seen.add(absolute)
        out.append((absolute, text))
    return out
