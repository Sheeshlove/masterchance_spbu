#!/usr/bin/env python3
"""
Диагностика источника списков: что вуз отдаёт прямо сейчас.

Отвечает на вопрос «почему по этому вузу пусто»: открывает страницу приёма,
показывает, сколько ссылок на списки нашлось, забирает первую и печатает, какие
колонки в ней узнались. Если раздел переехал или колонки названы непривычно,
это видно сразу — и чинится адресом в .env или синонимом в
app/infrastructure/parser/openlists/columns.py.

Запуск:
    python scripts/diagnose_source.py hse
    python scripts/diagnose_source.py msu --url=https://cpk.msu.ru/rating/2027

На сервере:
    docker run --rm --env-file .env -v "$PWD/data:/app/data" \
        masterchance:local scripts/diagnose_source.py hse
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.universities import (  # noqa: E402
    SPBGU,
    SUPPORTED_UNIVERSITIES,
    label,
    normalize_university,
)
from app.infrastructure.parser.base import ProgramListing  # noqa: E402
from app.infrastructure.parser.factory import create_source  # noqa: E402

_LIMIT = 12  # сколько найденных списков показывать


def _usage() -> int:
    print(f"Укажите вуз: {', '.join(SUPPORTED_UNIVERSITIES)}")
    print("Пример: python scripts/diagnose_source.py hse [--url=https://…]")
    return 2


def _describe_columns(program) -> None:
    """Что удалось вытащить из первой строки — по ней видно, всё ли на месте."""
    if not program.applications:
        print("   ⚠ Таблица найдена, но строк в ней нет.")
        return
    row = program.applications[0]
    print(f"   мест: {program.stats.num_places}, заявок: {len(program.applications)}")
    print(f"   пример строки: код={row.applicant_id}, конкурсный балл={row.total_score}, "
          f"ВИ={row.vi_score}, ИД={row.id_achievements}, приоритет={row.priority}, "
          f"согласие={'да' if row.consent else 'нет'}")
    if not row.total_score:
        print("   ⚠ Конкурсный балл нулевой — возможно, колонка называется непривычно.")
    if program.stats.num_places == 0:
        print("   ⚠ Число мест не найдено: шанс по этой программе посчитать будет не на чем.")


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    url = next((a.split("=", 1)[1] for a in argv[1:] if a.startswith("--url=")), None)
    if not args:
        return _usage()

    university = normalize_university(args[0])
    if university not in SUPPORTED_UNIVERSITIES:
        print(f"❌ Неизвестный вуз '{args[0]}'.")
        return _usage()

    print("=" * 68)
    print(f"ДИАГНОСТИКА ИСТОЧНИКА: {label(university)}")
    print("=" * 68)

    if url:
        # Разовая проверка чужого адреса без правки .env.
        from dataclasses import replace

        from app.infrastructure.parser.openlists.source import OpenListsSource
        from app.infrastructure.parser.openlists.specs import default_spec

        source = OpenListsSource(replace(default_spec(university), index_urls=(url,)))
        print(f"Адрес (из аргумента): {url}")
    else:
        try:
            source = create_source(university)
        except ValueError as exc:
            print(f"❌ {exc}")
            return 1
        if university != SPBGU:
            print(f"Адрес: {', '.join(source.spec.index_urls)}")
            if source.spec.note:
                print(f"Замечание: {source.spec.note}")

    try:
        # ── 1. что нашлось ─────────────────────────────────────────────────
        print("\n1. Ищем списки…")
        listings = source.discover()
        print(f"   найдено: {len(listings)}")
        if not listings:
            print("   ❌ Ни одного списка. Возможные причины:")
            print("      • раздел приёма переехал — задайте "
                  f"{university.upper()}_LISTS_URL в .env;")
            print("      • списки ещё не опубликованы;")
            print("      • страница собирается скриптом в браузере — тогда нужен адрес "
                  "запроса к API вуза (вкладка Network в инструментах разработчика).")
            return 1
        for item in listings[:_LIMIT]:
            print(f"      • {item.title or '—'}  →  {item.ref}")
        if len(listings) > _LIMIT:
            print(f"      … и ещё {len(listings) - _LIMIT}")

        # ── 2. пробный разбор ──────────────────────────────────────────────
        print("\n2. Пробуем разобрать первый список…")
        programs = source.fetch(ProgramListing(ref=listings[0].ref, title=listings[0].title))
        if not programs:
            print("   ❌ Ни одной программы. Скорее всего, страница — оглавление, а не "
                  "список, либо колонки названы непривычно (см. openlists/columns.py).")
            return 1

        for program in programs:
            print(f"\n   программа:   {program.program_name}")
            print(f"   направление: {program.speciality_code}, форма: {program.education_form or '—'}")
            print(f"   наш код:     {program.program_code}")
            print(f"   список от:   {program.stats.generated_at:%d.%m.%Y %H:%M}")
            _describe_columns(program)

        if any(p.applications for p in programs):
            print("\n✅ Источник отвечает нормально. Можно запускать обновление:")
            print(f"   python update_lists.py --university={university}")
            return 0
        return 1
    except Exception as exc:  # noqa: BLE001 — диагностике важно показать любую ошибку
        print(f"   ❌ Ошибка: {type(exc).__name__}: {exc}")
        return 1
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
