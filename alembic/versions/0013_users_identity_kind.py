"""0013_users_identity_kind — how a user's key came to be, as a real
forward revision.

Revision ID: 0013_users_identity_kind
Revises: 0012_roles_management
Create Date: 2026-09-11

`users.identity_kind` records WHETHER the account's key (users.dni) is a
real national document ('dni', the default — also for every account that
predates this column) or a synthetic key the server minted ("9" + 8 digits)
when a visitor whose active language is not Spanish signed up without one
(the product owner, 2026-09-11: the gate is the visitor's LANGUAGE, not the installation
country; the demo shows an AR install to international visitors). Every
DNI-keyed table keeps working unchanged — login/HSI/bookings still key on
users.dni — and the flag exists so no screen ever paints a made-up number
at a person as if it were their document.

Same two-halves contract as every revision since 0002: the DDL below must
stay byte-equivalent to the `identity_kind` ALTER in db._SCHEMA_SQL (fresh
installs get it through the baseline replay and this revision must be a
clean no-op there); existing stamped databases receive it here, on their
next boot's `upgrade(head)`. `IF NOT EXISTS` is load-bearing, not
defensive fluff.

Forward-only, like every revision in this repo (docs/UPGRADING.md,
"Rollback"): `downgrade()` refuses.
"""
from alembic import op

revision = "0013_users_identity_kind"
down_revision = "0012_roles_management"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS identity_kind text NOT NULL DEFAULT 'dni';
        """
    )


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "\"Rollback\") — restore from a backup if an upgrade goes wrong."
    )
