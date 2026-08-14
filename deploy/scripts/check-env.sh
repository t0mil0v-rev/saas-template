#!/usr/bin/env bash
# Предполётная проверка .env перед деплоем. Ловит самые опасные ошибки
# конфигурации до того, как контейнеры поднимутся. Код возврата != 0 при
# фатальных проблемах - удобно ставить в CI/деплой-скрипт.
set -euo pipefail

ENV_FILE="${1:-.env}"
errors=0
warns=0

fail() { echo "  ✗ $1" >&2; errors=$((errors + 1)); }
warn() { echo "  ! $1" >&2; warns=$((warns + 1)); }
ok()   { echo "  ✓ $1"; }

if [[ ! -f "$ENV_FILE" ]]; then
	echo "Файл $ENV_FILE не найден." >&2
	exit 1
fi

# Читаем значение ключа без source: проверка не исполняет содержимое .env.
val() {
	awk -v key="$1" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' "$ENV_FILE" \
		| sed 's/^"//; s/"$//'
}

echo "Проверка ${ENV_FILE}:"

APP_ENV="$(val APP_ENV)"
TLS_MODE="$(val TLS_MODE)"
PUBLIC_URL="$(val PUBLIC_URL)"

case "$APP_ENV" in
	development | staging | production) ;;
	*) fail "APP_ENV должен быть development|staging|production" ;;
esac

# секреты
for key in SECRET_KEY POSTGRES_PASSWORD REDIS_PASSWORD; do
	v="$(val "$key")"
	case "${v,,}" in
		"" | change_me | changeme | secret | password)
			fail "$key: заглушка или пусто - запустите deploy/scripts/gen-secrets.sh" ;;
		*)
			if [[ "$key" == "SECRET_KEY" && ${#v} -lt 32 ]]; then
				fail "SECRET_KEY короче 32 символов"
			elif [[ "$key" != "SECRET_KEY" && ${#v} -lt 16 ]]; then
				fail "$key короче 16 символов"
			else
				ok "$key задан"
			fi ;;
	esac
done

# права на файл
if command -v stat >/dev/null; then
	perm="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null || echo '')"
	if [[ -n "$perm" && "$perm" != "600" && "$perm" != "400" ]]; then
		warn ".env имеет права $perm - рекомендуется chmod 600 .env"
	fi
fi

# TLS
case "$TLS_MODE" in
	acme)
		[[ -z "$(val ACME_EMAIL)" ]] && warn "TLS_MODE=acme, но ACME_EMAIL пуст (Let's Encrypt будет анонимным)"
		[[ "$PUBLIC_URL" != https://* ]] && fail "TLS_MODE=acme требует https:// в PUBLIC_URL"
		ok "TLS: acme (Let's Encrypt / внутренний CA)"
		;;
	internal)
		if [[ ! -f deploy/certs/tls.crt || ! -f deploy/certs/tls.key ]]; then
			fail "TLS_MODE=internal, но нет deploy/certs/tls.crt и/или tls.key"
		else
			ok "TLS: internal (свой сертификат найден)"
		fi
		;;
	none)
		[[ -z "$(val TRUSTED_PROXIES)" ]] && fail "TLS_MODE=none без TRUSTED_PROXIES - реальный IP не определить"
		warn "TLS_MODE=none: HTTPS должен терминироваться вышестоящим прокси"
		;;
	*)
		fail "TLS_MODE должен быть acme|internal|none (сейчас: '$TLS_MODE')"
		;;
esac

# прод-специфика
if [[ "$APP_ENV" == "production" ]]; then
	[[ "$PUBLIC_URL" != https://* ]] && fail "production требует https:// в PUBLIC_URL"
	[[ "$(val ENABLE_API_DOCS)" == "true" ]] && warn "ENABLE_API_DOCS=true в production раскрывает схему API"
	mt="$(val MAIL_TRANSPORT)"
	rev="$(val REQUIRE_EMAIL_VERIFICATION)"
	if [[ "$mt" == "console" && "$rev" == "true" ]]; then
		fail "MAIL_TRANSPORT=console при REQUIRE_EMAIL_VERIFICATION=true - письма не уйдут, вход заблокируется"
	fi
	admin_pw="$(val BOOTSTRAP_ADMIN_PASSWORD)"
	case "${admin_pw,,}" in
		"" ) warn "BOOTSTRAP_ADMIN_PASSWORD пуст - администратор не создастся автоматически" ;;
		change_me | changeme ) fail "BOOTSTRAP_ADMIN_PASSWORD - заглушка" ;;
		*) [[ ${#admin_pw} -lt 12 ]] && fail "BOOTSTRAP_ADMIN_PASSWORD короче 12 символов" ;;
	esac
	if [[ "$(val MAIL_TRANSPORT)" == "smtp" ]]; then
		[[ -z "$(val SMTP_HOST)" ]] && fail "MAIL_TRANSPORT=smtp, но SMTP_HOST пуст"
		[[ -z "$(val MAIL_FROM)" ]] && fail "MAIL_TRANSPORT=smtp, но MAIL_FROM пуст"
	fi
fi

backup_recipient="$(val BACKUP_AGE_RECIPIENT)"
if [[ -n "$backup_recipient" ]]; then
	command -v age >/dev/null || fail "BACKUP_AGE_RECIPIENT задан, но age не установлен"
elif [[ "$APP_ENV" == "production" ]]; then
	warn "Резервные копии не шифруются: BACKUP_AGE_RECIPIENT пуст"
fi

echo "-------------------------------------------"
echo "Ошибок: $errors, предупреждений: $warns"
[[ $errors -gt 0 ]] && { echo "Исправьте ошибки перед деплоем." >&2; exit 1; }
echo "Проверка пройдена."
