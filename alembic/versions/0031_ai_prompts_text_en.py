"""`texto_en` -> `text_en` in the AI prompt tables (problems.md №14, 2026-10-24).

`001_english_schema` renamed `texto_es` to `text_es` but left `texto_en` behind,
so the prompt tables mixed two languages in their column names. This revision
finishes the rename for `ai_prompts` and `ai_prompts_history`. It is a pure
`RENAME COLUMN`: no row is rewritten and the API keeps answering `texto_en`
(`core/db_wire._WIRE_BACK` maps the column back to the wire name the panel reads).

Safe on every boot path: a table that is missing, or that already carries
`text_en` (a fresh install built from `_SCHEMA_SQL`), is skipped, so the
revision can never fail the start.
"""
from alembic import op

revision = "0031_ai_prompts_text_en"
down_revision = "0030_date_time_columns"
branch_labels = None
depends_on = None

_TABLES = ("ai_prompts", "ai_prompts_history")


def _rename(old, new):
    for table in _TABLES:
        op.execute(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM information_schema.columns "
            f"WHERE table_schema = current_schema() AND table_name = '{table}' "
            f"AND column_name = '{old}') "
            "AND NOT EXISTS (SELECT 1 FROM information_schema.columns "
            f"WHERE table_schema = current_schema() AND table_name = '{table}' "
            f"AND column_name = '{new}') "
            f"THEN ALTER TABLE {table} RENAME COLUMN {old} TO {new}; "
            "END IF; END $$")


def upgrade():
    _rename("texto_en", "text_en")


def downgrade():
    _rename("text_en", "texto_en")
