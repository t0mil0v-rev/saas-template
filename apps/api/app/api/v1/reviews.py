"""Роуты отзывов.

Два контура в одном файле, и граница между ними - суть безопасности модуля:

* Публичный (`/public/...`) - без аутентификации. Сюда пишет аноним.
  Ответ на отправку всегда одинаков, спам не подтверждается (см. ReviewService).
* Модерация (`/orgs/{slug}/reviews/...`) - требует роли не ниже admin
  в конкретной организации. Здесь виден e-mail автора и служебные поля.
"""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import (
    ClientDep,
    CsrfProtected,
    DbDep,
    OrgAdminContext,
    PublicOrg,
    review_rate_limit,
)
from app.db.models import ReviewStatus
from app.schemas.common import Message, Page
from app.schemas.review import (
    ReviewAdminOut,
    ReviewCreate,
    ReviewModerate,
    ReviewOut,
    ReviewStats,
)
from app.services.review import ReviewService

# Публичный контур

public_router = APIRouter(prefix="/public/orgs/{slug}", tags=["public"])


@public_router.get("", summary="Публичная карточка организации")
async def public_org(org: PublicOrg, db: DbDep) -> dict[str, object]:
    service = ReviewService(db)
    stats = await service.public_stats(org)
    return {
        "slug": org.slug,
        "name": org.name,
        "description": org.description,
        "reviews_count": stats["total"],
        "average_rating": stats["average"],
    }


@public_router.get("/reviews", summary="Опубликованные отзывы")
async def public_reviews(
    org: PublicOrg,
    db: DbDep,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> Page[ReviewOut]:
    service = ReviewService(db)
    items, total = await service.public_list(org, limit=limit, offset=offset)
    return Page[ReviewOut](
        items=[ReviewOut.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@public_router.get("/reviews/stats", summary="Сводка по оценкам")
async def public_review_stats(org: PublicOrg, db: DbDep) -> ReviewStats:
    service = ReviewService(db)
    stats = await service.public_stats(org)
    return ReviewStats(
        total=cast(int, stats["total"]),
        average=cast(float, stats["average"]),
        distribution=cast(dict[int, int], stats["distribution"]),
    )


@public_router.post(
    "/reviews",
    status_code=201,
    dependencies=[Depends(review_rate_limit)],
    summary="Оставить отзыв",
)
async def submit_review(
    payload: ReviewCreate,
    org: PublicOrg,
    request: Request,
    client: ClientDep,
    db: DbDep,
) -> Message:
    service = ReviewService(db, ip=client.ip, user_agent=request.headers.get("user-agent", ""))
    await service.submit(
        org,
        author_name=payload.author_name,
        author_email=str(payload.author_email) if payload.author_email else None,
        rating=payload.rating,
        title=payload.title,
        body=payload.body,
        honeypot=payload.website,
    )
    await db.commit()
    # Ответ намеренно нейтрален и одинаков для принятого и для отсеянного как
    # спам отзыва: бот не получает сигнала, а честный автор - понятный статус.
    return Message(detail="Спасибо! Отзыв отправлен и появится после проверки модератором.")


# Контур модерации

admin_router = APIRouter(prefix="/orgs/{slug}/reviews", tags=["moderation"])


@admin_router.get("", summary="Очередь модерации")
async def moderation_list(
    ctx: OrgAdminContext,
    db: DbDep,
    status: ReviewStatus | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> Page[ReviewAdminOut]:
    service = ReviewService(db)
    items, total = await service.moderation_list(ctx.org, status=status, limit=limit, offset=offset)
    return Page[ReviewAdminOut](
        items=[ReviewAdminOut.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@admin_router.get("/pending-count", summary="Сколько отзывов ждёт модерации")
async def pending_count(ctx: OrgAdminContext, db: DbDep) -> dict[str, int]:
    service = ReviewService(db)
    return {"pending": await service.pending_count(ctx.org)}


@admin_router.post(
    "/{review_id}/moderate",
    dependencies=[CsrfProtected],
    summary="Одобрить / отклонить / пометить спамом",
)
async def moderate_review(
    review_id: uuid.UUID,
    payload: ReviewModerate,
    ctx: OrgAdminContext,
    db: DbDep,
) -> ReviewAdminOut:
    service = ReviewService(db)
    review = await service.moderate(
        ctx.org, ctx.user, review_id, status=payload.status, note=payload.note
    )
    await db.commit()
    return ReviewAdminOut.model_validate(review)
