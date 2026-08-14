# Деплой

Ниже обычный сценарий для Ubuntu 22.04+. Основной путь - Docker Compose.
Нативный запуск нужен только если Docker у вас запрещён или не подходит.

## Перед началом

Для небольшой установки достаточно 1 vCPU, 1 ГБ RAM и 20 ГБ диска. Для
публичного сервиса берите запас и настройте мониторинг.

Создайте отдельного пользователя, если работаете под root:

```bash
adduser deploy
usermod -aG sudo deploy
su - deploy
sudo bash deploy/scripts/harden-ubuntu.sh
```

Скрипт hardening открывает 22, 80 и 443, включает fail2ban и не отключает
парольный SSH-вход, пока не найдёт SSH-ключ.

## Docker Compose

### Установка Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

### Конфигурация и запуск

```bash
sudo mkdir -p /opt/saas
sudo chown $USER /opt/saas
git clone <repo-url> /opt/saas
cd /opt/saas

make env
$EDITOR .env
make check
make up
make health
```

В `.env` задайте production-значения:

```ini
APP_ENV=production
DOMAIN=app.example.com
PUBLIC_URL=https://app.example.com
TLS_MODE=acme
ACME_EMAIL=ops@example.com
BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_ADMIN_PASSWORD=<strong-password>
MAIL_TRANSPORT=smtp
SMTP_HOST=smtp.example.com
```

В production консольная почта запрещена: она печатает ссылки с токенами в
логи. Настройте SMTP.

После запуска наружу доступны только Caddy на 80 и 443. Postgres и Redis не
публикуют порты. Проверьте `https://app.example.com`.

### Автозапуск

```bash
sudo INSTALL_DIR=/opt/saas bash deploy/systemd/install.sh
```

Будут установлены service для Compose и таймеры бэкапа и очистки.

## Нативный запуск

Если Docker не нужен, установите Python 3.11, PostgreSQL и Caddy сами:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv postgresql caddy build-essential libpq-dev

sudo useradd --system --home /opt/saas --shell /usr/sbin/nologin saas
sudo mkdir -p /opt/saas
sudo chown saas /opt/saas
sudo -u saas git clone <repo-url> /opt/saas

cd /opt/saas/apps/api
sudo -u saas python3.11 -m venv .venv
sudo -u saas .venv/bin/pip install -r requirements.lock

sudo -u postgres createuser saas --pwprompt
sudo -u postgres createdb saas -O saas
```

Скопируйте `.env.example` в `.env`, заполните `POSTGRES_HOST=127.0.0.1` и
нужные секреты, затем примените миграции:

```bash
cd /opt/saas/apps/api
sudo -u saas .venv/bin/python -m app.cli migrate
```

За образец systemd-сервиса возьмите `deploy/systemd/saas-api.service`. Caddy
должен отдавать `apps/web/dist` и проксировать `/api` на `127.0.0.1:8000`.

## TLS

| `TLS_MODE` | Сценарий |
|---|---|
| `acme` | Публичный домен. Caddy сам получает сертификат. |
| `internal` | Внутренняя сеть. Положите `tls.crt` и `tls.key` в `deploy/certs`. |
| `none` | TLS завершает внешний прокси. Укажите его сети в `TRUSTED_PROXIES`. |

Для `none` прокси обязан передавать `X-Forwarded-For` и
`X-Forwarded-Proto`. Без доверенных адресов приложение не сможет корректно
ограничивать запросы по IP.

## Обновление

```bash
cd /opt/saas
git pull
make deploy
```

`make deploy` проверяет конфигурацию, делает резервную копию, собирает образы,
применяет миграции и ждёт healthcheck.

## Бэкапы и диагностика

```bash
make backup
make restore f=/var/backups/saas/db-YYYYMMDD-HHMMSS.dump
make logs-api
```

Проверяйте восстановление бэкапа на отдельной машине. Для внешнего хранения
задайте `BACKUP_AGE_RECIPIENT` и используйте шифрование age. Перед
восстановлением скрипт проверяет checksum и структуру дампа, сохраняет текущую
БД и останавливает пишущие процессы. `SKIP_PRE_RESTORE_BACKUP=1` допустим
только после отдельной подтверждённой копии.

| Симптом | Что проверить |
|---|---|
| API не запускается | `make logs-api`; конфигурация обычно сообщает точную причину. |
| Caddy отвечает 502 | `docker compose ps`, затем `make health`. |
| Ошибка too many clients | Уменьшите `DB_POOL_SIZE` или `API_WORKERS`. |
| Письма не приходят | `MAIL_TRANSPORT`, `SMTP_*`, SPF/DKIM/DMARC. |
| Не получается сертификат | DNS, порты 80/443 и `ACME_EMAIL`. |
