# app/presentation/desktop/ui.py
"""
Десктоп-клиент «посмотри свои шансы» (tkinter).

Модель работы:
  • шансы (Монте-Карло) берутся из СНАПШОТА — их нельзя посчитать для одного
    человека, MC моделирует конкурс всей когорты;
  • факты по абитуриенту (баллы, приоритеты, согласия) по возможности
    подтягиваются СВЕЖИМИ одним запросом по его коду (live.py);
  • если сети нет — всё работает на снапшоте, о чём честно сообщается в UI.

Расчёты не дублируются: используется тот же GetApplicantForecastUseCase, что
и бот с веб-интерфейсом.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.use_cases.get_applicant_forecast import (
    ExamState,
    ForecastResult,
    GetApplicantForecastUseCase,
    ReasonKind,
)
from app.infrastructure.db.repositories.program_repository import ProgramRepository
from app.presentation import content
from app.presentation.desktop import theme
from app.presentation.desktop.live import LiveResult, fetch_live_applications
from app.presentation.desktop.snapshot import SnapshotManager, SnapshotUnavailable

_PAD = 22

# Знак влияния фактора на шанс + tk-тег, которым красится строка объяснения.
_REASON_NEUTRAL = ("•", "why_neutral")
_REASON_STYLE = {
    ReasonKind.GOOD: ("▲", "why_good"),
    ReasonKind.BAD: ("▼", "why_bad"),
    ReasonKind.NEUTRAL: _REASON_NEUTRAL,
}


class FlatButton(tk.Label):
    """
    Кнопка на основе Label.

    Родные ttk.Button и tk.Button на macOS рисуются системной темой и молча
    игнорируют background — красная кнопка на них просто не получится. Label
    красится везде одинаково, поэтому клики и состояния навешиваем руками.
    """

    def __init__(self, master, text: str, command: Callable[[], None],
                 *, font, primary: bool = True, **kw) -> None:
        self._command = command
        self._primary = primary
        self._enabled = True
        super().__init__(
            master, text=text, font=font, cursor="hand2",
            padx=20, pady=10, borderwidth=0,
            **self._colors("normal"), **kw,
        )
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda _e: self._paint("hover"))
        self.bind("<Leave>", lambda _e: self._paint("normal"))

    def _colors(self, state: str) -> dict:
        if not self._enabled:
            # белым по бледно-розовому надпись не читается — гасим текст, не фон
            return ({"background": theme.RED_TINT, "foreground": theme.INK_FAINT}
                    if self._primary
                    else {"background": theme.SURFACE_2, "foreground": theme.INK_FAINT})
        if self._primary:
            bg = {"normal": theme.RED, "hover": theme.RED_DEEP, "press": theme.RED_DEEP}[state]
            return {"background": bg, "foreground": "#FFFFFF"}
        bg = {"normal": theme.SURFACE_2, "hover": theme.RED_TINT, "press": theme.RED_TINT}[state]
        return {"background": bg, "foreground": theme.INK if state == "normal" else theme.RED}

    def _paint(self, state: str) -> None:
        self.configure(**self._colors(state))

    def _on_press(self, _e) -> None:
        if self._enabled:
            self._paint("press")

    def _on_release(self, _e) -> None:
        if not self._enabled:
            return
        self._paint("hover")
        self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint("normal")


def _fmt_qrange(q90: Optional[float], q95: Optional[float]) -> str:
    if q90 is None or q95 is None:
        return "—"
    if q90 == q95:
        return f"{q90:.0f}"
    return f"{q90:.0f} – {q95:.0f}"


def _exam_text(exam) -> str:
    if exam.state is ExamState.PASSED:
        parts = []
        if exam.vi_score and exam.vi_score > 0:
            parts.append(f"{exam.vi_score}")
        if exam.id_achievements and exam.id_achievements > 0:
            parts.append(f"+{exam.id_achievements}")
        if exam.target_id_achievements and exam.target_id_achievements > 0:
            parts.append(f"+{exam.target_id_achievements}")
        parts.append(f"={exam.total_score}")
        return f"Сдан: {''.join(parts)}"
    if exam.state is ExamState.NOT_PUBLISHED:
        return "Расписание экзамена пока не опубликовано"
    if exam.state is ExamState.UPCOMING:
        dates = "; ".join(d.strftime("%d.%m %H:%M") for d in exam.upcoming_dates)
        return f"Ближайшие экзамены: {dates}{' …' if exam.more else ''}"
    last = exam.last_date.strftime("%d.%m %H:%M") if exam.last_date else "—"
    tail = "  ⚠️ прошло < 3 дней, баллы могут ещё обновиться" if exam.recently_finished else ""
    return f"Экзамены завершились (последняя дата: {last}){tail}"


class DesktopApp:
    def __init__(self, snapshot_url: str, cache_dir: Optional[Path] = None) -> None:
        self._snapshots = SnapshotManager(snapshot_url, cache_dir=cache_dir)
        self._db_path: Optional[Path] = None
        self._events: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._about_win: Optional[tk.Toplevel] = None

        self.root = tk.Tk()
        self.root.title("MasterChance — посмотри свои шансы")
        self.root.geometry("820x680")
        self.root.minsize(660, 500)
        self.root.configure(background=theme.PAPER)

        self._pick_fonts()
        self._build_widgets()
        self.root.after(80, self._drain_events)
        self._run_bg(self._load_snapshot)

    # ── шрифты ─────────────────────────────────────────────────────────────
    def _pick_fonts(self) -> None:
        """Выбрать ближайшее к сайту из того, что реально стоит в системе."""
        available = tkfont.families(self.root)
        self.f_display = theme.pick_family(available, theme.DISPLAY_STACK)
        self.f_sans = theme.pick_family(available, theme.SANS_STACK)
        self.f_mono = theme.pick_family(available, theme.MONO_STACK)

    # ── построение интерфейса ──────────────────────────────────────────────
    def _build_widgets(self) -> None:
        head = tk.Frame(self.root, background=theme.PAPER)
        head.pack(fill="x", padx=_PAD, pady=(_PAD, 0))

        tk.Label(
            head,
            text="Посмотри свои шансы на магистратуру",
            font=(self.f_display, 24),
            background=theme.PAPER, foreground=theme.INK,
            anchor="w", justify="left",
        ).pack(anchor="w")
        tk.Label(
            head,
            text="Введите свой уникальный код поступающего.",
            font=(self.f_sans, 11),
            background=theme.PAPER, foreground=theme.INK_SOFT,
        ).pack(anchor="w", pady=(6, 0))

        form = tk.Frame(self.root, background=theme.PAPER)
        form.pack(fill="x", padx=_PAD, pady=(18, 0))

        self.code_var = tk.StringVar()
        entry = tk.Entry(
            form, textvariable=self.code_var, font=(self.f_mono, 13), width=18,
            background=theme.SURFACE, foreground=theme.INK,
            insertbackground=theme.RED, relief="flat",
            highlightthickness=1,
            highlightbackground=theme.LINE, highlightcolor=theme.RED,
        )
        entry.pack(side="left", ipady=8, ipadx=8)
        entry.bind("<Return>", lambda _e: self._on_lookup())
        entry.focus_set()

        self.lookup_btn = FlatButton(form, "Показать шансы", self._on_lookup,
                                     font=(self.f_sans, 11, "bold"))
        self.lookup_btn.pack(side="left", padx=(10, 0))

        self.refresh_btn = FlatButton(form, "Обновить данные", self._on_refresh,
                                      font=(self.f_sans, 11), primary=False)
        self.refresh_btn.pack(side="left", padx=(8, 0))

        self.about_btn = FlatButton(form, "Как всё устроено", self._on_about,
                                    font=(self.f_sans, 11), primary=False)
        self.about_btn.pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Загрузка данных…")
        tk.Label(
            self.root, textvariable=self.status_var, font=(self.f_mono, 9),
            background=theme.PAPER, foreground=theme.INK_FAINT, anchor="w",
        ).pack(fill="x", padx=_PAD, pady=(12, 8))

        body = tk.Frame(self.root, background=theme.PAPER)
        body.pack(fill="both", expand=True, padx=_PAD, pady=(0, _PAD))

        self.out = tk.Text(
            body, wrap="word", font=(self.f_sans, 11), relief="flat",
            background=theme.SURFACE, foreground=theme.INK,
            highlightthickness=1, highlightbackground=theme.LINE,
            padx=18, pady=16, spacing1=1, spacing3=3,
            state="disabled",
        )
        scroll = ttk.Scrollbar(body, command=self.out.yview)
        self.out.configure(yscrollcommand=scroll.set)
        self.out.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Правовая оговорка и авторство обязаны быть видны всегда, а не только
        # в справке.
        foot = tk.Frame(self.root, background=theme.PAPER)
        foot.pack(fill="x", padx=_PAD, pady=(0, 14))

        self.footer = tk.Label(
            foot, text=f"{content.FOOTER_NOTE}\n{content.CREDIT_NOTE}.",
            font=(self.f_mono, 8),
            background=theme.PAPER, foreground=theme.INK_FAINT,
            justify="left", anchor="w", wraplength=760,
        )
        self.footer.pack(fill="x", anchor="w")

        repo = tk.Label(
            foot, text=f"{content.REPO_LABEL}: {content.REPO_URL}",
            font=(self.f_mono, 8),
            background=theme.PAPER, foreground=theme.RED,
            cursor="hand2", anchor="w",
        )
        repo.pack(fill="x", anchor="w", pady=(4, 0))
        repo.bind("<Button-1>", lambda _e: self._open_repo())

        self.root.bind("<Configure>", self._reflow_footer)

        self._configure_tags()

    def _open_repo(self) -> None:
        # браузер может не открыться (нет DE, нет прав) — это не повод падать
        try:
            webbrowser.open_new_tab(content.REPO_URL)
        except Exception:
            self._set_status(f"Откройте вручную: {content.REPO_URL}")

    def _reflow_footer(self, event) -> None:
        """Оговорка длинная: без пересчёта переноса она обрежется при сужении окна."""
        if event.widget is self.root:
            self.footer.configure(wraplength=max(event.width - 2 * _PAD, 240))

    def _configure_tags(self) -> None:
        t = self.out.tag_configure
        t("h1", font=(self.f_display, 19), foreground=theme.INK, spacing3=8)
        # отступ карточки живёт на «dept»: он идёт первой строкой направления
        t("dept", font=(self.f_mono, 9), foreground=theme.INK_FAINT, spacing1=26, spacing3=2)
        t("prog", font=(self.f_sans, 13, "bold"), foreground=theme.INK, spacing3=8)
        t("label", font=(self.f_mono, 9), foreground=theme.INK_FAINT)
        t("chance", font=(self.f_sans, 19, "bold"), foreground=theme.RED)
        t("bar_on", font=(self.f_mono, 9), foreground=theme.RED, spacing1=4)
        t("bar_off", font=(self.f_mono, 9), foreground=theme.RED_LINE, spacing1=4)
        t("mono", font=(self.f_mono, 10), foreground=theme.INK)
        t("muted", foreground=theme.INK_SOFT)
        t("fresh", foreground=theme.OK)
        t("warn", foreground=theme.WAIT)
        t("fail", font=(self.f_sans, 12, "bold"), foreground=theme.RED_DEEP, spacing1=20)
        t("why_head", font=(self.f_mono, 9), foreground=theme.INK_FAINT, spacing1=12, spacing3=4)
        t("why_good", foreground=theme.OK, lmargin1=16, lmargin2=30, spacing3=3)
        t("why_bad", foreground=theme.RED, lmargin1=16, lmargin2=30, spacing3=3)
        t("why_neutral", foreground=theme.INK_SOFT, lmargin1=16, lmargin2=30, spacing3=3)
        t("note", foreground=theme.INK_SOFT, lmargin1=16, lmargin2=30, spacing3=3)

    # ── фоновая работа: очередь колбэков в UI-поток ────────────────────────
    def _run_bg(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def _post(self, fn: Callable[[], None]) -> None:
        self._events.put(fn)

    def _drain_events(self) -> None:
        try:
            while True:
                self._events.get_nowait()()
        except queue.Empty:
            pass
        finally:
            self.root.after(80, self._drain_events)

    def _set_status(self, text: str) -> None:
        self._post(lambda: self.status_var.set(text))

    def _set_busy(self, busy: bool) -> None:
        self._post(lambda: (self.lookup_btn.set_enabled(not busy),
                            self.refresh_btn.set_enabled(not busy)))

    # ── «Как всё устроено» ─────────────────────────────────────────────────
    def _on_about(self) -> None:
        """Отдельное окно с механикой. Текст общий с сайтом (presentation/content.py)."""
        if getattr(self, "_about_win", None) is not None and self._about_win.winfo_exists():
            self._about_win.lift()
            self._about_win.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._about_win = win
        win.title(content.MECHANISM_TITLE)
        win.geometry("720x680")
        win.minsize(520, 420)
        win.configure(background=theme.PAPER)

        frame = tk.Frame(win, background=theme.PAPER)
        frame.pack(fill="both", expand=True, padx=_PAD, pady=_PAD)

        txt = tk.Text(
            frame, wrap="word", font=(self.f_sans, 11), relief="flat",
            background=theme.SURFACE, foreground=theme.INK,
            highlightthickness=1, highlightbackground=theme.LINE,
            padx=20, pady=18, state="normal", cursor="arrow",
        )
        bar = ttk.Scrollbar(frame, command=txt.yview)
        txt.configure(yscrollcommand=bar.set)
        txt.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        t = txt.tag_configure
        t("title", font=(self.f_display, 21), foreground=theme.INK, spacing3=10)
        t("intro", foreground=theme.INK_SOFT, spacing3=14)
        t("head", font=(self.f_sans, 12, "bold"), foreground=theme.INK,
          spacing1=22, spacing3=6)
        t("body", foreground=theme.INK_SOFT, spacing3=8)
        t("item", foreground=theme.INK_SOFT, lmargin1=18, lmargin2=32, spacing3=4)
        t("note", foreground=theme.RED_DEEP, lmargin1=14, lmargin2=14,
          spacing1=6, spacing3=8)

        txt.insert("end", f"{content.MECHANISM_TITLE}\n", "title")
        txt.insert("end", f"{content.MECHANISM_INTRO}\n", "intro")
        for section in content.MECHANISM:
            txt.insert("end", f"{section.title}\n", "head")
            for block in section.blocks:
                if block.kind == "list":
                    for item in block.items:
                        txt.insert("end", f"•  {item}\n", "item")
                else:
                    txt.insert("end", f"{block.text}\n",
                               "note" if block.kind == "note" else "body")

        txt.configure(state="disabled")
        win.bind("<Escape>", lambda _e: win.destroy())

    # ── снапшот ────────────────────────────────────────────────────────────
    def _load_snapshot(self, force: bool = False) -> None:
        self._set_busy(True)
        try:
            path = self._snapshots.ensure(force=force, progress=self._set_status)
            self._db_path = path
            self._set_status(self._snapshot_status())
        except SnapshotUnavailable as exc:
            self._set_status(f"Нет данных: {exc}")
        finally:
            self._set_busy(False)

    def _snapshot_status(self) -> str:
        meta = self._snapshots.read_meta()
        dt = meta.downloaded_dt if meta else None
        when = dt.astimezone().strftime("%d.%m.%Y %H:%M") if dt else "неизвестно"
        return f"Данные загружены: {when}. Готово к поиску."

    def _on_refresh(self) -> None:
        self._run_bg(lambda: self._load_snapshot(force=True))

    # ── поиск по коду ──────────────────────────────────────────────────────
    def _on_lookup(self) -> None:
        code = self.code_var.get().strip()
        if not code:
            return
        if not self._db_path:
            self._set_status("Данные ещё не загружены — подождите.")
            return
        self._run_bg(lambda: self._lookup(code))

    def _lookup(self, code: str) -> None:
        self._set_busy(True)
        self._set_status("Считаем шансы…")
        try:
            result = self._forecast(code)
            if result is None:
                self._render_not_found(code)
                self._set_status(self._snapshot_status())
                return

            self._render(result, live=None)
            self._set_status("Проверяем ваши актуальные баллы…")

            live = fetch_live_applications(code)
            if live:
                self._render(result, live=live)
                self._set_status("Готово. Ваши баллы обновлены только что.")
            else:
                self._set_status(
                    "Готово. Актуальные баллы получить не удалось — показаны данные снапшота."
                )
        except Exception as exc:  # UI не должен падать из-за одной неудачи
            self._set_status(f"Ошибка: {exc}")
        finally:
            self._set_busy(False)

    def _forecast(self, code: str) -> Optional[ForecastResult]:
        engine = create_engine(f"sqlite:///{self._db_path}", future=True)
        session = sessionmaker(bind=engine, future=True)()
        try:
            return GetApplicantForecastUseCase(ProgramRepository(session)).execute(code)
        finally:
            session.close()
            engine.dispose()

    # ── отрисовка ──────────────────────────────────────────────────────────
    def _render_not_found(self, code: str) -> None:
        def paint() -> None:
            self.out.configure(state="normal")
            self.out.delete("1.0", "end")
            self.out.insert("end", f"Заявок для кода {code} не найдено.\n", "h1")
            self.out.insert(
                "end",
                "Проверьте код или нажмите «Обновить данные» — возможно, "
                "снапшот старее ваших заявок.\n",
                "muted",
            )
            self.out.configure(state="disabled")

        self._post(paint)

    def _render(self, result: ForecastResult, live: Optional[LiveResult]) -> None:
        live_by_code = {a.program_code: a for a in live.applications} if live else {}

        def paint() -> None:
            self.out.configure(state="normal")
            self.out.delete("1.0", "end")

            self.out.insert("end", f"Абитуриент {result.applicant_id}\n", "h1")
            upd = result.last_update.strftime("%d.%m.%Y %H:%M") if result.last_update else "неизвестно"
            self.out.insert("end", f"Шансы рассчитаны на данных от {upd}\n", "muted")
            if live and live.generated_at:
                self.out.insert(
                    "end",
                    f"Ваши баллы — из отчёта от {live.generated_at:%d.%m.%Y %H:%M} (только что)\n",
                    "fresh",
                )

            for it in result.items:
                self.out.insert("end", f"{it.department_code}\n", "dept")
                self.out.insert("end", f"{it.program_name}\n", "prog")

                chance = f"{it.prob_cond * 100:.1f}%" if it.prob_cond is not None else "—"
                self.out.insert("end", "ШАНС ЗАЧИСЛЕНИЯ   ", "label")
                self.out.insert("end", f"{chance}\n", "chance")

                # шкала отметками, как на сайте: одна отметка — один исход
                on, off = theme.tick_bar(it.prob_cond)
                self.out.insert("end", on, "bar_on")
                self.out.insert("end", off + "\n", "bar_off")

                self.out.insert("end", "Проходной (90–95%): ", "label")
                self.out.insert("end", f"{_fmt_qrange(it.q90, it.q95)}\n", "mono")

                self.out.insert("end", f"{_exam_text(it.exam)}\n", "muted")

                fresh = live_by_code.get(it.program_code)
                if fresh:
                    consent = "есть" if fresh.consent else "нет"
                    self.out.insert(
                        "end",
                        f"Сейчас: балл {fresh.total_score}, приоритет {fresh.priority}, "
                        f"согласие {consent} — {fresh.review_status}\n",
                        "fresh",
                    )

                if it.reasons:
                    self.out.insert("end", "ПОЧЕМУ ТАКОЙ ШАНС\n", "why_head")
                    for reason in it.reasons:
                        # .get, а не []: новый вид объяснения не должен ронять
                        # приложение на чужой машине — пусть будет нейтральным
                        sign, tag = _REASON_STYLE.get(reason.kind, _REASON_NEUTRAL)
                        self.out.insert("end", f"{sign} {reason.text}\n", tag)

            # отступ задан spacing1 у тега; лишний \n дал бы двойной провал
            self.out.insert(
                "end",
                f"«Пролетел с магой»: {result.fail_cond * 100:.1f}% симуляций\n",
                "fail",
            )

            if result.notes:
                self.out.insert("end", "КАК ЧИТАТЬ ЭТИ ЧИСЛА\n", "why_head")
                for note in result.notes:
                    self.out.insert("end", f"• {note.text}\n", "note")

            self.out.insert(
                "end",
                "\nПрогноз — вероятностная модель, а не гарантия поступления.\n",
                "muted",
            )
            self.out.configure(state="disabled")

        self._post(paint)

    def run(self) -> None:
        self.root.mainloop()
