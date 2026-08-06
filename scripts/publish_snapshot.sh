#!/usr/bin/env bash
#
# Публикация снапшота БД в GitHub Releases.
#
# Снапшот всегда лежит в одном и том же релизе с фиксированным тегом (по
# умолчанию `data`) и просто перезаписывается. Так адрес файла никогда не
# меняется, и десктоп-клиенты продолжают скачивать его после любых других
# релизов. Если класть снапшот в /releases/latest/, ссылка сломается сразу
# после публикации новой версии приложения.
#
# Использование:
#   export GITHUB_TOKEN=ghp_...          # токен с правом Contents: write
#   scripts/publish_snapshot.sh [путь/к/master-snapshot.db.gz]
#
# Необязательные переменные:
#   GITHUB_REPO   владелец/репозиторий      (по умолчанию Sheeshlove/masterchance_spbu)
#   SNAPSHOT_TAG  тег релиза со снапшотом   (по умолчанию data)

set -euo pipefail

FILE="${1:-dist/master-snapshot.db.gz}"
REPO="${GITHUB_REPO:-Sheeshlove/masterchance_spbu}"
TAG="${SNAPSHOT_TAG:-data}"
ASSET_NAME="$(basename "$FILE")"
API="https://api.github.com/repos/${REPO}"

die() { echo "❌ $*" >&2; exit 1; }

[ -n "${GITHUB_TOKEN:-}" ] || die "Не задан GITHUB_TOKEN. См. СЕРВЕР.md, шаг 7."
[ -f "$FILE" ] || die "Файл '$FILE' не найден. Сначала выполните build_snapshot.py."

auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github+json")

# Достаём одно поле из JSON без jq (его может не быть на сервере).
json_get() { python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$1','') or '')"; }

echo "→ Репозиторий: ${REPO}, тег релиза: ${TAG}, файл: ${FILE}"

# ── 1. Найти релиз с нужным тегом, при отсутствии — создать ────────────────
release_json="$(curl -fsS "${auth[@]}" "${API}/releases/tags/${TAG}" 2>/dev/null || true)"
release_id="$(printf '%s' "$release_json" | json_get id 2>/dev/null || true)"

if [ -z "$release_id" ]; then
    echo "→ Релиза с тегом '${TAG}' нет — создаём."
    body='{"tag_name":"'"${TAG}"'","name":"Данные для приложения","body":"Снапшот базы с посчитанными вероятностями. Файл обновляется автоматически, ссылка не меняется.","draft":false,"prerelease":false}'
    release_id="$(curl -fsS -X POST "${auth[@]}" -d "$body" "${API}/releases" | json_get id)"
    [ -n "$release_id" ] || die "Не удалось создать релиз. Проверьте права токена (Contents: write)."
fi
echo "→ Релиз найден, id=${release_id}"

# ── 2. Удалить прежний файл с тем же именем (иначе GitHub ответит 422) ─────
old_id="$(curl -fsS "${auth[@]}" "${API}/releases/${release_id}/assets" \
    | python3 -c "
import json,sys
name = '${ASSET_NAME}'
for a in json.load(sys.stdin):
    if a.get('name') == name:
        print(a['id']); break
")"
if [ -n "$old_id" ]; then
    echo "→ Удаляем прошлую версию файла (id=${old_id})."
    curl -fsS -X DELETE "${auth[@]}" "${API}/releases/assets/${old_id}" >/dev/null
fi

# ── 3. Загрузить новый файл ───────────────────────────────────────────────
size_mb="$(du -m "$FILE" | cut -f1)"
echo "→ Загружаем ${ASSET_NAME} (${size_mb} МБ)…"
upload_url="https://uploads.github.com/repos/${REPO}/releases/${release_id}/assets?name=${ASSET_NAME}"
download_url="$(curl -fsS -X POST "${auth[@]}" \
    -H "Content-Type: application/gzip" \
    --data-binary @"${FILE}" "${upload_url}" | json_get browser_download_url)"

[ -n "$download_url" ] || die "Загрузка не удалась."

echo "✅ Готово. Клиенты будут скачивать данные отсюда:"
echo "   ${download_url}"
