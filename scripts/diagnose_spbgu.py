#!/usr/bin/env python3
"""
Диагностика источника данных СПбГУ.

Отвечает на вопрос «почему списки приходят пустыми». Главная проверка —
совпадают ли коды программ, сохранённые в базе, с теми, что отдаёт отчёт
СЕЙЧАС. Внутренний код программы у нас — это UUID специальности из отчёта
(`spbgu:<uuid>`), и если вуз перезаливает отчёт с новыми UUID, сохранённый
каталог протухает: запросы уходят по кодам, которых в текущем отчёте уже нет,
и ответ приходит пустым.

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
    block_to_records,
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

    # ── 1. что говорит база ────────────────────────────────────────────────
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    repo = ProgramRepository(session)

    stored = repo.get_programs_by_university(SPBGU)
    stored_ids = {p.code.split("spbgu:", 1)[-1] for p in stored}
    print(f"\n1. В базе программ: {len(stored)}")
    if not stored:
        print("   ⚠ Каталог пуст — выполните seed_spbgu_programs.py (шаг 7).")
        session.close()
        return 1

    # ── 2. что говорит отчёт сейчас ────────────────────────────────────────
    print("\n2. Читаем отчёт…")
    try:
        html = fetch_report_html()
        meta = extract_report_meta(html)
        current = parse_report_meta(html)
    except Exception as exc:
        print(f"   ❌ Отчёт недоступен: {type(exc).__name__}: {exc}")
        session.close()
        return 1

    current_ids = {p["list_ref"] for p in current}
    print(f"   id отчёта:        {meta.get('id')}")
    print(f"   id загрузки:      {meta.get('report_upload_id')}")
    print(f"   программ в отчёте: {len(current)}")

    # ── 3. главное: пересекаются ли коды ───────────────────────────────────
    matched = stored_ids & current_ids
    print(f"\n3. Совпадение кодов: {len(matched)} из {len(stored_ids)}")

    if not matched:
        print("""
   ❌ ПРИЧИНА НАЙДЕНА: ни один сохранённый код не встречается в текущем отчёте.
      Вуз перезалил отчёт, и коды специальностей сменились. Запросы уходят
      по несуществующим кодам — отсюда пустые списки.

      Что делать: пересоздать каталог перед сбором —
          docker run --rm --env-file .env -v "$PWD/data:/app/data" \\
              masterchance:local seed_spbgu_programs.py
      и добавить этот шаг в регулярное обновление.""")
        session.close()
        return 1

    if len(matched) < len(stored_ids):
        print(f"   ⚠ Часть кодов устарела ({len(stored_ids) - len(matched)}) — каталог стоит пересоздать.")

    # ── 4. живая проверка одной программы ──────────────────────────────────
    probe = sorted(matched)[0]
    print(f"\n4. Пробный запрос по программе {probe}…")
    parser = SpbguMasterApplicationsParser()
    try:
        blocks = parser._fetch_speciality_blocks(probe)  # noqa: SLF001
        block_html = "".join(b.get("html", "") for b in blocks if isinstance(b, dict))
        print(f"   блоков получено: {len(blocks)}, размер html: {len(block_html)} символов")
        if not block_html:
            print("   ❌ Ответ пустой при живом коде — изменился формат запроса или фильтры.")
            return 1
        stats, apps = block_to_records(block_html, f"spbgu:{probe}", None)
        print(f"   мест: {stats.num_places}, заявок разобрано: {len(apps)}")
        if apps:
            a = apps[0]
            print(f"   пример строки: код={a.applicant_id}, балл={a.total_score}, приоритет={a.priority}")
            print("\n✅ Источник отвечает нормально. Можно запускать обновление.")
            return 0
        print("   ⚠ Блок пришёл, но строк в нём нет — возможно, списки ещё не опубликованы.")
        return 1
    except Exception as exc:
        print(f"   ❌ Ошибка запроса: {type(exc).__name__}: {exc}")
        return 1
    finally:
        parser.close()
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
