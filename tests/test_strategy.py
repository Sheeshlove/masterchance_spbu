"""
Выжимка «как поступить» перед списком направлений.

Карточки отвечают на «какой у меня шанс здесь», а человек приходит с вопросом
«что мне сделать». Здесь проверяется, что выжимка отвечает именно на второй и
не советует того, чего данные не подтверждают.
"""
from __future__ import annotations

from datetime import datetime

from app.application.use_cases.get_applicant_forecast import (
    ExamState,
    ExamStatus,
    ForecastItem,
    Outlook,
    ReasonKind,
    _build_strategy,
)
from app.domain.models import ProgramCompetition


def item(
    name: str,
    prob: float | None,
    *,
    priority: int = 1,
    score: int | None = 80,
    q90: float | None = 70.0,
    seats: int | None = 20,
    applications: int = 200,
    consent: bool = True,
    exam: ExamStatus | None = None,
) -> ForecastItem:
    return ForecastItem(
        program_code=name,
        program_name=name,
        department_code="38.04.02",
        prob_cond=prob,
        q90=q90,
        q95=(q90 + 6) if q90 is not None else None,
        exam=exam or ExamStatus(state=ExamState.PASSED, total_score=score),
        competition=ProgramCompetition(
            program_code=name,
            seats=seats,
            applications=applications,
            scored_rivals=max(applications - 1, 0),
            better=3,
            same=0,
            unscored_rivals=2,
            rivals_without_consent=7,
            my_priority=priority,
            my_total_score=score,
            my_consent=consent,
        ),
    )


def texts(strategy) -> str:
    return " ".join(s.text for s in strategy.steps)


# ── общий вывод ──────────────────────────────────────────────────────────────

def test_no_items_gives_no_strategy():
    assert _build_strategy([], fail_cond=1.0, p_excluded=0.0) is None


def test_items_without_probabilities_give_no_strategy():
    """Монте-Карло ещё не считался — советовать не на чем."""
    assert _build_strategy([item("X", None)], fail_cond=0.0, p_excluded=0.0) is None


def test_outlook_follows_the_overall_chance():
    for fail, expected in [
        (0.05, Outlook.SAFE),
        (0.35, Outlook.LIKELY),
        (0.70, Outlook.RISKY),
        (0.95, Outlook.LONGSHOT),
    ]:
        s = _build_strategy([item("X", 1 - fail)], fail_cond=fail, p_excluded=0.0)
        assert s.outlook is expected, f"fail={fail} → {s.outlook}"


def test_headline_names_the_program_only_when_one_clearly_leads():
    confident = _build_strategy([item("Химия", 0.9)], fail_cond=0.1, p_excluded=0.0)
    assert "Химия" in confident.headline

    spread = _build_strategy(
        [item("А", 0.25), item("Б", 0.25, priority=2), item("В", 0.25, priority=3)],
        fail_cond=0.25, p_excluded=0.0,
    )
    assert "«" not in spread.headline, (
        "при размазанных шансах называть одно направление нельзя: "
        f"{spread.headline}"
    )


def test_detail_reports_the_chance_of_getting_in_anywhere():
    s = _build_strategy([item("Химия", 0.6)], fail_cond=0.28, p_excluded=0.0)
    assert "72%" in s.detail


# ── согласие: единственное, что решается одним действием ─────────────────────

def test_missing_consent_is_the_very_first_thing_said():
    s = _build_strategy(
        [item("А", 0.5, consent=False), item("Б", 0.2, priority=2, consent=False)],
        fail_cond=0.3, p_excluded=0.2,
    )
    first = s.steps[0]
    assert first.kind is ReasonKind.BAD
    assert "Согласия нет" in first.text
    assert "20%" in first.text, "доля сценариев с уходом не показана"


def test_consent_anywhere_counts_as_submitted():
    """Согласие — признак абитуриента, а не отдельной заявки."""
    s = _build_strategy(
        [item("А", 0.5, consent=False), item("Б", 0.2, priority=2, consent=True)],
        fail_cond=0.3, p_excluded=0.0,
    )
    assert s.steps[0].kind is ReasonKind.GOOD
    assert "Согласие подано" in s.steps[0].text


# ── нули на прочих направлениях (тот самый вопрос) ───────────────────────────

def test_zeros_below_the_anchor_are_explained_as_good_news():
    s = _build_strategy(
        [item("Бизнес", 0.94), item("Финансы", 0.0, priority=2), item("Право", 0.0, priority=3)],
        fail_cond=0.06, p_excluded=0.0,
    )
    step = next(s for s in s.steps if "не дойдёт" in s.text)
    assert step.kind is ReasonKind.GOOD, "хорошая новость не должна выглядеть плохой"
    assert "Бизнес" in step.text


def test_zeros_are_not_explained_away_when_nothing_is_secured():
    """Если сильного направления нет, ноль — это честный ноль."""
    s = _build_strategy(
        [item("А", 0.10), item("Б", 0.0, priority=2)],
        fail_cond=0.90, p_excluded=0.0,
    )
    assert "не дойдёт" not in texts(s)


# ── приоритеты ───────────────────────────────────────────────────────────────

def test_advice_says_priorities_should_be_honest():
    """
    Модель распределяет отложенным согласием: человек оседает на самом
    приоритетном месте, куда проходит. Занижать желанное невыгодно.
    """
    s = _build_strategy(
        [item("А", 0.6), item("Б", 0.2, priority=2)], fail_cond=0.2, p_excluded=0.0
    )
    assert "по настоящему желанию" in texts(s)


def test_it_says_when_you_land_below_your_first_priority():
    s = _build_strategy(
        [item("Первое", 0.1), item("Второе", 0.7, priority=2)],
        fail_cond=0.2, p_excluded=0.0,
    )
    step = next(s for s in s.steps if "приоритет 2" in s.text)
    assert "Второе" in step.text and "Первое" in step.text


def test_single_program_gets_no_priority_advice():
    s = _build_strategy([item("Одна", 0.6)], fail_cond=0.4, p_excluded=0.0)
    assert "приоритет" not in texts(s).lower()


# ── экзамены и запас по баллам ───────────────────────────────────────────────

def test_upcoming_exam_is_flagged_as_the_only_remaining_lever():
    s = _build_strategy(
        [item("А", 0.2, exam=ExamStatus(
            state=ExamState.UPCOMING,
            upcoming_dates=[datetime(2026, 7, 18, 10, 0)],
        ))],
        fail_cond=0.8, p_excluded=0.0,
    )
    step = next(s for s in s.steps if "Экзамен ещё впереди" in s.text)
    assert "18.07" in step.text


def test_closest_program_is_shown_only_when_getting_in_is_in_doubt():
    """Тому, кто и так проходит, «не хватает баллов» читать незачем."""
    risky = _build_strategy(
        [item("А", 0.2, score=60, q90=70.0)], fail_cond=0.8, p_excluded=0.0
    )
    assert "Ближе всего" in texts(risky)

    safe = _build_strategy(
        [item("А", 0.95, score=60, q90=70.0)], fail_cond=0.05, p_excluded=0.0
    )
    assert "Ближе всего" not in texts(safe)


def test_score_gap_is_declined_correctly():
    """«не хватает 2 баллов» — так по-русски не говорят."""
    s = _build_strategy(
        [item("А", 0.2, score=68, q90=70.0)], fail_cond=0.8, p_excluded=0.0
    )
    assert "на 2 балла" in texts(s)


def test_surplus_is_not_called_reliable_when_the_chance_is_low():
    """
    Места и заявки вуз публикует порознь. На рассогласованных данных иначе
    вышла бы «самая надёжная опора» под направлением с шансом 9%.
    """
    s = _build_strategy(
        [item("А", 0.09, seats=30, applications=24)], fail_cond=0.91, p_excluded=0.0
    )
    assert "надёжная опора" not in texts(s)

    ok = _build_strategy(
        [item("А", 0.97, seats=30, applications=24)], fail_cond=0.03, p_excluded=0.0
    )
    assert "надёжная опора" in texts(ok)


# ── объём ────────────────────────────────────────────────────────────────────

def test_digest_stays_a_digest():
    """Выжимка на десять пунктов — уже не выжимка."""
    s = _build_strategy(
        [
            item("А", 0.55, consent=False, seats=30, applications=10),
            item("Б", 0.0, priority=2, score=50, q90=90.0, consent=False),
            item("В", 0.0, priority=3, consent=False, exam=ExamStatus(
                state=ExamState.UPCOMING, upcoming_dates=[datetime(2026, 7, 18, 10, 0)])),
        ],
        fail_cond=0.45, p_excluded=0.3,
    )
    assert len(s.steps) <= 5
