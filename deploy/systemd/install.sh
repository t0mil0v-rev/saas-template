#!/usr/bin/env bash
# Устанавливает systemd-юниты для Docker-варианта стека:
#   * saas-compose.service  - поднимает стек при загрузке
#   * saas-backup.timer     - ежедневный бэкап
#   * saas-cleanup.timer    - почасовая уборка
#
# Предполагается, что репозиторий установлен в INSTALL_DIR (по умолчанию /opt/saas).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
	echo "Запустите под root: sudo bash $0" >&2
	exit 1
fi

INSTALL_DIR="${INSTALL_DIR:-/opt/saas}"
SRC="$(cd "$(dirname "$0")" && pwd)"

echo "==> Устанавливаю юниты (WorkingDirectory=${INSTALL_DIR})"

for unit in saas-compose.service saas-backup.service saas-backup.timer \
            saas-cleanup.service saas-cleanup.timer; do
	# Подставляем реальный путь установки вместо /opt/saas.
	sed "s|/opt/saas|${INSTALL_DIR}|g" "${SRC}/${unit}" > "/etc/systemd/system/${unit}"
	echo "  ✓ ${unit}"
done

systemctl daemon-reload
systemctl enable --now saas-compose.service
systemctl enable --now saas-backup.timer
systemctl enable --now saas-cleanup.timer

echo ""
echo "==> Готово. Статус:"
systemctl --no-pager status saas-compose.service | head -5 || true
echo ""
echo "Таймеры:"
systemctl list-timers 'saas-*' --no-pager || true
