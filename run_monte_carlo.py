#!/usr/bin/env python3
from sqlalchemy.orm import sessionmaker

from app.application.use_cases.recalculate_monte_carlo import RecalculateMonteCarloUseCase
from app.config.config import settings
from app.config.logger import logger
from app.infrastructure.db.engine import analyze, ensure_indexes, make_engine
from app.infrastructure.db.models import Base
from app.infrastructure.db.repositories.program_repository import ProgramRepository


def main() -> None:
    logger.info("=== Полный пересчёт Monte‑Carlo ===")

    engine = make_engine(settings.database_url, echo=settings.db_echo)
    Base.metadata.create_all(engine)  # just in case
    ensure_indexes(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()

    try:
        repo = ProgramRepository(session)
        use_case = RecalculateMonteCarloUseCase(repo=repo, n_simulations=10_000)
        use_case.execute()
        logger.info("✅ Monte‑Carlo успешно пересчитан.")
        # Данные только что переписаны целиком — статистика планировщика
        # устарела вся сразу. Без неё SQLite выбирает план вслепую.
        analyze(engine)
    except Exception as exc:
        logger.exception("❌ Ошибка Monte‑Carlo: %s", exc)
    finally:
        session.close()
        logger.info("Сессия БД закрыта.")


if __name__ == "__main__":
    main()
