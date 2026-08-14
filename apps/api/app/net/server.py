"""Запуск и супервизия HTTP-сервера.

Модель - prefork с SO_REUSEPORT: родительский процесс не обрабатывает
запросы, а только следит за воркерами; каждый воркер открывает СВОЙ слушающий
сокет на том же порту, а распределением соединений занимается ядро Linux.

Почему так, а не ``uvicorn --workers`` и не gunicorn:

* Нет общего сокета - нет thundering herd. При одном разделяемом сокете
  каждое входящее соединение будит все процессы, и все, кроме одного,
  просыпаются впустую. SO_REUSEPORT раскладывает соединения по хеш-таблице
  внутри ядра: будится ровно один воркер.
* Падение воркера не роняет сервис. Ядро просто перестаёт слать в
  закрывшийся сокет, а супервизор поднимает воркер заново. Реализована
  экспоненциальная пауза, чтобы вечно падающий воркер не выжигал CPU
  бесконечным циклом рестартов.
* SIGHUP = перезапуск без простоя. Воркеры перезапускаются по одному;
  пока один поднимается, остальные держат нагрузку, и ни одно соединение
  не отбрасывается.
* Ограничение по числу запросов (``MAX_REQUESTS_PER_WORKER``) даёт
  дешёвую страховку от утечек памяти в зависимостях: воркер тихо уходит
  на покой после N запросов, супервизор ставит свежий.

На системах без SO_REUSEPORT (Windows, старые ядра) автоматически
включается одиночный процесс - шаблон остаётся работоспособным.
"""

from __future__ import annotations

import importlib.util
import multiprocessing
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from types import FrameType
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import uvicorn

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

log = get_logger("server")

APP_IMPORT_STRING = "app.main:app"

# Каталог для метрик prometheus в многопроцессном режиме. Лежит в tmpfs:
# запись быстрая, при перезапуске контейнера всё стирается само.
DEFAULT_PROMETHEUS_DIR = "/tmp/prometheus-multiproc"  # noqa: S108 - compose tmpfs

_SO_REUSEPORT = getattr(socket, "SO_REUSEPORT", None)
_REUSEPORT_SUPPORTED = _SO_REUSEPORT is not None and sys.platform.startswith("linux")

# Порог детектора «цикла падений»: столько рестартов за столько секунд.
_CRASH_WINDOW_S = 60.0
_CRASH_THRESHOLD = 5
_BACKOFF_MAX_S = 30.0


# Сокет


def create_listening_socket(settings: Settings, *, reuse_port: bool) -> socket.socket:
    """Создаёт и настраивает слушающий сокет."""
    family = socket.AF_INET6 if ":" in settings.api_host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            # Дуальный стек: один сокет принимает и IPv6, и IPv4-mapped.
            try:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
        if reuse_port and _SO_REUSEPORT is not None:
            sock.setsockopt(socket.SOL_SOCKET, _SO_REUSEPORT, 1)

        sock.bind((settings.api_host, settings.api_port))
        sock.listen(settings.listen_backlog)
        sock.set_inheritable(True)
    except Exception:
        sock.close()
        raise
    return sock


def _uvicorn_config(settings: Settings, *, reload: bool = False) -> uvicorn.Config:
    """Собирает конфигурацию uvicorn. Импорт внутри - чтобы родительский
    процесс супервизора не тянул в память весь HTTP-стек."""
    import uvicorn

    # httptools заметно быстрее чистого h11 и строже к некорректным запросам.
    http_impl: Literal["httptools", "h11"] = (
        "httptools" if importlib.util.find_spec("httptools") else "h11"
    )
    loop_impl: Literal["uvloop", "asyncio"] = (
        "uvloop" if importlib.util.find_spec("uvloop") else "asyncio"
    )

    # Потолок одновременно обрабатываемых запросов. Выше этого числа uvicorn
    # отвечает 503, не принимая работу, которую всё равно не сможет выполнить:
    # лучше быстрый честный отказ, чем очередь, растущая до OOM.
    concurrency = max(64, (settings.db_pool_size + settings.db_max_overflow) * 10)

    return uvicorn.Config(
        APP_IMPORT_STRING,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,  # логирование настраиваем сами, см. core.logging
        access_log=False,  # access-log пишет RequestContextMiddleware
        server_header=False,  # не раскрываем «uvicorn»
        date_header=True,
        http=http_impl,
        loop=loop_impl,
        proxy_headers=False,  # X-Forwarded-* разбираем сами, см. net.proxy
        forwarded_allow_ips=None,
        timeout_keep_alive=settings.keepalive_timeout_s,
        timeout_graceful_shutdown=settings.graceful_shutdown_s,
        limit_concurrency=concurrency,
        limit_max_requests=settings.max_requests_per_worker or None,
        backlog=settings.listen_backlog,
        reload=reload,
        reload_dirs=["app"] if reload else None,
    )


# Воркер


def _worker_main(worker_id: int) -> None:
    """Точка входа дочернего процесса.

    Функция определена на уровне модуля намеренно: так она пикулится и
    работает не только с fork, но и со spawn.
    """
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    # Сбрасываем унаследованные от родителя обработчики: дальше их
    # устанавливает сам uvicorn (graceful shutdown по SIGTERM).
    signals = [signal.SIGTERM, signal.SIGINT]
    sighup = getattr(signal, "SIGHUP", None)
    if sighup is not None:
        signals.append(sighup)
    for sig in signals:
        try:
            signal.signal(sig, signal.SIG_DFL)
        except (ValueError, OSError):
            pass

    sock = create_listening_socket(settings, reuse_port=True)
    server = uvicorn.Server(_uvicorn_config(settings))

    log.info("worker_started", worker_id=worker_id, pid=os.getpid())
    try:
        server.run(sockets=[sock])
    finally:
        try:
            sock.close()
        except OSError:
            log.warning("worker_socket_close_failed", worker_id=worker_id)
        log.info("worker_stopped", worker_id=worker_id, pid=os.getpid())


# Супервизор


@dataclass
class _Slot:
    """Одно «место» воркера: процесс плюс история его падений."""

    index: int
    process: multiprocessing.Process | None = None
    crashes: list[float] = field(default_factory=list)
    backoff_until: float = 0.0

    def alive(self) -> bool:
        return self.process is not None and self.process.is_alive()


class Supervisor:
    """Следит за пулом воркеров и переживает их падения."""

    def __init__(self, settings: Settings, worker_count: int) -> None:
        self.settings = settings
        self.slots = [_Slot(index=i) for i in range(worker_count)]
        self._stopping = False
        self._reload_requested = False
        # fork дешевле и делит память копированием при записи; spawn - запасной
        # путь для платформ без fork.
        method = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
        # Typeshed exposes context.Process inconsistently across platforms,
        # although it is part of Python's public multiprocessing API.
        self._ctx: Any = multiprocessing.get_context(method)
        self._start_method = method

    # жизненный цикл

    def run(self) -> None:
        self._prepare_metrics_dir()
        self._install_signal_handlers()

        log.info(
            "supervisor_started",
            pid=os.getpid(),
            workers=len(self.slots),
            start_method=self._start_method,
            host=self.settings.api_host,
            port=self.settings.api_port,
            reuse_port=True,
        )

        for slot in self.slots:
            self._spawn(slot)

        try:
            while not self._stopping:
                if self._reload_requested:
                    self._reload_requested = False
                    self._rolling_restart()
                self._reap()
                time.sleep(0.5)
        except KeyboardInterrupt:  # pragma: no cover - интерактивный запуск
            pass
        finally:
            self._shutdown()

    # сигналы

    def _install_signal_handlers(self) -> None:
        def stop(signum: int, _frame: FrameType | None) -> None:
            if not self._stopping:
                log.info("supervisor_signal", signal=signal.Signals(signum).name)
                self._stopping = True

        def reload(_signum: int, _frame: FrameType | None) -> None:
            log.info("supervisor_reload_requested")
            self._reload_requested = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, reload)
        # Дети умирают сами, зомби подбирает _reap(); SIGCHLD не нужен.

    # управление воркерами

    def _spawn(self, slot: _Slot) -> None:
        process = self._ctx.Process(
            target=_worker_main,
            args=(slot.index,),
            name=f"api-worker-{slot.index}",
            daemon=False,
        )
        process.start()
        slot.process = process

    def _reap(self) -> None:
        """Подбирает завершившиеся воркеры и поднимает замену."""
        now = time.monotonic()
        for slot in self.slots:
            if slot.alive() or self._stopping:
                continue
            slot.crashes = [t for t in slot.crashes if now - t < _CRASH_WINDOW_S]
            if slot.process is not None:
                pid = slot.process.pid
                exit_code = slot.process.exitcode
                slot.process.join(timeout=1)
                self._mark_worker_dead(pid)
                # exitcode 0 - штатный выход (сработал limit_max_requests).
                # Всё остальное - падение, его считаем в детекторе цикла.
                if exit_code not in (0, None):
                    slot.crashes.append(now)
                    log.error(
                        "worker_died",
                        worker_id=slot.index,
                        exit_code=exit_code,
                        crashes_in_window=len(slot.crashes),
                    )
                else:
                    log.info("worker_recycled", worker_id=slot.index)
                slot.process = None

            if now < slot.backoff_until:
                continue

            if len(slot.crashes) >= _CRASH_THRESHOLD:
                delay = min(_BACKOFF_MAX_S, 2.0 ** (len(slot.crashes) - _CRASH_THRESHOLD + 1))
                slot.backoff_until = now + delay
                log.critical(
                    "worker_crash_loop",
                    worker_id=slot.index,
                    retry_in_s=round(delay, 1),
                    hint=(
                        "проверьте логи воркера: обычно это ошибка конфигурации "
                        "или недоступная БД"
                    ),
                )
                continue

            self._spawn(slot)

    def _rolling_restart(self) -> None:
        """Перезапуск по одному воркеру. Приём соединений не прерывается."""
        log.info("rolling_restart_begin", workers=len(self.slots))
        for slot in self.slots:
            if self._stopping:
                break
            if slot.process is not None and slot.process.is_alive():
                pid = slot.process.pid
                slot.process.terminate()  # SIGTERM → uvicorn дорабатывает запросы
                slot.process.join(timeout=self.settings.graceful_shutdown_s + 5)
                if slot.process.is_alive():
                    log.warning("worker_kill_after_timeout", worker_id=slot.index)
                    slot.process.kill()
                    slot.process.join(timeout=5)
                self._mark_worker_dead(pid)
            slot.process = None
            slot.crashes.clear()
            slot.backoff_until = 0.0
            self._spawn(slot)
            # Пауза, чтобы новый воркер успел подняться и прогреть пул БД,
            # прежде чем мы выведем из строя следующий.
            time.sleep(1.0)
        log.info("rolling_restart_done")

    def _shutdown(self) -> None:
        deadline = time.monotonic() + self.settings.graceful_shutdown_s
        log.info("supervisor_stopping", grace_s=self.settings.graceful_shutdown_s)

        for slot in self.slots:
            if slot.process is not None and slot.process.is_alive():
                slot.process.terminate()

        for slot in self.slots:
            if slot.process is None:
                continue
            pid = slot.process.pid
            remaining = max(0.5, deadline - time.monotonic())
            slot.process.join(timeout=remaining)
            if not slot.process.is_alive():
                self._mark_worker_dead(pid)

        stuck = [s for s in self.slots if s.process is not None and s.process.is_alive()]
        for slot in stuck:
            assert slot.process is not None
            log.warning("worker_force_kill", worker_id=slot.index, pid=slot.process.pid)
            slot.process.kill()
            slot.process.join(timeout=5)
            self._mark_worker_dead(slot.process.pid)

        log.info("supervisor_stopped")

    # метрики

    def _mark_worker_dead(self, pid: int | None) -> None:
        """Удаляет метрики завершившегося процесса из multiprocess registry."""
        if pid is None or not self.settings.metrics_enabled:
            return
        try:
            from prometheus_client import multiprocess

            multiprocess.mark_process_dead(pid)  # type: ignore[no-untyped-call]
        except (ImportError, OSError) as exc:
            log.warning("metrics_worker_cleanup_failed", pid=pid, error=str(exc))

    def _prepare_metrics_dir(self) -> None:
        """Готовит каталог для сбора метрик со всех воркеров.

        prometheus_client в многопроцессном режиме складывает счётчики в
        mmap-файлы; каталог должен быть пуст на старте, иначе в выдачу
        попадут метрики мёртвых процессов прошлого запуска.
        """
        if not self.settings.metrics_enabled:
            return
        metrics_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR", DEFAULT_PROMETHEUS_DIR)
        try:
            if os.path.islink(metrics_dir):
                raise OSError("metrics directory must not be a symbolic link")
            os.makedirs(metrics_dir, mode=0o700, exist_ok=True)
            for entry in os.scandir(metrics_dir):
                if entry.is_file(follow_symlinks=False) and entry.name.endswith(".db"):
                    os.unlink(entry.path)
            os.environ["PROMETHEUS_MULTIPROC_DIR"] = metrics_dir
        except OSError as exc:
            log.warning("metrics_dir_unavailable", error=str(exc), path=metrics_dir)


# Публичная точка входа


def serve(*, reload: bool = False) -> None:
    """Поднимает сервер. Вызывается из ``python -m app.cli serve``."""
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    if reload:
        # Режим разработки: один процесс, автоперезагрузка, свой сокет.
        log.info("dev_server_starting", host=settings.api_host, port=settings.api_port)
        uvicorn.run(
            APP_IMPORT_STRING,
            host=settings.api_host,
            port=settings.api_port,
            reload=True,
            reload_dirs=["app"],
            log_config=None,
            access_log=False,
            server_header=False,
            proxy_headers=False,
            forwarded_allow_ips=None,
        )
        return

    workers = settings.worker_count
    if workers <= 1 or not _REUSEPORT_SUPPORTED:
        if workers > 1:
            log.warning(
                "reuseport_unavailable",
                platform=sys.platform,
                action="запускаю один процесс вместо нескольких",
            )
        log.info("single_process_starting", host=settings.api_host, port=settings.api_port)
        server = uvicorn.Server(_uvicorn_config(settings))
        sock = create_listening_socket(settings, reuse_port=False)
        try:
            server.run(sockets=[sock])
        finally:
            sock.close()
        return

    # Проверяем поддержку опции и занятость порта до запуска всего пула.
    probe = create_listening_socket(settings, reuse_port=True)
    probe.close()
    Supervisor(settings, workers).run()
