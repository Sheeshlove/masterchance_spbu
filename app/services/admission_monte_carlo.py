# app/services/admission_monte_carlo.py
from __future__ import annotations

from typing import Dict, Tuple, List

import numpy as np
import pandas as pd
from numba import njit

from app.config.config import settings
from app.config.logger import logger

SCORE_COL = "vi_score"

RANK_SCALE = 100


@njit(cache=True)
def _simulate_admission_numba(priority, program_idx, applicant_idx,
                              total_score, seats_init, jitter,
                              active, max_priority) -> Tuple[np.ndarray, np.ndarray]:
    """
    Симуляция распределения мест алгоритмом отложенного согласия
    (student-proposing Gale–Shapley):
      • список предпочтений каждого абитуриента — его заявки по возрастанию приоритета;
      • программа держит лучших по баллу в пределах мест, выбитые «протекают» вниз
        по своим менее приоритетным заявкам;
      • 'jitter' ломает тай-брейки (равные баллы → жребий);
      • 'active' (Uint8 по абитуриентам) — маска присутствия: неактивные («ушли в
        другой вуз») не предлагают себя, не зачисляются и освобождают места.
    Возвращает устойчивое паросочетание:
      admitted[A] = p_idx или -1
      passing[P]  = худший (минимальный) принятый балл или -1, если мест нет.

    `max_priority` сохранён в сигнатуре для совместимости с вызовом и больше
    не используется: порядок задаётся персональными списками предпочтений.
    """
    A = applicant_idx.max() + 1
    P = seats_init.size
    N = priority.size

    admitted = np.full(A, -1, np.int32)
    passing = np.full(P, -1, np.int16)

    # --- CSR-раскладка заявок по абитуриентам --------------------------------
    app_count = np.zeros(A, np.int32)
    for i in range(N):
        app_count[applicant_idx[i]] += 1
    app_off = np.zeros(A + 1, np.int32)
    for a in range(A):
        app_off[a + 1] = app_off[a] + app_count[a]
    app_rows = np.empty(N, np.int32)
    cursor = np.empty(A, np.int32)
    for a in range(A):
        cursor[a] = app_off[a]
    for i in range(N):
        a = applicant_idx[i]
        app_rows[cursor[a]] = i
        cursor[a] += 1
    # список предпочтений = заявки по возрастанию приоритета (insertion sort)
    for a in range(A):
        start = app_off[a]
        end = app_off[a + 1]
        for x in range(start + 1, end):
            key_row = app_rows[x]
            kp = priority[key_row]
            y = x - 1
            while y >= start and priority[app_rows[y]] > kp:
                app_rows[y + 1] = app_rows[y]
                y -= 1
            app_rows[y + 1] = key_row

    # --- ранг строки: балл с тай-брейком по jitter ---------------------------
    rank = np.empty(N, np.int32)
    for i in range(N):
        rank[i] = np.int32(total_score[i]) * RANK_SCALE + np.int32(jitter[i] * RANK_SCALE)

    max_seats = np.max(seats_init)
    if max_seats < 1:
        return admitted, passing

    seat_cnt = np.zeros(P, np.int32)
    tab_app = np.full((P, max_seats), -1, np.int32)
    tab_score = np.full((P, max_seats), -1, np.int16)
    tab_rank = np.full((P, max_seats), 0, np.int32)

    worst_score = np.full(P, -1, np.int16)
    worst_rank = np.full(P, -1, np.int32)
    worst_slot = np.zeros(P, np.int32)

    # --- указатели предпочтений и стек свободных -----------------------------
    ptr = np.empty(A, np.int32)
    for a in range(A):
        ptr[a] = app_off[a]
    free = np.empty(A, np.int32)
    in_free = np.zeros(A, np.uint8)
    top = 0
    for a in range(A):
        if app_count[a] > 0 and active[a]:
            free[top] = a
            in_free[a] = 1
            top += 1

    while top > 0:
        top -= 1
        s = free[top]
        in_free[s] = 0
        if ptr[s] >= app_off[s + 1]:
            continue  # список предпочтений исчерпан

        i = app_rows[ptr[s]]
        ptr[s] += 1
        p = program_idx[i]
        r = rank[i]
        raw = np.int16(total_score[i])

        if seat_cnt[p] < seats_init[p]:
            # есть свободное место — зачисляем
            slot = seat_cnt[p]
            tab_app[p, slot] = s
            tab_score[p, slot] = raw
            tab_rank[p, slot] = r
            seat_cnt[p] += 1
            admitted[s] = p
            if worst_rank[p] == -1 or r < worst_rank[p]:
                worst_rank[p] = r
                worst_score[p] = raw
                worst_slot[p] = slot
        elif seats_init[p] > 0 and r > worst_rank[p]:
            # мест нет, но абитуриент сильнее худшего — выбиваем худшего
            kick = worst_slot[p]
            old = tab_app[p, kick]
            admitted[old] = -1
            tab_app[p, kick] = s
            tab_score[p, kick] = raw
            tab_rank[p, kick] = r
            admitted[s] = p

            wr, ws, wslt = r, raw, kick
            for t in range(seat_cnt[p]):
                if tab_rank[p, t] < wr:
                    wr, ws, wslt = tab_rank[p, t], tab_score[p, t], t
            worst_rank[p] = wr
            worst_score[p] = ws
            worst_slot[p] = wslt

            # выбитый снова ищет место на следующем приоритете
            if ptr[old] < app_off[old + 1] and in_free[old] == 0:
                free[top] = old
                in_free[old] = 1
                top += 1
        else:
            # отказ — пробуем следующий приоритет
            if ptr[s] < app_off[s + 1] and in_free[s] == 0:
                free[top] = s
                in_free[s] = 1
                top += 1

    for p in range(P):
        if seat_cnt[p] > 0:
            passing[p] = worst_score[p]

    return admitted, passing


class AdmissionMonteCarlo:
    """
    MC-модель шансов зачисления по опубликованным баллам.

    Списки окончательные: балл берётся из них как есть, а ноль — это ноль.
    Раньше модель дорисовывала недостающие баллы (KDE по каждому экзамену,
    личное среднее абитуриента, глобальное распределение) — это имело смысл,
    пока результаты ещё не были выложены и человек без балла мог оказаться
    кем угодно. Теперь выложены все, и человек с нулём обогнать человека с
    выставленным баллом не может: любая импутация только придумывала бы ему
    чужие баллы и занижала шансы остальных.

    Поэтому конкурсный балл фиксирован, а случайными остаются ровно два
    источника неопределённости, которых в данных действительно нет:
      • жребий на равных баллах (jitter);
      • отток (opt-out) части абитуриентов без согласия в другие вузы.

    `consent_elsewhere` — коды тех, кто подал согласие в ДРУГОЙ вуз. Раньше
    такого знания не было и уход приходилось целиком угадывать по баллу; код
    поступающего единый для всех вузов, поэтому теперь это наблюдаемый факт, и
    для таких абитуриентов вероятность ухода берётся отдельная, высокая.
    """

    def __init__(self,
                 applications: pd.DataFrame,
                 applicants: pd.DataFrame | None,
                 submission_stats: pd.DataFrame,
                 *,
                 n_simulations: int = 10_000,
                 random_seed: int | None = None,
                 consent_elsewhere: set[str] | None = None):
        self.n_sim = n_simulations
        self.rng = np.random.default_rng(random_seed)

        logger.info("AdmissionMonteCarlo: подготовка данных…")

        self._rows = len(applications)

        # --- Индексация ------------------------------------------------------
        self._applicant2idx = {aid: i for i, aid in enumerate(applications["applicant_id"].unique())}
        self._program2idx = {c: i for i, c in enumerate(applications["program_code"].unique())}
        self.n_applicants = len(self._applicant2idx)
        self.n_programs = len(self._program2idx)

        # --- Вектора заявок --------------------------------------------------
        self.applicant_idx = applications["applicant_id"].map(self._applicant2idx).to_numpy(np.int32, copy=False)
        self.program_idx = applications["program_code"].map(self._program2idx).to_numpy(np.int32, copy=False)
        self.priority = applications["priority"].to_numpy(np.int16, copy=False)
        # Нормализация приоритетов: pr<=0 → глобальный max+1 (считаем наихудшим)
        if (self.priority <= 0).any():
            pr = self.priority.copy()
            pos = pr[pr > 0]
            max_pos = int(pos.max()) if pos.size else 1
            pr[pr <= 0] = max_pos + 1
            fixed = int((self.priority <= 0).sum())
            self.priority = pr
            logger.info("Нормализованы приоритеты: исправлено %d записей с pr<=0 → %d",
                        fixed, max_pos + 1)
        self._max_priority = int(self.priority.max()) if self._rows else 1

        self.vi_score = applications[SCORE_COL].to_numpy(np.int16, copy=False)
        self.id_ach = applications["id_achievements"].to_numpy(np.int16, copy=False)

        # Конкурсный балл: ВИ + индивидуальные достижения. Один и тот же во всех
        # симуляциях — списки окончательные, разыгрывать здесь нечего.
        self.total_score = (self.vi_score.astype(np.int32) + self.id_ach).astype(np.int16)
        n_zero = int((self.vi_score <= 0).sum())
        logger.info("   заявок: %d; из них без балла за ВИ: %d (идут в конкурсе с нулём).",
                    self._rows, n_zero)

        # --- Места по программам --------------------------------------------
        self.seats_per_program = np.zeros(self.n_programs, np.int32)
        for p_code, seats in submission_stats[["program_code", "num_places"]].values:
            if p_code in self._program2idx:
                self.seats_per_program[self._program2idx[p_code]] = int(seats)

        # --- Диагностические/выходные коллекции -----------------------------
        self.admit_counter = np.zeros((self.n_applicants, self.n_programs), np.int32)
        self.pass_scores_collect = [[] for _ in range(self.n_programs)]
        self._apps_by_applicant: Dict[str, List[str]] = (
            applications.groupby("applicant_id")["program_code"].apply(list).to_dict()
        )

        # --- Отток в другие вузы (opt-out) ----------------------------------
        # Из пула E (абитуриенты без согласия В ЭТОМ вузе) в каждой симуляции
        # часть «уходит»; уход освобождает места и поднимает шансы оставшихся, а
        # собственный уход абитуриента уводится в p_excluded.
        #
        # Пул делится надвое, и это главное, что модель знает про отток:
        #
        #   • подал согласие в ДРУГОЙ вуз — уходит почти наверняка. Согласие
        #     единовременно можно держать только одно, так что человек уже
        #     выбрал, и здесь его место освободится. Это не догадка, а факт из
        #     списков: код поступающего единый, и согласие видно во всех вузах;
        #   • не подал нигде — старая сценарная оценка: вероятность ∝ перцентиль
        #     балла^alpha, ожидаемая доля ушедших ≈ opt_out_ratio.
        self.opt_out_enabled = bool(settings.opt_out_enabled)
        self.opt_out_ratio = float(settings.opt_out_ratio)
        self.opt_out_alpha = float(settings.opt_out_alpha)
        self.committed_leave = float(settings.opt_out_committed)

        # consent на абитуриента: True, если согласие есть хотя бы по одной
        # заявке В ЭТОМ вузе (модель считается по вузам отдельно).
        consent_rows = applications["consent"].to_numpy(copy=False).astype(bool)
        self.has_consent = np.zeros(self.n_applicants, dtype=bool)
        np.logical_or.at(self.has_consent, self.applicant_idx, consent_rows)

        # Кто уже подал согласие в другом вузе.
        self.committed_elsewhere = np.zeros(self.n_applicants, dtype=bool)
        for applicant_id, index in self._applicant2idx.items():
            if applicant_id in (consent_elsewhere or ()):
                self.committed_elsewhere[index] = True
        # Согласие здесь перевешивает: человек выбрал этот вуз.
        self.committed_elsewhere &= ~self.has_consent

        # пул выбывающих E: все без согласия в этом вузе
        self._optout_pool = np.where(~self.has_consent)[0].astype(np.int32)

        # Вероятности ухода считаются один раз: балл больше не меняется от
        # симуляции к симуляции, поэтому и перцентиль способности постоянен.
        # Раньше для этого был режим MC_OPTOUT_MODE — выбор между пересчётом
        # по импутированным баллам и «базовой способностью»; без импутации оба
        # режима дают один и тот же вектор.
        if self.opt_out_enabled and self._optout_pool.size:
            ability = np.zeros(self.n_applicants, np.float64)
            np.maximum.at(ability, self.applicant_idx, self.total_score.astype(np.float64))
            self._leave_prob = self._compute_leave_probs(ability[self._optout_pool])
            # Тем, кто уже отдал согласие другому вузу, ставим свою, высокую
            # вероятность — по перцентилю балла её угадывать больше не нужно.
            committed = self.committed_elsewhere[self._optout_pool]
            self._leave_prob[committed] = self.committed_leave
        else:
            self._leave_prob = np.zeros(0, np.float64)

        # счётчик присутствия абитуриента (для p_excluded и условного p_fail)
        self.present_count = np.zeros(self.n_applicants, np.float64)

        logger.info(
            "   opt-out: %s; пул без согласия=%d/%d (из них с согласием в другом "
            "вузе=%d, уходят с p=%.2f); ratio=%.2f, alpha=%.2f.",
            "ON" if self.opt_out_enabled else "OFF",
            int(self._optout_pool.size), self.n_applicants,
            int(self.committed_elsewhere.sum()), self.committed_leave,
            self.opt_out_ratio, self.opt_out_alpha,
        )

    # --------------------------------------------------------------------- #
    def _compute_leave_probs(self, ability_pool: np.ndarray) -> np.ndarray:
        """
        Вероятности «уйти» для пула E по баллу.
          • перцентиль (0,1] внутри пула → вес p^alpha;
          • масштабируем так, чтобы средняя вероятность ≈ opt_out_ratio
            (ожидаемая доля ушедших), затем клипуем в [0,1].
        Это эффективная (per-applicant Bernoulli) трактовка «исключить долю ratio»:
        ожидаемая доля совпадает, без дорогой выборки фикс. размера на каждую симуляцию.
        """
        n = ability_pool.size
        if n == 0:
            return np.zeros(0, np.float64)
        # перцентиль через двойной argsort (ранг 1..n)
        ranks = np.empty(n, np.int64)
        ranks[np.argsort(ability_pool, kind="stable")] = np.arange(1, n + 1)
        pct = ranks / n
        w = pct ** self.opt_out_alpha
        mean_w = float(w.mean())
        if mean_w <= 0.0:
            return np.zeros(n, np.float64)
        return np.clip((self.opt_out_ratio / mean_w) * w, 0.0, 1.0)

    # --------------------------------------------------------------------- #
    def _single_simulation(self) -> None:
        jitter = self.rng.random(self._rows).astype(np.float32)

        # Маска присутствия: «ушедшие» из пула E не участвуют в этом прогоне.
        active = np.ones(self.n_applicants, np.uint8)
        if self.opt_out_enabled and self._optout_pool.size:
            leaves = self.rng.random(self._optout_pool.size) < self._leave_prob
            active[self._optout_pool[leaves]] = 0
        self.present_count += active

        admitted, passing = _simulate_admission_numba(
            self.priority, self.program_idx, self.applicant_idx,
            self.total_score, self.seats_per_program, jitter, active,
            max_priority=self._max_priority,
        )

        # Счётчики поступлений по программам
        for a_idx, p_idx in enumerate(admitted):
            if p_idx != -1:
                self.admit_counter[a_idx, p_idx] += 1

        # Проходные баллы
        for p_idx, scr in enumerate(passing):
            if scr != -1:
                self.pass_scores_collect[p_idx].append(int(scr))

    # --------------------------------------------------------------------- #
    def run_simulation(self) -> None:
        logger.info("Monte-Carlo: запускаем %d итераций…", self.n_sim)
        for i in range(self.n_sim):
            self._single_simulation()
            if (i + 1) % 500 == 0 or i == self.n_sim - 1:
                logger.debug("… %d / %d готово", i + 1, self.n_sim)

        self.prob_matrix = self.admit_counter / self.n_sim

        # Вероятности по программам
        self.p_admit = {
            aid: {
                code: float(self.prob_matrix[self._applicant2idx[aid], self._program2idx[code]])
                for code in self._apps_by_applicant.get(aid, [])
            }
            for aid in self._applicant2idx
        }

        # Квантили проходного (только по программам, где были наборы мест)
        self.pass_score_quantiles = {
            p_code: {
                "q90": float(np.percentile(scores, 90)),
                "q95": float(np.percentile(scores, 95)),
            }
            for p_code, p_idx in self._program2idx.items()
            if (scores := np.asarray(self.pass_scores_collect[p_idx])).size
        }

        # Диагностика с учётом оттока:
        #   p_excluded            = доля прогонов, где сам абитуриент «ушёл»;
        #   p_fail_when_included  = доля «не поступил» среди прогонов, где он присутствовал.
        # При выключенном opt-out present_count == n_sim → поведение как раньше
        # (p_excluded = 0, p_fail = доля по всем прогонам).
        admitted_totals = self.admit_counter.sum(axis=1)  # present-and-admitted по applicant
        self.diag = {}
        for aid, a_idx in self._applicant2idx.items():
            present = float(self.present_count[a_idx])
            if present > 0.0:
                p_excluded = max(0.0, 1.0 - present / self.n_sim)
                p_fail = min(1.0, max(0.0, 1.0 - admitted_totals[a_idx] / present))
            else:
                p_excluded, p_fail = 1.0, 0.0
            self.diag[aid] = {"p_excluded": p_excluded, "p_fail_when_included": p_fail}

        logger.info(
            "Monte-Carlo завершён: %d абитуриентов; %d направлений с квантилями. (opt-out=%s)",
            len(self.p_admit),
            len(self.pass_score_quantiles),
            "ON" if self.opt_out_enabled else "OFF",
        )

    # ------------------------------ API --------------------------------- #
    def get_probabilities(self) -> Dict[str, Dict[str, float]]:
        return self.p_admit

    def get_passing_score_quantiles(self) -> Dict[str, Dict[str, float]]:
        return self.pass_score_quantiles

    def get_diagnostics(self) -> Dict[str, Dict[str, float]]:
        """{applicant_id: {'p_excluded': ..., 'p_fail_when_included': ...}}"""
        return self.diag
