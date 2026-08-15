"""
Монте-Карло считается отдельно по каждому вузу.

Это условие правильности, а не оптимизация. Модель раздаёт места алгоритмом
отложенного согласия, где один абитуриент занимает ровно одно место: внутри
вуза так и есть, между вузами — нет. Человек может пройти и в СПбГУ, и в ВШЭ,
и приоритеты у него в каждом вузе свои. Общий прогон вдобавок считал бы
перцентили баллов по смеси разных шкал — и «уводил» бы в другие вузы не тех.
"""
from __future__ import annotations

import pytest

pytest.importorskip("numpy", reason="модель считает на numpy")
pytest.importorskip("pandas", reason="модель принимает DataFrame")
pytest.importorskip("numba", reason="симуляция конкурса компилируется numba")

from app.application.use_cases.recalculate_monte_carlo import (  # noqa: E402
    RecalculateMonteCarloUseCase,
)


@pytest.fixture
def two_universities(seed):
    """По одной программе в двух вузах; в каждой — свои абитуриенты."""
    for uni, program, dept in (("spbgu", "spbgu:38.04.02:aaa", "spbgu:38.04.02"),
                               ("hse", "hse:38.04.02:bbb", "hse:38.04.02")):
        seed.program(program, name="Менеджмент", department_code=dept, university=uni)
        seed.stats(program, num_places=1, num_applications=2)
        for n, score in ((1, 90), (2, 70)):
            seed.applicant(f"{uni}:{n}", university=uni)
            seed.application(program, f"{uni}:{n}", priority=1,
                             total_score=score, vi_score=score, consent=True)
    seed.commit()


def test_both_universities_get_their_own_probabilities(repo, two_universities):
    RecalculateMonteCarloUseCase(repo=repo, n_simulations=20).execute()

    for uni in ("spbgu", "hse"):
        strong = repo.get_probabilities_for_applicant(f"{uni}:1")
        weak = repo.get_probabilities_for_applicant(f"{uni}:2")
        assert strong and strong[0].probability == pytest.approx(1.0), \
            f"{uni}: сильнейший при одном месте проходит всегда"
        assert weak and weak[0].probability == pytest.approx(0.0)


def test_admission_in_one_university_does_not_block_the_other(repo, seed):
    """
    Один и тот же человек подался в два вуза — код у него единый. Пройти он
    может в оба: конкурсы независимы, и модель не должна отдавать ему место
    только где-то одном.
    """
    seed.applicant("1645144")
    for uni, program in (("spbgu", "spbgu:38.04.02:aaa"), ("hse", "hse:38.04.02:bbb")):
        seed.program(program, name="Менеджмент", department_code=f"{uni}:38.04.02",
                     university=uni)
        seed.stats(program, num_places=1, num_applications=1)
        seed.application(program, "1645144", priority=1,
                         total_score=90, vi_score=90, consent=True)
    seed.commit()

    RecalculateMonteCarloUseCase(repo=repo, n_simulations=20).execute()

    probs = {p.program_code: p.probability for p in repo.get_probabilities_for_applicant("1645144")}
    assert probs == {"spbgu:38.04.02:aaa": pytest.approx(1.0),
                     "hse:38.04.02:bbb": pytest.approx(1.0)}


def test_diagnostics_of_one_person_are_kept_per_university(repo, seed):
    """
    «Пролетел с магой» считается внутри вуза. Код единый, поэтому без вуза в
    ключе прогон второго вуза затирал бы диагностику первого — и на вкладках
    стояло бы одно и то же число.
    """
    seed.applicant("1645144")
    # в СПбГУ место есть и он его займёт, в ВШЭ — мест нет и он пролетает
    seed.program("spbgu:p1", name="Матмод", department_code="spbgu:01.04.02",
                 university="spbgu")
    seed.stats("spbgu:p1", num_places=1)
    seed.application("spbgu:p1", "1645144", priority=1, total_score=90, vi_score=90,
                     consent=True)

    seed.program("hse:p1", name="Экономика", department_code="hse:38.04.01", university="hse")
    seed.stats("hse:p1", num_places=1)
    seed.application("hse:p1", "1645144", priority=1, total_score=10, vi_score=10, consent=True)
    seed.applicant("999")
    seed.application("hse:p1", "999", priority=1, total_score=95, vi_score=95, consent=True)
    seed.commit()

    RecalculateMonteCarloUseCase(repo=repo, n_simulations=20).execute()

    spbgu = repo.get_diagnostics_for_applicant("1645144", "spbgu")
    hse = repo.get_diagnostics_for_applicant("1645144", "hse")
    assert spbgu.p_fail_when_included == pytest.approx(0.0), "в СПбГУ место занято"
    assert hse.p_fail_when_included == pytest.approx(1.0), "в ВШЭ его обошли"


def test_quantiles_are_computed_for_every_university(repo, two_universities):
    RecalculateMonteCarloUseCase(repo=repo, n_simulations=20).execute()

    quantiles = repo.get_quantiles_for_programs(["spbgu:38.04.02:aaa", "hse:38.04.02:bbb"])
    assert set(quantiles) == {"spbgu:38.04.02:aaa", "hse:38.04.02:bbb"}


def test_a_university_without_seats_is_skipped_not_fatal(repo, seed, two_universities):
    """Вуз не опубликовал число мест — конкурса нет, но остальные считаются."""
    seed.program("msu:38.04.02:ccc", name="Менеджмент", department_code="msu:38.04.02",
                 university="msu")
    seed.applicant("msu:1", university="msu")
    seed.application("msu:38.04.02:ccc", "msu:1", priority=1, total_score=80, vi_score=80)
    seed.commit()

    RecalculateMonteCarloUseCase(repo=repo, n_simulations=20).execute()

    assert repo.get_probabilities_for_applicant("msu:1") == []
    assert repo.get_probabilities_for_applicant("spbgu:1")


def test_empty_result_keeps_the_previous_forecast(repo, seed):
    """
    Считать стало нечего — прежние вероятности не стираем: устаревший расклад
    полезнее, чем «шанс —» по всем направлениям.
    """
    seed.program("spbgu:p1", name="Матмод", department_code="spbgu:01.04.02")
    seed.applicant("spbgu:1")
    seed.probability("spbgu:1", "spbgu:p1", 0.42)
    seed.commit()

    with pytest.raises(RuntimeError, match="ни по одному вузу"):
        RecalculateMonteCarloUseCase(repo=repo, n_simulations=10).execute()

    assert repo.get_probabilities_for_applicant("spbgu:1")[0].probability == pytest.approx(0.42)


# ── согласие в другом вузе ──────────────────────────────────────────────────
def test_consent_in_another_university_frees_the_seat(repo, seed):
    """
    Согласие можно держать только одно. Конкурент, отдавший его СПбГУ, из
    конкурса ВШЭ почти наверняка уйдёт — и место достанется следующему.

    Раньше такой уход модель могла только предполагать по баллу; код
    поступающего единый, поэтому теперь он виден в списках.
    """
    # одно место в ВШЭ, двое претендентов; сильнейший уже выбрал СПбГУ
    seed.program("hse:p1", name="Экономика", department_code="hse:38.04.01", university="hse")
    seed.stats("hse:p1", num_places=1)
    seed.program("spbgu:p1", name="Матмод", department_code="spbgu:01.04.02", university="spbgu")
    seed.stats("spbgu:p1", num_places=1)

    seed.applicant("сильный")
    seed.application("hse:p1", "сильный", priority=1, total_score=95, vi_score=95, consent=False)
    seed.application("spbgu:p1", "сильный", priority=1, total_score=95, vi_score=95, consent=True)

    seed.applicant("слабый")
    seed.application("hse:p1", "слабый", priority=1, total_score=60, vi_score=60, consent=True)
    seed.commit()

    RecalculateMonteCarloUseCase(repo=repo, n_simulations=400).execute()

    weak = {p.program_code: p.probability
            for p in repo.get_probabilities_for_applicant("слабый")}
    # уходит с вероятностью MC_CONSENT_ELSEWHERE_LEAVE (по умолчанию 0.9),
    # поэтому место достаётся слабому примерно в девяти случаях из десяти
    assert weak["hse:p1"] > 0.75, "место ушедшего должно доставаться следующему"
    assert weak["hse:p1"] < 1.0, "согласие отзывают — уход не гарантирован"


def test_consent_here_outweighs_consent_elsewhere(repo, seed):
    """
    Если согласие подано и здесь тоже (человек его переносил), то он выбрал
    этот вуз — уводить его нельзя.
    """
    seed.program("hse:p1", name="Экономика", department_code="hse:38.04.01", university="hse")
    seed.stats("hse:p1", num_places=1)
    seed.program("spbgu:p1", name="Матмод", department_code="spbgu:01.04.02", university="spbgu")
    seed.stats("spbgu:p1", num_places=1)

    seed.applicant("двойное согласие")
    seed.application("hse:p1", "двойное согласие", priority=1, total_score=95, vi_score=95,
                     consent=True)
    seed.application("spbgu:p1", "двойное согласие", priority=1, total_score=95, vi_score=95,
                     consent=True)

    seed.applicant("слабый")
    seed.application("hse:p1", "слабый", priority=1, total_score=60, vi_score=60, consent=True)
    seed.commit()

    RecalculateMonteCarloUseCase(repo=repo, n_simulations=200).execute()

    weak = {p.program_code: p.probability
            for p in repo.get_probabilities_for_applicant("слабый")}
    assert weak["hse:p1"] == pytest.approx(0.0), "он никуда не уходит, место занято"


def test_signal_works_when_consent_exists_in_one_university_only(repo, seed):
    """
    Регрессия. Согласия часто собраны только в одном вузе — остальные их ещё
    не открыли. Для этих остальных сигнал как раз и важен, а условие «а есть ли
    вообще несколько вузов с согласиями» тихо его отключало: числа получались
    ровно как без сигнала, и заметить это можно было только замером.
    """
    seed.program("hse:p1", name="Экономика", department_code="hse:38.04.01", university="hse")
    seed.stats("hse:p1", num_places=1)
    seed.program("spbgu:p1", name="Матмод", department_code="spbgu:01.04.02", university="spbgu")
    seed.stats("spbgu:p1", num_places=1)

    # в ВШЭ согласия не подал никто — все отметки только в СПбГУ
    seed.applicant("сильный")
    seed.application("hse:p1", "сильный", priority=1, total_score=95, vi_score=95, consent=False)
    seed.application("spbgu:p1", "сильный", priority=1, total_score=95, vi_score=95, consent=True)

    seed.applicant("слабый")
    seed.application("hse:p1", "слабый", priority=1, total_score=60, vi_score=60, consent=False)
    seed.commit()

    RecalculateMonteCarloUseCase(repo=repo, n_simulations=400).execute()

    weak = {p.program_code: p.probability
            for p in repo.get_probabilities_for_applicant("слабый")}
    # без сигнала сильный уходил бы с вероятностью MC_OPTOUT_RATIO (0.2),
    # и шанс слабого был бы около 0.2 вместо 0.9
    assert weak["hse:p1"] > 0.6, "согласие в единственном вузе с согласиями тоже сигнал"
