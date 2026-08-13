"""
Тесты Telegram Mini App.

Две разные вещи: что сайт корректно ведёт себя внутри Telegram и снаружи, и
что бот не падает, когда адрес мини-аппа не настроен или настроен неверно.
Плюс сторож на секреты — токен в репозитории не должен появиться никогда.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app.config.config import Settings

STATIC = Path("app/presentation/web/static")
TEMPLATES = Path("app/presentation/web/templates")


# ── секреты ──────────────────────────────────────────────────────────────────

def test_no_bot_token_is_committed():
    """
    Токен бота выглядит как 123456789:AA... — ни одного такого в репозитории.

    Проверяются отслеживаемые файлы, а не рабочая папка: .env лежит рядом,
    но в git его нет, и именно это здесь важно.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
    ).stdout.split("\0")

    token_like = re.compile(r"\b\d{8,12}:AA[A-Za-z0-9_-]{30,}")
    offenders = []
    for name in filter(None, tracked):
        path = Path(name)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if token_like.search(text):
            offenders.append(name)

    assert not offenders, f"похоже на токен бота в: {offenders}"


def test_env_file_is_ignored_by_git():
    done = subprocess.run(["git", "check-ignore", ".env"], capture_output=True)
    assert done.returncode == 0, ".env не игнорируется — токен может уехать в репозиторий"


def test_example_env_has_no_real_token():
    example = Path(".env.example").read_text(encoding="utf-8")
    assert "BOT_TOKEN=" in example
    assert "/revoke" in example, "нет напоминания, что делать при утечке"
    assert not re.search(r"\b\d{8,12}:AA[A-Za-z0-9_-]{30,}", example)


# ── настройка адреса ─────────────────────────────────────────────────────────

def test_webapp_disabled_when_url_absent(monkeypatch):
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    assert Settings(_env_file=None).webapp_ready is False


def test_webapp_rejects_plain_http(monkeypatch):
    """Telegram открывает Mini App только по https — http молча не заработает."""
    monkeypatch.setenv("WEBAPP_URL", "http://example.ru")
    assert Settings(_env_file=None).webapp_ready is False


def test_webapp_accepts_https(monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://example.ru")
    s = Settings(_env_file=None)
    assert s.webapp_ready is True
    assert s.webapp_url == "https://example.ru"


# ── кнопка в боте ────────────────────────────────────────────────────────────

@pytest.fixture
def bot_module():
    pytest.importorskip("aiogram", reason="бот использует aiogram")
    from app.presentation import bot

    return bot


def test_no_button_without_url(bot_module, monkeypatch):
    """Бот обязан работать и без мини-аппа, а не падать на старте."""
    monkeypatch.setattr(bot_module.settings, "webapp_url", None, raising=False)
    assert bot_module.webapp_markup() is None


def test_no_button_for_http_url(bot_module, monkeypatch):
    monkeypatch.setattr(bot_module.settings, "webapp_url", "http://example.ru", raising=False)
    assert bot_module.webapp_markup() is None


def test_button_points_at_the_configured_url(bot_module, monkeypatch):
    monkeypatch.setattr(bot_module.settings, "webapp_url", "https://example.ru", raising=False)
    markup = bot_module.webapp_markup()

    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.web_app.url == "https://example.ru"
    assert button.text == bot_module.WEBAPP_BUTTON_TEXT


# ── сайт внутри и снаружи Telegram ───────────────────────────────────────────

def test_sdk_is_loaded_from_telegram_org():
    """Со своей копией SDK Mini App не работает — источник обязан быть telegram.org."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "https://telegram.org/js/telegram-web-app.js" in base


def test_script_bails_out_outside_telegram():
    """
    В обычном браузере window.Telegram.WebApp тоже существует, но platform
    равен "unknown". Без этой проверки живой сайт остался бы без шапки.
    """
    js = (STATIC / "telegram.js").read_text(encoding="utf-8")
    assert '"unknown"' in js
    assert "classList.add(\"tg\")" in js


def test_telegram_styles_are_scoped():
    """Правила мини-аппа не должны трогать обычный сайт."""
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    tg_rules = [line for line in css.splitlines() if line.strip().startswith(".tg")]
    assert tg_rules, "стилей для Mini App нет"
    for rule in tg_rules:
        assert rule.strip().startswith(".tg "), f"правило вне класса .tg: {rule}"


def test_ios_zoom_guard_on_input():
    """Поле < 16px заставляет iOS зумить страницу при фокусе."""
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert "font-size: 16px" in css


def test_page_still_renders_without_telegram(web_client):
    """Главная обязана оставаться обычной страницей вне Telegram."""
    html = web_client.get("/").text
    assert "telegram-web-app.js" in html
    assert "Посмотри свои шансы" in html


# ── развёртывание: домен, nginx, certbot ─────────────────────────────────────

DOMAIN = "masterchance-bot.ru"


def test_nginx_config_exists_for_the_domain():
    conf = Path(f"deploy/nginx/{DOMAIN}.conf")
    assert conf.is_file(), "нет конфига nginx для домена"
    text = conf.read_text(encoding="utf-8")
    assert f"server_name {DOMAIN} www.{DOMAIN};" in text
    assert "proxy_pass http://127.0.0.1:8080;" in text


def test_nginx_forwards_the_scheme():
    """
    Без X-Forwarded-Proto приложение за nginx считает, что работает по http.
    Telegram Mini App на http-ссылке не открывается.
    """
    text = Path(f"deploy/nginx/{DOMAIN}.conf").read_text(encoding="utf-8")
    assert "X-Forwarded-Proto $scheme" in text


def test_nginx_config_has_no_tls_block():
    """TLS дописывает certbot и он же обновляет сертификат — руками не лезем."""
    text = Path(f"deploy/nginx/{DOMAIN}.conf").read_text(encoding="utf-8")
    assert "ssl_certificate" not in text
    assert "listen 443" not in text


def test_setup_script_is_executable_and_valid():
    script = Path("scripts/setup_https.sh")
    assert script.is_file()
    assert script.stat().st_mode & 0o111, "скрипт не исполняемый"
    done = subprocess.run(["bash", "-n", str(script)], capture_output=True)
    assert done.returncode == 0, done.stderr.decode()


def test_setup_script_checks_dns_before_certbot():
    """
    Главная причина падений на этом шаге — не доехавшая A-запись.
    Проверка обязана идти раньше certbot, иначе пользователь получит
    невнятную ошибку вместо понятной.
    """
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    assert text.index("getent ahostsv4") < text.index("certbot --nginx")


def test_docs_and_example_use_the_same_domain():
    for path in (".env.example", "СЕРВЕР.md"):
        assert DOMAIN in Path(path).read_text(encoding="utf-8"), f"{path} не знает домена"


def test_setup_script_verifies_certbot_before_using_it():
    """
    apt-овый certbot ломается, если в системном Python уже стоит pip-овый
    urllib3 2.x: `ImportError: cannot import name 'appengine'`. Скрипт обязан
    проверить, что certbot вообще запускается, и переставить его в изоляции.
    """
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    assert "certbot --version" in text, "certbot используется без проверки"
    assert "snap install --classic certbot" in text or "/opt/certbot" in text


def test_setup_script_keeps_renewal_working_for_the_venv_install():
    """snap и apt продлевают сертификат сами, отдельное venv — нет."""
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    if "/opt/certbot" in text:
        assert "certbot renew" in text, "нет продления для venv-установки"


def test_setup_script_checks_every_a_record_not_just_the_first():
    """
    Если рядом с нужной A-записью осталась чужая, DNS отдаёт их по очереди:
    часть посетителей уедет на чужой сервер, а Let's Encrypt провалит проверку.
    Скрипт обязан смотреть все адреса, а не первый попавшийся.
    """
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    assert "sort -u" in text, "адреса не собираются целиком"
    assert "NR==1" not in text, "берётся только первая A-запись"
    assert "лишние A-записи" in text, "нет понятного сообщения про лишнюю запись"


def test_setup_script_checks_the_www_record_too():
    """Сертификат выпускается на оба имени — значит проверять надо оба."""
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    assert 'check_records "www.${DOMAIN}"' in text
