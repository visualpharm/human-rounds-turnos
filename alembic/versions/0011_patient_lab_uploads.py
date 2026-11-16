"""Store lab results the patient uploads from their own account.

Revision ID: 0011_patient_lab_uploads
Revises: 0010_passkey_challenge_store
Create Date: 2026-09-11

These rows are patient-supplied, never authoritative clinical history: the
`patient_uploads` connector hands them to the chart summary flagged as third
party so a doctor reads them as unverified.
"""
from alembic import op


revision = "0011_patient_lab_uploads"
down_revision = "0010_passkey_challenge_store"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS patient_lab_uploads (
            id              bigserial PRIMARY KEY,
            dni             text NOT NULL,
            uploaded_by_dni text NOT NULL,
            filename        text,
            mime            text,
            archivo         bytea,
            texto           text,
            fecha           text,
            sin_lectura     boolean NOT NULL DEFAULT false,
            created         timestamptz NOT NULL DEFAULT now(),
            deleted         timestamptz
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS patient_lab_uploads_dni_idx
            ON patient_lab_uploads (dni, created DESC)
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS patient_lab_uploads")
