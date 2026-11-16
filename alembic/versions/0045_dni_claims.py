"""Claims on a document number: who still has to show their ID at the desk.

Revision ID: 0045_dni_claims
Revises: 0044_analytics_stream
Create Date: 2026-11-14

One additive table. A sign-up by password and DNI photo (the default path of an open-source installation,
no portal in front) proves nothing about the document, and `users.dni` is the primary key, so the first
person to type a number used to lock the real owner out (security review 2026-11, TASK-68). Each row is one
account whose number the front desk has not checked yet:
  unverified  the first account on that number; its owner sees no clinical data until the desk confirms;
  pending     a second sign-up on a number already taken: it lives under a minted key, `dni_claimed` says
              which number it asks for, and the desk decides which of the two is the owner;
  approved / rejected  how the desk closed it (who and when in resolved_by / resolved_at).
Accounts that existed before this revision have no row, which means «confirmed».
Forward-only.
"""
from alembic import op

revision = "0045_dni_claims"
down_revision = "0044_analytics_stream"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS dni_claims (
            id serial NOT NULL,
            dni_claimed text NOT NULL,
            claimant_key text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            created timestamptz NOT NULL DEFAULT now(),
            resolved_at timestamptz,
            resolved_by text,
            note text,
            CONSTRAINT dni_claims_pkey PRIMARY KEY (id),
            CONSTRAINT dni_claims_status_check CHECK (status IN ('unverified', 'pending', 'approved', 'rejected'))
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS dni_claims_claimed_idx ON dni_claims (dni_claimed)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS dni_claims_open_uniq ON dni_claims (claimant_key) "
               "WHERE status IN ('unverified', 'pending')")


def downgrade():
    raise NotImplementedError("the DNI claims table is forward-only")
