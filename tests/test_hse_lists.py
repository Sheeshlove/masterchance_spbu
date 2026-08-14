"""
ВШЭ: страница со ссылками на файлы XLS по программам.

Списки лежат не таблицей на странице, а файлами Excel — по одному на конкурс
(priem45.hse.ru/magstats.html). Страница разбита на разделы: кампус → форма
обучения → основа. Нам нужны только Москва и Санкт-Петербург, только очная
форма и только бюджет, поэтому проверяется не столько разбор, сколько отбор:
лишний раздел, попавший в выдачу, — это чужой конкурс в общем списке.

Фикстуры: hse_magstats.html повторяет структуру страницы (см. её комментарий),
hse_program_list.xlsx сделан настоящим Excel-writer'ом, читается нашим.
"""
from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

import pytest

from app.infrastructure.parser.base import ProgramListing
from app.infrastructure.parser.openlists import source as source_module
from app.infrastructure.parser.openlists.crawl import Fetched, find_links, sniff_binary
from app.infrastructure.parser.openlists.sheets import read_xlsx, tables_from
from app.infrastructure.parser.openlists.source import OpenListsSource
from app.infrastructure.parser.openlists.specs import default_spec

_FIXTURES = Path(__file__).parent / "fixtures" / "openlists"
_PAGE = (_FIXTURES / "hse_magstats.html").read_text(encoding="utf-8")
_XLSX = (_FIXTURES / "hse_program_list.xlsx").read_bytes()
_PAGE_URL = "https://priem45.hse.ru/magstats.html"


@pytest.fixture
def pages(monkeypatch):
    """Сеть → словарь {url: тело}. Тело может быть и байтами (файл Excel)."""
    served: dict[str, bytes | str] = {}

    def fake_fetch(url, timeout=60, data=None):
        if url not in served:
            raise OSError(f"страница не отвечает: {url}")
        body = served[url]
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        return Fetched(url=url, raw=raw, content_type="", charset="utf-8")

    monkeypatch.setattr(source_module, "fetch", fake_fetch)
    return served


def _hse() -> OpenListsSource:
    return OpenListsSource(default_spec("hse"))


# ── что берём со страницы ──────────────────────────────────────────────────
def test_only_moscow_and_spb_are_taken(pages):
    pages[_PAGE_URL] = _PAGE

    refs = [item.ref for item in _hse().discover()]

    assert any("msk_marketing.xlsx" in ref for ref in refs)
    assert any("spb_finance.xlsx" in ref for ref in refs)
    assert not any("nn_" in ref for ref in refs), "Нижний Новгород — чужая приёмная кампания"
    assert not any("perm_" in ref for ref in refs), "Пермь — чужая приёмная кампания"


def test_part_time_and_paid_sections_are_skipped(pages):
    pages[_PAGE_URL] = _PAGE

    refs = [item.ref for item in _hse().discover()]

    assert not any("ochno_zaochn" in ref or "vecher" in ref for ref in refs), \
        "очно-заочная форма — другой конкурс с другими местами"
    assert not any("platno" in ref for ref in refs), "платные места — другой конкурс"


def test_only_program_lists_and_not_the_summary(pages):
    """
    «СТАТИСТИКА ПОДАННЫХ ЗАЯВЛЕНИЙ» — сводка по числу заявлений, а не список:
    кодов поступающих в ней нет, качать её незачем.
    """
    pages[_PAGE_URL] = _PAGE
    refs = [item.ref for item in _hse().discover()]

    assert sorted(ref.rsplit("/", 1)[-1] for ref in refs) == [
        "msk_ds.xlsx", "msk_marketing.xlsx", "spb_finance.xlsx",
    ]


def test_link_keeps_the_section_it_was_found_in():
    """Без раздела ссылка «Скачать в формате XLS» неотличима от соседней."""
    links = find_links(_PAGE, _PAGE_URL, r"\.xlsx")
    by_name = {link.url.rsplit("/", 1)[-1]: link for link in links}

    assert "Москва" in by_name["msk_marketing.xlsx"].context
    assert "Санкт-Петербург" in by_name["spb_finance.xlsx"].context
    assert "Очно-заочная" in by_name["msk_mba_vecher.xlsx"].context


# ── чтение файла Excel ─────────────────────────────────────────────────────
def test_xlsx_is_recognised_by_its_signature():
    assert sniff_binary(_XLSX) == "xlsx"
    assert sniff_binary(b"<html><table></table></html>") is None


def test_xlsx_sheet_becomes_a_table():
    table = read_xlsx(_XLSX)[0]

    assert table.page_title == "Маркетинг"
    assert "Уникальный" not in " ".join(table.headers)   # у ВШЭ колонка названа иначе
    assert table.headers[1] == "Индивидуальный номер"
    assert len(table.rows) == 4
    # шапка над таблицей сохранилась — из неё берутся программа и места
    assert "Маркетинг и рыночная аналитика" in table.preamble
    assert "25" in table.preamble


def test_applications_are_parsed_from_the_file(pages):
    url = "https://priem45.hse.ru/data/2026/08/14/msk_marketing.xlsx"
    pages[url] = _XLSX

    programs = _hse().fetch(ProgramListing(ref=url, title="Маркетинг и рыночная аналитика"))

    assert len(programs) == 1, "лист с платными местами не должен стать второй программой"
    program = programs[0]
    assert program.program_name == "Маркетинг и рыночная аналитика"
    assert program.speciality_code == "38.04.02"
    assert program.program_code.startswith("hse:38.04.02:")
    assert program.stats.num_places == 25
    assert [a.applicant_id for a in program.applications] == \
        ["1000004", "1000117", "1000238", "1000411"]

    first = program.applications[0]
    assert (first.total_score, first.vi_score, first.id_achievements) == (98, 93, 5)
    assert first.priority == 1 and first.consent is True
    assert program.applications[1].consent is False


def test_paid_sheet_of_the_same_file_is_not_taken(pages):
    """В книге два листа: бюджет и платные места. Второй — другой конкурс."""
    url = "https://priem45.hse.ru/data/2026/08/14/msk_marketing.xlsx"
    pages[url] = _XLSX

    programs = _hse().fetch(ProgramListing(ref=url, title="Маркетинг"))
    codes = {a.applicant_id for p in programs for a in p.applications}

    assert "2000001" not in codes, "абитуриент с платного листа попал в бюджетный конкурс"


def test_publication_date_comes_from_the_file_header(pages):
    url = "https://priem45.hse.ru/data/2026/08/14/msk_marketing.xlsx"
    pages[url] = _XLSX

    program = _hse().fetch(ProgramListing(ref=url))[0]

    assert program.stats.generated_at.strftime("%d.%m.%Y") == "14.08.2026"


# ── формат файла ───────────────────────────────────────────────────────────
def test_xls_that_is_really_html_still_works():
    """Половина вузовских систем отдаёт HTML-таблицу под именем .xls."""
    html = (_FIXTURES / "list_simple.html").read_text(encoding="utf-8")
    page = Fetched(url="https://example.edu/list.xls", raw=html.encode("utf-8"))

    tables = tables_from(page)

    assert tables and tables[0].rows


@pytest.mark.skipif(find_spec("xlrd") is not None,
                    reason="проверяем поведение именно без xlrd")
def test_legacy_binary_xls_is_reported_not_silently_empty(caplog):
    """Старый .xls без xlrd — понятная запись в логе, а не тишина."""
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    page = Fetched(url="https://example.edu/list.xls", raw=ole)

    assert tables_from(page) == []
    assert any("xlrd" in record.message for record in caplog.records), \
        "источник обязан сказать, чего ему не хватает"


def test_pdf_is_reported_not_silently_empty(caplog):
    page = Fetched(url="https://example.edu/list.pdf", raw=b"%PDF-1.7\n%...")

    assert tables_from(page) == []
    assert any("PDF" in record.message for record in caplog.records)
