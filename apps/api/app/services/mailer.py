"""Постановка писем в outbox и доставка через SMTP.

HTTP-запрос только добавляет письмо в свою транзакцию. Фактическую отправку
вызывает отдельный worker из ``app.services.outbox``. SMTP работает в потоке,
поэтому блокирующая стандартная библиотека не останавливает event loop.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import hash_email_for_log
from app.db.models import MailOutbox

log = get_logger("mail")


@dataclass(frozen=True, slots=True)
class Letter:
    to: str
    subject: str
    body: str


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def enqueue(
        self, db: AsyncSession, letter: Letter, *, dedupe_key: str | None = None
    ) -> MailOutbox:
        """Добавляет письмо в текущую транзакцию."""
        if dedupe_key:
            await db.execute(
                update(MailOutbox)
                .where(
                    MailOutbox.dedupe_key == dedupe_key,
                    MailOutbox.sent_at.is_(None),
                    MailOutbox.failed_at.is_(None),
                )
                .values(failed_at=datetime.now(UTC), last_error="superseded")
            )
        item = MailOutbox(
            recipient=letter.to,
            dedupe_key=dedupe_key,
            subject=letter.subject,
            body=letter.body,
        )
        db.add(item)
        await db.flush()
        log.info(
            "mail_queued",
            outbox_id=str(item.id),
            recipient_hash=hash_email_for_log(letter.to),
            subject=letter.subject,
        )
        return item

    async def deliver(self, letter: Letter) -> None:
        """Доставляет письмо или выбрасывает ошибку для retry worker."""
        if self.settings.mail_transport == "console":
            # Settings разрешает этот транспорт исключительно в development.
            # Там ссылка нужна разработчику для ручной проверки flow; в staging
            # и production она никогда не попадёт в журналы.
            log.info(
                "mail_console",
                to=letter.to,
                subject=letter.subject,
                body=letter.body,
            )
            return

        try:
            await asyncio.to_thread(self._send_smtp, letter)
        except Exception as exc:
            log.error(
                "mail_send_failed",
                recipient_hash=hash_email_for_log(letter.to),
                subject=letter.subject,
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            raise
        log.info(
            "mail_sent",
            recipient_hash=hash_email_for_log(letter.to),
            subject=letter.subject,
        )

    # внутреннее

    def _build(self, letter: Letter) -> EmailMessage:
        settings = self.settings
        message = EmailMessage()
        display_name, address = parseaddr(settings.mail_from)
        message["From"] = formataddr((display_name or settings.app_name, address))
        message["To"] = letter.to
        message["Subject"] = letter.subject
        message["Message-ID"] = make_msgid(domain=settings.public_host)
        # Служебные письма не должны попадать в автоответчики и списки рассылки.
        message["Auto-Submitted"] = "auto-generated"
        message["X-Auto-Response-Suppress"] = "All"
        message.set_content(letter.body)
        return message

    def _send_smtp(self, letter: Letter) -> None:
        settings = self.settings
        message = self._build(letter)
        context = ssl.create_default_context()
        # Проверка сертификата релея включена. Если внутренний SMTP работает
        # с самоподписанным сертификатом - добавьте корпоративный CA в
        # доверенные внутри образа, а не отключайте проверку.
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED

        if settings.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_s,
                context=context,
            )
        else:
            server = smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_s
            )

        try:
            server.ehlo()
            if settings.smtp_security == "starttls":
                server.starttls(context=context)
                server.ehlo()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
        finally:
            try:
                server.quit()
            except Exception:  # noqa: S110 - соединение уже закрыто
                pass


# Шаблоны писем
#
#  Только текст, без HTML: проще, не режется спам-фильтрами и не даёт
#  возможности вставить в письмо активное содержимое.


def verification_letter(settings: Settings, to: str, token: str) -> Letter:
    link = f"{settings.public_url}/verify-email?token={token}"
    return Letter(
        to=to,
        subject=f"{settings.app_name}: подтверждение адреса",
        body=(
            "Здравствуйте!\n\n"
            f"Вы указали этот адрес при регистрации в «{settings.app_name}».\n"
            "Чтобы подтвердить его, откройте ссылку:\n\n"
            f"{link}\n\n"
            "Ссылка действует 24 часа.\n"
            "Если вы не регистрировались, просто проигнорируйте это письмо.\n"
        ),
    )


def password_reset_letter(settings: Settings, to: str, token: str) -> Letter:
    link = f"{settings.public_url}/reset-password?token={token}"
    return Letter(
        to=to,
        subject=f"{settings.app_name}: восстановление пароля",
        body=(
            "Здравствуйте!\n\n"
            "Поступил запрос на смену пароля. Чтобы задать новый, откройте ссылку:\n\n"
            f"{link}\n\n"
            "Ссылка действует 1 час и срабатывает один раз.\n\n"
            "Если запрос делали не вы - ничего делать не нужно, пароль остался прежним.\n"
        ),
    )


def invitation_letter(
    settings: Settings, to: str, token: str, org_name: str, inviter: str
) -> Letter:
    link = f"{settings.public_url}/accept-invite?token={token}"
    who = inviter or "администратор организации"
    return Letter(
        to=to,
        subject=f"{settings.app_name}: приглашение в «{org_name}»",
        body=(
            "Здравствуйте!\n\n"
            f"{who} приглашает вас в организацию «{org_name}» "
            f"в «{settings.app_name}».\n\n"
            f"Чтобы принять приглашение, откройте ссылку:\n\n{link}\n\n"
            "Ссылка действует 7 дней.\n"
        ),
    )


def password_changed_letter(settings: Settings, to: str) -> Letter:
    return Letter(
        to=to,
        subject=f"{settings.app_name}: пароль изменён",
        body=(
            "Здравствуйте!\n\n"
            "Пароль от вашей учётной записи только что изменён, "
            "все остальные сеансы завершены.\n\n"
            "Если это сделали не вы - немедленно восстановите доступ "
            "через «Забыли пароль» и сообщите в поддержку.\n"
        ),
    )
