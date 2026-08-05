# app/infrastructure/parser/factory.py
from __future__ import annotations

from app.infrastructure.parser.base import (
    SPBGU,
    SUPPORTED_UNIVERSITIES,
    IApplicationsParser,
)


def create_parser(university: str = SPBGU, headless: bool = True) -> IApplicationsParser:
    """
    Фабрика парсеров по ключу вуза.

    Импорт ленивый: нужен для parse_programs_in_parallel, который создаёт
    парсер уже внутри рабочего процесса.

    `headless` сохранён ради совместимости вызовов — парсер СПбГУ работает по
    обычному HTTP, браузер ему не нужен.
    """
    key = (university or SPBGU).strip().lower()

    if key == SPBGU:
        from app.infrastructure.parser.spbgu.spbgu_master_parser import (
            SpbguMasterApplicationsParser,
        )
        return SpbguMasterApplicationsParser(headless=headless)

    raise ValueError(
        f"Неизвестный вуз '{university}'. Поддерживается: {SUPPORTED_UNIVERSITIES}"
    )
