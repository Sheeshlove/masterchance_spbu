"""
Граничные случаи парсинга отчёта СПбГУ.

Основной happy-path проверяется в test_spbgu_list.py на реальной фикстуре;
здесь — устойчивость к вариациям разметки: два вступительных испытания,
пустой список, отсутствующее КЦП, мусорные строки, разные написания дат.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest

from app.infrastructure.parser.spbgu.spbgu_master_parser import (
    _field_for_header,
    block_to_records,
    parse_report_datetime,
)
from app.infrastructure.parser.spbgu.spbgu_programs import build_report_url

_GEN = datetime(2026, 8, 5, 16, 0)
_CODE = "spbgu:test"


def _block(headers: list[str], rows: list[list[str]], places: str | None = "5") -> str:
    info = ""
    if places is not None:
        info = (
            '<div class="table-information"><table>'
            f"<tr><th><div>Количество бюджетных мест:</div></th><td>{places}</td></tr>"
            "</table></div>"
        )
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td class="amount">{c}</td>' for c in row) + "</tr>" for row in rows
    )
    return (
        f'<div class="loaded-speciality-block">{info}'
        '<div class="table-data"><table>'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody>"
        "</table></div></div>"
    )


_TWO_EXAM_HEADERS = [
    "№",
    "Уникальный код поступающего",
    "Сумма конкурсных баллов",
    "Сумма баллов за вступительные испытания",
    "Балл за ВИ\n №1",
    "Балл за ВИ\n №2",
    "Сумма баллов за общие индивидуальные достижения",
    "Согласие на зачисление",
    "Приоритет зачисления, указанный поступающим по данной КГ",
    "Статус",
]


def test_two_entrance_exams_fill_both_subjects():
    """У части программ два ВИ — оба балла должны попасть в свои поля."""
    html = _block(_TWO_EXAM_HEADERS,
                  [["1", "1645144", "150", "140", "80", "60", "10", "Да", "2", "Участвует"]])
    stats, apps = block_to_records(html, _CODE, _GEN)
    app = apps[0]
    assert (app.subject1_score, app.subject2_score) == (80, 60)
    assert (app.total_score, app.vi_score, app.id_achievements) == (150, 140, 10)
    assert app.priority == 2 and app.consent is True
    assert stats.num_places == 5


def test_empty_list_yields_no_applications():
    html = _block(_TWO_EXAM_HEADERS, [])
    stats, apps = block_to_records(html, _CODE, _GEN)
    assert apps == []
    assert stats.num_applications == 0
    assert stats.generated_at == _GEN


def test_missing_places_defaults_to_zero():
    html = _block(_TWO_EXAM_HEADERS,
                  [["1", "1645144", "99", "99", "99", "0", "0", "Да", "1", "Участвует"]],
                  places=None)
    stats, _ = block_to_records(html, _CODE, _GEN)
    assert stats.num_places == 0


def test_rows_without_applicant_code_are_skipped():
    html = _block(_TWO_EXAM_HEADERS, [
        ["1", "", "99", "99", "99", "0", "0", "Да", "1", "Участвует"],
        ["2", "1645144", "97", "97", "97", "0", "0", "Нет", "3", "Участвует"],
    ])
    stats, apps = block_to_records(html, _CODE, _GEN)
    assert [a.applicant_id for a in apps] == ["1645144"]
    assert stats.num_applications == 1


def test_consent_only_da_is_true():
    html = _block(_TWO_EXAM_HEADERS, [
        ["1", "A", "0", "0", "0", "0", "0", "Да", "1", "Участвует"],
        ["2", "B", "0", "0", "0", "0", "0", "Нет", "1", "Участвует"],
        ["3", "C", "0", "0", "0", "0", "0", "", "1", "Участвует"],
    ])
    _, apps = block_to_records(html, _CODE, _GEN)
    assert [a.consent for a in apps] == [True, False, False]


def test_non_numeric_cells_fall_back_to_zero():
    """Прочерки и пустые ячейки не должны ронять парсер."""
    html = _block(_TWO_EXAM_HEADERS,
                  [["1", "1645144", "—", "", "-", "0", "", "Да", "", "Участвует"]])
    _, apps = block_to_records(html, _CODE, _GEN)
    app = apps[0]
    assert (app.total_score, app.vi_score, app.priority) == (0, 0, 0)


def test_target_achievements_always_zero_for_spbgu():
    html = _block(_TWO_EXAM_HEADERS,
                  [["1", "1645144", "99", "99", "99", "0", "0", "Да", "1", "Участвует"]])
    _, apps = block_to_records(html, _CODE, _GEN)
    assert apps[0].target_id_achievements == 0
    assert apps[0].program_code == _CODE


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Уникальный код поступающего", "applicant_id"),
        ("Сумма конкурсных баллов", "total_score"),
        ("Сумма баллов за вступительные испытания", "vi_score"),
        ("Балл за ВИ №1", "subject1_score"),
        ("Балл за ВИ\n                    №2", "subject2_score"),
        ("Сумма баллов за общие индивидуальные достижения", "id_achievements"),
        ("Согласие на зачисление", "consent"),
        ("Приоритет зачисления, указанный поступающим по данной КГ", "priority"),
        ("Статус", "review_status"),
        ("№", None),
        ("Балл за ВИ №3", None),
    ],
)
def test_header_mapping(header, expected):
    assert _field_for_header(header) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("05 августа 2026 г. 16:00", datetime(2026, 8, 5, 16, 0)),
        ("1 января 2027 г. 09:05", datetime(2027, 1, 1, 9, 5)),
        ("31 декабря 2026 г. 23:59", datetime(2026, 12, 31, 23, 59)),
    ],
)
def test_parse_report_datetime_variants(text, expected):
    html = f'<div id="datetime"><span>{text}</span></div>'
    assert parse_report_datetime(html) == expected


@pytest.mark.parametrize(
    "html",
    [
        "<div>без блока datetime</div>",
        '<div id="datetime"><span>какая-то ерунда</span></div>',
        '<div id="datetime"><span>05 месяца 2026 г. 16:00</span></div>',
    ],
)
def test_parse_report_datetime_returns_none_on_garbage(html):
    assert parse_report_datetime(html) is None


def test_build_report_url_defaults_to_master_budget():
    query = parse_qs(urlparse(build_report_url("https://example.test/r.php")).query)
    assert query["education_level_sort_order"] == ["2"]   # магистратура
    assert query["fin_source_name"] == ["Бюджет"]
    assert query["is_foreign"] == ["0"]


def test_build_report_url_applies_overrides():
    url = build_report_url("https://example.test/r.php", applicant_code="1645144")
    query = parse_qs(urlparse(url).query)
    assert query["applicant_code"] == ["1645144"]
    assert query["fin_source_name"] == ["Бюджет"]        # прочие фильтры не потерялись
