"""
Тесты сидинга каталога СПбГУ: разбор «шапки» блока и неймспейсинг кодов.

Офлайн, на фикстуре реального блока. Сети не требует.
"""
from pathlib import Path

import pytest

from app.application.use_cases.get_applicant_forecast import _display_code
from app.infrastructure.parser.spbgu.spbgu_master_parser import parse_block_info
from seed_spbgu_programs import namespaced_department, namespaced_institute

_BLOCK = (Path(__file__).parent / "fixtures" / "spbgu_speciality_block.html").read_text(encoding="utf-8")


# ── шапка блока: откуда берётся настоящее имя программы ────────────────────
def test_parse_block_info_reads_real_program_name():
    """
    В reportMeta лежит имя НАПРАВЛЕНИЯ («Филология»), одинаковое у нескольких
    программ. Настоящее имя есть только здесь — ради него и ходим за шапкой.
    """
    info = parse_block_info(_BLOCK)
    assert info["program_name"].startswith("Славянские языки и литературы")
    assert info["speciality_name"] == "Филология"
    assert info["program_name"] != info["speciality_name"]


def test_parse_block_info_splits_speciality_code_and_name():
    info = parse_block_info(_BLOCK)
    assert info["speciality_code"] == "45.04.01"
    assert info["speciality_name"] == "Филология"


def test_parse_block_info_reads_form_exams_and_places():
    info = parse_block_info(_BLOCK)
    assert info["education_form"] == "очная"
    assert "Литература зарубежных стран" in info["exams"]
    assert info["num_places"] == 5


def test_parse_block_info_unescapes_html_entities():
    """В названии программы встречаются &quot; — они должны стать кавычками."""
    info = parse_block_info(_BLOCK)
    assert "&quot;" not in info["program_name"]
    assert '"' in info["program_name"]


def test_parse_block_info_on_garbage_returns_empty():
    assert parse_block_info("<div>ничего похожего</div>") == {}
    assert parse_block_info("") == {}


def test_parse_block_info_ignores_empty_values():
    html = ('<div class="table-information"><table>'
            "<tr><th>Форма обучения:</th><td></td></tr>"
            "<tr><th>Образовательная программа:</th><td>Математика</td></tr>"
            "</table></div>")
    info = parse_block_info(html)
    assert "education_form" not in info
    assert info["program_name"] == "Математика"


# ── неймспейсинг кодов ────────────────────────────────────────────────────
def test_department_and_institute_are_namespaced():
    """
    Коды направлений федеральные и совпадают у вузов, а таблица departments
    общая. Без префикса СПбПУ и СПбГУ делили бы один department_code — он же
    exam_id в Monte-Carlo, и баллы за разные экзамены смешались бы.
    """
    assert namespaced_department("01.04.02") == "spbgu:01.04.02"
    assert namespaced_institute("01.04.02") == "spbgu:01"
    assert namespaced_institute("45.04.01") == "spbgu:45"


def test_namespaced_codes_do_not_collide_with_spbpu():
    assert namespaced_department("01.04.02") != "01.04.02"


# ── префикс не должен доезжать до пользователя ────────────────────────────
@pytest.mark.parametrize(
    "stored,shown",
    [
        ("spbgu:01.04.02", "01.04.02"),
        ("spbgu:45", "45"),
        ("01.04.02", "01.04.02"),   # коды СПбПУ не трогаем
        ("09.04.01", "09.04.01"),
    ],
)
def test_display_code_strips_university_prefix(stored, shown):
    assert _display_code(stored) == shown


def test_seeded_department_is_shown_without_prefix():
    """Сквозная проверка: что записали при сидинге → что увидит пользователь."""
    stored = namespaced_department("45.04.01")
    assert _display_code(stored) == "45.04.01"
