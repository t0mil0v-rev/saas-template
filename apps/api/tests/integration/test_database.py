"""PostgreSQL invariants and concurrent organization updates."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from app.core.config import Settings
from app.core.errors import ConflictError
from app.db.models import Organization, OrgMember, OrgRole, User
from app.db.session import get_sessionmaker
from app.services.mailer import Mailer
from app.services.org import OrgService
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_organization_without_owner_cannot_commit(
    integration_settings: Settings,
) -> None:
    maker = get_sessionmaker()
    async with maker() as db:
        db.add(Organization(name="Ownerless", slug="ownerless"))
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_organization_and_owner_can_commit(integration_settings: Settings) -> None:
    maker = get_sessionmaker()
    async with maker() as db:
        user = User(email="owner@example.com", password_hash="unused")
        org = Organization(name="Owned", slug="owned")
        db.add_all([user, org])
        await db.flush()
        db.add(OrgMember(org_id=org.id, user_id=user.id, role=OrgRole.OWNER.value))
        await db.commit()


@pytest.mark.asyncio
async def test_concurrent_demotions_leave_one_owner(integration_settings: Settings) -> None:
    maker = get_sessionmaker()
    async with maker() as db:
        first = User(email="first@example.com", password_hash="unused")
        second = User(email="second@example.com", password_hash="unused")
        org = Organization(name="Concurrent", slug="concurrent")
        db.add_all([first, second, org])
        await db.flush()
        db.add_all(
            [
                OrgMember(org_id=org.id, user_id=first.id, role=OrgRole.OWNER.value),
                OrgMember(org_id=org.id, user_id=second.id, role=OrgRole.OWNER.value),
            ]
        )
        await db.commit()
        org_id, first_id, second_id = org.id, first.id, second.id

    async def demote(actor_id: uuid.UUID) -> object:
        async with maker() as db:
            org = await db.get(Organization, org_id)
            actor = await db.get(User, actor_id)
            assert org is not None and actor is not None
            service = OrgService(db, integration_settings, Mailer(integration_settings))
            try:
                await service.change_role(org, actor, OrgRole.OWNER, actor_id, OrgRole.MEMBER)
                await db.commit()
                return "committed"
            except Exception:
                await db.rollback()
                raise

    results = await asyncio.gather(
        demote(first_id),
        demote(second_id),
        return_exceptions=True,
    )
    assert results.count("committed") == 1
    assert sum(isinstance(result, ConflictError) for result in results) == 1

    async with maker() as db:
        owners = await db.scalar(
            select(func.count())
            .select_from(OrgMember)
            .where(OrgMember.org_id == org_id, OrgMember.role == OrgRole.OWNER.value)
        )
        assert owners == 1


@pytest.mark.asyncio
async def test_last_active_owner_cannot_be_deactivated(
    integration_settings: Settings,
) -> None:
    maker = get_sessionmaker()
    async with maker() as db:
        owner = User(email="active-owner@example.com", password_hash="unused")
        org = Organization(name="Active owner", slug="active-owner")
        db.add_all([owner, org])
        await db.flush()
        db.add(OrgMember(org_id=org.id, user_id=owner.id, role=OrgRole.OWNER.value))
        await db.commit()

        owner.is_active = False
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_last_active_superuser_cannot_be_disabled(
    integration_settings: Settings,
) -> None:
    maker = get_sessionmaker()
    async with maker() as db:
        admin = User(
            email="last-admin@example.com",
            password_hash="unused",
            is_superuser=True,
        )
        db.add(admin)
        await db.commit()

        admin.is_superuser = False
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_concurrent_owner_deactivation_cannot_remove_all_active_owners(
    integration_settings: Settings,
) -> None:
    maker = get_sessionmaker()
    async with maker() as db:
        first = User(email="owner-a@example.com", password_hash="unused")
        second = User(email="owner-b@example.com", password_hash="unused")
        org = Organization(name="Active owners", slug="active-owners")
        db.add_all([first, second, org])
        await db.flush()
        db.add_all(
            [
                OrgMember(org_id=org.id, user_id=first.id, role=OrgRole.OWNER.value),
                OrgMember(org_id=org.id, user_id=second.id, role=OrgRole.OWNER.value),
            ]
        )
        await db.commit()
        user_ids = (first.id, second.id)

    async def deactivate(user_id: uuid.UUID) -> object:
        async with maker() as db:
            user = await db.get(User, user_id)
            assert user is not None
            user.is_active = False
            try:
                await db.commit()
                return "committed"
            except IntegrityError as exc:
                await db.rollback()
                return exc

    results = await asyncio.gather(*(deactivate(user_id) for user_id in user_ids))
    assert results.count("committed") == 1
    assert sum(isinstance(result, IntegrityError) for result in results) == 1
