"""
Поиск по коду в нескольких вузах и раскладка результата по вкладкам.

Код абитуриента вуз выдаёт свой, поэтому у одного человека их несколько, а один
и тот же код может встретиться в двух вузах у разных людей. Ни то, ни другое не
должно приводить к общему списку программ: у каждого вуза свой конкурс, свои
места и свой «пролетел».
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.application.use_cases.get_applicant_forecast import GetApplicantForecastUseCase
from app.presentation.web.view import to_view

jinja2 = pytest.importorskip("jinja2", reason="шаблоны рендерит Jinja2")
from jinja2 import Environment, FileSystemLoader  # noqa: E402

_TEMPLATES = Path("app/presentation/web/templates").resolve()


@pytest.fixture
def two_universities(seed):
    """Один человек с кодом 1000004 в СПбГУ и другой с тем же кодом в ВШЭ."""
    seed.program("spbgu:38.04.02:aaa", name="Матмод", department_code="spbgu:01.04.02",
                 university="spbgu")
    seed.applicant("spbgu:1000004", university="spbgu")
    seed.application("spbgu:38.04.02:aaa", "spbgu:1000004", priority=1, total_score=90,
                     vi_score=90, consent=True)
    seed.stats("spbgu:38.04.02:aaa", num_places=10)
    seed.probability("spbgu:1000004", "spbgu:38.04.02:aaa", 0.5)
    seed.diagnostics("spbgu:1000004", p_excluded=0.0, p_fail_when_included=0.5)

    seed.program("hse:38.04.01:bbb", name="Прикладная экономика",
                 department_code="hse:38.04.01", university="hse")
    seed.applicant("hse:1000004", university="hse")
    seed.application("hse:38.04.01:bbb", "hse:1000004", priority=1, total_score=88,
                     vi_score=88, consent=True)
    seed.stats("hse:38.04.01:bbb", num_places=15)
    seed.probability("hse:1000004", "hse:38.04.01:bbb", 0.8)
    seed.diagnostics("hse:1000004", p_excluded=0.0, p_fail_when_included=0.2)
    seed.commit()


def test_one_code_found_in_two_universities_gives_two_forecasts(repo, two_universities):
    results = GetApplicantForecastUseCase(repo).execute_all("1000004")

    assert [r.university for r in results] == ["spbgu", "hse"]
    assert [len(r.items) for r in results] == [1, 1]
    # программы не смешались в один список
    assert results[0].items[0].program_name == "Матмод"
    assert results[1].items[0].program_name == "Прикладная экономика"


def test_forecasts_keep_their_own_fail_percentage(repo, two_universities):
    results = GetApplicantForecastUseCase(repo).execute_all("1000004")
    assert results[0].fail_cond == pytest.approx(0.5)
    assert results[1].fail_cond == pytest.approx(0.2)


def test_prefix_is_not_shown_to_the_user(repo, two_universities):
    result = GetApplicantForecastUseCase(repo).execute_all("1000004")[0]
    assert result.applicant_id == "1000004"


def test_several_codes_at_once(repo, seed):
    """У человека свой код в каждом вузе — поле принимает их через запятую."""
    seed.program("spbgu:38.04.02:aaa", name="Матмод", department_code="spbgu:01.04.02",
                 university="spbgu")
    seed.applicant("spbgu:1000004", university="spbgu")
    seed.application("spbgu:38.04.02:aaa", "spbgu:1000004", priority=1)
    seed.program("hse:38.04.01:bbb", name="Экономика", department_code="hse:38.04.01",
                 university="hse")
    seed.applicant("hse:777", university="hse")
    seed.application("hse:38.04.01:bbb", "hse:777", priority=1)
    seed.commit()

    results = GetApplicantForecastUseCase(repo).execute_all("1000004, 777")

    assert [r.university for r in results] == ["spbgu", "hse"]


def test_unknown_code_gives_nothing(repo, two_universities):
    assert GetApplicantForecastUseCase(repo).execute_all("нет-такого") == []


def test_single_code_lookup_still_works(repo, two_universities):
    """Старый вход (бот, десктоп) продолжает получать один прогноз."""
    result = GetApplicantForecastUseCase(repo).execute("1000004")
    assert result is not None and result.university == "spbgu"


def test_legacy_snapshot_without_prefixes_is_still_found(repo, seed):
    """
    Снапшоты, собранные до разделения по вузам, лежат у людей на дисках —
    код без префикса обязан находиться и в них.
    """
    seed.program("spbgu:p1", name="Матмод", department_code="spbgu:01.04.02")
    seed.applicant("1037225")
    seed.application("spbgu:p1", "1037225", priority=1)
    seed.commit()

    results = GetApplicantForecastUseCase(repo).execute_all("1037225")

    assert len(results) == 1
    assert results[0].applicant_id == "1037225"


# ── вкладки на странице ────────────────────────────────────────────────────
@pytest.fixture
def env():
    e = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    e.globals["url_for"] = lambda name, **kw: f"/{name}/{kw.get('path', '')}"
    return e


def test_each_university_renders_as_its_own_tab(repo, two_universities, env):
    view = to_view(GetApplicantForecastUseCase(repo).execute_all("1000004"))
    html = env.get_template("result.html").render(view=view, not_found=None)

    # по вкладке на вуз: переключатель, подпись и панель
    assert html.count('class="tab-panel"') == 2
    assert html.count('class="tab-label"') == 2
    assert 'for="uni-tab-1"' in html and 'for="uni-tab-2"' in html

    labels = [chunk.split("</label>")[0] for chunk in html.split('class="tab-label"')[1:]]
    assert "СПбГУ" in labels[0] and "ВШЭ" in labels[1]

    # первая вкладка открыта по умолчанию, вторая — нет
    first, second = html.split('id="uni-tab-1"')[1], html.split('id="uni-tab-2"')[1]
    assert "checked" in first.split(">")[0]
    assert "checked" not in second.split(">")[0]


def test_programs_of_different_universities_are_not_mixed(repo, two_universities, env):
    view = to_view(GetApplicantForecastUseCase(repo).execute_all("1000004"))
    html = env.get_template("result.html").render(view=view, not_found=None)

    spbgu_panel, hse_panel = html.split('class="tab-panel"')[1:3]
    assert "Матмод" in spbgu_panel and "Прикладная экономика" not in spbgu_panel
    assert "Прикладная экономика" in hse_panel and "Матмод" not in hse_panel


def test_single_university_still_renders_a_tab(repo, seed, env):
    seed.program("spbgu:p1", name="Матмод", department_code="spbgu:01.04.02")
    seed.applicant("spbgu:1000004", university="spbgu")
    seed.application("spbgu:p1", "spbgu:1000004", priority=1)
    seed.commit()

    view = to_view(GetApplicantForecastUseCase(repo).execute_all("1000004"))
    html = env.get_template("result.html").render(view=view, not_found=None)

    assert html.count('class="tab-panel"') == 1
    assert "СПбГУ" in html
