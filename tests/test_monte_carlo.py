"""
Тесты модели Монте-Карло.

Главное правило: списки окончательные, поэтому балл берётся из них как есть.
Ноль — это ноль, а не «результат ещё не пришёл»: раньше модель дорисовывала
такому человеку балл по статистике экзамена, и он в части сценариев обходил
тех, у кого балл уже выставлен. Здесь это закреплено, чтобы не вернулось.

Заодно — сторож на баллы за индивидуальные достижения: они входят в конкурсный
балл, и это ровно то, в чём периодически возникают сомнения.
"""
from __future__ import annotations

import pytest

# Счётный стек стоит только на сервере (requirements.txt); тесты гоняются на
# лёгком наборе, поэтому без него эти проверки пропускаются, а не падают.
np = pytest.importorskip("numpy", reason="модель считает на numpy")
pd = pytest.importorskip("pandas", reason="модель принимает DataFrame")
pytest.importorskip("numba", reason="симуляция конкурса компилируется numba")

from app.services.admission_monte_carlo import AdmissionMonteCarlo  # noqa: E402


def build(rows: list[dict], *, seats: int = 5, n_sim: int = 12) -> AdmissionMonteCarlo:
    """
    Собрать модель из компактного описания заявок.

    В каждой строке: applicant, program, vi, id (ИД), priority, consent.
    """
    apps = pd.DataFrame([{
        "applicant_id": r["applicant"],
        "program_code": r["program"],
        "total_score": r.get("vi", 0) + r.get("id", 0),
        "vi_score": r.get("vi", 0),
        "subject1_score": 0,
        "subject2_score": 0,
        "id_achievements": r.get("id", 0),
        "target_id_achievements": 0,
        "priority": r.get("priority", 1),
        "consent": r.get("consent", True),
        "review_status": "Участвует в конкурсе",
    } for r in rows])

    programs = sorted({r["program"] for r in rows})
    stats = pd.DataFrame([{"program_code": p, "num_places": seats} for p in programs])

    return AdmissionMonteCarlo(apps, None, stats, n_simulations=n_sim, random_seed=7)


def scores_seen(mc: AdmissionMonteCarlo, applicant: str, runs: int) -> list[int]:
    """
    Баллы, с которыми абитуриент уходил в конкурс за `runs` прогонов.

    Перехватываем ровно тот массив, что уходит в симуляцию, — так видно
    итоговый балл, а не наши догадки о нём. ИД вычитаем: нас интересует ВИ.
    """
    import app.services.admission_monte_carlo as mod

    row = int(np.where(mc.applicant_idx == mc._applicant2idx[applicant])[0][0])
    seen: list[int] = []
    real = mod._simulate_admission_numba

    def spy(priority, program_idx, applicant_idx, total_score, *a, **kw):
        seen.append(int(total_score[row]) - int(mc.id_ach[row]))
        return real(priority, program_idx, applicant_idx, total_score, *a, **kw)

    mod._simulate_admission_numba = spy
    try:
        for _ in range(runs):
            mc._single_simulation()
    finally:
        mod._simulate_admission_numba = real
    return seen


# ── ноль остаётся нулём ──────────────────────────────────────────────────────

def test_missing_score_stays_zero_in_every_run():
    """
    Балла в окончательных списках нет — значит ноль, и он не должен «оживать»
    ни в одном сценарии. Раньше здесь работала импутация по статистике экзамена.
    """
    rows = [{"applicant": f"S{i}", "program": "P", "vi": v}
            for i, v in enumerate((40, 55, 70, 85, 95))]
    rows.append({"applicant": "NEW", "program": "P", "vi": 0})

    mc = build(rows, n_sim=1)
    assert set(scores_seen(mc, "NEW", runs=40)) == {0}


def test_zero_never_overtakes_a_scored_rival():
    """Суть правки: человек с нулём не может занять место у человека с баллом."""
    rows = [
        {"applicant": "ZERO", "program": "P", "vi": 0},
        {"applicant": "SCORED", "program": "P", "vi": 40},
    ]
    mc = build(rows, seats=1, n_sim=50)
    mc.run_simulation()

    assert mc.p_admit["SCORED"]["P"] == 1.0
    assert mc.p_admit["ZERO"]["P"] == 0.0


def test_zero_still_takes_a_seat_that_nobody_else_wants():
    """
    Ноль ставит человека в конец очереди, а не выкидывает из конкурса:
    если мест больше, чем людей с баллами, место достаётся и ему.
    """
    rows = [
        {"applicant": "ZERO", "program": "P", "vi": 0},
        {"applicant": "SCORED", "program": "P", "vi": 40},
    ]
    mc = build(rows, seats=2, n_sim=20)
    mc.run_simulation()

    assert mc.p_admit["ZERO"]["P"] == 1.0


def test_zeros_do_not_drag_the_passing_score_down_while_seats_are_taken():
    """
    Проходной — балл слабейшего из зачисленных. Пока мест меньше, чем людей
    с баллами, нули на него не влияют: они просто не проходят.
    """
    rows = [{"applicant": f"S{i}", "program": "P", "vi": v}
            for i, v in enumerate((60, 70, 80))]
    rows += [{"applicant": f"Z{i}", "program": "P", "vi": 0} for i in range(5)]

    mc = build(rows, seats=2, n_sim=30)
    mc.run_simulation()

    assert mc.pass_score_quantiles["P"]["q90"] == 70


def test_scores_do_not_change_between_runs():
    """Балл фиксирован: между прогонами меняются только жребий и отток."""
    rows = [{"applicant": f"S{i}", "program": "P", "vi": v}
            for i, v in enumerate((0, 55, 200))]

    mc = build(rows, n_sim=1)
    first = mc.total_score.copy()
    for _ in range(5):
        mc._single_simulation()

    assert np.array_equal(mc.total_score, first)


def test_wide_scale_scores_are_kept_as_they_are():
    """
    У 38.04.02 шкала 200-балльная. Модель больше не нормирует баллы на шкалу
    экзамена — она их и не трогает, поэтому 180 остаётся 180.
    """
    rows = [{"applicant": "A1", "program": "P200", "vi": 180},
            {"applicant": "A2", "program": "P200", "vi": 95}]

    mc = build(rows)
    row = int(np.where(mc.applicant_idx == mc._applicant2idx["A1"])[0][0])
    assert int(mc.total_score[row]) == 180


# ── индивидуальные достижения ────────────────────────────────────────────────

def test_id_achievements_are_added_to_the_competitive_score():
    """
    ИД входят в конкурсный балл. Проверяется напрямую: без ИД человек слабее
    соперника, с ИД — сильнее, и это меняет исход конкурса на единственное место.
    """
    weak_no_id = [
        {"applicant": "ME", "program": "P", "vi": 80, "id": 0},
        {"applicant": "RIVAL", "program": "P", "vi": 90, "id": 0},
    ]
    weak_with_id = [
        {"applicant": "ME", "program": "P", "vi": 80, "id": 30},
        {"applicant": "RIVAL", "program": "P", "vi": 90, "id": 0},
    ]

    without = build(weak_no_id, seats=1, n_sim=40)
    without.run_simulation()
    with_id = build(weak_with_id, seats=1, n_sim=40)
    with_id.run_simulation()

    assert without.p_admit["ME"]["P"] == 0.0, "без ИД слабейший не должен проходить"
    assert with_id.p_admit["ME"]["P"] == 1.0, "30 баллов ИД обязаны перевесить 10 баллов ВИ"


def test_id_achievements_beyond_ten_are_not_clipped():
    """Реальные ИД доходят до 60 — старая константа обрезала бы их до 10."""
    rows = [
        {"applicant": "ME", "program": "P", "vi": 50, "id": 60},
        {"applicant": "RIVAL", "program": "P", "vi": 100, "id": 0},
    ]
    mc = build(rows, seats=1, n_sim=20)
    mc.run_simulation()
    assert mc.p_admit["ME"]["P"] == 1.0, "50+60=110 должно обойти 100"


def test_id_achievements_alone_can_beat_a_zero():
    """У человека без ВИ, но с ИД, конкурсный балл всё-таки не нулевой."""
    rows = [
        {"applicant": "ID_ONLY", "program": "P", "vi": 0, "id": 10},
        {"applicant": "ZERO", "program": "P", "vi": 0, "id": 0},
    ]
    mc = build(rows, seats=1, n_sim=20)
    mc.run_simulation()

    assert mc.p_admit["ID_ONLY"]["P"] == 1.0
    assert mc.p_admit["ZERO"]["P"] == 0.0
