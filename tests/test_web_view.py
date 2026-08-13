"""
Тесты app/presentation/web/view.py — превращения структуры прогноза в
контекст для Jinja-шаблонов.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.application.use_cases.get_applicant_forecast import (
    ExamState,
    ExamStatus,
    ForecastItem,
    ForecastResult,
    Reason,
    ReasonKind,
)
from app.presentation.web.view import (
    exam_view,
    fmt_update,
    group_view,
    reason_view,
    to_view,
)


def _item(**kw) -> ForecastItem:
    base = dict(
        program_code="701",
        program_name="Матмод",
        department_code="01.04.02",
        prob_cond=0.4167,
        q90=221.7,
        q95=225.8,
        exam=ExamStatus(state=ExamState.NOT_PUBLISHED),
    )
    base.update(kw)
    return ForecastItem(**base)


def _result(**kw) -> ForecastResult:
    base = dict(
        applicant_id="A1",
        university="spbgu",
        items=[_item()],
        fail_cond=0.56,
        last_update=datetime(2026, 6, 22, 6, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return ForecastResult(**base)


def test_programs_key_not_named_items():
    """
    В Jinja `view.items` резолвится в метод dict.items, а не в наш список,
    поэтому ключ обязан называться иначе. Регрессия на реальный баг.
    """
    group = group_view(_result())
    assert "programs" in group
    assert "items" not in group
    assert isinstance(group["programs"], list)


def test_percentages_and_bar_width():
    group = group_view(_result())
    program = group["programs"][0]
    assert program["prob_pct"] == "41.7%"
    assert program["prob_width"] == 42          # ширина полосы — целые проценты
    assert group["fail_pct"] == "56.0%"


def test_missing_probability_renders_dash():
    program = group_view(_result(items=[_item(prob_cond=None)]))["programs"][0]
    assert program["prob_pct"] == "—"
    assert program["prob_width"] == 0           # без данных полосы нет


def test_qrange_formats():
    assert group_view(_result(items=[_item(q90=221.7, q95=225.8)]))["programs"][0]["qrange"] == "222 – 226"
    # одинаковые квантили — показываем одно число, а не «222 – 222»
    assert group_view(_result(items=[_item(q90=222.0, q95=222.0)]))["programs"][0]["qrange"] == "222"
    assert group_view(_result(items=[_item(q90=None, q95=None)]))["programs"][0]["qrange"] == "—"


def test_university_label_is_human_readable():
    assert group_view(_result(university="spbgu"))["label"] == "СПбГУ"
    assert group_view(_result(university="hse"))["label"] == "ВШЭ"
    # незнакомый ключ отдаём как есть, пустой — подписываем нейтрально
    assert group_view(_result(university="mit"))["label"] == "mit"
    assert group_view(_result(university=None))["label"] == "Вуз"


def test_each_university_gets_its_own_group():
    """Программы разных вузов не должны оказаться в одном списке."""
    view = to_view([
        _result(university="spbgu", applicant_id="1000004",
                items=[_item(program_name="Матмод")]),
        _result(university="hse", applicant_id="777",
                items=[_item(program_name="Прикладная экономика")]),
    ])

    assert [g["key"] for g in view["groups"]] == ["spbgu", "hse"]
    assert [g["label"] for g in view["groups"]] == ["СПбГУ", "ВШЭ"]
    assert [len(g["programs"]) for g in view["groups"]] == [1, 1]
    assert view["multi"] is True
    # коды у вузов разные — общий заголовок соврал бы
    assert view["applicant_id"] == ""


def test_single_result_still_renders_one_group():
    view = to_view(_result(applicant_id="1000004"))
    assert len(view["groups"]) == 1
    assert view["multi"] is False
    assert view["applicant_id"] == "1000004"


def test_empty_lookup_gives_no_view():
    assert to_view([]) is None
    assert to_view(None) is None


def test_exam_view_passed_composes_score_string():
    view = exam_view(
        ExamStatus(
            state=ExamState.PASSED,
            vi_score=99,
            id_achievements=3,
            target_id_achievements=0,
            total_score=102,
        )
    )
    assert view["cls"] == "ok"
    assert view["text"] == "Сдан: 99+3=102"
    assert view["warn"] is None


def test_exam_view_passed_omits_zero_achievements():
    view = exam_view(
        ExamStatus(state=ExamState.PASSED, vi_score=88, id_achievements=0,
                   target_id_achievements=0, total_score=88)
    )
    assert view["text"] == "Сдан: 88=88"


def test_exam_view_upcoming_lists_dates():
    dates = [datetime(2026, 7, 1, 10, 0), datetime(2026, 7, 5, 14, 30)]
    view = exam_view(ExamStatus(state=ExamState.UPCOMING, upcoming_dates=dates, more=True))
    assert view["cls"] == "wait"
    assert view["text"] == "Ближайшие экзамены: 01.07 10:00; 05.07 14:30 …"


def test_exam_view_not_published():
    view = exam_view(ExamStatus(state=ExamState.NOT_PUBLISHED))
    assert view["cls"] == "wait"
    assert "не опубликовано" in view["text"]


def test_exam_view_finished_with_and_without_warning():
    plain = exam_view(
        ExamStatus(state=ExamState.FINISHED, last_date=datetime(2026, 6, 1, 12, 0))
    )
    assert plain["cls"] == "done"
    assert "01.06 12:00" in plain["text"]
    assert plain["warn"] is None

    recent = exam_view(
        ExamStatus(state=ExamState.FINISHED, last_date=datetime(2026, 6, 1, 12, 0),
                   recently_finished=True)
    )
    assert recent["warn"] is not None


def test_fmt_update():
    assert fmt_update(datetime(2026, 6, 22, 6, 0)) == "22.06.2026 06:00"
    assert fmt_update(None) == "нет данных"


# ───────────────────── объяснение «почему такой шанс» ────────────────────────

def test_reason_view_maps_kind_to_css_class_and_icon():
    assert reason_view(Reason(ReasonKind.GOOD, "х"))["cls"] == "good"
    assert reason_view(Reason(ReasonKind.BAD, "х"))["cls"] == "bad"
    assert reason_view(Reason(ReasonKind.NEUTRAL, "х"))["cls"] == "neutral"
    assert reason_view(Reason(ReasonKind.GOOD, "х"))["icon"] == "\u25b2"
    assert reason_view(Reason(ReasonKind.BAD, "х"))["icon"] == "\u25bc"


def test_reasons_land_on_the_program_card():
    view = group_view(_result(items=[_item(reasons=[
        Reason(ReasonKind.GOOD, "По баллу вы 1-й из 10."),
        Reason(ReasonKind.BAD, "Ещё 5 человек без баллов."),
    ])]))
    reasons = view["programs"][0]["reasons"]

    assert [r["cls"] for r in reasons] == ["good", "bad"]
    assert reasons[0]["text"] == "По баллу вы 1-й из 10."


def test_notes_are_plain_strings_for_the_template():
    view = group_view(_result(notes=[Reason(ReasonKind.NEUTRAL, "Проходной показан вилкой.")]))
    assert view["notes"] == ["Проходной показан вилкой."]


def test_view_without_reasons_has_empty_lists():
    """Старый снапшот без объяснений — шаблон просто не покажет блок."""
    view = group_view(_result())
    assert view["programs"][0]["reasons"] == []
    assert view["notes"] == []
