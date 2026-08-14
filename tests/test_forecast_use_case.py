"""
Тесты GetApplicantForecastUseCase — общего источника правды для бота,
веб-интерфейса и десктоп-клиента.

Проверяется именно арифметика прогноза: условные вероятности
(cond = uncond / (1 - p_excluded)), процент «пролёта», порядок направлений,
поведение при неполных данных.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.application.use_cases.get_applicant_forecast import (
    ExamState,
    GetApplicantForecastUseCase,
)


@pytest.fixture
def forecast(repo):
    return GetApplicantForecastUseCase(repo)


def test_unknown_applicant_returns_none(seed, forecast):
    seed.program("701")
    seed.commit()
    assert forecast.execute("нет-такого") is None


def test_applicant_without_applications_returns_none(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.commit()
    assert forecast.execute("A1") is None


def test_conditional_probability_divides_by_inclusion(seed, forecast):
    """cond = uncond / (1 - p_excluded)."""
    seed.program("701", name="Матмод")
    seed.applicant("A1")
    seed.application("701", "A1", priority=1)
    seed.probability("A1", "701", 0.45)
    seed.diagnostics("A1", p_excluded=0.10, p_fail_when_included=0.5)
    seed.stats("701")
    seed.commit()

    result = forecast.execute("A1")
    assert result is not None
    assert result.items[0].prob_cond == pytest.approx(0.45 / 0.9)  # 0.5


def test_conditional_probability_capped_at_one(seed, forecast):
    """Даже если uncond/(1-p_excl) > 1, наружу выходит не больше 100%."""
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1")
    seed.probability("A1", "701", 0.98)
    seed.diagnostics("A1", p_excluded=0.5, p_fail_when_included=0.0)
    seed.stats("701")
    seed.commit()

    result = forecast.execute("A1")
    assert result.items[0].prob_cond == 1.0


def test_fail_cond_comes_from_diagnostics(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1")
    seed.probability("A1", "701", 0.30)
    seed.diagnostics("A1", p_excluded=0.0, p_fail_when_included=0.62)
    seed.stats("701")
    seed.commit()

    assert forecast.execute("A1").fail_cond == pytest.approx(0.62)


def test_fail_cond_falls_back_without_diagnostics(seed, forecast):
    """Нет диагностики → «пролёт» = 1 - сумма безусловных (p_excluded=0)."""
    seed.program("701")
    seed.program("702")
    seed.applicant("A1")
    seed.application("701", "A1", priority=1)
    seed.application("702", "A1", priority=2)
    seed.probability("A1", "701", 0.30)
    seed.probability("A1", "702", 0.20)
    seed.stats("701")
    seed.commit()

    result = forecast.execute("A1")
    assert result.fail_cond == pytest.approx(0.5)
    # без диагностики условные равны безусловным
    assert result.items[0].prob_cond == pytest.approx(0.30)


def test_items_ordered_by_priority(seed, forecast):
    seed.program("701", name="Третья")
    seed.program("702", name="Первая")
    seed.program("703", name="Вторая")
    seed.applicant("A1")
    seed.application("701", "A1", priority=3)
    seed.application("702", "A1", priority=1)
    seed.application("703", "A1", priority=2)
    for code in ("701", "702", "703"):
        seed.probability("A1", code, 0.1)
        seed.stats(code)
    seed.commit()

    names = [i.program_name for i in forecast.execute("A1").items]
    assert names == ["Первая", "Вторая", "Третья"]


def test_quantiles_and_metadata_are_exposed(seed, forecast):
    seed.program("701", name="Матмод", department_code="01.04.02", university="spbgu")
    seed.applicant("A1", university="spbgu")
    seed.application("701", "A1")
    seed.probability("A1", "701", 0.4)
    seed.quantiles("701", q90=210.5, q95=225.0)
    seed.stats("701")
    seed.commit()

    result = forecast.execute("A1")
    item = result.items[0]
    assert (item.q90, item.q95) == (210.5, 225.0)
    assert item.program_name == "Матмод"
    assert item.department_code == "01.04.02"
    assert result.university == "spbgu"


def test_missing_quantiles_leave_none(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1")
    seed.probability("A1", "701", 0.4)
    seed.stats("701")
    seed.commit()

    item = forecast.execute("A1").items[0]
    assert item.q90 is None and item.q95 is None


def test_probability_absent_for_program_gives_none(seed, forecast):
    """Заявка есть, а вероятности по ней MC не дал — показываем «нет данных»."""
    seed.program("701")
    seed.program("702")
    seed.applicant("A1")
    seed.application("701", "A1", priority=1)
    seed.application("702", "A1", priority=2)
    seed.probability("A1", "701", 0.4)   # для 702 вероятности нет
    seed.stats("701")
    seed.commit()

    by_code = {i.program_code: i for i in forecast.execute("A1").items}
    assert by_code["701"].prob_cond == pytest.approx(0.4)
    assert by_code["702"].prob_cond is None


def test_last_update_is_localised_from_moscow(seed, forecast):
    """generated_at лежит в МСК без tz; наружу отдаём в settings.timezone (UTC)."""
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1")
    seed.probability("A1", "701", 0.4)
    seed.stats("701", generated_at=datetime(2026, 6, 22, 9, 0))
    seed.commit()

    last = forecast.execute("A1").last_update
    assert last is not None
    assert last.strftime("%d.%m.%Y %H:%M") == "22.06.2026 06:00"  # 09:00 МСК = 06:00 UTC


def test_exam_state_passed_when_scores_present(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", vi_score=99, id_achievements=3, total_score=102)
    seed.probability("A1", "701", 0.4)
    seed.stats("701")
    seed.commit()

    exam = forecast.execute("A1").items[0].exam
    assert exam.state is ExamState.PASSED
    assert (exam.vi_score, exam.id_achievements, exam.total_score) == (99, 3, 102)


def test_exam_state_upcoming_when_future_sessions(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1")           # баллов нет
    seed.probability("A1", "701", 0.4)
    seed.stats("701")
    seed.exam_in_days("701", 5)
    seed.exam_in_days("701", 9)
    seed.commit()

    exam = forecast.execute("A1").items[0].exam
    assert exam.state is ExamState.UPCOMING
    assert len(exam.upcoming_dates) == 2
    assert exam.more is False


def test_exam_state_not_published_without_sessions(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1")
    seed.probability("A1", "701", 0.4)
    seed.stats("701")
    seed.commit()

    assert forecast.execute("A1").items[0].exam.state is ExamState.NOT_PUBLISHED


def test_exam_state_finished_when_all_past(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1")
    seed.probability("A1", "701", 0.4)
    seed.stats("701")
    seed.exam_in_days("701", -30)
    seed.commit()

    exam = forecast.execute("A1").items[0].exam
    assert exam.state is ExamState.FINISHED
    assert exam.recently_finished is False
    assert exam.last_date is not None


# ── вуз не опубликовал число мест ───────────────────────────────────────────
def test_no_seats_means_no_percentage(seed, forecast):
    """
    Пока мест нет, конкурса не существует: модель раздаёт нули, и «0.0%»
    выглядело бы посчитанным ответом. Так, например, устроены выгрузки ВШЭ —
    числа мест в них нет вовсе.
    """
    seed.program("hse:p1", name="Анализ данных", university="hse")
    seed.applicant("hse:A1", university="hse")
    seed.application("hse:p1", "hse:A1", priority=1, total_score=90, vi_score=90)
    seed.stats("hse:p1", num_places=0)          # вуз мест не объявил
    seed.probability("hse:A1", "hse:p1", 0.0)   # Монте-Карло без мест даёт ноль
    seed.diagnostics("hse:A1", p_excluded=0.0, p_fail_when_included=1.0)
    seed.commit()

    item = forecast.execute("hse:A1").items[0]

    assert item.prob_cond is None, "без мест шанс показывать нечем — нужен прочерк"
    assert any("мест" in r.text for r in item.reasons), \
        "человеку надо объяснить, почему вместо процента прочерк"


def test_seats_published_bring_the_percentage_back(seed, forecast):
    seed.program("hse:p1", name="Анализ данных", university="hse")
    seed.applicant("hse:A1", university="hse")
    seed.application("hse:p1", "hse:A1", priority=1, total_score=90, vi_score=90)
    seed.stats("hse:p1", num_places=25)
    seed.probability("hse:A1", "hse:p1", 0.42)
    seed.diagnostics("hse:A1", p_excluded=0.0, p_fail_when_included=0.58)
    seed.commit()

    assert forecast.execute("hse:A1").items[0].prob_cond == pytest.approx(0.42)
