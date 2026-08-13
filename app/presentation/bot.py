"""
Telegram-бот, показывающий направления, квантили и шансы.

Вся бизнес-логика прогноза вынесена в общий
`GetApplicantForecastUseCase` (app/application/use_cases/get_applicant_forecast.py),
который используют и бот, и веб-интерфейс. Здесь остаётся только рендеринг
структуры прогноза в Markdown.
"""
import asyncio
from datetime import datetime
from textwrap import dedent
from typing import List

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from sqlalchemy.orm import sessionmaker

from app.application.use_cases.get_applicant_forecast import (
    ExamState,
    ExamStatus,
    ForecastResult,
    GetApplicantForecastUseCase,
    Reason,
    ReasonKind,
)
from app.application.use_cases.get_last_update_time import GetLastUpdateTimeUseCase
from app.config.config import settings
from app.config.logger import logger
from app.domain.universities import label as university_label
from app.infrastructure.db.engine import ensure_indexes, make_engine
from app.infrastructure.db.models import Base
from app.infrastructure.db.repositories.program_repository import ProgramRepository

_engine = make_engine(settings.database_url)
Base.metadata.create_all(_engine)
ensure_indexes(_engine)
_Session = sessionmaker(bind=_engine, future=True)


# ── Работа с БД: только в отдельном потоке ───────────────────────────────────
#
# SQLAlchemy здесь синхронный, и раньше эти вызовы стояли прямо в теле
# async-хендлеров. Пока запрос шёл, event loop aiogram стоял целиком — то есть
# бот замирал не для одного человека, а СРАЗУ ДЛЯ ВСЕХ. Если в этот момент
# обновлятор держал базу (см. app/infrastructure/db/engine.py), бот выглядел
# мёртвым столько, сколько длилась запись.
#
# Сессия создаётся внутри функции намеренно: объект Session не потокобезопасен,
# и заводить его нужно в том же потоке, где он используется.

def _load_forecasts(codes: str) -> List[ForecastResult]:
    """Прогнозы по всем вузам, где нашёлся введённый код (или коды)."""
    session = _Session()
    try:
        return GetApplicantForecastUseCase(ProgramRepository(session)).execute_all(codes)
    finally:
        session.close()


def _load_last_update() -> datetime | None:
    session = _Session()
    try:
        return GetLastUpdateTimeUseCase(ProgramRepository(session)).execute()
    except Exception:
        return None
    finally:
        session.close()


def split_message(text: str, max_len: int = 4000) -> List[str]:
    parts = []
    while len(text) > max_len:
        split_idx = text.rfind('\n', 0, max_len)
        if split_idx == -1:
            split_idx = max_len
        parts.append(text[:split_idx].strip())
        text = text[split_idx:].strip()
    if text:
        parts.append(text)
    return parts


def _fmt_qrange(q90: float | None, q95: float | None) -> str:
    """Квантили → строка `X - X`. Если данных нет — '—'."""
    if q90 is None or q95 is None:
        return "—"
    if q90 == q95:
        return f"{q90:.0f}"
    return f"{q90:.0f} - {q95:.0f}"


def _human_prog_line(dept_code: str, prog_name: str) -> str:
    return f"• `{dept_code}`  *{prog_name}*"


def _render_exam_line(exam: ExamStatus) -> str | None:
    """Структура статуса экзамена → одна Markdown-«подстрочка» направления."""
    if exam.state is ExamState.PASSED:
        parts: List[str] = []
        if exam.vi_score and exam.vi_score > 0:
            parts.append(f"{exam.vi_score}")
        if exam.id_achievements and exam.id_achievements > 0:
            parts.append(f"+{exam.id_achievements}")
        if exam.target_id_achievements and exam.target_id_achievements > 0:
            parts.append(f"+{exam.target_id_achievements}")
        parts.append(f"=**{exam.total_score}**")
        return f"   ↳ 🟢 Cдан: {''.join(parts)}"

    if exam.state is ExamState.NOT_PUBLISHED:
        return "   ↳ 🟡 Расписание экзамена пока не опубликовано"

    if exam.state is ExamState.UPCOMING:
        dates = "; ".join(d.strftime("%d.%m %H:%M") for d in exam.upcoming_dates)
        more = " …" if exam.more else ""
        return f"   ↳ 🟡 Ближайшие экзамены: {dates}{more}"

    # FINISHED
    last = exam.last_date.strftime("%d.%m %H:%M") if exam.last_date else "—"
    line = f"   ↳ ⚪ Экзамены завершились (последняя дата: {last})"
    if exam.recently_finished:
        line += "\n   ↳ ⚠️ прошло < 3 дней — результаты могут ещё обновляться."
    return line


_REASON_ICONS = {
    ReasonKind.GOOD: "＋",
    ReasonKind.BAD: "−",
    ReasonKind.NEUTRAL: "·",
}


def _render_reasons(reasons: List[Reason], indent: str = "   ") -> List[str]:
    """Пояснения «почему такой шанс» → строки Markdown со знаком влияния."""
    return [f"{indent}{_REASON_ICONS[r.kind]} {r.text}" for r in reasons]


def _render_all(results: List[ForecastResult]) -> str:
    """
    Несколько вузов — несколько разделов, а не один общий список.

    Конкурсы в разных вузах независимы: и места, и приоритеты, и «пролетел»
    считаются внутри своего вуза. Склеенный список создавал бы впечатление
    одной очереди на всех.
    """
    if len(results) == 1:
        return _render_forecast(results[0])

    blocks = []
    for result in results:
        title = university_label(result.university) or "Вуз"
        blocks.append(f"🏛 *{title}* — код `{result.applicant_id}`\n{_render_forecast(result)}")
    return "\n\n————————————\n\n".join(blocks)


def _render_forecast(result: ForecastResult) -> str:
    """
    Рендер структуры прогноза в Markdown:
      1) Направления + строка про экзамены (баллы/даты).
      2) 🔮 Прогноз зачисления: вероятность, проходной и почему шанс такой.
      3) Блок про «пролёт» + общие пояснения к модели.
    """
    head_programs = "📝 *Ваши направления*"
    prog_lines: List[str] = []
    for it in result.items:
        prog_lines.append(_human_prog_line(it.department_code, it.program_name))
        exam_line = _render_exam_line(it.exam)
        if exam_line:
            prog_lines.append(exam_line)

    head_forecast = "\n\n🔮 *Прогноз зачисления*"
    forecast_lines: List[str] = []
    for it in result.items:
        p_str = f"{it.prob_cond * 100:.1f}%" if it.prob_cond is not None else "—"
        forecast_lines.append(
            f"• `{it.program_name}`  →  *{p_str}* (проходной: {_fmt_qrange(it.q90, it.q95)})"
        )
        forecast_lines.extend(_render_reasons(it.reasons))
        if it.reasons:
            forecast_lines.append("")

    head_fail = (
        "\n🚫 *«Пролетел с магой»*\n"
        f"• В *{result.fail_cond * 100:.1f}%* симуляций\n"
    )

    tail: List[str] = []
    if result.notes:
        tail.append("\nℹ️ *Как читать эти числа*")
        tail.extend(f"• {n.text}" for n in result.notes)

    return "\n".join([head_programs, *prog_lines, head_forecast, *forecast_lines, head_fail, *tail])


async def how_cmd(msg: Message):
    await msg.answer(dedent("""
    🧠 *Как работает прогноз?*

    Прогноз построен по методу Монте‑Карло — это способ смоделировать тысячи возможных будущих сценариев. Вот как это работает шаг за шагом:

    1. **Повторяем симуляцию десятки тысяч раз** — это как доктор Стрэндж, просматривающий альтернативные вселенные.

    2. **Баллы берутся из окончательных списков как есть** — ничего не додумывается.

    3. **Если балла в списках нет**, человек идёт в конкурсе с нулём: списки окончательные, и обогнать того, у кого балл выставлен, он уже не может. Раньше такой балл разыгрывался по статистике экзамена — это было нужно, пока результаты ещё не опубликовали.

    4. **Балл складывается с индивидуальными достижениями**, и получается полный конкурсный балл.

    5. **Имитация конкурса как в вузе**:
       • сначала все абитуриенты «распределяются» по 1‑м приоритетам;
       • если кто-то не проходит — перекидываются на 2‑й приоритет, и так далее.

    6. **Результат:**
       • если ты попал на направление в 8 000 симуляциях из 10 000, шанс ≈ 80%;
       • считаем также «средний» и «высокий» проходной балл (90 % и 95 % квантиль).

    📉 *Понижается ли проходной, если кто-то уходит в другой вуз?*

    Да — и это главная причина, по которой проходной показан вилкой, а не одним числом.
    Проходной балл направления = балл самого слабого из зачисленных. Если человек,
    занимавший последнее место, уходит, его место освобождается и достаётся следующему
    по списку — а у следующего балл ниже. Значит, проходной в этом сценарии опускается,
    и подняться он от чужого ухода не может никогда.

    Приоритет тут ничего не меняет: важно, что место освободилось. Разница только в том,
    куда уходит человек:
       • **в другой вуз** — модель разыгрывает такой уход для тех, кто не подал согласие
         ни на одно направление; место освобождается, проходной падает;
       • **на другое направление внутри вуза** — человек с приоритетом 1 сам туда не уйдёт:
         приоритет 1 и есть его первый выбор. Он освободит место, только если его выбьет
         кто-то с более высоким баллом — но тогда место занимает этот более сильный,
         и проходной, наоборот, подрастает.

    Именно поэтому в объяснении под каждым направлением видно, сколько конкурентов
    сидят без согласия: чем их больше, тем сильнее «гуляет» проходной вниз.

    ⚠️ *Предсказания не гарантируют поступление!*
    Это всего лишь вероятностная модель на основе того, что уже известно.
    **Что модель теперь приближённо учитывает:**
        • Уход части людей в другие вузы — по сигналу «согласие»: тех, кто не подал согласие ни на одно направление, модель с некоторой вероятностью «уводит», освобождая места. Это сценарная оценка, а не знание чужих планов.
    **Чего модель по-прежнему не знает:**
        • Точных заявок в другие вузы (такой информации нет).
        • Изменятся ли число мест и правила приёма.
    Поэтому результат — ориентир, а не гарантия.

    """).strip(), parse_mode="Markdown")


WEBAPP_BUTTON_TEXT = "Открыть приложение"


def webapp_markup() -> InlineKeyboardMarkup | None:
    """
    Кнопка «Открыть приложение» — тот же сайт, но внутри Telegram.

    None, если адрес не настроен или он не https: Telegram принимает Mini App
    только по https, и на http-адресе кнопка не создастся, а запрос упадёт.
    Тогда бот просто остаётся обычным ботом.
    """
    if not settings.webapp_ready:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=WEBAPP_BUTTON_TEXT,
            web_app=WebAppInfo(url=settings.webapp_url),
        )
    ]])


async def setup_menu_button(bot: Bot) -> None:
    """Кнопка меню рядом с полем ввода — главный вход в Mini App."""
    if not settings.webapp_ready:
        return
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text=WEBAPP_BUTTON_TEXT,
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        )
        logger.info("Mini App подключён: %s", settings.webapp_url)
    except Exception as exc:
        # Недоступный Mini App не повод не запускать бота: он и без него рабочий
        logger.warning("Не удалось включить кнопку Mini App: %s", exc)


async def start_cmd(msg: Message):
    last_dt = await asyncio.to_thread(_load_last_update)

    def _fmt(dt: datetime | None) -> str:
        return dt.strftime("%d.%m.%Y %H:%M") if dt else "нет данных"

    markup = webapp_markup()
    hint = "\n        Или нажми кнопку ниже — там то же самое, но с картинками." if markup else ""

    await msg.answer(
        dedent(f"""
        Привет! Отправь мне **код абитуриента** — покажу все направления, шанс зачисления и ориентиры проходных баллов.
        Код в каждом вузе свой: если подавался в несколько, пришли их через запятую — по каждому будет свой раздел.
{hint}
        Последнее обновление данных: **{_fmt(last_dt)}**

        /how — как это работает?
        Автором оригинальной модели и вдохновителем является @fascinat00r
        """).strip(),
        parse_mode="Markdown",
        reply_markup=markup,
    )


async def applicant_handler(msg: Message):
    codes = msg.text.strip()
    if not codes:
        return

    try:
        results = await asyncio.to_thread(_load_forecasts, codes)
        if not results:
            await msg.answer(
                f"Не найдено заявок для абитуриента `{codes}`.",
                parse_mode="Markdown"
            )
            return

        full_text = _render_all(results)

        for part in split_message(full_text):
            try:
                await msg.answer(part, parse_mode="Markdown")
            except TelegramBadRequest:
                await msg.answer("⚠️ Не удалось отправить сообщение (возможно, проблема с Markdown).")
                break

    except Exception as exc:
        logger.exception("TG-handler error: %s", exc)
        await msg.answer("Произошла ошибка 😥")


def start_bot(bot_token) -> None:
    bot = Bot(bot_token)
    dp = Dispatcher()

    dp.message.register(start_cmd, CommandStart())
    dp.message.register(how_cmd, Command("how"))
    dp.message.register(applicant_handler, F.text)
    dp.startup.register(setup_menu_button)

    logger.info("Telegram-бот запущен.")
    asyncio.run(dp.start_polling(bot))
