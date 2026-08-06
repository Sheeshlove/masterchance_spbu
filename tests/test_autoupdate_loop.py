"""
Тесты цикла автообновления.

Он крутится неделями без присмотра, поэтому проверяем не «работает ли проход»
(это покрыто отдельно), а поведение самого цикла: первый проход сразу, падение
источника не убивает цикл, остановка по сигналу, защита от слишком частых
обходов сервера вуза.
"""
import importlib
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def au(monkeypatch):
    """Свежий модуль цикла с коротким интервалом и без реальных пауз."""
    monkeypatch.setenv("UPDATE_INTERVAL_HOURS", "0.001")
    monkeypatch.setenv("UPDATE_MIN_INTERVAL_SECONDS", "0.01")
    module = importlib.import_module("scripts.autoupdate")
    importlib.reload(module)
    module._stopping = False
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    return module


def test_first_pass_runs_immediately(au):
    """Ждать три часа до первых данных пользователь не должен."""
    calls = []

    def once():
        calls.append("run")
        au._stopping = True

    au.run_once = once
    au.main()
    assert calls == ["run"]


def test_failure_does_not_kill_the_loop(au, capsys):
    """Источник бывает недоступен — это не повод останавливать обновления."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("источник недоступен")
        au._stopping = True

    au.run_once = flaky
    au.main()

    assert calls["n"] == 3, "после двух падений цикл обязан продолжить"
    out = capsys.readouterr().out
    assert out.count("Проход не удался") == 2
    assert "источник недоступен" in out


def test_stops_on_sigterm(au):
    """docker stop шлёт SIGTERM — цикл должен завершиться сам."""
    def once():
        au._handle_stop(signal.SIGTERM, None)

    au.run_once = once
    assert au.main() == 0
    assert au._stopping is True


def test_interval_floor_protects_the_university(monkeypatch):
    """Сколько бы ни попросили, чаще чем раз в 10 минут сервер вуза не трогаем."""
    from scripts.autoupdate import interval_seconds

    monkeypatch.delenv("UPDATE_MIN_INTERVAL_SECONDS", raising=False)
    monkeypatch.setenv("UPDATE_INTERVAL_HOURS", "0.0001")   # 0,36 секунды
    assert interval_seconds() == 600.0

    monkeypatch.setenv("UPDATE_INTERVAL_HOURS", "3")
    assert interval_seconds() == 3 * 3600


def test_default_interval_is_three_hours(monkeypatch):
    from scripts.autoupdate import interval_seconds

    monkeypatch.delenv("UPDATE_INTERVAL_HOURS", raising=False)
    monkeypatch.delenv("UPDATE_MIN_INTERVAL_SECONDS", raising=False)
    assert interval_seconds() == 3 * 3600


def test_reports_next_run_time(au, capsys):
    """В логе должно быть видно, когда ждать следующего обновления."""
    calls = {"n": 0}

    def stub():
        calls["n"] += 1
        if calls["n"] >= 2:
            au._stopping = True

    au.run_once = stub
    au.main()
    assert "Следующее обновление примерно в" in capsys.readouterr().out
