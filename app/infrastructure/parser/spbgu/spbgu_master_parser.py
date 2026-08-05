# app/infrastructure/parser/spbgu/spbgu_master_parser.py
"""
Парсер рейтинговых списков магистратуры СПбГУ (отчёт PriemList02).

Формат данных (снят с реального отчёта enrollelists.spbu.ru):
  1. GET страницы отчёта → встроенный JSON `#priem-list-02-report-meta`
     ({id, report_upload_id, sections[].specialities[]{id, code, name, ...}}).
  2. Данные абитуриентов по одной специальности приходят запросом
       POST /api/reports/priem-list-02/data
       Content-Type: application/json, X-Requested-With: XMLHttpRequest
       body: {"report_priem_list_02_id": <id>,
              "speciality_ids": [<speciality uuid>],
              "filters": {education_level_sort_order, report_upload_id,
                          fin_source_name:"Бюджет", is_foreign:"0", ...}}
     Ответ: {"blocks": [{"html": "<...строки абитуриентов...>", ...}]}.
     То есть строки приходят готовым HTML-фрагментом (block.html), который и
     нужно распарсить в Application и вытащить КЦП (num_places) / время
     формирования (generated_at).

Наш program_code для СПбГУ = f"spbgu:{speciality_uuid}" (см. spbgu_programs.py),
поэтому parse(program_code) отрезает префикс, находит текущий report id и
POST-ит один speciality_id.

СТАТУС: механика запроса реализована; разбор block.html (schema строки
абитуриента) ждёт образца ответа API — до этого `_parse_speciality_html`
осознанно бросает NotImplementedError, чтобы пайплайн не выдавал выдуманные
данные за настоящие.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import List, Tuple

from app.domain.models import Application, SubmissionStats
from app.infrastructure.parser.base import IApplicationsParser
from app.infrastructure.parser.spbgu.spbgu_programs import (
    _USER_AGENT,
    extract_report_meta,
    fetch_report_html,
)

_DATA_API = "https://enrollelists.spbu.ru/api/reports/priem-list-02/data"


class SpbguMasterApplicationsParser(IApplicationsParser):
    """
    Парсер одного рейтингового списка магистратуры СПбГУ.

    Один экземпляр кэширует reportMeta (id, report_upload_id) — он общий для
    всех специальностей одного отчёта, чтобы не перезапрашивать страницу на
    каждую программу.
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless  # для совместимости интерфейса (Selenium не нужен)
        self._meta: dict | None = None

    # ── reportMeta (ленивая загрузка, общая на отчёт) ──────────────────────
    def _report_meta(self) -> dict:
        if self._meta is None:
            self._meta = extract_report_meta(fetch_report_html())
        return self._meta

    def _current_filters(self) -> dict:
        meta = self._report_meta()
        return {
            "education_level_sort_order": "2",
            "report_upload_id": str(meta.get("report_upload_id") or ""),
            "faculty_name": "",
            "program_name": "",
            "speciality": "",
            "applicant_code": "",
            "education_form_name": "",
            "fin_source_name": "Бюджет",
            "contract_status": "",
            "consent_status": "",
            "priority": "",
            "status": "",
            "is_foreign": "0",
        }

    def _fetch_speciality_blocks(self, speciality_id: str, timeout: int = 60) -> list[dict]:
        """POST /api/reports/priem-list-02/data → список blocks с html-фрагментами."""
        meta = self._report_meta()
        payload = {
            "report_priem_list_02_id": str(meta.get("id") or ""),
            "speciality_ids": [speciality_id],
            "filters": self._current_filters(),
        }
        req = urllib.request.Request(
            _DATA_API,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return list(data.get("blocks") or [])

    # ── разбор строки абитуриента (ЖДЁТ ОБРАЗЦА block.html) ─────────────────
    @staticmethod
    def _parse_speciality_html(block_html: str, program_code: str) -> Tuple[SubmissionStats, List[Application]]:
        raise NotImplementedError(
            "Разбор block.html СПбГУ не реализован: нужен образец ответа "
            "POST /api/reports/priem-list-02/data (одной специальности), чтобы "
            "снять колонки строки абитуриента, КЦП и время формирования."
        )

    def parse(self, program_code: str) -> Tuple[SubmissionStats, List[Application]]:
        speciality_id = program_code.split("spbgu:", 1)[-1]
        blocks = self._fetch_speciality_blocks(speciality_id)
        html = "".join(b.get("html", "") for b in blocks if isinstance(b, dict))
        return self._parse_speciality_html(html, program_code)

    def close(self) -> None:
        self._meta = None
