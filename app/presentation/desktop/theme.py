# app/presentation/desktop/theme.py
"""
Оформление десктоп-клиента: та же палитра Threshold Vermilion, что и на сайте
(см. design/THRESHOLD_VERMILION.md и static/styles.css).

Вынесено из ui.py по одной причине: ui.py импортирует tkinter, а он есть не
везде (в CI его нет). Здесь чистые функции — палитра, выбор шрифта, шкала —
поэтому их можно проверить тестами, не поднимая окно.
"""
from __future__ import annotations

from typing import Iterable, Sequence

# ── бумага и краска ──────────────────────────────────────────────────────────
PAPER = "#FBFAF7"      # фон окна
SURFACE = "#FFFFFF"    # поле вывода
SURFACE_2 = "#F6F4F0"  # утопленные плашки
LINE = "#EAE5DF"

RED = "#CE2029"
RED_DEEP = "#8E1116"
RED_TINT = "#FBEAEB"
RED_LINE = "#F0D2D4"

INK = "#2B0A0C"        # красно-чёрный
INK_SOFT = "#6B5A5B"
INK_FAINT = "#9A8B8C"

# приглушённые служебные сигналы — красный остаётся главным
OK = "#1F7A4D"
WAIT = "#9A6700"
DONE = "#8B8081"

# ── шрифты ───────────────────────────────────────────────────────────────────
# tkinter умеет только те семейства, что установлены в системе: подключить
# наши .woff2 из static/fonts нельзя. Поэтому — стопки предпочтений, где
# первым идёт то, что мы используем на сайте, а дальше близкие системные.
DISPLAY_STACK: Sequence[str] = (
    "Poiret One", "Futura", "Avenir Next", "Segoe UI Light", "Helvetica Neue",
)
SANS_STACK: Sequence[str] = (
    "Inter", "SF Pro Text", "Helvetica Neue", "Segoe UI", "Arial",
)
MONO_STACK: Sequence[str] = (
    "IBM Plex Mono", "JetBrains Mono", "SF Mono", "Menlo", "Consolas", "Courier New",
)

FALLBACK = "TkDefaultFont"


def pick_family(available: Iterable[str], stack: Sequence[str], fallback: str = FALLBACK) -> str:
    """
    Первое семейство из `stack`, которое реально установлено.

    Сравнение регистронезависимое: tkinter возвращает названия как попало
    («Menlo», «menlo»), и точное совпадение здесь молча уронило бы стопку
    до fallback на ровном месте.
    """
    have = {name.strip().casefold() for name in available}
    for family in stack:
        if family.casefold() in have:
            return family
    return fallback


# ── шкала шанса ──────────────────────────────────────────────────────────────
MARK = "▌"


def tick_bar(prob: float | None, width: int = 44) -> tuple[str, str]:
    """
    Шанс → (закрашенные отметки, пустые отметки).

    Не сплошная полоса, а поле отметок — тот же язык, что на пластине и на
    сайте: одна отметка — один исход. Возвращаем две строки, чтобы UI покрасил
    их разными тегами.
    """
    p = 0.0 if prob is None else min(max(prob, 0.0), 1.0)
    filled = round(p * width)
    # ненулевой шанс не должен выглядеть как ровный ноль
    if prob and filled == 0:
        filled = 1
    return MARK * filled, MARK * (width - filled)
