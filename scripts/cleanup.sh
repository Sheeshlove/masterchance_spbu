#!/usr/bin/env bash
#
# Уборка на сервере: освободить место, ничего не сломав.
#
#     sudo bash scripts/cleanup.sh            обычная уборка
#     sudo bash scripts/cleanup.sh --deep     плюс все неиспользуемые образы
#
# Что забивает диск на этом проекте, по убыванию:
#   1. образы и кэш сборки Docker — каждый `docker compose up --build` оставляет
#      предыдущий образ висеть, а кэш buildkit растёт до гигабайтов;
#   2. логи контейнеров — Docker по умолчанию НЕ ротирует их вообще, а
#      обновлятор пишет каждые 3 часа годами;
#   3. кэш apt и журналы systemd.
#
# Чего скрипт НЕ трогает:
#   • data/ — рабочая база;
#   • dist/ — свежий снапшот (он один, имя фиксированное, не копится);
#   • docker volumes — данные лежат в bind-mount, но `prune --volumes` привычка
#     опасная, поэтому её здесь нет намеренно;
#   • запущенные контейнеры и их образы.
set -euo pipefail

DEEP=0
[ "${1:-}" = "--deep" ] && DEEP=1

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустите через sudo: sudo bash scripts/cleanup.sh" >&2; exit 1; }

docker_ok() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }
free_kb() { df -Pk / | awk 'NR==2{print $4}'; }
human()   { awk -v k="$1" 'BEGIN{ printf (k>1048576) ? "%.1f ГБ" : "%.0f МБ", (k>1048576) ? k/1048576 : k/1024 }'; }

before="$(free_kb)"

say "Сейчас на диске"
df -h / | awk 'NR==1||NR==2'

# ── 1. Что именно занимает место ────────────────────────────────────────────
if docker_ok; then
    say "Docker до уборки"
    docker system df 2>/dev/null || note "не удалось опросить docker"
fi

# ── 2. Docker: остановленные контейнеры, висячие образы, кэш сборки ─────────
if docker_ok; then
    say "Убираю остановленные контейнеры"
    docker container prune -f | tail -1

    say "Убираю висячие образы (остатки прошлых сборок)"
    docker image prune -f | tail -1

    say "Убираю кэш сборки"
    # Обычно это самое крупное: buildkit держит слои всех прошлых сборок.
    docker builder prune -af | tail -1

    if [ "$DEEP" -eq 1 ]; then
        say "Убираю все образы, не занятые запущенными контейнерами (--deep)"
        note "следующая сборка будет дольше — кэш придётся собрать заново"
        docker image prune -af | tail -1
    fi

    # ── 3. Логи контейнеров ────────────────────────────────────────────────
    say "Подрезаю разросшиеся логи контейнеров"
    big="$(find /var/lib/docker/containers -name '*-json.log' -size +20M 2>/dev/null || true)"
    if [ -z "$big" ]; then
        note "крупных логов нет"
    else
        echo "$big" | while read -r f; do
            [ -n "$f" ] || continue
            note "$(du -h "$f" | cut -f1)  $(basename "$(dirname "$f")" | cut -c1-12)"
            : > "$f"
        done
        note "подрезаны до нуля (сами контейнеры не тронуты)"
    fi
fi

# ── 4. Система ──────────────────────────────────────────────────────────────
say "Чищу кэш пакетов"
apt-get clean
note "готово"

if command -v journalctl >/dev/null 2>&1; then
    say "Подрезаю системные журналы до 200 МБ"
    journalctl --vacuum-size=200M 2>&1 | tail -1 || note "journald недоступен"
fi

# Логи Let's Encrypt: certbot пишет их при каждой попытке и продлении.
if [ -d /var/log/letsencrypt ]; then
    say "Убираю старые логи certbot"
    find /var/log/letsencrypt -name '*.log*' -mtime +30 -delete 2>/dev/null || true
    note "старше 30 дней — удалены"
fi

# ── 5. Итог ─────────────────────────────────────────────────────────────────
after="$(free_kb)"
freed=$(( after - before ))

say "Готово"
df -h / | awk 'NR==1||NR==2'
if [ "$freed" -gt 0 ]; then
    printf '\n    Освобождено: %s\n' "$(human "$freed")"
else
    printf '\n    Освобождать было нечего.\n'
fi

if docker_ok; then
    say "Docker после уборки"
    docker system df 2>/dev/null || true
fi

cat <<EOF

  Чтобы логи не росли снова, в docker-compose.yml задано ограничение на
  размер (10 МБ × 3 файла на сервис). Оно применяется при пересоздании
  контейнера:

      docker compose up -d

  Если места всё равно мало — посмотрите, что занимает:

      du -h -d1 / 2>/dev/null | sort -h | tail -15

EOF
