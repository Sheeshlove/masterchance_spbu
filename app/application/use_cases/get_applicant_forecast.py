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
from app.domain.models import Application, ExamSession, ProgramCompetition
from app.domain.universities import (
    SUPPORTED_UNIVERSITIES,
    display_code,
    raw_applicant_id,
    split_codes,
    university_of_program,
)
from app.infrastructure.db.repositories.program_repository import ProgramRepository

# Расписание экзаменов и submission_stats хранятся в МСК, tz-naive.
_SRC_TZ = ZoneInfo("Europe/Moscow")

# Сколько сценариев прогоняет Монте-Карло (см. recalculate_monte_carlo.py).
# Нужно только для текста объяснения.
N_SIMULATIONS = 10_000


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


class ReasonKind(str, Enum):
    """Как объяснение влияет на шанс — чтобы UI мог его подкрасить."""
    GOOD = "good"        # работает на пользователя
    BAD = "bad"          # работает против
    NEUTRAL = "neutral"  # просто расклад


@dataclass
class Reason:
    kind: ReasonKind
    text: str


@dataclass
class ForecastItem:
    program_code: str
    program_name: str
    department_code: str
    prob_cond: float | None  # условная вероятность поступления, 0..1
    q90: float | None
    q95: float | None
    exam: ExamStatus
    competition: ProgramCompetition | None = None
    reasons: list[Reason] = field(default_factory=list)  # «почему такой шанс»


@dataclass
class ForecastResult:
    applicant_id: str
    university: str | None        # 'spbgu' | None
    items: list[ForecastItem]
    fail_cond: float              # условная вероятность «пролёта», 0..1
    last_update: datetime | None  # tz-aware, settings.timezone
    p_excluded: float = 0.0       # доля сценариев, где модель увела самого абитуриента
    notes: list[Reason] = field(default_factory=list)  # общие пояснения к прогнозу


def _to_local(dt_naive_msk: datetime) -> datetime:
    """tz-naive МСК (как в БД) → tz-aware в settings.timezone."""
    return dt_naive_msk.replace(tzinfo=_SRC_TZ).astimezone(settings.timezone)


#: Коды направлений и абитуриентов лежат в БД с префиксом вуза (см.
#: app/domain/universities.py). Пользователю префикс показывать незачем — вуз
#: он и так видит на вкладке, — и срезаем мы его здесь, чтобы бот, сайт и
#: десктоп получили это разом.
_display_code = display_code


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


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение числительного: 1 место, 2 места, 5 мест."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _num(x: float) -> str:
    """Дробное число по-русски: 6.9 → «6,9»."""
    return f"{x:.1f}".replace(".", ",")


def _build_reasons(
    comp: ProgramCompetition | None,
    q90: float | None,
    q95: float | None,
    p_excluded: float,
) -> list[Reason]:
    """
    Разложить шанс на понятные человеку слагаемые.

    Монте-Карло выдаёт одно число, и без объяснения оно выглядит как гадание.
    Здесь те же входные данные пересказываются словами — в том порядке, в
    котором на исход влияет сама модель: сколько мест, где ты в очереди, как
    балл соотносится с прогнозом проходного, что делает приоритет и кто из
    конкурентов может уйти.

    Текст собирается один раз здесь, а не в боте/сайте/десктопе, чтобы три
    интерфейса не начали объяснять одно и то же по-разному.
    """
    if comp is None:
        return []

    reasons: list[Reason] = []
    seats = comp.seats or 0
    pending = comp.pending_results > comp.scored_rivals

    # Про незавершённые испытания говорим первым делом: без этого человек
    # решит, что прочерк вместо шанса — это поломка сайта.
    if pending:
        reasons.append(Reason(
            ReasonKind.NEUTRAL,
            f"Баллы ещё не выставлены: {comp.pending_results} из {comp.applications} "
            f"{_plural(comp.applications, 'заявки', 'заявок', 'заявок')} ждут результатов "
            f"испытаний. Пока конкурс не определён, шанс считать не на чем — "
            f"вернитесь, когда вуз опубликует результаты.",
        ))

    # 1. Сколько мест и сколько желающих.
    if seats > 0 and comp.applications <= seats:
        reasons.append(Reason(
            ReasonKind.GOOD,
            f"Мест — {seats}, заявок — {comp.applications}: желающих меньше, чем мест.",
        ))
    elif seats > 0:
        per_seat = comp.applications / seats
        # дробное числительное по-русски всегда в родительном единственного:
        # «3,4 человека», а не «3,4 человек»
        word = ("человека" if per_seat != int(per_seat)
                else _plural(int(per_seat), "человек", "человека", "человек"))
        reasons.append(Reason(
            ReasonKind.NEUTRAL,
            f"Мест — {seats}, заявок — {comp.applications}: "
            f"{_num(per_seat)} {word} на место.",
        ))
    else:
        reasons.append(Reason(
            ReasonKind.NEUTRAL,
            f"Заявок — {comp.applications}. Сколько мест, вуз пока не опубликовал, "
            f"поэтому шанс здесь оценить не на чем.",
        ))

    # 2. Где абитуриент в очереди по баллу.
    if comp.my_total_score:
        place = comp.better + 1
        line = (
            f"По баллу вы {place}-й из {comp.scored_rivals + 1} "
            f"{_plural(comp.scored_rivals + 1, 'человека', 'человек', 'человек')} "
            f"с уже известными баллами."
        )
        kind = ReasonKind.GOOD if seats and place <= seats else ReasonKind.BAD
        if seats and place <= seats:
            line += " Сейчас это внутри мест."
        elif seats:
            line += f" Это ниже {seats}-го места — нужно, чтобы кто-то сверху ушёл."
        reasons.append(Reason(kind, line))

        if comp.same:
            reasons.append(Reason(
                ReasonKind.NEUTRAL,
                f"Ровно такой же балл ещё у {comp.same} "
                f"{_plural(comp.same, 'человека', 'человек', 'человек')} — "
                f"в модели такие ничьи разыгрываются жребием.",
            ))

        # «Списки окончательные» — правда только там, где испытания завершены.
        # Пока баллы не выставлены, обещать, что нулевые соперники уже не
        # обойдут, нельзя: они как раз и обойдут, когда результаты появятся.
        if comp.unscored_rivals and not pending:
            reasons.append(Reason(
                ReasonKind.GOOD,
                f"Ещё {comp.unscored_rivals} "
                f"{_plural(comp.unscored_rivals, 'человек', 'человека', 'человек')} "
                f"в списках без баллов. Списки окончательные, так что в конкурсе они идут "
                f"с нулём и обойти вас уже не могут.",
            ))
    elif not pending:
        reasons.append(Reason(
            ReasonKind.BAD,
            "Вашего балла в списках нет. Списки окончательные, поэтому в конкурсе вы "
            "идёте с нулём: пройти можно только туда, где мест больше, чем людей "
            "с баллами.",
        ))

    # 3. Балл против прогноза проходного (пока конкурс не определён, прогноза нет).
    if comp.my_total_score and q90 is not None and q95 is not None and not pending:
        score = comp.my_total_score
        if score >= q95:
            reasons.append(Reason(
                ReasonKind.GOOD,
                f"Ваш балл {score} выше даже осторожного прогноза проходного "
                f"({q95:.0f}) — запас {score - q95:.0f}.",
            ))
        elif score >= q90:
            reasons.append(Reason(
                ReasonKind.NEUTRAL,
                f"Ваш балл {score} попал в вилку прогноза проходного "
                f"({q90:.0f}–{q95:.0f}) — исход зависит от того, как сдадут остальные.",
            ))
        else:
            reasons.append(Reason(
                ReasonKind.BAD,
                f"Ваш балл {score} ниже прогноза проходного "
                f"({q90:.0f}–{q95:.0f}) — не хватает примерно {q90 - score:.0f}.",
            ))

    # 4. Приоритет.
    if comp.my_priority == 1:
        reasons.append(Reason(
            ReasonKind.GOOD,
            "Приоритет 1: сюда вас распределяют в первую очередь, на другие "
            "направления вы уходите, только если не проходите здесь.",
        ))
    elif comp.my_priority and comp.my_priority > 1:
        n = comp.my_priority - 1
        reasons.append(Reason(
            ReasonKind.NEUTRAL,
            f"Приоритет {comp.my_priority}: сюда вы попадаете, только если не прошли по "
            f"{n} более приоритетной заявке." if n == 1 else
            f"Приоритет {comp.my_priority}: сюда вы попадаете, только если не прошли по "
            f"{n} более приоритетным заявкам.",
        ))

    # 5. Конкуренты, которые уйдут или могут уйти в другой вуз.
    #
    # Два разных факта, и путать их нельзя. Согласие в другом вузе — это
    # наблюдение: держать его можно только одно, человек уже выбрал. Отсутствие
    # согласия где бы то ни было — только предположение модели.
    if comp.rivals_committed_elsewhere:
        reasons.append(Reason(
            ReasonKind.GOOD,
            f"{comp.rivals_committed_elsewhere} из {comp.applications} конкурентов уже подали "
            f"согласие в другой вуз. Согласие бывает только одно, так что почти все они "
            f"отсюда уйдут и освободят места — это видно по спискам, а не предполагается.",
        ))

    # Пересечься эти две группы не могут: «нет согласия нигде» и «согласие в
    # другом вузе» взаимоисключающи, вычитать одно из другого не нужно.
    if comp.rivals_without_consent:
        reasons.append(Reason(
            ReasonKind.GOOD,
            f"{comp.rivals_without_consent} из {comp.applications} конкурентов пока не подали "
            f"согласие нигде. Часть из них модель уводит в другие вузы; освободившееся "
            f"место достаётся следующему по списку, и проходной в таком сценарии опускается.",
        ))

    # 6. Согласие самого абитуриента.
    if comp.my_consent:
        reasons.append(Reason(
            ReasonKind.GOOD,
            "Согласие вы подали — в модели вы точно остаётесь в конкурсе.",
        ))
    elif p_excluded > 0:
        reasons.append(Reason(
            ReasonKind.NEUTRAL,
            f"Согласие вы пока не подали, поэтому в {p_excluded * 100:.0f}% сценариев модель "
            f"уводит в другой вуз и вас. Показанный шанс — при условии, что вы остаётесь.",
        ))

    return reasons


#: Что мешает считать прогноз по направлению. None — ничего не мешает.
NO_SEATS = "seats"
RESULTS_PENDING = "pending"


def _forecast_blocker(comp: ProgramCompetition | None) -> str | None:
    """
    Определён ли конкурс настолько, чтобы его вообще можно было разыграть.

    Два случая, когда нет:

    • вуз не объявил число мест — делить нечего;
    • вуз ещё не выставил баллы (у ВШЭ в разгар кампании это статусы
      «Ожидание результатов ВИ» и «На рассмотрении» у большинства заявок).
      Модель исходит из того, что списки окончательные и ноль — это ноль; пока
      баллов нет, нули означают «не проверено», и разыгрывать по ним места
      значит выдавать жребий за прогноз.

    Расклад конкурса при этом остаётся виден: сколько мест, сколько заявок и
    где человек в очереди — это факты, а не модель.
    """
    if comp is None:
        return None
    if not comp.seats:
        return NO_SEATS
    if comp.pending_results > comp.scored_rivals:
        return RESULTS_PENDING
    return None


def _build_notes(p_excluded: float) -> list[Reason]:
    """Пояснения ко всему прогнозу целиком, а не к отдельному направлению."""
    n_sim = f"{N_SIMULATIONS:,}".replace(",", " ")  # 10 000, как принято по-русски
    notes = [
        Reason(
            ReasonKind.NEUTRAL,
            f"Шанс — это доля из {n_sim} смоделированных приёмных кампаний, "
            f"в которых вы прошли именно сюда.",
        ),
        Reason(
            ReasonKind.NEUTRAL,
            "Проходной показан вилкой, а не одним числом: в разных сценариях он "
            "разный. Нижняя граница — «обычный» расклад, верхняя — неудачный для вас.",
        ),
    ]
    if p_excluded > 0:
        notes.append(Reason(
            ReasonKind.NEUTRAL,
            f"В {p_excluded * 100:.0f}% сценариев модель уводит вас в другой вуз "
            f"(согласия нигде нет). Эти сценарии из расчёта исключены — иначе шанс "
            f"занижался бы за ваше же возможное решение уйти.",
        ))
    return notes


class GetApplicantForecastUseCase:
    """
    Возвращает структурированный прогноз по коду абитуриента либо None,
    если у абитуриента нет поданных заявок.
    """

    def __init__(self, repo: ProgramRepository):
        self._repo = repo

    def execute(self, applicant_id: str) -> ForecastResult | None:
        """
        Прогноз по одному коду. Если код совпал сразу в нескольких вузах,
        вернётся первый — за полным раскладом идите в execute_all().
        """
        results = self.execute_all(applicant_id)
        return results[0] if results else None

    def execute_all(self, codes: str) -> list[ForecastResult]:
        """
        Прогноз по каждому вузу, куда человек подал документы.

        Код у абитуриента один на все вузы, поэтому и вводить нужно один. А вот
        конкурсы разные: у каждого вуза свои места, свои приоритеты и свой
        «пролетел», складывать их в общий список нельзя. Поэтому заявки
        раскладываются по вузу ПРОГРАММЫ — он зашит в её код.

        Несколько кодов через запятую тоже принимаются: посмотреть чужой
        расклад или сравнить свой с товарищем.

        Порядок — как в SUPPORTED_UNIVERSITIES: вкладки на сайте не должны
        переставляться от запроса к запросу.
        """
        keys: list[str] = []
        for raw in split_codes(codes):
            for key in self._repo.find_applicant_keys(raw):
                if key not in keys:
                    keys.append(key)

        results: list[ForecastResult] = []
        for key in keys:
            results.extend(self._forecasts_by_university(key))

        order = {uni: i for i, uni in enumerate(SUPPORTED_UNIVERSITIES)}
        return sorted(results, key=lambda r: order.get(r.university or "", len(order)))

    def _forecasts_by_university(self, applicant_id: str) -> list[ForecastResult]:
        """Заявки одного человека → по прогнозу на каждый вуз, куда он подал."""
        by_university: dict[str, list[str]] = {}
        for code in self._repo.get_program_codes_by_applicant(applicant_id):
            by_university.setdefault(university_of_program(code) or "", []).append(code)

        return [
            result
            for university, codes in by_university.items()
            if (result := self._forecast(applicant_id, university, codes)) is not None
        ]

    def _forecast(
        self, applicant_id: str, university: str, all_codes: list[str]
    ) -> ForecastResult | None:
        if not all_codes:
            return None

        # Только программы ЭТОГО вуза: у человека один код на все вузы, и
        # вероятности по чужим конкурсам попали бы и в сумму, и в «пролетел».
        prob_objs = self._repo.get_probabilities_for_applicant(applicant_id)
        probs_uncond = {
            p.program_code: p.probability for p in prob_objs if p.program_code in set(all_codes)
        }

        quantiles = self._repo.get_quantiles_for_programs(all_codes)
        prog_map = self._repo.get_programs_by_codes(all_codes)
        diag = self._repo.get_diagnostics_for_applicant(applicant_id, university)

        apps = self._repo.get_applications_by_applicant(applicant_id)
        apps_by_code = {a.program_code: a for a in apps if a.program_code in all_codes}

        sessions_by_code = {
            code: self._repo.get_exam_sessions_by_program(code) for code in all_codes
        }

        # Расклад конкурса — только ради объяснения; на числа он не влияет.
        # Старые репозитории (снапшоты у пользователей на руках) метода могут
        # не знать — тогда просто нет объяснений, а прогноз остаётся.
        try:
            competition = self._repo.get_competition_for_programs(all_codes, applicant_id)
        except AttributeError:
            competition = {}

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
            comp = competition.get(code)

            # Прогноз показываем, только если конкурс вообще определён.
            # Иначе модель считает по данным, которых нет, а число на экране
            # выглядит посчитанным ответом (см. _forecast_blocker).
            probability = probs_cond.get(code)
            blocked = _forecast_blocker(comp) is not None
            if blocked:
                probability = None

            items.append(
                ForecastItem(
                    program_code=code,
                    program_name=prog.name if prog else code,
                    department_code=_display_code(prog.department_code) if prog else code.split(".")[0],
                    prob_cond=probability,
                    # Проходной по неопределённому конкурсу — такая же выдумка,
                    # как и шанс: при невыставленных баллах он выходит нулевым.
                    q90=None if blocked else (q.q90 if q else None),
                    q95=None if blocked else (q.q95 if q else None),
                    exam=_build_exam_status(apps_by_code.get(code), sessions_by_code.get(code, [])),
                    competition=comp,
                    reasons=_build_reasons(comp, q.q90 if q else None, q.q95 if q else None, p_excl),
                )
            )

        first = prog_map.get(all_codes[0])
        # Вуз известен от вызывающего — он выведен из кода программы. Каталог
        # тут запасной вариант: программы может не быть в нём вовсе.
        university = university or (first.university if first else None)

        last_update = GetLastUpdateTimeUseCase(self._repo).execute()

        return ForecastResult(
            applicant_id=raw_applicant_id(applicant_id),
            university=university,
            items=items,
            fail_cond=fail_cond,
            last_update=last_update,
            p_excluded=p_excl,
            notes=_build_notes(p_excl),
        )
