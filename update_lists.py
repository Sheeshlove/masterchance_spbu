#!/usr/bin/env python3
import sys

from sqlalchemy.orm import sessionmaker

from app.application.use_cases.recalculate_monte_carlo import RecalculateMonteCarloUseCase
from app.application.use_cases.update_lists import UpdateApplicationListsUseCase
from app.config.config import settings
from app.config.logger import logger
from app.infrastructure.db.engine import analyze, ensure_indexes, make_engine
from app.infrastructure.db.models import Base
from app.infrastructure.db.repositories.program_repository import ProgramRepository


def main():
    # вуз-источник: из конфига (UNIVERSITY), с возможностью переопределить CLI-аргументом
    university = settings.university
    # Monte-Carlo считается по ВСЕЙ базе сразу, поэтому при обновлении нескольких
    # вузов подряд его гоняют один раз в конце: --no-monte-carlo пропускает
    # пересчёт здесь, а run_monte_carlo.py вызывают отдельно.
    run_mc = True
    for arg in sys.argv[1:]:
        if arg.startswith("--university="):
            university = arg.split("=", 1)[1].strip().lower()
        elif arg == "--no-monte-carlo":
            run_mc = False
    logger.info("=== masterchance старт (вуз=%s, monte-carlo=%s) ===", university, run_mc)
    # 1) Настройка БД
    engine = make_engine(settings.database_url, echo=settings.db_echo)
    Base.metadata.create_all(engine)
    ensure_indexes(engine)
    Session = sessionmaker(bind=engine, future=True)

    # 2) Инициализация
    session = Session()
    repo = ProgramRepository(session)
    updater = UpdateApplicationListsUseCase(repo=repo)  # parser не нужен для параллельного режима

    # 3) Запуск
    try:
        parallelism = settings.parser_parallelism
        updater.execute_spbgu(parallelism=parallelism)
        logger.info("Данные по подаче заявлений успешно обновлены.")
        print("✅ Данные по подаче заявлений успешно обновлены.")
    except Exception as e:
        logger.exception("Ошибка при обновлении данных")
        print("❌ Ошибка при обновлении:", e, file=sys.stderr)
        session.close()
        logger.info("Сессия БД закрыта.")
        sys.exit(1)

    logger.info("=== masterchance завершён ===")

    if not run_mc:
        analyze(engine)
        session.close()
        logger.info("Monte‑Carlo пропущен (--no-monte-carlo). Сессия БД закрыта.")
        print("ℹ️  Monte‑Carlo пропущен — запустите run_monte_carlo.py отдельно.")
        return

    try:
        repo = ProgramRepository(session)
        use_case = RecalculateMonteCarloUseCase(repo=repo, n_simulations=10_000)
        use_case.execute()
        logger.info("✅ Monte‑Carlo успешно пересчитан.")
        analyze(engine)
    except Exception as exc:
        logger.exception("❌ Ошибка Monte‑Carlo: %s", exc)
        sys.exit(1)
    finally:
        session.close()
        logger.info("Сессия БД закрыта.")


if __name__ == "__main__":
    main()
