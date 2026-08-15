"""
Стабильный код программы и проход по отчёту.

Регрессия на потерю данных: раньше кодом программы был UUID специальности из
конкретной выгрузки отчёта. Вуз перезаливал отчёт, UUID менялись, сохранённый
каталог протухал, запросы уходили по несуществующим кодам, списки приходили
пустыми — и заявки стирались.
"""
from datetime import datetime
from pathlib import Path

import pytest

from app.application.use_cases.update_lists import UpdateApplicationListsUseCase
from app.infrastructure.parser.spbgu import spbgu_programs
from app.infrastructure.parser.spbgu.spbgu_master_parser import (
    SpbguMasterApplicationsParser,
    parse_block_info,
)
from app.infrastructure.parser.spbgu.spbgu_programs import stable_program_code

_BLOCK = (Path(__file__).parent / "fixtures" / "spbgu_speciality_block.html").read_text(encoding="utf-8")


# ── сам код ───────────────────────────────────────────────────────────────
def test_code_does_not_contain_report_uuid():
    code = stable_program_code("45.04.01", "Славянские языки и литературы", "очная")
    assert code.startswith("spbgu:45.04.01:")
    assert "-" not in code.split(":")[-1]        # не UUID
    assert len(code.split(":")[-1]) == 8


def test_same_program_gives_same_code_every_time():
    a = stable_program_code("45.04.01", "Славянские языки и литературы", "очная")
    b = stable_program_code("45.04.01", "Славянские языки и литературы", "очная")
    assert a == b


@pytest.mark.parametrize("variant", [
    "славянские языки и литературы",                 # регистр
    "Славянские  языки   и литературы",              # лишние пробелы
    "  Славянские языки и литературы  ",             # края
    "Слaвянские языки и литературы".replace("a", "а"),
])
def test_cosmetic_differences_do_not_change_code(variant):
    base = stable_program_code("45.04.01", "Славянские языки и литературы", "очная")
    assert stable_program_code("45.04.01", variant, "очная") == base


def test_quotes_and_dashes_are_normalised():
    a = stable_program_code("01.04.02", 'Анализ данных (с квалификацией "Аналитик")')
    b = stable_program_code("01.04.02", "Анализ данных (с квалификацией «Аналитик»)")
    assert a == b


def test_different_programs_get_different_codes():
    a = stable_program_code("01.04.02", "Теория игр")
    b = stable_program_code("01.04.02", "Инженерия данных")
    assert a != b


def test_education_form_separates_codes():
    """Одна программа бывает очной и очно-заочной — это разные конкурсы."""
    assert (stable_program_code("44.04.01", "Педагогика", "очная")
            != stable_program_code("44.04.01", "Педагогика", "очно-заочная"))


def test_speciality_code_separates_codes():
    assert stable_program_code("01.04.02", "X") != stable_program_code("45.04.01", "X")


# ── вывод кода из настоящего блока ────────────────────────────────────────
def test_parse_speciality_derives_code_from_block(monkeypatch):
    """Стабильный код берётся из шапки блока, которую мы и так скачиваем."""
    parser = SpbguMasterApplicationsParser()
    monkeypatch.setattr(parser, "_ensure_report", lambda: {})
    monkeypatch.setattr(parser, "_fetch_speciality_blocks",
                        lambda sid, timeout=60: [{"html": _BLOCK}])
    parser._generated_at = datetime(2026, 8, 6, 17, 0)

    res = parser.parse_speciality("любой-uuid")

    info = parse_block_info(_BLOCK)
    assert res.program_code == stable_program_code(
        info["speciality_code"], info["program_name"], info["education_form"])
    assert res.speciality_code == "45.04.01"
    assert res.program_name.startswith("Славянские языки")
    assert len(res.applications) == 4
    assert all(a.program_code == res.program_code for a in res.applications)


def test_parse_speciality_returns_none_on_empty_block(monkeypatch):
    parser = SpbguMasterApplicationsParser()
    monkeypatch.setattr(parser, "_ensure_report", lambda: {})
    monkeypatch.setattr(parser, "_fetch_speciality_blocks", lambda sid, timeout=60: [])
    assert parser.parse_speciality("uuid") is None


# ── проход по отчёту ──────────────────────────────────────────────────────
def _record(uuid_unused: str, program_name: str, n_apps: int = 2, speciality="01.04.02"):
    """Как это возвращает fetch_listings_in_parallel (уже сериализовано)."""
    code = stable_program_code(speciality, program_name, "очная")
    return {
        "program_code": code,
        "program_name": program_name,
        "speciality_code": speciality,
        "education_form": "очная",
        "is_international": False,
        "stats": {"program_code": code, "num_places": 10, "num_applications": n_apps,
                  "generated_at": datetime(2026, 8, 6, 17, 0)},
        "applications": [
            {"program_code": code, "applicant_id": f"100{i}", "total_score": 90,
             "vi_score": 90, "subject1_score": 0, "subject2_score": 0,
             "id_achievements": 0, "target_id_achievements": 0, "priority": 1,
             "consent": True, "review_status": "Участвует в конкурсе"}
            for i in range(n_apps)
        ],
    }


def _run(repo, monkeypatch, uuids, records):
    monkeypatch.setattr(
        spbgu_programs, "discover_programs",
        lambda *a, **k: [{"code": f"spbgu:{u}", "name": "n", "department_code": "01.04.02",
                          "is_international": False, "list_ref": u} for u in uuids],
    )
    monkeypatch.setattr(
        "app.infrastructure.parser.runner.fetch_listings_in_parallel",
        lambda university, listings, parallelism=4: records,
    )
    UpdateApplicationListsUseCase(repo=repo).execute_spbgu()


def test_report_uuid_change_does_not_break_anything(repo, monkeypatch):
    """
    Ровно тот сценарий, что сломал прод: отчёт перезалит, UUID другие,
    программы те же. Коды программ обязаны остаться прежними.
    """
    _run(repo, monkeypatch, ["uuid-старый"], [_record("uuid-старый", "Теория игр")])
    first = [p.code for p in repo.get_programs_by_university("spbgu")]
    apps_before = repo.get_applications_by_applicant("1000")

    # новая выгрузка: другой UUID, та же программа
    _run(repo, monkeypatch, ["uuid-совсем-другой"], [_record("uuid-совсем-другой", "Теория игр")])
    second = [p.code for p in repo.get_programs_by_university("spbgu")]

    assert first == second, "код программы не должен зависеть от UUID выгрузки"
    assert len(repo.get_programs_by_university("spbgu")) == 1, "дубликата быть не должно"
    assert repo.get_applications_by_applicant("1000")[0].program_code == apps_before[0].program_code


def test_catalogue_is_filled_by_the_same_pass(repo, monkeypatch):
    """Отдельный сидинг больше не обязателен: каталог наполняется проходом."""
    assert repo.get_programs_by_university("spbgu") == []
    _run(repo, monkeypatch, ["u1", "u2"],
         [_record("u1", "Теория игр"), _record("u2", "Инженерия данных")])

    programs = repo.get_programs_by_university("spbgu")
    assert len(programs) == 2
    assert {p.department_code for p in programs} == {"spbgu:01.04.02"}
    assert all(p.university == "spbgu" for p in programs)


def test_all_empty_aborts_without_touching_data(repo, monkeypatch):
    _run(repo, monkeypatch, ["u1"], [_record("u1", "Теория игр", n_apps=2)])
    before = len(repo.get_applications_by_applicant("1000"))

    empty = _record("u1", "Теория игр", n_apps=0)
    empty["applications"] = []
    with pytest.raises(RuntimeError, match="ни по одной программе"):
        _run(repo, monkeypatch, ["u1"], [empty])

    assert len(repo.get_applications_by_applicant("1000")) == before
