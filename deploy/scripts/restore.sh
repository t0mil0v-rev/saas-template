#!/usr/bin/env bash
# Восстановление БД из копии, снятой backup.sh.
#
#     ./deploy/scripts/restore.sh /var/backups/saas/db-YYYYMMDD-HHMMSS.dump[.age]
#
# ВНИМАНИЕ: операция ПЕРЕЗАПИСЫВАЕТ текущую базу. Требует подтверждения.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

FILE="${1:-}"
if [[ -z "$FILE" || ! -f "$FILE" ]]; then
	echo "Укажите файл копии: ./deploy/scripts/restore.sh <path-to-dump>" >&2
	exit 1
fi

if [[ -f .env ]]; then
	set -a
	# shellcheck disable=SC1091
	. ./.env
	set +a
fi
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
COMPOSE=(docker compose)
TMP=""

cleanup() {
	if [[ -n "$TMP" ]]; then
		rm -f "$TMP"
	fi
}
trap cleanup EXIT

# Проверяем контрольную сумму, если она рядом.
if [[ -f "${FILE}.sha256" ]]; then
	echo "==> Проверяю контрольную сумму"
	expected="$(awk 'NR == 1 { print $1 }' "${FILE}.sha256")"
	if [[ ! "$expected" =~ ^[0-9a-fA-F]{64}$ ]]; then
		echo "Некорректный файл контрольной суммы: ${FILE}.sha256" >&2
		exit 1
	fi
	actual="$(sha256sum "$FILE" | cut -d' ' -f1)"
	[[ "$actual" == "$expected" ]] || { echo "Контрольная сумма не совпадает" >&2; exit 1; }
fi

echo ""
echo "!!  Текущая база «${POSTGRES_DB:-saas}» будет ПЕРЕЗАПИСАНА содержимым:"
echo "!!  ${FILE}"
read -rp "Введите 'yes' для продолжения: " confirm
[[ "$confirm" == "yes" ]] || { echo "Отменено."; exit 1; }

# Расшифровываем age при необходимости - во временный файл с жёсткими правами.
DUMP="$FILE"
if [[ "$FILE" == *.age ]]; then
	command -v age >/dev/null || { echo "Нужен age для расшифровки" >&2; exit 1; }
	[[ -n "${BACKUP_AGE_IDENTITY:-}" ]] || { echo "Задайте BACKUP_AGE_IDENTITY (путь к приватному ключу age)" >&2; exit 1; }
	TMP="$(mktemp)"; chmod 600 "$TMP"
	echo "==> Расшифровываю дамп"
	age -d -i "$BACKUP_AGE_IDENTITY" -o "$TMP" "$FILE"
	DUMP="$TMP"
fi

echo "==> Проверяю структуру дампа"
"${COMPOSE[@]}" exec -T postgres pg_restore --list < "$DUMP" >/dev/null

if [[ "${SKIP_PRE_RESTORE_BACKUP:-0}" != "1" ]]; then
	echo "==> Сохраняю текущую базу перед восстановлением"
	bash deploy/scripts/backup.sh
fi

echo "==> Останавливаю процессы, которые пишут в базу"
"${COMPOSE[@]}" stop api mail-worker

echo "==> Восстанавливаю (существующие объекты пересоздаются)"
# Пароль раскрывается оболочкой контейнера и не попадает в host argv.
# shellcheck disable=SC2016
"${COMPOSE[@]}" exec -T postgres \
	sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_restore "$@"' _ \
	-U "${POSTGRES_USER:-saas}" \
	-d "${POSTGRES_DB:-saas}" \
	--clean --if-exists --no-owner --no-privileges --exit-on-error \
	< "$DUMP"

echo "==> Применяю миграции из текущей версии приложения"
"${COMPOSE[@]}" run --rm migrate python -m app.cli migrate

echo "==> Запускаю API и почтовый worker"
"${COMPOSE[@]}" up -d api mail-worker

echo "  ✓ Восстановление завершено"
