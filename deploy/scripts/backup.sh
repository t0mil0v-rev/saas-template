#!/usr/bin/env bash
# Резервная копия PostgreSQL с ротацией и опциональным шифрованием.
#
# Дамп снимается pg_dump в custom-формате (-Fc): он сжат, поддерживает
# частичное восстановление и параллельную загрузку. При заданном
# BACKUP_AGE_RECIPIENT дамп шифруется age - тогда копию можно безопасно
# держать даже вне защищённого периметра.
set -euo pipefail
cd "$(dirname "$0")/../.."

# Загружаем настройки из .env для дочерних процессов.
if [[ -f .env ]]; then
	set -a
	# shellcheck disable=SC1091
	. ./.env
	set +a
fi

BACKUP_DIR="${BACKUP_DIR:-/var/backups/saas}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
COMPOSE=(docker compose)
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="${BACKUP_DIR}/db-${STAMP}.dump"
PARTIAL="${TARGET}.partial"

cleanup() {
	rm -f "$PARTIAL" "${TARGET}.age.partial"
}
trap cleanup EXIT

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "==> Снимаю дамп → ${TARGET}"
# --clean --if-exists делают дамп самодостаточным для восстановления «начисто».
# Пароль раскрывается оболочкой контейнера и не попадает в host argv.
# shellcheck disable=SC2016
"${COMPOSE[@]}" exec -T postgres \
	sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_dump "$@"' _ \
	-U "${POSTGRES_USER:-saas}" \
	-d "${POSTGRES_DB:-saas}" \
	-Fc --clean --if-exists --no-owner --no-privileges \
	> "$PARTIAL"

# Проверяем, что дамп не пустой и читается pg_restore.
if [[ ! -s "$PARTIAL" ]]; then
	echo "  ✗ Дамп пуст - прерываю" >&2
	exit 1
fi
"${COMPOSE[@]}" exec -T postgres pg_restore --list < "$PARTIAL" >/dev/null
mv "$PARTIAL" "$TARGET"

FINAL="$TARGET"
if [[ -n "${BACKUP_AGE_RECIPIENT:-}" ]]; then
	if ! command -v age >/dev/null; then
		echo "  ✗ BACKUP_AGE_RECIPIENT задан, но age не установлен" >&2
		exit 1
	fi
	echo "==> Шифрую дамп (age)"
	age -r "$BACKUP_AGE_RECIPIENT" -o "${TARGET}.age.partial" "$TARGET"
	mv "${TARGET}.age.partial" "${TARGET}.age"
	rm -f "$TARGET"
	FINAL="${TARGET}.age"
fi

chmod 600 "$FINAL"
SIZE="$(du -h "$FINAL" | cut -f1)"
echo "  ✓ Готово: ${FINAL} (${SIZE})"

# Контрольная сумма для проверки целостности при восстановлении.
sha256sum "$FINAL" | cut -d' ' -f1 > "${FINAL}.sha256"

echo "==> Ротация: удаляю копии старше ${RETENTION_DAYS} дней"
find "$BACKUP_DIR" -name 'db-*.dump*' -type f -mtime "+${RETENTION_DAYS}" -print -delete

echo "==> Текущие копии:"
shopt -s nullglob
backups=("$BACKUP_DIR"/db-*)
if (( ${#backups[@]} == 0 )); then
	echo "  (пусто)"
else
	du -h "${backups[@]}"
fi
