#!/usr/bin/env python3
"""
Сборка снапшота БД для десктоп-клиента.

Монте-Карло считается по всей когорте на сервере (run_monte_carlo.py), а
десктоп-клиент работает с готовым результатом. Этот скрипт делает
консистентную копию master.db (через sqlite backup API — безопасно даже если
рядом идёт запись), сжимает её VACUUM'ом и упаковывает в gzip.

Полученный master-snapshot.db.gz публикуется туда, куда смотрит
settings.snapshot_url (по умолчанию — GitHub Releases).

Запуск:
    python build_snapshot.py                       # data/master.db → dist/
    python build_snapshot.py --out dist/snap.db.gz
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import tempfile
from pathlib import Path

from app.config.config import settings

# Таблицы, которые реально читает клиент (см. GetApplicantForecastUseCase).
_REQUIRED_TABLES = [
    "programs",
    "applications",
    "applicants",
    "submission_stats",
    "program_quantiles",
    "admission_probabilities",
    "admission_diagnostics",
    "exam_sessions",
]


def _source_path() -> Path:
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"Снапшот поддерживается только для SQLite, а DATABASE_URL={url}")
    return Path(url[len("sqlite:///"):])


def build(source: Path, out: Path) -> None:
    if not source.exists():
        raise SystemExit(f"Исходная БД не найдена: {source}")

    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "snapshot.db"

        # 1) консистентная копия «на лету»
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(staged)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()

        # 2) проверка наличия таблиц + счётчики
        con = sqlite3.connect(staged)
        try:
            have = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            missing = [t for t in _REQUIRED_TABLES if t not in have]
            if missing:
                raise SystemExit(
                    "В БД нет таблиц, нужных клиенту: " + ", ".join(missing) +
                    ". Сначала выполните update_lists.py и run_monte_carlo.py."
                )
            counts = {
                t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in _REQUIRED_TABLES
            }
            if counts["admission_probabilities"] == 0:
                raise SystemExit(
                    "admission_probabilities пуста — снапшот бесполезен. "
                    "Запустите run_monte_carlo.py."
                )
            # Рабочая база живёт в WAL (см. app/infrastructure/db/engine.py), и
            # backup() переносит этот режим в копию. Снапшоту он не нужен и
            # вреден: клиент его только читает, а рядом с файлом появлялись бы
            # -wal и -shm, без которых скопированная база уже неполна. Возвращаем
            # обычный журнал — так снапшот остаётся одним самодостаточным файлом.
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("VACUUM")
        finally:
            con.close()

        raw_mb = staged.stat().st_size / 1024 / 1024

        # 3) gzip
        with open(staged, "rb") as fsrc, gzip.open(out, "wb", compresslevel=9) as fdst:
            shutil.copyfileobj(fsrc, fdst)

    gz_mb = out.stat().st_size / 1024 / 1024
    print(f"✅ Снапшот собран: {out}")
    print(f"   размер: {raw_mb:.1f} МБ → {gz_mb:.1f} МБ (gzip)")
    for table, n in counts.items():
        print(f"   {table:26} {n:>8}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Собрать снапшот БД для десктоп-клиента.")
    ap.add_argument("--source", type=Path, default=None, help="путь к master.db (по умолчанию из конфига)")
    ap.add_argument("--out", type=Path, default=Path("dist/master-snapshot.db.gz"), help="куда положить .db.gz")
    args = ap.parse_args()

    build(args.source or _source_path(), args.out)


if __name__ == "__main__":
    main()
