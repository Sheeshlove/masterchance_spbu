"""
Бот не должен ходить в БД из event loop.

SQLAlchemy здесь синхронный. Если его звать прямо в теле async-хендлера, на
время запроса встаёт весь цикл событий aiogram — то есть бот перестаёт
отвечать не одному человеку, а всем сразу. Пока база быстрая, это незаметно;
как только обновлятор начинает писать (а он пишет раз в 3 часа), бот выглядит
мёртвым столько, сколько длится запись.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

pytest.importorskip("aiogram")

from app.presentation import bot as bot_module  # noqa: E402


class _Msg:
    """Минимальная замена aiogram.types.Message: нужен только text и answer."""

    def __init__(self, text: str):
        self.text = text
        self.sent: list[str] = []

    async def answer(self, text: str, **_kw) -> None:
        self.sent.append(text)


def test_forecast_is_fetched_outside_the_event_loop(monkeypatch):
    where = {}

    class _StubUseCase:
        def __init__(self, _repo):
            pass

        def execute(self, _applicant_id):
            where["db"] = threading.current_thread()
            return None

    monkeypatch.setattr(bot_module, "GetApplicantForecastUseCase", _StubUseCase)

    async def go():
        where["loop"] = threading.current_thread()
        await bot_module.applicant_handler(_Msg("1000042"))

    asyncio.run(go())

    assert "db" in where, "хендлер вообще не дошёл до запроса в БД"
    assert where["db"] is not where["loop"], (
        "запрос к БД выполнен в потоке event loop — на это время бот замирает "
        "для всех пользователей сразу"
    )


def test_last_update_is_fetched_outside_the_event_loop(monkeypatch):
    """/start дёргает базу так же, и точно так же не должен блокировать цикл."""
    where = {}

    class _StubUseCase:
        def __init__(self, _repo):
            pass

        def execute(self):
            where["db"] = threading.current_thread()
            return None

    monkeypatch.setattr(bot_module, "GetLastUpdateTimeUseCase", _StubUseCase)

    async def go():
        where["loop"] = threading.current_thread()
        await bot_module.start_cmd(_Msg("/start"))

    asyncio.run(go())

    assert where["db"] is not where["loop"]


def test_slow_database_does_not_stall_other_users(monkeypatch):
    """
    Ровно тот сценарий, ради которого всё делалось: пока один запрос ждёт
    заблокированную базу, второй пользователь должен получить ответ.
    """
    started = threading.Event()

    class _SlowUseCase:
        def __init__(self, _repo):
            pass

        def execute(self, _applicant_id):
            started.set()
            time_to_wait = 1.0
            threading.Event().wait(time_to_wait)  # имитируем busy_timeout
            return None

    monkeypatch.setattr(bot_module, "GetApplicantForecastUseCase", _SlowUseCase)

    async def go():
        slow = asyncio.create_task(bot_module.applicant_handler(_Msg("1000001")))
        await asyncio.to_thread(started.wait, 5)

        # Цикл событий жив: обычная корутина успевает отработать, пока
        # медленный запрос ещё висит в своём потоке.
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.5)
        assert not slow.done(), "медленный запрос успел закончиться — тест бессмысленный"

        await slow

    asyncio.run(go())


def test_session_is_opened_inside_the_worker_thread(monkeypatch):
    """
    Session у SQLAlchemy не потокобезопасна, поэтому заводить её нужно там же,
    где она используется, — иначе соединение уедет между потоками.
    """
    where = {}
    real_session = bot_module._Session

    def _tracking_session():
        where["session"] = threading.current_thread()
        return real_session()

    monkeypatch.setattr(bot_module, "_Session", _tracking_session)

    async def go():
        where["loop"] = threading.current_thread()
        await bot_module.applicant_handler(_Msg("нет-такого-кода"))

    asyncio.run(go())

    assert where["session"] is not where["loop"]
