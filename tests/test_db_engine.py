"""
Настройки подключения к SQLite и индексы под запросы пользователя.

Ломается это тихо и дорого: без WAL сайт и бот молча стоят всё время, пока
обновлятор пишет, а без индекса каждый показ прогноза читает таблицу заявок
целиком. И то и другое не видно ни по логам, ни по тестам логики — только по
секундам ожидания у живого человека. Поэтому проверяем прямо здесь.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from app.infrastructure.db.engine import analyze, ensure_indexes, make_engine
from app.infrastructure.db.models import Base


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'x.db'}"


# ── PRAGMA ───────────────────────────────────────────────────────────────────

def test_engine_switches_the_database_to_wal(db_url):
    """
    Главная правка всего этапа. В режиме по умолчанию (delete) пишущий запирает
    файл целиком, и читатели ждут его столько, сколько идёт запись.
    """
    engine = make_engine(db_url)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"


def test_engine_waits_longer_than_the_default_five_seconds(db_url):
    """
    pysqlite ждёт освобождения базы 5 секунд и затем бросает
    `database is locked`. Для страницы это 500, для бота — «Произошла ошибка».
    """
    engine = make_engine(db_url)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() >= 15_000


def test_every_new_connection_is_tuned_not_just_the_first(db_url):
    """
    journal_mode записан в самом файле, а busy_timeout — свойство соединения.
    Значит, настраивать нужно каждое, иначе второй запрос снова ждёт 5 секунд.
    """
    engine = make_engine(db_url)
    for _ in range(3):
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() >= 15_000
        engine.dispose()  # следующий connect() поднимет соединение заново


# ── Индексы ──────────────────────────────────────────────────────────────────

def _plan(engine, sql: str, *params) -> str:
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " | ".join(r[-1] for r in rows)


def test_lookup_by_applicant_stops_scanning_the_whole_table(db_url):
    """
    Первичный ключ applications — (program_code, applicant_id), и для выборки
    по одному лишь applicant_id он не годится: колонка в нём вторая.
    """
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)

    ensure_indexes(engine)

    plan = _plan(engine, "SELECT * FROM applications WHERE applicant_id = ?", "1000042")
    assert "SCAN" not in plan, f"таблица всё ещё читается целиком: {plan}"
    assert "ix_applications_applicant" in plan


def test_consent_lookup_uses_a_covering_index(db_url):
    """
    Справочник «кто подал согласие хоть где-то» строится на КАЖДЫЙ показ
    прогноза, хотя от введённого кода не зависит вовсе.
    """
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    ensure_indexes(engine)

    plan = _plan(engine, "SELECT DISTINCT applicant_id FROM applications WHERE consent = 1")
    assert "SCAN applications" not in plan, f"скан по согласиям остался: {plan}"
    assert "ix_applications_consent" in plan


def test_ensure_indexes_can_be_called_twice(db_url):
    """Зовётся на каждом старте контейнера — падать со второго раза нельзя."""
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)

    ensure_indexes(engine)
    ensure_indexes(engine)  # не должно бросить


def test_indexes_appear_on_a_database_that_already_exists(db_url):
    """
    Ради этого случая ensure_indexes и написан: боевая база создана давно,
    create_all() к существующей таблице индексы не добавляет, а alembic в
    docker-compose не участвует.
    """
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_applications_applicant"))
        conn.execute(text("DROP INDEX IF EXISTS ix_applications_consent"))

    ensure_indexes(engine)

    with engine.connect() as conn:
        names = {
            r[0] for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert {"ix_applications_applicant", "ix_applications_consent"} <= names


def test_fresh_database_gets_the_indexes_from_the_models_alone(db_url):
    """Те же индексы объявлены в models.py — на чистой базе хватает create_all."""
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        names = {
            r[0] for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert {"ix_applications_applicant", "ix_applications_consent"} <= names


def test_analyze_fills_the_planner_statistics(db_url):
    """Без sqlite_stat1 планировщик выбирает план вслепую."""
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    ensure_indexes(engine)

    analyze(engine)

    with engine.connect() as conn:
        assert conn.exec_driver_sql(
            "SELECT count(*) FROM sqlite_master WHERE name='sqlite_stat1'"
        ).scalar() == 1


# ── Собственно то, ради чего всё затевалось ──────────────────────────────────

def test_reader_is_not_blocked_while_another_process_writes(tmp_path):
    """
    Самое дорогое место всего сервиса, проверенное поведением, а не настройкой.

    Открываем два независимых соединения (как обновлятор и сайт — разные
    процессы), одно начинает запись и её не завершает. В режиме по умолчанию
    второе не смогло бы прочитать ничего; в WAL читает как ни в чём не бывало.
    """
    db = tmp_path / "shared.db"
    engine = make_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    ensure_indexes(engine)
    engine.dispose()

    writer = sqlite3.connect(db, timeout=1)
    reader = sqlite3.connect(db, timeout=1)
    try:
        # busy_timeout читателю ставим маленький: если WAL не включён, тест
        # должен упасть быстро, а не ждать пятнадцать секунд.
        reader.execute("PRAGMA busy_timeout=1000")

        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO applications VALUES "
            "('p1','a1',10,10,10,0,0,0,1,1,'ok')"
        )

        rows = reader.execute(
            "SELECT * FROM applications WHERE applicant_id = ?", ("a1",)
        ).fetchall()
        assert rows == [], "читатель увидел незавершённую транзакцию"
    finally:
        writer.rollback()
        writer.close()
        reader.close()
