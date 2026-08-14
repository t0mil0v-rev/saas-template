"""Enforce the organization-owner invariant in PostgreSQL.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make an owner-less existing organization impossible to commit.

    Application-level row locking gives a friendly conflict response. This
    deferred constraint trigger is the final database-level guard against
    ad-hoc SQL, future code paths, and races that bypass the service layer.
    """
    op.execute(
        """
        CREATE FUNCTION ensure_organization_has_owner()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            affected_org uuid;
            affected_orgs uuid[];
        BEGIN
            -- On a move between organizations both sides must remain valid:
            -- the source can lose its last owner, while the destination is
            -- checked too if it was already inconsistent.
            IF TG_OP = 'DELETE' THEN
                affected_orgs := ARRAY[OLD.org_id];
            ELSE
                affected_orgs := ARRAY[OLD.org_id, NEW.org_id];
            END IF;

            FOREACH affected_org IN ARRAY affected_orgs LOOP
                IF affected_org IS NULL THEN
                    CONTINUE;
                END IF;

                -- A cascading delete of the organization itself legitimately
                -- removes all memberships; do not reject that transaction.
                IF NOT EXISTS (SELECT 1 FROM organizations WHERE id = affected_org) THEN
                    CONTINUE;
                END IF;

                -- Serialize direct SQL changes that touch different membership
                -- rows but can jointly remove all active owners.
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
        CREATE CONSTRAINT TRIGGER org_members_require_owner
        AFTER DELETE OR UPDATE OF role, org_id ON org_members
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION ensure_organization_has_owner();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS org_members_require_owner ON org_members")
    op.execute("DROP FUNCTION IF EXISTS ensure_organization_has_owner()")
