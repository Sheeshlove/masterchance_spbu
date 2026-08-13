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

# Смотрим ВСЕ A-записи, а не первую попавшуюся. Если рядом с нужной осталась
# чужая (парковка регистратора, прошлый хостинг), DNS отдаёт их по очереди:
# половина посетителей уедет не туда, а Let's Encrypt почти наверняка попадёт
# на лишнюю и вернёт «unauthorized» с чужим IP в тексте ошибки.
check_records() {
    local name="$1" ips extra
    ips="$(getent ahostsv4 "$name" 2>/dev/null | awk '{print $1}' | sort -u)"

    if [ -z "$ips" ]; then
        fail "$name никуда не указывает.
    В панели регистратора добавьте A-запись:
        имя: $2   тип: A   значение: $server_ip
    Обновление занимает от минут до пары часов — потом запустите скрипт снова."
    fi

    echo "    $name -> $(echo "$ips" | tr '\n' ' ')"

    extra="$(echo "$ips" | grep -vx "$server_ip" || true)"
    if [ -n "$extra" ]; then
        fail "у $name есть лишние A-записи: $(echo "$extra" | tr '\n' ' ')
    Этот сервер — $server_ip, остальные ведут в другое место.
    Удалите лишние записи (имя «$2») в панели регистратора, оставьте только
    $server_ip, дождитесь обновления DNS и запустите скрипт снова.
    Пока их две, DNS отдаёт адреса по очереди: часть посетителей попадёт на
    чужой сервер, а выпуск сертификата будет срываться."
    fi
}

check_records "$DOMAIN" "@"
check_records "www.${DOMAIN}" "www"

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

# Куда класть конфиг, зависит от сборки nginx: у одних nginx.conf подключает
# sites-enabled, у других — только conf.d. Положить не туда особенно коварно:
# `nginx -t` пройдёт (файл просто не читается), сайт вроде работает, а certbot
# потом скажет «не нашёл server block» — потому что и правда не нашёл.
if grep -qE '^[[:space:]]*include[[:space:]]+/etc/nginx/sites-enabled/' /etc/nginx/nginx.conf; then
    install -d /etc/nginx/sites-available /etc/nginx/sites-enabled
    install -m 0644 "$CONF_SRC" "/etc/nginx/sites-available/${DOMAIN}.conf"
    ln -sfn "/etc/nginx/sites-available/${DOMAIN}.conf" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
    note "через sites-enabled"
    # дефолтный сайт перехватывает всё без server_name — он тут мешает
    rm -f /etc/nginx/sites-enabled/default
else
    install -d /etc/nginx/conf.d
    install -m 0644 "$CONF_SRC" "/etc/nginx/conf.d/${DOMAIN}.conf"
    note "через conf.d (sites-enabled этот nginx не подключает)"
    rm -f /etc/nginx/conf.d/default.conf
fi

nginx -t
systemctl reload nginx

# Единственная надёжная проверка: `nginx -T` печатает конфигурацию так, как её
# видит сам nginx. Если нашего server_name там нет — файл не подключён, и идти
# к certbot бессмысленно.
say "Проверяю, что nginx действительно видит $DOMAIN"
if ! nginx -T 2>/dev/null | grep -qE "server_name[[:space:]].*\b${DOMAIN//./\\.}\b"; then
    fail "nginx не подхватил конфиг для $DOMAIN.
    Файл на месте, но в рабочую конфигурацию он не попал. Посмотрите, что
    подключает главный конфиг:
        grep -n include /etc/nginx/nginx.conf
    и куда лёг наш файл:
        ls -l /etc/nginx/conf.d/ /etc/nginx/sites-enabled/ 2>/dev/null"
fi
note "видит"

# ── 4. Сертификат ───────────────────────────────────────────────────────────
# Выпуск и установка — разные шаги, и второй умеет падать отдельно от первого.
# Если сертификат уже выпущен (прошлый запуск дошёл до него, а установка не
# удалась), повторный выпуск не нужен и вреден: у Let's Encrypt есть лимит на
# число выпусков для домена. Доустанавливаем уже имеющийся.
if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    say "Сертификат уже выпущен — устанавливаю его в nginx"
    certbot install --cert-name "$DOMAIN" --nginx --non-interactive
    # --redirect у `install` нет; поворот на https включаем отдельно
    certbot --nginx -d "$DOMAIN" -d "www.${DOMAIN}" \
        --non-interactive --agree-tos -m "$EMAIL" \
        --keep-until-expiring --redirect
else
    say "Выпускаю сертификат Let's Encrypt"
    certbot --nginx \
        -d "$DOMAIN" -d "www.${DOMAIN}" \
        --non-interactive --agree-tos -m "$EMAIL" \
        --redirect
fi

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
