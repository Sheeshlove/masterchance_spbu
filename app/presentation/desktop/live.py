# app/presentation/desktop/live.py
"""
Свежие ЛИЧНЫЕ данные абитуриента (СПбГУ), best-effort.

Отчёт PriemList02 умеет фильтроваться по `applicant_code`, поэтому актуальные
строки конкретного человека можно получить точечно, не выкачивая все 179
списков: GET отчёта с фильтром → в reportMeta остаются только «его»
специальности → POST /api/reports/priem-list-02/data по ним.

Это НЕ пересчёт вероятностей (MC требует всю когорту — он берётся из
снапшота), а обновление фактов: баллы, приоритеты, согласия, статусы.

Всё завёрнуто в best-effort: если сети нет, фильтр не поддержан или ответ
неожиданный — возвращаем None, и клиент показывает данные из снапшота.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from app.domain.models import Application
from app.infrastructure.parser.spbgu.spbgu_master_parser import (
    block_to_records,
    parse_report_datetime,
)
from app.infrastructure.parser.spbgu.spbgu_programs import (
    build_report_url,
    extract_report_meta,
    fetch_report_html,
)

# Если фильтр по коду не сработал, отчёт вернёт ВСЕ специальности. Тянуть их
# с машины пользователя нельзя (это сотни запросов к серверу вуза), поэтому
# при превышении порога отказываемся от live-режима.
MAX_SPECIALITIES = 15


@dataclass
class LiveResult:
    """Свежие заявки абитуриента + время формирования отчёта."""
    applications: List[Application]
    generated_at: Optional[datetime]
    # code → человекочитаемое имя программы из отчёта (для незнакомых снапшоту)
    program_names: dict


def _speciality_ids(meta: dict) -> list[tuple[str, str]]:
    """[(speciality_id, name)] из reportMeta."""
    out: list[tuple[str, str]] = []
    for section in meta.get("sections", []) or []:
        for sp in section.get("specialities", []) or []:
            sp_id = str(sp.get("id") or "").strip()
            if sp_id:
                out.append((sp_id, str(sp.get("name") or "").strip()))
    return out


def fetch_live_applications(applicant_code: str, timeout: int = 45) -> Optional[LiveResult]:
    """
    Забрать свежие строки абитуриента. None — если live-режим недоступен.

    Бросать исключения наружу не хотим: вызывающий UI просто откатится на
    снапшот, поэтому все сетевые/парсерные ошибки гасим здесь.
    """
    code = (applicant_code or "").strip()
    if not code:
        return None

    try:
        html = fetch_report_html(build_report_url(applicant_code=code), timeout=timeout)
        meta = extract_report_meta(html)
        generated_at = parse_report_datetime(html)
        specialities = _speciality_ids(meta)
    except Exception:
        return None

    if not specialities or len(specialities) > MAX_SPECIALITIES:
        # Пусто или фильтр проигнорирован — не устраиваем шторм запросов.
        return None

    # Ленивый импорт: парсер тянет urllib и знает про API отчёта.
    from app.infrastructure.parser.spbgu.spbgu_master_parser import (
        SpbguMasterApplicationsParser,
    )

    parser = SpbguMasterApplicationsParser()
    apps: List[Application] = []
    names: dict = {}
    try:
        # Переиспользуем уже загруженную meta/дату, чтобы не ходить на страницу второй раз.
        parser._meta = meta            # noqa: SLF001 — тот же пакет, осознанное переиспользование
        parser._generated_at = generated_at
        for sp_id, sp_name in specialities:
            program_code = f"spbgu:{sp_id}"
            try:
                blocks = parser._fetch_speciality_blocks(sp_id, timeout=timeout)  # noqa: SLF001
            except Exception:
                continue
            block_html = "".join(b.get("html", "") for b in blocks if isinstance(b, dict))
            if not block_html:
                continue
            _stats, rows = block_to_records(block_html, program_code, generated_at or datetime.now())
            mine = [r for r in rows if r.applicant_id == code]
            if mine:
                apps.extend(mine)
                names[program_code] = sp_name
    except Exception:
        return None
    finally:
        parser.close()

    if not apps:
        return None
    return LiveResult(applications=apps, generated_at=generated_at, program_names=names)
