"""
Правила именования: по ним данные шести вузов держатся врозь.

Это самый дешёвый способ всё перепутать — собрать идентификатор мимо этих
функций. Тесты фиксируют сами правила, чтобы ошибка нашлась здесь, а не в
прогнозе, где она выглядит просто как «странный шанс».
"""
from __future__ import annotations

import pytest

from app.domain.universities import (
    SUPPORTED_UNIVERSITIES,
    UNIVERSITY_LABELS,
    applicant_key,
    candidate_applicant_keys,
    display_code,
    label,
    namespaced_department,
    namespaced_institute,
    normalize_university,
    parse_university_list,
    raw_applicant_id,
    split_codes,
    stable_program_code,
    university_of_applicant,
    university_of_program,
)


def test_all_six_universities_are_supported():
    assert set(SUPPORTED_UNIVERSITIES) == {"spbgu", "hse", "itmo", "mgimo", "msu", "ranepa"}
    assert all(UNIVERSITY_LABELS[u] for u in SUPPORTED_UNIVERSITIES)


def test_labels_are_the_short_russian_names():
    assert label("hse") == "ВШЭ"
    assert label("itmo") == "ИТМО"
    assert label("ranepa") == "РАНХиГС"
    assert label("mit") == "mit"          # незнакомый ключ показываем как есть
    assert label(None) == ""


@pytest.mark.parametrize("written, key", [
    ("SPBGU", "spbgu"), (" hse ", "hse"), ("ВШЭ", "hse"), ("МГУ", "msu"), ("РАНХиГС", "ranepa"),
])
def test_university_keys_are_forgiving_to_how_they_are_written(written, key):
    assert normalize_university(written) == key


# ── коды программ ──────────────────────────────────────────────────────────
def test_program_code_carries_its_university():
    code = stable_program_code("hse", "38.04.02", "Маркетинг", "очная")
    assert code.startswith("hse:38.04.02:")
    assert university_of_program(code) == "hse"


def test_same_program_name_in_two_universities_gives_two_codes():
    """
    «Менеджмент» есть почти везде. Если код совпадёт, заявки двух вузов
    сложатся в один конкурс — и число мест окажется чужим.
    """
    assert (stable_program_code("hse", "38.04.02", "Менеджмент")
            != stable_program_code("msu", "38.04.02", "Менеджмент"))


def test_program_code_is_stable_across_cosmetic_edits():
    assert (stable_program_code("itmo", "09.04.01", "Машинное  обучение")
            == stable_program_code("itmo", "09.04.01", " машинное обучение "))


def test_departments_and_institutes_are_namespaced_too():
    """department_code — это ещё и exam_id в Монте-Карло: смешивать нельзя."""
    assert namespaced_department("msu", "01.04.02") == "msu:01.04.02"
    assert namespaced_institute("msu", "01.04.02") == "msu:01"
    assert namespaced_department("hse", "01.04.02") != namespaced_department("msu", "01.04.02")


def test_display_code_hides_the_prefix():
    assert display_code("hse:38.04.02") == "38.04.02"
    assert display_code("38.04.02") == "38.04.02"


def test_unknown_prefix_is_not_taken_for_a_university():
    assert university_of_program("mit:6.006:abc") is None


# ── коды абитуриентов ──────────────────────────────────────────────────────
def test_applicant_key_separates_people_with_the_same_code():
    assert applicant_key("spbgu", "1645144") != applicant_key("msu", "1645144")
    assert university_of_applicant(applicant_key("msu", "1645144")) == "msu"
    assert raw_applicant_id("msu:1645144") == "1645144"


def test_raw_id_survives_a_code_without_prefix():
    assert raw_applicant_id("1645144") == "1645144"


def test_a_typed_code_is_looked_up_in_every_university():
    candidates = candidate_applicant_keys("1645144")
    assert candidates[: len(SUPPORTED_UNIVERSITIES)] == [
        f"{u}:1645144" for u in SUPPORTED_UNIVERSITIES
    ]
    # ...и как есть — ради снапшотов, собранных до разделения по вузам
    assert candidates[-1] == "1645144"


def test_a_full_key_is_looked_up_as_is():
    assert candidate_applicant_keys("hse:1645144") == ["hse:1645144"]


def test_empty_input_looks_for_nothing():
    assert candidate_applicant_keys("  ") == []


# ── разбор пользовательского ввода ─────────────────────────────────────────
def test_codes_are_split_by_commas_and_newlines():
    assert split_codes("1000004, 777\n1234; 55") == ["1000004", "777", "1234", "55"]


def test_snils_with_a_space_stays_one_code():
    """«231-045-812 30» — один код, а не два: по пробелу резать нельзя."""
    assert split_codes("231-045-812 30") == ["231-045-812 30"]


def test_duplicates_collapse():
    assert split_codes("777, 777") == ["777"]


# ── список вузов из конфига ────────────────────────────────────────────────
def test_all_means_every_university():
    assert parse_university_list("all") == SUPPORTED_UNIVERSITIES
    assert parse_university_list("") == SUPPORTED_UNIVERSITIES


def test_explicit_list_keeps_the_order_it_was_written_in():
    assert parse_university_list("itmo, spbgu") == ("itmo", "spbgu")


def test_unknown_keys_are_dropped_not_fatal():
    """Опечатка в .env не должна ронять обновление всех вузов разом."""
    assert parse_university_list("hse, mit, itmo") == ("hse", "itmo")
