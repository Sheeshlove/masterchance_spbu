"""
Threshold Vermilion — Пластина I.

Композиция строится по философии из THRESHOLD_VERMILION.md: одна краска,
одна бумага, один порог. Поле — десять тысяч отметок, по одной на исход;
горизонт никем не проведён, он проявляется сам из накопления отметок.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.font_manager as fm
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.patches import Rectangle

# ── материал ────────────────────────────────────────────────────────────────
FONTS = Path("/root/.claude/skills/canvas-design/canvas-fonts")
OUT = Path(__file__).resolve().parent

PAPER = "#FBFAF7"
RED = "#CE2029"
INK = "#2B0A0C"          # красно-чёрный: третьей краски нет

MONO = fm.FontProperties(fname=str(FONTS / "GeistMono-Regular.ttf"))
MONO_B = fm.FontProperties(fname=str(FONTS / "GeistMono-Bold.ttf"))
DISPLAY = fm.FontProperties(fname=str(FONTS / "PoiretOne-Regular.ttf"))
SERIF_I = fm.FontProperties(fname=str(FONTS / "IBMPlexSerif-Italic.ttf"))

# ── поле ────────────────────────────────────────────────────────────────────
N_COLS = 236                   # столбцов-исходов
# Кадрируем только полосу, где живёт граница: масса уходит за нижний край,
# и глаз попадает не в текстуру, а в горизонт.
SCORE_LO, SCORE_HI = 194, 238
N_ROWS = SCORE_HI - SCORE_LO

rng = np.random.default_rng(20260806)

# порог в каждом исходе: слегка левоскошенное распределение —
# уход одного человека роняет порог вниз, поднять его чужой уход не может
raw = rng.normal(0.0, 1.0, 60_000)
skewed = raw - 0.55 * np.abs(rng.normal(0.0, 1.0, 60_000))
cut_all = 214.0 + 8.2 * skewed
q50, q90, q95 = np.percentile(cut_all, [50, 90, 95])

# Не подрезаем: столбец, чей порог ушёл ниже кадра, честно остаётся пустым,
# а ушедший выше — заполняет кадр целиком. Подрезка слепила бы ложный пол.
cuts = np.rint(rng.choice(cut_all, N_COLS)).astype(int)


def tracked_text(ax, x, y, s, fp, size, color, tracking, alpha=1.0, ha="left"):
    """Ручная разрядка: matplotlib её не умеет, а без неё тонкий шрифт разваливается."""
    r = ax.figure.canvas.get_renderer()
    widths = []
    for ch in s:
        t = ax.text(0, -5, ch, fontproperties=fp, fontsize=size, alpha=0)
        bb = t.get_window_extent(renderer=r).transformed(ax.transData.inverted())
        widths.append(bb.width if ch != " " else size * 0.021)
        t.remove()
    total = sum(widths) + tracking * (len(s) - 1)
    cx = x - total * (0.5 if ha == "center" else 1.0 if ha == "right" else 0.0)
    for ch, w in zip(s, widths):
        ax.text(cx, y, ch, fontproperties=fp, fontsize=size, color=color, alpha=alpha,
                ha="left", va="baseline")
        cx += w + tracking
    return total


def build() -> None:
    fig = plt.figure(figsize=(9.45, 12.99), dpi=300)   # 24 × 33 см
    fig.patch.set_facecolor(PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(PAPER)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    # ── геометрия поля ──────────────────────────────────────────────────────
    FX0, FX1 = 15.0, 88.0          # поле по горизонтали
    FY0, FY1 = 30.5, 78.0          # поле по вертикали
    cw = (FX1 - FX0) / N_COLS
    rh = (FY1 - FY0) / N_ROWS
    mw, mh = cw * 0.58, rh * 0.42   # отметка меньше ячейки: между ними дышит бумага

    def sy(score: float) -> float:
        return FY0 + (score - SCORE_LO) * rh

    # ── накопление отметок ──────────────────────────────────────────────────
    # Тело массы приглушено до шёпота; во весь голос звучит только верхняя
    # отметка каждого столбца. Линию горизонта никто не проводит — она
    # проявляется сама, из повторения.
    for i, cut in enumerate(cuts):
        x = FX0 + i * cw + (cw - mw) / 2
        n = cut - SCORE_LO                      # сколько отметок попало в кадр
        for r in range(max(min(n, N_ROWS), 0)):
            a = 0.12 + 0.16 * (1.0 - r / max(n, 1))
            # у нижнего края масса не обрывается, а растворяется в бумаге:
            # она продолжается за кадром, и об этом говорит именно затухание
            if r < 7:
                a *= 0.16 + 0.84 * (r / 7)
            ax.add_patch(Rectangle(
                (x, FY0 + r * rh + (rh - mh) / 2), mw, mh,
                facecolor=RED, edgecolor="none", alpha=a, linewidth=0, zorder=2,
            ))
        if 0 < n <= N_ROWS:                     # горизонт виден только внутри кадра
            ax.add_patch(Rectangle(
                (x - mw * 0.14, FY0 + (n - 1) * rh + (rh - mh) / 2), mw * 1.28, mh,
                facecolor=RED, edgecolor="none", alpha=1.0, linewidth=0, zorder=5,
            ))

    # ── три спокойные линии, извлечённые из шума ────────────────────────────
    for score, label in ((q50, "МЕДИАНА"), (q90, "q90"), (q95, "q95")):
        y = sy(score)
        ax.plot([FX0 - 3.2, FX1 + 1.6], [y, y], color=INK, lw=0.32, alpha=0.55, zorder=4)
        ax.text(FX1 + 2.4, y, label, fontproperties=MONO, fontsize=4.6, color=INK,
                alpha=0.75, va="center", ha="left")
        ax.text(FX1 + 2.4, y - 0.95, f"{score:.0f}", fontproperties=MONO_B, fontsize=4.6,
                color=RED, va="center", ha="left")

    # ── вертикальная шкала ──────────────────────────────────────────────────
    ax.plot([FX0 - 3.2, FX0 - 3.2], [FY0, FY1], color=INK, lw=0.34, alpha=0.5, zorder=4)
    for score in range(SCORE_LO, SCORE_HI + 1, 10):
        y = sy(score)
        ax.plot([FX0 - 3.2, FX0 - 2.3], [y, y], color=INK, lw=0.34, alpha=0.5, zorder=4)
        ax.text(FX0 - 3.9, y, str(score), fontproperties=MONO, fontsize=4.4, color=INK,
                alpha=0.62, va="center", ha="right")

    # ── одна отметка: тот, кто стоит ровно на границе ───────────────────────
    # Вертикальный вынос, как на препараторской пластине: короче, тише, точнее.
    ix = 148
    x = FX0 + ix * cw + (cw - mw) / 2
    y = sy(q90)
    ax.plot([x + mw / 2, x + mw / 2], [y + mh * 1.9, y + 4.1],
            color=INK, lw=0.28, alpha=0.5, zorder=6)
    ax.add_patch(Rectangle((x - mw * 0.62, y - mh * 0.72), mw * 2.24, mh * 2.44,
                           facecolor=PAPER, edgecolor=RED, linewidth=0.5, zorder=6))
    ax.text(x + mw / 2, y + 4.6, "ОДИН", fontproperties=MONO_B, fontsize=4.8,
            color=INK, alpha=0.85, va="bottom", ha="center")

    # ── клинические пометы ──────────────────────────────────────────────────
    ax.plot([15.0, 88.0], [85.6, 85.6], color=INK, lw=0.4, alpha=0.65, zorder=4)
    ax.text(15.0, 86.4, "ПЛАСТИНА  I", fontproperties=MONO_B, fontsize=5.4, color=INK,
            alpha=0.9, va="bottom", ha="left")
    ax.text(15.0, 84.5, "РАСПРЕДЕЛЕНИЕ  ПОРОГА", fontproperties=MONO, fontsize=5.0,
            color=INK, alpha=0.7, va="top", ha="left")
    ax.text(88.0, 86.4, "n = 10 000", fontproperties=MONO, fontsize=5.4, color=RED,
            alpha=0.95, va="bottom", ha="right")
    ax.text(88.0, 84.5, "МОНТЕ-КАРЛО", fontproperties=MONO, fontsize=5.0, color=INK,
            alpha=0.7, va="top", ha="right")

    ax.text(15.0, 28.4, "ОДНА  ОТМЕТКА — ОДИН  ИСХОД", fontproperties=MONO, fontsize=4.6,
            color=INK, alpha=0.6, va="top", ha="left")
    ax.text(88.0, 28.4, "МАССА  УХОДИТ  ЗА  КРАЙ", fontproperties=MONO, fontsize=4.6,
            color=INK, alpha=0.6, va="top", ha="right")

    # ── якорь ───────────────────────────────────────────────────────────────
    tracked_text(ax, 50.0, 17.0, "ПОРОГ ДВИЖЕТСЯ", DISPLAY, 33, INK, 0.62, ha="center")
    ax.text(50.0, 13.2, "десять тысяч возможных исходов, одна граница",
            fontproperties=SERIF_I, fontsize=6.2, color=INK, alpha=0.62,
            va="top", ha="center")

    # ── колофон ─────────────────────────────────────────────────────────────
    ax.plot([15.0, 88.0], [8.4, 8.4], color=INK, lw=0.3, alpha=0.4, zorder=4)
    ax.text(15.0, 7.4, "THRESHOLD  VERMILION", fontproperties=MONO, fontsize=4.4,
            color=INK, alpha=0.55, va="top", ha="left")
    ax.text(88.0, 7.4, "MMXXVI", fontproperties=MONO, fontsize=4.4,
            color=INK, alpha=0.55, va="top", ha="right")

    fig.savefig(OUT / "threshold-vermilion.png", dpi=300, facecolor=PAPER)
    fig.savefig(OUT / "threshold-vermilion.pdf", facecolor=PAPER)
    plt.close(fig)
    print(f"q50={q50:.1f} q90={q90:.1f} q95={q95:.1f}")


if __name__ == "__main__":
    build()
