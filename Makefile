#  SaaS Template - управление. Запускать на Ubuntu 22+ (или в WSL/Git Bash).
#  `make` без аргументов покажет список целей.
SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help
help: ## Показать список целей
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# Первичная настройка
.PHONY: env
env: ## Создать .env из шаблона и сгенерировать секреты
	@test -f .env || cp .env.example .env
	@./deploy/scripts/gen-secrets.sh
	@chmod 600 .env
	@echo "Готово. Отредактируйте DOMAIN, PUBLIC_URL и TLS_MODE в .env"

.PHONY: check
check: ## Проверить .env на заглушки и слабые секреты
	@./deploy/scripts/check-env.sh

# Production
.PHONY: up
up: deploy ## Безопасно собрать и поднять весь стек

.PHONY: down
down: ## Остановить стек (данные сохраняются)
	$(COMPOSE) down --remove-orphans

.PHONY: restart
restart: ## Перезапустить API без простоя прокси
	$(COMPOSE) up -d --no-deps --build api

.PHONY: deploy
deploy: ## Полный цикл выкладки: миграции, сборка, перезапуск, проверка
	./deploy/scripts/deploy.sh

.PHONY: ps
ps: ## Состояние контейнеров
	$(COMPOSE) ps

.PHONY: logs
logs: ## Хвост логов всех сервисов
	$(COMPOSE) logs -f --tail=200

.PHONY: logs-api
logs-api: ## Хвост логов только API
	$(COMPOSE) logs -f --tail=200 api

# Разработка
.PHONY: dev
dev: ## Поднять БД, Redis и API с автоперезагрузкой
	$(DEV) up --build api

.PHONY: web-dev
web-dev: ## Запустить Vite dev-server на :5173
	cd apps/web && npm ci && npm run dev

# База данных
.PHONY: migrate
migrate: ## Применить миграции
	$(COMPOSE) run --rm migrate python -m app.cli migrate

.PHONY: revision
revision: ## Создать миграцию: make revision m="add table"
	$(COMPOSE) run --rm migrate alembic revision --autogenerate -m "$(m)"

.PHONY: psql
psql: ## Открыть psql внутри контейнера
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-saas} -d $${POSTGRES_DB:-saas}

.PHONY: backup
backup: ## Снять резервную копию БД
	./deploy/scripts/backup.sh

.PHONY: restore
restore: ## Восстановить из копии: make restore f=/var/backups/saas/db-....dump
	./deploy/scripts/restore.sh "$(f)"

# Качество
.PHONY: test
test: ## Прогнать unit-тесты API и web
	$(DEV) run --rm --no-deps -e APP_ENV=development api pytest -q
	cd apps/web && npm ci && npm test

.PHONY: test-e2e
test-e2e: ## Прогнать браузерные тесты (нужен установленный Chromium Playwright)
	cd apps/web && npm run test:e2e

.PHONY: lint
lint: ## Линт и типы
	cd apps/api && ruff check app tests migrations && ruff format --check app tests migrations && mypy app
	cd apps/web && npm run lint && npm run typecheck

.PHONY: fmt
fmt: ## Автоформатирование
	cd apps/api && ruff format app tests migrations && ruff check --fix app tests migrations

.PHONY: audit
audit: ## Проверка зависимостей на известные уязвимости
	cd apps/api && pip-audit -r requirements.lock
	cd apps/web && npm audit --audit-level=high

.PHONY: academic-drill
academic-drill: ## Restore, SMTP/TLS, нагрузочный smoke и DAST (Linux + Docker)
	./deploy/scripts/academic-drill.sh

# Обслуживание
.PHONY: harden
harden: ## Базовое усиление хоста Ubuntu (ufw, fail2ban, sysctl, автообновления)
	sudo ./deploy/scripts/harden-ubuntu.sh

.PHONY: health
health: ## Проверить живость всех компонентов
	./deploy/scripts/healthcheck.sh

.PHONY: clean
clean: ## Удалить контейнеры и образы (ДАННЫЕ В ТОМАХ ОСТАЮТСЯ)
	$(COMPOSE) down --rmi local --remove-orphans
