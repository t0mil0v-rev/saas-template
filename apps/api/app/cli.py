"""Единый CLI приложения:  python -m app.cli <команда>

Команды:

    init          миграции + первичный bootstrap (используется контейнером migrate)
    migrate       применить миграции
    serve         запустить сервер (--reload для разработки)
    healthcheck   проверка живости (используется HEALTHCHECK в Dockerfile)
    createsuperuser [email]
    cleanup       удалить протухшие сессии, токены и записи аудита
    mail-worker   доставлять письма из transactional outbox
    check-config  проверить конфигурацию и выйти

Всё завязано на один вход, чтобы в контейнере не тащить набор разрозненных
скриптов и entrypoint'ов.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime, timedelta
from typing import cast

from alembic.config import Config
from sqlalchemy.engine import CursorResult


def _run_alembic(argv: list[str]) -> int:
    """Запускает alembic внутри процесса - не плодя дочерний интерпретатор."""
    from alembic.config import CommandLine

    cli = CommandLine(prog="alembic")
    options = cli.parser.parse_args(argv)
    if not hasattr(options, "cmd"):
        cli.parser.error("не указана команда alembic")
    cli.run_cmd(_alembic_config(), options)
    return 0


def _alembic_config() -> Config:
    from app.core.config import get_settings

    config = Config("alembic.ini")
    # URL передаём из настроек, а не из ini: там его намеренно нет.
    config.set_main_option("sqlalchemy.url", get_settings().sync_database_url)
    return config


# Команды


def cmd_migrate(_args: argparse.Namespace) -> int:
    print("→ Применяю миграции…")
    _run_alembic(["upgrade", "head"])
    print("✓ Миграции применены")
    return 0


def cmd_init(_args: argparse.Namespace) -> int:
    """Полная инициализация: дождаться БД, накатить миграции, создать админа."""
    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.db import session as db

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    async def _wait() -> None:
        await db.wait_for_db(settings)
        await db.dispose_engine()

    print("→ Жду готовности базы данных…")
    asyncio.run(_wait())

    cmd_migrate(_args)

    print("→ Первичная настройка…")
    asyncio.run(_bootstrap(settings))
    print("✓ Инициализация завершена")
    return 0


async def _bootstrap(settings: object) -> None:
    """Создаёт первого администратора и организацию-владельца лендинга.

    Идемпотентно: повторный запуск ничего не портит. Пароль администратора
    задаётся из BOOTSTRAP_ADMIN_PASSWORD и в лог не попадает.
    """
    from sqlalchemy import select

    from app.core.config import Settings
    from app.core.security import check_password_policy, hash_password
    from app.db.models import Organization, OrgMember, OrgRole, User
    from app.db.session import session_scope

    assert isinstance(settings, Settings)

    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        print("  · BOOTSTRAP_ADMIN_* не заданы - администратор не создаётся")
        return

    admin_email = settings.bootstrap_admin_email.lower()
    policy = check_password_policy(settings.bootstrap_admin_password, email=admin_email)
    if not policy.ok:
        raise RuntimeError(f"BOOTSTRAP_ADMIN_PASSWORD: {policy.reason}")

    async with session_scope() as db:
        admin = await db.scalar(select(User).where(User.email == admin_email))
        if admin is None:
            admin = User(
                email=admin_email,
                password_hash=hash_password(settings.bootstrap_admin_password),
                full_name="Administrator",
                is_active=True,
                is_superuser=True,
                email_verified_at=datetime.now(UTC),
            )
            db.add(admin)
            await db.flush()
            print(f"  · Создан администратор {admin.email}")
        else:
            admin.is_active = True
            admin.is_superuser = True
            print(f"  · Администратор {admin.email} уже существует")

        if settings.bootstrap_org_slug:
            org = await db.scalar(
                select(Organization).where(Organization.slug == settings.bootstrap_org_slug)
            )
            if org is None:
                org = Organization(
                    slug=settings.bootstrap_org_slug,
                    name=settings.bootstrap_org_name,
                    description="Демонстрационная организация шаблона.",
                    is_public=True,
                )
                db.add(org)
                await db.flush()
                db.add(OrgMember(org_id=org.id, user_id=admin.id, role=OrgRole.OWNER.value))
                print(f"  · Создана организация «{org.name}» (/{org.slug})")
            else:
                print(f"  · Организация /{org.slug} уже существует")


def cmd_createsuperuser(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.core.security import check_password_policy, hash_password
    from app.db.models import User
    from app.db.session import session_scope

    email = (args.email or input("E-mail: ")).strip().lower()
    password = getpass.getpass("Пароль: ")
    policy = check_password_policy(password, email=email)
    if not policy.ok:
        print(f"✗ {policy.reason}", file=sys.stderr)
        return 1

    async def _create() -> int:
        async with session_scope() as db:
            existing = await db.scalar(select(User).where(User.email == email))
            if existing is not None:
                existing.is_superuser = True
                existing.is_active = True
                print(f"✓ {email} повышен до администратора")
                return 0
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(password),
                    is_superuser=True,
                    is_active=True,
                    email_verified_at=datetime.now(UTC),
                )
            )
            print(f"✓ Администратор {email} создан")
            return 0

    return asyncio.run(_create())


def cmd_cleanup(_args: argparse.Namespace) -> int:
    """Периодическая уборка. Ставится в cron/таймер (см. deploy/systemd)."""
    from sqlalchemy import and_, delete, or_

    from app.core.config import get_settings
    from app.db.models import (
        AuditLog,
        EmailToken,
        Invitation,
        MailOutbox,
        RecoveryCode,
        Session,
    )
    from app.db.session import session_scope

    settings = get_settings()

    async def _cleanup() -> int:
        now = datetime.now(UTC)
        # Аудит старше срока удаляем, но события безопасности храним дольше:
        # расследование инцидента может начаться спустя месяцы.
        audit_horizon = now - timedelta(days=settings.audit_retention_days)
        async with session_scope() as db:
            expired_sessions = await db.execute(
                delete(Session).where(
                    or_(
                        Session.expires_at < now,
                        and_(
                            Session.revoked_at.is_not(None),
                            Session.last_seen_at
                            < now - timedelta(days=settings.session_retention_days),
                        ),
                    )
                )
            )
            expired_tokens = await db.execute(
                delete(EmailToken).where(
                    or_(EmailToken.expires_at < now, EmailToken.used_at.is_not(None))
                )
            )
            used_codes = await db.execute(
                delete(RecoveryCode).where(RecoveryCode.used_at < now - timedelta(days=30))
            )
            old_invitations = await db.execute(
                delete(Invitation).where(
                    or_(
                        Invitation.expires_at < now,
                        Invitation.accepted_at
                        < now - timedelta(days=settings.invitation_retention_days),
                    )
                )
            )
            old_audit = await db.execute(
                delete(AuditLog).where(AuditLog.created_at < audit_horizon)
            )
            mail_horizon = now - timedelta(days=settings.mail_outbox_retention_days)
            old_mail = await db.execute(
                delete(MailOutbox).where(
                    or_(MailOutbox.sent_at < mail_horizon, MailOutbox.failed_at < mail_horizon)
                )
            )
            print(
                f"✓ Убрано: сессий {cast(CursorResult[object], expired_sessions).rowcount}, "
                f"токенов {cast(CursorResult[object], expired_tokens).rowcount}, "
                f"кодов {cast(CursorResult[object], used_codes).rowcount}, "
                f"приглашений {cast(CursorResult[object], old_invitations).rowcount}, "
                f"записей аудита {cast(CursorResult[object], old_audit).rowcount}, "
                f"писем {cast(CursorResult[object], old_mail).rowcount}"
            )
            return 0

    return asyncio.run(_cleanup())


def cmd_healthcheck(_args: argparse.Namespace) -> int:
    """Живость для Docker HEALTHCHECK. Проверяет БД, код возврата = статус."""
    from app.db import session as db

    async def _check() -> bool:
        try:
            ok = await db.ping()
            await db.dispose_engine()
            return ok
        except Exception:
            return False

    ok = asyncio.run(_check())
    if not ok:
        print("unhealthy: база данных недоступна", file=sys.stderr)
        return 1
    print("ok")
    return 0


def cmd_mail_worker(args: argparse.Namespace) -> int:
    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.db import session as db
    from app.services.outbox import OutboxWorker

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    async def _run() -> None:
        db.init_engine(settings)
        try:
            await db.wait_for_db(settings)
            await OutboxWorker(settings).run(once=args.once)
        finally:
            await db.dispose_engine()

    asyncio.run(_run())
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from app.net.server import serve

    serve(reload=args.reload)
    return 0


def cmd_check_config(_args: argparse.Namespace) -> int:
    """Загружает и валидирует конфигурацию. Падение = проблема в .env."""
    from app.core.config import get_settings

    try:
        settings = get_settings()
    except Exception as exc:
        print(f"✗ Конфигурация недействительна:\n{exc}", file=sys.stderr)
        return 1

    print("✓ Конфигурация корректна")
    print(f"  окружение:   {settings.app_env}")
    print(f"  публичный:   {settings.public_url}")
    print(f"  TLS-режим:   {settings.tls_mode}")
    print(f"  воркеров:    {settings.worker_count}")
    print(f"  Redis:       {'да' if settings.redis_url else 'нет (лимиты в памяти)'}")
    print(f"  почта:       {settings.mail_transport}")
    print(f"  /docs:       {'открыт' if settings.enable_api_docs else 'закрыт'}")
    return 0


# Разбор аргументов


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Управление SaaS-приложением")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Миграции + первичная настройка").set_defaults(func=cmd_init)
    sub.add_parser("migrate", help="Применить миграции").set_defaults(func=cmd_migrate)
    sub.add_parser("cleanup", help="Удалить протухшие данные").set_defaults(func=cmd_cleanup)
    sub.add_parser("healthcheck", help="Проверка живости").set_defaults(func=cmd_healthcheck)
    sub.add_parser("check-config", help="Проверить конфигурацию").set_defaults(
        func=cmd_check_config
    )

    p_serve = sub.add_parser("serve", help="Запустить сервер")
    p_serve.add_argument("--reload", action="store_true", help="Автоперезагрузка (разработка)")
    p_serve.set_defaults(func=cmd_serve)

    p_su = sub.add_parser("createsuperuser", help="Создать администратора")
    p_su.add_argument("email", nargs="?", default="")
    p_su.set_defaults(func=cmd_createsuperuser)

    p_mail = sub.add_parser("mail-worker", help="Доставлять письма из очереди")
    p_mail.add_argument("--once", action="store_true", help="Обработать очередь и выйти")
    p_mail.set_defaults(func=cmd_mail_worker)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
