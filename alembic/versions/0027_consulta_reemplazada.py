"""One current consultation per interview: a reissue explicitly voids the old one.

Reopening a confirmed consultation used to show an empty form, and a second
confirm silently created a second valid prescription for the same visit
(problems.md №1, 2026-10-23). `superseded_at`/`superseded_by` mark a
consultation replaced by an explicit «Reemplazar»; its printable links keep
showing exactly what was issued, marked «ANULADA — REEMPLAZADA» with a link to
the new document (the product owner's hub, 2026-10-23: nothing is deleted or rewritten).
The partial unique index makes «one current consultation per interview» a
database rule.

Backfill: an installation that already holds several consultations for one
interview keeps the NEWEST as current (it is what the doctor issued last) and
MARKS the older ones superseded by it — it sets those two columns and touches
nothing else (no row removed, no text, token or link rewritten).
`scripts/fixes/report_consultas_duplicadas.py` lists, read-only, what this
backfill will mark before upgrading (production had 0 consultations on
2026-10-21).
"""
from alembic import op

revision = "0027_consulta_reemplazada"
down_revision = "0026_clinical_access_log"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE consultations ADD COLUMN IF NOT EXISTS superseded_at timestamptz")
    op.execute("ALTER TABLE consultations ADD COLUMN IF NOT EXISTS superseded_by bigint")
    op.execute(
        "UPDATE consultations c SET superseded_at = n.created, superseded_by = n.id "
        "FROM (SELECT DISTINCT ON (interview_id) interview_id, id, created "
        "      FROM consultations ORDER BY interview_id, id DESC) n "
        "WHERE c.interview_id = n.interview_id AND c.id < n.id "
        "AND c.superseded_at IS NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS consultations_vigente_uniq "
               "ON consultations (interview_id) WHERE superseded_at IS NULL")


def downgrade():
    raise NotImplementedError("consultation replacement is forward-only; restore a verified backup")
