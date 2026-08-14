# ADR 0004: prefork supervisor

Статус: принято, 2026-08-14.

## Контекст

Нужны несколько ASGI workers, корректные сигналы, контролируемый restart и
Prometheus multiprocess lifecycle без зависимости от внешнего process manager
в контейнере.

## Решение

Родитель создаёт workers, каждый открывает порт с `SO_REUSEPORT`. Supervisor
обрабатывает SIGTERM и SIGHUP, поднимает упавший slot с backoff и удаляет
Prometheus-файлы умершего PID.

## Последствия

Решение зависит от поддержки `SO_REUSEPORT` платформой. На Windows production
сервер не заявлен. Crash-loop ограничивает частоту fork, но внешний orchestrator
всё равно должен следить за healthcheck родительского процесса.
