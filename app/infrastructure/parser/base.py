# app/infrastructure/parser/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Tuple

from app.domain.models import Application, SubmissionStats

# Ключи вузов (используются в конфиге, БД-колонке `university` и фабрике парсеров).
SPBPU = "spbpu"  # СПбПУ (Политех) — исходный источник
SPBGU = "spbgu"  # СПбГУ — добавляемый второй источник

SUPPORTED_UNIVERSITIES = (SPBPU, SPBGU)


class IApplicationsParser(ABC):
    """
    Единый контракт парсера списков поступающих (магистратура).

    Реализации:
      • MasterApplicationsParser           — СПбПУ (my.spbstu.ru)
      • SpbguMasterApplicationsParser       — СПбГУ (cabinet.spbu.ru)

    Парсер инкапсулирует свой WebDriver/HTTP-клиент и обязан корректно
    освобождать ресурсы в close(). Один экземпляр — один драйвер
    (см. parse_programs_in_parallel: один драйвер на процесс).
    """

    @abstractmethod
    def parse(self, program_code: str) -> Tuple[SubmissionStats, List[Application]]:
        """
        Скачать и распарсить один рейтинговый список.

        Возвращает (SubmissionStats, [Application, ...]).
        program_code — наш внутренний код программы (для СПбГУ может быть
        неймспейснут, см. фабрику discover_programs).
        """
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Освободить ресурсы (закрыть WebDriver)."""
        raise NotImplementedError
