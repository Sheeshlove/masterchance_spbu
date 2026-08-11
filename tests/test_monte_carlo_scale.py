"""
Тесты шкалы вступительных испытаний в Монте-Карло.

У разных экзаменов СПбГУ разный потолок: у большинства 100 баллов, у 38.04.02 —
200. Раньше сотня была зашита глобально, из-за чего на 200-балльном экзамене
верхняя половина баллов считалась «неизвестной», а импутация физически не могла
выдать больше 100. Здесь это закреплено, чтобы не вернулось.

Заодно — сторож на баллы за индивидуальные достижения: они входят в конкурсный
балл, и это ровно то, в чём периодически возникают сомнения.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.admission_monte_carlo import MIN_EXAM_SCALE, AdmissionMonteCarlo


def build(rows: list[dict], *, seats: int = 5, n_sim: int = 12) -> AdmissionMonteCarlo:
    """
    Собрать модель из компактного описания заявок.

    В каждой строке: applicant, program, dept, vi, id (ИД), priority, consent.
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
    meta = pd.DataFrame([{
        "program_code": p,
        "department_code": next(r["dept"] for r in rows if r["program"] == p),
        "is_international": False,
    } for p in programs])
    stats = pd.DataFrame([{"program_code": p, "num_places": seats} for p in programs])

    return AdmissionMonteCarlo(apps, None, stats, meta, n_simulations=n_sim, random_seed=7)


def scale_of(mc: AdmissionMonteCarlo, exam_id: str) -> int:
    return int(mc.exam_scale[mc._exam2idx[exam_id]])


# ── определение шкалы ────────────────────────────────────────────────────────

def test_scale_follows_the_observed_maximum():
    """Экзамен, где встречаются баллы до 200, не может считаться 100-балльным."""
    rows = [{"applicant": f"A{i}", "program": "P200", "dept": "38.04.02",
             "vi": v} for i, v in enumerate((40, 120, 175, 200))]
    rows += [{"applicant": f"B{i}", "program": "P100", "dept": "01.04.02",
              "vi": v} for i, v in enumerate((30, 60, 85, 100))]

    mc = build(rows)
    assert scale_of(mc, "38.04.02") == 200
    assert scale_of(mc, "01.04.02") == 100


def test_scale_never_drops_below_hundred():
    """Пара низких баллов в начале приёма — не повод объявить шкалу крошечной."""
    rows = [{"applicant": f"A{i}", "program": "P", "dept": "01.04.02", "vi": v}
            for i, v in enumerate((12, 15))]
    assert scale_of(build(rows), "01.04.02") == MIN_EXAM_SCALE


def test_exam_without_any_scores_keeps_the_default_scale():
    rows = [{"applicant": f"A{i}", "program": "P", "dept": "01.04.02", "vi": 0}
            for i in range(3)]
    assert scale_of(build(rows), "01.04.02") == MIN_EXAM_SCALE


# ── высокие баллы больше не «неизвестны» ─────────────────────────────────────

def test_high_scores_on_a_wide_scale_count_as_known():
    """
    150 из 200 — обычный балл, а не пропуск. Раньше всё от 100 и выше выпадало
    из статистики, и такой абитуриент считался человеком без результатов.
    """
    rows = [{"applicant": "A1", "program": "P200", "dept": "38.04.02", "vi": 150}]
    rows += [{"applicant": f"B{i}", "program": "P200", "dept": "38.04.02", "vi": v}
             for i, v in enumerate((60, 110, 190))]

    mc = build(rows)
    assert mc.personal_mu[mc._applicant2idx["A1"]] > 0


def test_personal_mu_is_a_fraction_of_the_scale():
    rows = [{"applicant": "A1", "program": "P200", "dept": "38.04.02", "vi": 150}]
    rows += [{"applicant": f"B{i}", "program": "P200", "dept": "38.04.02", "vi": v}
             for i, v in enumerate((60, 110, 200))]

    mc = build(rows)
    assert scale_of(mc, "38.04.02") == 200
    assert mc.personal_mu[mc._applicant2idx["A1"]] == pytest.approx(150 / 200, abs=1e-6)


def test_mixed_scales_average_as_fractions_not_raw_points():
    """
    90 из 100 и 180 из 200 — это один и тот же уровень. Среднее сырых баллов
    дало бы бессмысленные 135; среднее долей даёт 0,9.
    """
    rows = [
        {"applicant": "A1", "program": "P100", "dept": "01.04.02", "vi": 90},
        {"applicant": "A1", "program": "P200", "dept": "38.04.02", "vi": 180},
    ]
    rows += [{"applicant": f"B{i}", "program": "P100", "dept": "01.04.02", "vi": v}
             for i, v in enumerate((40, 70, 99))]
    rows += [{"applicant": f"C{i}", "program": "P200", "dept": "38.04.02", "vi": v}
             for i, v in enumerate((80, 140, 200))]

    mc = build(rows)
    assert (scale_of(mc, "01.04.02"), scale_of(mc, "38.04.02")) == (100, 200)
    assert mc.personal_mu[mc._applicant2idx["A1"]] == pytest.approx(0.9, abs=1e-6)


# ── импутация ────────────────────────────────────────────────────────────────

def imputed_scores(mc: AdmissionMonteCarlo, applicant: str, runs: int) -> list[int]:
    """
    Баллы, которые модель разыграла абитуриенту за `runs` прогонов.

    Перехватываем ровно тот массив, что уходит в симуляцию конкурса, — так
    видно итоговый балл, а не наши догадки о нём. ИД вычитаем: нас интересует
    разыгранный балл ВИ.
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


def test_imputed_score_can_exceed_a_hundred_on_a_wide_scale():
    """
    Суть починки: человеку без результата на 200-балльном экзамене модель
    обязана уметь разыграть балл выше 100. Раньше потолок был жёстко 100,
    и такой абитуриент навсегда оставался вдвое слабее реальных соперников.
    """
    rows = [{"applicant": f"S{i}", "program": "P200", "dept": "38.04.02", "vi": v}
            for i, v in enumerate((150, 160, 170, 180, 190, 195, 198, 200))]
    rows.append({"applicant": "NEW", "program": "P200", "dept": "38.04.02", "vi": 0})

    mc = build(rows, n_sim=1)
    assert scale_of(mc, "38.04.02") == 200

    scores = imputed_scores(mc, "NEW", runs=40)
    assert max(scores) > 100, f"балл так и не превысил 100: {sorted(scores)[-5:]}"
    assert max(scores) <= 200, "балл вышел за пределы шкалы"


def test_imputed_scores_stay_inside_their_own_scale():
    """На 100-балльном экзамене разыгранный балл не должен вылезать за 100."""
    rows = [{"applicant": f"S{i}", "program": "P100", "dept": "01.04.02", "vi": v}
            for i, v in enumerate((40, 55, 70, 85, 95))]
    rows.append({"applicant": "NEW", "program": "P100", "dept": "01.04.02", "vi": 0})

    mc = build(rows, n_sim=1)
    scores = imputed_scores(mc, "NEW", runs=40)

    assert scores, "импутация не сработала"
    assert max(scores) <= 100, f"балл вышел за 100-балльную шкалу: {max(scores)}"
    assert min(scores) >= 1


# ── индивидуальные достижения ────────────────────────────────────────────────

def test_id_achievements_are_added_to_the_competitive_score():
    """
    ИД входят в конкурсный балл. Проверяется напрямую: без ИД человек слабее
    соперника, с ИД — сильнее, и это меняет исход конкурса на единственное место.
    """
    weak_no_id = [
        {"applicant": "ME", "program": "P", "dept": "01.04.02", "vi": 80, "id": 0},
        {"applicant": "RIVAL", "program": "P", "dept": "01.04.02", "vi": 90, "id": 0},
    ]
    weak_with_id = [
        {"applicant": "ME", "program": "P", "dept": "01.04.02", "vi": 80, "id": 30},
        {"applicant": "RIVAL", "program": "P", "dept": "01.04.02", "vi": 90, "id": 0},
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
        {"applicant": "ME", "program": "P", "dept": "01.04.02", "vi": 50, "id": 60},
        {"applicant": "RIVAL", "program": "P", "dept": "01.04.02", "vi": 100, "id": 0},
    ]
    mc = build(rows, seats=1, n_sim=20)
    mc.run_simulation()
    assert mc.p_admit["ME"]["P"] == 1.0, "50+60=110 должно обойти 100"
