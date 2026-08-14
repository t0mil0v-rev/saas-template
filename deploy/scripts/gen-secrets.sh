#!/usr/bin/env bash
# Генерирует стойкие секреты и вписывает их в .env вместо заглушек CHANGE_ME.
# Идемпотентно: уже заданные (не-заглушечные) значения не трогает.
set -euo pipefail

ENV_FILE="${1:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
	echo "Файл $ENV_FILE не найден. Сначала: cp .env.example .env" >&2
	exit 1
fi

# Генератор: 48 байт энтропии в base64, без символов, ломающих .env.
gen() { openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-48; }

# Заменяет значение ключа только если сейчас там заглушка или пусто.
set_if_placeholder() {
	local key="$1" value="$2"
	local current
	current="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
	# Снимаем возможные кавычки.
	current="${current%\"}"; current="${current#\"}"
	case "${current,,}" in
		"" | "change_me" | "changeme" | "secret" | "password")
			# Экранируем спецсимволы sed в значении.
			local esc="${value//\\/\\\\}"; esc="${esc//&/\\&}"; esc="${esc//|/\\|}"
			if grep -qE "^${key}=" "$ENV_FILE"; then
				sed -i "s|^${key}=.*|${key}=${esc}|" "$ENV_FILE"
			else
				printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
			fi
			echo "  ✓ ${key} сгенерирован"
			;;
		*)
			echo "  · ${key} уже задан - пропускаю"
			;;
	esac
}

echo "Генерация секретов в ${ENV_FILE}:"
set_if_placeholder SECRET_KEY "$(gen)"
set_if_placeholder POSTGRES_PASSWORD "$(gen)"
set_if_placeholder REDIS_PASSWORD "$(gen)"

# REDIS_URL в шаблоне ссылается на ${REDIS_PASSWORD}. Docker Compose такую
# подстановку внутри .env НЕ выполняет, поэтому подставляем пароль явно.
redis_pw="$(grep -E '^REDIS_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
if grep -qE '^REDIS_URL=.*\$\{REDIS_PASSWORD\}' "$ENV_FILE"; then
	sed -i "s|redis://:\${REDIS_PASSWORD}@|redis://:${redis_pw}@|" "$ENV_FILE"
	echo "  ✓ REDIS_URL: подставлен пароль"
fi

chmod 600 "$ENV_FILE"
echo "Готово. Проверьте DOMAIN, PUBLIC_URL, TLS_MODE и BOOTSTRAP_ADMIN_* в ${ENV_FILE}."
