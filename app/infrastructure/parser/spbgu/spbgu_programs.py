# app/infrastructure/parser/spbgu/spbgu_programs.py
from __future__ import annotations

from typing import List, TypedDict


class DiscoveredProgram(TypedDict):
    """
    Описание программы магистратуры СПбГУ, полученное на этапе discovery.

    code              — наш внутренний код программы (неймспейснутый, напр.
                        f"spbgu:{<id списка>}"), служит PK в таблице programs.
    name              — название программы.
    department_code   — синтетический код кафедры/группы для MC (exam_id).
    is_international   — флаг международной программы (для MC exam_id).
    list_ref          — навигационная ссылка/параметр конкретного списка
                        (URL или id), по которому парсер откроет рейтинг.
    """

    code: str
    name: str
    department_code: str
    is_international: bool
    list_ref: str


def discover_programs(headless: bool = True) -> List[DiscoveredProgram]:
    """
    Обнаружить список программ магистратуры СПбГУ.

    Каркас: реализуется после Selenium-разведки (Фаза 0). Аналог
    scripts/parse_programs.py для СПбПУ. Из текущего окружения сеть к
    cabinet.spbu.ru закрыта, поэтому навигация и селекторы пока не сняты.
    """
    raise NotImplementedError(
        "discover_programs для СПбГУ ещё не реализован: нужна Selenium-разведка "
        "формата cabinet.spbu.ru (Фаза 0)."
    )
