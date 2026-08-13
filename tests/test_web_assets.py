"""
Тесты статики веб-интерфейса.

Шрифты лежат у нас, а не на CDN, поэтому ломается это тихо: путь разъехался —
браузер молча падает на системный шрифт, и никто не замечает. Дешевле проверить.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path("app/presentation/web/static").resolve()
TEMPLATES = Path("app/presentation/web/templates").resolve()


def test_every_font_face_points_at_a_real_file():
    css = (STATIC / "fonts.css").read_text(encoding="utf-8")
    refs = re.findall(r"url\('([^']+)'\)", css)

    assert refs, "в fonts.css не осталось ни одного @font-face"
    missing = [r for r in refs if not (STATIC / r).is_file()]
    assert not missing, f"нет файлов шрифтов: {missing}"


def test_preloaded_fonts_exist():
    """<link rel=preload> на несуществующий файл — впустую потраченный запрос."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    preloads = re.findall(r'href="/static/(fonts/[^"]+)"', base)

    assert preloads, "критические шрифты больше не преложены"
    for p in preloads:
        assert (STATIC / p).is_file(), f"preload ссылается на отсутствующий {p}"


def test_no_external_font_dependency():
    """Сайт открывают из России: внешний CDN в критическом пути не нужен."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in base
    assert "fonts.gstatic.com" not in base


@pytest.mark.parametrize("token", ["--red", "--paper", "--ink", "--display", "--mono"])
def test_design_tokens_are_declared(token):
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert f"{token}:" in css, f"токен {token} пропал из палитры"


def test_stylesheet_has_no_leftover_dark_palette():
    """Старая тёмная тема не должна просачиваться в красно-белую."""
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    for stale in ("#0f1115", "#181b22", "#20242d", "#5b8cff"):
        assert stale not in css, f"остался цвет тёмной темы {stale}"


def test_pages_never_link_static_over_plain_http(web_client):
    """
    Сайт живёт за nginx по https. Если ссылка на стиль абсолютная и с http://,
    браузер и особенно webview Telegram блокируют её как mixed content —
    страница остаётся без оформления. `url_for` в Starlette даёт именно такой
    абсолютный адрес, подставляя схему из запроса, а за прокси она http.
    """
    import re

    for url in ("/", "/how", "/mechanism"):
        html = web_client.get(url).text
        local = re.findall(r'(?:href|src)="(http://[^"]+)"', html)
        assert not local, f"{url}: ссылки по http на https-странице: {local}"


def test_static_links_are_root_relative(web_client):
    """Относительный адрес не имеет схемы — испортить его прокси не может."""
    import re

    html = web_client.get("/").text
    refs = re.findall(r'(?:href|src)="([^"]*\/static\/[^"]*)"', html)
    assert refs, "на странице нет ссылок на статику"
    for ref in refs:
        assert ref.startswith("/static/"), f"не относительная ссылка: {ref}"


def test_app_is_started_behind_a_proxy():
    """
    uvicorn по умолчанию доверяет X-Forwarded-* только с 127.0.0.1, а nginx
    приходит в контейнер с адреса docker-шлюза. Без этого схема остаётся http
    и ломаются любые абсолютные ссылки и редиректы.
    """
    web = Path("web.py").read_text(encoding="utf-8")
    assert "proxy_headers=True" in web
    assert 'forwarded_allow_ips="*"' in web


def test_no_render_blocking_scripts(web_client):
    """
    Скрипт в <head> без defer/async останавливает отрисовку, пока не скачается.
    Для telegram-web-app.js это особенно дорого: свою копию положить нельзя,
    а до telegram.org из России бывает далеко — страница висит белой.
    """
    import re

    html = web_client.get("/").text
    head = html.split("</head>")[0]
    for tag in re.findall(r"<script\b[^>]*>", head):
        if "src=" not in tag:
            continue
        assert " defer" in tag or " async" in tag, f"блокирующий скрипт: {tag}"


def test_only_telegram_sdk_comes_from_outside(web_client):
    """
    Всё, что можно, лежит у нас. Единственное исключение — SDK Telegram:
    со своей копией Mini App не работает.

    Считаются только теги, которые ЗАГРУЖАЮТ ресурс: <script src> и <link href>.
    Обычные ссылки в тексте (телеграм автора, репозиторий) ничего не тянут и
    на скорость не влияют.
    """
    import re
    from urllib.parse import urlparse

    html = web_client.get("/").text
    loading = re.findall(r'<(?:script|link)\b[^>]*?(?:src|href)="(https?://[^"]+)"', html)
    hosts = {urlparse(u).netloc for u in loading}

    assert hosts <= {"telegram.org"}, f"лишние внешние источники: {hosts - {'telegram.org'}}"


def test_htmx_is_served_locally():
    js = STATIC / "htmx.min.js"
    assert js.is_file(), "htmx не лежит в static"
    assert js.stat().st_size > 20_000, "файл htmx подозрительно мал"
