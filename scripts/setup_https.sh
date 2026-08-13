#!/usr/bin/env bash
#
# Домен + HTTPS для сайта. Запускать на сервере от root:
#
#     sudo bash scripts/setup_https.sh
#
# Делает: проверяет, что домен указывает на этот сервер, ставит nginx и
# certbot, включает конфиг из deploy/nginx, выпускает сертификат.
#
# Отдельный скрипт, а не строчки в инструкции, ровно из-за первой проверки:
# certbot без прописанной A-записи падает с невнятной ошибкой, и почти всё
# время на этом шаге уходит именно на неё.
set -euo pipefail

DOMAIN="${DOMAIN:-masterchance-bot.ru}"
EMAIL="${EMAIL:-Egorsheeshwork@yandex.ru}"
UPSTREAM_PORT="${UPSTREAM_PORT:-8080}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_SRC="${SCRIPT_DIR}/../deploy/nginx/${DOMAIN}.conf"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "запустите через sudo: sudo bash scripts/setup_https.sh"
[ -f "$CONF_SRC" ]  || fail "нет файла конфигурации: $CONF_SRC"

# ── 1. Домен обязан указывать на этот сервер ────────────────────────────────
say "Проверяю, куда указывает $DOMAIN"

server_ip="$(curl -fsS --max-time 10 https://api.ipify.org || true)"
[ -n "$server_ip" ] || fail "не удалось узнать внешний IP сервера (нет интернета?)"
echo "    IP этого сервера:  $server_ip"

domain_ip="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"
if [ -z "$domain_ip" ]; then
    fail "$DOMAIN никуда не указывает.
    В панели регистратора добавьте A-запись:
        имя: @   тип: A   значение: $server_ip
    Обновление занимает от минут до пары часов — потом запустите скрипт снова."
fi
echo "    $DOMAIN указывает на: $domain_ip"

if [ "$domain_ip" != "$server_ip" ]; then
    fail "$DOMAIN указывает на $domain_ip, а этот сервер — $server_ip.
    Исправьте A-запись у регистратора и запустите скрипт снова."
fi

# ── 2. Сайт должен уже работать локально ────────────────────────────────────
say "Проверяю, что сайт отвечает на 127.0.0.1:${UPSTREAM_PORT}"
if ! curl -fsS --max-time 5 "http://127.0.0.1:${UPSTREAM_PORT}/healthz" >/dev/null; then
    fail "сайт не отвечает. Сначала поднимите его:  docker compose up -d web"
fi
echo "    отвечает"

# ── 3. nginx и certbot ──────────────────────────────────────────────────────
say "Ставлю nginx и certbot"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx

# certbot из apt тянет зависимости в системный Python, а там нередко уже лежит
# pip-овый urllib3 2.x. Он перекрывает системный urllib3 1.x, и старый certbot
# падает на `ImportError: cannot import name 'appengine'` ещё до первой строчки
# работы. Поэтому ставим его в изоляции: сначала snap (он несёт свой Python и
# сам продлевает сертификат), при отсутствии snap — отдельное venv.
install_certbot() {
    if certbot --version >/dev/null 2>&1; then
        echo "    certbot уже работает: $(certbot --version 2>&1)"
        return
    fi

    if command -v certbot >/dev/null 2>&1; then
        echo "    системный certbot сломан (конфликт зависимостей) — переставляю"
        apt-get remove -y -qq certbot python3-certbot-nginx >/dev/null 2>&1 || true
        hash -r
    fi

    if command -v snap >/dev/null 2>&1 || apt-get install -y -qq snapd >/dev/null 2>&1; then
        if snap install --classic certbot >/dev/null 2>&1; then
            ln -sf /snap/bin/certbot /usr/bin/certbot
            hash -r
            if certbot --version >/dev/null 2>&1; then
                echo "    certbot поставлен через snap: $(certbot --version 2>&1)"
                return
            fi
        fi
        echo "    snap не сработал — ставлю certbot в отдельное окружение"
    fi

    # venv: свой Python, свои зависимости, системного не касается
    apt-get install -y -qq python3-venv
    rm -rf /opt/certbot
    python3 -m venv /opt/certbot
    /opt/certbot/bin/pip install --quiet --upgrade pip
    /opt/certbot/bin/pip install --quiet certbot certbot-nginx
    ln -sf /opt/certbot/bin/certbot /usr/bin/certbot
    hash -r
    certbot --version >/dev/null 2>&1 || fail "не удалось поставить certbot"
    echo "    certbot поставлен в /opt/certbot: $(certbot --version 2>&1)"

    # snap и apt заводят продление сами, venv — нет
    cat > /etc/cron.d/certbot-renew <<'CRON'
# Продление сертификата Let's Encrypt (certbot в /opt/certbot)
0 3,15 * * * root /usr/bin/certbot renew --quiet --nginx
CRON
    echo "    продление добавлено в /etc/cron.d/certbot-renew"
}

say "Проверяю certbot"
install_certbot

say "Включаю конфиг для $DOMAIN"
install -m 0644 "$CONF_SRC" "/etc/nginx/sites-available/${DOMAIN}.conf"
ln -sfn "/etc/nginx/sites-available/${DOMAIN}.conf" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
# дефолтный сайт перехватывает все запросы без server_name — он тут мешает
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl reload nginx

# ── 4. Сертификат ───────────────────────────────────────────────────────────
say "Выпускаю сертификат Let's Encrypt"
certbot --nginx \
    -d "$DOMAIN" -d "www.${DOMAIN}" \
    --non-interactive --agree-tos -m "$EMAIL" \
    --redirect

say "Проверяю результат"
code="$(curl -s -o /dev/null -w '%{http_code}' "https://${DOMAIN}/healthz")"
[ "$code" = "200" ] || fail "https://${DOMAIN}/healthz ответил $code (ожидался 200)"

cat <<EOF

  Готово. Сайт открыт по https://${DOMAIN}

  Осталось два шага:
    1) в .env добавьте строку
           WEBAPP_URL=https://${DOMAIN}
       и перезапустите бота:  docker compose up -d --build bot
    2) у @BotFather: /mybots → ваш бот → Bot Settings → Menu Button
       → пришлите https://${DOMAIN}

  Сертификат продлевается сам (certbot ставит таймер systemd).
EOF
