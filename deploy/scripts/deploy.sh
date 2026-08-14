#!/usr/bin/env bash
# Полный цикл выкладки: проверка окружения → сборка → миграции → перезапуск →
# проверка здоровья. Безопасно запускать повторно.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

COMPOSE=(docker compose)

echo "==> 1/5  Проверка конфигурации"
bash deploy/scripts/check-env.sh

echo "==> 2/5  Сборка образов"
"${COMPOSE[@]}" build

echo "==> 3/5  Резервная копия БД (если контейнер запущен)"
if "${COMPOSE[@]}" ps postgres 2>/dev/null | grep -Eq "Up|running"; then
	bash deploy/scripts/backup.sh
else
	echo "  · postgres ещё не запущен: это первый запуск, копировать пока нечего"
fi

echo "==> 4/5  Миграции и запуск"
"${COMPOSE[@]}" up -d postgres redis
"${COMPOSE[@]}" up -d --force-recreate migrate
"${COMPOSE[@]}" wait migrate
"${COMPOSE[@]}" up -d --remove-orphans api mail-worker web caddy

echo "==> 5/5  Ожидание готовности"
deadline=$(($(date +%s) + 120))
until "${COMPOSE[@]}" exec -T api python -m app.cli healthcheck >/dev/null 2>&1; do
	if [[ $(date +%s) -gt $deadline ]]; then
		echo "  ✗ Сервис не поднялся за 120 c. Логи:" >&2
		"${COMPOSE[@]}" logs --tail=40 api >&2
		exit 1
	fi
	sleep 3
done

echo "==> Готово. Состояние:"
"${COMPOSE[@]}" ps
