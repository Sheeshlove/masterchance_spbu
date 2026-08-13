# app/infrastructure/parser/runner.py
"""
Параллельный обход списков: один источник на процесс.

Списков у шести вузов — тысячи, и почти всё время уходит на ожидание ответа
сервера. Разбор идёт в нескольких процессах (а не потоках) по той же причине,
что и раньше: каждый источник держит своё состояние (meta отчёта, сессию), и
делить его между потоками нельзя.

Между процессами ездят словари, а не доменные объекты: pickle доменных
датаклассов работает, но требует, чтобы обе стороны знали одни и те же
классы, — а это лишняя связность ради нулевой выгоды.
"""
from __future__ import annotations

import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from typing import Iterable, List, Tuple

from app.config.logger import logger
from app.domain.models import Application, SubmissionStats
from app.domain.universities import SPBGU
from app.infrastructure.parser.base import ParsedProgram, ProgramListing
from app.infrastructure.parser.factory import create_source


# ── сериализация ───────────────────────────────────────────────────────────
def _to_dict(obj):
    """Безопасная сериализация для передачи между процессами."""
    if hasattr(obj, "model_dump"):      # pydantic v2
        return obj.model_dump()
    if hasattr(obj, "dict"):            # pydantic v1
        return obj.dict()
    if is_dataclass(obj):
        return asdict(obj)
    return obj.__dict__


def serialize_program(program: ParsedProgram) -> dict:
    return {
        "program_code": program.program_code,
        "program_name": program.program_name,
        "speciality_code": program.speciality_code,
        "education_form": program.education_form,
        "is_international": program.is_international,
        "stats": _to_dict(program.stats),
        "applications": [_to_dict(a) for a in program.applications],
    }


def deserialize_program(record: dict) -> Tuple[SubmissionStats, List[Application]]:
    """Запись из воркера → доменные объекты (stats, applications)."""
    return (
        SubmissionStats(**record["stats"]),
        [Application(**a) for a in record["applications"]],
    )


def _chunkify(seq: List[str], n_chunks: int) -> List[List[str]]:
    n = max(1, n_chunks)
    size = max(1, math.ceil(len(seq) / n))
    return [seq[i: i + size] for i in range(0, len(seq), size)]


# ── воркер ─────────────────────────────────────────────────────────────────
def _worker_fetch(university: str, refs: List[Tuple[str, str]]) -> List[dict]:
    """Разобрать пачку списков одного вуза. Ошибка на одном не роняет пачку."""
    source = create_source(university)
    out: List[dict] = []
    try:
        for ref, title in refs:
            try:
                programs = source.fetch(ProgramListing(ref=ref, title=title))
            except Exception as exc:  # noqa: BLE001 — источник за пределами нашего контроля
                logger.exception("[PID %s][%s] Ошибка разбора %s: %s",
                                 os.getpid(), university, ref, exc)
                continue
            if not programs:
                logger.debug("[PID %s][%s] Пусто: %s", os.getpid(), university, ref)
                continue
            out.extend(serialize_program(p) for p in programs)
        return out
    finally:
        try:
            source.close()
        except Exception:  # noqa: BLE001
            pass


def fetch_listings_in_parallel(
    university: str,
    listings: Iterable[ProgramListing],
    parallelism: int = 4,
) -> List[dict]:
    """
    Разобрать все найденные списки вуза. Возвращает сериализованные ParsedProgram.

    Порядок результатов не гарантируется — он никому и не нужен: дальше записи
    кладутся в базу по своему стабильному коду.
    """
    refs = [(item.ref, item.title) for item in listings]
    if not refs:
        return []

    n = max(1, int(parallelism))
    if n == 1 or len(refs) == 1:
        return _worker_fetch(university, refs)

    chunks = _chunkify(refs, n)
    logger.info("[%s] Разбор списков: %d процессов, %d списков",
                university, len(chunks), len(refs))
    started = time.perf_counter()

    aggregated: List[dict] = []
    with ProcessPoolExecutor(max_workers=len(chunks)) as pool:
        futures = [pool.submit(_worker_fetch, university, chunk) for chunk in chunks]
        for future in as_completed(futures):
            aggregated.extend(future.result())

    logger.info("[%s] Разбор завершён за %.2f сек. Программ с данными: %d из %d списков",
                university, time.perf_counter() - started, len(aggregated), len(refs))
    return aggregated


def discover_listings(university: str = SPBGU) -> List[ProgramListing]:
    """Найти списки вуза (в основном процессе — обход оглавления дешёвый)."""
    source = create_source(university)
    try:
        return source.discover()
    finally:
        source.close()
