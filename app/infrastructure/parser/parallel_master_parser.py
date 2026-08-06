# app/infrastructure/parser/parallel_master_parser.py
from __future__ import annotations

import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from typing import Dict, Iterable, List, Tuple

from app.config.logger import logger
from app.domain.models import SubmissionStats, Application
from app.infrastructure.parser.base import SPBGU
from app.infrastructure.parser.factory import create_parser


# --- utils ------------------------------------------------------------------

def _to_dict(obj):
    """Безопасная сериализация для передачи между процессами."""
    # pydantic v2
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    # pydantic v1
    if hasattr(obj, "dict"):
        return obj.dict()
    if is_dataclass(obj):
        return asdict(obj)
    # простой объект
    return obj.__dict__


def _from_stats(d: dict) -> SubmissionStats:
    return SubmissionStats(**d)


def _from_app(d: dict) -> Application:
    return Application(**d)


def _chunkify(seq: List[str], n_chunks: int) -> List[List[str]]:
    n = max(1, n_chunks)
    size = max(1, math.ceil(len(seq) / n))
    return [seq[i : i + size] for i in range(0, len(seq), size)]


# --- worker -----------------------------------------------------------------

def _worker_parse_chunk(
    codes: List[str], headless: bool = True, university: str = SPBGU
) -> Dict[str, Tuple[dict, List[dict]]]:
    """
    Процесс-воркер: один ChromeDriver на чанк.
    Возвращает сериализованные dict'и, чтобы не споткнуться о pickle.
    """
    # необязательно, но помогает диагностировать
    os.environ.setdefault("WDM_LOG", "0")

    parser = create_parser(university=university, headless=headless)
    out: Dict[str, Tuple[dict, List[dict]]] = {}

    try:
        for code in codes:
            try:
                logger.info("[PID %s] → парсим %s", os.getpid(), code)
                stats, apps = parser.parse(code)
                out[code] = (_to_dict(stats), [_to_dict(a) for a in apps])
            except Exception as e:
                logger.exception("[PID %s] Ошибка парсинга %s: %s", os.getpid(), code, e)
                # возвращаем пустые, чтобы основной процесс мог решить судьбу записи
                out[code] = (None, [])
        return out
    finally:
        try:
            parser.close()
        except Exception:
            pass


# --- проход по специальностям текущего отчёта (стабильные коды) ---------------

def _worker_parse_specialities(speciality_ids: List[str], headless: bool = True) -> List[dict]:
    """
    Воркер: разбирает специальности текущей выгрузки отчёта.

    Возвращает сериализованные SpecialityResult. Стабильный код программы
    вычисляется внутри разбора — из шапки блока, которую мы и так скачиваем.
    """
    parser = create_parser(university=SPBGU, headless=headless)
    out: List[dict] = []
    try:
        for sp_id in speciality_ids:
            try:
                res = parser.parse_speciality(sp_id)
            except Exception as e:
                logger.exception("[PID %s] Ошибка разбора специальности %s: %s", os.getpid(), sp_id, e)
                continue
            if res is None:
                logger.warning("[PID %s] Пустой блок по специальности %s", os.getpid(), sp_id)
                continue
            out.append({
                "program_code": res.program_code,
                "program_name": res.program_name,
                "speciality_code": res.speciality_code,
                "education_form": res.education_form,
                "is_international": res.is_international,
                "stats": _to_dict(res.stats),
                "applications": [_to_dict(a) for a in res.applications],
            })
        return out
    finally:
        try:
            parser.close()
        except Exception:
            pass


def parse_specialities_in_parallel(
    speciality_ids: Iterable[str],
    parallelism: int = 4,
    headless: bool = True,
) -> List[dict]:
    """
    Разобрать специальности текущего отчёта в несколько процессов.

    На вход идут UUID из свежей выгрузки, на выход — записи со СТАБИЛЬНЫМ кодом
    программы. Каталог благодаря этому обновляется тем же проходом, и протухание
    сохранённых кодов больше не возможно.
    """
    ids = list(speciality_ids)
    if not ids:
        return []

    n = max(1, int(parallelism))
    if n == 1 or len(ids) == 1:
        return _worker_parse_specialities(ids, headless)

    chunks = _chunkify(ids, n)
    logger.info("Разбор специальностей: %d процессов, %d специальностей", len(chunks), len(ids))
    started = time.perf_counter()

    aggregated: List[dict] = []
    with ProcessPoolExecutor(max_workers=len(chunks)) as pool:
        futures = [pool.submit(_worker_parse_specialities, chunk, headless) for chunk in chunks]
        for fut in as_completed(futures):
            aggregated.extend(fut.result())

    logger.info(
        "Разбор завершён за %.2f сек. С данными: %d из %d",
        time.perf_counter() - started, len(aggregated), len(ids),
    )
    return aggregated


def deserialize_speciality(d: dict) -> Tuple[SubmissionStats, List[Application]]:
    """Обратно в доменные объекты (stats, applications)."""
    return _from_stats(d["stats"]), [_from_app(a) for a in d["applications"]]


# --- public API --------------------------------------------------------------

def parse_programs_in_parallel(
    program_codes: Iterable[str],
    parallelism: int = 4,
    headless: bool = True,
    university: str = SPBGU,
) -> Dict[str, Tuple[SubmissionStats, List[Application]]]:
    """
    Запускает N независимых ChromeDriver в отдельных процессах.
    Каждый процесс переиспользует драйвер для своего чанка направлений.
    Возвращает {code: (stats, applications)}.

    Рекомендации соблюдены:
      • один WebDriver — один процесс;
      • никакого шеринга драйвера между потоками/процессами;
      • корректное завершение драйвера в finally;
      • if __name__ == '__main__' должен быть в вызывающем скрипте (Windows).
    """
    codes = list(program_codes)
    if not codes:
        return {}

    n = max(1, int(parallelism))
    if n == 1 or len(codes) == 1:
        # однопоточный бэкап — без накладных расходов на процессы
        parser = create_parser(university=university, headless=headless)
        try:
            result: Dict[str, Tuple[SubmissionStats, List[Application]]] = {}
            for code in codes:
                stats, apps = parser.parse(code)
                result[code] = (stats, apps)
            return result
        finally:
            parser.close()

    chunks = _chunkify(codes, n)
    logger.info("Стартуем параллельный парсинг: %d процессов, %d направлений", len(chunks), len(codes))

    aggregated: Dict[str, Tuple[SubmissionStats, List[Application]]] = {}
    started = time.perf_counter()

    # ВАЖНО: на Windows обязательна защита if __name__ == '__main__' в вызывающем коде!
    with ProcessPoolExecutor(max_workers=len(chunks)) as pool:
        futures = [pool.submit(_worker_parse_chunk, chunk, headless, university) for chunk in chunks]
        for fut in as_completed(futures):
            data = fut.result()  # dict[code] -> (stats_dict|None, apps_dicts)
            for code, (s_dict, a_dicts) in data.items():
                if s_dict is None:
                    # передаём как исключение вверх? здесь помечаем как пропущенное
                    logger.warning("Направление %s пропущено из-за ошибки парсинга в воркере.", code)
                    continue
                stats = _from_stats(s_dict)
                apps = [_from_app(d) for d in a_dicts]
                aggregated[code] = (stats, apps)

    elapsed = time.perf_counter() - started
    logger.info("Параллельный парсинг завершён за %.2f сек. Успешно: %d/%d", elapsed, len(aggregated), len(codes))
    return aggregated
