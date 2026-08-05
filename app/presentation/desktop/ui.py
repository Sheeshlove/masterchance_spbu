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
from pathlib import Path
from tkinter import ttk
from typing import Callable, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.use_cases.get_applicant_forecast import (
    ExamState,
    ForecastResult,
    GetApplicantForecastUseCase,
)
from app.infrastructure.db.repositories.program_repository import ProgramRepository
from app.presentation.desktop.live import LiveResult, fetch_live_applications
from app.presentation.desktop.snapshot import SnapshotManager, SnapshotUnavailable

_PAD = 12


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

        self.root = tk.Tk()
        self.root.title("MasterChance — посмотри свои шансы")
        self.root.geometry("760x620")
        self.root.minsize(620, 460)

        self._build_widgets()
        self.root.after(80, self._drain_events)
        self._run_bg(self._load_snapshot)

    # ── построение интерфейса ──────────────────────────────────────────────
    def _build_widgets(self) -> None:
        head = ttk.Frame(self.root, padding=(_PAD, _PAD, _PAD, 0))
        head.pack(fill="x")

        ttk.Label(
            head,
            text="Посмотри свои шансы на магистратуру",
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            head,
            text="Введите свой уникальный код поступающего.",
            foreground="#555",
        ).pack(anchor="w", pady=(2, 0))

        form = ttk.Frame(self.root, padding=(_PAD, _PAD, _PAD, 0))
        form.pack(fill="x")

        self.code_var = tk.StringVar()
        entry = ttk.Entry(form, textvariable=self.code_var, font=("Segoe UI", 12), width=22)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self._on_lookup())
        entry.focus_set()

        self.lookup_btn = ttk.Button(form, text="Показать шансы", command=self._on_lookup)
        self.lookup_btn.pack(side="left", padx=(8, 0))

        self.refresh_btn = ttk.Button(form, text="Обновить данные", command=self._on_refresh)
        self.refresh_btn.pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Загрузка данных…")
        ttk.Label(
            self.root, textvariable=self.status_var, foreground="#555", padding=(_PAD, 6)
        ).pack(fill="x")

        body = ttk.Frame(self.root, padding=(_PAD, 0, _PAD, _PAD))
        body.pack(fill="both", expand=True)

        self.out = tk.Text(body, wrap="word", font=("Segoe UI", 10), relief="flat",
                           background="#fbfbfd", borderwidth=1, state="disabled")
        scroll = ttk.Scrollbar(body, command=self.out.yview)
        self.out.configure(yscrollcommand=scroll.set)
        self.out.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.out.tag_configure("h1", font=("Segoe UI", 13, "bold"), spacing3=6)
        self.out.tag_configure("prog", font=("Segoe UI", 11, "bold"), spacing1=10)
        self.out.tag_configure("dept", foreground="#777")
        self.out.tag_configure("chance", font=("Segoe UI", 11, "bold"), foreground="#1f5fd0")
        self.out.tag_configure("muted", foreground="#666")
        self.out.tag_configure("fresh", foreground="#1a7f37")
        self.out.tag_configure("warn", foreground="#b26a00")
        self.out.tag_configure("fail", font=("Segoe UI", 11, "bold"), foreground="#b3261e",
                               spacing1=12)

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
        state = "disabled" if busy else "normal"
        self._post(lambda: (self.lookup_btn.configure(state=state),
                            self.refresh_btn.configure(state=state)))

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
                self.out.insert("end", f"\n{it.program_name}\n", "prog")
                self.out.insert("end", f"{it.department_code}\n", "dept")

                chance = f"{it.prob_cond * 100:.1f}%" if it.prob_cond is not None else "—"
                self.out.insert("end", "Шанс зачисления: ")
                self.out.insert("end", chance, "chance")
                self.out.insert(
                    "end", f"    Проходной (90–95%): {_fmt_qrange(it.q90, it.q95)}\n", "muted"
                )

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

            self.out.insert(
                "end",
                f"\n«Пролетел с магой»: {result.fail_cond * 100:.1f}% симуляций\n",
                "fail",
            )
            self.out.insert(
                "end",
                "\nПрогноз — вероятностная модель, а не гарантия поступления.\n",
                "muted",
            )
            self.out.configure(state="disabled")

        self._post(paint)

    def run(self) -> None:
        self.root.mainloop()
