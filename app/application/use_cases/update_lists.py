from __future__ import annotations

from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from app.config.logger import logger
from app.domain.models import Department, Institute, Program
from app.infrastructure.db.repositories.program_repository import ProgramRepository
from app.infrastructure.parser.base import SPBGU, IApplicationsParser
from app.infrastructure.parser.parallel_master_parser import parse_programs_in_parallel


class UpdateApplicationListsUseCase:
    """
    Полная синхронизация заявок из приёмной комиссии с БД.
    Для каждого направления:
        1. скачиваем свежие данные
        2. удаляем «старые» заявки этого направления
        3. bulk-добавляем абитуриентов и заявки
        4. обновляем submission_stats

    Коммит происходит один раз в конце прохода.
    Если нужно «жёстко» всё-или-ничего — не ловите исключения per-program.
    """

    def __init__(self, repo: ProgramRepository, parser: Optional[IApplicationsParser] = None):
        self._repo = repo
        self._parser = parser  # опционально (для последовательного режима)

    # сохранён оригинальный последовательный режим
    def execute(self, university: str = SPBGU) -> None:
        if self._parser is None:
            raise RuntimeError("Для последовательного режима требуется parser")
        logger.info("=== Синхронизация списков заявок начинается (последовательно, вуз=%s) ===", university)
        try:
            programs = self._repo.get_programs_by_university(university)

            for prog in programs:
                logger.info("→ Обработка направления %s …", prog.code)
                try:
                    stats, applications = self._parser.parse(prog.code)
                except Exception as e:
                    logger.warning("✕ Пропускаем %s: %s", prog.code, e)
                    continue

                self._repo.delete_applications_by_program(prog.code)
                applicant_ids = [a.applicant_id for a in applications]
                self._repo.add_applicants_bulk(applicant_ids, university=university)
                self._repo.add_applications_bulk(applications)
                self._repo.add_submission_stats(stats)

            self._repo.commit()
            logger.info("✅ Синхронизация списков завершена без ошибок")
        except SQLAlchemyError as db_err:
            logger.exception("Ошибка транзакции, выполняем rollback: %s", db_err)
            self._repo._session.rollback()
            raise

    # новый параллельный режим
    def execute_spbgu(self, parallelism: int = 4) -> None:
        """
        Обновление СПбГУ одним проходом по текущей выгрузке отчёта.

        Отличие от execute_parallel: список программ берётся не из базы, а из
        самого отчёта, а код программы вычисляется из её названия и направления
        (stable_program_code). Поэтому перезаливка отчёта с новыми UUID больше
        ничего не ломает, а каталог обновляется этим же проходом — отдельный
        сидинг перестаёт быть обязательным.
        """
        from app.infrastructure.parser.parallel_master_parser import (
            deserialize_speciality,
            parse_specialities_in_parallel,
        )
        from app.infrastructure.parser.spbgu.spbgu_programs import (
            discover_programs,
            namespaced_department,
            namespaced_institute,
        )

        logger.info("=== Синхронизация СПбГУ (проход по отчёту, N=%d) ===", parallelism)
        try:
            discovered = discover_programs()
            if not discovered:
                raise RuntimeError("Отчёт не вернул ни одной специальности.")
            logger.info("В отчёте специальностей: %d", len(discovered))

            records = parse_specialities_in_parallel(
                [d["list_ref"] for d in discovered], parallelism=parallelism,
            )

            ok, empty = 0, 0
            seen_codes: set[str] = set()
            for rec in records:
                code = rec["program_code"]
                stats, applications = deserialize_speciality(rec)
                seen_codes.add(code)

                # каталог: институт → направление → программа
                spec = rec["speciality_code"]
                self._repo.add_institute(Institute(
                    code=namespaced_institute(spec),
                    name=f"Группа направлений {spec.split('.')[0]}",
                ))
                self._repo.add_department(Department(
                    code=namespaced_department(spec),
                    name=spec,
                    institute_code=namespaced_institute(spec),
                ))
                self._repo.add_program(Program(
                    code=code,
                    name=rec["program_name"],
                    department_code=namespaced_department(spec),
                    is_ino=False,
                    is_international=rec["is_international"],
                    university=SPBGU,
                ))

                if not applications:
                    # см. пояснение в execute_parallel: пустой список не повод
                    # стирать уже собранные заявки
                    empty += 1
                    continue

                self._repo.delete_applications_by_program(code)
                self._repo.add_applicants_bulk(
                    [a.applicant_id for a in applications], university=SPBGU,
                )
                self._repo.add_applications_bulk(applications)
                self._repo.add_submission_stats(stats)
                ok += 1

            if ok == 0:
                self._repo._session.rollback()
                raise RuntimeError(
                    f"Ни по одной программе не получено заявок (пусто: {empty} "
                    f"из {len(discovered)}). Данные оставлены без изменений. "
                    f"Диагностика: python scripts/diagnose_spbgu.py"
                )

            self._repo.commit()
            logger.info(
                "✅ Синхронизация СПбГУ завершена. С заявками: %d, пусто: %d, программ в каталоге: %d",
                ok, empty, len(seen_codes),
            )
        except SQLAlchemyError as db_err:
            logger.exception("Ошибка транзакции, выполняем rollback: %s", db_err)
            self._repo._session.rollback()
            raise

    def execute_parallel(self, parallelism: int = 4, headless: bool = True, university: str = SPBGU) -> None:
        logger.info(
            "=== Синхронизация списков заявок начинается (параллельно, N=%d, вуз=%s) ===",
            parallelism, university,
        )
        try:
            programs = self._repo.get_programs_by_university(university)
            codes = [p.code for p in programs]
            if not codes:
                logger.info("Нет направлений для обработки — выходим")
                return

            # парсим во множестве процессов, один ChromeDriver на процесс
            results = parse_programs_in_parallel(
                program_codes=codes,
                parallelism=parallelism,
                headless=headless,
                university=university,
            )

            # сохраняем в исходном порядке (на случай зависимостей логики)
            ok, skipped, empty = 0, 0, 0
            for prog in programs:
                code = prog.code
                if code not in results:
                    logger.warning("✕ Пропускаем %s: результата нет (ошибка парсинга в воркере?)", code)
                    skipped += 1
                    continue

                stats, applications = results[code]

                # Пустой список — это почти всегда сбой источника, а не «все
                # забрали документы». Раньше мы в этом случае удаляли старые
                # заявки и не добавляли ничего: один такой прогон обнулял
                # базу, по которой уже был посчитан Monte-Carlo. Лучше
                # сохранить прошлые данные — они устарели, но они есть.
                if not applications:
                    logger.warning(
                        "✕ Пропускаем %s: источник вернул пустой список, прежние заявки сохраняем", code
                    )
                    empty += 1
                    continue

                # 1) удаляем старые заявки
                self._repo.delete_applications_by_program(code)

                # 2) добавляем (bulk)
                applicant_ids = [a.applicant_id for a in applications]
                self._repo.add_applicants_bulk(applicant_ids, university=university)
                self._repo.add_applications_bulk(applications)

                # 3) статистика
                self._repo.add_submission_stats(stats)
                ok += 1

            # Ни одной программы с данными — это не «обновление без изменений»,
            # а сломанный источник. Откатываемся, чтобы следом не запустился
            # пересчёт и публикация пустого снапшота.
            if ok == 0:
                self._repo._session.rollback()
                raise RuntimeError(
                    f"Ни по одной программе не получено заявок "
                    f"(пусто: {empty}, без результата: {skipped}). "
                    f"Данные в базе оставлены без изменений. "
                    f"Проверьте источник: python scripts/diagnose_spbgu.py"
                )

            # один общий commit
            self._repo.commit()
            logger.info(
                "✅ Параллельная синхронизация завершена. Успешно: %d, пусто: %d, пропущено: %d",
                ok, empty, skipped,
            )

        except SQLAlchemyError as db_err:
            logger.exception("Ошибка транзакции, выполняем rollback: %s", db_err)
            self._repo._session.rollback()
            raise
