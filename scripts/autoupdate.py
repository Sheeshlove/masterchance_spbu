#!/usr/bin/env python3
"""
Бесконечный цикл обновления данных. Запускается ВНУТРИ контейнера.

Один проход:
    1. списки + каталог  (UpdateApplicationListsUseCase.execute_all по всем вузам)
    2. Monte-Carlo       (RecalculateMonteCarloUseCase)
    3. снапшот           (build_snapshot)
    4. публикация        (scripts/publish_snapshot.sh, если задан GITHUB_TOKEN)

Первый проход идёт сразу, дальше — раз в UPDATE_INTERVAL_HOURS (по умолчанию 3).

Почему циклом, а не cron: контейнер с `--restart unless-stopped` переживает и
закрытие терминала, и перезагрузку сервера, и его видно одной командой
`docker ps`. Отдельный планировщик для этого не нужен.

Сбой одного прохода не роняет цикл: ошибка пишется в лог, и работа
продолжается со следующего раза. Источник бывает недоступен, и это не повод
останавливать обновления навсегда.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config.config import settings  # noqa: E402
from app.domain.universities import label  # noqa: E402
from app.infrastructure.db.engine import analyze, ensure_indexes, make_engine  # noqa: E402
from app.infrastructure.db.models import Base  # noqa: E402
from app.infrastructure.db.repositories.program_repository import ProgramRepository  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "dist" / "master-snapshot.db.gz"

_stopping = False


def say(msg: str) -> None:
    """Лог в stdout — его видно через `docker logs`."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _handle_stop(signum, _frame) -> None:
    global _stopping
    _stopping = True
    say(f"Получен сигнал {signum} — завершаемся после текущего шага.")


def run_once() -> None:
    """Один полный проход. Бросает исключение, если что-то пошло не так."""
    from app.application.use_cases.recalculate_monte_carlo import RecalculateMonteCarloUseCase
    from app.application.use_cases.update_lists import UpdateApplicationListsUseCase
    from build_snapshot import build

    engine = make_engine(settings.database_url, echo=settings.db_echo)
    Base.metadata.create_all(engine)
    ensure_indexes(engine)
    session = sessionmaker(bind=engine, future=True)()
    repo = ProgramRepository(session)

    try:
        universities = settings.enabled_universities
        say(f"Шаг 1/4: списки и каталог ({', '.join(label(u) for u in universities)})…")
        report = UpdateApplicationListsUseCase(repo=repo).execute_all(
            universities, parallelism=settings.parser_parallelism
        )
        for uni, outcome in report.items():
            say(f"   {label(uni)}: {outcome}")
        if all(outcome.startswith("ошибка") for outcome in report.values()):
            raise RuntimeError("ни один источник не обновился")

        say("Шаг 2/4: Monte-Carlo (10 000 симуляций)…")
        RecalculateMonteCarloUseCase(repo=repo, n_simulations=10_000).execute()

        # Вся база только что переписана, статистика планировщика устарела.
        # Без ANALYZE SQLite строит планы вслепую — см. engine.analyze().
        say("Обновляю статистику планировщика…")
        analyze(engine)
    finally:
        session.close()
        engine.dispose()

    say("Шаг 3/4: собираем снапшот…")
    source = Path(settings.database_url[len("sqlite:///"):])
    build(source, SNAPSHOT)

    if os.environ.get("GITHUB_TOKEN"):
        say("Шаг 4/4: публикуем снапшот…")
        subprocess.run(
            ["bash", str(ROOT / "scripts" / "publish_snapshot.sh"), str(SNAPSHOT)],
            check=True,
        )
    else:
        say("Шаг 4/4 пропущен: не задан GITHUB_TOKEN.")


def interval_seconds() -> float:
    """
    Пауза между проходами.

    Нижняя граница бережёт сервер вуза от слишком частых обходов: сколько бы
    ни попросили в UPDATE_INTERVAL_HOURS, чаще неё не пойдём.
    UPDATE_MIN_INTERVAL_SECONDS существует только ради тестов.
    """
    hours = float(os.environ.get("UPDATE_INTERVAL_HOURS", "3"))
    floor = float(os.environ.get("UPDATE_MIN_INTERVAL_SECONDS", "600"))
    return max(floor, hours * 3600)


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    interval = interval_seconds()
    hours = float(os.environ.get("UPDATE_INTERVAL_HOURS", "3"))
    say(f"Автообновление запущено. Интервал: {hours} ч.")

    while not _stopping:
        started = time.monotonic()
        try:
            run_once()
            say("Проход завершён успешно.")
        except Exception as exc:
            say(f"❌ Проход не удался: {type(exc).__name__}: {exc}")
            say("   Данные оставлены как есть, попробуем в следующий раз.")

        if _stopping:
            break

        spent = time.monotonic() - started
        wait = max(min(60.0, interval), interval - spent)
        say(f"Следующее обновление примерно в "
            f"{(datetime.now() + timedelta(seconds=wait)):%H:%M %d.%m}.")

        # спим короткими кусками, чтобы быстро реагировать на остановку
        deadline = time.monotonic() + wait
        while not _stopping and time.monotonic() < deadline:
            time.sleep(min(5.0, deadline - time.monotonic()))

    say("Остановлено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
