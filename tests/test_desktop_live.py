"""
Тесты live-режима десктоп-клиента (свежие личные данные по коду).

Сеть подменяется через monkeypatch (он же всё и восстанавливает, чтобы
подмена не протекла в соседние тесты). Проверяем: (1) защиту от «шторма
запросов», если фильтр applicant_code не сработал, (2) счастливый путь,
(3) мягкую деградацию при ошибках.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.infrastructure.parser.spbgu import spbgu_master_parser
from app.presentation.desktop import live

_BLOCK = (Path(__file__).parent / "fixtures" / "spbgu_speciality_block.html").read_text(encoding="utf-8")
_SPEC_ID = "4c0814a2-199a-4ed0-8695-e198b230399b"


def _meta_html(n_specialities: int) -> str:
    """Страница отчёта с заданным числом специальностей в reportMeta."""
    specs = [
        {"id": f"{_SPEC_ID[:-1]}{i}", "code": "45.04.01", "name": f"Программа {i}"}
        for i in range(n_specialities)
    ]
    meta = {
        "id": "report-1",
        "report_upload_id": "upload-1",
        "sections": [{"key": "s", "specialities": specs}],
    }
    return (
        '<div class="row" id="datetime"><div><span>05 августа 2026 г. 16:00</span></div></div>'
        '<script type="application/json" id="priem-list-02-report-meta">'
        + json.dumps(meta, ensure_ascii=False)
        + "</script>"
    )


@pytest.fixture
def fake_network(monkeypatch):
    """
    Подменяет сеть. Возвращает функцию настройки:
        fake_network(meta_html=..., blocks=...)
    и список speciality_id, по которым реально ходили за списками.
    """
    requested: list[str] = []

    def configure(meta_html, blocks):
        def fake_fetch(*_a, **_kw):
            if isinstance(meta_html, Exception):
                raise meta_html
            return meta_html

        def fake_blocks(_self, speciality_id, timeout=60):
            requested.append(speciality_id)
            return blocks

        monkeypatch.setattr(live, "fetch_report_html", fake_fetch)
        monkeypatch.setattr(
            spbgu_master_parser.SpbguMasterApplicationsParser,
            "_fetch_speciality_blocks",
            fake_blocks,
        )
        return requested

    return configure


def test_refuses_storm_when_filter_ignored(fake_network):
    """Фильтр не применён → отчёт вернул все программы → в сеть за ними не идём."""
    requested = fake_network(_meta_html(live.MAX_SPECIALITIES + 5), [])
    assert live.fetch_live_applications("1645144") is None
    assert requested == [], "не должно быть ни одного запроса за списками"


def test_fetches_when_speciality_count_is_sane(fake_network):
    requested = fake_network(_meta_html(2), [{"html": _BLOCK}])
    live.fetch_live_applications("1645144")
    assert len(requested) == 2


def test_happy_path_returns_only_my_rows(fake_network):
    fake_network(_meta_html(1), [{"html": _BLOCK}])
    result = live.fetch_live_applications("1645144")

    assert result is not None
    assert len(result.applications) == 1  # только моя строка, а не весь список
    app = result.applications[0]
    assert app.applicant_id == "1645144"
    assert (app.total_score, app.vi_score, app.priority) == (99, 99, 1)
    assert app.consent is True
    assert result.generated_at == datetime(2026, 8, 5, 16, 0)
    assert result.program_names  # имя программы подхвачено из отчёта


def test_code_absent_in_lists_returns_none(fake_network):
    fake_network(_meta_html(1), [{"html": _BLOCK}])
    assert live.fetch_live_applications("0000000") is None


def test_empty_blocks_return_none(fake_network):
    fake_network(_meta_html(1), [])
    assert live.fetch_live_applications("1645144") is None


def test_no_specialities_returns_none(fake_network):
    fake_network(_meta_html(0), [{"html": _BLOCK}])
    assert live.fetch_live_applications("1645144") is None


def test_network_failure_is_soft(fake_network):
    """Ошибка сети не должна вылетать наружу — UI просто останется на снапшоте."""
    fake_network(OSError("нет сети"), [])
    assert live.fetch_live_applications("1645144") is None


def test_broken_report_html_is_soft(fake_network):
    fake_network("<html>без reportMeta</html>", [])
    assert live.fetch_live_applications("1645144") is None


@pytest.mark.parametrize("code", ["", "   ", None])
def test_blank_code_returns_none_without_network(fake_network, code):
    requested = fake_network(_meta_html(1), [{"html": _BLOCK}])
    assert live.fetch_live_applications(code) is None
    assert requested == []
