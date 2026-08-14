"""
Источник открытых списков: обход оглавления и разбор страницы списка.

Сеть здесь не нужна и не используется — вместо неё подставляются сохранённые
страницы. Проверяется то, что от сети не зависит: какие ссылки движок считает
списками, как из страницы получаются программы и что коды разных вузов не
пересекаются.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.infrastructure.parser.base import ProgramListing
from app.infrastructure.parser.openlists import source as source_module
from app.infrastructure.parser.openlists.crawl import Fetched
from app.infrastructure.parser.openlists.source import OpenListsSource, SourceSpec
from app.infrastructure.parser.openlists.specs import default_spec

_FIXTURES = Path(__file__).parent / "fixtures" / "openlists"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def pages(monkeypatch):
    """Подменяет сеть словарём {url: тело ответа}."""
    served: dict[str, str] = {}

    def fake_fetch(url, timeout=60, data=None):
        if url not in served:
            raise OSError(f"страница не отвечает: {url}")
        body = served[url]
        content_type = "application/json" if body.lstrip()[:1] in "{[" else "text/html"
        return Fetched(url=url, raw=body.encode("utf-8"),
                       content_type=content_type, charset="utf-8")

    monkeypatch.setattr(source_module, "fetch", fake_fetch)
    return served


def _source(university: str = "hse", **kw) -> OpenListsSource:
    spec = SourceSpec(
        university=university,
        index_urls=("https://example.edu/priem/",),
        list_pattern=r"(?:spisk|rating|список|рейтинг|конкурсн)",
        **kw,
    )
    return OpenListsSource(spec)


# ── discovery ──────────────────────────────────────────────────────────────
def test_only_list_links_are_taken_from_the_index(pages):
    pages["https://example.edu/priem/"] = _fixture("list_index.html")

    refs = [item.ref for item in _source().discover()]

    assert refs == [
        "https://example.edu/priem/magistratura/spiski/menedzhment/",
        "https://example.edu/priem/magistratura/spiski/ekonomika/",
        "https://example.edu/priem/magistratura/rating/pmi.html",
    ]
    assert not any("news" in ref or "mailto" in ref for ref in refs), \
        "новости и почта — не списки"


def test_relative_links_become_absolute(pages):
    pages["https://example.edu/priem/"] = _fixture("list_index.html")
    assert all(item.ref.startswith("https://") for item in _source().discover())


def test_unreachable_index_does_not_raise(pages):
    """Вуз перенёс раздел — обход возвращает пусто, а не роняет обновление."""
    assert _source().discover() == []


def test_max_lists_is_respected(pages):
    pages["https://example.edu/priem/"] = _fixture("list_index.html")
    assert len(_source(max_lists=2).discover()) == 2


# ── разбор страницы списка ─────────────────────────────────────────────────
def test_page_with_one_list_gives_one_program(pages):
    url = "https://example.edu/priem/list.html"
    pages[url] = _fixture("list_simple.html")

    programs = _source("hse").fetch(ProgramListing(ref=url))

    assert len(programs) == 1
    program = programs[0]
    assert program.program_name == "Маркетинг и рыночная аналитика"
    assert program.speciality_code == "38.04.02"
    assert program.program_code.startswith("hse:38.04.02:")
    assert program.stats.num_places == 25
    assert program.stats.num_applications == 4
    assert all(a.program_code == program.program_code for a in program.applications)


def test_publication_date_is_taken_from_the_page(pages):
    url = "https://example.edu/priem/list.html"
    pages[url] = _fixture("list_simple.html")

    program = _source().fetch(ProgramListing(ref=url))[0]

    assert program.stats.generated_at.strftime("%d.%m.%Y %H:%M") == "05.08.2026 16:00"


def test_two_lists_on_a_page_become_two_programs(pages):
    url = "https://example.edu/priem/faculty.html"
    pages[url] = _fixture("list_two_programs.html")

    programs = _source("msu").fetch(ProgramListing(ref=url))

    assert sorted(p.program_name for p in programs) == ["Анализ данных", "Финансовая экономика"]
    assert len({p.program_code for p in programs}) == 2, "у разных конкурсов разные коды"
    assert all(p.program_code.startswith("msu:") for p in programs)


def test_the_same_program_in_two_universities_gets_different_codes(pages):
    url = "https://example.edu/priem/list.html"
    pages[url] = _fixture("list_simple.html")

    hse = _source("hse").fetch(ProgramListing(ref=url))[0]
    msu = _source("msu").fetch(ProgramListing(ref=url))[0]

    assert hse.program_code != msu.program_code, \
        "одинаковое название в разных вузах — это разные конкурсы"


def test_json_answer_is_parsed_too(pages):
    url = "https://example.edu/api/rating/42"
    pages[url] = json.dumps({
        "result": {
            "program_name": "Образовательная программа «Машинное обучение», 09.04.01, очная, 20 мест",
            "rating": [
                {"position": 1, "unique_code": "1645144", "total_score": 96,
                 "priority": 1, "agreement": True},
                {"position": 2, "unique_code": "1700231", "total_score": 88,
                 "priority": 2, "agreement": False},
            ],
        }
    }, ensure_ascii=False)

    programs = _source("itmo").fetch(ProgramListing(ref=url))

    assert len(programs) == 1
    assert programs[0].program_name == "Машинное обучение"
    assert programs[0].program_code.startswith("itmo:09.04.01:")
    assert [a.applicant_id for a in programs[0].applications] == ["1645144", "1700231"]


def test_page_without_lists_gives_nothing(pages):
    url = "https://example.edu/priem/news.html"
    pages[url] = "<html><body><h1>Новости приёма</h1><p>Ждём вас!</p></body></html>"

    assert _source().fetch(ProgramListing(ref=url)) == []


def test_unreachable_list_page_is_skipped(pages):
    assert _source().fetch(ProgramListing(ref="https://example.edu/gone.html")) == []


# ── описания источников ────────────────────────────────────────────────────
@pytest.mark.parametrize("university", ["hse", "itmo", "mgimo", "msu", "ranepa"])
def test_every_new_university_has_a_source_description(university):
    spec = default_spec(university)
    assert spec.university == university
    assert spec.index_urls and spec.index_urls[0].startswith("https://")
    assert spec.list_pattern, "иначе обход примет за список любую страницу"


def test_index_url_can_be_overridden_without_touching_the_code():
    """Раздел приёма вузы переносят каждый сезон — адрес меняется в .env."""
    spec = default_spec("hse", "https://example.edu/2027/lists")
    assert spec.index_urls == ("https://example.edu/2027/lists",)
