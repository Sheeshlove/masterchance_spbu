"""
Тесты shell-скриптов проекта.

`bash -n` проверяет только синтаксис: вызов несуществующей функции для него
корректен, и ошибка вылезает уже на сервере, посреди работы. Ровно так и
случилось — `note` был определён в cleanup.sh, а вызван в setup_https.sh.
Здесь проверяется то, что ловится статически: определения, режимы, права.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SCRIPTS = sorted(Path("scripts").glob("*.sh"))


def defined_functions(text: str) -> set[str]:
    return set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\s*\(\)\s*\{", text, re.M))


def called_words(text: str) -> set[str]:
    """Слова в позиции команды: начало строки, после `|`, `&&`, `||`, `;`."""
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    return set(re.findall(r"(?:^|\||&&|\|\||;)\s*([a-z_][a-z0-9_]*)\b", body, re.M))


#: Все хелперы, определённые хоть в одном скрипте. Если такое имя вызвано
#: в другом скрипте без своего определения — это копипаста, и она упадёт.
ALL_HELPERS = {f for p in SCRIPTS for f in defined_functions(p.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_syntax_is_valid(path: Path):
    done = subprocess.run(["bash", "-n", str(path)], capture_output=True)
    assert done.returncode == 0, done.stderr.decode()


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_every_helper_used_is_defined_here(path: Path):
    """
    Хелпер, взятый из соседнего скрипта, не приедет вместе с вызовом.
    Для bash это не ошибка до момента запуска — а запускают это на сервере.
    """
    text = path.read_text(encoding="utf-8")
    mine = defined_functions(text)
    used_helpers = called_words(text) & ALL_HELPERS

    missing = used_helpers - mine
    assert not missing, f"{path.name} зовёт, но не определяет: {sorted(missing)}"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_is_executable(path: Path):
    assert path.stat().st_mode & 0o111, f"{path.name} не исполняемый"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_stops_on_first_error(path: Path):
    """
    Скрипты идут на сервере от root. Без `set -e` упавший шаг не остановит
    остальные, и разбираться придётся уже по последствиям.

    Исключение возможно, но должно быть объявлено вслух: у диагностики другая
    задача — дойти до конца и показать все проблемы разом, а не встать на
    первой. Такой скрипт обязан объяснить это комментарием «без -e».
    """
    text = path.read_text(encoding="utf-8")
    if re.search(r"^set -[a-z]*e", text, re.M):
        return
    assert re.search(r"без -e", text), (
        f"{path.name} без set -e и без объяснения, почему так задумано"
    )


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_has_a_shebang(path: Path):
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#!"), f"{path.name} без shebang"
