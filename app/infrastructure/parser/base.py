# app/infrastructure/parser/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Tuple

from app.domain.models import Application, SubmissionStats
from app.domain.universities import (  # noqa: F401  — исторические имена импортируют отсюда
    HSE,
    ITMO,
    MGIMO,
    MSU,
    RANEPA,
    SPBGU,
    SUPPORTED_UNIVERSITIES,
)


class IApplicationsParser(ABC):
    """
    Контракт парсера одного рейтингового списка по нашему коду программы.

    Остаётся ради последовательного и «по каталогу из базы» режимов обновления
    (UpdateApplicationListsUseCase.execute / execute_parallel). Основной путь —
    IUniversitySource ниже: он умеет ещё и находить списки сам.

    Парсер инкапсулирует свой HTTP-клиент и обязан освобождать ресурсы в
    close(). Один экземпляр — один клиент (см. parse_programs_in_parallel:
    по парсеру на процесс).
    """

    @abstractmethod
    def parse(self, program_code: str) -> Tuple[SubmissionStats, List[Application]]:
        """Скачать и распарсить один рейтинговый список."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Освободить ресурсы."""
        raise NotImplementedError


@dataclass(frozen=True)
class ProgramListing:
    """
    Ссылка на один опубликованный список, найденная на этапе discovery.

    `ref` — то, чем список забирается: URL страницы у большинства вузов или
    идентификатор специальности у СПбГУ. Парсер знает, что с ним делать;
    остальной код обращается с ним как с непрозрачной строкой.
    """
    ref: str
    title: str = ""


@dataclass
class ParsedProgram:
    """
    Разобранный конкурс: и каталожные поля, и заявки.

    Один список = один конкурс = одна наша программа. На странице вуза их
    может быть несколько (таблица на программу), поэтому fetch() возвращает
    список таких записей, а не одну.
    """
    program_code: str          # наш стабильный код (не идентификатор выгрузки)
    program_name: str
    speciality_code: str       # направление, напр. 45.04.01
    education_form: str
    is_international: bool
    stats: SubmissionStats
    applications: List[Application] = field(default_factory=list)


class IUniversitySource(ABC):
    """
    Источник списков одного вуза: сам находит конкурсы и сам их разбирает.

    Две операции разделены намеренно. discover() выполняется в основном
    процессе один раз, fetch() — в рабочих процессах пачками (см.
    app/infrastructure/parser/runner.py), поэтому источник обязан быть
    сериализуемым по своему ключу вуза, а не по состоянию: всё, что он
    накопил (сессии, meta отчёта), живёт внутри процесса и умирает в close().
    """

    #: Ключ вуза из app.domain.universities.
    university: str = SPBGU

    @abstractmethod
    def discover(self) -> List[ProgramListing]:
        """Найти все опубликованные списки магистратуры этого вуза."""
        raise NotImplementedError

    @abstractmethod
    def fetch(self, listing: ProgramListing) -> List[ParsedProgram]:
        """
        Скачать и разобрать один список.

        Пустой список в ответе — нормальная ситуация (конкурс ещё не
        опубликован или страница оказалась не списком), а не ошибка.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Освободить ресурсы. По умолчанию их нет."""
