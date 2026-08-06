# app/infrastructure/parser/spbgu/spbgu_master_parser.py
"""
Парсер рейтинговых списков магистратуры СПбГУ (отчёт PriemList02).

Формат данных (снят с реального отчёта enrollelists.spbu.ru):
  1. GET страницы отчёта → встроенный JSON `#priem-list-02-report-meta`
     ({id, report_upload_id, sections[].specialities[]{id, code, name, ...}}),
     а также дата формирования в `#datetime` (напр. «05 августа 2026 г. 16:00»).
  2. Данные абитуриентов по одной специальности приходят запросом
       POST /api/reports/priem-list-02/data
       body: {"report_priem_list_02_id": <id>,
              "speciality_ids": [<speciality uuid>],
              "filters": {... fin_source_name:"Бюджет", is_foreign:"0" ...}}
     Ответ: {"blocks": [{"html": "<...>", ...}]}. В block.html:
       • инфо-таблица (форма, направление, программа, ВИ, «Количество
         бюджетных мест» → num_places);
       • таблица данных с колонками:
           № | Уникальный код поступающего | Сумма конкурсных баллов |
           Сумма баллов за ВИ | Балл за ВИ №1[, №2] |
           Сумма баллов за общие ИД | Согласие (Да/Нет) | Приоритет | Статус.

Наш program_code для СПбГУ = f"spbgu:{speciality_uuid}" (см. spbgu_programs.py).
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.domain.models import Application, SubmissionStats
from app.infrastructure.http import urlopen
from app.infrastructure.parser.base import IApplicationsParser
from app.infrastructure.parser.spbgu.spbgu_programs import (
    _USER_AGENT,
    extract_report_meta,
    fetch_report_html,
)

_DATA_API = "https://enrollelists.spbu.ru/api/reports/priem-list-02/data"
_MSK = ZoneInfo("Europe/Moscow")

_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


# ── дата формирования отчёта (#datetime) ───────────────────────────────────
def parse_report_datetime(html: str) -> Optional[datetime]:
    """«05 августа 2026 г. 16:00» из #datetime → tz-naive МСК datetime, либо None."""
    m = re.search(r'id="datetime".*?<span[^>]*>(.*?)</span>', html, re.S)
    if not m:
        return None
    text = re.sub(r"\s+", " ", unescape(m.group(1))).strip()
    mm = re.search(r"(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{4}).*?(\d{1,2}):(\d{2})", text)
    if not mm:
        return None
    day, mon, year, hh, mi = mm.groups()
    month = _RU_MONTHS.get(mon.lower())
    if not month:
        return None
    return datetime(int(year), month, int(day), int(hh), int(mi))


# ── разбор block.html (чистые функции, тестируются офлайн) ──────────────────
class _BlockParser(HTMLParser):
    """
    Вытаскивает из block.html:
      info_rows  — строки инфо-таблицы (первой): [label, value, ...];
      headers    — заголовки таблицы данных (второй, из <thead>);
      data_rows  — строки таблицы данных (из <tbody>).
    """

    def __init__(self) -> None:
        super().__init__()
        self._table_idx = 0
        self._cur_table = 0
        self._in_thead = False
        self._in_tbody = False
        self._in_cell = False
        self._cell: list[str] = []
        self._row: list[str] = []
        self.info_rows: list[list[str]] = []
        self.headers: list[str] = []
        self.data_rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table_idx += 1
            self._cur_table = self._table_idx
        elif tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_tbody = True
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "thead":
            self._in_thead = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag in ("td", "th"):
            if self._in_cell:
                self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
                self._in_cell = False
        elif tag == "tr":
            if self._cur_table == 1:
                if self._row:
                    self.info_rows.append(self._row)
            elif self._cur_table == 2:
                if self._in_thead:
                    self.headers = self._row
                elif self._in_tbody and self._row:
                    self.data_rows.append(self._row)
            self._row = []

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def _to_int(s: Optional[str], default: int = 0) -> int:
    m = re.search(r"-?\d+", s or "")
    return int(m.group()) if m else default


def parse_block_info(block_html: str) -> dict:
    """
    Разобрать «шапку» блока (инфо-таблицу) в словарь.

    Нужна на этапе сидинга: в reportMeta лежит только название НАПРАВЛЕНИЯ
    («Прикладная математика и информатика»), одинаковое у нескольких программ,
    а настоящее имя образовательной программы есть только здесь.

    Возвращает ключи (те, что нашлись):
        education_form, speciality_code, speciality_name, program_name,
        exams, num_places
    """
    p = _BlockParser()
    p.feed(block_html)

    out: dict = {}
    for row in p.info_rows:
        if len(row) < 2:
            continue
        label = row[0].rstrip(":").strip().lower()
        value = row[1].strip()
        if not value:
            continue
        if "форма обучения" in label:
            out["education_form"] = value
        elif "направление подготовки" in label:
            # «45.04.01 Филология» → код + название
            parts = value.split(None, 1)
            if parts and re.match(r"^\d{2}\.\d{2}\.\d{2}$", parts[0]):
                out["speciality_code"] = parts[0]
                if len(parts) > 1:
                    out["speciality_name"] = parts[1].strip()
            else:
                out["speciality_name"] = value
        elif "образовательная программа" in label:
            out["program_name"] = value
        elif "вступительные испытания" in label:
            out["exams"] = value
        elif "количество" in label and "мест" in label:
            out["num_places"] = _to_int(value)
    return out


def _num_places(info_rows: list[list[str]]) -> int:
    for row in info_rows:
        if row and "количество бюджетных мест" in row[0].lower():
            if len(row) > 1:
                return _to_int(row[1], 0)
    return 0


def _field_for_header(header: str) -> Optional[str]:
    n = re.sub(r"\s+", " ", header.lower()).strip()
    if "уникальный код" in n:
        return "applicant_id"
    if "сумма конкурсных баллов" in n:
        return "total_score"
    if "сумма баллов за вступительные" in n:
        return "vi_score"
    if n.startswith("балл за ви"):
        m = re.search(r"№\s*(\d+)", n)
        idx = int(m.group(1)) if m else 1
        return f"subject{idx}_score" if idx in (1, 2) else None
    if "индивидуальны" in n and "достижени" in n:
        return "id_achievements"
    if "согласие" in n:
        return "consent"
    if n.startswith("приоритет"):
        return "priority"
    if "статус" in n:
        return "review_status"
    return None  # столбец «№» и прочее игнорируем


def block_to_records(
    block_html: str, program_code: str, generated_at: datetime
) -> Tuple[SubmissionStats, List[Application]]:
    """block.html → (SubmissionStats, [Application]). Маппинг по заголовкам колонок."""
    p = _BlockParser()
    p.feed(block_html)

    fields = [_field_for_header(h) for h in p.headers]
    apps: List[Application] = []
    for row in p.data_rows:
        rec = {f: v for f, v in zip(fields, row) if f}
        applicant_id = (rec.get("applicant_id") or "").strip()
        if not applicant_id:
            continue
        apps.append(
            Application(
                program_code=program_code,
                applicant_id=applicant_id,
                total_score=_to_int(rec.get("total_score")),
                vi_score=_to_int(rec.get("vi_score")),
                subject1_score=_to_int(rec.get("subject1_score")),
                subject2_score=_to_int(rec.get("subject2_score")),
                id_achievements=_to_int(rec.get("id_achievements")),
                target_id_achievements=0,  # у СПбГУ нет целевых ИД
                priority=_to_int(rec.get("priority")),
                consent=(rec.get("consent") or "").strip().lower() == "да",
                review_status=(rec.get("review_status") or "").strip(),
            )
        )

    stats = SubmissionStats(
        program_code=program_code,
        num_places=_num_places(p.info_rows),
        num_applications=len(apps),
        generated_at=generated_at,
    )
    return stats, apps


class SpbguMasterApplicationsParser(IApplicationsParser):
    """
    Парсер одного рейтингового списка магистратуры СПбГУ.

    Кэширует reportMeta (id, report_upload_id) и дату формирования — они общие
    для всех специальностей одного отчёта, чтобы не перезапрашивать страницу на
    каждую программу.
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless  # для совместимости интерфейса (Selenium не нужен)
        self._meta: dict | None = None
        self._generated_at: datetime | None = None

    # ── reportMeta + дата формирования (ленивая загрузка, общая на отчёт) ───
    def _ensure_report(self) -> dict:
        if self._meta is None:
            html = fetch_report_html()
            self._meta = extract_report_meta(html)
            self._generated_at = parse_report_datetime(html) or datetime.now(_MSK).replace(tzinfo=None)
        return self._meta

    def _current_filters(self) -> dict:
        meta = self._ensure_report()
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
        meta = self._ensure_report()
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
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return list(data.get("blocks") or [])

    def fetch_program_info(self, speciality_id: str, timeout: int = 60) -> dict:
        """
        Шапка блока по одной специальности: настоящее имя образовательной
        программы, форма обучения, КЦП. Используется сидингом каталога
        (seed_spbgu_programs.py) — в reportMeta этих полей нет.
        """
        blocks = self._fetch_speciality_blocks(speciality_id, timeout=timeout)
        html = "".join(b.get("html", "") for b in blocks if isinstance(b, dict))
        return parse_block_info(html) if html else {}

    def parse(self, program_code: str) -> Tuple[SubmissionStats, List[Application]]:
        self._ensure_report()
        speciality_id = program_code.split("spbgu:", 1)[-1]
        blocks = self._fetch_speciality_blocks(speciality_id)
        html = "".join(b.get("html", "") for b in blocks if isinstance(b, dict))
        return block_to_records(html, program_code, self._generated_at)

    def close(self) -> None:
        self._meta = None
        self._generated_at = None
