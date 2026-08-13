#!/usr/bin/env bash
#
# «Сайт не открывается» — где именно рвётся цепочка.
#
#     sudo bash scripts/diagnose_server.sh
#
# Ничего не чинит и не меняет: только смотрит и говорит, что делать.
# Путь запроса: браузер → nginx (443) → контейнер web (127.0.0.1:8080) → база.
# Проверяем по звеньям, от ближнего к дальнему.
set -uo pipefail   # без -e: диагностика обязана дойти до конца, даже если шаг упал

DOMAIN="${DOMAIN:-masterchance-bot.ru}"
PORT="${UPSTREAM_PORT:-8080}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '    \033[31m✗\033[0m %s\n' "$*"; PROBLEMS+=("$*"); }
note() { printf '      %s\n' "$*"; }

PROBLEMS=()
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# ── 1. Контейнеры ───────────────────────────────────────────────────────────
say "Контейнеры"
if ! docker info >/dev/null 2>&1; then
    bad "Docker не отвечает"
    note "systemctl start docker"
else
    docker compose ps 2>/dev/null || true
    for svc in web updater bot; do
        state="$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$svc" '$1==s{print $2}')"
        case "$state" in
            running) ok "$svc работает" ;;
            "")      bad "$svc не создан";  note "docker compose up -d --build $svc" ;;
            *)       bad "$svc в состоянии «$state»"
                     note "docker compose logs --tail 40 $svc" ;;
        esac
    done
fi

# ── 2. Приложение внутри сервера ────────────────────────────────────────────
say "Приложение на 127.0.0.1:${PORT}"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null)"
if [ "$code" = "200" ]; then
    ok "отвечает 200"
else
    bad "не отвечает (код «${code:-нет ответа}»)"
    note "docker compose logs --tail 40 web"
    note "чаще всего контейнер не запустился или упал при старте"
fi

# ── 3. nginx ────────────────────────────────────────────────────────────────
say "nginx"
if ! command -v nginx >/dev/null 2>&1; then
    bad "nginx не установлен"
elif ! systemctl is-active --quiet nginx; then
    bad "nginx не запущен"
    note "systemctl status nginx --no-pager | tail -20"
    note "systemctl start nginx"
else
    ok "запущен"
    if nginx -t >/dev/null 2>&1; then
        ok "конфигурация валидна"
    else
        bad "конфигурация с ошибкой"
        nginx -t 2>&1 | sed 's/^/      /'
    fi

    dump="$(nginx -T 2>/dev/null)"
    if [ -z "$dump" ]; then
        note "'nginx -T' не дал вывода — проверить конфигурацию нечем"
    else
        printf '%s' "$dump" | grep -q "server_name.*${DOMAIN//./\\.}" \
            && ok "знает про $DOMAIN" \
            || bad "в конфигурации нет server_name $DOMAIN"
        printf '%s' "$dump" | grep -q "listen.*443" \
            && ok "слушает 443 (https)" \
            || bad "нет блока listen 443 — сертификат не установлен"
    fi
fi

# ── 4. Сертификат ───────────────────────────────────────────────────────────
say "Сертификат"
cert="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
if [ -f "$cert" ]; then
    until="$(openssl x509 -enddate -noout -in "$cert" 2>/dev/null | cut -d= -f2)"
    ok "есть, действует до ${until:-?}"
else
    bad "сертификата нет"
    note "sudo bash scripts/setup_https.sh"
fi

# ── 5. Снаружи ──────────────────────────────────────────────────────────────
say "Снаружи"
for url in "http://${DOMAIN}/healthz" "https://${DOMAIN}/healthz"; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null)"
    case "$code" in
        200)     ok "$url → 200" ;;
        301|302) ok "$url → $code (переадресация, это нормально для http)" ;;
        000|"")  bad "$url → нет ответа" ;;
        *)       bad "$url → $code" ;;
    esac
done

# ── 5a. Статика: доезжают ли стили ──────────────────────────────────────────
say "Оформление"
page="$(curl -s --max-time 15 "https://${DOMAIN}/" 2>/dev/null)"
if [ -z "$page" ]; then
    bad "главная страница не отдалась — проверить нечего"
else
    # Какая версия разметки реально отдаётся сервером
    if printf '%s' "$page" | grep -q 'href="/static/styles.css"'; then
        ok "ссылка на стили относительная (свежая версия)"
    elif printf '%s' "$page" | grep -q 'styles.css'; then
        link="$(printf '%s' "$page" | grep -o '[^"]*styles\.css' | head -1)"
        bad "ссылка на стили: ${link}"
        note "это старая версия страницы — контейнер не пересобран"
        note "docker compose up -d --build web"
    else
        bad "на странице вообще нет ссылки на styles.css"
    fi

    if printf '%s' "$page" | grep -q 'telegram-web-app.js" defer'; then
        ok "SDK Telegram не блокирует отрисовку"
    elif printf '%s' "$page" | grep -q 'telegram-web-app.js'; then
        bad "SDK Telegram подключён без defer — страница ждёт telegram.org"
        note "старая версия; пересоберите: docker compose up -d --build web"
    fi

    for asset in /static/styles.css /static/htmx.min.js /static/fonts.css; do
        hdr="$(curl -s -o /dev/null --max-time 15 \
               -w '%{http_code} %{size_download} %{content_type}' \
               "https://${DOMAIN}${asset}" 2>/dev/null)"
        set -- $hdr
        if [ "${1:-000}" = "200" ]; then
            ok "$asset → 200, ${2} байт"
        else
            bad "$asset → ${1:-нет ответа}"
        fi
    done
fi

# ── 6. Место на диске ───────────────────────────────────────────────────────
say "Диск"
use="$(df -P / | awk 'NR==2{print $5}' | tr -d '%')"
if [ "${use:-0}" -ge 95 ]; then
    bad "занято ${use}% — почти нет места"
    note "sudo bash scripts/cleanup.sh"
else
    ok "занято ${use}%"
fi

# ── Итог ────────────────────────────────────────────────────────────────────
say "Итог"
if [ ${#PROBLEMS[@]} -eq 0 ]; then
    printf '    Всё звенья цепочки в порядке.\n'
    printf '    Если в браузере всё равно пусто — почистите кеш или откройте\n'
    printf '    в режиме инкогнито: старая версия страницы могла закешироваться.\n'
else
    printf '    Найдено проблем: %d\n\n' "${#PROBLEMS[@]}"
    for p in "${PROBLEMS[@]}"; do printf '      • %s\n' "$p"; done
    printf '\n    Чинить сверху вниз: нижние звенья зависят от верхних.\n'
fi
printf '\n'
