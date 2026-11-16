"""core/personal_data.py — the personal-data registry (Ley 25.326, art. 14-16).

One declarative list answers, for every table that holds something about a
person: which columns, how to FIND one person's rows, which columns are
secrets that never leave, and what happens to the rows when the person asks to
be erased. The data-subject tools read it and nothing else:

* `core/subject_export.py` — the read-only copy handed to the person (art. 14),
* `core/subject_erase.py`  — erase / anonymise (art. 16, P4).

`DNI_MOVED_COLUMNS` (core/db.py) stays what it is — the columns `change_dni`
moves. This registry is wider: it also knows the phone-keyed, email-keyed,
request-id-keyed and free-text places that a DNI registry cannot reach.

`tests/test_personal_data.py` reads the LIVE schema and fails when a table is
in neither `REGISTRY` nor the two "no personal data" lists, when a column
that looks like a DNI / phone / email / name / birth date / address / IP is in
no entry, when an entry names a column the schema doesn't have, when a secret
column would be exported, or when a child table is listed after its parent
(erasure runs in registry order — children first — so a future FOREIGN KEY
... RESTRICT never trips it).

Adding a table or a personal column = add (or extend) its entry here in the
same change. Everything below is the working reading in
docs/architecture/derechos-de-los-titulares.md; the entries marked `disputed`
are the ones whose fate depends on what the hospital's counsel says is the
"historia clínica" (Ley 26.529 art. 18) — questions Q1/Q2 there.
"""
import re
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql.elements import Grouping

# What happens to a person's rows when they ask to be erased.
DELETE = "delete"          # Class A: no legal reason to keep — the rows go
ANONYMISE = "anonymise"    # Class B: the row stays, the person does not: `blank` columns are
                           #   emptied and the DNI-keyed identity moves to a random key
KEEP = "keep"              # Class C: stays as is, with the reason in `reason`


class Entry(NamedTuple):
    table: str
    columns: tuple          # the personal columns this table holds
    find: tuple             # selectors OR-ed together (see the constructors below)
    erase: str              # DELETE | ANONYMISE | KEEP
    reason: str             # why this class — shown in the plan and the manifest
    part: str = ""          # a table can have several entries (chat_messages: web / whatsapp / ...)
    secret: tuple = ()      # columns that are never exported (tokens, hashes)
    blank: tuple = ()       # ANONYMISE: "col" -> NULL, or ("col", "<sql expression>")
    only_where: str = ""    # extra SQL condition for the erase step on this entry
    disputed: bool = False  # the class depends on counsel (Q1/Q2): the recommendation is stated
    export: bool = True     # False: withheld from the export, and the manifest says why
    withheld: str = ""      # the reason when export is False

    @property
    def label(self):
        return f"{self.table}.{self.part}" if self.part else self.table


# ---- selectors: how to find ONE person's rows ------------------------------
# Every selector is a plain tuple so the registry stays data. `where_sql`
# (below) turns them into SQL with bound parameters — values never get
# concatenated, identifiers only come from this file.

def dni(col):
    """col = the person's clinical DNI."""
    return ("dni", col)


def phone(col):
    """The last 10 digits of col equal the person's phone (same rule as `db_chat.user_by_phone`)."""
    return ("phone", col)


def email(col, unowned_by=None):
    """lower(col) is one of the person's emails. `unowned_by="dni"`: only rows
    that do not belong to a DNI of their own — a parent's address on a child's
    waiting-list entry is not the parent's row."""
    return ("email", col, unowned_by)


def key(col):
    """A chat key built from the DNI: "dni:<dni>" or "dni:<dni>:...". """
    return ("key", col)


def phone_key(col):
    """A WhatsApp thread key "tel:<digits>" whose last 10 digits are the person's phone."""
    return ("phone_key", col)


def inn(col, parent, parent_col, cast="", extra=""):
    """col is among `parent_col` of the PARENT table's rows found for the person.
    `cast="text"` compares as text (an integer id stored in a text column)."""
    return ("in", col, parent, parent_col, cast, extra)


def text(col):
    """The DNI appears as a whole number inside col (text or jsonb) — a sweep, not a key."""
    return ("text", col)


def json_dni(col, field):
    """col->>field equals the person's DNI."""
    return ("json_dni", col, field)


def access_hash(col):
    """col equals the HMAC of the DNI used by the clinical access log."""
    return ("hash", col)


_BLANK_USER = ("first_name", "last_name", "birth_date", "sex", "contact", "email", "address",
               "coverage", "notifications", "renaper", "portal_user_id", "pass_hash",
               "feed_token", "feed_token_hint", "mcp_token", "mcp_token_hint",
               "mcp_token_at", "mcp_token_used", ("active", "false"))

# Children first: erasure walks this list top to bottom, so a row that points
# at another (appointment_events -> booking_requests) is handled before the
# row it points at. test_personal_data pins the order for every `inn` selector.
REGISTRY = (
    # ---- conversations ------------------------------------------------------
    Entry("chat_messages", ("key", "content"), (key("key"),), DELETE,
          "the booking-assistant conversation of this person's account", part="web"),
    Entry("chat_messages", ("key", "content"), (phone_key("key"),), DELETE,
          "the WhatsApp thread, found by phone only (skipped when the phone also belongs to "
          "another account)", part="whatsapp"),
    Entry("chat_messages", ("key", "content"), (inn("key", "interviews", "session_key"),), KEEP,
          "transcript of a pre-consult interview; it follows the interview's fate (Q1)",
          part="interview", disputed=True),
    Entry("chat_summaries", ("patient_key", "summary"), (key("patient_key"),), DELETE,
          "AI memory of the conversations, regenerable"),
    Entry("chat_compactions", ("patient_key", "summary"), (key("patient_key"),), DELETE,
          "AI memory of the conversations, regenerable"),
    Entry("chat_handoffs", ("web_key", "used_phone"), (key("web_key"), phone("used_phone")), DELETE,
          "web -> WhatsApp grant, one-time", secret=("code",)),
    # ---- clinical and AI-derived -------------------------------------------
    Entry("consultations", ("diagnoses", "exams", "meds", "notes", "receta_json", "orden_json"),
          (inn("interview_id", "interviews", "id"),), KEEP,
          "prescriptions and orders the doctor issued: the strongest candidate for the "
          "10-year duty of Ley 26.529 art. 18 (Q1)",
          secret=("receta_token", "orden_token"), disputed=True),
    Entry("appointment_events", ("request_id",), (inn("request_id", "booking_requests", "id"),), KEEP,
          "audit of the appointment's state; carries no identity"),
    Entry("slot_holds", ("request_id",), (inn("request_id", "booking_requests", "id"),), DELETE,
          "short-lived hold on a slot", secret=("token",)),
    Entry("interviews",
          ("dni", "entered_name", "entered_surname", "session_key", "summary", "brief",
           "differential", "suggested_exams", "red_flags"),
          (dni("dni"), inn("request_id", "booking_requests", "id"),
           inn("detached_request_id", "booking_requests", "id")), ANONYMISE,
          "pre-consult clinical data: possibly part of the care record (Q1), so it stays "
          "without the person: the DNI key moves, the typed name goes",
          secret=("patient_token",), blank=("entered_name", "entered_surname", "patient_token"),
          disputed=True),
    Entry("appointment_offers", ("waitlist_id",), (inn("waitlist_id", "waitlist", "id"),), DELETE,
          "offer of a freed slot", secret=("token",)),
    Entry("waitlist", ("dni", "email"), (dni("dni"), email("email", unowned_by="dni")), DELETE,
          "waiting-list entry; found by email only when it has no DNI of its own"),
    # ---- HSI mirrors and the sync journal ----------------------------------
    Entry("hsi_outbox", ("entity_id", "payload", "hsi_result", "last_error"),
          (json_dni("payload", "dni"),
           inn("entity_id", "booking_requests", "id", "text", "entity_type = 'appointment'")),
          ANONYMISE,
          "the intent sent to HSI keeps its row; once confirmed the payload (DNI, name, birth "
          "date, sex) is emptied — a pending one is still needed for replay, so it blocks (Q5)",
          blank=(("payload", "'{}'::jsonb"), "hsi_result", "last_error"),
          only_where="state = 'confirmed'",
          disputed=True),
    Entry("hsi_inbox", ("payload",), (text("payload"),), DELETE,
          "mirror of what HSI said; the next inbound sync recreates it while the person "
          "exists in HSI (Q5)", disputed=True),
    Entry("hsi_sync_conflicts", ("local_payload", "remote_payload"),
          (text("local_payload"), text("remote_payload")), DELETE,
          "a resolved sync conflict; an unresolved one blocks the erasure (Q5)",
          only_where="resolved_at IS NOT NULL", disputed=True),
    Entry("hsi_checkpoints", ("entity_id", "remote"), (text("remote"),), DELETE,
          "mirror of HSI's last answer", disputed=True),
    Entry("hsi_appointments", ("hsi_patient_id", "payload"),
          (inn("hsi_patient_id", "hsi_patients", "hsi_patient_id"), text("payload")), DELETE,
          "mirror of HSI's appointments (HSI is the system of record)", disputed=True),
    Entry("hsi_patients", ("hsi_patient_id", "dni", "payload"), (dni("dni"),), DELETE,
          "mirror of HSI's patient; `change_dni` leaves it under the DNI by design (HSI says "
          "who holds it), so it has to go for the person to be unreachable (Q5)", disputed=True),
    # ---- clinical documents of the patient ---------------------------------
    Entry("patient_lab_uploads", ("dni", "uploaded_by_dni", "filename", "archivo", "texto"),
          (dni("dni"),), ANONYMISE,
          "the file and its extracted text go (the soft delete used to keep the bytes); "
          "the stub row stays. Found by `dni` only: a file a guardian uploaded for a "
          "dependent is the dependent's, and `uploaded_by_dni` just follows the DNI move",
          blank=("archivo", "texto", "filename", ("deleted", "coalesce(deleted, now())"))),
    Entry("lab_report_analysis", ("dni", "resumen", "valores"), (dni("dni"),), DELETE,
          "AI reading of a lab report, regenerable"),
    Entry("clinical_history_records", ("dni", "record"), (dni("dni"),), ANONYMISE,
          "rows imported from the hospital's old systems: a read copy, but whether it is "
          "also the record is for counsel (Q1)", disputed=True),
    Entry("patient_summaries", ("dni", "resumen"), (dni("dni"),), DELETE,
          "AI chart summary, regenerable"),
    Entry("patient_health_profile", ("dni", "datos"), (dni("dni"),), ANONYMISE,
          "what the patient declared (medication, conditions, allergies): kept without the "
          "person pending counsel (Q1)", disputed=True),
    # ---- requests and appointments -----------------------------------------
    Entry("attendance_events", ("dni", "request_id", "operator"),
          (dni("dni"), inn("request_id", "booking_requests", "id")), ANONYMISE,
          "who marked present / absent: audit of the appointment, kept under the random key"),
    Entry("booking_requests",
          ("dni", "requester_dni", "first_name", "last_name", "birth_date", "sex", "contact",
           "referral_path", "ai_json", "patient_note", "case_chat", "hsi_patient_id",
           "rejection_reason"),
          (dni("dni"),), ANONYMISE,
          "administrative record of the care: the copy of name, birth date, sex, contact, the "
          "free-text note, the «Contanos tu caso» conversation and the AI reading of the "
          "referral go; ids, service, dates, status "
          "and the HSI ids stay so the hospital can reconcile with HSI (Q1/Q2)",
          secret=("confirm_token", "idem_key"),
          blank=("first_name", "last_name", "birth_date", "sex", "contact", "patient_note",
                 "case_chat", "rejection_reason", "ai_json", "confirm_token", "idem_key", "referral_path",
                 "referral_mime"),
          disputed=True),
    # ---- account and identity ----------------------------------------------
    Entry("dependents",
          ("adult_dni", "dni", "first_name", "last_name", "birth_date", "sex", "relationship"),
          (dni("adult_dni"), dni("dni")), DELETE,
          "the person's links as a guardian, and the lines other accounts hold about the "
          "person as a dependent"),
    Entry("sessions", ("dni",), (dni("dni"),), DELETE, "login sessions", secret=("token",)),
    Entry("reset_tokens", ("dni",), (dni("dni"),), DELETE, "password-reset links",
          secret=("token",)),
    Entry("dni_claims", ("dni_claimed", "claimant_key"), (dni("claimant_key"),), DELETE,
          "the desk's check of who holds a document number: the claims the person's own account made "
          "(claims OTHER accounts made on their number stay: erasing the holder must not turn a pending "
          "claimant into a confirmed one)"),
    Entry("otp_codes", ("contact",), (phone("contact"),), DELETE,
          "one-time codes sent to the phone", secret=("code_hash",)),
    Entry("otp_verified", ("contact",), (phone("contact"),), DELETE,
          "phone verified tickets"),
    Entry("users", ("dni", "first_name", "last_name", "birth_date", "sex", "contact", "email",
                    "address", "coverage", "notifications", "renaper", "portal_user_id"),
          (dni("dni"),), ANONYMISE,
          "the account is deactivated and emptied; the row stays as the anchor of the records "
          "that stay (Q2)",
          secret=("pass_hash", "feed_token", "feed_token_hint", "mcp_token", "mcp_token_hint",
                  "mcp_token_at", "mcp_token_used"),
          blank=_BLANK_USER,
          disputed=True),
    # ---- logs and telemetry -------------------------------------------------
    Entry("ai_costs", ("ip", "user_dni"), (dni("user_dni"),), ANONYMISE,
          "analytics: the IP goes, the DNI key moves; the IP also expires on its own after 180 days "
          "(housekeeping, TURNOS_HOUSEKEEPING_AI_COSTS_IP_DAYS)", blank=("ip",)),
    Entry("searches", ("q",), (text("q"),), DELETE,
          "raw search-box text; only the rows where the person typed their own DNI"),
    Entry("clinical_access_log", ("dni_hash", "actor"), (access_hash("dni_hash"),), KEEP,
          "append-only evidence of who read the record (database trigger), kept 10 years; "
          "never erased on request",
          export=False,
          withheld="evidence of staff access, not the person's data; whether its lines are "
                   "disclosed is a question for counsel (Q4)"),
    Entry("data_subject_request_events", ("request_id",),
          (inn("request_id", "data_subject_requests", "id"),), KEEP,
          "trail of the person's own requests (append-only, database trigger): the proof "
          "that they were answered on time"),
    Entry("data_subject_requests", ("subject_hash",), (access_hash("subject_hash"),), KEEP,
          "ledger of the person's own requests; the person is only an HMAC of the DNI"),
    Entry("backup_journal", ("path", "remote_name"), (text("path"),), KEEP,
          "legacy file-backup journal; object names carry the DNI (cuenta-fotos/<dni>.<ext>)",
          export=False, withheld="backup bookkeeping; the files themselves are listed apart"),
    Entry("backup_versions", ("remote_name", "journal_path"),
          (text("remote_name"), text("journal_path")), KEEP,
          "legacy file-backup versions; object names carry the DNI",
          export=False, withheld="backup bookkeeping"),
    Entry("backup_version_tombstones", ("remote_name",), (text("remote_name"),), KEEP,
          "legacy file-backup tombstones; object names carry the DNI",
          export=False, withheld="backup bookkeeping"),
)

# Staff are data subjects too, but a separate request type (spec Q8).
STAFF_TABLES = {
    "admin_account_security": "staff login security",
    "admin_preferences": "staff preferences",
    "admin_reset_tokens": "staff password reset",
    "admin_session_revocations": "staff sessions that were signed out",
    "operator_access": "staff permissions",
    "operator_username_history": "staff usernames",
    "operators": "staff accounts (username, email, CUIT, portal id)",
    "passkeys": "staff passkeys",
    "passkey_challenges": "staff passkey challenge digests",
}

# Tables that hold nothing about a person. A column that looks personal in one
# of these is still caught by the completeness test.
NO_PERSONAL_DATA = {
    "ai_prompts": "editable AI instructions",
    "ai_prompts_history": "history of the AI instructions",
    "ai_register_changes": "register of AI function changes",
    "alembic_version": "schema version",
    "alert_claims": "which daily alert was already sent",
    "analytics_events": "anonymous page/search/step events: allowlisted fields only, no IP, no persistent id",
    "analytics_keys": "SHA-256 of the read-only export keys (no person)",
    "analytics_schedule": "public availability counts by doctor/center/day (no patient)",
    "backup_purge_state": "what the last remote purge pass of the B2 sweep did (counts and times)",
    "backup_runs": "backup runs of the whole archive",
    "catalog": "content overlay (services, centres, doctors; test identities on stands)",
    "date_time_conversion_leftovers": "slot coordinates and report dates one migration (0030) set aside — "
                                      "no name, DNI or contact; a hold's request id is a bare number",
    "day_queue_events": "no identifier by design",
    "demo_refresh_completion": "demo bookkeeping",
    "hsi_doctor_schedules": "doctors' HSI schedules",
    "launch_signal_counters": "hourly counts of operational events by closed code (no identifier)",
    "legal_docs": "the two legal texts",
    "login_panel_events": "anonymous login-panel telemetry",
    "mcp_ai_budget": "daily AI budget counter",
    "purged_files": "paths of referral files the retention or an erasure deleted (ordenes/<request id>.<ext>); "
                    "never a cuenta-fotos/<dni> path, which would carry the DNI",
    "refeps_cache": "public registry of professionals",
    "roles": "role definitions",
    "study_events": "anonymous opt-in research",
    "study_findings": "research findings",
    "study_sessions": "anonymous opt-in research",
    "study_task_results": "anonymous opt-in research",
    "translations": "machine-translation cache of catalog content",
}
NO_PERSONAL_PREFIXES = ("procrastinate_",)  # the job queue: only `timestamp=...` (spec 2.6)

# A column named like this holds something about a person. Every such column of
# every table outside STAFF_TABLES must be listed in an entry of its table.
PERSONAL_COLUMN = re.compile(
    r"dni|phone|contact|e?mail|cuit|first_name|last_name|entered_|birth|address|^sex$|"
    r"^ip$|portal_user|patient_id", re.I)
# A column named like this is a secret (token, hash of a secret): never exported.
SECRET_COLUMN = re.compile(r"token|pass_hash|secret|idem_key|^code$|code_hash", re.I)
# Not secrets although they match: the HMAC of the DNI in the access log is an
# identifier, handled by the entry's own `export=False`.
SECRET_COLUMN_OK = {("clinical_access_log", "dni_hash")}


def entries_of(table):
    return [e for e in REGISTRY if e.table == table]


def is_no_personal(table):
    return table in NO_PERSONAL_DATA or table.startswith(NO_PERSONAL_PREFIXES)


def audit(schema):
    """Check the registry against a schema and return the list of problems
    (empty = complete). `schema` = {table: {column: data_type}}.

    This is the completeness test of the registry: a table nobody classified,
    a personal-looking column nobody listed, an entry naming something the
    schema doesn't have, a secret that would be exported, a parent listed
    before its child."""
    problems = []
    seen_tables = {}
    for e in REGISTRY:
        seen_tables.setdefault(e.table, []).append(e)
    for table in sorted(schema):
        homes = [name for name, group in (("registry", seen_tables), ("staff", STAFF_TABLES),
                                          ("no-personal", NO_PERSONAL_DATA)) if table in group]
        if table.startswith(NO_PERSONAL_PREFIXES):
            homes.append("no-personal")
        if not homes:
            problems.append(f"table {table}: not in REGISTRY, STAFF_TABLES or NO_PERSONAL_DATA — "
                            "decide whether it holds anything about a person and register it")
        elif len(set(homes)) > 1:
            problems.append(f"table {table}: listed in more than one of {sorted(set(homes))}")
    for table in sorted(set(seen_tables) | set(STAFF_TABLES) | set(NO_PERSONAL_DATA)):
        if table not in schema:
            problems.append(f"table {table}: named by the registry but not in the schema")
    for table, cols in sorted(schema.items()):
        if table in STAFF_TABLES:
            continue
        entries = seen_tables.get(table, [])
        listed = {c for e in entries for c in e.columns}
        secret = {c for e in entries for c in e.secret}
        for col in sorted(cols):
            if PERSONAL_COLUMN.search(col) and col not in listed:
                problems.append(f"column {table}.{col}: looks personal but no registry entry "
                                "lists it (add it to `columns` of the table's entry, or "
                                "register the table)")
            if (entries and SECRET_COLUMN.search(col) and col not in secret
                    and (table, col) not in SECRET_COLUMN_OK):
                problems.append(f"column {table}.{col}: looks like a secret but is not in "
                                "`secret` — it would be exported")
    for e in REGISTRY:
        cols = schema.get(e.table)
        if cols is None:
            continue
        named = set(e.columns) | set(e.secret)
        named |= {b if isinstance(b, str) else b[0] for b in e.blank}
        for sel in e.find:
            named.add(sel[1])
            if sel[0] == "email" and sel[2]:
                named.add(sel[2])
        for col in sorted(named):
            if col not in cols:
                problems.append(f"entry {e.label}: names column {col}, which {e.table} doesn't have")
        if e.erase not in (DELETE, ANONYMISE, KEEP):
            problems.append(f"entry {e.label}: unknown erase class {e.erase!r}")
        if not e.export and not e.withheld:
            problems.append(f"entry {e.label}: withheld from the export without a reason")
        if not e.reason:
            problems.append(f"entry {e.label}: no reason")
        for sel in e.find:
            if sel[0] == "in":
                parent = sel[2]
                if parent not in seen_tables:
                    problems.append(f"entry {e.label}: parent table {parent} is not registered")
                    continue
                if _index_of_table(parent) < _index_of_entry(e):
                    problems.append(f"entry {e.label}: listed AFTER its parent {parent} — "
                                    "erasure runs in order, children first")
                pcols = schema.get(parent, {})
                if sel[3] not in pcols:
                    problems.append(f"entry {e.label}: parent column {parent}.{sel[3]} is missing")
    return problems


def _index_of_table(table):
    return min(i for i, e in enumerate(REGISTRY) if e.table == table)


def _index_of_entry(entry):
    return REGISTRY.index(entry)


# ---- the subject and the SQL that finds their rows -------------------------

class Subject(NamedTuple):
    dni: str
    phone10: str = ""        # "" = no usable phone: phone selectors match nothing
    emails: tuple = ()       # lowercase; () = no usable email
    dni_hash: str = ""       # access_log.dni_hash(dni)
    notes: tuple = ()        # ambiguities found while resolving (shared phone / email)

    @property
    def params(self):
        return {"dni": self.dni, "phone10": self.phone10, "emails": list(self.emails),
                "dni_hash": self.dni_hash}


_BY_TABLE_FIRST = {}


def _parent_entry(table):
    """The entry whose `find` identifies a PARENT table's rows. Parents are
    identified by their own keys, so the first entry of the table is the one."""
    if not _BY_TABLE_FIRST:
        for e in REGISTRY:
            _BY_TABLE_FIRST.setdefault(e.table, e)
    return _BY_TABLE_FIRST[table]


def _clause(sel, subject, depth):
    kind = sel[0]
    col = sel[1]
    if kind == "dni":
        return f"{col} = %(dni)s::text"
    if kind == "phone":
        if not subject.phone10:
            return "FALSE"
        return f"right(regexp_replace({col}, '\\D', '', 'g'), 10) = %(phone10)s::text"
    if kind == "email":
        if not subject.emails:
            return "FALSE"
        owner = f" AND {sel[2]} IS NULL" if sel[2] else ""
        return f"(lower({col}) = ANY(%(emails)s::text[]){owner})"
    if kind == "key":
        return (f"({col} = 'dni:' || %(dni)s::text OR "
                f"left({col}, length(%(dni)s::text) + 5) = 'dni:' || %(dni)s::text || ':')")
    if kind == "phone_key":
        if not subject.phone10:
            return "FALSE"
        return (f"(left({col}, 4) = 'tel:' AND "
                f"right(regexp_replace({col}, '\\D', '', 'g'), 10) = %(phone10)s::text)")
    if kind == "text":
        return f"{col}::text ~ ('(^|[^0-9])' || %(dni)s::text || '([^0-9]|$)')"
    if kind == "json_dni":
        return f"{col}->>'{sel[2]}' = %(dni)s::text"
    if kind == "hash":
        return f"{col} = %(dni_hash)s::text"
    if kind == "in":
        _, _, parent, parent_col, cast, extra = sel
        if depth > 4:
            raise ValueError("selector nesting too deep: " + parent)
        entry = _parent_entry(parent)
        inner = " OR ".join(_clause(s, subject, depth + 1) for s in entry.find)
        pcol = f"{parent_col}::{cast}" if cast else parent_col
        cond = f"{col} IN (SELECT {pcol} FROM {parent} WHERE {inner})"
        return f"({extra} AND {cond})" if extra else cond
    raise ValueError("unknown selector: %r" % (sel,))


def where_sql(entry, subject):
    """SQL condition (named %(param)s placeholders, see `Subject.params`) that
    selects this entry's rows of the subject.

    The text form of `where_clause`, kept as the readable statement of what each
    selector means (and as the oracle `test_subject_store_pins` compares the Core
    form with); the data-subject tools run `where_clause`."""
    return " OR ".join(f"({_clause(s, subject, 0)})" for s in entry.find)


# ---- the same selectors as SQLAlchemy Core ------------------------------------
# Columns are bare (unqualified), exactly as the text form writes them: the clause
# goes into `DELETE FROM t`, `FROM t AS t` and into the sub-select of an `inn`
# selector alike, and in each of them the bare name belongs to the table of that
# statement. Constants of the SQL (`'dni:'`, the regular expression) stay literals,
# a value of the subject is a bound parameter cast to text like `%(dni)s::text`.

_CASTS = {"text": sa.Text, "integer": sa.Integer, "bigint": sa.BigInteger}


def _txt(value):
    return sa.cast(sa.literal(value, sa.Text), sa.Text)


def _lit(sql):
    return sa.literal_column(sql, sa.Text)


def _last10_digits(col):
    """`right(regexp_replace(col, '\\D', '', 'g'), 10)`: the last 10 digits of a phone."""
    digits = sa.func.regexp_replace(col, _lit(r"'\D'"), _lit("''"), _lit("'g'"))
    return sa.func.right(digits, sa.literal_column("10"))


def _clause_core(sel, subject, depth):
    kind = sel[0]
    col = sa.column(sel[1])
    if kind == "dni":
        return col == _txt(subject.dni)
    if kind == "phone":
        if not subject.phone10:
            return sa.false()
        return _last10_digits(col) == _txt(subject.phone10)
    if kind == "email":
        if not subject.emails:
            return sa.false()
        emails = sa.cast(sa.literal(list(subject.emails), ARRAY(sa.Text)), ARRAY(sa.Text))
        match = sa.func.lower(col) == sa.any_(emails)
        return sa.and_(match, sa.column(sel[2]).is_(None)) if sel[2] else match
    if kind == "key":
        exact = _lit("'dni:'").concat(_txt(subject.dni))
        prefix = sa.func.left(col, sa.func.length(_txt(subject.dni)) + sa.literal_column("5"))
        return sa.or_(col == exact, prefix == exact.concat(_lit("':'")))
    if kind == "phone_key":
        if not subject.phone10:
            return sa.false()
        return sa.and_(sa.func.left(col, sa.literal_column("4")) == _lit("'tel:'"),
                       _last10_digits(col) == _txt(subject.phone10))
    if kind == "text":
        pattern = _lit("'(^|[^0-9])'").concat(_txt(subject.dni)).concat(_lit("'([^0-9]|$)'"))
        # `~` and `||` share a precedence level in PostgreSQL: the pattern needs its parentheses
        return sa.cast(col, sa.Text).op("~")(Grouping(pattern))
    if kind == "json_dni":
        return col.op("->>")(sa.literal_column("'%s'" % sel[2])) == _txt(subject.dni)
    if kind == "hash":
        return col == _txt(subject.dni_hash)
    if kind == "in":
        _, _, parent, parent_col, cast, extra = sel
        if depth > 4:
            raise ValueError("selector nesting too deep: " + parent)
        inner = sa.or_(*[_clause_core(s, subject, depth + 1) for s in _parent_entry(parent).find])
        pcol = sa.cast(sa.column(parent_col), _CASTS[cast]) if cast else sa.column(parent_col)
        cond = col.in_(sa.select(pcol).select_from(sa.table(parent)).where(inner))
        return sa.and_(sa.text(extra), cond) if extra else cond
    raise ValueError("unknown selector: %r" % (sel,))


def where_clause(entry, subject):
    """The Core predicate that selects this entry's rows of the subject: the same
    condition as `where_sql`, built from the selectors instead of written as text."""
    return sa.or_(*[_clause_core(s, subject, 0) for s in entry.find])
