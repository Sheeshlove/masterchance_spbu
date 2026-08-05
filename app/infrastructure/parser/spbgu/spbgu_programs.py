# app/infrastructure/parser/spbgu/spbgu_programs.py
"""
Discovery программ магистратуры СПбГУ.

Отчёт «Списки подавших заявление» (`PriemList02.php`) отдаёт server-rendered
HTML со встроенным JSON `<script id="priem-list-02-report-meta">`, в котором
лежит полный справочник программ (специальностей): у каждой — UUID `id`, код
направления `code`, название `name` и число абитуриентов. Отдельный Selenium
не нужен — достаточно обычного GET и разбора этого JSON.

Сами строки абитуриентов приходят другим запросом
(`POST /api/reports/priem-list-02/data`) и парсятся в
`spbgu_master_parser.py`. Здесь — только discovery каталога.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import List, TypedDict

_META_RE = re.compile(
    r'<script[^>]*id="priem-list-02-report-meta"[^>]*>(.*?)</script>',
    re.S,
)

# Фильтры отчёта по умолчанию: магистратура (education_level_sort_order=2),
# бюджет, не иностранцы. Пустые поля отдаём весь список программ.
_DEFAULT_QUERY = {
    "mode": "list",
    "education_level_sort_order": "2",
    "is_foreign": "0",
    "faculty_name": "",
    "speciality": "",
    "program_name": "",
    "education_form_name": "",
    "fin_source_name": "Бюджет",
    "consent_status": "",
    "contract_status": "",
    "priority": "",
    "status": "",
    "applicant_code": "",
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class DiscoveredProgram(TypedDict):
    """
    Описание программы магистратуры СПбГУ, полученное на этапе discovery.

    code              — наш внутренний код программы: f"spbgu:{<uuid списка>}"
                        (коды направлений СПбГУ не уникальны, поэтому PK — UUID).
    name              — название программы.
    department_code   — код направления СПбГУ (напр. '01.04.02'); синтетическая
                        группа для MC (exam_id).
    is_international   — флаг международной программы (для MC exam_id).
    list_ref          — speciality_id (UUID) для POST /api/.../priem-list-02/data.
    """

    code: str
    name: str
    department_code: str
    is_international: bool
    list_ref: str


def build_report_url(base_url: str | None = None) -> str:
    """URL отчёта со всеми бюджетными программами магистратуры."""
    if base_url is None:
        from app.config.config import settings  # ленивый импорт: не тянем pydantic в чистый парс
        base_url = settings.spbgu_base_url
    return f"{base_url}?{urllib.parse.urlencode(_DEFAULT_QUERY)}"


def fetch_report_html(url: str | None = None, timeout: int = 60) -> str:
    """GET страницы отчёта (server-rendered, с встроенным reportMeta JSON)."""
    req = urllib.request.Request(
        url or build_report_url(),
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (доверенный хост СПбГУ)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def extract_report_meta(html: str) -> dict:
    """Достать и распарсить JSON из <script id="priem-list-02-report-meta">."""
    m = _META_RE.search(html)
    if not m:
        raise ValueError("reportMeta JSON не найден в HTML отчёта СПбГУ")
    return json.loads(m.group(1))


def parse_report_meta(html: str) -> List[DiscoveredProgram]:
    """
    Разобрать HTML отчёта в список программ (чистая функция — тестируется офлайн).

    Обходит meta.sections[].specialities[] и маппит каждую специальность в
    DiscoveredProgram. Один и тот же `code` может встречаться у нескольких
    программ — уникальность обеспечивает UUID `id`.
    """
    meta = extract_report_meta(html)
    programs: List[DiscoveredProgram] = []
    seen_ids: set[str] = set()

    for section in meta.get("sections", []) or []:
        for sp in section.get("specialities", []) or []:
            sp_id = str(sp.get("id") or "").strip()
            if not sp_id or sp_id in seen_ids:
                continue
            seen_ids.add(sp_id)
            name = str(sp.get("name") or "").strip()
            dep_code = str(sp.get("code") or "").strip()
            programs.append(
                DiscoveredProgram(
                    code=f"spbgu:{sp_id}",
                    name=name,
                    department_code=dep_code,
                    is_international=("ждунар" in name.lower()),
                    list_ref=sp_id,
                )
            )
    return programs


def discover_programs(headless: bool = True) -> List[DiscoveredProgram]:
    """
    Обнаружить список программ магистратуры СПбГУ.

    `headless` оставлен для совместимости интерфейса; Selenium не используется —
    отчёт отдаёт данные обычным GET. Требует, чтобы хост enrollelists.spbu.ru был
    доступен из окружения (network egress allowlist).
    """
    return parse_report_meta(fetch_report_html())
