#!/usr/bin/env bash
# Воспроизводимая проверка восстановления, SMTP/TLS, нагрузки и HTTP-поверхности.
# Запускается только на отдельном Docker project и не использует рабочие тома.
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd -P)"
RUNTIME="${ROOT}/.academic-runtime"
RESULTS="${ROOT}/.academic-results"

for command in docker openssl curl; do
	command -v "$command" >/dev/null || {
		echo "Для academic drill нужна команда: $command" >&2
		exit 1
	}
done
docker compose version >/dev/null

if [[ -e .env ]]; then
	echo "Academic drill не заменяет существующий .env. Запускайте в чистом checkout." >&2
	exit 1
fi
if [[ -e "$RUNTIME" || -e "$RESULTS" ]]; then
	echo "Удалите .academic-runtime и .academic-results от предыдущего запуска." >&2
	exit 1
fi

case "$RUNTIME" in
	"$ROOT"/.academic-runtime) ;;
	*) echo "Некорректный runtime path" >&2; exit 1 ;;
esac

mkdir -p "$RUNTIME/certs" "$RUNTIME/backups" "$RESULTS"
chmod 755 "$RUNTIME" "$RUNTIME/certs"
chmod 700 "$RUNTIME/backups"
chmod 777 "$RESULTS"

export COMPOSE_PROJECT_NAME="saas-academic-${GITHUB_RUN_ID:-$$}"
export COMPOSE_FILE="${ROOT}/docker-compose.yml:${ROOT}/tests/ops/docker-compose.academic.yml"
COMPOSE=(docker compose)

psql_compose() {
	# Пароль раскрывается только внутри контейнера.
	# shellcheck disable=SC2016
	"${COMPOSE[@]}" exec -T postgres \
		sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql "$@"' _ "$@"
}

cleanup() {
	local code=$?
	"${COMPOSE[@]}" logs --no-color > "$RESULTS/compose.log" 2>&1 || true
	"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
	rm -f .env
	rm -rf -- "$RUNTIME"
	return "$code"
}
trap cleanup EXIT

cat > .env <<EOF
APP_NAME="Academic SaaS"
APP_ENV=staging
PUBLIC_URL=https://localhost:18443
DOMAIN=localhost
TLS_MODE=none
PROXY_HTTP_PORT=18080
PROXY_HTTPS_PORT=18443
SECRET_KEY=academic-drill-secret-key-with-more-than-32-characters
POSTGRES_USER=saas
POSTGRES_PASSWORD=academic-postgres-password
POSTGRES_DB=saas
POSTGRES_HOST=postgres
DB_SSLMODE=disable
REDIS_PASSWORD=academic-redis-password
REDIS_URL=redis://:academic-redis-password@redis:6379/0
API_WORKERS=2
TRUSTED_PROXIES=172.16.0.0/12
RATE_LIMIT_GLOBAL_PER_MIN=100000
RATE_LIMIT_AUTH_PER_MIN=1000
RATE_LIMIT_REVIEW_PER_HOUR=1000
REQUIRE_EMAIL_VERIFICATION=true
MAIL_TRANSPORT=smtp
MAIL_FROM="Academic SaaS <no-reply@example.test>"
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_SECURITY=starttls
SMTP_TIMEOUT_S=5
METRICS_ALLOWED_NETS=172.16.0.0/12
BOOTSTRAP_ADMIN_EMAIL=admin@example.test
BOOTSTRAP_ADMIN_PASSWORD=Academic-bootstrap-password-2026!
BOOTSTRAP_ORG_NAME="Acme"
BOOTSTRAP_ORG_SLUG=acme
BACKUP_DIR=$RUNTIME/backups
BACKUP_RETENTION_DAYS=2
EOF
chmod 600 .env
set -a
# shellcheck disable=SC1091
. ./.env
set +a

CERTS="$RUNTIME/certs"
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 2 \
	-subj "/CN=SaaS academic CA" \
	-addext "basicConstraints=critical,CA:TRUE" \
	-keyout "$CERTS/ca.key" -out "$CERTS/ca.crt" >/dev/null 2>&1

make_certificate() {
	local name="$1"
	local subject="$2"
	local san="$3"
	openssl req -newkey rsa:2048 -nodes -sha256 -subj "/CN=${subject}" \
		-addext "subjectAltName=${san}" \
		-keyout "$CERTS/${name}.key" -out "$CERTS/${name}.csr" >/dev/null 2>&1
	openssl x509 -req -sha256 -days 2 -in "$CERTS/${name}.csr" \
		-CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
		-copy_extensions copy -out "$CERTS/${name}.crt" >/dev/null 2>&1
}

make_certificate mailpit mailpit "DNS:mailpit,DNS:localhost,IP:127.0.0.1"
make_certificate tls localhost "DNS:localhost,IP:127.0.0.1"
chmod 644 "$CERTS"/*.crt "$CERTS"/*.key

echo "==> Поднимаю production Compose"
"${COMPOSE[@]}" up -d --build

deadline=$(($(date +%s) + 180))
until curl -fsS http://127.0.0.1:18080/api/health/ready >/dev/null; do
	if (( $(date +%s) > deadline )); then
		echo "Стек не стал ready за 180 секунд" >&2
		exit 1
	fi
	sleep 3
done

echo "==> Проверяю SMTP с обязательным STARTTLS"
curl -fsS -X POST http://127.0.0.1:18080/api/auth/register \
	-H "Content-Type: application/json" \
	-d '{"email":"audit@example.test","password":"Academic-test-password-2026!","full_name":"Audit User","org_name":"Audit Org"}' \
	> "$RESULTS/register.json"

deadline=$(($(date +%s) + 60))
until curl -fsS http://127.0.0.1:18025/api/v1/messages | grep -q 'audit@example.test'; do
	if (( $(date +%s) > deadline )); then
		echo "Письмо не появилось в Mailpit за 60 секунд" >&2
		exit 1
	fi
	sleep 2
done
openssl s_client -starttls smtp -connect 127.0.0.1:11025 \
	-CAfile "$CERTS/ca.crt" -verify_hostname localhost </dev/null \
	> "$RESULTS/smtp-tls.txt" 2>&1
grep -q "Verify return code: 0" "$RESULTS/smtp-tls.txt"

echo "==> Проверяю штатное backup/restore"
psql_compose -v ON_ERROR_STOP=1 \
	-U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
CREATE TABLE academic_restore_probe (id integer PRIMARY KEY, value text NOT NULL);
INSERT INTO academic_restore_probe VALUES (1, 'before-backup');
SQL
bash deploy/scripts/backup.sh
dump="$(find "$RUNTIME/backups" -maxdepth 1 -type f -name 'db-*.dump' -print -quit)"
[[ -n "$dump" ]]
psql_compose -v ON_ERROR_STOP=1 \
	-U "$POSTGRES_USER" -d "$POSTGRES_DB" \
	-c "UPDATE academic_restore_probe SET value = 'after-backup' WHERE id = 1"
restore_started="$(date +%s)"
printf 'yes\n' | SKIP_PRE_RESTORE_BACKUP=1 bash deploy/scripts/restore.sh "$dump"
restore_seconds=$(($(date +%s) - restore_started))
restored_value="$(psql_compose -At \
	-U "$POSTGRES_USER" -d "$POSTGRES_DB" \
	-c "SELECT value FROM academic_restore_probe WHERE id = 1")"
[[ "$restored_value" == "before-backup" ]]

echo "==> Переключаю Caddy на проверяемый TLS"
sed -i 's/^TLS_MODE=none$/TLS_MODE=internal/' .env
"${COMPOSE[@]}" up -d --force-recreate caddy
deadline=$(($(date +%s) + 60))
until curl -fsS --cacert "$CERTS/ca.crt" \
	https://localhost:18443/api/health/live >/dev/null; do
	if (( $(date +%s) > deadline )); then
		echo "TLS endpoint не стал доступен" >&2
		exit 1
	fi
	sleep 2
done
curl -fsS --cacert "$CERTS/ca.crt" -D "$RESULTS/tls-headers.txt" \
	-o /dev/null https://localhost:18443/
grep -qi '^strict-transport-security:' "$RESULTS/tls-headers.txt"
grep -qi '^content-security-policy:' "$RESULTS/tls-headers.txt"

echo "==> Запускаю нагрузочный smoke"
docker run --rm --network host \
	-e BASE_URL=https://127.0.0.1:18443 \
	-v "$ROOT/tests/ops:/scripts:ro" -v "$RESULTS:/results" \
	grafana/k6:2.2.0@sha256:9bd01d6941fca969cb61bb57d2da5ee9b385fe2aa8881df3798c196564d6ace6 \
	run --summary-export=/results/k6-summary.json /scripts/load.js

echo "==> Запускаю OWASP ZAP baseline"
docker run --rm --network host -v "$RESULTS:/zap/wrk:rw" \
	ghcr.io/zaproxy/zaproxy:stable@sha256:781a2bdaea47324e7bab583e2263f21d257b0aee61ed51521a5be45f5f5081ef \
	zap-baseline.py -t https://127.0.0.1:18443 -m 1 -I \
	-r zap.html -J zap.json -w zap.md \
	-z "-config connection.sslCert.ignoreCertErrors=true"

cat > "$RESULTS/summary.json" <<EOF
{
  "status": "passed",
  "restore_seconds": $restore_seconds,
  "smtp_starttls": true,
  "tls_certificate_verified": true,
  "load_thresholds": "passed",
  "zap_baseline": "passed"
}
EOF

echo "Academic validation passed. Результаты: $RESULTS"
