# app/services/admission_monte_carlo.py
from __future__ import annotations

from typing import Dict, Tuple, List

import numpy as np
import pandas as pd
from numba import njit
from scipy.stats import gaussian_kde

from app.config.config import settings
from app.config.logger import logger

# Шкала вступительного испытания у разных экзаменов разная: у большинства
# направлений СПбГУ это 100 баллов, а, например, у 38.04.02 — 200. Раньше сотня
# была зашита глобально, и всё, что выше, модель считала «неизвестным»: баллы
# ≥ 100 выпадали из статистики, а импутация не могла выдать больше 100 даже там,
# где потолок вдвое выше. Теперь шкала определяется по каждому экзамену
# отдельно, а вся статистика считается в ДОЛЯХ от неё — только так сравнимы
# баллы человека, сдававшего и по 100-, и по 200-балльной шкале.
MIN_EXAM_SCALE = 100
SCORE_COL = "vi_score"

KDE_RESAMPLE_N = 10_000
RANK_SCALE = 100

# Сетка для CDF в долях шкалы: шаг 0,5 %. Одна и та же для всех экзаменов,
# поэтому разные шкалы сравнимы и хранятся в одном массиве.
FRACTION_BINS = 200


def _kde_resample(kde: gaussian_kde, n: int, rng: np.random.Generator) -> np.ndarray:
    """Безопасная выборка из gaussian_kde для случаев, когда random_state недоступен."""
    try:
        return kde.resample(n, random_state=rng).ravel()
    except TypeError:
        seed = int(rng.integers(0, 2 ** 32 - 1, dtype=np.uint32))
        saved_state = np.random.get_state()
        try:
            np.random.seed(seed)
            return kde.resample(n).ravel()
        finally:
            np.random.set_state(saved_state)


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
    MC-модель шансов зачисления, с поддержкой «заморозки» нулей по истёкшим экзаменам
    и оттока (opt-out) части абитуриентов без согласия в другие вузы.
      • Импутация ВИ: по personal μ → CDF конкретного экзамена → глобальная CDF.
      • Freeze: если для (applicant×exam_id) нет ни одной известной оценки и exam_id помечен как истёкший,
        нули остаются нулями (импутации нет).
      • Opt-out (если включён в settings): из пула без согласия часть «уходит» в каждой
        симуляции, освобождая места; уходы влияют на p_admit и уводятся в p_excluded.
    """

    def __init__(self,
                 applications: pd.DataFrame,
                 applicants: pd.DataFrame | None,
                 submission_stats: pd.DataFrame,
                 programs_meta: pd.DataFrame,
                 *,
                 n_simulations: int = 10_000,
                 random_seed: int | None = None,
                 expired_exam_ids: set[str] | None = None,
                 freeze_expired_exams: bool | None = None):
        self.n_sim = n_simulations
        self.rng = np.random.default_rng(random_seed)

        # Freeze экзаменов
        self.freeze_expired_exams = (
            settings.exam_freeze_enabled if freeze_expired_exams is None else bool(freeze_expired_exams)
        )
        self._expired_exam_ids_in = set(expired_exam_ids or [])
        logger.info(
            "AdmissionMonteCarlo: подготовка данных… (exam-freeze: %s)",
            "ON" if self.freeze_expired_exams else "OFF",
        )

        self._rows = len(applications)

        # --- Индексация ------------------------------------------------------
        self._applicant2idx = {aid: i for i, aid in enumerate(applications["applicant_id"].unique())}
        self._program2idx = {c: i for i, c in enumerate(applications["program_code"].unique())}
        self.n_applicants = len(self._applicant2idx)
        self.n_programs = len(self._program2idx)

        # exam_id: department_code (обычные) или department_code__eng (международные)
        meta = programs_meta.set_index("program_code")
        self.exam_id = np.empty(self._rows, dtype="U24")
        for i, p_code in enumerate(applications["program_code"]):
            row = meta.loc[p_code]
            dept = str(row["department_code"])
            self.exam_id[i] = f"{dept}__eng" if bool(row["is_international"]) else dept

        self._exam2idx = {eid: j for j, eid in enumerate(np.unique(self.exam_id))}
        self.exam_idx = np.vectorize(self._exam2idx.get)(self.exam_id).astype(np.int32)
        self.n_exams = len(self._exam2idx)
        logger.info("   найдено %d различных экзаменов.", self.n_exams)

        # Какие exam_id истёкли (по индексу экзамена)
        self.expired_exam_mask = np.zeros(self.n_exams, dtype=bool)
        if self._expired_exam_ids_in:
            for eid, j in self._exam2idx.items():
                if eid in self._expired_exam_ids_in:
                    self.expired_exam_mask[j] = True
        n_expired_ids = int(self.expired_exam_mask.sum())

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

        self.vi_score = applications[SCORE_COL].to_numpy(np.int16, copy=False)
        self.id_ach = applications["id_achievements"].to_numpy(np.int16, copy=False)

        # Ряды по (applicant, exam) и по applicant
        self._rows_by_app_exam: Dict[Tuple[int, int], np.ndarray] = {}
        for r, (a, e) in enumerate(zip(self.applicant_idx, self.exam_idx)):
            self._rows_by_app_exam.setdefault((a, e), []).append(r)
        for k in list(self._rows_by_app_exam):
            self._rows_by_app_exam[k] = np.asarray(self._rows_by_app_exam[k], dtype=np.int32)

        # --- Шкала каждого экзамена -----------------------------------------
        # Берём по наблюдаемому максимуму, но не ниже 100: если по экзамену
        # результатов ещё мало, лучше считать шкалу обычной, чем занизить её
        # до максимума пары случайных баллов.
        self.exam_scale = np.full(self.n_exams, MIN_EXAM_SCALE, np.int32)
        for j in range(self.n_exams):
            observed = self.vi_score[(self.exam_idx == j) & (self.vi_score > 0)]
            if observed.size:
                self.exam_scale[j] = max(MIN_EXAM_SCALE, int(observed.max()))
        self.row_scale = self.exam_scale[self.exam_idx].astype(np.float64)
        non_standard = {
            eid: int(self.exam_scale[j])
            for eid, j in self._exam2idx.items()
            if self.exam_scale[j] != MIN_EXAM_SCALE
        }
        if non_standard:
            logger.info("   экзамены не со 100-балльной шкалой: %s", non_standard)

        # --- Персональные средние (μ), в долях шкалы -------------------------
        # Балл на потолке шкалы исключаем — там скапливаются «упёршиеся в
        # максимум», и распределение в этой точке уже не про способности.
        # Раньше это была глобальная сотня, из-за чего на 200-балльном экзамене
        # выбрасывалась вся верхняя половина.
        logger.info("→ вычисляем personal_mu …")
        known = (self.vi_score > 0) & (self.vi_score < self.row_scale)
        known_frac = self.vi_score[known] / self.row_scale[known]

        sums = np.bincount(self.applicant_idx[known],
                           known_frac.astype(np.float32),
                           minlength=self.n_applicants)
        cnts = np.bincount(self.applicant_idx[known], minlength=self.n_applicants)
        # personal_mu — доля от шкалы (0..1), а не сырой балл: иначе у человека
        # с 90 из 100 и 180 из 200 среднее «135» не значит ничего.
        self.personal_mu = np.zeros(self.n_applicants, np.float32)
        mask = cnts > 0
        self.personal_mu[mask] = sums[mask] / cnts[mask]
        logger.debug("   персональный μ есть у %d / %d абитуриентов.", mask.sum(), self.n_applicants)

        # --- KDE → CDF по каждому экзамену (fallback → глобальная CDF) ------
        # Всё в долях шкалы, поэтому сетка бинов одна на все экзамены.
        logger.info("→ строим KDE → CDF (%d точек)…", KDE_RESAMPLE_N)
        self._exam_cdf = np.zeros((self.n_exams, FRACTION_BINS), np.float32)

        def _cdf_from(samples: np.ndarray) -> np.ndarray | None:
            """Доли → CDF по сетке FRACTION_BINS. None, если выборка непригодна."""
            if samples.size < 2 or np.ptp(samples) <= 0:
                return None
            try:
                kde = gaussian_kde(samples, bw_method="scott")
                raw = _kde_resample(kde, KDE_RESAMPLE_N, self.rng)
            except Exception:
                return None
            bins = np.clip(np.ceil(raw * FRACTION_BINS), 1, FRACTION_BINS).astype(int)
            hist = np.bincount(bins, minlength=FRACTION_BINS + 1)[1:].astype(np.float32)
            if hist.sum() <= 0:
                return None
            return np.cumsum(hist / hist.sum())

        global_frac = known_frac.astype(np.float64)
        self.global_cdf = _cdf_from(global_frac)
        if self.global_cdf is None:  # вырожденные данные — равномерная доля
            self.global_cdf = np.linspace(1.0 / FRACTION_BINS, 1.0, FRACTION_BINS, dtype=np.float32)

        for eid, j in self._exam2idx.items():
            mask_e = (self.exam_idx == j) & known
            cdf = _cdf_from((self.vi_score[mask_e] / self.row_scale[mask_e]).astype(np.float64))
            if cdf is None:
                self._exam_cdf[j] = self.global_cdf
                logger.debug("   exam %s: fallback → глобальная CDF", eid)
            else:
                self._exam_cdf[j] = cdf
                logger.debug("   exam %s: KDE ok (шкала %d, %d образцов)",
                             eid, self.exam_scale[j], int(mask_e.sum()))

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
        # Разброс тоже в долях шкалы — иначе σ, посчитанная в основном по
        # 100-балльным экзаменам, применялась бы к 200-балльному как есть.
        self.global_sigma = float(global_frac.std(ddof=1)) if global_frac.size else 0.15

        # --- Заморозка нулей по истёкшим экзаменам (подготовка) -------------
        # Для каждой группы (applicant×exam) отметим freeze, если:
        #   • exam_freeze включен;
        #   • exam_id входит в expired_exam_ids;
        #   • в группе нет ни одной известной оценки (все vi==0).
        self._freeze_group: dict[tuple[int, int], bool] = {}
        frozen_groups = 0
        frozen_rows_total = 0
        if self.freeze_expired_exams and n_expired_ids > 0:
            for (a_idx, e_idx), rows in self._rows_by_app_exam.items():
                has_known = (self.vi_score[rows] > 0).any()
                to_freeze = bool(self.expired_exam_mask[e_idx] and not has_known)
                self._freeze_group[(a_idx, e_idx)] = to_freeze
                if to_freeze:
                    frozen_groups += 1
                    frozen_rows_total += int(rows.size)
        else:
            for k in self._rows_by_app_exam.keys():
                self._freeze_group[k] = False

        logger.info(
            "   истёкших exam_id=%d; замороженных групп a×exam=%d; затронуто строк=%d.",
            n_expired_ids, frozen_groups, frozen_rows_total
        )

        # --- Отток в другие вузы (opt-out) ----------------------------------
        # Модель (см. config.py): из пула E (абитуриенты без согласия НИГДЕ)
        # в каждой симуляции часть «уходит» с вероятностью ∝ перцентиль
        # способности^alpha; ожидаемая доля ушедших ≈ opt_out_ratio. Уход
        # освобождает места и поднимает шансы оставшихся; собственный уход
        # абитуриента уводится в p_excluded.
        self.opt_out_enabled = bool(settings.opt_out_enabled)
        self.opt_out_ratio = float(settings.opt_out_ratio)
        self.opt_out_alpha = float(settings.opt_out_alpha)
        self.opt_out_mode = str(settings.opt_out_mode)

        # consent на абитуриента: True, если согласие есть хотя бы по одной заявке
        consent_rows = applications["consent"].to_numpy(copy=False).astype(bool)
        self.has_consent = np.zeros(self.n_applicants, dtype=bool)
        np.logical_or.at(self.has_consent, self.applicant_idx, consent_rows)
        # пул выбывающих E: абитуриенты без согласия нигде
        self._optout_pool = np.where(~self.has_consent)[0].astype(np.int32)

        # базовая способность (режим fixed и fallback): personal_mu, нули → медиана
        base_ability = self.personal_mu.astype(np.float64).copy()
        known_ab = base_ability[base_ability > 0]
        med_ability = float(np.median(known_ab)) if known_ab.size else 0.0
        base_ability[base_ability <= 0] = med_ability
        self._base_ability = base_ability

        if self.opt_out_enabled and self.opt_out_mode == "fixed" and self._optout_pool.size:
            self._fixed_leave_prob = self._compute_leave_probs(self._base_ability[self._optout_pool])
        else:
            self._fixed_leave_prob = None

        # счётчик присутствия абитуриента (для p_excluded и условного p_fail)
        self.present_count = np.zeros(self.n_applicants, np.float64)

        logger.info(
            "   opt-out: %s; пул без согласия=%d/%d; ratio=%.2f, alpha=%.2f, mode=%s.",
            "ON" if self.opt_out_enabled else "OFF",
            int(self._optout_pool.size), self.n_applicants,
            self.opt_out_ratio, self.opt_out_alpha, self.opt_out_mode,
        )

    # --------------------------------------------------------------------- #
    def _compute_leave_probs(self, ability_pool: np.ndarray) -> np.ndarray:
        """
        Вероятности «уйти» для пула E по способности.
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
        vi = self.vi_score.copy()

        # Импутация по группам (applicant×exam): единый балл на группу.
        for (a_idx, e_idx), rows in self._rows_by_app_exam.items():
            if (vi[rows] == 0).any():
                existing = vi[rows][vi[rows] > 0]
                if existing.size:
                    # В группе есть реальная оценка → копируем её всем нулевым
                    vi[rows] = existing[0]
                    continue

                # Заморозка: если экзамен истёк и баллов в группе нет — оставляем нули
                if self._freeze_group.get((a_idx, e_idx), False):
                    continue

                # Иначе имитируем. Разыгрывается ДОЛЯ от шкалы, и только потом
                # она переводится в баллы этого экзамена: 0,9 на 100-балльном —
                # это 90, а на 200-балльном — 180.
                scale = int(self.exam_scale[e_idx])
                if self.personal_mu[a_idx] > 0:
                    frac = self.rng.normal(self.personal_mu[a_idx], self.global_sigma)
                else:
                    u = float(self.rng.random())
                    frac = (1 + int(np.searchsorted(self._exam_cdf[e_idx], u))) / FRACTION_BINS
                vi[rows] = int(np.clip(round(frac * scale), 1, scale))

        total = (vi + self.id_ach).astype(np.int16)
        jitter = self.rng.random(self._rows).astype(np.float32)

        # Маска присутствия: «ушедшие» из пула E не участвуют в этом прогоне.
        active = np.ones(self.n_applicants, np.uint8)
        if self.opt_out_enabled and self._optout_pool.size:
            pool = self._optout_pool
            if self.opt_out_mode == "fixed":
                leave_prob = self._fixed_leave_prob
            else:  # per_simulation: способность по текущим импутированным баллам
                ability = np.zeros(self.n_applicants, np.float64)
                np.maximum.at(ability, self.applicant_idx, total.astype(np.float64))
                leave_prob = self._compute_leave_probs(ability[pool])
            leaves = self.rng.random(pool.size) < leave_prob
            active[pool[leaves]] = 0
        self.present_count += active

        admitted, passing = _simulate_admission_numba(
            self.priority, self.program_idx, self.applicant_idx,
            total, self.seats_per_program, jitter, active,
            max_priority=int(self.priority.max()),
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
            "Monte-Carlo завершён: %d абитуриентов; %d направлений с квантилями. (opt-out=%s, freeze=%s)",
            len(self.p_admit),
            len(self.pass_score_quantiles),
            "ON" if self.opt_out_enabled else "OFF",
            "ON" if self.freeze_expired_exams else "OFF",
        )

    # ------------------------------ API --------------------------------- #
    def get_probabilities(self) -> Dict[str, Dict[str, float]]:
        return self.p_admit

    def get_passing_score_quantiles(self) -> Dict[str, Dict[str, float]]:
        return self.pass_score_quantiles

    def get_diagnostics(self) -> Dict[str, Dict[str, float]]:
        """{applicant_id: {'p_excluded': ..., 'p_fail_when_included': ...}}"""
        return self.diag
