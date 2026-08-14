"""Отзывы: публичный приём и модерация.

Публичная форма - единственная точка, куда анонимный посетитель может
что-то записать в базу. Поэтому здесь сосредоточена защита от спама, и
устроена она без внешних сервисов: сервер в закрытом контуре не может
сходить в reCAPTCHA, а отзывы принимать должен.

Слои защиты, каждый дешёвый и независимый:

1. Ограничение частоты по IP (см. ``RATE_LIMIT_REVIEW_PER_HOUR``).
2. Honeypot-поле, скрытое в вёрстке.
3. Проверка на повтор: тот же текст с того же адреса.
4. Эвристика по ссылкам: текст из одних ссылок уходит в ручную модерацию,
   даже если у организации включена автопубликация.

Важная деталь поведения: спам не отвергается с ошибкой. Ответ всегда
одинаковый - «принято, ожидает проверки». Бот не получает обратной связи
и не может подобрать обход, а живой человек, случайно попавший под
эвристику, увидит свой отзыв после ручной модерации.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.security import hash_ip
from app.db.models import Organization, Review, ReviewStatus, User
from app.services import audit

log = get_logger("review")

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
DUPLICATE_WINDOW = timedelta(hours=24)
MAX_LINKS_FOR_AUTO_APPROVE = 1


def _now() -> datetime:
    return datetime.now(UTC)


class ReviewService:
    def __init__(self, session: AsyncSession, *, ip: str = "", user_agent: str = "") -> None:
        self.db = session
        self.ip = ip
        self.ip_hash = hash_ip(ip) if ip else ""
        self.user_agent = user_agent[:256]

    # Публичный приём

    async def submit(
        self,
        org: Organization,
        *,
        author_name: str,
        author_email: str | None,
        rating: int,
        title: str,
        body: str,
        honeypot: str = "",
    ) -> Review:
        status, reason = await self._classify(org, body=body, honeypot=honeypot)

        review = Review(
            org_id=org.id,
            author_name=author_name,
            author_email=author_email or "",
            rating=rating,
            title=title,
            body=body,
            status=status.value,
            ip_hash=self.ip_hash,
            user_agent=self.user_agent,
        )
        self.db.add(review)
        await self.db.flush()

        await audit.record(
            self.db,
            audit.Action.REVIEW_SUBMITTED,
            org_id=org.id,
            target_type="review",
            target_id=str(review.id),
            ip_hash=self.ip_hash,
            meta={"status": status.value, "reason": reason, "rating": rating},
        )
        if status is ReviewStatus.SPAM:
            log.info("review_flagged_spam", org=org.slug, reason=reason)
        return review

    async def _classify(
        self, org: Organization, *, body: str, honeypot: str
    ) -> tuple[ReviewStatus, str]:
        """Решает, в каком статусе принять отзыв."""
        if honeypot.strip():
            # Поле скрыто в вёрстке - заполнить его мог только бот.
            return ReviewStatus.SPAM, "honeypot"

        if self.ip_hash:
            duplicate = await self.db.scalar(
                select(Review.id).where(
                    Review.org_id == org.id,
                    Review.ip_hash == self.ip_hash,
                    Review.body == body,
                    Review.created_at > _now() - DUPLICATE_WINDOW,
                )
            )
            if duplicate is not None:
                return ReviewStatus.SPAM, "duplicate"

        links = len(_URL_RE.findall(body))
        if links > MAX_LINKS_FOR_AUTO_APPROVE:
            return ReviewStatus.PENDING, "too_many_links"

        if org.auto_approve_reviews:
            return ReviewStatus.APPROVED, "auto_approved"
        return ReviewStatus.PENDING, "manual_moderation"

    # Публичное чтение

    async def public_list(
        self, org: Organization, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[Review], int]:
        base = select(Review).where(
            Review.org_id == org.id, Review.status == ReviewStatus.APPROVED.value
        )
        total = int(await self.db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        items = list(
            (
                await self.db.scalars(
                    base.order_by(Review.created_at.desc()).limit(limit).offset(offset)
                )
            ).all()
        )
        return items, total

    async def public_stats(self, org: Organization) -> dict[str, object]:
        rows = (
            await self.db.execute(
                select(Review.rating, func.count())
                .where(
                    Review.org_id == org.id,
                    Review.status == ReviewStatus.APPROVED.value,
                )
                .group_by(Review.rating)
            )
        ).all()

        distribution = {int(rating): int(count) for rating, count in rows}
        total = sum(distribution.values())
        average = (
            sum(rating * count for rating, count in distribution.items()) / total if total else 0.0
        )
        return {
            "total": total,
            "average": round(average, 2),
            "distribution": {star: distribution.get(star, 0) for star in range(5, 0, -1)},
        }

    # Модерация

    async def moderation_list(
        self,
        org: Organization,
        *,
        status: ReviewStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Review], int]:
        base = select(Review).where(Review.org_id == org.id)
        if status is not None:
            base = base.where(Review.status == status.value)

        total = int(await self.db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        items = list(
            (
                await self.db.scalars(
                    base.order_by(Review.created_at.desc()).limit(limit).offset(offset)
                )
            ).all()
        )
        return items, total

    async def moderate(
        self,
        org: Organization,
        actor: User,
        review_id: uuid.UUID,
        *,
        status: ReviewStatus,
        note: str = "",
    ) -> Review:
        review = await self.db.scalar(
            # Условие по org_id обязательно: без него модератор одной
            # организации смог бы менять отзывы чужой, зная только id.
            select(Review).where(Review.id == review_id, Review.org_id == org.id).with_for_update()
        )
        if review is None:
            raise NotFoundError("Отзыв не найден")

        previous = review.status
        review.status = status.value
        review.moderation_note = note
        review.moderated_by = actor.id
        review.moderated_at = _now()

        await audit.record(
            self.db,
            audit.Action.REVIEW_MODERATED,
            actor_user_id=actor.id,
            org_id=org.id,
            target_type="review",
            target_id=str(review.id),
            ip_hash=self.ip_hash,
            meta={"from": previous, "to": status.value},
        )
        return review

    async def pending_count(self, org: Organization) -> int:
        return int(
            await self.db.scalar(
                select(func.count())
                .select_from(Review)
                .where(
                    Review.org_id == org.id,
                    Review.status == ReviewStatus.PENDING.value,
                )
            )
            or 0
        )
