# Матрица защитных мер

Матрица помогает проверить, что существенное утверждение подтверждено кодом и
тестом. Она не заменяет модель угроз конкретного продукта.

| Риск | Контроль | Проверка |
|---|---|---|
| Кража пароля из БД | Argon2id, параметризованная проверка пароля | `apps/api/tests/test_security.py` |
| Перечисление аккаунтов | Одинаковый публичный ответ и dummy Argon2 | `apps/api/tests/test_auth_routes.py`, integration auth flow |
| Кража сессии | Непрозрачный токен, SHA-256 в БД, Secure/HttpOnly cookie | `apps/api/tests/test_security.py`, `apps/api/tests/integration/test_api.py` |
| CSRF | Origin или same-origin Referer плюс токен сессии | `apps/api/tests/test_security.py` |
| Повтор TOTP | Блокировка уже принятого временного шага под row lock | auth unit и integration tests |
| Доступ между организациями | `org_id` во всех tenant-запросах и проверка роли | org/review service tests |
| Организация без владельца | Deferred trigger, row lock и advisory lock | `apps/api/tests/integration/test_database.py` |
| Система без активного администратора | Транзакционный инвариант superuser | `apps/api/tests/integration/test_database.py` |
| Потеря письма после commit | Транзакционный outbox, lease и retry | `apps/api/tests/test_outbox.py` |
| Подмена клиентского IP | Список доверенных прокси и ограниченная глубина цепочки | `apps/api/tests/test_proxy.py` |
| Обход rate limit гонкой | Атомарный Redis Lua bucket | `apps/api/tests/test_ratelimit.py` и Redis integration test |
| Утечка через логи | Маскирование полей, HMAC IP и e-mail получателя | logging и mailer unit tests |
| Повреждённый backup | Partial-файл, checksum и `pg_restore --list` | ShellCheck и academic drill |
| Несовместимое восстановление | Backup, мутация и restore на отдельном Compose project | `deploy/scripts/academic-drill.sh` |
| Незащищённый SMTP | Обязательный STARTTLS и проверяемая цепочка CA | Mailpit STARTTLS в academic drill |
| Ошибка TLS или заголовков | Проверка сертификата, HSTS и CSP | Caddy TLS в academic drill |
| Регрессия под небольшой нагрузкой | Порог error rate, checks и p95 | `tests/ops/load.js` |
| Ошибка внешней HTTP-поверхности | Пассивный black-box baseline | OWASP ZAP в academic drill |
| Подмена build-зависимостей | Hash-locked Python, npm lock, digest images, pinned Actions | CI и release workflows |
| Недоступный worker | Prefork supervision и ограничение crash loop | `apps/api/tests/test_server.py` |

Ручные проверки перечислены в [TESTING.md](TESTING.md). После добавления нового
security-инварианта нужно обновить модель угроз, эту таблицу и тест, который
воспроизводит нарушение.
