"""Protect active organization owners and platform administrators.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM organizations AS o
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM org_members AS m
                    JOIN users AS u ON u.id = m.user_id
                    WHERE m.org_id = o.id AND m.role = 'owner' AND u.is_active
                )
            ) THEN
                RAISE EXCEPTION 'existing organization without active owner'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ensure_organization_has_owner()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            affected_org uuid;
            affected_orgs uuid[];
        BEGIN
            IF TG_OP = 'DELETE' THEN
                affected_orgs := ARRAY[OLD.org_id];
            ELSE
                affected_orgs := ARRAY[OLD.org_id, NEW.org_id];
            END IF;

            FOREACH affected_org IN ARRAY affected_orgs LOOP
                IF affected_org IS NULL
                   OR NOT EXISTS (SELECT 1 FROM organizations WHERE id = affected_org) THEN
                    CONTINUE;
                END IF;

                PERFORM id FROM organizations WHERE id = affected_org FOR UPDATE;

                IF NOT EXISTS (
                    SELECT 1
                    FROM org_members AS m
                    JOIN users AS u ON u.id = m.user_id
                    WHERE m.org_id = affected_org
                      AND m.role = 'owner'
                      AND u.is_active
                ) THEN
                    RAISE EXCEPTION 'organization % must retain an active owner', affected_org
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION ensure_new_organization_has_owner()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM org_members AS m
                JOIN users AS u ON u.id = m.user_id
                WHERE m.org_id = NEW.id AND m.role = 'owner' AND u.is_active
            ) THEN
                RAISE EXCEPTION 'organization % must have an active owner', NEW.id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER organizations_require_owner
        AFTER INSERT ON organizations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION ensure_new_organization_has_owner();
        """
    )
    op.execute(
        """
        CREATE FUNCTION ensure_user_deactivation_is_safe()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            owned_org uuid;
            removes_superuser boolean;
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.is_active AND NOT NEW.is_active THEN
                FOR owned_org IN
                    SELECT org_id
                    FROM org_members
                    WHERE user_id = OLD.id AND role = 'owner'
                LOOP
                    PERFORM id FROM organizations WHERE id = owned_org FOR UPDATE;
                    IF NOT EXISTS (
                        SELECT 1
                        FROM org_members AS m
                        JOIN users AS u ON u.id = m.user_id
                        WHERE m.org_id = owned_org
                          AND m.role = 'owner'
                          AND u.is_active
                    ) THEN
                        RAISE EXCEPTION 'organization % must retain an active owner', owned_org
                            USING ERRCODE = '23514';
                    END IF;
                END LOOP;
            END IF;

            IF TG_OP = 'DELETE' THEN
                removes_superuser := OLD.is_active AND OLD.is_superuser;
            ELSE
                removes_superuser := OLD.is_active AND OLD.is_superuser
                    AND (NOT NEW.is_active OR NOT NEW.is_superuser);
            END IF;

            IF removes_superuser THEN
                -- Prevent write-skew when administrators disable different rows.
                PERFORM pg_advisory_xact_lock(1515870811);
            END IF;

            IF removes_superuser AND NOT EXISTS (
                   SELECT 1
                   FROM users
                   WHERE id <> OLD.id AND is_active AND is_superuser
               ) THEN
                RAISE EXCEPTION 'platform must retain an active superuser'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER users_require_active_owner_and_superuser
        AFTER UPDATE OF is_active, is_superuser OR DELETE ON users
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION ensure_user_deactivation_is_safe();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS users_require_active_owner_and_superuser ON users")
    op.execute("DROP FUNCTION IF EXISTS ensure_user_deactivation_is_safe()")
    op.execute("DROP TRIGGER IF EXISTS organizations_require_owner ON organizations")
    op.execute("DROP FUNCTION IF EXISTS ensure_new_organization_has_owner()")
