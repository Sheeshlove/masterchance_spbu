"""
Регрессия на реальную потерю данных.

Источник вернул пустые списки по всем программам, а пайплайн на каждую делал
«удалить старое → записать новое». В итоге заявки были стёрты, хотя по ним уже
был посчитан Monte-Carlo, и приложение отвечало «заявок не найдено» на любой код.
"""
import pytest

from app.application.use_cases.update_lists import UpdateApplicationListsUseCase
from app.domain.models import Application, SubmissionStats
from datetime import datetime


def _app(program_code: str, applicant_id: str, priority: int = 1) -> Application:
    return Application(
        program_code=program_code, applicant_id=applicant_id, total_score=90,
        vi_score=90, subject1_score=0, subject2_score=0, id_achievements=0,
        target_id_achievements=0, priority=priority, consent=True,
        review_status="Участвует в конкурсе",
    )


def _stats(program_code: str, n: int) -> SubmissionStats:
    return SubmissionStats(program_code=program_code, num_places=10,
                           num_applications=n, generated_at=datetime(2026, 8, 6, 17, 0))


@pytest.fixture
def existing(seed, repo):
    """База с уже собранными заявками — то, что жалко потерять."""
    seed.program("spbgu:p1", name="Первая", university="spbgu")
    seed.program("spbgu:p2", name="Вторая", university="spbgu")
    seed.applicant("1037225", university="spbgu")
    seed.application("spbgu:p1", "1037225", priority=1)
    seed.application("spbgu:p2", "1037225", priority=2)
    seed.commit()
    return repo


def _run(repo, results, monkeypatch):
    """Запустить синхронизацию с подменённым парсингом."""
    monkeypatch.setattr(
        "app.application.use_cases.update_lists.parse_programs_in_parallel",
        lambda **kw: results,
    )
    UpdateApplicationListsUseCase(repo=repo).execute_parallel(university="spbgu")


def test_empty_source_does_not_wipe_existing_applications(existing, monkeypatch):
    """Все списки пришли пустыми → прежние заявки должны остаться на месте."""
    empty = {"spbgu:p1": (_stats("spbgu:p1", 0), []),
             "spbgu:p2": (_stats("spbgu:p2", 0), [])}

    with pytest.raises(RuntimeError, match="Ни по одной программе"):
        _run(existing, empty, monkeypatch)

    # данные целы — именно это раньше и терялось
    assert existing.get_program_codes_by_applicant("1037225") == ["spbgu:p1", "spbgu:p2"]
    assert len(existing.get_applications_by_applicant("1037225")) == 2


def test_partial_emptiness_keeps_the_empty_one_and_updates_the_rest(existing, monkeypatch):
    """Одна программа пустая, другая с данными — теряем только «пустую» новизну."""
    results = {
        "spbgu:p1": (_stats("spbgu:p1", 0), []),                       # сбой источника
        "spbgu:p2": (_stats("spbgu:p2", 2), [_app("spbgu:p2", "999"),
                                             _app("spbgu:p2", "1037225", priority=3)]),
    }
    _run(existing, results, monkeypatch)

    # по p1 сохранилась прежняя заявка
    assert "spbgu:p1" in existing.get_program_codes_by_applicant("1037225")
    # по p2 приехали свежие
    assert len(existing.get_applications_by_applicant("999")) == 1


def test_normal_update_replaces_applications(existing, monkeypatch):
    """Обычный случай: непустые списки заменяют старые."""
    results = {
        "spbgu:p1": (_stats("spbgu:p1", 1), [_app("spbgu:p1", "555")]),
        "spbgu:p2": (_stats("spbgu:p2", 1), [_app("spbgu:p2", "555")]),
    }
    _run(existing, results, monkeypatch)

    assert sorted(existing.get_program_codes_by_applicant("555")) == ["spbgu:p1", "spbgu:p2"]
    # прежний абитуриент из этих списков исчез — это ожидаемая замена
    assert existing.get_program_codes_by_applicant("1037225") == []


def test_missing_results_also_keep_data(existing, monkeypatch):
    """Воркер вообще не вернул результата по программам — данные не трогаем."""
    with pytest.raises(RuntimeError):
        _run(existing, {}, monkeypatch)
    assert len(existing.get_applications_by_applicant("1037225")) == 2
