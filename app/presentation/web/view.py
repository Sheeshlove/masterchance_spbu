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
    Reason,
    ReasonKind,
    Strategy,
)

UNIVERSITY_LABELS = {"spbgu": "СПбГУ"}

REASON_ICONS = {
    ReasonKind.GOOD: "▲",
    ReasonKind.BAD: "▼",
    ReasonKind.NEUTRAL: "•",
}


def reason_view(reason: Reason) -> dict:
    """Пояснение «почему такой шанс» → цвет и значок для шаблона."""
    return {
        "cls": reason.kind.value,
        "icon": REASON_ICONS[reason.kind],
        "text": reason.text,
    }


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


def strategy_view(strategy: Strategy | None) -> dict | None:
    """Выжимка «что делать» → контекст шаблона. None — блок просто не рисуем."""
    if strategy is None:
        return None
    return {
        "outlook": strategy.outlook.value,
        "headline": strategy.headline,
        "detail": strategy.detail,
        "steps": [reason_view(s) for s in strategy.steps],
    }


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
            "reasons": [reason_view(r) for r in it.reasons],
        })
    return {
        "applicant_id": result.applicant_id,
        "university": UNIVERSITY_LABELS.get(result.university or "", result.university or ""),
        "fail_pct": f"{result.fail_cond * 100:.1f}%",
        # getattr, а не точка: десктоп читает снапшоты, собранные до появления
        # выжимки, и падать из-за отсутствующего поля он не должен
        "strategy": strategy_view(getattr(result, "strategy", None)),
        # ключ НЕ называем "items": в Jinja `view.items` резолвится в метод dict.items
        "programs": items,
        "notes": [n.text for n in result.notes],
    }


def fmt_update(dt: datetime | None) -> str:
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "нет данных"
