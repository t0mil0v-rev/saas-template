# SaaS Platform

> Академически выверенный SaaS-шаблон с воспроизводимыми проверками кода,
> восстановления, почты, TLS, нагрузки и внешнего HTTP-контура.

Шаблон SaaS-приложения на FastAPI и React. В репозитории есть не только
демонстрационные маршруты, но и миграции, проверки инвариантов, тесты,
наблюдаемость и воспроизводимый production-контур. Это исходная система, а не
сертификат безопасности: после изменения бизнес-логики модель угроз и тесты
нужно пересмотреть.

## Что используется

| Часть | Стек |
|---|---|
| API | Python 3.11, FastAPI, SQLAlchemy, asyncpg, Alembic |
| Клиент | React 19, TypeScript, Vite |
| Данные | PostgreSQL 16, Redis 7 по желанию |
| Прокси | Caddy 2 |

## Что уже работает

- Регистрация, вход, сброс пароля, серверные сессии и 2FA.
- Организации, роли member/admin/owner и приглашения по e-mail.
- Публичная форма отзывов с лимитами и модерацией.
- Транзакционная очередь писем, аудит действий, healthchecks и резервные копии.
- Docker Compose для разработки и production, а также systemd-юниты.
- Unit-, integration-, browser- и accessibility-тесты.
- CI, CodeQL, dependency review, SBOM и provenance для release-образов.

## Быстрый запуск

Нужен Docker с Compose.

```bash
git clone <repo-url> /opt/saas
cd /opt/saas

make env
$EDITOR .env
make check
make up
make health
```

Перед production-запуском задайте как минимум `DOMAIN`, `PUBLIC_URL`,
`TLS_MODE`, `BOOTSTRAP_ADMIN_*` и SMTP-параметры. Приложение будет доступно
по адресу `https://<DOMAIN>`.

Для локальной разработки:

```bash
make dev       # Postgres, Redis и API на :8000
make web-dev   # Vite на :5173
make test
make test-e2e
make lint
```

## TLS

`TLS_MODE` выбирает способ завершения TLS:

| Режим | Когда использовать |
|---|---|
| `acme` | Публичный DNS и доступ в интернет. Caddy получает сертификат сам. |
| `internal` | Закрытая сеть и свой центр сертификации. |
| `none` | TLS завершается на балансировщике. Заполните `TRUSTED_PROXIES`. |

## Структура

```text
apps/api/     API, миграции и тесты
apps/web/     React-клиент
deploy/       Caddy, systemd и эксплуатационные скрипты
docs/         архитектура, решения, тестирование и эксплуатация
```

## Документация

- [Деплой](docs/DEPLOYMENT.md)
- [Модель безопасности](docs/SECURITY.md)
- [Архитектура](docs/ARCHITECTURE.md)
- [Модель угроз](docs/THREAT_MODEL.md)
- [Тестирование](docs/TESTING.md)
- [Надёжность и восстановление](docs/RELIABILITY.md)
- [Академическая валидация](docs/ACADEMIC_VALIDATION.md)
- [Хранение и удаление данных](docs/DATA_POLICY.md)
- [Матрица защитных мер](docs/CONTROL_MATRIX.md)
- [Как внести изменение](CONTRIBUTING.md)
- [Политика комментариев](docs/COMMENTING.md)
- [История решений](docs/adr/README.md)
- [Лицензия MIT](LICENSE)

## Лицензия

Код распространяется по лицензии MIT. Смотрите [LICENSE](LICENSE).
