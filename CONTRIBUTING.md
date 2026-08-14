# Как внести изменение

Сначала откройте issue или коротко опишите задачу в pull request. Для исправления
ошибки добавьте тест, который воспроизводит проблему. Для изменения архитектуры,
границ доверия или формата данных добавьте ADR в `docs/adr`.

## Локальная проверка

Backend требует Python 3.11:

```bash
cd apps/api
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
ruff check app tests migrations
ruff format --check app tests migrations
mypy app
pytest -q
```

Frontend использует версию Node из `.nvmrc`:

```bash
cd apps/web
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Интеграционные backend-тесты требуют PostgreSQL и Redis. Их окружение и точные
переменные описаны в `docs/TESTING.md` и CI.

## Критерии pull request

- Изменение ограничено заявленной задачей.
- Публичное поведение и миграции описаны.
- Проверены ошибки, конкурентные запросы и границы tenant-доступа, если они
  затронуты.
- Новая зависимость обоснована и попала в lock-файл.
- Комментарии следуют `docs/COMMENTING.md`.
- Все локально доступные проверки проходят.

Не добавляйте секреты, реальные персональные данные и сгенерированные отчёты.
Security-проблемы отправляйте по правилам из `SECURITY.md`, а не в публичный issue.
