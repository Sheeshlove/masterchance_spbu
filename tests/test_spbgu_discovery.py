"""
Тест discovery программ магистратуры СПбГУ из отчёта PriemList02.

Работает офлайн на фикстуре tests/fixtures/spbgu_report_meta.html (усечённый
reportMeta реального отчёта). Сети/pydantic/selenium не требует.
Запуск: `pytest tests/test_spbgu_discovery.py` или `python tests/test_spbgu_discovery.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # корень репо в sys.path

from app.infrastructure.parser.spbgu.spbgu_programs import (  # noqa: E402
    extract_report_meta,
    parse_report_meta,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "spbgu_report_meta.html"


def _html() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def test_extract_report_meta_has_ids():
    meta = extract_report_meta(_html())
    assert meta["id"]
    assert meta["report_upload_id"]
    assert len(meta["sections"]) == 2


def test_parse_report_meta_maps_programs():
    progs = parse_report_meta(_html())
    # 2 секции × 2 специальности в фикстуре
    assert len(progs) == 4
    # коды уникальны и неймспейснуты UUID специальности
    assert all(p["code"].startswith("spbgu:") for p in progs)
    assert len({p["code"] for p in progs}) == len(progs)
    # у каждой программы есть код направления и list_ref (speciality uuid)
    for p in progs:
        assert p["department_code"]
        assert p["list_ref"] and p["code"].endswith(p["list_ref"])
        assert isinstance(p["is_international"], bool)


if __name__ == "__main__":
    test_extract_report_meta_has_ids()
    test_parse_report_meta_maps_programs()
    print("OK")
