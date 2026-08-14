"""Начальная схема: пользователи, сессии, организации, отзывы, аудит.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
NOW = sa.text("now()")
EMPTY = sa.text("''")


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", UUID, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(120), server_default=EMPTY, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("email_verified_at", TS, nullable=True),
        sa.Column("totp_secret_enc", sa.Text(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", TS, nullable=True),
        sa.Column("last_login_at", TS, nullable=True),
        sa.Column("password_changed_at", TS, nullable=True),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("updated_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("position('@' in email) > 1", name="ck_users_email_shape"),
    )
    op.create_index(
        "ix_users_locked_until",
        "users",
        ["locked_until"],
        postgresql_where=sa.text("locked_until IS NOT NULL"),
    )

    # organizations
    op.create_table(
        "organizations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), server_default=EMPTY, nullable=False),
        sa.Column("plan", sa.String(32), server_default=sa.text("'free'"), nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "auto_approve_reviews",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("updated_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'", name="ck_organizations_slug_shape"
        ),
        sa.CheckConstraint(
            "plan IN ('free', 'team', 'business')", name="ck_organizations_plan_known"
        ),
    )

    # sessions
    op.create_table(
        "sessions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("ip_hash", sa.String(32), server_default=EMPTY, nullable=False),
        sa.Column("user_agent", sa.String(256), server_default=EMPTY, nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("last_seen_at", TS, server_default=NOW, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_sessions_user_active", "sessions", ["user_id", "expires_at"])

    # email_tokens
    op.create_table(
        "email_tokens",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_email_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_email_tokens_token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_email_tokens_user_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "purpose IN ('email_verify', 'password_reset')", name="ck_email_tokens_purpose_known"
        ),
    )
    op.create_index("ix_email_tokens_user_purpose", "email_tokens", ["user_id", "purpose"])

    # recovery_codes
    op.create_table(
        "recovery_codes",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_recovery_codes"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_recovery_codes_user_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_recovery_user_code"),
    )

    # org_members
    op.create_table(
        "org_members",
        sa.Column("org_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("role", sa.String(16), server_default=sa.text("'member'"), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("org_id", "user_id", name="pk_org_members"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_org_members_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_org_members_user_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "role IN ('member', 'admin', 'owner')", name="ck_org_members_role_known"
        ),
    )
    op.create_index("ix_org_members_user", "org_members", ["user_id"])

    # invitations
    op.create_table(
        "invitations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("org_id", UUID, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(16), server_default=sa.text("'member'"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by", UUID, nullable=True),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("accepted_at", TS, nullable=True),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_invitations_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name="fk_invitations_invited_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "role IN ('member', 'admin', 'owner')", name="ck_invitations_role_known"
        ),
    )
    # Одно неотвеченное приглашение на пару (организация, e-mail).
    op.create_index(
        "uq_invitations_pending",
        "invitations",
        ["org_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL"),
    )

    # reviews
    op.create_table(
        "reviews",
        sa.Column("id", UUID, nullable=False),
        sa.Column("org_id", UUID, nullable=False),
        sa.Column("author_name", sa.String(80), nullable=False),
        sa.Column("author_email", sa.String(320), server_default=EMPTY, nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(120), server_default=EMPTY, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("ip_hash", sa.String(32), server_default=EMPTY, nullable=False),
        sa.Column("user_agent", sa.String(256), server_default=EMPTY, nullable=False),
        sa.Column("moderated_by", UUID, nullable=True),
        sa.Column("moderated_at", TS, nullable=True),
        sa.Column("moderation_note", sa.String(500), server_default=EMPTY, nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reviews"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_reviews_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["moderated_by"],
            ["users.id"],
            name="fk_reviews_moderated_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating_range"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'spam')", name="ck_reviews_status_known"
        ),
        sa.CheckConstraint("length(body) BETWEEN 10 AND 5000", name="ck_reviews_body_length"),
    )
    op.create_index(
        "ix_reviews_public", "reviews", ["org_id", "status", sa.text("created_at DESC")]
    )
    op.create_index(
        "ix_reviews_pending",
        "reviews",
        ["org_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_reviews_ip_recent", "reviews", ["ip_hash", sa.text("created_at DESC")])

    # audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", UUID, nullable=False),
        sa.Column("actor_user_id", UUID, nullable=True),
        sa.Column("org_id", UUID, nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), server_default=EMPTY, nullable=False),
        sa.Column("target_id", sa.String(64), server_default=EMPTY, nullable=False),
        sa.Column("ip_hash", sa.String(32), server_default=EMPTY, nullable=False),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_log_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_audit_log_org_id_organizations",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_audit_actor_time", "audit_log", ["actor_user_id", sa.text("created_at DESC")]
    )
    op.create_index("ix_audit_org_time", "audit_log", ["org_id", sa.text("created_at DESC")])
    op.create_index("ix_audit_action_time", "audit_log", ["action", sa.text("created_at DESC")])


def downgrade() -> None:
    # Порядок обратный созданию: сначала таблицы, ссылающиеся на другие.
    op.drop_table("audit_log")
    op.drop_table("reviews")
    op.drop_table("invitations")
    op.drop_table("org_members")
    op.drop_table("recovery_codes")
    op.drop_table("email_tokens")
    op.drop_table("sessions")
    op.drop_table("organizations")
    op.drop_table("users")
