"""
Тесты объяснения «почему такой шанс».

Само число шанса приходит из Монте-Карло и здесь не проверяется — проверяется,
что расклад конкурса собран по тем же данным, что скармливаются модели, и что
объяснение не врёт: место в очереди считается по реальным баллам соперников,
конкурентом «без согласия» назван ровно тот, кого MC кладёт в пул оттока,
а знак влияния (+/−) соответствует тому, помогает фактор или мешает.
"""
from __future__ import annotations

import pytest

from app.application.use_cases.get_applicant_forecast import (
    GetApplicantForecastUseCase,
    ReasonKind,
    _plural,
)


@pytest.fixture
def forecast(repo):
    return GetApplicantForecastUseCase(repo)


def texts(item) -> str:
    """Все объяснения направления одной строкой — удобно для поиска подстроки."""
    return "\n".join(r.text for r in item.reasons)


def kinds(item) -> list[ReasonKind]:
    return [r.kind for r in item.reasons]


# ─────────────────────────── расклад конкурса ────────────────────────────────

def test_competition_counts_seats_and_applications(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200)
    for i in range(4):
        seed.applicant(f"R{i}")
        seed.application("701", f"R{i}", total_score=150)
    seed.probability("A1", "701", 0.5)
    seed.stats("701", num_places=2)
    seed.commit()

    comp = forecast.execute("A1").items[0].competition
    assert comp.seats == 2
    assert comp.applications == 5          # сам абитуриент тоже в конкурсе
    assert comp.scored_rivals == 4
    assert comp.unscored_rivals == 0


def test_rank_counts_only_rivals_with_higher_score(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=180)
    for score in (250, 200, 180, 100, 0):
        seed.applicant(f"R{score}")
        seed.application("701", f"R{score}", total_score=score)
    seed.probability("A1", "701", 0.5)
    seed.stats("701", num_places=3)
    seed.commit()

    comp = forecast.execute("A1").items[0].competition
    assert comp.better == 2          # 250 и 200
    assert comp.same == 1            # ещё один со 180
    assert comp.unscored_rivals == 1  # тот, у кого балла нет
    assert comp.my_total_score == 180


def test_applicant_without_score_has_no_rank(seed, forecast):
    """Балла нет — «вы N-й» показывать нельзя, это было бы выдумкой."""
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1")           # баллов нет
    seed.applicant("R1")
    seed.application("701", "R1", total_score=200)
    seed.probability("A1", "701", 0.5)
    seed.stats("701")
    seed.commit()

    item = forecast.execute("A1").items[0]
    assert item.competition.my_total_score is None
    assert item.competition.better == 0
    assert "разыгрывает его" in texts(item)
    assert "-й из" not in texts(item)


def test_rivals_without_consent_matches_monte_carlo_pool(seed, forecast):
    """
    В пул оттока MC попадает тот, у кого нет согласия НИ ПО ОДНОЙ заявке.
    Соперник без согласия здесь, но с согласием на другом направлении —
    не конкурент «на уход», и считать его таким нельзя.
    """
    seed.program("701")
    seed.program("702")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200)

    seed.applicant("R_leaves")                                   # нигде нет согласия
    seed.application("701", "R_leaves", total_score=210, consent=False)

    seed.applicant("R_stays")                                    # согласие на 702
    seed.application("701", "R_stays", total_score=205, consent=False)
    seed.application("702", "R_stays", total_score=205, consent=True)

    seed.probability("A1", "701", 0.5)
    seed.stats("701", num_places=1)
    seed.commit()

    comp = forecast.execute("A1").items[0].competition
    assert comp.rivals_without_consent == 1


def test_own_consent_is_reported(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200, consent=True)
    seed.probability("A1", "701", 0.5)
    seed.stats("701")
    seed.commit()

    comp = forecast.execute("A1").items[0].competition
    assert comp.my_consent is True


# ──────────────────────────── тексты объяснений ──────────────────────────────

def test_inside_seats_is_a_good_reason(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=300, priority=1)
    for i in range(9):
        seed.applicant(f"R{i}")
        seed.application("701", f"R{i}", total_score=100)
    seed.probability("A1", "701", 0.9)
    seed.stats("701", num_places=3)
    seed.commit()

    item = forecast.execute("A1").items[0]
    assert "1-й из 10" in texts(item)
    assert "внутри мест" in texts(item)
    assert ReasonKind.GOOD in kinds(item)


def test_below_the_cutoff_is_a_bad_reason(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=100)
    for i in range(9):
        seed.applicant(f"R{i}")
        seed.application("701", f"R{i}", total_score=300)
    seed.probability("A1", "701", 0.02)
    seed.stats("701", num_places=2)
    seed.commit()

    item = forecast.execute("A1").items[0]
    assert "10-й из 10" in texts(item)
    assert "чтобы кто-то сверху ушёл" in texts(item)
    assert ReasonKind.BAD in kinds(item)


def test_score_above_pessimistic_quantile_reads_as_margin(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=250)
    seed.probability("A1", "701", 0.9)
    seed.quantiles("701", q90=200.0, q95=220.0)
    seed.stats("701", num_places=5)
    seed.commit()

    assert "запас 30" in texts(forecast.execute("A1").items[0])


def test_score_inside_quantile_band_reads_as_uncertain(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=210)
    seed.probability("A1", "701", 0.5)
    seed.quantiles("701", q90=200.0, q95=220.0)
    seed.stats("701", num_places=5)
    seed.commit()

    assert "попал в вилку прогноза проходного" in texts(forecast.execute("A1").items[0])


def test_score_below_quantiles_reports_the_gap(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=180)
    seed.probability("A1", "701", 0.1)
    seed.quantiles("701", q90=200.0, q95=220.0)
    seed.stats("701", num_places=5)
    seed.commit()

    assert "не хватает примерно 20" in texts(forecast.execute("A1").items[0])


def test_first_priority_is_explained_as_advantage(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", priority=1, total_score=200)
    seed.probability("A1", "701", 0.5)
    seed.stats("701")
    seed.commit()

    assert "Приоритет 1" in texts(forecast.execute("A1").items[0])


def test_low_priority_explains_the_queue(seed, forecast):
    seed.program("700")
    seed.program("701")
    seed.applicant("A1")
    seed.application("700", "A1", priority=1, total_score=200)
    seed.application("701", "A1", priority=3, total_score=200)
    seed.probability("A1", "701", 0.5)
    seed.stats("700")
    seed.stats("701")
    seed.commit()

    by_code = {i.program_code: i for i in forecast.execute("A1").items}
    assert "не прошли по 2 более приоритетным заявкам" in texts(by_code["701"])


def test_leaving_rivals_are_explained_as_falling_cutoff(seed, forecast):
    """
    Ответ на вопрос «понижается ли проходной, если кто-то уходит» должен быть
    виден прямо в объяснении, а не только в справке.
    """
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200, consent=True)
    for i in range(3):
        seed.applicant(f"R{i}")
        seed.application("701", f"R{i}", total_score=250, consent=False)
    seed.probability("A1", "701", 0.3)
    seed.stats("701", num_places=1)
    seed.commit()

    text = texts(forecast.execute("A1").items[0])
    assert "3 из 4 конкурентов" in text
    assert "проходной в таком сценарии опускается" in text


def test_own_optout_is_disclosed_when_consent_missing(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200, consent=False)
    seed.probability("A1", "701", 0.45)
    seed.diagnostics("A1", p_excluded=0.20, p_fail_when_included=0.4)
    seed.stats("701")
    seed.commit()

    result = forecast.execute("A1")
    assert result.p_excluded == pytest.approx(0.20)
    assert "в 20% сценариев" in texts(result.items[0])
    assert any("уводит вас в другой вуз" in n.text for n in result.notes)


def test_notes_always_explain_what_the_number_is(seed, forecast):
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200, consent=True)
    seed.probability("A1", "701", 0.5)
    seed.stats("701")
    seed.commit()

    notes = " ".join(n.text for n in forecast.execute("A1").notes)
    assert "10 000" in notes
    assert "вилкой" in notes


def test_missing_seats_are_admitted_not_invented(seed, forecast):
    """Вуз не опубликовал число мест — врать про «человек на место» нельзя."""
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200)
    seed.probability("A1", "701", 0.5)
    seed.commit()   # без seed.stats → мест нет

    item = forecast.execute("A1").items[0]
    assert item.competition.seats is None
    assert "не опубликовал" in texts(item)
    assert "на место" not in texts(item)


def test_reasons_are_isolated_per_program(seed, forecast):
    """Расклад одного направления не должен протекать в другое."""
    seed.program("701", name="Тесная")
    seed.program("702", name="Просторная")
    seed.applicant("A1")
    seed.application("701", "A1", priority=1, total_score=100)
    seed.application("702", "A1", priority=2, total_score=100)
    for i in range(20):
        seed.applicant(f"R{i}")
        seed.application("701", f"R{i}", total_score=300)
    seed.probability("A1", "701", 0.01)
    seed.probability("A1", "702", 0.90)
    seed.stats("701", num_places=1)
    seed.stats("702", num_places=50)
    seed.commit()

    by_code = {i.program_code: i for i in forecast.execute("A1").items}
    assert by_code["701"].competition.applications == 21
    assert by_code["702"].competition.applications == 1
    assert "21-й из 21" in texts(by_code["701"])


def test_plural_forms():
    assert _plural(1, "человек", "человека", "человек") == "человек"
    assert _plural(2, "человек", "человека", "человек") == "человека"
    assert _plural(5, "человек", "человека", "человек") == "человек"
    assert _plural(11, "человек", "человека", "человек") == "человек"
    assert _plural(21, "человек", "человека", "человек") == "человек"
    assert _plural(22, "человек", "человека", "человек") == "человека"


def test_every_reason_kind_has_a_renderer_icon():
    """Новый вид объяснения не должен ронять интерфейсы по KeyError."""
    from app.presentation.web.view import REASON_ICONS

    assert set(REASON_ICONS) == set(ReasonKind)

    aiogram = pytest.importorskip("aiogram", reason="бот использует aiogram")
    from app.presentation.bot import _REASON_ICONS

    assert set(_REASON_ICONS) == set(ReasonKind)


def test_fractional_competition_uses_genitive_singular(seed, forecast):
    """«3,4 человека на место», а не «3,4 человек»."""
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200)
    for i in range(16):
        seed.applicant(f"R{i}")
        seed.application("701", f"R{i}", total_score=150)
    seed.probability("A1", "701", 0.5)
    seed.stats("701", num_places=5)      # 17 заявок / 5 мест = 3,4
    seed.commit()

    assert "3,4 человека на место" in texts(forecast.execute("A1").items[0])


def test_more_seats_than_applications_is_reported_plainly(seed, forecast):
    """«0,4 человека на место» — бессмыслица; надо сказать словами."""
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200)
    seed.probability("A1", "701", 0.99)
    seed.stats("701", num_places=25)
    seed.commit()

    item = forecast.execute("A1").items[0]
    assert "желающих меньше, чем мест" in texts(item)
    assert "на место" not in texts(item)
    assert ReasonKind.GOOD in kinds(item)


def test_simulation_count_note_keeps_its_comma(seed, forecast):
    """Разделитель разрядов не должен съедать запятую предложения."""
    seed.program("701")
    seed.applicant("A1")
    seed.application("701", "A1", total_score=200)
    seed.probability("A1", "701", 0.5)
    seed.stats("701")
    seed.commit()

    note = forecast.execute("A1").notes[0].text
    assert "10 000 смоделированных приёмных кампаний, в которых" in note
