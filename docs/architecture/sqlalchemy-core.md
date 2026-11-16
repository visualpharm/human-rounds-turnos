# SQLAlchemy Core: the schema as code and the query language of new code

Decided 2026-10-25 (`docs/decisions/2026-10-w4g.md`): the database layer moves, in stages, from SQL
text written by hand with psycopg to **SQLAlchemy Core** — never the ORM. Stage 1 (this
document) puts the schema description, the autogenerate hint, the way a Core statement
reaches the existing connection, and one converted module in place. The ~600 hand-written
statements of `app/core/db.py` and the other modules moved in stages 2-7, one module at a time
(below); none is left, and `tests/test_raw_sql_ratchet.py` keeps it so.

## The three pieces

| Piece | What it is |
|---|---|
| `app/core/schema_model.py` | One `MetaData`, one `Table` per table of a fresh install at Alembic head (columns, types, NOT NULL, server defaults, primary keys, UNIQUE, CHECK, indexes with predicates and sort order). No mapped classes. |
| `alembic/env.py` | `target_metadata = schema_model.metadata`, so `alembic revision --autogenerate -m "..."` and `alembic check` compare a database with the model. |
| `app/core/sqlcore.py` | `sqlcore.run(cur, statement)`: compiles a Core statement for the psycopg 3 dialect and executes it on the cursor you already hold. |

Not described by the model: triggers, functions and `$$` bodies (a revision owns them;
`alembic/schema-head.txt` guards them), and — outside the model AND the snapshot — the Alembic
table, the retired `schema_migrations`, the queue library's `procrastinate_*` tables and the optional
`pg_trgm` search indexes (their own mechanisms own them; `alembic/env.py` keeps autogenerate away
from them).

## Changing the schema

One commit contains all four:

1. the edit of `app/core/schema_model.py` (the column / table / constraint / index);
2. a forward revision `alembic/versions/00NN_*.py` — `alembic revision --autogenerate -m "..."`
   proposes it from step 1, **read and edit it by hand**: autogenerate never writes triggers,
   functions, data backfills or `IF NOT EXISTS` idempotence, and the SQL in the revision is what runs;
3. `python3 scripts/schema_snapshot.py --write` (the catalog snapshot `alembic/schema-head.txt`);
4. the test that pins the change, as always.

Guards: `tests/test_schema_model.py` builds a database from the model alone (`metadata.create_all`)
and compares it with `schema-head.txt` — table, column, type, order, NOT NULL, default,
constraint, index (predicate, sort order), sequence. A revision without the model edit, or the
model edit without a revision, is red and the failure names what differs.
`TestAlembicTargetsTheModel` proves `alembic check` is silent on a fresh install and loud on drift.

Conventions in the model: a table is a module attribute of the same name; every constraint and
index carries the name the database has; defaults are `server_default=text("...")` as the catalog
prints them; a serial primary key is `autoincrement=True`; an index on an expression is built with
SQLAlchemy functions over the table's columns (`last_ten_digits(users.c.contact)`) — an `Index`
made only of `text()` is not attached to its table and `create_all` silently skips it.

## Writing a query

```python
from sqlalchemy import select, update
from core import db, sqlcore
from core.schema_model import waitlist

with db._connect() as conn, conn.cursor() as cur:
    row = sqlcore.run(cur, select(waitlist).where(waitlist.c.id == id_)).fetchone()
    sqlcore.run(cur, update(waitlist).where(waitlist.c.id == id_).values(active=False))
```

How the connection works, and what stays exactly as before:

- **The connection is `db._connect()`'s pooled psycopg connection.** Checkout, health check,
  `DbUnavailable`, pool size, and the commit-on-exit / rollback-on-error of the `with` block are
  untouched. No `Engine`, `Session` or SQLAlchemy `Connection` exists for application queries
  (Alembic's `env.py` alone builds a throwaway NullPool engine for a migration).
- **The transaction is the caller's.** A Core statement and a hand-written `cur.execute(...)` in
  the same `with` block share one transaction (committed together, rolled back together, each sees
  the other's uncommitted rows). A function that receives `conn` or `cur` from its caller runs its
  statements on it — `sqlcore.run(cur, ...)` — exactly like `cur.execute(...)`.
- **Rows are the pool's `dict_row` dicts**, keyed by column name (or the `.label(...)`), so
  `db_wire._wire(row, "table")` and every caller see the same shape. The model uses the database's
  (English) column names; the Spanish wire keys stay `db_wire._wire`'s job until the consumers move.
- **No bind casts.** SQLAlchemy's psycopg dialect writes `%(id_1)s::INTEGER`; `sqlcore` switches
  that off (`BindTyping.NONE`) so the database receives what psycopg's own adaptation of the Python
  value sends, as it does for hand-written SQL.
- **Parameters go through the type's bind processor** (a `JSONB` value becomes psycopg's `Jsonb`),
  `IN (...)` lists are expanded (an empty list matches nothing).
- **Cost.** The statement is built on every call, its compiled SQL is cached by structure
  (`sqlcore._COMPILED_CACHE`, SQLAlchemy's own mechanism, 500 entries, LRU): the values of each call
  are read off the statement being run, never off the cached one (`TestCompiledCache`). Measured on
  `study_store` against the hand-written version, CPU time in the client: without the cache a
  six-column insert cost ≈ 0.35 ms (the compile), with it ≈ 0.07–0.09 ms more than the raw
  `cur.execute`; a `select` by key ≈ 0.1 ms more. A loopback round trip to Postgres is of the same order,
  so the converted code paths that run on every panel request (stages 5–6) still get a before/after
  measurement of the whole request in their MR.
- It returns the cursor, so `.fetchone()`, `.fetchall()`, `.rowcount` and `RETURNING` read as after
  `cur.execute`.

Translating the usual constructs: `WHERE x IS NULL` → `.is_(None)`; `ON CONFLICT (cols) WHERE pred
DO NOTHING` → `postgresql.insert(t).values(...).on_conflict_do_nothing(index_elements=[...], index_where=...)`;
`FOR UPDATE` → `.with_for_update()`; `now()` → `func.now()`; advisory locks and one-off functions →
`func.pg_advisory_xact_lock(...)` or `text(...)`. A statement that has no Core spelling stays `text()` or
a plain `cur.execute` inside the same cursor, with a comment saying why.

## Converting an existing module

The pattern that `app/modules/turnos/waitlist_store.py` set (stage 1's sample):

1. Pin the module's behavior first: a DB-backed test that calls each function and asserts its rows,
   ordering, idempotence and every branch (`tests/test_waitlist_store.py`). It passes on the old code.
2. Rewrite the statements with Core; keep function names, signatures, return shapes and the
   `with db._connect()` frames unchanged.
3. Run the pinning test and the suites that use the module; ship the conversion as its own commit.
4. A statement that needs a table the model does not describe means the model is incomplete —
   fix the model, never bypass it.

Order of conversion: the small adapters that own their tables and have a DB-backed suite first
(`waitlist_store`, `study_store`, `permissions`, `ai_register`, the `app/ops/` stores); then `app/core/db.py`
in slices by table group, each slice behind its own pinning tests; the hot paths (day queue, approve /
cancel with slot locking, the HSI journal) last, after the pattern has proven itself on the rest.

Stage 2 (adapters that own their tables, no access rights): `app/modules/turnos/study_store.py` (all 24
statements), `app/core/backup_journal.py` (upsert, list and the session advisory lock), `app/ops/demo_refresh_store.py`
(the marker read and the try-lock / unlock), `app/core/config.py` `guardar` (advisory lock + row lock on the
settings overlay), `app/core/ai_register.py` `observe_inventory` (advisory lock, baseline probe, latest snapshot)
and all of `app/ops/backup_full.py` and `admin_panel/system_status.probe_db`: none of these files calls
`.execute(` any more. Statements with no table behind them are still written through `sqlcore.run`:
server functions are `func.to_regclass(...)` / `func.pg_export_snapshot()`, the liveness ping is
`select(literal_column("1"))`, `alembic_version` is a `Table` in `schema_model.external` (a second
`MetaData`: queryable, but never seen by `create_all`, autogenerate or the snapshot), and the
statements that are SQL by nature — `SET TRANSACTION`, the `pg_class` / `pg_proc` emptiness probe,
`CREATE` / `DROP DATABASE` (the name goes through `sqlcore.quote_identifier`, an identifier cannot be
a parameter) — are `text()`. Advisory locks are `func.pg_advisory_*` over `func.hashtext(...)` cast to
`BigInteger`. A statement that carries a lock (`with_for_update`, `skip_locked`) keeps it in the Core spelling and its
effect is pinned by a test that holds the lock from a second connection.

Stage 3 (`app/core/db.py`, the groups with no access rights and no hot path): the AI group (editable
prompts, the cost ledger and its report queries, the MCP daily budget, the AI register), backup /
purged files / the demo-refresh completion marker, the catalog overlay, translations, legal texts,
the REFEPS cache, searches, the login-panel experiment, daily alert claims — and the boot-time and
locking helpers of the same file (`_owner_conn_same_db`, `_alembic_version_present`,
`migrations_status`, `_apply_trigrams`, `_seed_builtin_roles`, `catalog_schedule_lock`, the advisory
locks of the converted functions, the `SAVEPOINT` of `backup_journal_commit`). No hand-written SQL
is left in those functions. Pinned by `test_db_ai_store`, `test_db_backup_store`,
`test_db_catalog_store`, `test_db_bootstrap_store` (each passed on the old code before the rewrite).

Stage 4 (`app/core/db.py`: chat, HSI, clinical). **4a, chat and HSI:** the multi-turn chat (messages, history, the
spend-guard count, the admin «Chats» list, the nightly export, the booking-proposal metadata with its advisory
lock, the cross-session summaries and compactions, the web -> WhatsApp hand-off codes, and the phone -> account
resolution of WhatsApp) is `app/core/db_chat.py`; the durable HSI journal (outbox with the `FOR UPDATE SKIP LOCKED`
lease, inbox, mirror tables, checkpoints, the sync-conflict log, the resolver's two booking-row writes, the
numbers of the «HSI» panel, the patient's remote appointments) is `app/core/db_hsi.py`. Pinned by `test_db_chat_store`
and `test_db_hsi_store` (each passed on the old code before the rewrite; the lease and the locks are held from a
second connection). No hand-written SQL is left in either module. **4b, clinical:** the pre-visit interviews
(`interview_bind` under its advisory lock and the `FOR UPDATE` of the existing row, the red-triage bell, the day
queue's lookups), their consultations (`consultation_confirm`: row lock, one current consultation, the supersede
chain, `ConsultationExists`), the patient's health profile, the pre-generated chart summaries, the lab readings and
uploads, the imported clinical history, «Mis pacientes» and the clinical access log (`insert_clinical_access_many` is
one `executemany` through `sqlcore.run_many`; `purge_clinical_access_log` still sets `turnos.access_log_purge` for its
own transaction, which is all the append-only trigger accepts) are `app/core/db_clinical.py`. Pinned by
`test_db_clinical_store` (the trigger is exercised too: a plain `DELETE` is refused, the purge is the only deleter).
The conversion also found, and fixed first with its own failing test, that `consultation_confirm` caught
`psycopg.errors.UniqueViolation` without importing `psycopg` (a repeated token ended in a `NameError` instead of
`ConsultationExists`, which `consult_admin` already knows how to answer).

Stage 5 (`app/core/db.py`: accounts and authentication — 5a; the permissions store `app/core/permissions.py` — 5b). The patient accounts — registration
with its first session and the advisory lock on the email, profile, password with its transparent hash upgrade,
sessions, reset tokens, OTP codes, the portal and RENAPER bindings, the calendar-feed and MCP tokens, archiving and
the DNI change — are `app/core/db_accounts.py`; the panel operators (`operators` plus the `operator_access` overlay, the
doctor links under an advisory lock, rename with its history, deactivation) are `app/core/db_operators.py`; the owner's
account security, passkeys with their challenges, admin preferences and the admin password-reset tokens are
`app/core/db_auth.py`; in 5b the roles, the per-account overrides, the effective-permission read that runs on every panel
request (`access_for`, a prepared statement) and the last-manager guard are `app/core/permissions.py` itself (no new module,
pinned by `test_permissions_store`). The accounts are pinned by `test_db_accounts_store`, `test_db_operators_store`, `test_db_auth_store` (each passed
on the old code before the rewrite; the row locks of the reset-token and owner-password reads are held from a second
connection) and by `test_db_null_semantics` (below). Only `LOCK TABLE operators` — the one statement with no Core
form — is a `sa.text`. `passkey_delete` now treats only `None` as «any owner» (an empty owner deletes nothing); the
only caller passes `None` for the admin role alone, so no behavior of a real caller changed.

Stage 6 (`app/core/db.py`: booking). **6a, the requests and their state machine** — `app/core/db_booking.py`: creating a request
and reading it, the patient's own data, the referral file and its retention sweep, the staff queue lists and counts, the
approved-appointment windows, `approve` / `reject` / `cancel` / `reassign_appointment` with their `appointment_events`,
`mark_attendance` (row lock, the audit rows, the day-queue invalidation and its `NOTIFY`), the confirmation link and answer, the
two reminder sweeps and claims, the HSI re-push list, the attendance and ranking history, and `create_booking_request_with_limit`
(count-and-create under the (patient, service) advisory lock, the idempotency key). Pinned by `test_db_booking_store` (84 tests)
and `test_db_booking_flow_store` (65), written and green on the old SQL first; the row locks and the advisory-lock keys
(`slot:<service>:<time>`, the overbooking budget key, `booklimit:<dni>:<service>`) are held from a second connection and the
function must wait for them, and a function that takes a value which can be `None` is also called with `None` over rows whose
column is NULL. The exception classes (`AlreadyProcessed`, `SlotConflict`, `LimitReached`, `OverbookingCapacityReached`) stay in
`app/core/db.py`. **`booking_requests` has 40 columns**, and building a `RETURNING *` statement costs far more than the round trip
(+0.1-0.7 ms of client CPU per call when every statement was built per call, measured against the hand-written version), so in
this module nearly every statement is prepared (`_P = sqlcore.prepare`, named `bindparam()`s, module level): the CPU of the
whole module is back inside the noise of the old code (`run/sqla/stage6-bench.txt`). Dynamic SQL (`update_booking_patient_data`
sets only the keys present, `referrals_to_purge`) stays `sqlcore.run`. The static guardian of `test_formal_invariants`
(«every function that writes `status` or `appointment_at` logs an event») reads the Core `update(...).values(...)` chains in the
AST, including a statement built once at module level, instead of the text `UPDATE booking_requests`.

**6b, the admin reports** — `app/core/db_reports.py`: all 23 `reports_*` aggregations (management KPIs and medians, rejections,
volume per day, the CSV rows, the «Pacientes» sub-report, holds by day and hour, time preference, the funnel, offers, PUCO
hits, completed consultations for cost recovery / SUMAR+, the weekly demand series and the statistics rows). They are
read-only aggregations of fixed structure that run once per report view (admin only), so they use `sqlcore.run`, not
`sqlcore.prepare` (+0.2-1.0 ms of client CPU per call, ~10 ms per page view; the SQL text is the same, so the plans are).
`create_appointment_offer`, which sat in the same block, is a waitlist write and stays in `app/core/db.py`. Pinned by
`test_db_reports_store` (32 tests, green on the old SQL first: the calendar groupings are checked against an independent
Python oracle, the windows on both sides of their edge, every function over empty tables). Idioms: `percentile_cont(0.5)
WITHIN GROUP (ORDER BY x)` is `sa.func.percentile_cont(sa.literal_column("0.5")).within_group(x)`; `COUNT(*) FILTER (WHERE c)`
is `sa.func.count().filter(c)`; **Core's `/` adds a cast** (`expr / sa.literal_column("3600.0")` compiles to
`/ CAST(3600.0 AS NUMERIC)`), so a division is `expr.op("/")(sa.literal_column("3600.0"))`; `GROUP BY` an expression and
`ORDER BY` its label work with the zone inlined as a literal (`db_values._local_date`); a CTE with `generate_series` is
`select(cast(func.generate_series(a, b, interval), Date).label("mes")).cte("meses")`; `LIMIT 500` stays literal
(`.limit(sa.literal_column("500"))`, a plain `.limit(500)` binds it). Compare the compiled SQL of every converted function
with the old text (a spy on `sqlcore.compile_statement`): it is how the extra `CAST` was found, result tests alone would not.

**6c, the slots, the day queue and the counters** — `app/core/db_slots.py` (29 functions): the slot holds a patient takes while
choosing a time (`hold_acquire` — the move-semantics delete, the sweep of expired loose holds and the `INSERT ... ON CONFLICT
(service_id, day, time) DO UPDATE ... WHERE expires < now() OR token = excluded.token RETURNING id` that decides the race,
fail-CLOSED on `DbUnavailable` —, `hold_release`, `holds_active`, the link/release/lookup of a request's hold,
`active_booking_request_by_slot`), the durable day-queue events behind the SSE stream, the waitlist lookups, the booking-limit
count (`count_active_requests`, `_count_active_requests_cur`, which `create_booking_request_with_limit` calls on its own
cursor) and the other counters (the «disable, never delete»
checks of a service, center or doctor; the holiday counts), the reads of the day queue (`list_approved_day`,
`approved_calendar_groups`, `live_requests_of`, `get_booking_request_by_idem_key`, `interviews_by_dni`), the dependents
(upsert included) and the nightly purge of summarized chat messages. `slot_key` and `DOCTOR_ID_COLUMNS` stay in `app/core/db.py`;
`_DOCTOR_REQUEST_MATCH`, a SQL string with four positional parameters, became the Core predicate
`db_slots._doctor_request_match(id_, nombre)`. Pinned by `test_db_slots_store` (153 tests, green on the old SQL first, 116/116
lines of the module): the `None` of every lookup over rows whose columns are NULL, the filter combinations of the counters, the
`DbUnavailable` contract of every function that opens a connection, and the race of two and of ten tokens for one slot (fresh,
expired, the same token twice) with exactly one winner per round. Idioms: every statement of fixed structure is prepared at
import (the holds, the day queue, the limit count, the dependents), and a prepared statement keeps the
old meaning of a `None` (`col = NULL`); **a bind parameter never carries the name of a column the INSERT/UPDATE sets** (`token`,
`dni`) — SQLAlchemy reserves those names for the VALUES/SET clause (the holds use `tok`, the dependents upsert `dni_`);
`to_char(day, 'YYYY-MM-DD')` takes its format as `sa.literal_column`, not a bound string; `status = ANY(%s)` is
`status == sa.any_(sa.bindparam("estados", type_=ARRAY(Text)))`; `SELECT EXISTS (...) AS hay` is
`sa.select(sa.exists(sa.literal_column("1")).where(...).label("hay"))` (label the `exists`, not the `select`);
`ORDER BY col NULLS LAST` is `col.asc().nulls_last()`; a literal `LIMIT 1` stays `.limit(sa.literal_column("1"))`. The trap,
pinned: `active_booking_request_by_slot` gets `(None, None)` from `slot_key` for an impossible slot, and `h.day = NULL` over the
`LEFT JOIN slot_holds` matches nothing where `h.day IS NULL` would match every request of the patient that has no hold — the
test includes a request with no hold, since rows that all have one would pass either way. Client CPU against the hand-written
version (alternating runs): 0-17 µs more per call, the plans from `EXPLAIN (ANALYZE)` identical.

**Stage 7, the rest: scripts, maintenance tools, the queue plumbing and the data-subject tools.** After this stage
`.execute(` / `.executemany(` appears in exactly two shipped files: `app/core/sqlcore.py` (the executor) and
`scripts/tools/msd_pull.py` (a SQLite cookie store of the developer's Chrome, not our database).
**`tests/test_raw_sql_ratchet.py` keeps it that way** — it scans the shipped Python (not `tests/`, `alembic/`)
by AST, and a NEW file with a raw `.execute(` is red at once; the allowance of the two files above can only be lowered.
What moved: the demo seed `scripts/seed_demo_all.py` (it runs on every deploy of the demo; the snapshot of every table after
`seed(force=True)` at a frozen clock is identical to the old code's), the data-subject tools `app/core/subject_requests.py`,
`app/core/subject_export.py`, `app/core/subject_erase.py` (with `personal_data.where_clause`, the registry selectors as a Core predicate;
`where_sql` stays as the readable text and as the oracle the tests compare the predicate with), the maintenance scripts
(`medico_por_id`, `medico_de_web`, `fixes/report_consultas_duplicadas`, `fixes/report_fechas_texto`, `fixes/validate_foreign_keys`,
`fixes/purge_renaper_account_copies`, `db_app_role`, `schema_snapshot`), `worker._db_ok`, and the queue plumbing — the advisory
locks of `queue_infra`, `hsi_journal_queue`, `data_backups` and `data_backups_queue`, the `procrastinate_jobs` / `procrastinate_events`
reads of the two self-rescheduling chains (`queue_infra.task_chain_alive`, `_next_schedule_at`) — and the `LISTEN` of the day-queue
stream (`sqlcore.run_async`, `run` for a psycopg `AsyncConnection`). Four scripts were deleted instead of converted:
`seed_demo_cola`, `seed_demo_solicitudes`, `seed_demo_solicitudes_multi` and `normalizar_telefonos` wrote to the tables
`solicitudes` / `usuarios`, which the schema Anglicization removed — they could only fail; `seed_demo_all.py` replaced the seeds
(history stays in git). Pinned on the old code first: `test_scripts_sql_pins` (the guarded UPDATEs of both `medico_*` scripts, the
DNI lookup of the RENAPER purge, the worker probe), `test_queue_sql_pins` (lock KEYS held from a second connection, both chains'
`chain_alive` / `_next_schedule_at` including the cold start and the clamp to now, the sweep lock), `test_subject_store_pins`,
`test_seed_demo_store`, and in `test_db_app_role` a password full of `'`, `%`, `:`, `"`, `\` and `{}`.
Shared pieces: `db_locks._advisory_lock` / `_try_advisory_lock` / `_try_advisory_xact_lock` / `_advisory_unlock` (session and
transaction locks over `hashtext(<bound key>)`; the test seam that breaks an unlock passes a function name that does not exist),
and in `schema_model.external` the catalog tables the scripts read (`information_schema.columns`, `pg_roles`, `pg_constraint`)
and the queue library's two tables (`procrastinate_jobs`, `procrastinate_events`) — queryable, never created by the model.
Idioms and traps of the stage:
- **`ALTER ROLE ... PASSWORD` cannot take a bind parameter and `text()` reads `:name` and `%` in a literal.** The password travels
  as the value of a transaction-local setting (`set_config(..., true)` with a bound parameter) and `format('%L')` writes the literal
  inside the server (`DO $$ ... EXECUTE format('ALTER ROLE %I PASSWORD %L', ...) $$`), so it is never part of any SQL text and neither
  the client nor the server log can carry it. The other utility statements (`GRANT`, `REVOKE`, `ALTER ROLE`) are `sa.text` with every
  `:` escaped (`\:`); `sa.text` doubles a `%` and psycopg undoes it because `sqlcore.run` always passes a parameter dict.
- **`sa.text()` with `:name::type` is unsafe**: the bind regex reads `:dn` out of `:dni::text`. Build casts with
  `sa.cast(sa.literal(v, sa.Text), sa.Text)`. Catalog queries that are SQL by nature (`pg_class`, `pg_get_*def`) are `sa.text` with named
  `bindparams` (`schema_snapshot`); a `%` inside them is written once.
- **`~` and `||` share a precedence level in PostgreSQL**: a pattern built with `||` on the right of `~` needs `Grouping(...)`.
- **A `DELETE`/`UPDATE` whose sub-select reads the same table** needs `.correlate(None)`, or Core drops the inner `FROM`.
- `DISTINCT ON (col)` is `select(...).distinct(col)`; `CASE WHEN max(x) IS NULL THEN NULL ELSE ... END` is `sa.case((m.is_(None), sa.null()), else_=...)`;
  `IS NOT DISTINCT FROM` (`a.is_not_distinct_from(b)`) keeps its NULL-matches-NULL meaning for a `None` value — it is not the `col == None` trap;
  a table name that comes from a registry is `schema_model.metadata.tables[name]`, a column of it `table.c[name]`.
- `ALTER TABLE ... VALIDATE CONSTRAINT` is `sqlcore.run_ddl(cur, sa.DDL(...))` with both names through `sqlcore.quote_identifier`.
  `run_ddl` sends its text with no parameters, so a statement that may carry a `%` or `:` is not a candidate (the password above is why).

**A `None` that must find nothing.** The hand-written `col = %s` with a Python `None` is `col = NULL`: never true. Core
renders `col == None` as `col IS NULL`, which matches every row whose column is NULL — an account without a feed
token, an operator without a doctor link, a challenge without a username. For a NULLABLE column compared with a value
that can be `None`, bind the value: `col == sa.literal(value, sa.Text)` (or `db_operators._eq`); for a NOT NULL column
the two spellings give the same rows. `test_db_null_semantics` calls every lookup and write of the stage with each
argument replaced by `None`, over rows whose nullable columns are NULL, and compares the outcome with what the old SQL
gave (recorded from it); a bare `== None` in a converted statement makes it red.

**Statements built once** (`sqlcore.prepare`). Building a Core statement and keying it for the compiled cache costs
30-60 µs on the client, most of what a one-row lookup costs. The reads that run on EVERY request — the panel's
operator row and its 2FA deadline, the patient site's session and user — are built at import time with named
`bindparam()`s and run with `STATEMENT.run(cur, name=value)`: the same SQL and bind processors as `sqlcore.run`,
none of the per-call build. Because the structure is fixed, a `None` value is `col = NULL` (finds nothing), exactly
what the hand-written SQL did and the `IS NULL` trap above cannot arise. Use it for a hot lookup whose shape never
changes; everything else stays `sqlcore.run`.

**`app/core/db.py` is split by domain as it is converted.** The converted groups moved out of it:
`app/core/db_ai.py` (prompts, cost ledger, MCP budget, AI register), `app/core/db_backup.py` (backup journal,
versions, tombstones, purged files, the demo-refresh marker), `app/core/db_catalog.py` (catalog overlay and its
schedule lock, translations, legal texts, REFEPS cache), `app/core/db_analytics.py` (searches, login-panel
events, daily alert claims), `app/core/db_chat.py` (chat and the WhatsApp phone resolution), `app/core/db_hsi.py` (the
HSI journal), `app/core/db_clinical.py` (interviews, consultations, labs, history, the access log), `app/core/db_accounts.py`
(patient accounts, sessions, OTP, tokens), `app/core/db_booking.py` (booking requests and their state machine),
`app/core/db_operators.py` (panel operators), `app/core/db_reports.py` (admin report aggregations), `app/core/db_slots.py` (slot holds, day queue, counters, dependents), `app/core/db_auth.py` (owner security,
passkeys, admin preferences and reset tokens). `app/core/db.py` is the connection and nothing else (the pool,
`_connect`, the web timeouts, `migrations_status`: `test_db_domain_modules.CONNECTION_API`; the cut was done in stages on
2026-10-30, hospital TASK-57). A name that is not the connection is reached through the module that owns it, at
call time: `db_booking.approve(...)`, never `db.approve(...)`, so a patch on `db_booking.approve` is seen by
every caller, and a test that stubs a storage function by name finds its owner with `tests/_dbowner.py`. Under the domain
modules sit four layers: `app/core/db_values.py` (the installation timezone, the
coercions, `_days_ago`, `_interval`, `_jsonb`, `_last_ten_digits`, `_local_date`, `_text_value`),
`app/core/db_wire.py` (`_WIRE_BACK`, `_wire`, `_wire_all`), `app/core/db_locks.py` (`_lock_key`, the advisory locks) and
`app/core/db_schema.py` (the baseline text, the schema owner settings, `_ensure_schema` and the Alembic and bridge steps,
the trigram indexes and the built-in roles), with `db_values` < `db_wire` < `db_locks` < `db_schema` < `db`: `db` calls
them, never the reverse. There is no re-export: `tests/test_db_domain_modules.py` forbids every way of reaching a name through
`db` that another module owns (an alias, `from core.db import x`, `getattr`/`patch.object`, `patch("core.db.x")`, a
`(db, "x")` pair, a computed name, importing it by its string name), pins that `db` defines only the connection API and
imports no domain module, that no import cycle exists among the modules (top of a file or inside a function) and a lower
layer never imports a higher one, that a module is reached through the module and not `from core.db_x import f`, one owner
per name, that any module can be imported first, and a ceiling (that only goes down) on the module `db` handed over as a value.

Idioms that came out of the conversion:

- **Advisory locks** are `func.pg_advisory_xact_lock(func.hashtext(<key>))` through `db_locks._advisory_xact_lock(cur, "ai_prompt:", db_values._text_value(key))`:
  a `str` piece is a constant of the key and stays a SQL literal, a bound value is `db_values._text_value(x)`.
  The KEY is the contract (a second process holds the same one), so `test_db_bootstrap_store.TestLockKeys`
  holds each key from a second connection and checks the function waits for it — and does not wait for
  another key.
- **`SAVEPOINT`, `RELEASE`, `ROLLBACK TO`** are `sa.text("...")` run with `sqlcore.run` on the caller's cursor.
- **DDL** is a Core construct (`sa.DDL`, `CreateIndex(..., if_not_exists=True)`) executed with
  `sqlcore.run_ddl(cur, ddl)`. An optional index that the model leaves out on purpose (the trigram
  indexes) is described on a throwaway `Table`, never on the model's, or `create_all` and
  `alembic check` would start to know it.
- **Tables the model does not own**: `alembic_version` is `schema_model.alembic_version` (the
  `external` metadata); a catalog table such as `pg_database` is a lightweight `sa.table(...)` next to
  the one function that reads it.
- **A SQL constant stays a literal** (`sa.literal_column("'success'")`, `sa.false()`), as it was in the
  hand-written text; a Python value is a bound parameter. A timezone name inlined into a `GROUP BY`
  expression must be a literal too, or Postgres cannot match the select-list expression to the group
  expression.
- **A column named like a method of the column collection is shadowed**: `refeps_cache.items` is
  `t.c["items"]` (and `excluded["items"]`), `t.c.items` is the collection's own `items()`.
- A JSONB value is passed as the Python value (`doc=doc`), not wrapped in `Json(...)`: the column type's
  bind processor is what serializes it. A JSON `None` is stored as the JSON `null`, as before.
- **A nullable JSONB column that took `None` as SQL NULL needs `sa.null()`**: a bare `None` becomes the JSON
  `null` value (`Jsonb(None)`), which `IS NULL` does not see. `db_values._jsonb(value)` is the spelling for a column
  that used to get `_coerce_jsonb(value)`; `db_hsi._doc(value)` also cleans datetimes. A NOT NULL document that
  took `Json(None)` keeps the bare `None` (the JSON `null`, as before).
- **A span from `now()`** is `db_values._days_ago(n)` or `sa.func.now() - db_values._interval(n, "hour")` (`n * interval '1 hour'`):
  the same value as `make_interval(hours => n)` or `(n || ' hours')::interval`, without a cast of the parameter.
- **A phone lookup uses `db_values._last_ten_digits(column)`**, not `schema_model.last_ten_digits`: the same expression
  with inline literals, which is what lets Postgres match the expression indexes (`chat_messages_whatsapp_tel_idx`,
  `users_contact_tel_idx`; checked with `EXPLAIN`). The partial index also needs `channel = 'whatsapp'` as a
  literal (`sa.literal_column("'whatsapp'")`).
- **A batch is `sqlcore.run_many(cur, [insert(t).inline().values(...), ...])`** (`executemany` of one compiled
  statement; `inline()` keeps SQLAlchemy from adding the `RETURNING <key>` nobody reads in a batch).
- **A partial index keeps its literal**: `interviews_red_alert_idx` is `WHERE triage = 'ROJO' AND alert_seen_at IS
  NULL`, so the red-alert queries write `'ROJO'` with `sa.literal_column`, as `'whatsapp'` and `'aprobada'` stay.
- **A scalar subquery over the table being updated** (`UPDATE chat_messages ... WHERE id = (SELECT id FROM
  chat_messages ...)`) selects from an alias of the table, or SQLAlchemy correlates it away.
- **Every INSERT into a table with a generated key carries `RETURNING <key>`** unless the statement binds the
  key itself: that is SQLAlchemy's implicit key fetch, nothing reads it. It cannot be switched off per table
  (the INSERT would then send `id = NULL`).

## Moved out of CLAUDE.md: schema authority and the converted `core/db_<domain>.py` modules

Former `CLAUDE.md` wording, moved here on 2026-11-06 to keep that file under its size limit:

> (`db_ai`, `db_backup`, `db_catalog`, `db_analytics`, `db_chat`, `db_clinical`, `db_hsi`, `db_accounts`, `db_operators`, `db_auth`, `db_booking`, `db_reports`, `db_slots`)
