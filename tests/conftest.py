"""
Общие фикстуры: SQLite в памяти со схемой проекта и удобный сидер.

Тесты не ходят в сеть и не трогают data/master.db — каждая проверка получает
чистую БД, которую сама наполняет ровно теми строками, что ей нужны.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.infrastructure.db.models import (
    AdmissionDiagnosticsModel,
    AdmissionProbabilityModel,
    ApplicantModel,
    ApplicationModel,
    Base,
    DepartmentModel,
    ExamSessionModel,
    InstituteModel,
    ProgramModel,
    ProgramQuantileModel,
    SubmissionStatsModel,
)
from app.infrastructure.db.repositories.program_repository import ProgramRepository

MSK = ZoneInfo("Europe/Moscow")


def msk_now_naive() -> datetime:
    """«Сейчас» в МСК без tz — в таком виде даты лежат в БД."""
    return datetime.now(MSK).replace(tzinfo=None)


class Seeder:
    """Маленький помощник, чтобы тесты читались как описание данных."""

    def __init__(self, session):
        self.session = session
        self._institutes: set[str] = set()
        self._departments: set[str] = set()

    def program(
        self,
        code: str,
        name: str = "Программа",
        department_code: str = "01.04.02",
        university: str = "spbgu",
        is_international: bool = False,
    ) -> None:
        inst_code = department_code.split(".")[0]
        if inst_code not in self._institutes:
            self.session.add(InstituteModel(code=inst_code, name=f"Институт {inst_code}"))
            self._institutes.add(inst_code)
        if department_code not in self._departments:
            self.session.add(
                DepartmentModel(
                    code=department_code,
                    name=f"Кафедра {department_code}",
                    institute_code=inst_code,
                )
            )
            self._departments.add(department_code)
        self.session.add(
            ProgramModel(
                code=code,
                name=name,
                department_code=department_code,
                is_ino=False,
                is_international=is_international,
                university=university,
            )
        )

    def applicant(self, applicant_id: str, university: str = "spbgu") -> None:
        self.session.add(ApplicantModel(id=applicant_id, university=university))

    def application(
        self,
        program_code: str,
        applicant_id: str,
        priority: int = 1,
        total_score: int = 0,
        vi_score: int = 0,
        subject1_score: int = 0,
        subject2_score: int = 0,
        id_achievements: int = 0,
        target_id_achievements: int = 0,
        consent: bool = False,
        review_status: str = "Участвует в конкурсе",
    ) -> None:
        self.session.add(
            ApplicationModel(
                program_code=program_code,
                applicant_id=applicant_id,
                total_score=total_score,
                vi_score=vi_score,
                subject1_score=subject1_score,
                subject2_score=subject2_score,
                id_achievements=id_achievements,
                target_id_achievements=target_id_achievements,
                priority=priority,
                consent=consent,
                review_status=review_status,
            )
        )

    def probability(self, applicant_id: str, program_code: str, probability: float) -> None:
        self.session.add(
            AdmissionProbabilityModel(
                applicant_id=applicant_id, program_code=program_code, probability=probability
            )
        )

    def diagnostics(self, applicant_id: str, p_excluded: float, p_fail_when_included: float,
                    university: str = "spbgu") -> None:
        # Вуз — часть ключа: код абитуриента единый, а «пролетел» считается
        # внутри конкурса, и у одного человека их столько, во сколько вузов он
        # подал документы.
        self.session.add(
            AdmissionDiagnosticsModel(
                applicant_id=applicant_id,
                university=university,
                p_excluded=p_excluded,
                p_fail_when_included=p_fail_when_included,
            )
        )

    def quantiles(self, program_code: str, q90: float, q95: float) -> None:
        self.session.add(ProgramQuantileModel(program_code=program_code, q90=q90, q95=q95))

    def stats(
        self,
        program_code: str,
        num_places: int = 10,
        num_applications: int = 100,
        generated_at: datetime | None = None,
    ) -> None:
        self.session.add(
            SubmissionStatsModel(
                program_code=program_code,
                num_places=num_places,
                num_applications=num_applications,
                generated_at=generated_at or datetime(2026, 6, 22, 9, 0),
            )
        )

    def exam(self, program_code: str, dt: datetime) -> None:
        self.session.add(ExamSessionModel(program_code=program_code, exam_code="ВИ-1", dt=dt))

    def exam_in_days(self, program_code: str, days: float) -> None:
        """Экзамен через `days` дней от «сейчас» (отрицательное — в прошлом)."""
        self.exam(program_code, msk_now_naive() + timedelta(days=days))

    def commit(self) -> None:
        self.session.commit()


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture
def seed(session) -> Seeder:
    return Seeder(session)


@pytest.fixture
def repo(session) -> ProgramRepository:
    return ProgramRepository(session)


@pytest.fixture
def web_client():
    """
    HTTP-клиент поверх сайта.

    Приложение поднимается на той же временной БД, что и остальные тесты
    (её подставляет корневой conftest.py через DATABASE_URL), поэтому запросы
    ходят по настоящим маршрутам и шаблонам, а не по их пересказу.
    """
    pytest.importorskip("httpx", reason="TestClient требует httpx")
    from fastapi.testclient import TestClient

    from app.presentation.web.app import app

    with TestClient(app) as client:
        yield client
