"""0028_data_subject_requests — the ledger of data-subject requests.

Revision ID: 0028_data_subject_requests
Revises: 0027_consulta_reemplazada
Create Date: 2026-10-25

Ley 25.326 arts. 14-16: a person may ask to see their data (answer within 10
calendar days), to correct it or to have it erased (5 working days). Until now
nothing recorded that such a request arrived, when it was due, who checked the
person's identity or what was answered. `data_subject_requests` is the state of
each request; `data_subject_request_events` is its trail and, like the clinical
access log, only ever grows (a trigger refuses UPDATE and DELETE). The person
is stored as the HMAC of the DNI (`core/access_log.dni_hash`), never the DNI.

Additive only — two new tables, nothing existing is touched. The tables are
also in the frozen baseline (`alembic/baseline/0001_schema.sql`, written before
it was frozen); the trigger (a dollar-quoted plpgsql body the baseline parser
does not accept) lives only here, as `GUARD_SQL` — frozen with this revision,
so an edit elsewhere can never change what 0028 applies. A fresh install runs
the baseline and then every forward revision, so it gets the trigger too.
Idempotent; forward-only.
"""
from alembic import op

revision = "0028_data_subject_requests"
down_revision = "0027_consulta_reemplazada"
branch_labels = None
depends_on = None

# La otra mitad de data_subject_request_events (revisión 0028): el rastro de un
# pedido de acceso / rectificación / supresión sólo se agrega, igual que el
# registro de accesos y por la misma razón (prueba de que se respondió a
# tiempo). No hay purga: cada fila es una línea de un expediente. TRUNCATE no
# pasa por triggers de fila: lo cierra el rol de la aplicación.
GUARD_SQL = """
CREATE OR REPLACE FUNCTION data_subject_request_events_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'data_subject_request_events es de sólo agregado'
        USING ERRCODE = 'insufficient_privilege';
END
$$;
DROP TRIGGER IF EXISTS data_subject_request_events_guard_row ON data_subject_request_events;
CREATE TRIGGER data_subject_request_events_guard_row
    BEFORE UPDATE OR DELETE ON data_subject_request_events
    FOR EACH ROW EXECUTE FUNCTION data_subject_request_events_guard();
"""


def upgrade():
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS data_subject_requests (
            id              bigserial PRIMARY KEY,
            kind            text NOT NULL CHECK (kind IN ('access','rectification','erasure')),
            subject_hash    text NOT NULL,
            received_on     date NOT NULL,
            due_on          date NOT NULL,
            channel         text NOT NULL DEFAULT '',
            identity_check  text NOT NULL DEFAULT '',
            status          text NOT NULL DEFAULT 'received'
                            CHECK (status IN ('received','under_review','answered','refused')),
            outcome         text NOT NULL DEFAULT '',
            registered_by   text NOT NULL,
            decided_by      text,
            answered_on     date,
            created         timestamptz NOT NULL DEFAULT now(),
            updated         timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS data_subject_requests_subject_idx
            ON data_subject_requests (subject_hash, received_on DESC);
        CREATE INDEX IF NOT EXISTS data_subject_requests_open_idx
            ON data_subject_requests (status, due_on);
        CREATE TABLE IF NOT EXISTS data_subject_request_events (
            id          bigserial PRIMARY KEY,
            request_id  bigint NOT NULL,
            ts          timestamptz NOT NULL DEFAULT now(),
            actor       text NOT NULL,
            event       text NOT NULL,
            detail      text NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS data_subject_request_events_request_idx
            ON data_subject_request_events (request_id, ts);
        """
    )
    op.get_bind().exec_driver_sql(GUARD_SQL)


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "Rollback) — restore from a backup if an upgrade goes wrong."
    )
