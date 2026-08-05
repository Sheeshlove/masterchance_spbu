"""
Тесты _build_exam_status — чистой функции, определяющей, что показать про
вступительное испытание: баллы, ближайшие даты, «расписания нет» или
«экзамены завершились».

Без БД: на вход подаются доменные объекты напрямую.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.application.use_cases.get_applicant_forecast import (
    ExamState,
    _build_exam_status,
)
from app.domain.models import Application, ExamSession

MSK = ZoneInfo("Europe/Moscow")


def _now() -> datetime:
    return datetime.now(MSK).replace(tzinfo=None)


def _app(**kw) -> Application:
    base = dict(
        program_code="701",
        applicant_id="A1",
        total_score=0,
        vi_score=0,
        subject1_score=0,
        subject2_score=0,
        id_achievements=0,
        target_id_achievements=0,
        priority=1,
        consent=False,
        review_status="Участвует в конкурсе",
    )
    base.update(kw)
    return Application(**base)


def _session(days: float) -> ExamSession:
    return ExamSession(program_code="701", exam_code="ВИ-1", dt=_now() + timedelta(days=days))


def test_no_application_and_no_sessions_is_not_published():
    status = _build_exam_status(None, None)
    assert status.state is ExamState.NOT_PUBLISHED


def test_passed_by_vi_score():
    status = _build_exam_status(_app(vi_score=88, total_score=88), [])
    assert status.state is ExamState.PASSED
    assert status.vi_score == 88


def test_passed_by_subject1_even_if_vi_is_zero():
    """Балл может стоять только по предмету — это всё равно «сдан»."""
    status = _build_exam_status(_app(subject1_score=70, total_score=70), [])
    assert status.state is ExamState.PASSED


def test_zero_scores_are_not_passed():
    """Нули — это «ещё не сдавал», а не результат."""
    status = _build_exam_status(_app(), [_session(3)])
    assert status.state is ExamState.UPCOMING


def test_scores_win_over_schedule():
    """Если баллы есть, даты уже не показываем."""
    status = _build_exam_status(_app(vi_score=75, total_score=75), [_session(5)])
    assert status.state is ExamState.PASSED


def test_upcoming_shows_at_most_three_dates_and_more_flag():
    sessions = [_session(1), _session(2), _session(3), _session(4)]
    status = _build_exam_status(_app(), sessions)
    assert status.state is ExamState.UPCOMING
    assert len(status.upcoming_dates) == 3
    assert status.more is True


def test_upcoming_ignores_past_sessions():
    status = _build_exam_status(_app(), [_session(-10), _session(4)])
    assert status.state is ExamState.UPCOMING
    assert len(status.upcoming_dates) == 1
    assert status.more is False


def test_finished_long_ago_has_no_warning():
    status = _build_exam_status(_app(), [_session(-30), _session(-20)])
    assert status.state is ExamState.FINISHED
    assert status.recently_finished is False
    assert status.last_date is not None


def test_finished_recently_raises_warning():
    """Меньше 3 дней после последнего экзамена — баллы могут ещё обновиться."""
    status = _build_exam_status(_app(), [_session(-1)])
    assert status.state is ExamState.FINISHED
    assert status.recently_finished is True


def test_finished_exactly_over_three_days_has_no_warning():
    status = _build_exam_status(_app(), [_session(-3.1)])
    assert status.state is ExamState.FINISHED
    assert status.recently_finished is False


def test_upcoming_dates_are_converted_to_target_timezone():
    """Даты в БД — МСК без tz; наружу отдаём tz-aware в зоне настроек."""
    status = _build_exam_status(_app(), [_session(2)])
    assert status.upcoming_dates[0].tzinfo is not None
