"""core/schema_model.py — the schema of a fresh install, described in SQLAlchemy Core.

One `MetaData` with one `Table` per table of the application (every table a
fresh install has at Alembic head), written as plain Core: columns with their
types, NOT NULL, server defaults, primary keys, UNIQUE and CHECK constraints
and indexes. No ORM classes, no mapped objects: queries are built from these
tables (`schema_model.booking_requests.c.status`) and run through
`core/sqlcore.py`, the schema is changed by a revision under `alembic/versions/`.

What this file is for
  - `alembic/env.py` points `target_metadata` at `metadata`, so
    `alembic revision --autogenerate` proposes the revision for a change made here.
    The proposal is a HINT: a revision is still read and edited by hand, and its
    SQL is what runs.
  - `tests/test_schema_model.py` builds a database from `metadata` alone and
    compares it with `alembic/schema-head.txt` (the catalog snapshot of a real
    fresh boot). A table, column, type, default, constraint or index that differs
    in either direction is a red test with the line that differs.
  - New query code takes its table and column names from here instead of
    repeating them as SQL text.

What this file does NOT describe
  - triggers, functions and `$$` bodies: a revision owns them and the catalog
    snapshot `alembic/schema-head.txt` (`test_alembic_revisions.TestSchemaSnapshot`)
    guards them;
  - the Alembic table, the retired `schema_migrations`, the queue library's
    `procrastinate_*` objects and the optional `pg_trgm` search indexes: outside
    this model AND outside the snapshot (the queue library, the retired runner and
    the best-effort `db_schema._apply_trigrams` own them; `alembic/env.py` tells
    autogenerate to leave them alone).

A schema change is TWO edits in the same commit: the matching change in this
file and a forward revision (`alembic/versions/00NN_*.py`), then
`python3 scripts/schema_snapshot.py --write`. The test fails when only one of
the two happened.

Conventions
  - A table is bound to a module attribute of the same name.
  - Every constraint and index carries the name the database has (the names a
    fresh install gets from the frozen baseline and the revisions): nothing is
    left to a naming convention.
  - `server_default=text(...)` holds the default as the catalog prints it.
  - `autoincrement=True` marks the serial primary key; its sequence is the
    implicit `<table>_<column>_seq`.
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REGCLASS

metadata = MetaData()

# Tables that exist in the database but belong to another mechanism (Alembic's version
# table). They are NOT part of `metadata`: `create_all`, autogenerate and the schema
# snapshot never see them, but a query can still be written against them.
external = MetaData()
alembic_version = Table(
    "alembic_version",
    external,
    Column("version_num", String(32), primary_key=True),
)
# The two catalog views the maintenance scripts read, as far as they read them.
information_schema_columns = Table(
    "columns",
    external,
    Column("table_schema", Text),
    Column("table_name", Text),
    Column("column_name", Text),
    Column("data_type", Text),
    schema="information_schema",
)
# The queue library's own tables (`procrastinate_*`): created by its bundled schema, not by the
# model or Alembic. Described only as far as the queue plumbing reads them.
procrastinate_jobs = Table(
    "procrastinate_jobs",
    external,
    Column("id", BigInteger),
    Column("task_name", Text),
    Column("status", Text),
    Column("scheduled_at", DateTime(timezone=True)),
)
procrastinate_events = Table(
    "procrastinate_events",
    external,
    Column("job_id", BigInteger),
    Column("type", Text),
    Column("at", DateTime(timezone=True)),
)
pg_roles = Table("pg_roles", external, Column("rolname", Text))
pg_constraint = Table(
    "pg_constraint",
    external,
    Column("conname", Text),
    Column("contype", Text),
    Column("convalidated", Boolean),
    Column("conrelid", REGCLASS),
)

# The installation stores every instant as `timestamptz`.
TIMESTAMPTZ = DateTime(timezone=True)


def last_ten_digits(column):
    """The expression the two phone-lookup indexes are built on: the last ten
    digits of a free-text phone/WhatsApp key. A query that must use those indexes
    builds the same expression with this function."""
    return func.right(func.regexp_replace(column, r"\D", "", "g"), 10)


admin_account_security = Table(
    "admin_account_security",
    metadata,
    Column("username", Text),
    Column("owner_pass_hash", Text),
    Column("owner_totp_secret", Text),
    Column("owner_totp_set", Boolean, nullable=False, server_default=text("false")),
    Column("totp_deadline", TIMESTAMPTZ),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("owner_email", Text),
    Column("owner_last_access", TIMESTAMPTZ),
    PrimaryKeyConstraint("username", name="admin_account_security_pkey"),
)

admin_preferences = Table(
    "admin_preferences",
    metadata,
    Column("username", Text),
    Column("key", Text),
    Column("value", JSONB, nullable=False),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("username", "key", name="admin_preferences_pkey"),
)

admin_reset_tokens = Table(
    "admin_reset_tokens",
    metadata,
    Column("token_hash", Text),
    Column("username", Text, nullable=False),
    Column("expires", TIMESTAMPTZ, nullable=False),
    Column("used", Boolean, nullable=False, server_default=text("false")),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("token_hash", name="admin_reset_tokens_pkey"),
)

admin_session_revocations = Table(
    "admin_session_revocations",
    metadata,
    Column("sid", Text),
    Column("username", Text, nullable=False),
    Column("revoked_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("sid", name="admin_session_revocations_pkey"),
)

ai_costs = Table(
    "ai_costs",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("ts", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("operation", Text, nullable=False),
    Column("cost_usd", Numeric(10, 6), nullable=False, server_default=text("0")),
    Column("ip", Text),
    Column("user_dni", Text),
    Column("model", Text),
    Column("recognized", Boolean),
    PrimaryKeyConstraint("id", name="ai_costs_pkey"),
)
Index("ai_costs_ip_idx", ai_costs.c.ip, ai_costs.c.ts.desc())
Index("ai_costs_ts_idx", ai_costs.c.ts.desc())

ai_prompts = Table(
    "ai_prompts",
    metadata,
    Column("key", Text),
    Column("text_en", Text, nullable=False),
    Column("text_es", Text),
    Column("edited", Boolean, nullable=False, server_default=text("false")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated_by", Text),
    PrimaryKeyConstraint("key", name="ai_prompts_pkey"),
)

ai_prompts_history = Table(
    "ai_prompts_history",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("key", Text, nullable=False),
    Column("text_en", Text, nullable=False),
    Column("text_es", Text),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated_by", Text),
    PrimaryKeyConstraint("id", name="ai_prompts_history_pkey"),
)
Index("ai_prompts_history_idx", ai_prompts_history.c.key, ai_prompts_history.c.id.desc())

ai_register_changes = Table(
    "ai_register_changes",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("function_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("changed_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("substantial", Boolean, nullable=False, server_default=text("false")),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", Text, nullable=False),
    Column("notification_reference", Text),
    Column("notified_on", Date),
    Column("notified_by", Text),
    Column("notified_at", TIMESTAMPTZ),
    PrimaryKeyConstraint("id", name="ai_register_changes_pkey"),
    CheckConstraint(
        "status = ANY (ARRAY['recorded'::text, 'pending'::text, 'notified'::text])",
        name="ai_register_changes_status_check",
    ),
)

analytics_events = Table(
    "analytics_events",
    metadata,
    Column("id", Text, nullable=False),
    Column("ts", TIMESTAMPTZ, nullable=False),
    Column("type", Text, nullable=False),
    Column("route", Text),
    Column("rid", Text),
    Column("ref", Text),
    Column("lang", Text),
    Column("device", Text),
    Column("jid", Text),
    Column("pl", Text),
    Column("q", Text),
    Column("n", Integer),
    Column("origin", Text),
    Column("step", Text),
    Column("service", Text),
    PrimaryKeyConstraint("id", name="analytics_events_pkey"),
)
Index("analytics_events_ts_idx", analytics_events.c.ts)
Index("analytics_events_type_ts_idx", analytics_events.c.type, analytics_events.c.ts)

analytics_schedule = Table(
    "analytics_schedule",
    metadata,
    Column("snapshot", Date, nullable=False),
    Column("day", Date, nullable=False),
    Column("doctor", Text, nullable=False),
    Column("center", Text, nullable=False, server_default=text("''::text")),
    Column("service", Text, nullable=False, server_default=text("''::text")),
    Column("hours", Numeric(6, 2), nullable=False, server_default=text("0")),
    Column("slots_total", Integer, nullable=False, server_default=text("0")),
    Column("slots_web", Integer, nullable=False, server_default=text("0")),
    Column("taken", Integer, nullable=False, server_default=text("0")),
    Column("free", Integer, nullable=False, server_default=text("0")),
    PrimaryKeyConstraint("snapshot", "day", "doctor", "center", "service", name="analytics_schedule_pkey"),
)

analytics_keys = Table(
    "analytics_keys",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("label", Text, nullable=False),
    Column("key_hash", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("revoked_at", TIMESTAMPTZ),
    Column("last_used_at", TIMESTAMPTZ),
    PrimaryKeyConstraint("id", name="analytics_keys_pkey"),
    UniqueConstraint("key_hash", name="analytics_keys_hash_key"),
)

alert_claims = Table(
    "alert_claims",
    metadata,
    Column("alert_key", Text),
    Column("day", Date),
    Column("claimed_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("alert_key", "day", name="alert_claims_pkey"),
)

appointment_events = Table(
    "appointment_events",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("request_id", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("actor", Text),
    Column("status_from", Text),
    Column("status_to", Text),
    Column("appointment_from", TIMESTAMPTZ),
    Column("appointment_to", TIMESTAMPTZ),
    Column("attendance_from", Text),
    Column("attendance_to", Text),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="appointment_events_pkey"),
    ForeignKeyConstraint(
        ["request_id"],
        ["booking_requests.id"],
        name="fk_appointment_events_request",
        ondelete="RESTRICT",
    ),
)
Index("appointment_events_request_idx", appointment_events.c.request_id, appointment_events.c.id)

appointment_offers = Table(
    "appointment_offers",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("slot_at", TIMESTAMPTZ),
    Column("service_id", Text),
    Column("center_slug", Text),
    Column("waitlist_id", Integer),
    Column("token", Text),
    Column("sent_at", TIMESTAMPTZ),
    Column("expires_at", TIMESTAMPTZ),
    Column("status", Text, server_default=text("'ofrecida'::text")),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="appointment_offers_pkey"),
    UniqueConstraint("token", name="appointment_offers_token_key"),
    ForeignKeyConstraint(
        ["waitlist_id"],
        ["waitlist.id"],
        name="fk_appointment_offers_waitlist",
        ondelete="RESTRICT",
    ),
)
Index(
    "appointment_offers_current_idx",
    appointment_offers.c.status,
    appointment_offers.c.expires_at,
    postgresql_where=text("(status = 'ofrecida'::text)"),
)
Index(
    "appointment_offers_live_slot_uniq",
    appointment_offers.c.service_id,
    appointment_offers.c.center_slug,
    appointment_offers.c.slot_at,
    unique=True,
    postgresql_where=text("(status = 'ofrecida'::text)"),
)
Index(
    "appointment_offers_slot_idx",
    appointment_offers.c.slot_at,
    appointment_offers.c.service_id,
    appointment_offers.c.center_slug,
)
Index("appointment_offers_waitlist_idx", appointment_offers.c.waitlist_id)

attendance_events = Table(
    "attendance_events",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("request_id", Integer, nullable=False),
    Column("dni", Text, nullable=False),
    Column("attendance_from", Text),
    Column("attendance_to", Text, nullable=False),
    Column("operator", Text, nullable=False),
    Column("source", Text, nullable=False, server_default=text("'sana'::text")),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="attendance_events_pkey"),
    CheckConstraint(
        "attendance_from = ANY (ARRAY['presente'::text, 'ausente'::text])",
        name="attendance_events_attendance_from_check",
    ),
    CheckConstraint(
        "attendance_to = ANY (ARRAY['presente'::text, 'ausente'::text])",
        name="attendance_events_attendance_to_check",
    ),
    CheckConstraint(
        "source = ANY (ARRAY['sana'::text, 'hsi'::text])",
        name="attendance_events_source_check",
    ),
    ForeignKeyConstraint(
        ["request_id"],
        ["booking_requests.id"],
        name="fk_attendance_events_request",
        ondelete="RESTRICT",
    ),
)
Index("attendance_events_dni_idx", attendance_events.c.dni, attendance_events.c.created.desc())
Index(
    "attendance_events_request_idx",
    attendance_events.c.request_id,
    attendance_events.c.created.desc(),
)

backup_journal = Table(
    "backup_journal",
    metadata,
    Column("path", Text),
    Column("sha256", Text, nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("mtime_ns", BigInteger, nullable=False),
    Column("remote_name", Text, nullable=False),
    Column("origin", Text, nullable=False, server_default=text("'upload'::text")),
    Column("uploaded_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("remote_file_id", Text),
    PrimaryKeyConstraint("path", name="backup_journal_pkey"),
)

backup_purge_state = Table(
    "backup_purge_state",
    metadata,
    Column("key", Text),
    Column("last_pass_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("last_success_at", TIMESTAMPTZ),
    Column("closed", Integer, nullable=False, server_default=text("0")),
    Column("denied", Integer, nullable=False, server_default=text("0")),
    Column("failed", Integer, nullable=False, server_default=text("0")),
    PrimaryKeyConstraint("key", name="backup_purge_state_pkey"),
)

backup_runs = Table(
    "backup_runs",
    metadata,
    Column("id", Text),
    Column("started_at", TIMESTAMPTZ, nullable=False),
    Column("finished_at", TIMESTAMPTZ),
    Column("status", Text, nullable=False),
    Column("archive_key", Text),
    Column("size", BigInteger, nullable=False, server_default=text("0")),
    Column("sha256", Text),
    Column("uploaded", Boolean, nullable=False, server_default=text("false")),
    Column("integrity_verified", Boolean, nullable=False, server_default=text("false")),
    Column("restore_tested", Boolean, nullable=False, server_default=text("false")),
    Column("error", Text),
    Column("evidence", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    PrimaryKeyConstraint("id", name="backup_runs_pkey"),
)

backup_version_tombstones = Table(
    "backup_version_tombstones",
    metadata,
    Column("file_id", Text),
    Column("remote_name", Text, nullable=False),
    Column("deleted_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("file_id", name="backup_version_tombstones_pkey"),
)

backup_versions = Table(
    "backup_versions",
    metadata,
    Column("file_id", Text),
    Column("remote_name", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("sha1", Text, nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("journal_path", Text, nullable=False),
    Column("accepted_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("file_id", name="backup_versions_pkey"),
)
Index(
    "backup_versions_path_idx",
    backup_versions.c.journal_path,
    backup_versions.c.accepted_at.desc(),
)

booking_requests = Table(
    "booking_requests",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("dni", Text),
    Column("first_name", Text),
    Column("last_name", Text),
    Column("birth_date", Date),
    Column("sex", Text),
    Column("contact", Text),
    Column("service_id", Text),
    Column("center_slug", Text),
    Column("status", Text, server_default=text("'pendiente'::text")),
    Column("referral_path", Text),
    Column("referral_mime", Text),
    Column("ai_json", JSONB),
    Column("rejection_reason", Text),
    Column("appointment_at", TIMESTAMPTZ),
    Column("hsi_patient_id", Text),
    Column("hsi_appointment_id", Text),
    Column("hsi_status", Text),
    Column("attendance", Text),
    Column("attendance_updated_at", TIMESTAMPTZ),
    Column("attendance_updated_by", Text),
    Column("overbooking", Boolean, nullable=False, server_default=text("false")),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    Column("time_preference", Text),
    Column("requester_dni", Text),
    Column("assigned_doctor", Text),
    Column("assigned_doctor_id", Text),
    Column("reminder_at", TIMESTAMPTZ),
    Column("interview_reminder_at", TIMESTAMPTZ),
    Column("resolved_by", Text),
    Column("resolved_at", TIMESTAMPTZ),
    Column("confirmation", Text),
    Column("confirmation_at", TIMESTAMPTZ),
    Column("confirmation_channel", Text),
    Column("confirm_token", Text),
    Column("cancelled_at", TIMESTAMPTZ),
    Column("slot_doctor_id", Text),
    Column("idem_key", Text),
    Column("patient_note", Text),
    Column("requested_at", TIMESTAMPTZ),
    Column("case_chat", JSONB),
    Column("reminder_skipped", Boolean, nullable=False, server_default=text("false")),
    PrimaryKeyConstraint("id", name="booking_requests_pkey"),
    CheckConstraint(
        "attendance = ANY (ARRAY['presente'::text, 'ausente'::text])",
        name="booking_requests_attendance_check",
    ),
    CheckConstraint(
        "confirmation = ANY (ARRAY['si'::text, 'no'::text])",
        name="booking_requests_confirmation_check",
    ),
)
Index("booking_requests_confirm_token_idx", booking_requests.c.confirm_token, unique=True)
Index("booking_requests_dni_idx", booking_requests.c.dni)
Index(
    "booking_requests_doctor_appointment_idx",
    booking_requests.c.assigned_doctor_id,
    booking_requests.c.appointment_at,
    postgresql_where=text("((assigned_doctor_id IS NOT NULL) AND (appointment_at IS NOT NULL))"),
)
Index(
    "booking_requests_doctor_slot_uniq",
    booking_requests.c.assigned_doctor_id,
    booking_requests.c.appointment_at,
    unique=True,
    postgresql_where=text("((status = 'aprobada'::text) AND (NOT overbooking) AND (assigned_doctor_id IS NOT NULL) AND (appointment_at IS NOT NULL))"),
)
Index(
    "booking_requests_hsi_appt_idx",
    booking_requests.c.hsi_appointment_id,
    postgresql_where=text("(hsi_appointment_id IS NOT NULL)"),
)
Index(
    "booking_requests_idem_key_uniq",
    booking_requests.c.idem_key,
    unique=True,
    postgresql_where=text("(idem_key IS NOT NULL)"),
)
Index("booking_requests_requester_idx", booking_requests.c.requester_dni)
Index(
    "booking_requests_status_appointment_idx",
    booking_requests.c.status,
    booking_requests.c.appointment_at,
)
Index(
    "booking_requests_status_created_idx",
    booking_requests.c.status,
    booking_requests.c.created.desc(),
)

catalog = Table(
    "catalog",
    metadata,
    Column("type", Text),
    Column("id", Text),
    Column("doc", JSONB, nullable=False),
    Column("deleted", Boolean, server_default=text("false")),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    Column("updated", TIMESTAMPTZ, server_default=text("now()")),
    PrimaryKeyConstraint("type", "id", name="catalog_pkey"),
)

chat_compactions = Table(
    "chat_compactions",
    metadata,
    Column("patient_key", Text),
    Column("summary", Text, nullable=False),
    Column("message_count", Integer, nullable=False, server_default=text("0")),
    Column("covers_until", TIMESTAMPTZ, nullable=False),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("patient_key", name="chat_compactions_pkey"),
)

chat_handoffs = Table(
    "chat_handoffs",
    metadata,
    Column("code", Text),
    Column("web_key", Text, nullable=False),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("expires", TIMESTAMPTZ, nullable=False),
    Column("used", Boolean, nullable=False, server_default=text("false")),
    Column("used_phone", Text),
    Column("used_at", TIMESTAMPTZ),
    PrimaryKeyConstraint("code", name="chat_handoffs_pkey"),
)
Index("chat_handoffs_web_key_idx", chat_handoffs.c.web_key)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("channel", Text, nullable=False),
    Column("key", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("content", JSONB, nullable=False),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="chat_messages_pkey"),
)
Index(
    "chat_messages_conv_idx",
    chat_messages.c.channel,
    chat_messages.c.key,
    chat_messages.c.created,
)
Index(
    "chat_messages_whatsapp_tel_idx",
    chat_messages.c.channel,
    last_ten_digits(chat_messages.c.key),
    postgresql_where=text("(channel = 'whatsapp'::text)"),
)

chat_summaries = Table(
    "chat_summaries",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("patient_key", Text, nullable=False),
    Column("channel", Text, nullable=False, server_default=text("'merged'::text")),
    Column("summary", Text, nullable=False),
    Column("message_count", Integer, nullable=False, server_default=text("0")),
    Column("covers_until", TIMESTAMPTZ, nullable=False),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="chat_summaries_pkey"),
)
Index(
    "chat_summaries_patient_idx",
    chat_summaries.c.patient_key,
    chat_summaries.c.covers_until.desc(),
)

clinical_access_log = Table(
    "clinical_access_log",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("ts", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("actor", Text, nullable=False),
    Column("actor_role", Text, nullable=False, server_default=text("''::text")),
    Column("action", Text, nullable=False),
    Column("dni_hash", Text, nullable=False),
    Column("target_id", Text),
    Column("allowed", Boolean, nullable=False, server_default=text("true")),
    PrimaryKeyConstraint("id", name="clinical_access_log_pkey"),
)
Index(
    "clinical_access_log_actor_idx",
    clinical_access_log.c.actor,
    clinical_access_log.c.ts.desc(),
)
Index(
    "clinical_access_log_dni_idx",
    clinical_access_log.c.dni_hash,
    clinical_access_log.c.ts.desc(),
)
Index("clinical_access_log_ts_idx", clinical_access_log.c.ts)

clinical_history_records = Table(
    "clinical_history_records",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("dni", Text, nullable=False),
    Column("source_system", Text, nullable=False),
    Column("record_hash", Text, nullable=False),
    Column("occurred_on", Date),
    Column("record", JSONB, nullable=False),
    Column("imported_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="clinical_history_records_pkey"),
    UniqueConstraint("dni", "source_system", "record_hash", name="clinical_history_records_dni_source_system_record_hash_key"),
)
Index(
    "clinical_history_records_dni_date_idx",
    clinical_history_records.c.dni,
    clinical_history_records.c.occurred_on.desc(),
)

consultations = Table(
    "consultations",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("interview_id", BigInteger, nullable=False),
    Column("doctor_id", Text, nullable=False),
    Column("diagnoses", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("exams", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("meds", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("notes", Text),
    Column("receta_json", JSONB),
    Column("orden_json", JSONB),
    Column("receta_token", Text),
    Column("orden_token", Text),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("created_by", Text),
    Column("superseded_at", TIMESTAMPTZ),
    Column("superseded_by", BigInteger),
    PrimaryKeyConstraint("id", name="consultations_pkey"),
    UniqueConstraint("orden_token", name="consultations_orden_token_key"),
    UniqueConstraint("receta_token", name="consultations_receta_token_key"),
    ForeignKeyConstraint(
        ["interview_id"],
        ["interviews.id"],
        name="fk_consultations_interview",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["superseded_by"],
        ["consultations.id"],
        name="fk_consultations_superseded_by",
        ondelete="RESTRICT",
    ),
)
Index("consultations_interview_idx", consultations.c.interview_id)
Index("consultations_superseded_by_idx", consultations.c.superseded_by)
Index(
    "consultations_vigente_uniq",
    consultations.c.interview_id,
    unique=True,
    postgresql_where=text("(superseded_at IS NULL)"),
)

data_subject_request_events = Table(
    "data_subject_request_events",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("request_id", BigInteger, nullable=False),
    Column("ts", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("actor", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("detail", Text, nullable=False, server_default=text("''::text")),
    PrimaryKeyConstraint("id", name="data_subject_request_events_pkey"),
)
Index(
    "data_subject_request_events_request_idx",
    data_subject_request_events.c.request_id,
    data_subject_request_events.c.ts,
)

data_subject_requests = Table(
    "data_subject_requests",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("kind", Text, nullable=False),
    Column("subject_hash", Text, nullable=False),
    Column("received_on", Date, nullable=False),
    Column("due_on", Date, nullable=False),
    Column("channel", Text, nullable=False, server_default=text("''::text")),
    Column("identity_check", Text, nullable=False, server_default=text("''::text")),
    Column("status", Text, nullable=False, server_default=text("'received'::text")),
    Column("outcome", Text, nullable=False, server_default=text("''::text")),
    Column("registered_by", Text, nullable=False),
    Column("decided_by", Text),
    Column("answered_on", Date),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="data_subject_requests_pkey"),
    CheckConstraint(
        "kind = ANY (ARRAY['access'::text, 'rectification'::text, 'erasure'::text])",
        name="data_subject_requests_kind_check",
    ),
    CheckConstraint(
        "status = ANY (ARRAY['received'::text, 'under_review'::text, 'answered'::text, 'refused'::text])",
        name="data_subject_requests_status_check",
    ),
)
Index(
    "data_subject_requests_open_idx",
    data_subject_requests.c.status,
    data_subject_requests.c.due_on,
)
Index(
    "data_subject_requests_subject_idx",
    data_subject_requests.c.subject_hash,
    data_subject_requests.c.received_on.desc(),
)

date_time_conversion_leftovers = Table(
    "date_time_conversion_leftovers",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("revision", Text, nullable=False),
    Column("source_table", Text, nullable=False),
    Column("row_id", BigInteger, nullable=False),
    Column("reason", Text, nullable=False),
    Column("original", JSONB, nullable=False),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="date_time_conversion_leftovers_pkey"),
)

day_queue_events = Table(
    "day_queue_events",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("appointment_day", Date, nullable=False),
    Column("center_slug", Text),
    Column("assigned_doctor_id", Text),
    Column("assigned_doctor", Text),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="day_queue_events_pkey"),
)
Index("day_queue_events_replay_idx", day_queue_events.c.id)

demo_refresh_completion = Table(
    "demo_refresh_completion",
    metadata,
    Column("job", Text),
    Column("attempted_for", Date, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("last_attempt_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("success_for", Date),
    Column("last_success_at", TIMESTAMPTZ),
    PrimaryKeyConstraint("job", name="demo_refresh_completion_pkey"),
    CheckConstraint("job = 'demo_refresh'::text", name="demo_refresh_completion_job_check"),
    CheckConstraint(
        "outcome = ANY (ARRAY['success'::text, 'partial'::text, 'failed'::text])",
        name="demo_refresh_completion_outcome_check",
    ),
)

dependents = Table(
    "dependents",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("adult_dni", Text, nullable=False),
    Column("dni", Text, nullable=False),
    Column("first_name", Text, nullable=False),
    Column("last_name", Text, nullable=False),
    Column("birth_date", Date, nullable=False),
    Column("sex", Text, nullable=False),
    Column("relationship", Text, nullable=False),
    Column("affidavit_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    Column("updated", TIMESTAMPTZ, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="dependents_pkey"),
    UniqueConstraint("adult_dni", "dni", name="dependents_adult_dni_dni_key"),
    ForeignKeyConstraint(
        ["adult_dni"],
        ["users.dni"],
        name="fk_dependents_adult_dni",
        ondelete="RESTRICT",
        onupdate="CASCADE",
    ),
)
Index("dependents_dni_idx", dependents.c.dni)

hsi_appointments = Table(
    "hsi_appointments",
    metadata,
    Column("hsi_appointment_id", Text),
    Column("hsi_diary_id", Text),
    Column("hsi_patient_id", Text),
    Column("payload", JSONB, nullable=False),
    Column("observed_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("hsi_appointment_id", name="hsi_appointments_pkey"),
)
Index("hsi_appointments_diary_idx", hsi_appointments.c.hsi_diary_id)
Index("hsi_appointments_patient_idx", hsi_appointments.c.hsi_patient_id)

hsi_checkpoints = Table(
    "hsi_checkpoints",
    metadata,
    Column("entity_type", Text),
    Column("entity_id", Text),
    Column("remote", JSONB, nullable=False),
    Column("observed_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("entity_type", "entity_id", name="hsi_checkpoints_pkey"),
)

hsi_doctor_schedules = Table(
    "hsi_doctor_schedules",
    metadata,
    Column("hsi_diary_id", Text),
    Column("hsi_professional_id", Text),
    Column("payload", JSONB, nullable=False),
    Column("observed_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("hsi_diary_id", name="hsi_doctor_schedules_pkey"),
)

hsi_inbox = Table(
    "hsi_inbox",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("source_key", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("observed_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("reconciled_at", TIMESTAMPTZ),
    Column("state", Text, nullable=False, server_default=text("'pending'::text")),
    Column("conflict", JSONB),
    PrimaryKeyConstraint("id", name="hsi_inbox_pkey"),
    UniqueConstraint("source_key", name="hsi_inbox_source_key_key"),
    CheckConstraint(
        "state = ANY (ARRAY['pending'::text, 'confirmed'::text, 'conflict'::text, 'error'::text])",
        name="hsi_inbox_state_check",
    ),
)
Index("hsi_inbox_pending_idx", hsi_inbox.c.state, hsi_inbox.c.observed_at)

hsi_outbox = Table(
    "hsi_outbox",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("operation", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("state", Text, nullable=False, server_default=text("'pending'::text")),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("next_attempt_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("lease_expires_at", TIMESTAMPTZ),
    Column("last_error", Text),
    Column("hsi_result", JSONB),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="hsi_outbox_pkey"),
    UniqueConstraint("idempotency_key", name="hsi_outbox_idempotency_key_key"),
    CheckConstraint(
        "state = ANY (ARRAY['pending'::text, 'sending'::text, 'confirmed'::text, 'error'::text, 'conflict'::text])",
        name="hsi_outbox_state_check",
    ),
)
Index("hsi_outbox_ready_idx", hsi_outbox.c.state, hsi_outbox.c.next_attempt_at)

hsi_patients = Table(
    "hsi_patients",
    metadata,
    Column("hsi_patient_id", Text),
    Column("dni", Text),
    Column("payload", JSONB, nullable=False),
    Column("observed_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("hsi_patient_id", name="hsi_patients_pkey"),
)
Index("hsi_patients_dni_idx", hsi_patients.c.dni)

hsi_sync_conflicts = Table(
    "hsi_sync_conflicts",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("direction", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("local_payload", JSONB),
    Column("remote_payload", JSONB),
    Column("resolved_at", TIMESTAMPTZ),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("resolution", JSONB),
    Column("resolved_by", Text),
    PrimaryKeyConstraint("id", name="hsi_sync_conflicts_pkey"),
    CheckConstraint(
        "direction = ANY (ARRAY['inbound'::text, 'outbound'::text])",
        name="hsi_sync_conflicts_direction_check",
    ),
)
Index(
    "hsi_sync_conflicts_open_idx",
    hsi_sync_conflicts.c.resolved_at,
    hsi_sync_conflicts.c.created.desc(),
)

interviews = Table(
    "interviews",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("doctor_id", Text),
    Column("request_id", Integer),
    Column("entry_mode", Text, nullable=False, server_default=text("'qr'::text")),
    Column("entered_name", Text),
    Column("entered_surname", Text),
    Column("matched", Boolean, nullable=False, server_default=text("false")),
    Column("dni", Text),
    Column("session_key", Text, nullable=False),
    Column("patient_token", Text),
    Column("status", Text, nullable=False, server_default=text("'active'::text")),
    Column("triage", Text),
    Column("summary", Text),
    Column("brief", JSONB),
    Column("differential", JSONB),
    Column("suggested_exams", JSONB),
    Column("red_flags", JSONB),
    Column("ai_model", Text),
    Column("analyzed_at", TIMESTAMPTZ),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("detached_request_id", Integer),
    Column("alert_seen_at", TIMESTAMPTZ),
    Column("alert_seen_by", Text),
    Column("reopened_at", TIMESTAMPTZ),
    PrimaryKeyConstraint("id", name="interviews_pkey"),
    UniqueConstraint("patient_token", name="interviews_patient_token_key"),
    UniqueConstraint("session_key", name="interviews_session_key_key"),
    ForeignKeyConstraint(
        ["request_id"],
        ["booking_requests.id"],
        name="fk_interviews_request",
        ondelete="RESTRICT",
    ),
)
Index("interviews_doctor_day_idx", interviews.c.doctor_id, interviews.c.created)
Index("interviews_patient_token_idx", interviews.c.patient_token, unique=True)
Index(
    "interviews_red_alert_idx",
    interviews.c.doctor_id,
    postgresql_where=text("((triage = 'ROJO'::text) AND (alert_seen_at IS NULL))"),
)
Index("interviews_request_idx", interviews.c.request_id)
Index(
    "interviews_request_uniq",
    interviews.c.request_id,
    unique=True,
    postgresql_where=text("(request_id IS NOT NULL)"),
)

lab_report_analysis = Table(
    "lab_report_analysis",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("dni", Text, nullable=False),
    Column("report_hash", Text, nullable=False),
    Column("prompt_hash", Text, nullable=False),
    Column("fecha", Date),
    Column("sistema", Text),
    Column("tipo_estudio", Text),
    Column("resumen", Text),
    Column("tono", Text),
    Column("sensible", Boolean, nullable=False, server_default=text("false")),
    Column("valores", JSONB),
    Column("modelo", Text),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="lab_report_analysis_pkey"),
    UniqueConstraint("dni", "report_hash", name="lab_report_analysis_dni_report_hash_key"),
)
Index("lab_report_analysis_dni_idx", lab_report_analysis.c.dni)

launch_signal_counters = Table(
    "launch_signal_counters",
    metadata,
    Column("bucket", TIMESTAMPTZ),
    Column("code", Text),
    Column("n", BigInteger, nullable=False, server_default=text("0")),
    PrimaryKeyConstraint("bucket", "code", name="launch_signal_counters_pkey"),
)

legal_docs = Table(
    "legal_docs",
    metadata,
    Column("doc", Text),
    Column("body", Text, nullable=False),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated_by", Text),
    PrimaryKeyConstraint("doc", name="legal_docs_pkey"),
)

login_panel_events = Table(
    "login_panel_events",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("ts", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("sid", Text, nullable=False),
    Column("variant", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("tab", Text),
    Column("item", Text),
    Column("ms", Integer),
    PrimaryKeyConstraint("id", name="login_panel_events_pkey"),
)
Index("login_panel_events_sid_idx", login_panel_events.c.sid)
Index("login_panel_events_ts_idx", login_panel_events.c.ts.desc())

mcp_ai_budget = Table(
    "mcp_ai_budget",
    metadata,
    Column("day", Date),
    Column("uses", Integer, nullable=False, server_default=text("0")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("day", name="mcp_ai_budget_pkey"),
    CheckConstraint("uses >= 0", name="mcp_ai_budget_uses_check"),
)

operator_access = Table(
    "operator_access",
    metadata,
    Column("username", Text),
    Column("role_slug", Text),
    Column("granted", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("denied", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("username", name="operator_access_pkey"),
    ForeignKeyConstraint(
        ["role_slug"],
        ["roles.slug"],
        name="fk_operator_access_role",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["username"],
        ["operators.username"],
        name="fk_operator_access_username",
        ondelete="RESTRICT",
        onupdate="CASCADE",
    ),
)
Index("operator_access_role_idx", operator_access.c.role_slug)

operator_username_history = Table(
    "operator_username_history",
    metadata,
    Column("old_username", Text),
    Column("new_username", Text, nullable=False),
    Column("operator_id", Integer, nullable=False),
    Column("changed_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("old_username", name="operator_username_history_pkey"),
    ForeignKeyConstraint(
        ["operator_id"],
        ["operators.id"],
        name="fk_operator_username_history_operator",
        ondelete="RESTRICT",
    ),
)
Index("operator_username_history_operator_idx", operator_username_history.c.operator_id)

operators = Table(
    "operators",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("username", Text, nullable=False),
    Column("pass_hash", Text, nullable=False),
    Column("totp_secret", Text),
    Column("role", Text, nullable=False, server_default=text("'medico'::text")),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    Column("doctor_id", Text),
    Column("onboarding_completed_at", TIMESTAMPTZ),
    Column("feed_token", Text),
    Column("last_access", TIMESTAMPTZ),
    Column("email", Text),
    Column("invited_at", TIMESTAMPTZ),
    Column("cuit", Text),
    Column("portal_user_id", Text),
    Column("centers", ARRAY(Text)),
    # feed_token holds the SHA-256 hex of the doctor's calendar address token;
    # feed_token_hint its last 4 characters (alembic 0040).
    Column("feed_token_hint", Text),
    PrimaryKeyConstraint("id", name="operators_pkey"),
    UniqueConstraint("feed_token", name="operators_feed_token_key"),
    UniqueConstraint("username", name="operators_username_key"),
    CheckConstraint(
        "role = ANY (ARRAY['admin'::text, 'medico'::text, 'admisionista'::text, 'reportes'::text])",
        name="operators_role_check",
    ),
)
Index(
    "operators_cuit_uniq",
    operators.c.cuit,
    unique=True,
    postgresql_where=text("(cuit IS NOT NULL)"),
)
Index(
    "operators_doctor_id_uniq",
    operators.c.doctor_id,
    unique=True,
    postgresql_where=text("(doctor_id IS NOT NULL)"),
)
Index(
    "operators_portal_user_id_uniq",
    operators.c.portal_user_id,
    unique=True,
    postgresql_where=text("(portal_user_id IS NOT NULL)"),
)

otp_codes = Table(
    "otp_codes",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("contact", Text, nullable=False),
    Column("purpose", Text, nullable=False),
    Column("code_hash", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("used", Boolean, server_default=text("false")),
    Column("expires", TIMESTAMPTZ, nullable=False),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="otp_codes_pkey"),
)
Index(
    "otp_codes_contact_idx",
    otp_codes.c.contact,
    otp_codes.c.purpose,
    otp_codes.c.created.desc(),
)

otp_verified = Table(
    "otp_verified",
    metadata,
    Column("contact", Text),
    Column("purpose", Text),
    Column("verified_until", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("contact", "purpose", name="otp_verified_pkey"),
)

passkey_challenges = Table(
    "passkey_challenges",
    metadata,
    Column("challenge_digest", LargeBinary),
    Column("purpose", Text, nullable=False),
    Column("username", Text),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    Column("consumed_at", TIMESTAMPTZ),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("challenge_digest", name="passkey_challenges_pkey"),
    CheckConstraint(
        "purpose = ANY (ARRAY['registration'::text, 'login'::text])",
        name="passkey_challenges_purpose_check",
    ),
)
Index("passkey_challenges_expiry_idx", passkey_challenges.c.expires_at)

passkeys = Table(
    "passkeys",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("username", Text, nullable=False),
    Column("credential_id", Text, nullable=False),
    Column("public_key", JSONB, nullable=False),
    Column("sign_count", BigInteger, nullable=False, server_default=text("0")),
    Column("name", Text),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("last_used", TIMESTAMPTZ),
    PrimaryKeyConstraint("id", name="passkeys_pkey"),
    UniqueConstraint("credential_id", name="passkeys_credential_id_key"),
)
Index("passkeys_username_idx", passkeys.c.username)

patient_health_profile = Table(
    "patient_health_profile",
    metadata,
    Column("dni", Text),
    Column("datos", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("source_interview_id", BigInteger),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("dni", name="patient_health_profile_pkey"),
    ForeignKeyConstraint(
        ["source_interview_id"],
        ["interviews.id"],
        name="fk_patient_health_profile_interview",
        ondelete="RESTRICT",
    ),
)
Index("patient_health_profile_interview_idx", patient_health_profile.c.source_interview_id)

patient_lab_uploads = Table(
    "patient_lab_uploads",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("dni", Text, nullable=False),
    Column("uploaded_by_dni", Text, nullable=False),
    Column("filename", Text),
    Column("mime", Text),
    Column("archivo", LargeBinary),
    Column("texto", Text),
    Column("fecha", Date),
    Column("sin_lectura", Boolean, nullable=False, server_default=text("false")),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("deleted", TIMESTAMPTZ),
    PrimaryKeyConstraint("id", name="patient_lab_uploads_pkey"),
    ForeignKeyConstraint(
        ["uploaded_by_dni"],
        ["users.dni"],
        name="fk_patient_lab_uploads_uploaded_by",
        ondelete="RESTRICT",
        onupdate="CASCADE",
    ),
)
Index(
    "patient_lab_uploads_dni_idx",
    patient_lab_uploads.c.dni,
    patient_lab_uploads.c.created.desc(),
)
Index("patient_lab_uploads_uploaded_by_idx", patient_lab_uploads.c.uploaded_by_dni)

patient_summaries = Table(
    "patient_summaries",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("dni", Text, nullable=False),
    Column("doctor_id", Text, nullable=False),
    Column("especialidad", Text),
    Column("resumen", JSONB, nullable=False),
    Column("fingerprint", Text),
    Column("ai_model", Text),
    Column("generated_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="patient_summaries_pkey"),
    UniqueConstraint("dni", "doctor_id", name="patient_summaries_dni_doctor_id_key"),
)
Index("patient_summaries_dni_idx", patient_summaries.c.dni)

purged_files = Table(
    "purged_files",
    metadata,
    Column("path", Text),
    Column("reason", Text, nullable=False),
    Column("purged_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("remote_done", TIMESTAMPTZ),
    PrimaryKeyConstraint("path", name="purged_files_pkey"),
)

refeps_cache = Table(
    "refeps_cache",
    metadata,
    Column("license_number", Text),
    Column("items", JSONB),
    Column("ok", Boolean, server_default=text("true")),
    Column("checked", TIMESTAMPTZ, server_default=text("now()")),
    PrimaryKeyConstraint("license_number", name="refeps_cache_pkey"),
)

reset_tokens = Table(
    "reset_tokens",
    metadata,
    Column("token", Text),
    Column("dni", Text, nullable=False),
    Column("expires", TIMESTAMPTZ, nullable=False),
    Column("used", Boolean, server_default=text("false")),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    PrimaryKeyConstraint("token", name="reset_tokens_pkey"),
    ForeignKeyConstraint(
        ["dni"],
        ["users.dni"],
        name="fk_reset_tokens_dni",
        ondelete="RESTRICT",
        onupdate="CASCADE",
    ),
)
Index("reset_tokens_dni_idx", reset_tokens.c.dni)

# Claims on a document number (alembic 0045): the accounts whose number the front desk has not checked.
# `claimant_key` is the users.dni key of the account asking (a minted key when the number was taken);
# deliberately NOT a foreign key — see docs/architecture/claves-foraneas.md.
dni_claims = Table(
    "dni_claims",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("dni_claimed", Text, nullable=False),
    Column("claimant_key", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'pending'::text")),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("resolved_at", TIMESTAMPTZ),
    Column("resolved_by", Text),
    Column("note", Text),
    PrimaryKeyConstraint("id", name="dni_claims_pkey"),
    CheckConstraint("status IN ('unverified', 'pending', 'approved', 'rejected')", name="dni_claims_status_check"),
)
Index("dni_claims_claimed_idx", dni_claims.c.dni_claimed)
Index(
    "dni_claims_open_uniq",
    dni_claims.c.claimant_key,
    unique=True,
    postgresql_where=text("status IN ('unverified', 'pending')"),
)

roles = Table(
    "roles",
    metadata,
    Column("slug", Text),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=text("''::text")),
    Column("builtin", Boolean, nullable=False, server_default=text("false")),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("permissions", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("slug", name="roles_pkey"),
)

searches = Table(
    "searches",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("ts", TIMESTAMPTZ, server_default=text("now()")),
    Column("q", Text, nullable=False),
    Column("results", Integer, nullable=False),
    Column("source", Text),
    Column("sid", Text),
    PrimaryKeyConstraint("id", name="searches_pkey"),
)
Index("searches_q_idx", searches.c.q, searches.c.ts.desc())
Index("searches_ts_idx", searches.c.ts.desc())

sessions = Table(
    "sessions",
    metadata,
    Column("token", Text),
    Column("dni", Text, nullable=False),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    Column("last_seen", TIMESTAMPTZ, server_default=text("now()")),
    PrimaryKeyConstraint("token", name="sessions_pkey"),
    ForeignKeyConstraint(
        ["dni"],
        ["users.dni"],
        name="fk_sessions_dni",
        ondelete="RESTRICT",
        onupdate="CASCADE",
    ),
)
Index("sessions_dni_idx", sessions.c.dni)

slot_holds = Table(
    "slot_holds",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("service_id", Text, nullable=False),
    Column("day", Date, nullable=False),
    Column("time", Time, nullable=False),
    Column("token", Text, nullable=False),
    Column("request_id", BigInteger),
    Column("expires", TIMESTAMPTZ, nullable=False),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="slot_holds_pkey"),
    UniqueConstraint("service_id", "day", "time", name="slot_holds_service_id_day_time_key"),
    ForeignKeyConstraint(
        ["request_id"],
        ["booking_requests.id"],
        name="fk_slot_holds_request",
        ondelete="RESTRICT",
    ),
)
Index(
    "slot_holds_request_idx",
    slot_holds.c.request_id,
    postgresql_where=text("(request_id IS NOT NULL)"),
)
Index("slot_holds_token_idx", slot_holds.c.token)

study_events = Table(
    "study_events",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("session_id", BigInteger, nullable=False),
    Column("recorded_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("task_id", Text, nullable=False),
    Column("t", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("target", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("path", Text, nullable=False),
    PrimaryKeyConstraint("id", name="study_events_pkey"),
    CheckConstraint(
        "kind = ANY (ARRAY['click'::text, 'nav'::text, 'dead_click'::text, 'scroll_stop'::text, 'visibility'::text])",
        name="study_events_kind_check",
    ),
    CheckConstraint("t >= 0 AND t <= 86400000", name="study_events_t_check"),
    ForeignKeyConstraint(
        ["session_id"],
        ["study_sessions.id"],
        name="fk_study_events_session",
        ondelete="RESTRICT",
    ),
)
Index("study_events_session", study_events.c.session_id, study_events.c.task_id)

study_findings = Table(
    "study_findings",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("title", Text, nullable=False),
    Column("detail", Text, nullable=False, server_default=text("''::text")),
    Column("role", Text, nullable=False),
    Column("task_id", Text, nullable=False),
    Column("kind", Text),
    Column("severity", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("evidence_summary", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", Text, nullable=False, server_default=text("'found'::text")),
    Column("fix_note", Text, nullable=False, server_default=text("''::text")),
    Column("commit", Text, nullable=False, server_default=text("''::text")),
    Column("live_url", Text, nullable=False, server_default=text("''::text")),
    Column("decided_by", Text),
    Column("decided_at", TIMESTAMPTZ),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="study_findings_pkey"),
    CheckConstraint(
        "role = ANY (ARRAY['patient'::text, 'doctor'::text, 'admin'::text])",
        name="study_findings_role_check",
    ),
    CheckConstraint(
        "severity = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])",
        name="study_findings_severity_check",
    ),
    CheckConstraint(
        "source = ANY (ARRAY['auto'::text, 'manual'::text])",
        name="study_findings_source_check",
    ),
    CheckConstraint(
        "status = ANY (ARRAY['found'::text, 'in_progress'::text, 'fixed'::text, 'not_done'::text, 'dismissed'::text])",
        name="study_findings_status_check",
    ),
)
Index(
    "study_findings_auto",
    study_findings.c.task_id,
    study_findings.c.kind,
    unique=True,
    postgresql_where=text("(source = 'auto'::text)"),
)

study_sessions = Table(
    "study_sessions",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("token_hash", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("locale", Text, nullable=False),
    Column("consent_at", TIMESTAMPTZ, nullable=False),
    Column("started_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("finished_at", TIMESTAMPTZ),
    Column("user_agent_class", Text, nullable=False),
    PrimaryKeyConstraint("id", name="study_sessions_pkey"),
    UniqueConstraint("token_hash", name="study_sessions_token_hash_key"),
    CheckConstraint(
        "locale = ANY (ARRAY['es'::text, 'en'::text])",
        name="study_sessions_locale_check",
    ),
    CheckConstraint(
        "role = ANY (ARRAY['patient'::text, 'doctor'::text, 'admin'::text])",
        name="study_sessions_role_check",
    ),
    CheckConstraint(
        "user_agent_class = ANY (ARRAY['mobile'::text, 'tablet'::text, 'desktop'::text])",
        name="study_sessions_user_agent_class_check",
    ),
)
Index("study_sessions_retention", study_sessions.c.started_at)

study_task_results = Table(
    "study_task_results",
    metadata,
    Column("id", BigInteger, autoincrement=True),
    Column("session_id", BigInteger, nullable=False),
    Column("task_id", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("ease", Integer),
    Column("comment", Text, nullable=False, server_default=text("''::text")),
    Column("duration_ms", Integer, nullable=False),
    Column("started_at", TIMESTAMPTZ, nullable=False),
    Column("ended_at", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="study_task_results_pkey"),
    UniqueConstraint("session_id", "task_id", name="study_task_results_session_id_task_id_key"),
    CheckConstraint(
        "duration_ms >= 0 AND duration_ms <= 86400000",
        name="study_task_results_duration_ms_check",
    ),
    CheckConstraint("ease >= 1 AND ease <= 5", name="study_task_results_ease_check"),
    CheckConstraint(
        "outcome = ANY (ARRAY['ok'::text, 'doubt'::text, 'stuck'::text, 'skipped'::text])",
        name="study_task_results_outcome_check",
    ),
    ForeignKeyConstraint(
        ["session_id"],
        ["study_sessions.id"],
        name="fk_study_task_results_session",
        ondelete="RESTRICT",
    ),
)

translations = Table(
    "translations",
    metadata,
    Column("source_key", Text),
    Column("lang", Text),
    Column("source_hash", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("model", Text),
    Column("override", Boolean, nullable=False, server_default=text("false")),
    Column("created", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated", TIMESTAMPTZ, nullable=False, server_default=text("now()")),
    Column("updated_by", Text),
    PrimaryKeyConstraint("source_key", "lang", name="translations_pkey"),
)

users = Table(
    "users",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("dni", Text, nullable=False),
    Column("first_name", Text),
    Column("last_name", Text),
    Column("birth_date", Date),
    Column("sex", Text),
    Column("contact", Text),
    Column("email", Text),
    Column("pass_hash", Text),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    Column("identity_kind", Text, nullable=False, server_default=text("'dni'::text")),
    Column("active", Boolean, server_default=text("true")),
    Column("feed_token", Text),
    Column("mcp_token", Text),
    Column("coverage", JSONB),
    Column("address", JSONB),
    Column("notifications", JSONB),
    Column("updated", TIMESTAMPTZ),
    Column("lang", Text),
    Column("renaper", JSONB),
    Column("portal_user_id", Text),
    # mcp_token holds the SHA-256 hex of the MCP token, never the token itself;
    # mcp_token_hint its last 4 characters for «Mi cuenta» (alembic 0038).
    Column("mcp_token_hint", Text),
    # feed_token holds the SHA-256 hex of the calendar address token, never the
    # token itself; feed_token_hint its last 4 characters (alembic 0040).
    Column("feed_token_hint", Text),
    # When the MCP token was issued and when it was last used: it dies after
    # 30 days without use (alembic 0043).
    Column("mcp_token_at", TIMESTAMPTZ),
    Column("mcp_token_used", TIMESTAMPTZ),
    PrimaryKeyConstraint("id", name="users_pkey"),
    UniqueConstraint("dni", name="users_dni_key"),
    UniqueConstraint("feed_token", name="users_feed_token_key"),
    UniqueConstraint("mcp_token", name="users_mcp_token_key"),
)
Index("users_active_idx", users.c.active)
Index(
    "users_contact_tel_idx",
    last_ten_digits(users.c.contact),
    postgresql_where=text("(contact IS NOT NULL)"),
)
Index(
    "users_portal_user_id_uniq",
    users.c.portal_user_id,
    unique=True,
    postgresql_where=text("(portal_user_id IS NOT NULL)"),
)

waitlist = Table(
    "waitlist",
    metadata,
    Column("id", Integer, autoincrement=True),
    Column("request_id", Integer),
    Column("dni", Text),
    Column("email", Text),
    Column("service_id", Text),
    Column("center_slug", Text),
    Column("appointment_at", TIMESTAMPTZ),
    Column("active", Boolean, server_default=text("true")),
    Column("created", TIMESTAMPTZ, server_default=text("now()")),
    PrimaryKeyConstraint("id", name="waitlist_pkey"),
    ForeignKeyConstraint(
        ["request_id"],
        ["booking_requests.id"],
        name="fk_waitlist_request",
        ondelete="RESTRICT",
    ),
)
Index(
    "waitlist_created_idx",
    waitlist.c.service_id,
    waitlist.c.center_slug,
    waitlist.c.created,
    postgresql_where=text("active"),
)
Index("waitlist_request_idx", waitlist.c.request_id)
