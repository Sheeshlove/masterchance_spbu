"""
Тест разбора block.html СПбГУ (ответ POST /api/reports/priem-list-02/data)
в SubmissionStats + [Application], и парсинга даты формирования из #datetime.

Офлайн, на фикстуре tests/fixtures/spbgu_speciality_block.html. Сети/pydantic
не требует (только stdlib + app.domain dataclasses).
Запуск: `python tests/test_spbgu_list.py` или `pytest`.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # корень репо в sys.path

from app.infrastructure.parser.spbgu.spbgu_master_parser import (  # noqa: E402
    block_to_records,
    parse_report_datetime,
)

_BLOCK = (Path(__file__).parent / "fixtures" / "spbgu_speciality_block.html").read_text(encoding="utf-8")
_CODE = "spbgu:4c0814a2-199a-4ed0-8695-e198b230399b"
_GEN = datetime(2026, 8, 5, 16, 0)


def test_num_places_and_count():
    stats, apps = block_to_records(_BLOCK, _CODE, _GEN)
    assert stats.program_code == _CODE
    assert stats.num_places == 5          # «Количество бюджетных мест: 5»
    assert stats.num_applications == len(apps) == 4
    assert stats.generated_at == _GEN


def test_row_mapping():
    _, apps = block_to_records(_BLOCK, _CODE, _GEN)
    by_id = {a.applicant_id: a for a in apps}

    a = by_id["1645144"]                  # 1-я строка: 99, согласие Да, приоритет 1
    assert (a.total_score, a.vi_score, a.subject1_score) == (99, 99, 99)
    assert a.subject2_score == 0 and a.id_achievements == 0 and a.target_id_achievements == 0
    assert a.consent is True and a.priority == 1
    assert a.review_status == "Участвует в конкурсе"

    b = by_id["2344199"]                  # согласие Нет, приоритет 3
    assert b.consent is False and b.priority == 3 and b.total_score == 95

    z = by_id["1022176"]                  # нулевые баллы (ещё не сдавал)
    assert z.total_score == 0 and z.vi_score == 0 and z.consent is False and z.priority == 21

    r = by_id["2101859"]                  # иной статус
    assert r.review_status == "На рассмотрении"


def test_all_ids_and_consent_shape():
    _, apps = block_to_records(_BLOCK, _CODE, _GEN)
    assert all(a.program_code == _CODE for a in apps)
    assert all(isinstance(a.consent, bool) for a in apps)
    assert all(a.applicant_id.isdigit() for a in apps)


def test_parse_report_datetime():
    html = '<div class="row" id="datetime"><div class="col text-center">' \
           '<span class="">  05 августа 2026 г. 16:00  </span></div></div>'
    assert parse_report_datetime(html) == datetime(2026, 8, 5, 16, 0)
    assert parse_report_datetime("<div>no datetime here</div>") is None


if __name__ == "__main__":
    test_num_places_and_count()
    test_row_mapping()
    test_all_ids_and_consent_shape()
    test_parse_report_datetime()
    print("OK")
