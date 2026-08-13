#!/usr/bin/env python3
import sys

from sqlalchemy.orm import sessionmaker

from app.application.use_cases.recalculate_monte_carlo import RecalculateMonteCarloUseCase
from app.application.use_cases.update_lists import UpdateApplicationListsUseCase
from app.config.config import settings
from app.config.logger import logger
from app.domain.universities import (
    SUPPORTED_UNIVERSITIES,
    label,
    parse_university_list,
)
from app.infrastructure.db.engine import analyze, ensure_indexes, make_engine
from app.infrastructure.db.models import Base
from app.infrastructure.db.repositories.program_repository import ProgramRepository


def main():
    # Какие вузы обновлять: из конфига (UNIVERSITIES), с возможностью
    # переопределить аргументом --university=hse,itmo или --university=all.
    universities = settings.enabled_universities
    # Monte-Carlo считается по всей базе сразу (отдельными прогонами на вуз),
    # поэтому он идёт один раз в конце: --no-monte-carlo пропускает пересчёт
    # здесь, а run_monte_carlo.py вызывают отдельно.
    run_mc = True
    for arg in sys.argv[1:]:
        if arg.startswith("--university="):
            universities = parse_university_list(arg.split("=", 1)[1])
        elif arg == "--no-monte-carlo":
            run_mc = False

    if not universities:
        print("❌ Не задан ни один известный вуз. Проверьте UNIVERSITIES в .env "
              f"или --university=. Поддерживаются: {', '.join(SUPPORTED_UNIVERSITIES)}",
              file=sys.stderr)
        sys.exit(2)

    logger.info("=== masterchance старт (вузы=%s, monte-carlo=%s) ===",
                ",".join(universities), run_mc)
    # 1) Настройка БД
    engine = make_engine(settings.database_url, echo=settings.db_echo)
    Base.metadata.create_all(engine)
    ensure_indexes(engine)
    Session = sessionmaker(bind=engine, future=True)

    # 2) Инициализация
    session = Session()
    repo = ProgramRepository(session)
    updater = UpdateApplicationListsUseCase(repo=repo)  # parser не нужен для параллельного режима

    # 3) Запуск: по вузу за раз, сбой одного не отменяет остальные
    report = updater.execute_all(universities, parallelism=settings.parser_parallelism)
    for uni, outcome in report.items():
        mark = "✅" if not outcome.startswith("ошибка") else "❌"
        print(f"{mark} {label(uni)}: {outcome}")

    if all(outcome.startswith("ошибка") for outcome in report.values()):
        logger.error("Ни один источник не обновился — прогноз не пересчитываем.")
        print("❌ Ни один вуз не обновился, данные оставлены как были.", file=sys.stderr)
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
