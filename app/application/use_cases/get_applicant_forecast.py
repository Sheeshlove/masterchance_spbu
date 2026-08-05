# app/application/use_cases/get_applicant_forecast.py
"""
Единый use case прогноза зачисления по коду абитуриента.

Это «single source of truth» для расчётов, которые раньше были зашиты в
Telegram-боте (`app/presentation/bot.py`): сбор данных из репозитория,
условные вероятности (`cond = uncond / (1 - p_excluded)`), процент «пролёта»
(`p_fail_when_included`) и статус экзамена. Бот и веб-интерфейс используют
этот use case и лишь по-разному рендерят возвращённую структуру (Markdown / HTML),
чтобы числа никогда не расходились.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from app.application.use_cases.get_last_update_time import GetLastUpdateTimeUseCase
from app.config.config import settings
from app.domain.models import Application, ExamSession
from app.infrastructure.db.repositories.program_repository import ProgramRepository

# Расписание экзаменов и submission_stats хранятся в МСК, tz-naive.
_SRC_TZ = ZoneInfo("Europe/Moscow")


class ExamState(str, Enum):
    """Состояние вступительного испытания для одного направления."""
    PASSED = "passed"               # есть баллы
    UPCOMING = "upcoming"           # есть будущие даты экзаменов
    NOT_PUBLISHED = "not_published"  # расписания пока нет
    FINISHED = "finished"           # все даты в прошлом


@dataclass
class ExamStatus:
    state: ExamState
    # PASSED
    vi_score: int | None = None
    id_achievements: int | None = None
    target_id_achievements: int | None = None
    total_score: int | None = None
    # UPCOMING — даты уже переведены в settings.timezone (tz-aware)
    upcoming_dates: list[datetime] = field(default_factory=list)
    more: bool = False
    # FINISHED — последняя дата в settings.timezone (tz-aware)
    last_date: datetime | None = None
    recently_finished: bool = False  # последний экзамен был < 3 дней назад


@dataclass
class ForecastItem:
    program_code: str
    program_name: str
    department_code: str
    prob_cond: float | None  # условная вероятность поступления, 0..1
    q90: float | None
    q95: float | None
    exam: ExamStatus


@dataclass
class ForecastResult:
    applicant_id: str
    university: str | None        # 'spbgu' | None
    items: list[ForecastItem]
    fail_cond: float              # условная вероятность «пролёта», 0..1
    last_update: datetime | None  # tz-aware, settings.timezone


def _to_local(dt_naive_msk: datetime) -> datetime:
    """tz-naive МСК (как в БД) → tz-aware в settings.timezone."""
    return dt_naive_msk.replace(tzinfo=_SRC_TZ).astimezone(settings.timezone)


def _display_code(code: str) -> str:
    """
    Убрать служебный префикс вуза: 'spbgu:01.04.02' → '01.04.02'.

    Коды направлений федеральные и совпадают у разных вузов, а таблица
    departments общая, поэтому в БД коды СПбГУ неймспейснуты (см.
    seed_spbgu_programs.py). Пользователю префикс показывать незачем, и
    срезаем мы его здесь — чтобы бот, сайт и десктоп получили это разом.
    """
    return code.split(":", 1)[-1]


def _build_exam_status(app: Application | None, sessions: list[ExamSession] | None) -> ExamStatus:
    """
    Повторяет логику бывшей `_exam_info_line` из бота, но возвращает структуру,
    а не готовую строку. Порядок ветвлений и пороги идентичны.
    """
    # 1) Есть результат
    if app and ((app.vi_score and app.vi_score > 0) or (app.subject1_score and app.subject1_score > 0)):
        return ExamStatus(
            state=ExamState.PASSED,
            vi_score=app.vi_score,
            id_achievements=app.id_achievements,
            target_id_achievements=app.target_id_achievements,
            total_score=app.total_score,
        )

    # 2) Нет результата — смотрим расписание
    sessions = sessions or []
    if not sessions:
        return ExamStatus(state=ExamState.NOT_PUBLISHED)

    now_msk = datetime.now(_SRC_TZ)
    upcoming = [s for s in sessions if s.dt.replace(tzinfo=_SRC_TZ) >= now_msk]
    if upcoming:
        show = upcoming[:3]
        return ExamStatus(
            state=ExamState.UPCOMING,
            upcoming_dates=[_to_local(s.dt) for s in show],
            more=len(upcoming) > 3,
        )

    # 3) Все экзамены в прошлом
    last_dt = sessions[-1].dt
    recently = False
    delta = now_msk - last_dt.replace(tzinfo=_SRC_TZ)
    if delta.total_seconds() >= 0 and delta < timedelta(days=3):
        recently = True
    return ExamStatus(
        state=ExamState.FINISHED,
        last_date=_to_local(last_dt),
        recently_finished=recently,
    )


class GetApplicantForecastUseCase:
    """
    Возвращает структурированный прогноз по коду абитуриента либо None,
    если у абитуриента нет поданных заявок.
    """

    def __init__(self, repo: ProgramRepository):
        self._repo = repo

    def execute(self, applicant_id: str) -> ForecastResult | None:
        all_codes = self._repo.get_program_codes_by_applicant(applicant_id)
        if not all_codes:
            return None

        prob_objs = self._repo.get_probabilities_for_applicant(applicant_id)
        probs_uncond = {p.program_code: p.probability for p in prob_objs}

        quantiles = self._repo.get_quantiles_for_programs(all_codes)
        prog_map = self._repo.get_programs_by_codes(all_codes)
        diag = self._repo.get_diagnostics_for_applicant(applicant_id)

        apps = self._repo.get_applications_by_applicant(applicant_id)
        apps_by_code = {a.program_code: a for a in apps if a.program_code in all_codes}

        sessions_by_code = {
            code: self._repo.get_exam_sessions_by_program(code) for code in all_codes
        }

        # Условные вероятности — как в боте.
        p_excl = diag.p_excluded if diag else 0.0
        p_incl = max(1.0 - p_excl, 1e-9)
        probs_cond = {k: min(v / p_incl, 1.0) for k, v in probs_uncond.items()}

        fail_uncond = max(0.0, 1.0 - sum(probs_uncond.values()))
        fail_cond = min(1.0, (diag.p_fail_when_included if diag else fail_uncond / p_incl))

        items: list[ForecastItem] = []
        for code in all_codes:
            prog = prog_map.get(code)
            q = quantiles.get(code)
            items.append(
                ForecastItem(
                    program_code=code,
                    program_name=prog.name if prog else code,
                    department_code=_display_code(prog.department_code) if prog else code.split(".")[0],
                    prob_cond=probs_cond.get(code),
                    q90=q.q90 if q else None,
                    q95=q.q95 if q else None,
                    exam=_build_exam_status(apps_by_code.get(code), sessions_by_code.get(code, [])),
                )
            )

        first = prog_map.get(all_codes[0])
        university = first.university if first else None

        last_update = GetLastUpdateTimeUseCase(self._repo).execute()

        return ForecastResult(
            applicant_id=applicant_id,
            university=university,
            items=items,
            fail_cond=fail_cond,
            last_update=last_update,
        )
