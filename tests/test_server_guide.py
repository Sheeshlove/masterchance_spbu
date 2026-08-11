"""
Тесты инструкции по установке (СЕРВЕР.md) и docker-compose.

Инструкцию читает человек, который не может сам догадаться, что команда
устарела: если она ссылается на несуществующий файл или на переименованную
настройку, он просто упрётся в ошибку. Поэтому проверяем, что всё, на что
инструкция показывает, действительно есть.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="разбор docker-compose требует PyYAML")

GUIDE = Path("СЕРВЕР.md")
COMPOSE = Path("docker-compose.yml")
DOMAIN = "masterchance-bot.ru"


@pytest.fixture(scope="module")
def guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ── docker-compose ───────────────────────────────────────────────────────────

def test_compose_has_all_three_services(compose):
    assert set(compose["services"]) == {"updater", "web", "bot"}


def test_site_is_not_exposed_to_the_internet_directly(compose):
    """
    Порт сайта должен слушать только localhost: наружу пускает nginx, который
    держит сертификат. Иначе 8080 торчал бы в интернет по голому http.
    """
    for port in compose["services"]["web"]["ports"]:
        assert str(port).startswith("127.0.0.1:"), f"порт открыт наружу: {port}"


def test_only_the_updater_writes_snapshots(compose):
    """dist/ нужен тому, кто публикует; остальным он ни к чему."""
    assert any("dist" in v for v in compose["services"]["updater"]["volumes"])
    for name in ("web", "bot"):
        assert not any("dist" in v for v in compose["services"][name]["volumes"])


def test_every_service_shares_the_same_database(compose):
    for name, svc in compose["services"].items():
        assert any(v.startswith("./data:") for v in svc["volumes"]), name


def test_services_survive_a_reboot(compose):
    for name, svc in compose["services"].items():
        assert svc.get("restart") == "unless-stopped", name


# ── инструкция ───────────────────────────────────────────────────────────────

def test_guide_covers_the_whole_path(guide):
    """От пустого сервера до кнопки в Telegram — без пропущенных кусков."""
    for topic in ("Установите Docker", "Токен бота", "Токен GitHub",
                  "docker compose up -d --build", "A-запись",
                  "setup_https.sh", "WEBAPP_URL", "BotFather"):
        assert topic in guide, f"в инструкции нет шага про «{topic}»"


def test_guide_uses_the_real_setting_name(guide):
    """
    Настройка называется UNIVERSITY. Инструкция когда-то писала UNIVERSITIES —
    такая строка молча игнорируется, и это невозможно заметить.
    """
    assert "UNIVERSITY=spbgu" in guide
    assert "UNIVERSITIES" not in guide


def test_guide_only_names_settings_that_exist(guide):
    """Каждая ПЕРЕМЕННАЯ=значение из инструкции должна быть известна конфигу."""
    from app.config.config import Settings

    known = {f.alias or name for name, f in Settings.model_fields.items()}
    known |= {"GITHUB_TOKEN", "DATA_DIR", "PARSER_PARALLELISM"}  # читаются скриптами

    mentioned = set(re.findall(r"^([A-Z][A-Z0-9_]{3,})=", guide, re.M))
    unknown = mentioned - known
    assert not unknown, f"инструкция задаёт неизвестные настройки: {unknown}"


def test_guide_points_at_files_that_exist(guide):
    """Ссылки на скрипты и файлы проекта не должны протухать."""
    referenced = set(re.findall(r"\b(?:scripts|deploy)/[\w./-]+", guide))
    missing = [r for r in referenced if not Path(r).exists()]
    assert not missing, f"инструкция ссылается на несуществующее: {missing}"


def test_guide_warns_about_the_token(guide):
    """Токен бота — полный доступ к нему; предупреждение обязано быть."""
    assert "Revoke current token" in guide
    assert "не вставляйте в код" in guide or "не вставляйте в чатах" in guide \
        or "Не отправляйте его в чатах" in guide


def test_guide_explains_the_https_requirement(guide):
    """Без этого человек потратит вечер, гадая, почему Mini App не открывается."""
    assert "только по `https://`" in guide


def test_guide_warns_about_the_old_updater_container(guide):
    """У кого обновлятор уже запущен старой командой — иначе будет два сразу."""
    assert "docker rm -f masterchance-updater" in guide


def test_guide_and_compose_agree_on_commands(guide):
    """Инструкция не должна учить командам, которых больше нет."""
    assert "docker compose logs -f updater" in guide
    assert "docker run -d --name masterchance-updater" not in guide.replace(
        "docker rm -f masterchance-updater", ""
    )


def test_makefile_matches_the_guide():
    """`make autoupdate` и инструкция должны поднимать одно и то же."""
    mk = Path("Makefile").read_text(encoding="utf-8")
    assert "docker compose up -d --build updater" in mk
    assert "docker run -d --name masterchance-updater" not in mk
