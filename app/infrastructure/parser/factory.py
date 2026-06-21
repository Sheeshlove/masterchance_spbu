# app/infrastructure/parser/factory.py
from __future__ import annotations

from app.infrastructure.parser.base import (
    SPBGU,
    SPBPU,
    SUPPORTED_UNIVERSITIES,
    IApplicationsParser,
)


def create_parser(university: str = SPBPU, headless: bool = True) -> IApplicationsParser:
    """
    Фабрика парсеров по ключу вуза.

    Импорты ленивые: тяжёлые selenium-зависимые модули подтягиваются только
    тогда, когда реально нужен соответствующий парсер. Это позволяет
    импортировать фабрику и оркестрацию в окружениях без selenium.
    """
    key = (university or SPBPU).strip().lower()

    if key == SPBPU:
        from app.infrastructure.parser.master_applications_parser import (
            MasterApplicationsParser,
        )
        return MasterApplicationsParser(headless=headless)

    if key == SPBGU:
        from app.infrastructure.parser.spbgu.spbgu_master_parser import (
            SpbguMasterApplicationsParser,
        )
        return SpbguMasterApplicationsParser(headless=headless)

    raise ValueError(
        f"Неизвестный вуз '{university}'. Поддерживаются: {SUPPORTED_UNIVERSITIES}"
    )
