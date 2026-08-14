# История изменений

Формат основан на Keep a Changelog. До первого стабильного релиза несовместимые
изменения допускаются и должны быть отмечены здесь.

## 1.0.0 - 2026-08-14

- Добавлен SaaS-контур с аутентификацией, 2FA, организациями и отзывами.
- Добавлены транзакционная почтовая очередь и инварианты владельцев в PostgreSQL.
- Добавлены unit-, integration-, browser- и accessibility-тесты.
- Добавлены production Compose, backup/restore, hardening и systemd units.
- Добавлены CI, security scanning, SBOM и provenance release-образов.
- Production-сборка клиента автоматически обфусцирует код приложения.
- Добавлен воспроизводимый drill восстановления, SMTP/TLS, нагрузки и DAST.
