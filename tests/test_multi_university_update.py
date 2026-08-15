"""
Обновление нескольких вузов: данные не смешиваются, сбой одного не роняет остальные.

Главная проверка здесь — про идентичность. Уникальный код поступающего единый
для всех вузов, поэтому 1645144 в СПбГУ и 1645144 в ВШЭ — один человек и одна
строка в базе. Разделяются не абитуриенты, а конкурсы: вуз зашит в код
программы, и по нему же расходятся заявки, места и вкладки.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.application.use_cases.update_lists import UpdateApplicationListsUseCase
from app.domain.models import Application, SubmissionStats
from app.infrastructure.parser import runner as runner_module
from app.infrastructure.parser.base import IUniversitySource, ParsedProgram, ProgramListing
from app.domain.universities import stable_program_code, university_of_program


def _application(program_code: str, applicant_id: str, priority: int = 1) -> Application:
    return Application(
        program_code=program_code, applicant_id=applicant_id, total_score=90,
        vi_score=90, subject1_score=0, subject2_score=0, id_achievements=0,
        target_id_achievements=0, priority=priority, consent=True,
        review_status="Участвует в конкурсе",
    )


class _StubSource(IUniversitySource):
    """Источник, отвечающий заранее заданными программами."""

    def __init__(self, university: str, programs: list[ParsedProgram], fails: bool = False):
        self.university = university
        self._programs = programs
        self._fails = fails

    def discover(self):
        if self._fails:
            raise RuntimeError("раздел приёма переехал")
        return [ProgramListing(ref=p.program_code) for p in self._programs]

    def fetch(self, listing):
        return [p for p in self._programs if p.program_code == listing.ref]

    def close(self):
        pass


def _program(university: str, name: str, applicant_ids: list[str],
             speciality: str = "38.04.02") -> ParsedProgram:
    code = stable_program_code(university, speciality, name, "очная")
    applications = [_application(code, aid) for aid in applicant_ids]
    return ParsedProgram(
        program_code=code,
        program_name=name,
        speciality_code=speciality,
        education_form="очная",
        is_international=False,
        stats=SubmissionStats(program_code=code, num_places=10,
                              num_applications=len(applications),
                              generated_at=datetime(2026, 8, 6, 17, 0)),
        applications=applications,
    )


@pytest.fixture
def sources(monkeypatch):
    """Реестр {вуз: источник}, который подставляется вместо фабрики."""
    registry: dict[str, _StubSource] = {}

    def fake_create_source(university):
        if university not in registry:
            raise ValueError(f"источник для {university} не настроен")
        return registry[university]

    monkeypatch.setattr(runner_module, "create_source", fake_create_source)
    return registry


def _update(repo, university: str) -> int:
    # parallelism=1 — разбор идёт в этом же процессе, иначе заглушку
    # пришлось бы уметь передавать между процессами
    return UpdateApplicationListsUseCase(repo=repo).execute_source(university, parallelism=1)


def test_one_code_is_one_person_in_every_university(repo, sources):
    """
    Код единый, поэтому 1645144 в ВШЭ и в МГУ — один человек: одна строка
    абитуриента и обе его заявки под ней. Раскладывать по вузам нужно не его,
    а конкурсы.
    """
    sources["hse"] = _StubSource("hse", [_program("hse", "Маркетинг", ["1645144"])])
    sources["msu"] = _StubSource("msu", [_program("msu", "Экономика", ["1645144"])])

    _update(repo, "hse")
    _update(repo, "msu")

    codes = repo.get_program_codes_by_applicant("1645144")
    assert len(codes) == 2, "обе заявки принадлежат одному человеку"
    assert {university_of_program(c) for c in codes} == {"hse", "msu"}
    # разложенных по вузам ключей больше не существует
    assert repo.get_applications_by_applicant("hse:1645144") == []


def test_catalogue_rows_carry_their_university(repo, sources):
    sources["itmo"] = _StubSource("itmo", [
        _program("itmo", "Машинное обучение", ["100"], speciality="09.04.01"),
    ])

    _update(repo, "itmo")

    programs = repo.get_programs_by_university("itmo")
    assert len(programs) == 1
    assert programs[0].code.startswith("itmo:09.04.01:")
    assert programs[0].department_code == "itmo:09.04.01"
    assert repo.get_programs_by_university("spbgu") == []


def test_two_universities_keep_their_own_programs(repo, sources):
    sources["hse"] = _StubSource("hse", [_program("hse", "Маркетинг", ["1"])])
    sources["mgimo"] = _StubSource("mgimo", [_program("mgimo", "Маркетинг", ["2"])])

    _update(repo, "hse")
    _update(repo, "mgimo")

    assert len(repo.get_programs_by_university("hse")) == 1
    assert len(repo.get_programs_by_university("mgimo")) == 1
    # одинаковое название — но это разные конкурсы, и коды разные
    assert (repo.get_programs_by_university("hse")[0].code
            != repo.get_programs_by_university("mgimo")[0].code)


def test_empty_source_aborts_without_touching_other_universities(repo, sources):
    """Пустой ответ вуза — почти всегда его сбой, а не «все забрали документы»."""
    sources["hse"] = _StubSource("hse", [_program("hse", "Маркетинг", ["1645144"])])
    _update(repo, "hse")
    before = len(repo.get_applications_by_applicant("1645144"))

    empty = _program("msu", "Экономика", [])
    sources["msu"] = _StubSource("msu", [empty])
    with pytest.raises(RuntimeError, match="ни по одной программе"):
        _update(repo, "msu")

    assert len(repo.get_applications_by_applicant("1645144")) == before


def test_one_broken_source_does_not_stop_the_others(repo, sources):
    """
    ВШЭ перенесла раздел — это не повод оставить пользователей без свежих
    данных по остальным пяти вузам.
    """
    sources["hse"] = _StubSource("hse", [], fails=True)
    sources["itmo"] = _StubSource("itmo", [_program("itmo", "Машинное обучение", ["7"])])

    report = UpdateApplicationListsUseCase(repo=repo).execute_all(["hse", "itmo"], parallelism=1)

    assert report["hse"].startswith("ошибка")
    assert report["itmo"] == "обновлено программ: 1"
    assert len(repo.get_programs_by_university("itmo")) == 1


def test_repeated_update_does_not_duplicate_programs(repo, sources):
    sources["ranepa"] = _StubSource("ranepa", [_program("ranepa", "Госуправление", ["5"])])

    _update(repo, "ranepa")
    _update(repo, "ranepa")

    assert len(repo.get_programs_by_university("ranepa")) == 1
    assert len(repo.get_applications_by_applicant("5")) == 1
