"""
Тесты рендеринга Jinja-шаблонов веб-интерфейса.

Тестов view.py недостаточно: шаблон может молча не показать данные (как было
с ключом `items`, который в Jinja резолвится в метод `dict.items`). Здесь
шаблоны рендерятся по-настоящему и проверяется, что объяснение доходит до HTML.
"""
from __future__ import annotations

from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2", reason="шаблоны рендерит Jinja2")

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from app.application.use_cases.get_applicant_forecast import (  # noqa: E402
    ExamState,
    ExamStatus,
    ForecastItem,
    ForecastResult,
    Reason,
    ReasonKind,
)
from app.presentation.web.view import to_view  # noqa: E402

_TEMPLATES = Path("app/presentation/web/templates").resolve()


@pytest.fixture
def env():
    e = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    # base.html зовёт url_for из Starlette; вне приложения подставляем заглушку
    e.globals["url_for"] = lambda name, **kw: f"/{name}/{kw.get('path', '')}"
    return e


def _result(reasons=None, notes=None) -> ForecastResult:
    return ForecastResult(
        applicant_id="A1",
        university="spbgu",
        items=[ForecastItem(
            program_code="701",
            program_name="Матмод",
            department_code="01.04.02",
            prob_cond=0.4167,
            q90=221.7,
            q95=225.8,
            exam=ExamStatus(state=ExamState.NOT_PUBLISHED),
            reasons=reasons or [],
        )],
        fail_cond=0.56,
        last_update=None,
        notes=notes or [],
    )


def test_reasons_reach_the_html(env):
    html = env.get_template("result.html").render(
        view=to_view(_result(
            reasons=[
                Reason(ReasonKind.GOOD, "По баллу вы 1-й из 10."),
                Reason(ReasonKind.BAD, "Ещё 5 человек пока без баллов."),
            ],
            notes=[Reason(ReasonKind.NEUTRAL, "Проходной показан вилкой.")],
        )),
        not_found=None,
    )

    assert "Почему такой шанс?" in html
    assert "По баллу вы 1-й из 10." in html
    assert "why-good" in html and "why-bad" in html
    assert "Проходной показан вилкой." in html


def test_no_why_block_without_reasons(env):
    """Пустое объяснение не должно оставлять на карточке пустую «гармошку»."""
    html = env.get_template("result.html").render(view=to_view(_result()), not_found=None)

    assert "Почему такой шанс?" not in html
    assert "Матмод" in html          # само направление на месте
    assert "41.7%" in html


def test_how_page_answers_the_cutoff_question(env):
    """
    «Понижается ли проходной, если кто-то уходит» — частый вопрос;
    ответ должен быть в справке, а не только в голове у автора модели.
    """
    html = env.get_template("how.html").render()

    assert "Понижается ли проходной" in html
    assert "самого слабого из зачисленных" in html
