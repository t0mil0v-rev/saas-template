"""Поведение почтовой очереди без подключения к SMTP и PostgreSQL."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.config import Settings
from app.services.mailer import Letter, Mailer
from app.services.outbox import ClaimedMail, OutboxWorker


def _settings(**changes: object) -> Settings:
    return Settings(app_env="development", mail_transport="console", **changes)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_enqueue_uses_current_database_transaction() -> None:
    db = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    letter = Letter(to="user@example.com", subject="Subject", body="Body")

    item = await Mailer(_settings()).enqueue(db, letter)

    db.add.assert_called_once_with(item)
    db.flush.assert_awaited_once()
    assert item.recipient == letter.to
    assert item.subject == letter.subject
    assert item.body == letter.body


@pytest.mark.asyncio
async def test_worker_marks_successful_delivery() -> None:
    mailer = MagicMock()
    mailer.deliver = AsyncMock()
    worker = OutboxWorker(_settings(), mailer=mailer)
    claimed = ClaimedMail(uuid.uuid4(), Letter("user@example.com", "Subject", "Body"), 1)
    worker._claim = AsyncMock(return_value=claimed)  # type: ignore[method-assign]
    worker._mark_sent = AsyncMock()  # type: ignore[method-assign]
    worker._mark_failed = AsyncMock()  # type: ignore[method-assign]

    assert await worker.process_one()
    mailer.deliver.assert_awaited_once_with(claimed.letter)
    worker._mark_sent.assert_awaited_once_with(claimed)  # type: ignore[attr-defined]
    worker._mark_failed.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_worker_schedules_retry_after_failure() -> None:
    error = OSError("smtp unavailable")
    mailer = MagicMock()
    mailer.deliver = AsyncMock(side_effect=error)
    worker = OutboxWorker(_settings(), mailer=mailer)
    claimed = ClaimedMail(uuid.uuid4(), Letter("user@example.com", "Subject", "Body"), 2)
    worker._claim = AsyncMock(return_value=claimed)  # type: ignore[method-assign]
    worker._mark_sent = AsyncMock()  # type: ignore[method-assign]
    worker._mark_failed = AsyncMock()  # type: ignore[method-assign]

    assert await worker.process_one()
    worker._mark_failed.assert_awaited_once_with(claimed, error)  # type: ignore[attr-defined]
    worker._mark_sent.assert_not_awaited()  # type: ignore[attr-defined]


def test_retry_delay_is_bounded() -> None:
    assert OutboxWorker.retry_delay(1).total_seconds() == 2
    assert OutboxWorker.retry_delay(20).total_seconds() == 3600
