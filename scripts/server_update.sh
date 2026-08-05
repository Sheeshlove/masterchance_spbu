#!/usr/bin/env bash
#
# Полный цикл обновления данных НА СЕРВЕРЕ. Именно этот скрипт вызывает cron.
#
#   1. update_lists.py    — скачать свежие списки (по каждому вузу)
#   2. run_monte_carlo.py — посчитать вероятности ОДИН раз по всей базе
#   3. build_snapshot.py  — упаковать результат в master-snapshot.db.gz
#   4. publish_snapshot   — выложить файл в GitHub Releases
#
# Почему пересчёт вынесен отдельно: update_lists.py умеет считать Monte-Carlo
# сам, но считает он по ВСЕЙ базе. При обновлении двух вузов подряд это дало бы
# 20 000 симуляций вместо 10 000, поэтому вузы обновляются с --no-monte-carlo,
# а пересчёт запускается один раз в конце.
#
# Запуск (из каталога проекта):
#   scripts/server_update.sh
#
# Переменные (обычно лежат в .env рядом):
#   GITHUB_TOKEN  — токен для публикации; без него шаг 4 пропускается
#   IMAGE         — имя docker-образа (по умолчанию masterchance:local)
#   UNIVERSITIES  — через пробел, напр. "spbpu spbgu" (по умолчанию — UNIVERSITY)

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

UNIVERSITIES="${UNIVERSITIES:-${UNIVERSITY:-spbpu}}"

say "=== Обновление данных начато (вузы: ${UNIVERSITIES}) ==="

say "Шаг 1/4: скачиваем списки (самая долгая часть)…"
for uni in $UNIVERSITIES; do
    say "  → вуз ${uni}…"
    docker_run update_lists.py "--university=${uni}" --no-monte-carlo
done
say "Шаг 1/4 готов."

say "Шаг 2/4: считаем вероятности (10 000 симуляций)…"
docker_run run_monte_carlo.py
say "Шаг 2/4 готов."

say "Шаг 3/4: собираем снапшот…"
docker_run build_snapshot.py
say "Шаг 3/4 готов."

if [ -n "${GITHUB_TOKEN:-}" ]; then
    say "Шаг 4/4: публикуем снапшот в GitHub…"
    "${PROJECT_DIR}/scripts/publish_snapshot.sh" "${PROJECT_DIR}/dist/master-snapshot.db.gz"
    say "Шаг 4/4 готов."
else
    say "Шаг 4/4 пропущен: не задан GITHUB_TOKEN (файл лежит в dist/, но не опубликован)."
fi

say "=== Обновление завершено успешно ==="
