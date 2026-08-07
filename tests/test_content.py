"""
Тесты общих текстов сайта и десктопа (app/presentation/content.py).

Правовая оговорка — не украшение: она заявляет, что проект не связан с вузом.
Проверяется, что она цела, что её видно в обоих интерфейсах и что описание
механики не разъехалось со структурой, которую эти интерфейсы отрисовывают.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.presentation import content

WEB = Path("app/presentation/web")


# ── оговорка ─────────────────────────────────────────────────────────────────

def test_footer_note_says_exactly_what_it_must():
    note = content.FOOTER_NOTE
    assert note.startswith("Собрано Sheeshlove.")
    assert "никак не аффилированы с Санкт-Петербургским Государственным Университетом" in note
    assert "Данные взяты из открытых источников" in note
    assert note.endswith("Egorsheeshwork@yandex.ru")


def test_footer_note_is_assembled_from_one_source():
    """Ссылку mailto собирают из тех же кусков — иначе фраза разойдётся."""
    assert content.FOOTER_NOTE == content.FOOTER_NOTE_LEAD + content.CONTACT_EMAIL


def test_site_footer_does_not_retype_the_note():
    """
    В шаблоне не должно быть своей копии текста: он подставляется из content.py.
    Иначе однажды поправят Python, а на сайте останется старая формулировка.
    """
    base = (WEB / "templates/base.html").read_text(encoding="utf-8")
    assert "footer_note_lead" in base
    assert "не аффилированы" not in base, "оговорка продублирована в шаблоне"


def test_desktop_shows_the_note():
    ui = Path("app/presentation/desktop/ui.py").read_text(encoding="utf-8")
    assert "content.FOOTER_NOTE" in ui


def test_note_reaches_every_page(web_client):
    for url in ("/", "/how", "/mechanism"):
        html = web_client.get(url).text
        assert "Egorsheeshwork@yandex.ru" in html, f"нет оговорки на {url}"
        assert "не аффилированы" in html, f"нет оговорки на {url}"


# ── страница «Как всё устроено» ──────────────────────────────────────────────

def test_mechanism_page_renders_every_section(web_client):
    html = web_client.get("/mechanism").text
    assert content.MECHANISM_TITLE in html
    for section in content.MECHANISM:
        assert section.title in html, f"раздел «{section.title}» не дошёл до страницы"


def test_mechanism_page_covers_the_whole_pipeline(web_client):
    """Страница должна отвечать на весь путь данных, а не на его половину."""
    html = web_client.get("/mechanism").text
    for topic in ("открытые списки", "каждые три часа", "десять тысяч",
                  "слепок", "не знает", "Регистрации нет"):
        assert topic in html, f"на странице не сказано про «{topic}»"


def test_mechanism_is_linked_from_every_page(web_client):
    for url in ("/", "/how"):
        assert "/mechanism" in web_client.get(url).text, f"нет ссылки на {url}"


# ── структура ────────────────────────────────────────────────────────────────

def test_blocks_use_known_kinds():
    """Неизвестный kind шаблон и tkinter молча пропустят — текст исчезнет."""
    allowed = {"p", "list", "note"}
    for section in content.MECHANISM:
        assert section.blocks, f"раздел «{section.title}» пуст"
        for block in section.blocks:
            assert block.kind in allowed, f"неизвестный блок {block.kind!r}"


@pytest.mark.parametrize("section", content.MECHANISM, ids=lambda s: s.title[:18])
def test_no_empty_text_anywhere(section):
    for block in section.blocks:
        if block.kind == "list":
            assert block.items and all(i.strip() for i in block.items)
        else:
            assert block.text.strip()
