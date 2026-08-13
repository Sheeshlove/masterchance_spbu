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
    js = (STATIC / "telegram.js").read_text(encoding="utf-8")
    assert "https://telegram.org/js/telegram-web-app.js" in js


def test_page_itself_never_requests_telegram_org(web_client):
    """
    telegram.org в России недоступен, и запрос к нему не отклоняется, а висит.
    Тегом <script> он раньше стоял у всех: страница рисовалась, но событие
    load не наступало — у людей вечно крутился индикатор, а измерялки
    скорости показывали ноль. Теперь SDK подтягивается из telegram.js и
    только внутри Telegram.
    """
    for url in ("/", "/how", "/mechanism"):
        assert "telegram.org" not in web_client.get(url).text, url


def test_sdk_is_requested_only_inside_telegram():
    """Признак Telegram — параметры tgWebApp… в адресе, запомненные на сессию."""
    js = (STATIC / "telegram.js").read_text(encoding="utf-8")
    assert "tgWebApp" in js
    assert "sessionStorage" in js
    # выход до обращения к сети
    assert js.index("insideTelegram()) return") < js.index("appendChild")


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
    assert "/static/telegram.js" in html
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


def test_setup_script_verifies_nginx_loaded_the_config():
    """
    Конфиг может лежать в правильной папке и не подключаться: у одних сборок
    nginx.conf читает sites-enabled, у других только conf.d. `nginx -t` в
    обоих случаях проходит — файл просто не парсится, — а certbot потом не
    находит server block. Проверять надо `nginx -T`, то есть итоговую
    конфигурацию.
    """
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    assert "nginx -T" in text, "нет проверки итоговой конфигурации"
    assert "sites-enabled" in text and "conf.d" in text, "выбран только один путь"


def test_setup_script_does_not_reissue_an_existing_certificate():
    """
    У Let's Encrypt есть лимит на число выпусков для домена. Если сертификат
    уже есть, а не установился — его надо доустановить, а не выпускать снова.
    """
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    assert "letsencrypt/live/${DOMAIN}/fullchain.pem" in text
    assert "certbot install --cert-name" in text


def test_setup_script_can_add_the_include_itself():
    """
    Встречаются nginx.conf, которые не подключают ни conf.d, ни sites-enabled,
    а только свой единственный файл. Тогда наш конфиг лежит на диске и не
    читается — скрипт должен дописать include, а не сдаться.
    """
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    assert "include /etc/nginx/conf.d/*.conf" in text
    assert "nginx.conf.bak-" in text, "нет резервной копии перед правкой nginx.conf"


def test_setup_script_removes_the_unused_copy():
    """Две расходящиеся копии одного сайта в разных папках — источник путаницы."""
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    assert 'rm -f "/etc/nginx/sites-enabled/${DOMAIN}.conf"' in text


def test_setup_script_does_not_confuse_a_failed_check_with_a_missing_domain():
    """
    `nginx -T` может не отработать сам по себе. Это «не знаем», а не «домена
    нет», и останавливать установку из-за незнания нельзя: certbot скажет
    точнее. Раньше stderr глушился, и любая ошибка команды читалась как
    отсутствие конфига.
    """
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    assert "DUMP_OK" in text, "нет различия между «не проверили» и «не нашли»"
    # проверка больше не обрывает работу
    assert "fail \"nginx не подхватил конфиг" not in text


def test_nginx_compresses_text_assets():
    """htmx — 48 КБ; без сжатия это заметно на мобильной сети."""
    text = Path(f"deploy/nginx/{DOMAIN}.conf").read_text(encoding="utf-8")
    assert "gzip on;" in text
    assert "application/javascript" in text


def test_nginx_caches_static_but_not_forever():
    """
    Шрифты неизменны — их можно кешировать надолго. Стили меняются при
    обновлении проекта, поэтому им короткий кеш: иначе правка не доедет.
    """
    text = Path(f"deploy/nginx/{DOMAIN}.conf").read_text(encoding="utf-8")
    assert "woff2" in text and "expires 30d" in text
    assert "location /static/" in text and "expires 1h" in text


def test_nginx_serves_static_from_its_own_cache():
    """
    Ни одной директивы root здесь нет — вся статика уходит в python через
    proxy_pass. То есть восемь запросов на холодную загрузку конкурируют за
    единственный воркер uvicorn с самой отрисовкой страниц. Кеш nginx снимает
    это, не заводя копию файлов на диске (репозиторий лежит в домашнем
    каталоге root, куда www-data не заглянет).
    """
    text = Path(f"deploy/nginx/{DOMAIN}.conf").read_text(encoding="utf-8")

    assert "proxy_cache_path" in text, "зона кеша не объявлена"
    # Директива уровня http: внутри server nginx её не примет.
    before_server = text.split("server {", 1)[0]
    assert "proxy_cache_path" in before_server, "proxy_cache_path оказался внутри server"

    assert text.count("proxy_cache mc_static;") >= 2, "кеш включён не для всей статики"
    assert "proxy_cache_use_stale" in text, (
        "без use_stale занятый python превращается в 502 вместо отдачи из кеша"
    )


def test_nginx_keeps_the_connection_to_the_app_alive():
    """
    По умолчанию nginx ходит к апстриму по HTTP/1.0 и закрывает соединение
    после каждого файла. Шрифтов несколько — это лишние установки соединения.
    """
    text = Path(f"deploy/nginx/{DOMAIN}.conf").read_text(encoding="utf-8")
    fonts_block = re.search(r"location ~\* \^/static/fonts.*?\n    \}", text, re.S)
    assert fonts_block, "блок шрифтов не найден"
    assert "proxy_http_version 1.1;" in fonts_block.group(0)


def test_setup_script_turns_on_http2():
    """
    TLS-блок пишет certbot, и http2 он не включает никогда. Без него страница
    едет по HTTP/1.1, где браузер держит к домену максимум шесть соединений, —
    на канале с большой задержкой это лишние сотни миллисекунд.
    """
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")

    assert "http2" in text, "HTTP/2 нигде не включается"
    # Синтаксис разный до и после nginx 1.25.1, а на Ubuntu 24.04 приезжает
    # 1.24 — то есть нужен именно старый вариант, и одного мало.
    assert "http2 on;" in text, "нет варианта для nginx ≥ 1.25.1"
    assert "1.25.1" in text, "версия не проверяется — на nginx 1.24 конфиг не соберётся"

    # Включается после выпуска сертификата: до него TLS-блока ещё нет.
    assert text.index("certbot --nginx") < text.index('say "Включаю HTTP/2"')


def test_http2_never_breaks_a_working_site():
    """
    HTTP/2 — ускорение, а не условие работы. Если nginx правку не принял,
    сайт должен остаться на HTTP/1.1, а не лечь.
    """
    text = Path("scripts/setup_https.sh").read_text(encoding="utf-8")
    block = text.split('say "Включаю HTTP/2"', 1)[1]

    assert "nginx -t" in block, "правка применяется без проверки конфигурации"
    assert 'mv -f "$HTTP2_BAK"' in block, "нет отката к рабочему конфигу"


def test_diagnostic_checks_which_markup_is_deployed():
    """
    «Дизайн не подтягивается» чаще всего значит, что контейнер не пересобран
    и отдаёт старую разметку. Диагностика должна отличать это от поломки.
    """
    text = Path("scripts/diagnose_server.sh").read_text(encoding="utf-8")
    assert 'href="/static/styles.css"' in text
    assert "не пересобран" in text


def test_www_redirects_to_the_canonical_host():
    """Один сайт по двум адресам — на это ругаются проверки и поисковики."""
    text = Path(f"deploy/nginx/{DOMAIN}.conf").read_text(encoding="utf-8")
    assert f"if ($host = www.{DOMAIN})" in text
    assert f"return 301 https://{DOMAIN}$request_uri;" in text


def test_www_redirect_cannot_break_certificate_renewal():
    """
    Серверный `if` перехватил бы и /.well-known/acme-challenge/, по которому
    Let's Encrypt подтверждает домен www при каждом продлении. Редирект обязан
    жить внутри location /, где своя локация certbot имеет приоритет выше.
    """
    import re

    text = Path(f"deploy/nginx/{DOMAIN}.conf").read_text(encoding="utf-8")
    # на уровне server (отступ 4) никаких if быть не должно
    assert not re.search(r"^\s{4}if \(", text, re.M), "if на уровне server"
    body = re.search(r"location / \{(.*?)\n    \}", text, re.S).group(1)
    assert "return 301" in body, "редирект не внутри location /"
