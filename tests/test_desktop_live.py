"""
Тесты live-режима десктоп-клиента (свежие личные данные по коду).

Сеть подменяется: проверяем (1) защиту от «шторма запросов», если фильтр
applicant_code не сработал, (2) счастливый путь, (3) мягкую деградацию.
Офлайн, без обращения к серверу вуза.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infrastructure.parser.spbgu import spbgu_master_parser  # noqa: E402
from app.presentation.desktop import live  # noqa: E402

_BLOCK = (Path(__file__).parent / "fixtures" / "spbgu_speciality_block.html").read_text(encoding="utf-8")
_SPEC_ID = "4c0814a2-199a-4ed0-8695-e198b230399b"


def _meta_html(n_specialities: int) -> str:
    """Страница отчёта с заданным числом специальностей в reportMeta."""
    specs = [
        {"id": f"{_SPEC_ID[:-1]}{i}", "code": "45.04.01", "name": f"Программа {i}"}
        for i in range(n_specialities)
    ]
    meta = {"id": "report-1", "report_upload_id": "upload-1",
            "sections": [{"key": "s", "specialities": specs}]}
    return (
        '<div class="row" id="datetime"><div><span>05 августа 2026 г. 16:00</span></div></div>'
        '<script type="application/json" id="priem-list-02-report-meta">'
        + json.dumps(meta, ensure_ascii=False) + "</script>"
    )


def _patch(monkey_html, monkey_blocks):
    live.fetch_report_html = monkey_html
    spbgu_master_parser.SpbguMasterApplicationsParser._fetch_speciality_blocks = monkey_blocks


def test_refuses_storm_when_filter_ignored():
    """Фильтр не применён → отчёт вернул все программы → в сеть за ними не идём."""
    called = []
    _patch(
        lambda *a, **k: _meta_html(live.MAX_SPECIALITIES + 5),
        lambda self, sid, timeout=60: called.append(sid) or [],
    )
    assert live.fetch_live_applications("1645144") is None
    assert called == [], "не должно быть ни одного запроса за списками"


def test_happy_path_returns_only_my_rows():
    _patch(
        lambda *a, **k: _meta_html(1),
        lambda self, sid, timeout=60: [{"html": _BLOCK}],
    )
    res = live.fetch_live_applications("1645144")
    assert res is not None
    assert len(res.applications) == 1              # только моя строка, не весь список
    app = res.applications[0]
    assert app.applicant_id == "1645144"
    assert (app.total_score, app.vi_score, app.priority) == (99, 99, 1)
    assert app.consent is True
    assert res.generated_at == datetime(2026, 8, 5, 16, 0)
    assert res.program_names  # имя программы подхвачено из отчёта


def test_code_absent_returns_none():
    _patch(
        lambda *a, **k: _meta_html(1),
        lambda self, sid, timeout=60: [{"html": _BLOCK}],
    )
    assert live.fetch_live_applications("0000000") is None


def test_network_failure_is_soft():
    def boom(*a, **k):
        raise OSError("нет сети")

    _patch(boom, lambda self, sid, timeout=60: [])
    assert live.fetch_live_applications("1645144") is None   # не бросает наружу
    assert live.fetch_live_applications("") is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
