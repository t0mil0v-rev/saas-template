#!/usr/bin/env bash
# Быстрая проверка живости всех компонентов. Возврат != 0, если что-то не так.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

COMPOSE=(docker compose)
fail=0

check() {
	local name="$1"; shift
	if "$@" >/dev/null 2>&1; then
		echo "  ✓ $name"
	else
		echo "  ✗ $name"
		fail=1
	fi
}

# Функция передаётся в check как команда, что статический анализ не видит.
# shellcheck disable=SC2329
api_container_running() {
	"${COMPOSE[@]}" ps --status running --services | grep -qx api
}

echo "Проверка компонентов:"
check "API-контейнер запущен" api_container_running
check "БД принимает подключения" "${COMPOSE[@]}" exec -T postgres pg_isready -q
check "API отвечает на /live" "${COMPOSE[@]}" exec -T api python -m app.cli healthcheck
check "API готов принимать трафик" curl -fsS --max-time 3 \
	http://127.0.0.1:8000/api/health/ready

# Caddy: проверяем только если контейнер существует (dev-режим его не поднимает).
if "${COMPOSE[@]}" ps caddy 2>/dev/null | grep -Eq "Up|running"; then
	check "Caddy жив" "${COMPOSE[@]}" exec -T caddy \
		wget -qO- http://127.0.0.1:2019/config/ --timeout=3
fi

if [[ $fail -eq 0 ]]; then
	echo "Все проверки пройдены."
else
	echo "Есть проблемы. Логи: make logs" >&2
fi
exit $fail
