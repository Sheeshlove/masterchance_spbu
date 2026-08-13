# app/infrastructure/parser/factory.py
from __future__ import annotations

from app.domain.universities import (
    SPBGU,
    SUPPORTED_UNIVERSITIES,
    normalize_university,
)
from app.infrastructure.parser.base import IApplicationsParser, IUniversitySource


def create_source(university: str = SPBGU) -> IUniversitySource:
    """
    Фабрика источников по ключу вуза.

    Импорты ленивые: источник создаётся уже внутри рабочего процесса
    (см. runner.py), и тянуть в родительский процесс всё сразу незачем.

    СПбГУ разбирается своим парсером — у отчёта PriemList02 есть API, которое
    отдаёт данные точнее и дешевле, чем обход страниц. Остальные пять идут
    через общий движок открытых списков.
    """
    key = normalize_university(university) or SPBGU

    if key == SPBGU:
        from app.infrastructure.parser.spbgu.spbgu_master_parser import SpbguSource

        return SpbguSource()

    if key in SUPPORTED_UNIVERSITIES:
        from dataclasses import replace

        from app.config.config import settings
        from app.infrastructure.parser.openlists.source import OpenListsSource
        from app.infrastructure.parser.openlists.specs import default_spec

        spec = replace(
            default_spec(key, settings.lists_url(key)),
            max_lists=settings.max_lists_per_source,
        )
        if not spec.index_urls or not spec.index_urls[0]:
            raise ValueError(
                f"Для вуза '{key}' не задан адрес списков. "
                f"Укажите {key.upper()}_LISTS_URL в .env"
            )
        return OpenListsSource(spec)

    raise ValueError(
        f"Неизвестный вуз '{university}'. Поддерживается: {SUPPORTED_UNIVERSITIES}"
    )


def create_parser(university: str = SPBGU, headless: bool = True) -> IApplicationsParser:
    """
    Фабрика парсеров одного списка по нашему коду программы.

    Нужна режимам обновления «по каталогу из базы» (execute / execute_parallel).
    `headless` сохранён ради совместимости вызовов — браузер не используется
    ни одним источником.
    """
    key = normalize_university(university) or SPBGU

    if key == SPBGU:
        from app.infrastructure.parser.spbgu.spbgu_master_parser import (
            SpbguMasterApplicationsParser,
        )
        return SpbguMasterApplicationsParser(headless=headless)

    raise ValueError(
        f"Разбор по коду программы поддержан только для СПбГУ; "
        f"для '{university}' используйте create_source()."
    )
