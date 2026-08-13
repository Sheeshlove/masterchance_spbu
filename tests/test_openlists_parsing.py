"""
Разбор открытых списков: колонки узнаются по смыслу, а не по вёрстке.

Пять вузов печатают одно и то же разными словами («Уникальный код», «СНИЛС»,
«Индивидуальный номер») и в разном порядке. Эти тесты фиксируют, что движок
понимает такие списки, не берёт из них лишнего и не путает баллы между собой.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.infrastructure.parser.openlists.columns import (
    field_for_header,
    field_for_key,
    looks_like_ranking,
    map_headers,
)
from app.infrastructure.parser.openlists.records import (
    json_rows_to_applications,
    parse_consent,
    parse_generated_at,
    parse_places,
    program_facts,
    table_to_applications,
    to_int,
)
from app.infrastructure.parser.openlists.tables import extract_tables

_FIXTURES = Path(__file__).parent / "fixtures" / "openlists"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


# ── заголовки колонок ──────────────────────────────────────────────────────
@pytest.mark.parametrize("header", [
    "Уникальный код поступающего",
    "уникальный код",
    "СНИЛС",
    "Индивидуальный номер",
    "Регистрационный номер",
    "Номер заявления",
    "Идентификатор",
])
def test_identifier_columns_are_recognised(header):
    assert field_for_header(header) == "applicant_id"


@pytest.mark.parametrize("header, field", [
    ("Сумма конкурсных баллов", "total_score"),
    ("Конкурсный балл", "total_score"),
    ("Общий балл", "total_score"),
    ("Итоговый балл", "total_score"),
    ("Сумма баллов за вступительные испытания", "vi_score"),
    ("Балл за вступительное испытание", "vi_score"),
    ("Индивидуальные достижения", "id_achievements"),
    ("ИД", "id_achievements"),
    ("Приоритет", "priority"),
    ("Согласие на зачисление", "consent"),
    ("Оригинал документа", "consent"),
    ("Статус", "review_status"),
])
def test_columns_map_to_their_fields(header, field):
    assert field_for_header(header) == field


def test_exam_sum_is_not_mistaken_for_the_competitive_score():
    """
    Оба заголовка начинаются с «Сумма баллов». Если общее правило сработает
    раньше частного, балл за экзамен уедет в конкурсный — и человек с ИД
    потеряет их из итога.
    """
    headers = ["Сумма конкурсных баллов", "Сумма баллов за вступительные испытания",
               "Сумма баллов за индивидуальные достижения"]
    assert map_headers(headers) == ["total_score", "vi_score", "id_achievements"]


@pytest.mark.parametrize("header", ["Фамилия", "ФИО", "Фамилия И.О.", "Имя", "Дата рождения"])
def test_personal_names_are_never_taken(header):
    """
    Сервис обещает, что связать код с человеком не может. Обещание должно
    держаться кодом: колонку с фамилией мы распознаём именно для того, чтобы
    гарантированно её выбросить.
    """
    assert field_for_header(header) is None


def test_row_number_column_is_ignored():
    assert field_for_header("№ п/п") is None
    assert field_for_header("№") is None


def test_ranking_needs_an_identifier_and_a_competition_signal():
    assert looks_like_ranking(["applicant_id", "total_score"])
    assert not looks_like_ranking(["applicant_id"])           # только коды — не конкурс
    assert not looks_like_ranking(["total_score", "priority"])  # некого ранжировать


@pytest.mark.parametrize("key, field", [
    ("snils", "applicant_id"),
    ("unique_code", "applicant_id"),
    ("case_number", "applicant_id"),
    ("total_score", "total_score"),
    ("totalScore", "total_score"),
    ("priority", "priority"),
    ("agreement", "consent"),
    ("Приоритет", "priority"),
    ("program_id", None),
])
def test_json_keys_map_to_fields(key, field):
    assert field_for_key(key) == field


# ── значения ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value, expected", [
    ("98", 98), ("96,5", 96), ("—", 0), ("", 0), (None, 0), (88, 88), (True, 1),
])
def test_numbers_survive_the_formatting(value, expected):
    assert to_int(value) == expected


@pytest.mark.parametrize("value", ["Да", "да", "+", "есть", "Подано", "Оригинал", True])
def test_consent_yes(value):
    assert parse_consent(value) is True


@pytest.mark.parametrize("value", ["Нет", "-", "—", "", None, "не подано", "отозвано"])
def test_consent_no(value):
    assert parse_consent(value) is False


@pytest.mark.parametrize("text, places", [
    ("Количество мест: 25", 25),
    ("Количество бюджетных мест — 8", 8),
    ("КЦП: 12", 12),
    ("очная форма, 40 мест", 40),
    ("мест вуз пока не объявил", 0),
])
def test_places_are_read_from_the_heading(text, places):
    assert parse_places(text) == places


def test_generated_at_needs_a_publication_marker():
    assert parse_generated_at("Список сформирован по состоянию на 05.08.2026 16:00") == \
        datetime(2026, 8, 5, 16, 0)
    assert parse_generated_at("по состоянию на 05 августа 2026 г.") == datetime(2026, 8, 5, 0, 0)
    # Дата подачи документов — не дата публикации списка; выдумывать нельзя.
    assert parse_generated_at("Приём документов до 25.07.2026") is None


@pytest.mark.parametrize("heading, name", [
    ("Образовательная программа «Маркетинг», очная, 25 мест", "Маркетинг"),
    ("38.04.02 Менеджмент. Магистерская программа «Финансы», бюджет", "Финансы"),
    ("01.04.02 Прикладная математика — программа магистратуры Анализ данных, очная",
     "Анализ данных"),
])
def test_program_name_is_cut_out_of_the_heading(heading, name):
    assert program_facts(heading).program_name == name


def test_speciality_code_comes_from_the_heading():
    facts = program_facts("38.04.02 Менеджмент. Образовательная программа «Маркетинг», очная")
    assert facts.speciality_code == "38.04.02"
    assert facts.education_form == "очная"


def test_missing_speciality_falls_back_to_a_placeholder():
    """Без кода направления программу всё равно показываем — это не повод её терять."""
    assert program_facts("Образовательная программа «Маркетинг»").speciality_code == "00.04.00"


# ── таблицы целиком ────────────────────────────────────────────────────────
def test_simple_list_is_parsed():
    table = extract_tables(_fixture("list_simple.html"))[0]
    apps = table_to_applications(table, "hse:38.04.02:test")

    assert [a.applicant_id for a in apps] == ["1000004", "1000117", "1000238", "1000411"]
    first = apps[0]
    assert (first.total_score, first.vi_score, first.id_achievements) == (98, 93, 5)
    assert first.priority == 1 and first.consent is True
    assert apps[1].consent is False
    # прочерк в баллах — это ноль, а не пропуск строки
    assert apps[3].total_score == 0


def test_heading_above_the_table_gives_name_and_seats():
    table = extract_tables(_fixture("list_simple.html"))[0]
    facts = program_facts(table.preamble or table.page_title)

    assert facts.program_name == "Маркетинг и рыночная аналитика"
    assert facts.speciality_code == "38.04.02"
    assert facts.num_places == 25


def test_two_programs_on_one_page_stay_separate():
    tables = extract_tables(_fixture("list_two_programs.html"))
    ranking = [t for t in tables if table_to_applications(t, "x")]

    assert len(ranking) == 2, "таблица с расписанием не должна считаться списком"
    assert program_facts(ranking[0].preamble).program_name == "Анализ данных"
    assert program_facts(ranking[1].preamble).program_name == "Финансовая экономика"


def test_names_from_the_page_never_reach_the_records():
    """В этом списке есть колонка с фамилией — в заявках её быть не должно."""
    table = extract_tables(_fixture("list_two_programs.html"))[0]
    apps = table_to_applications(table, "msu:01.04.02:test")

    assert [a.applicant_id for a in apps] == ["231-045-812 30", "118-902-334 12"]
    stored = " ".join(f"{a.applicant_id} {a.review_status}" for a in apps)
    assert "Иванов" not in stored and "Петрова" not in stored


def test_exam_score_is_restored_when_only_the_total_is_published():
    """
    У части вузов колонки «баллы за ВИ» нет вовсе. Монте-Карло считает
    конкурсный балл как ВИ + ИД, поэтому нулевой ВИ отправил бы человека
    в конкурс с нулём при опубликованных 88 баллах.
    """
    table = extract_tables(_fixture("list_two_programs.html"))[0]
    first = table_to_applications(table, "msu:01.04.02:test")[0]

    assert first.total_score == 88
    assert first.vi_score == 85          # 88 итог − 3 за индивидуальные достижения
    assert first.id_achievements == 3


def test_duplicate_rows_collapse_to_one_application():
    """
    Один и тот же человек попадает в список дважды (общий конкурс и квота).
    В базе ключ — (программа, абитуриент), и дубль валит вставку целиком.
    """
    rows = [
        {"snils": "111", "total_score": 80, "priority": 1, "agreement": "Да"},
        {"snils": "111", "total_score": 92, "priority": 1, "agreement": "Да"},
        {"snils": "222", "total_score": 70, "priority": 2, "agreement": "Нет"},
    ]
    apps = json_rows_to_applications(rows, "itmo:09.04.01:test")

    assert len(apps) == 2
    assert next(a for a in apps if a.applicant_id == "111").total_score == 92


def test_json_payload_is_parsed_like_a_table():
    rows = [
        {"position": 1, "unique_code": "1645144", "total_score": 96,
         "exam_score": 91, "achievements_score": 5, "priority": 1, "agreement": True,
         "status": "Участвует в конкурсе"},
    ]
    app = json_rows_to_applications(rows, "itmo:09.04.01:test")[0]

    assert app.applicant_id == "1645144"
    assert (app.total_score, app.vi_score, app.id_achievements) == (96, 91, 5)
    assert app.consent is True
    assert app.priority == 1
