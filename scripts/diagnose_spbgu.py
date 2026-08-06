#!/usr/bin/env python3
"""
Диагностика источника данных СПбГУ.

Отвечает на вопрос «почему списки приходят пустыми»: открывает отчёт,
показывает, сколько в нём специальностей, и разбирает одну целиком — с
названием программы, нашим стабильным кодом и числом заявок.

Запуск на сервере:
    docker run --rm --env-file .env -v "$PWD/data:/app/data" \
        masterchance:local scripts/diagnose_spbgu.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config.config import settings  # noqa: E402
from app.infrastructure.db.models import Base  # noqa: E402
from app.infrastructure.db.repositories.program_repository import ProgramRepository  # noqa: E402
from app.infrastructure.parser.spbgu.spbgu_master_parser import (  # noqa: E402
    SpbguMasterApplicationsParser,
)
from app.infrastructure.parser.spbgu.spbgu_programs import (  # noqa: E402
    extract_report_meta,
    fetch_report_html,
    parse_report_meta,
)

SPBGU = "spbgu"


def main() -> int:
    print("=" * 68)
    print("ДИАГНОСТИКА ИСТОЧНИКА СПбГУ")
    print("=" * 68)

    # ── 1. отчёт ───────────────────────────────────────────────────────────
    print("\n1. Читаем отчёт…")
    try:
        html = fetch_report_html()
        meta = extract_report_meta(html)
        current = parse_report_meta(html)
    except Exception as exc:
        print(f"   ❌ Отчёт недоступен: {type(exc).__name__}: {exc}")
        return 1

    print(f"   id выгрузки:       {meta.get('report_upload_id')}")
    print(f"   специальностей:    {len(current)}")
    if not current:
        print("   ❌ Отчёт пуст — списки ещё не опубликованы.")
        return 1

    # ── 2. что уже лежит в базе ────────────────────────────────────────────
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    repo = ProgramRepository(session)
    stored = repo.get_programs_by_university(SPBGU)
    print(f"\n2. В базе программ: {len(stored)}")
    legacy = [p for p in stored if len(p.code.split(":")) == 2]
    if legacy:
        print(f"   ⚠ {len(legacy)} программ со старым кодом-UUID — они осядут мусором.")
        print("     Проще всего начать с чистой базы: rm data/master.db")

    # ── 3. живая проверка одной специальности ──────────────────────────────────
    probe = current[0]["list_ref"]
    print(f"\n3. Пробный разбор специальности {probe}…")
    parser = SpbguMasterApplicationsParser()
    try:
        res = parser.parse_speciality(probe)
        if res is None:
            print("   ❌ Ответ пустой — списки не опубликованы или изменился формат запроса.")
            return 1
        print(f"   программа:  {res.program_name}")
        print(f"   направление: {res.speciality_code}, форма: {res.education_form}")
        print(f"   наш код:    {res.program_code}   (стабильный, без UUID выгрузки)")
        print(f"   мест: {res.stats.num_places}, заявок: {len(res.applications)}")
        if res.applications:
            a = res.applications[0]
            print(f"   пример: код={a.applicant_id}, балл={a.total_score}, приоритет={a.priority}")
            print("\n✅ Источник отвечает нормально. Можно запускать обновление.")
            return 0
        print("   ⚠ Блок пришёл, но строк в нём нет.")
        return 1
    except Exception as exc:
        print(f"   ❌ Ошибка запроса: {type(exc).__name__}: {exc}")
        return 1
    finally:
        parser.close()
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
