"""Foreign keys for the 18 id/DNI links inside the database (Ley 25.326 art. 16 plans,
orphans report 2026-10-24, TASK-23).

Revision ID: 0032_foreign_keys
Revises: 0031_ai_prompts_text_en
Create Date: 2026-10-25

Until now no table of the application had a foreign key: the links between a
request and its history, an interview and its consultations, an account and
its sessions lived only in code, and a parent could be deleted under its
children. This revision adds the 18 links whose parent is a real row of the
database (`core.db_accounts.FOREIGN_KEYS` is the live map; this file keeps its own copy
frozen, like every revision).

Rules (the owner's answers of 2026-10-25, orphans report §5):

* `ON DELETE RESTRICT` everywhere — the same rule as «se deshabilitan, no se
  eliminan»: the database refuses to delete a request that has appointment
  history, attendance, holds, a waiting-list entry, an interview or a
  consultation, an account that has sessions, and so on. Erasure of a person
  (Ley 25.326) goes from the children to the parents.
* `ON UPDATE CASCADE` only on the links to `users(dni)` and
  `operators(username)`: a DNI correction or an operator rename is ONE update
  of the parent row and the database moves the children (`change_dni`,
  `operator_rename` keep their explicit updates, which become no-ops).
* A DNI of a clinical identity with no account, the catalog, the owner's name
  and the HSI mirrors are NOT foreign keys (no parent row exists); they stay
  with `DNI_MOVED_COLUMNS`, the code checks and `orphans.sql`.

Safe by construction, because the migration runs at app start and a failed
revision means «database unavailable»:

1. Every constraint is added `NOT VALID` — nothing is scanned, existing rows
   are not judged, new rows are checked at once. `lock_timeout` bounds the
   wait for the (instant) table lock.
2. `VALIDATE CONSTRAINT` runs afterwards, outside the migration transaction,
   one constraint at a time. A foreign-key violation is LOGGED with its
   orphan count and the constraint stays `NOT VALID`; the site starts anyway.
   `scripts/fixes/validate_foreign_keys.py` retries the validation after the
   data is fixed. Nothing is deleted or changed.
3. Idempotent: constraints and indexes are skipped when present, so a rerun
   after a lock timeout continues where it stopped. Forward-only.
"""
from alembic import op

revision = "0032_foreign_keys"
down_revision = "0031_ai_prompts_text_en"
branch_labels = None
depends_on = None

# (constraint, child table, child column, parent table, parent column, ON UPDATE)
_FOREIGN_KEYS = (
    ("fk_attendance_events_request", "attendance_events", "request_id", "booking_requests", "id", None),
    ("fk_appointment_events_request", "appointment_events", "request_id", "booking_requests", "id", None),
    ("fk_waitlist_request", "waitlist", "request_id", "booking_requests", "id", None),
    ("fk_appointment_offers_waitlist", "appointment_offers", "waitlist_id", "waitlist", "id", None),
    ("fk_slot_holds_request", "slot_holds", "request_id", "booking_requests", "id", None),
    ("fk_interviews_request", "interviews", "request_id", "booking_requests", "id", None),
    ("fk_consultations_interview", "consultations", "interview_id", "interviews", "id", None),
    ("fk_consultations_superseded_by", "consultations", "superseded_by", "consultations", "id", None),
    ("fk_patient_health_profile_interview", "patient_health_profile", "source_interview_id",
     "interviews", "id", None),
    ("fk_operator_username_history_operator", "operator_username_history", "operator_id",
     "operators", "id", None),
    ("fk_operator_access_username", "operator_access", "username", "operators", "username", "CASCADE"),
    ("fk_operator_access_role", "operator_access", "role_slug", "roles", "slug", None),
    ("fk_study_task_results_session", "study_task_results", "session_id", "study_sessions", "id", None),
    ("fk_study_events_session", "study_events", "session_id", "study_sessions", "id", None),
    ("fk_sessions_dni", "sessions", "dni", "users", "dni", "CASCADE"),
    ("fk_reset_tokens_dni", "reset_tokens", "dni", "users", "dni", "CASCADE"),
    ("fk_dependents_adult_dni", "dependents", "adult_dni", "users", "dni", "CASCADE"),
    ("fk_patient_lab_uploads_uploaded_by", "patient_lab_uploads", "uploaded_by_dni",
     "users", "dni", "CASCADE"),
)

# A foreign key's referencing column needs an index (parent delete/update
# checks scan the child); only the ones the schema lacks.
_INDEXES = (
    ("appointment_offers_waitlist_idx", "appointment_offers", "waitlist_id"),
    ("consultations_superseded_by_idx", "consultations", "superseded_by"),
    ("patient_health_profile_interview_idx", "patient_health_profile", "source_interview_id"),
    ("operator_username_history_operator_idx", "operator_username_history", "operator_id"),
    ("operator_access_role_idx", "operator_access", "role_slug"),
    ("reset_tokens_dni_idx", "reset_tokens", "dni"),
    ("patient_lab_uploads_uploaded_by_idx", "patient_lab_uploads", "uploaded_by_dni"),
)


def _add_not_valid(name, child, column, parent, pcolumn, on_update):
    cascade = " ON UPDATE CASCADE" if on_update == "CASCADE" else ""
    return (
        "DO $do$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '%(name)s' "
        "AND conrelid = 'public.%(child)s'::regclass) THEN "
        "ALTER TABLE %(child)s ADD CONSTRAINT %(name)s FOREIGN KEY (%(column)s) "
        "REFERENCES %(parent)s (%(pcolumn)s) ON DELETE RESTRICT%(cascade)s NOT VALID; "
        "END IF; END $do$" % dict(name=name, child=child, column=column, parent=parent,
                                  pcolumn=pcolumn, cascade=cascade))


def upgrade():
    from sqlalchemy.exc import DBAPIError
    bind = op.get_bind()
    bind.exec_driver_sql("SET LOCAL lock_timeout = '15s'")
    for index, table, column in _INDEXES:
        bind.exec_driver_sql("CREATE INDEX IF NOT EXISTS %s ON %s (%s)" % (index, table, column))
    for fk in _FOREIGN_KEYS:
        bind.exec_driver_sql(_add_not_valid(*fk))
    # The constraints are committed NOT VALID; validate them outside the
    # migration transaction so a violation can be logged instead of failing it.
    with op.get_context().autocommit_block():
        for name, child, column, parent, pcolumn, _ in _FOREIGN_KEYS:
            try:
                bind.exec_driver_sql(
                    "ALTER TABLE %s VALIDATE CONSTRAINT %s" % (child, name))
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) != "23503":  # foreign_key_violation
                    raise
                orphans = bind.exec_driver_sql(
                    "SELECT count(*) FROM %s c WHERE c.%s IS NOT NULL AND NOT EXISTS "
                    "(SELECT 1 FROM %s p WHERE p.%s = c.%s)"
                    % (child, column, parent, pcolumn, column)).scalar()
                print("[alembic 0032] %s stays NOT VALID: %s orphan row(s) in %s.%s with no "
                      "%s.%s; new rows are checked, old ones are not. Fix the data, then "
                      "run scripts/fixes/validate_foreign_keys.py"
                      % (name, orphans, child, column, parent, pcolumn))


def downgrade():
    raise NotImplementedError("foreign keys are forward-only; drop a constraint with a new revision")
