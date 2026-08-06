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
    preloads = re.findall(r"path='(fonts/[^']+)'", base)

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
