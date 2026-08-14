# Модель угроз

Версия модели: 2026-08-14. Пересмотр требуется при добавлении нового способа
входа, платёжного провайдера, файлов, внешнего API или фонового worker.

## Активы и нарушители

Защищаются пароли, сессионные и почтовые токены, TOTP-секреты, tenant-данные,
аудит, резервные копии и ключи инфраструктуры. Рассматриваются анонимный
интернет-клиент, обычный пользователь, участник чужой организации,
скомпрометированный браузер и оператор с ошибочной конфигурацией.

Полный контроль хоста, Docker daemon, CI-секретов или почтового ящика находится
за границей модели. После такой компрометации требуется ротация ключей и
восстановление доверенной среды.

## Границы доверия

| Переход | Недоверенные данные | Основная проверка |
|---|---|---|
| Internet -> Caddy | HTTP, Host, тело | TLS, лимиты, security headers |
| Caddy -> API | forwarded headers | список trusted proxies и depth |
| Browser -> session | cookie, Origin, CSRF | server session, Origin, подписанный token |
| API -> tenant data | slug, user id, role | membership, `org_id`, повторная role check |
| API -> PostgreSQL | конкурентные изменения | transaction, row lock, deferred trigger |
| Outbox -> SMTP | письмо и адрес | committed job, lease, retry, TLS mode |
| Backup -> storage | полный дамп | age encryption, SHA-256, права 0600 |

## Основные сценарии

| Угроза | Мера | Остаточный риск |
|---|---|---|
| Перебор пароля | Argon2id, account lock, IP limiter | распределённая атака требует внешнего WAF |
| Перечисление аккаунтов | одинаковые ответы reset/register | время SMTP/БД нужно наблюдать отдельно |
| Кража сессии через XSS | HttpOnly cookie, CSP, no token storage | XSS может действовать от имени открытой вкладки |
| CSRF | SameSite, Origin/Referer, signed double-submit | доверенный XSS обходит CSRF |
| Повтор TOTP | сохранение последнего принятого time step | параллельность зависит от row lock пользователя |
| Межtenant-доступ | membership dependency и service recheck | новый прямой запрос может забыть фильтр `org_id` |
| Потеря владельца | org row lock и deferred DB trigger | ручное отключение triggers снимает гарантию |
| Подделка client IP | forwarded headers только от trusted proxy | слишком широкий CIDR доверяет лишним узлам |
| Потеря письма после commit | transactional outbox | постоянная SMTP-ошибка требует alert по failed jobs |
| Компрометация дампа | age и минимальные права | ключ age нельзя хранить рядом с копией |
| Уязвимая зависимость | lock, audit, Dependabot, CodeQL | zero-day до появления сигнатуры остаётся |

## Проверка модели

Связь мер с автоматическими тестами описана в `docs/TESTING.md`. Academic drill
проверяет локальный TLS, восстановление backup, SMTP STARTTLS, небольшой
нагрузочный профиль и пассивный ZAP baseline. Перед релизом оператор отдельно
проверяет внешний зашифрованный backup, публичный TLS, реальную доставку почты,
alerting и фактические права облачной среды. Автоматический green build не
заменяет независимый authenticated pentest продукта.
