# Тестирование

Проект использует несколько уровней. Один высокий процент покрытия не считается
заменой проверке транзакций, браузера и production-сборки.

## Backend unit

```bash
cd apps/api
pytest -q
ruff check app tests migrations
ruff format --check app tests migrations
mypy app
```

Unit-набор проверяет криптографические функции, ключевую ротацию, TOTP replay,
rate limiter, trusted proxy, outbox retry и prefork supervisor. Локально он не
требует инфраструктуры.

## Backend integration

Сначала примените миграции к отдельной тестовой БД. Никогда не указывайте здесь
production DSN.

```bash
export APP_ENV=development
export SECRET_KEY=integration-secret-key-with-at-least-32-characters
export POSTGRES_HOST=127.0.0.1
export POSTGRES_USER=saas
export POSTGRES_PASSWORD=integration-password
export POSTGRES_DB=saas_test
export REDIS_URL=redis://127.0.0.1:6379/0
export TEST_REDIS_URL=redis://127.0.0.1:6379/1
export RUN_INTEGRATION=1

python -m alembic upgrade head
pytest -q --cov=app --cov-branch --cov-fail-under=50
```

Integration-набор проверяет ASGI lifecycle, cookie/CSRF flow, атомарность
регистрации, реальные PostgreSQL triggers, конкурентные изменения ролей и общий
Redis limiter. CI поднимает чистые PostgreSQL и Redis для каждого запуска.

## Frontend

```bash
cd apps/web
npm ci
npm run typecheck
npm run lint
npm run test:coverage
npm run build
npx playwright install chromium
npm run test:e2e
```

Порог unit coverage относится к общему frontend-ядру (`api`, `auth`, routing,
hooks, components). Страницы проверяются пользовательскими Chromium-сценариями.
E2E запускаются в светлой и тёмной темах; axe проверяет вычисленный DOM и CSS.
Перед браузерными тестами собирается production-версия клиента с включённой
обфускацией. Поэтому сценарии проверяют тот же тип артефакта, который попадает
в контейнер, а не dev-сервер Vite.

Production-сборка автоматически обфусцирует только собственные чанки
приложения. Код React и остальных зависимостей остаётся отдельным, чтобы не
увеличивать размер и не затруднять анализ сторонних лицензий. Для диагностики
сборку можно выполнить с `OBFUSCATE=false npm run build`. Обфускация усложняет
поверхностное чтение клиента, но не скрывает переданные браузеру секреты и не
считается границей безопасности.

## Инфраструктура

CI запускает ShellCheck, `docker compose config`, production-сборки обоих
образов, `pip-audit`, `npm audit`, CodeQL и dependency review. Release по тегу
публикует SBOM и attestation происхождения.

Расширенный контур запускается командой `make academic-drill`. Он проверяет
штатное восстановление backup, доставку через обязательный SMTP STARTTLS,
сертификат и заголовки Caddy, пороговый k6 smoke и OWASP ZAP baseline. Полное
описание и границы результата находятся в
[ACADEMIC_VALIDATION.md](ACADEMIC_VALIDATION.md).

## Что остаётся ручным

- Восстановление зашифрованного age backup с внешнего хранилища.
- Проверка SPF, DKIM, DMARC и доставки через реального провайдера.
- Capacity test на production-подобном железе и поиск точки насыщения.
- Проверка публичного DNS, ACME renewal и TLS внешним сканером.
- Независимый authenticated pentest бизнес-логики.
- Проверка alerting, ротации логов и заполнения диска.
