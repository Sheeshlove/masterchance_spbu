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
    # Выжимка «что делать» перед списком направлений. None у старых снапшотов,
    # собранных до её появления, — интерфейсы обязаны это переживать.
    strategy: "Strategy | None" = None


def _to_local(dt_naive_msk: datetime) -> datetime:
    """tz-naive МСК (как в БД) → tz-aware в settings.timezone."""
    return dt_naive_msk.replace(tzinfo=_SRC_TZ).astimezone(settings.timezone)


def _display_code(code: str) -> str:
    """
    Убрать служебный префикс вуза: 'spbgu:01.04.02' → '01.04.02'.

    Коды направлений федеральные, а таблица departments не разделена по
    источникам, поэтому в БД они неймспейснуты. Пользователю префикс показывать
    незачем, и срезаем мы его здесь — чтобы бот, сайт и десктоп получили это
    разом.
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

        if comp.unscored_rivals:
            reasons.append(Reason(
                ReasonKind.GOOD,
                f"Ещё {comp.unscored_rivals} "
                f"{_plural(comp.unscored_rivals, 'человек', 'человека', 'человек')} "
                f"в списках без баллов. Списки окончательные, так что в конкурсе они идут "
                f"с нулём и обойти вас уже не могут.",
            ))
    else:
        reasons.append(Reason(
            ReasonKind.BAD,
            "Вашего балла в списках нет. Списки окончательные, поэтому в конкурсе вы "
            "идёте с нулём: пройти можно только туда, где мест больше, чем людей "
            "с баллами.",
        ))

    # 3. Балл против прогноза проходного.
    if comp.my_total_score and q90 is not None and q95 is not None:
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

    # 5. Конкуренты, которых модель может увести в другой вуз.
    if comp.rivals_without_consent:
        reasons.append(Reason(
            ReasonKind.GOOD,
            f"{comp.rivals_without_consent} из {comp.applications} конкурентов пока нигде "
            f"не подали согласие. Часть из них модель уводит в другие вузы; освободившееся "
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


class Outlook(str, Enum):
    """Насколько всё хорошо в целом — чтобы UI мог подобрать тон."""
    SAFE = "safe"          # пройду почти наверняка
    LIKELY = "likely"      # скорее да, чем нет
    RISKY = "risky"        # скорее нет, чем да
    LONGSHOT = "longshot"  # шанс есть, но маленький


@dataclass
class Strategy:
    """
    Выжимка перед списком направлений: чем всё кончится и что делать.

    Карточки отвечают на вопрос «какой у меня шанс здесь», но человек приходит
    с другим вопросом — «что мне сделать, чтобы поступить». Ответ на него
    разбросан по объяснениям под каждым направлением, и собрать его самому
    трудно. Здесь он собран: один вывод, одна цифра и несколько шагов по
    убыванию того, насколько они в руках самого абитуриента.
    """
    outlook: Outlook
    headline: str          # чем всё скорее всего кончится
    detail: str            # тот же вывод числом
    steps: list[Reason]    # что делать, самое важное первым


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _quoted(name: str) -> str:
    return f"«{name}»"


def _outlook_for(chance_any: float) -> Outlook:
    if chance_any >= 0.8:
        return Outlook.SAFE
    if chance_any >= 0.5:
        return Outlook.LIKELY
    if chance_any >= 0.2:
        return Outlook.RISKY
    return Outlook.LONGSHOT


def _consent_step(items: list[ForecastItem], p_excluded: float) -> Reason | None:
    """
    Согласие — единственное, что решается одним действием и прямо сегодня.

    Поэтому оно идёт первым: балл уже выставлен и не изменится, приоритеты
    влияют лишь на то, куда именно вы попадёте, а без согласия не зачислят
    никуда вообще.
    """
    comps = [it.competition for it in items if it.competition]
    if not comps:
        return None

    if any(c.my_consent for c in comps):
        return Reason(ReasonKind.GOOD, "Согласие подано — в конкурсе вы остаётесь.")

    tail = (
        f" Модель поэтому уводит вас в другой вуз в {_pct(p_excluded)} сценариев, "
        f"а показанные шансы — при условии, что вы остаётесь."
        if p_excluded > 0 else ""
    )
    return Reason(
        ReasonKind.BAD,
        "Согласия нет ни на одном направлении. Без него не зачислят даже туда, "
        "где вы проходите по баллам — сейчас это важнее всего остального." + tail,
    )


def _exam_step(items: list[ForecastItem]) -> Reason | None:
    """Оставшиеся экзамены — единственный способ ещё поднять свой балл."""
    upcoming = [it for it in items if it.exam.state is ExamState.UPCOMING]
    if not upcoming:
        return None

    nearest = min(
        (it.exam.upcoming_dates[0] for it in upcoming if it.exam.upcoming_dates),
        default=None,
    )
    when = f" Ближайший — {nearest.strftime('%d.%m в %H:%M')}." if nearest else ""
    where = (
        f"на {len(upcoming)} направлениях" if len(upcoming) > 1
        else f"на направлении {_quoted(upcoming[0].program_name)}"
    )
    return Reason(
        ReasonKind.NEUTRAL,
        f"Экзамен ещё впереди {where} — это единственное, чем вы сейчас можете "
        f"поднять свой балл.{when}",
    )


def _landing_step(items: list[ForecastItem], anchor: ForecastItem) -> Reason | None:
    """
    Откуда берутся нули на остальных направлениях.

    Зачисляют ровно в одно место, поэтому шансы по направлениям — это не пять
    независимых оценок, а одно распределение: вместе с «пролётом» они дают
    100%. Ноль под направлением ниже приоритетом означает не «здесь вам не
    светит», а «сюда очередь не дойдёт». Без этой строчки хорошая новость
    читается как плохая.
    """
    if anchor.prob_cond is None or anchor.prob_cond < 0.5:
        return None

    spare = [
        it for it in items
        if it is not anchor and (it.prob_cond or 0.0) < 0.05
    ]
    if not spare:
        return None

    return Reason(
        ReasonKind.GOOD,
        f"Нули на остальных направлениях — не «нет шансов», а «туда не дойдёт»: "
        f"зачисляют в одно место, и вы проходите на направление {_quoted(anchor.program_name)} "
        f"({_pct(anchor.prob_cond)}). Остальные сработают, только если здесь не выйдет.",
    )


def _priority_step(items: list[ForecastItem], anchor: ForecastItem) -> Reason | None:
    """
    Стоит ли переставлять приоритеты.

    Модель распределяет отложенным согласием: человек предлагает себя по
    списку сверху вниз и оседает на самом приоритетном месте, куда проходит.
    При таком правиле хитрить с порядком невыгодно — занизив желанное
    направление, вы не повышаете шанс на остальные, а только теряете его.
    """
    if len(items) < 2:
        return None

    prio = anchor.competition.my_priority if anchor.competition else None
    if prio and prio > 1:
        first = items[0]
        return Reason(
            ReasonKind.NEUTRAL,
            f"Вероятнее всего вы пройдёте на направление {_quoted(anchor.program_name)} — это "
            f"приоритет {prio}, а не первый: на направлении {_quoted(first.program_name)} шанс "
            f"{_pct(first.prob_cond or 0.0)}. Порядок при этом менять незачем — "
            f"вас распределяют на самое приоритетное направление из доступных, "
            f"так что ставить их стоит по настоящему желанию.",
        )

    return Reason(
        ReasonKind.NEUTRAL,
        "Приоритеты расставляйте по настоящему желанию: вас распределяют на самое "
        "приоритетное направление из тех, куда вы проходите, поэтому занижать "
        "желанное ради подстраховки бессмысленно — так можно только потерять его.",
    )


def _closest_step(items: list[ForecastItem]) -> Reason | None:
    """Где не хватает меньше всего баллов — если пройти пока не выходит."""
    gaps: list[tuple[float, ForecastItem]] = []
    for it in items:
        score = it.competition.my_total_score if it.competition else None
        if score and it.q90 is not None and it.q90 > score:
            gaps.append((it.q90 - score, it))
    if not gaps:
        return None

    gap, item = min(gaps, key=lambda g: g[0])
    return Reason(
        ReasonKind.NEUTRAL,
        f"Ближе всего вы к направлению {_quoted(item.program_name)}: ваш балл ниже "
        f"нижней границы прогноза проходного примерно на {gap:.0f} "
        f"{_plural(int(round(gap)), 'балл', 'балла', 'баллов')}.",
    )


def _surplus_step(items: list[ForecastItem]) -> Reason | None:
    """
    Направление, где желающих меньше, чем мест, — самая надёжная опора.

    Шанс проверяем отдельно, хотя при недоборе он и так обязан быть высоким:
    вуз публикует места и заявки порознь, и на несогласованных данных иначе
    вышло бы «самая надёжная опора» под направлением с шансом 9%.
    """
    for it in items:
        comp = it.competition
        if (it.prob_cond or 0.0) < 0.5:
            continue
        if comp and comp.seats and comp.applications <= comp.seats:
            return Reason(
                ReasonKind.GOOD,
                f"На направлении {_quoted(it.program_name)} заявок ({comp.applications}) меньше, "
                f"чем мест ({comp.seats}) — это самая надёжная опора в вашем списке.",
            )
    return None


def _build_strategy(
    items: list[ForecastItem],
    fail_cond: float,
    p_excluded: float,
) -> Strategy | None:
    """Собрать выжимку. None — если считать не на чем (нет ни одного шанса)."""
    if not items:
        return None

    scored = [it for it in items if it.prob_cond is not None]
    if not scored:
        return None

    chance_any = max(0.0, min(1.0, 1.0 - fail_cond))
    anchor = max(scored, key=lambda it: it.prob_cond or 0.0)
    outlook = _outlook_for(chance_any)

    named = (anchor.prob_cond or 0.0) >= 0.4
    if outlook is Outlook.SAFE:
        headline = (
            f"Вы почти наверняка поступите — скорее всего на направление {_quoted(anchor.program_name)}."
            if named else "Вы почти наверняка поступите, но пока не ясно, куда именно."
        )
    elif outlook is Outlook.LIKELY:
        headline = (
            f"Скорее всего вы поступите, вероятнее всего — на направление {_quoted(anchor.program_name)}."
            if named else
            "Скорее всего вы поступите, но шансы размазаны по нескольким направлениям."
        )
    elif outlook is Outlook.RISKY:
        headline = "Расклад не в вашу пользу, но проходной сценарий есть."
    else:
        headline = "По нынешним данным пройти трудно — но кое-что ещё в ваших руках."

    detail = (
        f"Шанс поступить хоть куда-нибудь — {_pct(chance_any)}. "
        f"Лучшее направление: {_quoted(anchor.program_name)}, {_pct(anchor.prob_cond or 0.0)}."
    )

    candidates = [
        _consent_step(items, p_excluded),
        _exam_step(items),
        _surplus_step(items),
        _landing_step(items, anchor),
        _priority_step(items, anchor),
        _closest_step(items) if chance_any < 0.8 else None,
    ]
    steps = [s for s in candidates if s is not None][:5]

    return Strategy(outlook=outlook, headline=headline, detail=detail, steps=steps)


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
            items.append(
                ForecastItem(
                    program_code=code,
                    program_name=prog.name if prog else code,
                    department_code=_display_code(prog.department_code) if prog else code.split(".")[0],
                    prob_cond=probs_cond.get(code),
                    q90=q.q90 if q else None,
                    q95=q.q95 if q else None,
                    exam=_build_exam_status(apps_by_code.get(code), sessions_by_code.get(code, [])),
                    competition=comp,
                    reasons=_build_reasons(comp, q.q90 if q else None, q.q95 if q else None, p_excl),
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
            p_excluded=p_excl,
            notes=_build_notes(p_excl),
            strategy=_build_strategy(items, fail_cond, p_excl),
        )
