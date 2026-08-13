"""
Тесты build_snapshot.py — сборки снапшота БД для десктоп-клиента.

Важно не только «файл появился», но и что скрипт ОТКАЗЫВАЕТСЯ собирать
заведомо бесполезный снапшот (нет таблиц / не посчитан Monte-Carlo):
клиент на таком снапшоте молча показывал бы пустоту.
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3

import pytest

from build_snapshot import _REQUIRED_TABLES, build

_SCHEMA = {
    "programs": "code TEXT, name TEXT, department_code TEXT, is_international INT, university TEXT",
    "applications": "program_code TEXT, applicant_id TEXT, priority INT",
    "applicants": "id TEXT, university TEXT",
    "submission_stats": "program_code TEXT, num_places INT, num_applications INT, generated_at TEXT",
    "program_quantiles": "program_code TEXT, q90 REAL, q95 REAL",
    "admission_probabilities": "applicant_id TEXT, program_code TEXT, probability REAL",
    "admission_diagnostics": "applicant_id TEXT, p_excluded REAL, p_fail_when_included REAL",
    "exam_sessions": "id INTEGER PRIMARY KEY, program_code TEXT, dt TEXT",
}


def _make_db(path, tables=None, with_probabilities=True):
    con = sqlite3.connect(path)
    for name in (tables if tables is not None else _REQUIRED_TABLES):
        con.execute(f"CREATE TABLE {name} ({_SCHEMA[name]})")
    if with_probabilities and "admission_probabilities" in (tables or _REQUIRED_TABLES):
        con.execute("INSERT INTO admission_probabilities VALUES ('A1', '701', 0.42)")
    con.commit()
    con.close()


def test_builds_valid_gzipped_snapshot(tmp_path, capsys):
    source = tmp_path / "master.db"
    out = tmp_path / "out" / "snap.db.gz"
    _make_db(source)

    build(source, out)

    assert out.exists()
    # распаковывается и содержит данные
    unpacked = tmp_path / "snap.db"
    with gzip.open(out, "rb") as src, open(unpacked, "wb") as dst:
        shutil.copyfileobj(src, dst)
    con = sqlite3.connect(unpacked)
    assert con.execute("SELECT probability FROM admission_probabilities").fetchone()[0] == 0.42
    con.close()

    # в отчёте видно, что именно уехало клиенту
    assert "admission_probabilities" in capsys.readouterr().out


def test_creates_output_directory(tmp_path):
    source = tmp_path / "master.db"
    _make_db(source)
    out = tmp_path / "нет" / "такого" / "каталога" / "snap.db.gz"

    build(source, out)

    assert out.exists()


def test_refuses_when_source_missing(tmp_path):
    with pytest.raises(SystemExit, match="не найдена"):
        build(tmp_path / "нет.db", tmp_path / "snap.db.gz")


def test_refuses_when_tables_missing(tmp_path):
    source = tmp_path / "master.db"
    _make_db(source, tables=["programs", "applications"])

    with pytest.raises(SystemExit) as exc:
        build(source, tmp_path / "snap.db.gz")
    assert "admission_probabilities" in str(exc.value)


def test_refuses_when_monte_carlo_not_computed(tmp_path):
    """Все таблицы на месте, но вероятностей нет — снапшот бесполезен."""
    source = tmp_path / "master.db"
    _make_db(source, with_probabilities=False)

    with pytest.raises(SystemExit, match="run_monte_carlo"):
        build(source, tmp_path / "snap.db.gz")


def test_source_database_is_not_modified(tmp_path):
    source = tmp_path / "master.db"
    _make_db(source)
    before = source.read_bytes()

    build(source, tmp_path / "snap.db.gz")

    assert source.read_bytes() == before


def test_snapshot_is_a_single_self_contained_file(tmp_path):
    """
    Рабочая база переведена в WAL, а backup() переносит режим в копию. Для
    снапшота это вредно: клиент его только читает, а рядом с файлом появлялись
    бы -wal и -shm, без которых скопированная база уже неполна.
    """
    source = tmp_path / "master.db"
    _make_db(source)
    con = sqlite3.connect(source)
    con.execute("PRAGMA journal_mode=WAL")   # как в бою
    con.close()

    out = tmp_path / "snap.db.gz"
    build(source, out)

    restored = tmp_path / "restored.db"
    with gzip.open(out, "rb") as src, open(restored, "wb") as dst:
        shutil.copyfileobj(src, dst)

    con = sqlite3.connect(restored)
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()
    assert mode.lower() != "wal", "снапшот уехал клиенту в режиме WAL"
