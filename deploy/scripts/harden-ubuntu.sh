#!/usr/bin/env bash
# Базовое усиление хоста Ubuntu 22.04+ под этот стек.
#
# Делает: файрвол (ufw) с минимумом портов, fail2ban для SSH, автоматические
# security-обновления, ужесточение sysctl, отключение парольного входа по SSH.
#
# Идемпотентно и консервативно. Запускать под root:  sudo bash harden-ubuntu.sh
# Каждый блок можно применять по отдельности - они независимы.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
	echo "Запустите под root: sudo bash $0" >&2
	exit 1
fi

# shellcheck disable=SC1091
. /etc/os-release 2>/dev/null || true
echo "==> Хардненинг ${PRETTY_NAME:-Ubuntu}"

# 1. Обновления
echo "==> Пакеты и автоматические обновления безопасности"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ufw fail2ban unattended-upgrades apt-listchanges
dpkg-reconfigure -f noninteractive unattended-upgrades

# 2. Файрвол
# Открываем только SSH и веб. Postgres/Redis наружу не публикуются вообще -
# они доступны лишь во внутренней docker-сети.
echo "==> Файрвол (ufw)"
SSH_PORT="${SSH_PORT:-22}"
if [[ ! "$SSH_PORT" =~ ^[0-9]+$ ]] || (( SSH_PORT < 1 || SSH_PORT > 65535 )); then
	echo "Некорректный SSH_PORT: $SSH_PORT" >&2
	exit 1
fi
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}/tcp" comment 'SSH'
ufw allow 80/tcp   comment 'HTTP (ACME + редирект)'
ufw allow 443/tcp  comment 'HTTPS'
ufw allow 443/udp  comment 'HTTP/3 (QUIC)'
ufw --force enable
ufw status verbose

# 3. fail2ban
echo "==> fail2ban для SSH"
cat > /etc/fail2ban/jail.d/ssh.local <<EOF
[sshd]
enabled  = true
port     = ${SSH_PORT}
maxretry = 5
findtime = 10m
bantime  = 1h
backend  = systemd
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

# 4. sysctl
echo "==> Сетевые параметры ядра"
cat > /etc/sysctl.d/99-saas-hardening.conf <<'EOF'
# Защита от IP-спуфинга
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
# Игнорировать ICMP-редиректы (защита от MITM)
net.ipv4.conf.all.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
# Не принимать source-routed пакеты
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
# SYN-cookies против SYN-flood
net.ipv4.tcp_syncookies = 1
# Логировать марсианские пакеты
net.ipv4.conf.all.log_martians = 1
# Расширить диапазон эфемерных портов (важно для нашего prefork + пул БД)
net.ipv4.ip_local_port_range = 1024 65535
# Больше очередь входящих соединений - под всплески трафика
net.core.somaxconn = 4096
net.ipv4.tcp_max_syn_backlog = 4096
# Быстрее переиспользовать TIME_WAIT сокеты
net.ipv4.tcp_fin_timeout = 20
EOF
sysctl --system >/dev/null
echo "  ✓ sysctl применён"

# 5. SSH
# Отключаем парольную аутентификацию ТОЛЬКО если у текущего пользователя уже
# есть authorized_keys - иначе можно закрыть себе доступ.
echo "==> Конфигурация SSH"
ADMIN_USER="${SUDO_USER:-root}"
if [[ "$ADMIN_USER" == "root" ]]; then
	ADMIN_HOME=/root
else
	ADMIN_HOME="$(getent passwd "$ADMIN_USER" | cut -d: -f6)"
fi
if [[ -n "$ADMIN_HOME" && -s "${ADMIN_HOME}/.ssh/authorized_keys" ]]; then
	cat > /etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
	if sshd -t 2>/dev/null; then
		if ! systemctl reload ssh 2>/dev/null && ! systemctl reload sshd 2>/dev/null; then
			rm -f /etc/ssh/sshd_config.d/99-hardening.conf
			echo "Не удалось перезагрузить SSH; конфигурация удалена" >&2
			exit 1
		fi
		echo "  ✓ Парольный вход по SSH отключён (остались только ключи)"
	else
		rm -f /etc/ssh/sshd_config.d/99-hardening.conf
		echo "  ! Конфиг SSH не прошёл проверку - изменения откатаны"
	fi
else
	echo "  ! authorized_keys не найдены - парольный вход НЕ отключён"
	echo "    Сначала добавьте SSH-ключ, затем запустите скрипт повторно."
fi

# 6. Docker
echo "==> Проверка Docker"
if ! command -v docker >/dev/null; then
	echo "  ! Docker не установлен. Установка:"
	echo "    curl -fsSL https://get.docker.com | sh"
else
	# Живое ограничение логов на уровне демона - страховка, если у сервиса
	# забыли задать драйвер логов.
	if [[ ! -f /etc/docker/daemon.json ]]; then
		mkdir -p /etc/docker
		cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "live-restore": true,
  "no-new-privileges": true
}
EOF
		systemctl restart docker
		echo "  ✓ /etc/docker/daemon.json создан (ротация логов, live-restore)"
	else
		echo "  · /etc/docker/daemon.json уже есть - не трогаю"
	fi
fi

echo ""
echo "==> Хардненинг завершён. Рекомендуется проверить SSH-доступ в НОВОЙ сессии,"
echo "    не закрывая текущую."
