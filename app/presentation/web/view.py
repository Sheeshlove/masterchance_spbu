"""
Преобразование структуры прогноза (`ForecastResult`) в «глупый» контекст для
Jinja-шаблонов. Вынесено из `app.py`, чтобы не тянуть FastAPI и чтобы логику
можно было покрыть юнит-тестами отдельно.
"""
from __future__ import annotations

from datetime import datetime

from app.application.use_cases.get_applicant_forecast import (
    ExamState,
    ExamStatus,
    ForecastResult,
)

UNIVERSITY_LABELS = {"spbgu": "СПбГУ"}


def exam_view(exam: ExamStatus) -> dict:
    if exam.state is ExamState.PASSED:
        parts = []
        if exam.vi_score and exam.vi_score > 0:
            parts.append(f"{exam.vi_score}")
        if exam.id_achievements and exam.id_achievements > 0:
            parts.append(f"+{exam.id_achievements}")
        if exam.target_id_achievements and exam.target_id_achievements > 0:
            parts.append(f"+{exam.target_id_achievements}")
        parts.append(f"={exam.total_score}")
        return {"cls": "ok", "icon": "🟢", "text": f"Сдан: {''.join(parts)}", "warn": None}

    if exam.state is ExamState.NOT_PUBLISHED:
        return {"cls": "wait", "icon": "🟡", "text": "Расписание экзамена пока не опубликовано", "warn": None}

    if exam.state is ExamState.UPCOMING:
        dates = "; ".join(d.strftime("%d.%m %H:%M") for d in exam.upcoming_dates)
        more = " …" if exam.more else ""
        return {"cls": "wait", "icon": "🟡", "text": f"Ближайшие экзамены: {dates}{more}", "warn": None}

    # FINISHED
    last = exam.last_date.strftime("%d.%m %H:%M") if exam.last_date else "—"
    warn = "прошло < 3 дней — результаты могут ещё обновляться" if exam.recently_finished else None
    return {"cls": "done", "icon": "⚪", "text": f"Экзамены завершились (последняя дата: {last})", "warn": warn}


def to_view(result: ForecastResult) -> dict:
    items = []
    for it in result.items:
        prob_pct = f"{it.prob_cond * 100:.1f}%" if it.prob_cond is not None else "—"
        if it.q90 is None or it.q95 is None:
            qrange = "—"
        elif it.q90 == it.q95:
            qrange = f"{it.q90:.0f}"
        else:
            qrange = f"{it.q90:.0f} – {it.q95:.0f}"
        items.append({
            "dept": it.department_code,
            "name": it.program_name,
            "prob_pct": prob_pct,
            "prob_width": round((it.prob_cond or 0.0) * 100),
            "qrange": qrange,
            "exam": exam_view(it.exam),
        })
    return {
        "applicant_id": result.applicant_id,
        "university": UNIVERSITY_LABELS.get(result.university or "", result.university or ""),
        "fail_pct": f"{result.fail_cond * 100:.1f}%",
        # ключ НЕ называем "items": в Jinja `view.items` резолвится в метод dict.items
        "programs": items,
    }


def fmt_update(dt: datetime | None) -> str:
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "нет данных"
