"""
ВШЭ: страница со ссылками на файлы XLS по программам.

Списки лежат не таблицей на странице, а файлами Excel — по одному на конкурс
(priem45.hse.ru/magstats.html). Страница разбита на разделы: кампус → форма
обучения → основа. Нам нужны только Москва и Санкт-Петербург, только очная
форма и только бюджет, поэтому проверяется не столько разбор, сколько отбор:
лишний раздел, попавший в выдачу, — это чужой конкурс в общем списке.

Фикстуры сняты с настоящих: hse_magstats.html повторяет структуру страницы,
hse_program_list.xlsx — строение реальной выгрузки (шапка, парные «целевые»
колонки, подвал про олимпиады). Коды поступающих в нём выдуманные: настоящие в
открытый репозиторий класть незачем.
"""
from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

import pytest

from app.infrastructure.parser.base import ProgramListing
from app.infrastructure.parser.openlists import source as source_module
from app.infrastructure.parser.openlists.crawl import Fetched, find_links, sniff_binary
from app.infrastructure.parser.openlists.sheets import read_xlsx, rows_from, tables_from
from app.infrastructure.parser.openlists.source import OpenListsSource
from app.infrastructure.parser.openlists.specs import default_spec

_FIXTURES = Path(__file__).parent / "fixtures" / "openlists"
_PAGE = (_FIXTURES / "hse_magstats.html").read_text(encoding="utf-8")
_XLSX = (_FIXTURES / "hse_program_list.xlsx").read_bytes()
_SUMMARY_MSK = (_FIXTURES / "hse_summary_msk.xls").read_bytes()
_SUMMARY_SPB = (_FIXTURES / "hse_summary_spb.xls").read_bytes()
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

    assert table.headers[2] == "Уникальный код поступающего"
    # шапка над таблицей сохранилась — из неё берутся программа и дата
    assert "Анализ данных в биологии и медицине" in table.preamble
    assert "Время формирования" in table.preamble


def _parse_file(pages) -> object:
    url = "https://priem45.hse.ru/36634049850_Budget.xlsx"
    pages[url] = _XLSX
    return _hse().fetch(ProgramListing(ref=url, title="Анализ данных в биологии и медицине"))[0]


def test_applications_are_parsed_from_the_file(pages):
    program = _parse_file(pages)

    assert program.program_name == "Анализ данных в биологии и медицине (Москва)"
    assert program.speciality_code == "01.04.02"
    assert program.program_code.startswith("hse:01.04.02:")
    assert [a.applicant_id for a in program.applications] == [
        "9000001", "9000002", "9000003", "9000004", "9000005",
    ]

    first = program.applications[0]
    assert first.total_score == 100
    assert first.priority == 7


def test_target_quota_columns_do_not_clobber_the_real_ones(pages):
    """
    Рядом с «Приоритет бюджетного места» стоит пустой «Приоритет целевого
    места», а рядом с «Сумма конкурсных баллов» — «…в рамках квоты на целевые
    места». Обе подходят под общие правила, и, будучи прочитанными, обнуляли
    приоритет и балл: у всех выходил приоритет 1.
    """
    program = _parse_file(pages)

    assert sorted(a.priority for a in program.applications) == [1, 2, 2, 7, 41]
    assert program.applications[0].total_score == 100


def test_consent_marked_with_a_letter_is_still_consent(pages):
    """ВШЭ ставит в колонке согласия букву «Б» — согласие на бюджетное место."""
    program = _parse_file(pages)
    consented = [a.applicant_id for a in program.applications if a.consent]

    assert consented == ["9000003", "9000004"]


def test_footer_line_does_not_become_an_applicant(pages):
    """Под таблицей идёт подпись про зачёт олимпиад — это не абитуриент."""
    program = _parse_file(pages)
    codes = [a.applicant_id for a in program.applications]

    assert not any("Олимпиада" in code for code in codes)
    assert all(code.isdigit() for code in codes)


def test_withdrawn_documents_leave_the_competition(pages):
    """Забрал документы — на место не претендует и конкурентом больше не является."""
    program = _parse_file(pages)

    assert "9000006" not in [a.applicant_id for a in program.applications]


def test_publication_date_comes_from_the_file_header(pages):
    program = _parse_file(pages)
    assert program.stats.generated_at.strftime("%d.%m.%Y %H:%M") == "12.08.2026 15:39"


def test_seats_are_not_invented_when_the_file_has_none(pages):
    """
    Числа мест в выгрузке ВШЭ нет вовсе. Ноль здесь — это «неизвестно», и
    придумывать его нельзя: по нему считается весь конкурс.
    """
    program = _parse_file(pages)
    assert program.stats.num_places == 0


# ── формат файла ───────────────────────────────────────────────────────────
def test_xls_that_is_really_html_still_works():
    """Половина вузовских систем отдаёт HTML-таблицу под именем .xls."""
    html = (_FIXTURES / "list_simple.html").read_text(encoding="utf-8")
    page = Fetched(url="https://example.edu/list.xls", raw=html.encode("utf-8"))

    tables = tables_from(page)

    assert tables and tables[0].rows


@pytest.mark.skipif(find_spec("xlrd") is None, reason="xlrd входит в requirements.txt")
def test_legacy_binary_xls_is_read(caplog):
    """
    Сводку с числом мест ВШЭ выкладывает в старом бинарном .xls — именно из-за
    неё xlrd в зависимостях.
    """
    page = Fetched(url="https://example.edu/summary.xls", raw=_SUMMARY_MSK)

    sheets = rows_from(page)

    assert sheets and sheets[0][0] == "на 12.08.2026"
    assert any("Анализ данных" in " ".join(row) for row in sheets[0][1])


def test_broken_xls_is_reported_not_silently_empty(caplog):
    """Битый файл — понятная запись в логе, а не тишина."""
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    page = Fetched(url="https://example.edu/list.xls", raw=ole)

    assert tables_from(page) == []
    assert any("не читается" in record.message or "xlrd" in record.message
               for record in caplog.records)


def test_pdf_is_reported_not_silently_empty(caplog):
    page = Fetched(url="https://example.edu/list.pdf", raw=b"%PDF-1.7\n%...")

    assert tables_from(page) == []
    assert any("PDF" in record.message for record in caplog.records)


# ── число мест: его нет в списке, оно в сводке ──────────────────────────────
def _with_summary(pages) -> None:
    """Страница + обе сводки (Москва и Петербург), как на priem45."""
    pages[_PAGE_URL] = _PAGE
    pages["https://priem45.hse.ru/data/2026/08/14/stat_msk_ochn.xlsx"] = _SUMMARY_MSK
    pages["https://priem45.hse.ru/data/2026/08/14/stat_spb_ochn.xlsx"] = _SUMMARY_SPB


def test_seats_come_from_the_summary(pages):
    """
    В списке ВШЭ числа мест нет вовсе — оно лежит в сводке «Статистика
    поданных заявлений». Без него шанс считать не на чем.
    """
    _with_summary(pages)
    url = "https://priem45.hse.ru/36634049850_Budget.xlsx"
    pages[url] = _XLSX

    program = _hse().fetch(ProgramListing(ref=url, title="Анализ данных в биологии и медицине"))[0]

    assert program.stats.num_places == 25


def test_summary_is_read_but_never_becomes_a_program(pages):
    """Сводка — не список: кодов поступающих в ней нет, программ из неё не делаем."""
    _with_summary(pages)

    seats = _hse().seats()
    assert len(seats) == 4                       # три московские программы и питерский «Дизайн»
    assert not any("итого" in name for _campus, name in seats)
    assert not any("зарегистрирован" in name for _campus, name in seats)


def test_same_program_in_two_campuses_stays_two_competitions(pages):
    """
    «Дизайн» есть и в Москве (30 мест), и в Петербурге (7). Это разные наборы,
    и склеиться в один конкурс они не должны — иначе места одного кампуса
    достанутся абитуриентам другого.
    """
    _with_summary(pages)
    source = _hse()
    seats = source.seats()

    msk = seats[("москва", "дизайн")]
    spb = seats[("санкт-петербург", "дизайн")]
    assert (msk.places, spb.places) == (30, 7)

    # и в коде программы кампус тоже различается
    from app.domain.universities import stable_program_code
    assert (stable_program_code("hse", "54.04.01", "Дизайн (Москва)")
            != stable_program_code("hse", "54.04.01", "Дизайн (Санкт-Петербург)"))


def test_summary_of_a_foreign_campus_is_not_downloaded(pages):
    """Сводки Перми и Нижнего не нужны — их программы мы всё равно не берём."""
    _with_summary(pages)
    urls = _hse()._seats_urls()

    assert all("nn_" not in url and "perm" not in url for url in urls)
    assert all("ochno_zaochn" not in url for url in urls), "очно-заочка — другой конкурс"


def test_a_downloaded_file_can_be_parsed_without_the_network():
    """
    Разбор отделён от скачивания: скачанный список проверяется с диска
    (scripts/diagnose_source.py hse --file=…), и сразу видно, дело в адресе
    или в самом файле.
    """
    source = _hse()
    source._seats = {}          # сводку с диска взять неоткуда

    programs = source.parse(
        Fetched(url="/tmp/36634049850_Budget.xlsx", raw=_XLSX),
        ProgramListing(ref="/tmp/36634049850_Budget.xlsx"),
    )

    assert len(programs) == 1
    assert programs[0].program_name == "Анализ данных в биологии и медицине (Москва)"
    assert len(programs[0].applications) == 5
