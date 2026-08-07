"""
Тесты оформления десктоп-клиента.

Само окно здесь не поднимается — tkinter есть не в каждой среде, и в CI его
нет. Проверяется то, что от tkinter не зависит и при этом реально ломается:
выбор шрифта из установленных в системе и шкала шанса.
"""
from __future__ import annotations

import pytest

from app.presentation.desktop import theme


# ── выбор шрифта ─────────────────────────────────────────────────────────────

def test_prefers_the_site_font_when_installed():
    got = theme.pick_family(["Arial", "Inter", "Menlo"], theme.SANS_STACK)
    assert got == "Inter"


def test_falls_through_the_stack_in_order():
    """Inter нет — берём следующий по списку, а не что попало."""
    got = theme.pick_family(["Helvetica Neue", "Arial"], theme.SANS_STACK)
    assert got == "Helvetica Neue"


def test_falls_back_when_nothing_matches():
    got = theme.pick_family(["Comic Sans MS"], theme.MONO_STACK)
    assert got == theme.FALLBACK


def test_family_match_ignores_case_and_padding():
    """tkinter отдаёт названия как попало; точное сравнение уронило бы стопку."""
    assert theme.pick_family(["  menlo  "], theme.MONO_STACK) == "Menlo"
    assert theme.pick_family(["IBM PLEX MONO"], theme.MONO_STACK) == "IBM Plex Mono"


def test_empty_system_still_returns_something():
    assert theme.pick_family([], theme.DISPLAY_STACK) == theme.FALLBACK


# ── шкала шанса ──────────────────────────────────────────────────────────────

def test_bar_splits_marks_by_probability():
    on, off = theme.tick_bar(0.5, width=10)
    assert len(on) == 5 and len(off) == 5


def test_bar_width_is_constant():
    """Шкалы разных карточек должны совпадать по длине, иначе колонка «поедет»."""
    for p in (0.0, 0.01, 0.37, 0.5, 0.99, 1.0):
        on, off = theme.tick_bar(p, width=44)
        assert len(on) + len(off) == 44


def test_full_and_empty_are_exact():
    assert theme.tick_bar(1.0, width=8) == (theme.MARK * 8, "")
    assert theme.tick_bar(0.0, width=8) == ("", theme.MARK * 8)


def test_tiny_chance_still_shows_a_mark():
    """0,4% — это не ноль; пустая шкала соврала бы пользователю."""
    on, _ = theme.tick_bar(0.004, width=44)
    assert len(on) == 1


def test_missing_probability_reads_as_empty():
    on, off = theme.tick_bar(None, width=12)
    assert on == "" and len(off) == 12


@pytest.mark.parametrize("p", [-0.5, 1.7])
def test_out_of_range_is_clamped(p):
    on, off = theme.tick_bar(p, width=10)
    assert len(on) + len(off) == 10
    assert 0 <= len(on) <= 10


# ── палитра ──────────────────────────────────────────────────────────────────

def test_palette_matches_the_site():
    """Десктоп и сайт должны быть одного цвета — иначе это два разных продукта."""
    css = open("app/presentation/web/static/styles.css", encoding="utf-8").read()
    for value in (theme.RED, theme.RED_DEEP, theme.PAPER, theme.INK):
        assert value in css, f"{value} есть в десктопе, но пропал из стилей сайта"


def test_no_leftover_dark_palette_in_desktop():
    ui = open("app/presentation/desktop/ui.py", encoding="utf-8").read()
    for stale in ("#1f5fd0", "#fbfbfd", "#b3261e", "#555", "#666", "#777"):
        assert stale not in ui, f"в десктопе остался цвет старой темы {stale}"
