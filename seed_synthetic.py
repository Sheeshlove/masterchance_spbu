#!/usr/bin/env python3
"""
Генератор синтетических данных в формате, который читает бот.

Создаёт (или дополняет) БД `settings.database_url` и заполняет все таблицы,
которые опрашивает `applicant_handler` в `app/presentation/bot.py`:

    institutes → departments → programs → applicants → applications
    + submission_stats   (время последнего обновления для /start)
    + program_quantiles  (q90/q95 — результат Monte-Carlo)
    + admission_probabilities (вероятности — результат Monte-Carlo)
    + admission_diagnostics   (p_excluded / p_fail_when_included)
    + exam_sessions      (расписание экзаменов)

Данные внутренне согласованы так, как ожидает бот:
  conditional = uncond / (1 - p_excluded);  fail_cond = p_fail_when_included.

Запуск:
    python seed_synthetic.py                 # 200 абитуриентов
    python seed_synthetic.py --applicants 50 # другое число
    python seed_synthetic.py --reset         # очистить таблицы перед заливкой

После заливки печатает несколько кодов абитуриентов — их можно ввести в бота.
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config.config import settings
from app.infrastructure.db.models import (
    AdmissionDiagnosticsModel,
    AdmissionProbabilityModel,
    ApplicantModel,
    ApplicationModel,
    Base,
    DepartmentModel,
    ExamSessionModel,
    InstituteModel,
    ProgramModel,
    ProgramQuantileModel,
    SubmissionStatsModel,
)

SEED = 42
NOW = datetime(2026, 6, 22, 12, 0, 0)  # совпадает с "сегодня" в задаче (Москва, tz-naive)

# ── Структура каталога направлений (институт → факультеты → программы) ──────
INSTITUTES = [
    {
        "code": "01",
        "name": "Институт прикладной математики и механики",
        "departments": [
            {
                "code": "01.04.02",
                "name": "Прикладная математика и информатика",
                "programs": [
                    "Математическое моделирование",
                    "Анализ данных и машинное обучение",
                    "Вычислительная механика",
                ],
            },
            {
                "code": "02.04.01",
                "name": "Математика и компьютерные науки",
                "programs": [
                    "Дискретная математика и теория алгоритмов",
                    "Криптография и защита информации",
                ],
            },
        ],
    },
    {
        "code": "02",
        "name": "Институт компьютерных наук и технологий",
        "departments": [
            {
                "code": "09.04.01",
                "name": "Информатика и вычислительная техника",
                "programs": [
                    "Искусственный интеллект",
                    "Распределённые системы",
                    "Компьютерное зрение",
                ],
            },
            {
                "code": "09.04.04",
                "name": "Программная инженерия",
                "programs": [
                    "Разработка высоконагруженных систем",
                    "Инженерия данных",
                ],
            },
        ],
    },
    {
        "code": "03",
        "name": "Институт физики и инженерии",
        "departments": [
            {
                "code": "03.04.02",
                "name": "Физика",
                "programs": [
                    "Физика конденсированного состояния",
                    "Квантовые технологии",
                ],
            },
        ],
    },
]

EDUCATION_FORM = "Очная"
CONTRACT = "Бюджет"
REVIEW_STATUSES = ["Подана", "Рассмотрена", "Допущен к конкурсу"]


def build_catalog(rng: random.Random):
    """Возвращает списки ORM-объектов institutes/departments/programs."""
    institutes, departments, programs = [], [], []
    prog_seq = 700  # числовые коды программ как строки ('701', '702', ...)
    for inst in INSTITUTES:
        institutes.append(InstituteModel(code=inst["code"], name=inst["name"]))
        for dep in inst["departments"]:
            departments.append(
                DepartmentModel(
                    code=dep["code"],
                    name=dep["name"],
                    institute_code=inst["code"],
                )
            )
            for pname in dep["programs"]:
                prog_seq += 1
                programs.append(
                    ProgramModel(
                        code=str(prog_seq),
                        name=pname,
                        department_code=dep["code"],
                        is_ino=False,
                        is_international=rng.random() < 0.15,
                        university="spbpu",
                    )
                )
    return institutes, departments, programs


def make_exam_sessions(programs, rng: random.Random):
    """Несколько сессий на программу: часть в прошлом, часть в будущем."""
    sessions = []
    for prog in programs:
        n = rng.randint(1, 3)
        for i in range(n):
            # смещение от −20 до +40 дней относительно NOW → оба ветвления в боте
            offset_days = rng.randint(-20, 40)
            dt = (NOW + timedelta(days=offset_days)).replace(
                hour=rng.choice([10, 11, 14, 16]), minute=rng.choice([0, 30]), second=0
            )
            sessions.append(
                ExamSessionModel(
                    program_code=prog.code,
                    exam_code=f"{prog.department_code}_{i + 1:02d}",
                    dt=dt,
                    institute=prog.department_code.split(".")[0],
                    education_form=EDUCATION_FORM,
                    contract=CONTRACT,
                    program_name=prog.name,
                    program_pdf_url=f"https://example.edu/programs/{prog.code}.pdf",
                )
            )
    return sessions


def main() -> None:
    parser = argparse.ArgumentParser(description="Заливка синтетических данных для бота.")
    parser.add_argument("--applicants", type=int, default=200, help="сколько абитуриентов (default 200)")
    parser.add_argument("--reset", action="store_true", help="очистить таблицы перед заливкой")
    args = parser.parse_args()

    rng = random.Random(SEED)

    engine = create_engine(settings.database_url, echo=settings.db_echo, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()

    try:
        if args.reset:
            for model in (
                AdmissionProbabilityModel,
                AdmissionDiagnosticsModel,
                ProgramQuantileModel,
                ExamSessionModel,
                SubmissionStatsModel,
                ApplicationModel,
                ApplicantModel,
                ProgramModel,
                DepartmentModel,
                InstituteModel,
            ):
                session.query(model).delete()
            session.commit()

        # 1) Каталог
        institutes, departments, programs = build_catalog(rng)
        session.add_all(institutes + departments + programs)
        session.flush()
        prog_codes = [p.code for p in programs]

        # 2) Квантили проходного балла по программам (q90 < q95)
        prog_q = {}
        for p in programs:
            q90 = round(rng.uniform(165, 235), 1)
            q95 = round(q90 + rng.uniform(4, 18), 1)
            prog_q[p.code] = (q90, q95)
            session.add(ProgramQuantileModel(program_code=p.code, q90=q90, q95=q95))

        # 3) submission_stats (время последнего обновления для /start)
        generated_at = NOW - timedelta(hours=3)
        for p in programs:
            session.add(
                SubmissionStatsModel(
                    program_code=p.code,
                    num_places=rng.randint(15, 60),
                    num_applications=rng.randint(40, 350),
                    generated_at=generated_at,
                )
            )

        # 4) Экзамены
        session.add_all(make_exam_sessions(programs, rng))

        # 5) Абитуриенты + заявки + Monte-Carlo результаты
        sample_codes: list[str] = []
        for n in range(args.applicants):
            applicant_id = str(1_000_001 + n)  # коды вида '1000001'
            session.add(ApplicantModel(id=applicant_id, university="spbpu"))

            # абитуриент подаёт на 1..5 программ с приоритетами 1..k
            k = rng.randint(1, 5)
            chosen = rng.sample(prog_codes, k)
            # «способность» абитуриента → балл и вероятности
            ability = rng.gauss(0.0, 1.0)
            has_scores = rng.random() < 0.7  # часть ещё без баллов → бот покажет даты экзаменов

            uncond = {}
            for prio, code in enumerate(chosen, start=1):
                vi = int(max(0, min(100, round(50 + ability * 12)))) if has_scores else 0
                s1 = int(max(0, min(100, round(45 + ability * 14)))) if has_scores else 0
                s2 = int(max(0, min(100, round(40 + ability * 13)))) if has_scores else 0
                ida = rng.randint(0, 10)
                tida = 0
                total_score = vi + ida + tida if has_scores else 0
                session.add(
                    ApplicationModel(
                        program_code=code,
                        applicant_id=applicant_id,
                        total_score=total_score,
                        vi_score=vi,
                        subject1_score=s1,
                        subject2_score=s2,
                        id_achievements=ida,
                        target_id_achievements=tida,
                        priority=prio,
                        consent=(prio == 1 and rng.random() < 0.6),
                        review_status=rng.choice(REVIEW_STATUSES),
                    )
                )
                # безусловная вероятность: выше для высокого приоритета и сильного абитуриента
                p = max(0.01, min(0.97, 0.5 + 0.18 * ability - 0.12 * (prio - 1)))
                p *= rng.uniform(0.85, 1.0)
                uncond[code] = round(p, 4)

            # нормируем, чтобы сумма безусловных не превышала ~0.95
            s = sum(uncond.values())
            if s > 0.95:
                scale = 0.95 / s
                uncond = {c: round(v * scale, 4) for c, v in uncond.items()}

            for code, p in uncond.items():
                session.add(
                    AdmissionProbabilityModel(
                        applicant_id=applicant_id, program_code=code, probability=p
                    )
                )

            # диагностика, согласованная с тем, как бот считает условные вероятности
            p_excluded = round(rng.uniform(0.0, 0.15), 4)
            p_incl = max(1e-9, 1.0 - p_excluded)
            cond_sum = min(1.0, sum(uncond.values()) / p_incl)
            p_fail_when_included = round(max(0.0, 1.0 - cond_sum), 4)
            session.add(
                AdmissionDiagnosticsModel(
                    applicant_id=applicant_id,
                    p_excluded=p_excluded,
                    p_fail_when_included=p_fail_when_included,
                )
            )

            if n < 8:
                sample_codes.append(applicant_id)

        session.commit()

        # ── отчёт ──────────────────────────────────────────────────────────
        print("✅ Синтетические данные залиты в:", settings.database_url)
        print(f"   институтов:   {len(institutes)}")
        print(f"   факультетов:  {len(departments)}")
        print(f"   программ:     {len(programs)}")
        print(f"   абитуриентов: {args.applicants}")
        print(f"   обновлено:    {generated_at:%d.%m.%Y %H:%M} (submission_stats.generated_at)")
        print("\nПримеры кодов абитуриентов для ввода в бота:")
        print("   " + "  ".join(sample_codes))
    finally:
        session.close()


if __name__ == "__main__":
    main()
