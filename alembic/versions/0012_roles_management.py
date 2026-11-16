"""Editable staff roles and per-account permission overrides (R5/R6).

Revision ID: 0012_roles_management
Revises: 0011_patient_lab_uploads
Create Date: 2026-09-10

Two new tables, the same DDL `core/db.py:_SCHEMA_SQL` carries for fresh
installs (a fresh install replays the baseline and then every revision, so
everything here is idempotent — IF NOT EXISTS and ON CONFLICT DO NOTHING —
exactly like 0002-0010):

* `roles` — one row per role, keyed by the slug the session cookie carries,
  seeded with the five built-in presets of `admin_panel/permissions.py`.
* `operator_access` — per-account role slug (only when it is not one of the
  four values `operators.role` can hold) plus granted/denied overrides.

`operators` is deliberately untouched: its frozen `operators_role_check` is
part of the pre-Alembic baseline shape proof (`migrations.stamp_shape_gaps`).
"""
from alembic import op


revision = "0012_roles_management"
down_revision = "0011_patient_lab_uploads"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            slug         text PRIMARY KEY,
            name         text NOT NULL,
            description  text NOT NULL DEFAULT '',
            builtin      boolean NOT NULL DEFAULT false,
            enabled      boolean NOT NULL DEFAULT true,
            permissions  text[] NOT NULL DEFAULT '{}',
            created      timestamptz NOT NULL DEFAULT now(),
            updated      timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        INSERT INTO roles (slug, name, description, builtin, permissions) VALUES
            ('admin', 'Administrador', 'Puede hacer todo: atención, catálogo, usuarios, configuración y reportes.', true,
             ARRAY['solicitudes','cola','crear_turnos','pacientes','historia_clinica','atencion_medica','medicos','servicios','reportes','asistente','apariencia','configuracion','hsi','sistema','legal','usuarios']),
            ('admisionista', 'Mesa de admisión', 'Ve solicitudes y pacientes de hoy. Puede crear pacientes y turnos.', true,
             ARRAY['solicitudes','cola','crear_turnos','pacientes']),
            ('gestor_agendas', 'Gestor de agendas', 'Atiende la mesa de admisión y además arma las agendas de los médicos: horarios, días de atención y cierres.', true,
             ARRAY['solicitudes','cola','crear_turnos','pacientes','medicos']),
            ('medico', 'Médico', 'Ve las solicitudes asignadas, sus pacientes de hoy y sus turnos. Puede reasignar una solicitud a otro médico.', true,
             ARRAY['solicitudes','cola','crear_turnos','atencion_medica']),
            ('reportes', 'Ve reportes y estadísticas', 'Sólo entra a Reportes: ocupación, tiempos de atención y estadística mensual.', true,
             ARRAY['reportes'])
        ON CONFLICT (slug) DO NOTHING
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS operator_access (
            username     text PRIMARY KEY,
            role_slug    text,
            granted      text[] NOT NULL DEFAULT '{}',
            denied       text[] NOT NULL DEFAULT '{}',
            updated      timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS operator_access")
    op.execute("DROP TABLE IF EXISTS roles")
