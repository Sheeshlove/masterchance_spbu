from collections import defaultdict
from typing import List, Sequence

import pandas as pd

from app.config.logger import logger
from app.domain.models import (
    AdmissionDiagnostics,
    AdmissionProbability,
    Application,
    ProgramPassingQuantile,
    SubmissionStats,
)
from app.domain.universities import label, university_of_program
from app.infrastructure.db.repositories.program_repository import ProgramRepository
from app.services.admission_monte_carlo import AdmissionMonteCarlo

#: Куда складывать заявки, у которых код программы без известного префикса
#: вуза (старые записи из снапшотов, собранных до разделения по источникам).
_LEGACY = "—"


class RecalculateMonteCarloUseCase:
    """
    • Полностью очищает результаты Monte‑Carlo в БД
    • Запускает подсчёт, сохраняет новые probabilities / quantiles

    Прогон идёт ОТДЕЛЬНО ПО КАЖДОМУ ВУЗУ. Это не оптимизация, а условие
    правильности: модель раздаёт места алгоритмом отложенного согласия, где
    один абитуриент занимает ровно одно место. Внутри вуза так и есть, а между
    вузами — нет: человек может пройти и в СПбГУ, и в ВШЭ, и приоритеты у него
    в каждом вузе свои, несопоставимые с чужими. Общий прогон вдобавок считал
    бы перцентили баллов по смеси разных шкал — и «отток» получал бы не тот,
    кто на самом деле балансирует на границе.
    """

    def __init__(self, repo: ProgramRepository, n_simulations: int = 10_000):
        self._repo = repo
        self._n_sim = n_simulations

    @staticmethod
    def _df_from_records(recs):
        return pd.DataFrame([r.__dict__ for r in recs])

    def _run_model(
        self, applications: Sequence[Application], stats: Sequence[SubmissionStats]
    ) -> AdmissionMonteCarlo:
        apps_df = self._df_from_records(applications)
        stats_df = self._df_from_records(stats)

        model = AdmissionMonteCarlo(
            applications=apps_df,
            applicants=None,
            submission_stats=stats_df,
            n_simulations=self._n_sim,
            random_seed=None,
        )
        model.run_simulation()
        return model

    def execute(self) -> None:
        logger.info("→ Запуск Monte‑Carlo…")

        applications = self._repo.get_all_applications()
        stats = self._repo.get_all_submission_stats()

        apps_by_uni: dict[str, list[Application]] = defaultdict(list)
        for app in applications:
            apps_by_uni[university_of_program(app.program_code) or _LEGACY].append(app)

        stats_by_uni: dict[str, list[SubmissionStats]] = defaultdict(list)
        for row in stats:
            stats_by_uni[university_of_program(row.program_code) or _LEGACY].append(row)

        quant_models: List[ProgramPassingQuantile] = []
        prob_models: List[AdmissionProbability] = []
        diag_models: List[AdmissionDiagnostics] = []

        for uni in sorted(apps_by_uni):
            uni_apps = apps_by_uni[uni]
            uni_stats = stats_by_uni.get(uni, [])
            logger.info("Monte‑Carlo [%s]: %d заявок, %d программ со статистикой",
                        label(uni), len(uni_apps), len(uni_stats))
            if not uni_stats:
                # Без числа мест разыгрывать нечего: конкурс не определён.
                logger.warning("Monte‑Carlo [%s]: нет данных о местах — вуз пропущен.", label(uni))
                continue

            try:
                monte = self._run_model(uni_apps, uni_stats)
            except Exception as exc:  # noqa: BLE001 — один вуз не должен ронять пересчёт целиком
                logger.exception("Monte‑Carlo [%s] не посчитался: %s", label(uni), exc)
                continue

            quant_models.extend(
                ProgramPassingQuantile(program_code=code, **vals)
                for code, vals in monte.get_passing_score_quantiles().items()
            )
            for aid, mapping in monte.get_probabilities().items():
                prob_models.extend(
                    AdmissionProbability(applicant_id=aid, program_code=prog, probability=prob)
                    for prog, prob in mapping.items()
                )
            diag_models.extend(
                AdmissionDiagnostics(
                    applicant_id=aid,
                    p_excluded=vals["p_excluded"],
                    p_fail_when_included=vals["p_fail_when_included"],
                )
                for aid, vals in monte.get_diagnostics().items()
            )

        if not prob_models:
            # Пустой результат затёр бы уже посчитанные шансы и оставил сайт
            # с «шанс —» по всем направлениям. Прошлый расклад устарел, но он
            # хотя бы есть.
            raise RuntimeError(
                "Monte‑Carlo не дал результатов ни по одному вузу — "
                "прежние вероятности оставлены без изменений."
            )

        logger.info("→ Очистка старых Monte‑Carlo результатов…")
        self._repo.clear_admission_probabilities()
        self._repo.clear_program_quantiles()
        self._repo.clear_admission_diagnostics()
        self._repo.commit()

        logger.info("→ Сохраняем: probabilities=%d, quantiles=%d, diagnostics=%d",
                    len(prob_models), len(quant_models), len(diag_models))

        self._repo.add_program_quantiles_bulk(quant_models)
        self._repo.add_admission_probabilities_bulk(prob_models)
        self._repo.add_admission_diagnostics_bulk(diag_models)
        self._repo.commit()

        logger.info("Monte‑Carlo результаты обновлены в БД.")
