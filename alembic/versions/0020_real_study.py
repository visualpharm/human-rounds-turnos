"""Anonymous consented usability study, forward-only."""
from alembic import op

revision = "0020_real_study"
down_revision = "0019_ai_register_changes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS study_sessions (
    id bigserial PRIMARY KEY, token_hash text NOT NULL UNIQUE,
    role text NOT NULL CHECK (role IN ('patient','doctor','admin')),
    locale text NOT NULL CHECK (locale IN ('es','en')),
    consent_at timestamptz NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    user_agent_class text NOT NULL CHECK (user_agent_class IN ('mobile','tablet','desktop'))
);
CREATE INDEX IF NOT EXISTS study_sessions_retention ON study_sessions(started_at);
CREATE TABLE IF NOT EXISTS study_task_results (
    id bigserial PRIMARY KEY, session_id bigint NOT NULL,
    task_id text NOT NULL, outcome text NOT NULL CHECK (outcome IN ('ok','doubt','stuck','skipped')),
    ease integer CHECK (ease BETWEEN 1 AND 5), comment text NOT NULL DEFAULT '',
    duration_ms integer NOT NULL CHECK (duration_ms BETWEEN 0 AND 86400000),
    started_at timestamptz NOT NULL, ended_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id,task_id)
);
CREATE TABLE IF NOT EXISTS study_events (
    id bigserial PRIMARY KEY, session_id bigint NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    task_id text NOT NULL, t integer NOT NULL CHECK (t BETWEEN 0 AND 86400000),
    kind text NOT NULL CHECK (kind IN ('click','nav','dead_click','scroll_stop','visibility')),
    target jsonb NOT NULL DEFAULT '{}'::jsonb, path text NOT NULL
);
CREATE INDEX IF NOT EXISTS study_events_session ON study_events(session_id,task_id);
CREATE TABLE IF NOT EXISTS study_findings (
    id bigserial PRIMARY KEY, title text NOT NULL, detail text NOT NULL DEFAULT '',
    role text NOT NULL CHECK (role IN ('patient','doctor','admin')),
    task_id text NOT NULL, kind text,
    severity text NOT NULL CHECK (severity IN ('low','medium','high')),
    source text NOT NULL CHECK (source IN ('auto','manual')),
    evidence_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'found' CHECK (status IN ('found','in_progress','fixed','not_done','dismissed')),
    fix_note text NOT NULL DEFAULT '', commit text NOT NULL DEFAULT '', live_url text NOT NULL DEFAULT '',
    decided_by text, decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS study_findings_auto ON study_findings(task_id,kind) WHERE source='auto';
""")


def downgrade():
    raise NotImplementedError("Study retention is controlled by housekeeping, not downgrade")
