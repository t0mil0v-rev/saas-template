"""Transactional outbox worker for e-mail delivery."""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import MailOutbox
from app.db.session import session_scope
from app.services.mailer import Letter, Mailer

log = get_logger("mail.outbox")


@dataclass(frozen=True, slots=True)
class ClaimedMail:
    id: uuid.UUID
    letter: Letter
    attempts: int


class OutboxWorker:
    def __init__(self, settings: Settings, mailer: Mailer | None = None) -> None:
        self.settings = settings
        self.mailer = mailer or Mailer(settings)
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    @staticmethod
    def retry_delay(attempts: int) -> timedelta:
        return timedelta(seconds=min(3600, 2 ** min(max(1, attempts), 12)))

    async def _claim(self) -> ClaimedMail | None:
        now = datetime.now(UTC)
        lease_expired = now - timedelta(seconds=self.settings.mail_outbox_lease_s)
        async with session_scope() as db:
            item = await db.scalar(
                select(MailOutbox)
                .where(
                    MailOutbox.sent_at.is_(None),
                    MailOutbox.failed_at.is_(None),
                    MailOutbox.available_at <= now,
                    or_(MailOutbox.locked_at.is_(None), MailOutbox.locked_at < lease_expired),
                )
                .order_by(MailOutbox.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if item is None:
                return None
            item.locked_at = now
            item.locked_by = self.worker_id
            item.attempts += 1
            return ClaimedMail(
                id=item.id,
                letter=Letter(to=item.recipient, subject=item.subject, body=item.body),
                attempts=item.attempts,
            )

    async def _mark_sent(self, claimed: ClaimedMail) -> None:
        now = datetime.now(UTC)
        async with session_scope() as db:
            await db.execute(
                update(MailOutbox)
                .where(MailOutbox.id == claimed.id, MailOutbox.locked_by == self.worker_id)
                .values(sent_at=now, locked_at=None, locked_by=None, last_error=None)
            )

    async def _mark_failed(self, claimed: ClaimedMail, exc: Exception) -> None:
        now = datetime.now(UTC)
        terminal = claimed.attempts >= self.settings.mail_outbox_max_attempts
        async with session_scope() as db:
            await db.execute(
                update(MailOutbox)
                .where(MailOutbox.id == claimed.id, MailOutbox.locked_by == self.worker_id)
                .values(
                    available_at=now + self.retry_delay(claimed.attempts),
                    locked_at=None,
                    locked_by=None,
                    failed_at=now if terminal else None,
                    last_error=f"{type(exc).__name__}: {exc}"[:2000],
                )
            )
        level = log.error if terminal else log.warning
        level(
            "mail_delivery_failed",
            outbox_id=str(claimed.id),
            attempt=claimed.attempts,
            terminal=terminal,
            error=type(exc).__name__,
        )

    async def process_one(self) -> bool:
        claimed = await self._claim()
        if claimed is None:
            return False
        try:
            await self.mailer.deliver(claimed.letter)
        except Exception as exc:
            await self._mark_failed(claimed, exc)
        else:
            await self._mark_sent(claimed)
        return True

    async def run(self, *, once: bool = False) -> None:
        log.info("mail_worker_started", worker_id=self.worker_id, once=once)
        while True:
            processed = await self.process_one()
            if once and not processed:
                break
            if not processed:
                await asyncio.sleep(self.settings.mail_outbox_poll_s)
        log.info("mail_worker_stopped", worker_id=self.worker_id)
