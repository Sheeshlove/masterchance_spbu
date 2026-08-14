"""
Преобразование структуры прогноза (`ForecastResult`) в «глупый» контекст для
Jinja-шаблонов. Вынесено из `app.py`, чтобы не тянуть FastAPI и чтобы логику
можно было покрыть юнит-тестами отдельно.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

from app.application.use_cases.get_applicant_forecast import (
    NO_SEATS,
    RESULTS_PENDING,
    ExamState,
    ExamStatus,
    ForecastResult,
    Reason,
    ReasonKind,
    _forecast_blocker,
)
from app.domain.universities import label as university_label

REASON_ICONS = {
    ReasonKind.GOOD: "▲",
    ReasonKind.BAD: "▼",
    ReasonKind.NEUTRAL: "•",
}


def score_breakdown(exam: ExamStatus) -> str:
    """
    Балл слагаемыми: «93+5=98».

    Разбивку печатают не все: ВШЭ публикует только сумму конкурсных баллов, без
    отдельных колонок за испытание и за индивидуальные достижения. Складывать
    там нечего, и «100=100» — не разбивка, а шум, поэтому в таком случае
    показывается одно число.
    """
    parts: list[str] = []
    if exam.vi_score and exam.vi_score > 0:
        parts.append(f"{exam.vi_score}")
    if exam.id_achievements and exam.id_achievements > 0:
        parts.append(f"+{exam.id_achievements}")
    if exam.target_id_achievements and exam.target_id_achievements > 0:
        parts.append(f"+{exam.target_id_achievements}")

    if len(parts) <= 1:
        return f"{exam.total_score}"
    return f"{''.join(parts)}={exam.total_score}"


def reason_view(reason: Reason) -> dict:
    """Пояснение «почему такой шанс» → цвет и значок для шаблона."""
    return {
        "cls": reason.kind.value,
        "icon": REASON_ICONS[reason.kind],
        "text": reason.text,
    }


def exam_view(exam: ExamStatus) -> dict:
    if exam.state is ExamState.PASSED:
        return {"cls": "ok", "icon": "🟢", "text": f"Сдан: {score_breakdown(exam)}", "warn": None}

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


def _no_forecast_note(result: ForecastResult) -> str:
    """
    Почему по этому вузу нет ни одного шанса — одной фразой.

    Пустая строка, если шансы есть: тогда вкладка показывает обычный итог.
    Причина берётся у самих направлений, чтобы объяснение на вкладке и
    объяснение под карточкой не разошлись.
    """
    if any(it.prob_cond is not None for it in result.items):
        return ""

    blockers = {_forecast_blocker(it.competition) for it in result.items}
    blockers.discard(None)
    if blockers == {RESULTS_PENDING}:
        return ("вуз ещё не выставил баллы за вступительные испытания — "
                "конкурс не определён, и шанс считать не на чем. "
                "Заявки, приоритеты и согласия показаны как есть.")
    if NO_SEATS in blockers:
        return ("вуз не опубликовал число мест ни по одному вашему направлению — "
                "шанс считать не на чем. Заявки и баллы показаны как есть.")
    return ("прогноза по этому вузу пока нет — под каждым направлением написано, "
            "чего не хватает.")


def group_view(result: ForecastResult) -> dict:
    """
    Один вуз = одна вкладка.

    Смешивать программы разных вузов в общем списке нельзя: у каждого вуза свой
    конкурс, свои места и свой «пролетел» — сложить их в одну колонку значило бы
    показать сумму, которой не существует.
    """
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
        "key": result.university or "",
        "label": university_label(result.university) or "Вуз",
        "applicant_id": result.applicant_id,
        "fail_pct": f"{result.fail_cond * 100:.1f}%",
        # Ни по одному направлению прогноза нет — считать было не на чем, и
        # «в 100% симуляций не прошёл никуда» здесь означало бы не результат
        # модели, а отсутствие данных у вуза.
        "has_chances": any(it.prob_cond is not None for it in result.items),
        "no_forecast_note": _no_forecast_note(result),
        # ключ НЕ называем "items": в Jinja `view.items` резолвится в метод dict.items
        "programs": items,
        "notes": [n.text for n in result.notes],
    }


def to_view(results: ForecastResult | Sequence[ForecastResult] | None) -> dict | None:
    """
    Результаты прогноза → контекст страницы со вкладками по вузам.

    Принимает и один ForecastResult, и список: вызывающему не нужно помнить,
    нашёлся код в одном вузе или в трёх.
    """
    if results is None:
        return None
    if isinstance(results, ForecastResult):
        results = [results]
    groups = [group_view(r) for r in results]
    if not groups:
        return None
    return {
        "groups": groups,
        # Код показываем в шапке, только когда он один на все вкладки: у
        # разных вузов коды разные, и общий заголовок соврал бы.
        "applicant_id": groups[0]["applicant_id"] if len({g["applicant_id"] for g in groups}) == 1 else "",
        "multi": len(groups) > 1,
    }


def fmt_update(dt: datetime | None) -> str:
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "нет данных"


def fmt_codes(codes: Iterable[str]) -> str:
    """Список кодов → строка для поля ввода."""
    return ", ".join(codes)
