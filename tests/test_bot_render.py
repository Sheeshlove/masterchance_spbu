"""
Тесты Markdown-рендеринга Telegram-бота.

После выноса расчётов в общий GetApplicantForecastUseCase бот только
форматирует готовую структуру — здесь проверяется именно форматирование,
включая нарезку длинных сообщений под лимит Telegram.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("aiogram", reason="бот использует aiogram")

from app.application.use_cases.get_applicant_forecast import (  # noqa: E402
    ExamState,
    ExamStatus,
    ForecastItem,
    ForecastResult,
)
from app.presentation.bot import (  # noqa: E402
    _fmt_qrange,
    _render_exam_line,
    _render_forecast,
    split_message,
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


# ── _fmt_qrange ────────────────────────────────────────────────────────────
def test_fmt_qrange_variants():
    assert _fmt_qrange(221.7, 225.8) == "222 - 226"
    assert _fmt_qrange(222.0, 222.0) == "222"      # одинаковые — одно число
    assert _fmt_qrange(None, None) == "—"
    assert _fmt_qrange(221.0, None) == "—"


# ── _render_exam_line ──────────────────────────────────────────────────────
def test_exam_line_passed():
    line = _render_exam_line(
        ExamStatus(state=ExamState.PASSED, vi_score=99, id_achievements=3,
                   target_id_achievements=0, total_score=102)
    )
    assert "🟢" in line
    assert "99+3=**102**" in line


def test_exam_line_passed_skips_zero_achievements():
    line = _render_exam_line(
        ExamStatus(state=ExamState.PASSED, vi_score=88, id_achievements=0,
                   target_id_achievements=0, total_score=88)
    )
    assert "88=**88**" in line
    assert "+0" not in line


def test_exam_line_not_published():
    assert "🟡" in _render_exam_line(ExamStatus(state=ExamState.NOT_PUBLISHED))


def test_exam_line_upcoming():
    line = _render_exam_line(
        ExamStatus(
            state=ExamState.UPCOMING,
            upcoming_dates=[datetime(2026, 7, 1, 10, 0), datetime(2026, 7, 5, 14, 30)],
            more=True,
        )
    )
    assert "01.07 10:00; 05.07 14:30 …" in line


def test_exam_line_finished_with_warning():
    status = ExamStatus(state=ExamState.FINISHED,
                        last_date=datetime(2026, 6, 1, 12, 0),
                        recently_finished=True)
    line = _render_exam_line(status)
    assert "⚪" in line and "01.06 12:00" in line
    assert "⚠️" in line and "< 3 дней" in line


def test_exam_line_finished_without_warning_has_no_second_line():
    line = _render_exam_line(
        ExamStatus(state=ExamState.FINISHED, last_date=datetime(2026, 6, 1, 12, 0))
    )
    assert "⚠️" not in line
    assert "\n" not in line


# ── _render_forecast ───────────────────────────────────────────────────────
def test_render_forecast_contains_all_sections():
    text = _render_forecast(_result())
    assert "📝 *Ваши направления*" in text
    assert "🔮 *Прогноз зачисления*" in text
    assert "🚫 *«Пролетел с магой»*" in text


def test_render_forecast_shows_percentages_and_passing_scores():
    text = _render_forecast(_result())
    assert "*41.7%*" in text
    assert "(проходной: 222 - 226)" in text
    assert "*56.0%*" in text


def test_render_forecast_lists_programs_in_order():
    result = _result(items=[
        _item(program_code="701", program_name="Первая"),
        _item(program_code="702", program_name="Вторая"),
    ])
    text = _render_forecast(result)
    assert text.index("Первая") < text.index("Вторая")
    assert "`01.04.02`" in text


def test_render_forecast_handles_missing_probability():
    text = _render_forecast(_result(items=[_item(prob_cond=None, q90=None, q95=None)]))
    assert "*—*" in text
    assert "(проходной: —)" in text


# ── split_message ──────────────────────────────────────────────────────────
def test_split_message_keeps_short_text_intact():
    assert split_message("короткий текст", max_len=100) == ["короткий текст"]


def test_split_message_splits_on_newlines():
    parts = split_message("line1\nline2\nline3", max_len=12)
    assert parts == ["line1\nline2", "line3"]


def test_split_message_respects_limit_without_newlines():
    parts = split_message("a" * 25, max_len=10)
    assert all(len(p) <= 10 for p in parts)
    assert "".join(parts) == "a" * 25


def test_split_message_empty_text_gives_no_parts():
    assert split_message("", max_len=10) == []


def test_real_forecast_fits_telegram_limit():
    """Длинный список направлений всё равно нарезается под лимит Telegram."""
    result = _result(items=[_item(program_code=str(700 + i),
                                  program_name=f"Направление {i}") for i in range(60)])
    parts = split_message(_render_forecast(result))
    assert all(len(p) <= 4000 for p in parts)
    assert len(parts) >= 1
