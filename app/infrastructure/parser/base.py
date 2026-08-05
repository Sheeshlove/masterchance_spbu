# app/infrastructure/parser/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Tuple

from app.domain.models import Application, SubmissionStats

# Ключ вуза: используется в конфиге, в колонке `university` и в фабрике парсеров.
SPBGU = "spbgu"  # СПбГУ — единственный поддерживаемый источник

SUPPORTED_UNIVERSITIES = (SPBGU,)


class IApplicationsParser(ABC):
    """
    Контракт парсера списков поступающих (магистратура).

    Реализация: SpbguMasterApplicationsParser (enrollelists.spbu.ru).

    Парсер инкапсулирует свой HTTP-клиент и обязан освобождать ресурсы в
    close(). Один экземпляр — один клиент (см. parse_programs_in_parallel:
    по парсеру на процесс).
    """

    @abstractmethod
    def parse(self, program_code: str) -> Tuple[SubmissionStats, List[Application]]:
        """
        Скачать и распарсить один рейтинговый список.

        Возвращает (SubmissionStats, [Application, ...]).
        program_code — наш внутренний код программы (`spbgu:<uuid специальности>`,
        см. spbgu_programs.discover_programs).
        """
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Освободить ресурсы."""
        raise NotImplementedError
