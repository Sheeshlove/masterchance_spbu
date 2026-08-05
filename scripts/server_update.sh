#!/usr/bin/env bash
#
# Полный цикл обновления данных НА СЕРВЕРЕ. Именно этот скрипт вызывает cron.
#
#   1. update_lists.py    — скачать свежие списки и пересчитать Monte-Carlo
#                           (пересчёт уже внутри, отдельно run_monte_carlo.py
#                            запускать НЕ надо — это лишние 10 000 симуляций)
#   2. build_snapshot.py  — упаковать результат в master-snapshot.db.gz
#   3. publish_snapshot   — выложить файл в GitHub Releases
#
# Запуск (из каталога проекта):
#   scripts/server_update.sh
#
# Переменные (обычно лежат в .env рядом):
#   GITHUB_TOKEN  — токен для публикации; без него шаг 3 пропускается
#   IMAGE         — имя docker-образа (по умолчанию masterchance:local)
#   UNIVERSITY    — spbpu | spbgu

set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_DIR="$PWD"
IMAGE="${IMAGE:-masterchance:local}"
LOG_DIR="${PROJECT_DIR}/logs"
LOCK_FILE="/tmp/masterchance-update.lock"

mkdir -p "$LOG_DIR" "$PROJECT_DIR/data" "$PROJECT_DIR/dist"

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Не даём двум обновлениям идти одновременно: они подрались бы за одну
# SQLite-базу, а параллельные Chrome съели бы всю память.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    say "Предыдущее обновление ещё идёт — выходим."
    exit 0
fi

[ -f .env ] && ENV_ARG=(--env-file .env) || ENV_ARG=()

docker_run() {
    docker run --rm "${ENV_ARG[@]}" \
        -v "${PROJECT_DIR}/data:/app/data" \
        -v "${PROJECT_DIR}/dist:/app/dist" \
        "$IMAGE" "$@"
}

say "=== Обновление данных начато ==="

say "Шаг 1/3: скачиваем списки и считаем вероятности (это самая долгая часть)…"
docker_run update_lists.py ${UNIVERSITY:+--university=$UNIVERSITY}
say "Шаг 1/3 готов."

say "Шаг 2/3: собираем снапшот…"
docker_run build_snapshot.py
say "Шаг 2/3 готов."

if [ -n "${GITHUB_TOKEN:-}" ]; then
    say "Шаг 3/3: публикуем снапшот в GitHub…"
    "${PROJECT_DIR}/scripts/publish_snapshot.sh" "${PROJECT_DIR}/dist/master-snapshot.db.gz"
    say "Шаг 3/3 готов."
else
    say "Шаг 3/3 пропущен: не задан GITHUB_TOKEN (файл лежит в dist/, но не опубликован)."
fi

say "=== Обновление завершено успешно ==="
