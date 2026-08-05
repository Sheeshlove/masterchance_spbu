#!/usr/bin/env python3
"""
Сидинг каталога программ магистратуры СПбГУ.

`update_lists.py` берёт список программ ИЗ БАЗЫ (get_programs_by_university),
поэтому перед первым запуском с `--university=spbgu` каталог нужно наполнить.
Этот скрипт делает именно это:

    reportMeta отчёта PriemList02  →  institutes / departments / programs

Про имена. В reportMeta у каждой специальности лежит название НАПРАВЛЕНИЯ
(«Прикладная математика и информатика»), одинаковое сразу у нескольких
образовательных программ. Настоящее имя программы («Славянские языки и
литературы …») есть только в шапке её списка, поэтому по умолчанию скрипт
дочитывает шапку каждой программы (по одному запросу). С `--fast` этот шаг
пропускается — тогда названия будут неразличимы.

Про коды. Коды направлений (01.04.02 и т.п.) — федеральные и совпадают у
разных вузов, а таблицы institutes/departments общие. Поэтому для СПбГУ они
неймспейсятся префиксом `spbgu:`: иначе два вуза делили бы один
department_code, который служит exam_id в Монте-Карло, и статистика баллов за
РАЗНЫЕ экзамены смешалась бы в одну. Пользователю префикс не показывается.

Запуск:
    python seed_spbgu_programs.py             # с настоящими именами программ
    python seed_spbgu_programs.py --fast      # быстро, имена = названия направлений
    python seed_spbgu_programs.py --limit 5   # попробовать на пяти программах
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config.config import settings
from app.config.logger import logger
from app.domain.models import Department, Institute, Program
from app.infrastructure.db.models import Base
from app.infrastructure.db.repositories.program_repository import ProgramRepository
from app.infrastructure.parser.spbgu.spbgu_master_parser import SpbguMasterApplicationsParser
from app.infrastructure.parser.spbgu.spbgu_programs import discover_programs

SPBGU = "spbgu"
NS = f"{SPBGU}:"  # префикс для кодов кафедр/институтов


def namespaced_department(speciality_code: str) -> str:
    """'01.04.02' → 'spbgu:01.04.02' (см. пояснение про exam_id в докстринге)."""
    return f"{NS}{speciality_code}"


def namespaced_institute(speciality_code: str) -> str:
    """'01.04.02' → 'spbgu:01' — укрупнённая группа направлений."""
    return f"{NS}{speciality_code.split('.')[0]}"


def seed_catalog(discovered: list, repo: ProgramRepository, parser=None) -> dict:
    """
    Записать найденные программы в каталог (institutes / departments / programs).

    parser=None — не дочитывать настоящие названия программ.
    Возвращает счётчики для отчёта. Коммит остаётся за вызывающим.
    """
    seen_institutes: set[str] = set()
    seen_departments: set[str] = set()
    named = fallback = 0

    for i, prog in enumerate(discovered, start=1):
        speciality_code = prog["department_code"]
        dep_code = namespaced_department(speciality_code)
        inst_code = namespaced_institute(speciality_code)

        # 1) институт (укрупнённая группа) и кафедра (направление)
        if inst_code not in seen_institutes:
            repo.add_institute(Institute(
                code=inst_code,
                name=f"Группа направлений {speciality_code.split('.')[0]}",
            ))
            seen_institutes.add(inst_code)
        if dep_code not in seen_departments:
            repo.add_department(Department(
                code=dep_code, name=prog["name"], institute_code=inst_code,
            ))
            seen_departments.add(dep_code)

        # 2) настоящее имя образовательной программы
        name = prog["name"]
        if parser is not None:
            try:
                info = parser.fetch_program_info(prog["list_ref"])
                if info.get("program_name"):
                    name = info["program_name"]
                    named += 1
                else:
                    fallback += 1
            except Exception as exc:      # одна неудача не должна ронять весь сидинг
                logger.warning("Не удалось прочитать имя программы %s: %s", prog["code"], exc)
                fallback += 1

        repo.add_program(Program(
            code=prog["code"],
            name=name,
            department_code=dep_code,
            is_ino=False,
            is_international=prog["is_international"],
            university=SPBGU,
        ))

        if i % 25 == 0 or i == len(discovered):
            logger.info("… обработано %d / %d", i, len(discovered))

    return {
        "programs": len(discovered),
        "departments": len(seen_departments),
        "institutes": len(seen_institutes),
        "named": named,
        "fallback": fallback,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Наполнить каталог программ СПбГУ.")
    ap.add_argument("--fast", action="store_true",
                    help="не дочитывать настоящие названия программ (быстрее, но названия дублируются)")
    ap.add_argument("--limit", type=int, default=None, help="обработать только первые N программ")
    args = ap.parse_args()

    logger.info("=== Сидинг каталога СПбГУ ===")

    try:
        discovered = discover_programs()
    except Exception as exc:
        logger.exception("Не удалось получить список программ: %s", exc)
        print(f"❌ Не удалось открыть отчёт СПбГУ: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.limit:
        discovered = discovered[: args.limit]
    if not discovered:
        print("❌ Отчёт не вернул ни одной программы — сидить нечего.", file=sys.stderr)
        sys.exit(1)

    logger.info("Найдено программ: %d", len(discovered))

    engine = create_engine(settings.database_url, echo=settings.db_echo, future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    repo = ProgramRepository(session)

    parser = None if args.fast else SpbguMasterApplicationsParser()

    try:
        counts = seed_catalog(discovered, repo, parser)
        repo.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("Ошибка сидинга: %s", exc)
        print(f"❌ Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        if parser is not None:
            parser.close()
        session.close()

    print("✅ Каталог СПбГУ заполнен.")
    print(f"   программ:    {counts['programs']}")
    print(f"   направлений: {counts['departments']}")
    print(f"   групп:       {counts['institutes']}")
    if parser is not None:
        print(f"   настоящих названий получено: {counts['named']}, "
              f"осталось названием направления: {counts['fallback']}")
    print("\nТеперь можно запускать:  python update_lists.py --university=spbgu")


if __name__ == "__main__":
    main()
