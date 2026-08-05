"""
Тест записи каталога СПбГУ в базу (seed_catalog).

Сеть подменяется, БД настоящая (SQLite в памяти из фикстур), поэтому
проверяется ровно то, что потом прочитает update_lists.py.
"""
from app.domain.models import Program
from seed_spbgu_programs import SPBGU, seed_catalog


def _discovered(n: int = 3) -> list:
    """Как это возвращает discover_programs(): имя = НАЗВАНИЕ НАПРАВЛЕНИЯ."""
    return [
        {
            "code": f"spbgu:uuid-{i}",
            "name": "Прикладная математика и информатика",
            "department_code": "01.04.02",
            "is_international": i == 2,
            "list_ref": f"uuid-{i}",
        }
        for i in range(n)
    ]


class FakeParser:
    """Отдаёт настоящие имена программ, как шапка блока."""

    def __init__(self, names=None, boom_on=None):
        self._names = names or {}
        self._boom_on = boom_on or set()
        self.calls = []

    def fetch_program_info(self, speciality_id, timeout=60):
        self.calls.append(speciality_id)
        if speciality_id in self._boom_on:
            raise OSError("нет сети")
        name = self._names.get(speciality_id)
        return {"program_name": name} if name else {}


def test_writes_programs_departments_institutes(repo, session):
    counts = seed_catalog(_discovered(3), repo)
    repo.commit()

    programs = repo.get_programs_by_university(SPBGU)
    assert len(programs) == 3
    assert counts == {"programs": 3, "departments": 1, "institutes": 1,
                      "named": 0, "fallback": 0}
    # одно направление на три программы — кафедра создаётся один раз
    assert {p.department_code for p in programs} == {"spbgu:01.04.02"}


def test_programs_are_tagged_with_university(repo):
    seed_catalog(_discovered(2), repo)
    repo.commit()
    assert repo.get_programs_by_university("spbpu") == []
    assert len(repo.get_programs_by_university(SPBGU)) == 2


def test_international_flag_is_preserved(repo):
    seed_catalog(_discovered(3), repo)
    repo.commit()
    by_code = {p.code: p for p in repo.get_programs_by_university(SPBGU)}
    assert by_code["spbgu:uuid-2"].is_international is True
    assert by_code["spbgu:uuid-0"].is_international is False


def test_real_program_names_are_fetched(repo):
    parser = FakeParser(names={
        "uuid-0": "Математическое моделирование",
        "uuid-1": "Анализ данных",
        "uuid-2": "Криптография",
    })
    counts = seed_catalog(_discovered(3), repo, parser)
    repo.commit()

    names = sorted(p.name for p in repo.get_programs_by_university(SPBGU))
    assert names == ["Анализ данных", "Криптография", "Математическое моделирование"]
    assert counts["named"] == 3 and counts["fallback"] == 0
    assert parser.calls == ["uuid-0", "uuid-1", "uuid-2"]


def test_falls_back_to_speciality_name_when_fetch_fails(repo):
    """Сеть отвалилась на одной программе — остальные всё равно записываются."""
    parser = FakeParser(names={"uuid-0": "Математическое моделирование"},
                        boom_on={"uuid-1"})
    counts = seed_catalog(_discovered(3), repo, parser)
    repo.commit()

    by_code = {p.code: p for p in repo.get_programs_by_university(SPBGU)}
    assert by_code["spbgu:uuid-0"].name == "Математическое моделирование"
    assert by_code["spbgu:uuid-1"].name == "Прикладная математика и информатика"
    assert len(by_code) == 3
    assert counts["named"] == 1 and counts["fallback"] == 2


def test_seeding_twice_does_not_duplicate(repo):
    """Скрипт должен быть безопасен для повторного запуска (merge, не insert)."""
    seed_catalog(_discovered(3), repo)
    repo.commit()
    seed_catalog(_discovered(3), repo)
    repo.commit()

    assert len(repo.get_programs_by_university(SPBGU)) == 3


def test_does_not_collide_with_existing_spbpu_catalog(repo, seed):
    """
    Ключевая проверка: у вузов совпадают федеральные коды направлений.
    Каталог СПбГУ не должен перетереть кафедру Политеха с тем же кодом.
    """
    seed.program("701", name="Матмод Политеха", department_code="01.04.02",
                 university="spbpu")
    seed.commit()

    seed_catalog(_discovered(2), repo)
    repo.commit()

    spbpu = repo.get_programs_by_university("spbpu")
    assert len(spbpu) == 1
    assert spbpu[0].department_code == "01.04.02"          # код Политеха не тронут
    spbgu = repo.get_programs_by_university(SPBGU)
    assert {p.department_code for p in spbgu} == {"spbgu:01.04.02"}


def test_update_lists_would_see_the_catalog(repo):
    """
    update_lists.py берёт программы через get_programs_by_university —
    именно этого раньше и не хватало для --university=spbgu.
    """
    seed_catalog(_discovered(4), repo)
    repo.commit()

    codes = [p.code for p in repo.get_programs_by_university(SPBGU)]
    assert len(codes) == 4
    assert all(c.startswith("spbgu:") for c in codes)
    # именно этот код парсер превращает обратно в speciality_id
    assert all(isinstance(p, Program) for p in repo.get_programs_by_university(SPBGU))
