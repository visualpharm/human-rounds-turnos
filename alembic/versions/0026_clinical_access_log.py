"""0026_clinical_access_log — who opened a patient's chart and medical data.

Revision ID: 0026_clinical_access_log
Revises: 0025_patient_portal_user_id
Create Date: 2026-10-23

Ley 26.529 art. 18 / Ley 25.326 art. 9-10 / Res. AAIP 47/2018: every staff
read of a patient's clinical data (ficha, pre-consult interview, consultation,
AI chart summary, clinical history, labs, referral file, health profile,
printables) leaves one row here — who, when, which patient, which screen or
action, and whether it was allowed. The patient is identified only by an HMAC
of the clinical DNI (`core/access_log.py`): the log can be searched by DNI but
a dump of it does not list anyone's document.

The table is append-only at the database level: the guard trigger refuses
every UPDATE, and a DELETE outside the retention purge
(`turnos.access_log_purge` set for that transaction) or on a row younger than
one year, whatever the configured retention says. TRUNCATE is closed by the
application role's grants (scripts/db_app_role.py).

Two halves: the table is also in the frozen baseline (`alembic/baseline/
0001_schema.sql`, written before the baseline was frozen); the guard (a
dollar-quoted plpgsql body the baseline parser does not accept) lives only
here, as `GUARD_SQL` — frozen with this revision, so a later edit elsewhere can
never change what 0026 applies. A fresh install runs the baseline and then
every forward revision, so it gets the guard too. Idempotent; forward-only.
"""
from alembic import op

revision = "0026_clinical_access_log"
down_revision = "0025_patient_portal_user_id"
branch_labels = None
depends_on = None

# UPDATE is always refused; DELETE only inside the age-based purge
# (`purge_clinical_access_log` sets turnos.access_log_purge for that
# transaction) and never on a row younger than a year, whatever retention is
# configured. TRUNCATE bypasses row triggers: it is closed by the application
# role lacking that privilege (scripts/db_app_role.py); tests use it to clean
# between cases.
GUARD_SQL = """
CREATE OR REPLACE FUNCTION clinical_access_log_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_setting('turnos.access_log_purge', true) = 'on'
       AND OLD.ts < now() - interval '365 days' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'clinical_access_log es de sólo agregado'
        USING ERRCODE = 'insufficient_privilege';
END
$$;
DROP TRIGGER IF EXISTS clinical_access_log_guard_row ON clinical_access_log;
CREATE TRIGGER clinical_access_log_guard_row
    BEFORE UPDATE OR DELETE ON clinical_access_log
    FOR EACH ROW EXECUTE FUNCTION clinical_access_log_guard();
"""


def upgrade():
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS clinical_access_log (
            id          bigserial PRIMARY KEY,
            ts          timestamptz NOT NULL DEFAULT now(),
            actor       text NOT NULL,
            actor_role  text NOT NULL DEFAULT '',
            action      text NOT NULL,
            dni_hash    text NOT NULL,
            target_id   text,
            allowed     boolean NOT NULL DEFAULT true
        );
        CREATE INDEX IF NOT EXISTS clinical_access_log_dni_idx
            ON clinical_access_log (dni_hash, ts DESC);
        CREATE INDEX IF NOT EXISTS clinical_access_log_actor_idx
            ON clinical_access_log (actor, ts DESC);
        CREATE INDEX IF NOT EXISTS clinical_access_log_ts_idx
            ON clinical_access_log (ts);
        """
    )
    op.get_bind().exec_driver_sql(GUARD_SQL)


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "Rollback) — restore from a backup if an upgrade goes wrong."
    )
