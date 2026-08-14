"""Сборка всех роутеров под общим префиксом /api."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import admin, auth, health, orgs, reviews

api_router = APIRouter(prefix="/api")

# Порядок регистрации важен: более специфичные префиксы раньше общих,
# чтобы /orgs/{slug}/reviews не перехватывался маршрутом /orgs/{slug}.
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(reviews.public_router)
api_router.include_router(reviews.admin_router)
api_router.include_router(orgs.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
