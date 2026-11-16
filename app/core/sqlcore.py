"""core/sqlcore.py — run SQLAlchemy Core statements on the cursor the code already has.

New query code is written with SQLAlchemy Core (`select`, `insert`, `update`,
`delete` over the tables of `core/schema_model.py`) instead of SQL text. It does
NOT get a second connection or a second pool: a Core statement is compiled to SQL
text plus parameters for the psycopg 3 dialect, and executed on the psycopg
cursor the caller already holds.

    from sqlalchemy import select
    from core import db, sqlcore
    from core.schema_model import waitlist

    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, select(waitlist).where(waitlist.c.id == 7)).fetchone()

That is the whole contract, and what it keeps unchanged:
  - the connection is `db._connect()`'s pooled psycopg connection: checkout,
    health check, `DbUnavailable`, and the commit-on-exit / rollback-on-error of
    its `with` block stay exactly as they are;
  - the transaction is the caller's: a Core statement and a hand-written
    `cur.execute(...)` in the same `with` block share one transaction, and a
    caller that gets a `conn` (or a `cur`) passed in runs its statements on it;
  - rows come back as the dicts the pool's `dict_row` factory builds, keyed by the
    column name (or the label), so callers and `db_wire._wire` see what they saw before;
  - JSON/array/date parameters are adapted by the SAME code path SQLAlchemy uses
    (`_bind_processors`), so `JSONB` parameters are serialized the way a Core
    `insert` expects.

Not here on purpose: no SQLAlchemy `Engine`, `Session` or `Connection` is ever
created for the application's queries. Alembic's `env.py` is the only place an
engine exists, and it is a separate, NullPool connection used by a migration.

The price of compiling outside an `Engine`: `compile_statement` and `Prepared`
call three private SQLAlchemy members (`_compile_w_cache`,
`_process_parameters_for_postcompile`, `_bind_processors`) and one private
keyword (`construct_params(_no_postcompile=True)`). `requirements.txt` pins
SQLAlchemy to `SQLALCHEMY_VERIFIED`, and `tests/test_dependency_pins.py` checks
both the pin and the shape of each of those names on the installed release, so
an upgrade that moves them is a red test, not a wrong query.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import psycopg as _psycopg_dialect
from sqlalchemy.engine.interfaces import BindTyping
from sqlalchemy.util import LRUCache

# The SQLAlchemy release the private calls below were checked on; `requirements.txt` pins the same one
# (tests/test_dependency_pins.py). Moving the pin means re-running that test on the new release first.
SQLALCHEMY_VERIFIED = "2.0.51"


# One dialect object, shared: it only decides how SQL text and parameters are
# written (`%(name)s` placeholders); it never connects. SQLAlchemy's psycopg
# dialect wraps every parameter in a cast (`%(id_1)s::INTEGER`); the code base's
# hand-written SQL never did, and psycopg's own adaptation of the Python value
# is what the database has always received, so the casts are switched off to keep
# a converted query's wire behavior identical.
_DIALECT = _psycopg_dialect.dialect()
_DIALECT.bind_typing = BindTyping.NONE


# Compiled statements by structure. Building and compiling a statement costs
# 0.3-0.4 ms on the code base's statements (a six-column insert with a JSONB
# value measured 0.35 ms, the round trip to Postgres being of the same order);
# SQLAlchemy's own engine avoids it with exactly this cache, keyed by the
# statement's structure with the parameter VALUES taken out, so one compiled
# object serves every call that differs only in values. 500 entries is the engine's
# default size; the least recently used one goes first.
_COMPILED_CACHE = LRUCache(500)


def compile_statement(stmt):
    """`(sql, params)` for `stmt`, ready for `cursor.execute(sql, params)`.

    `IN` lists are expanded into one placeholder per value (an empty list becomes
    a predicate that matches nothing), and every parameter goes through the
    processor SQLAlchemy would apply to it at execution time (JSON serialization,
    for one). The compiled SQL is cached by the statement's structure; the values
    of the statement being run are read off it for every call."""
    # The steps of SQLAlchemy's own execution: compile (or find in the cache) the
    # statement, take THIS call's parameter values out of it, expand the `IN`
    # lists (which renames their parameters: `value_1` -> `value_1_1`, `value_1_2`)
    # and run each parameter through its type's bind processor, the expanded ones
    # included. A statement without a cache key (a construct that cannot be keyed)
    # is simply compiled every time.
    compiled, extracted, _ = stmt._compile_w_cache(
        dialect=_DIALECT, compiled_cache=_COMPILED_CACHE, column_keys=[])
    state = compiled._process_parameters_for_postcompile(compiled.construct_params(
        extracted_parameters=extracted, escape_names=True, _no_postcompile=True))
    processors = compiled._bind_processors
    if state.processors:
        processors = dict(processors)
        processors.update(state.processors)
    params = {key: processors[key](value) if key in processors else value
              for key, value in state.parameters.items()}
    return state.statement, params


class Prepared:
    """A statement built and compiled ONCE, at import time, for the paths that run on every request.

    Building a Core statement and keying it for the compiled cache costs 30-60 µs per statement, which is
    most of what a one-row lookup costs on the client; a prepared statement pays it once. It is written
    with named `bindparam()`s and run with `.run(cur, name=value, ...)`. The parameters keep the meaning
    the hand-written SQL gave them: the structure is fixed, so a `None` stays `col = NULL` (it never
    matches), never the `IS NULL` that a `col == None` built per call would be. The values go through the
    same bind processors as `compile_statement`'s. `IN` lists (expanding bind parameters) are supported;
    a statement that needs per-call structure is not prepared, it uses `run`."""

    def __init__(self, stmt):
        self._compiled = stmt.compile(dialect=_DIALECT, column_keys=[])

    def render(self, **values):
        compiled = self._compiled
        state = compiled._process_parameters_for_postcompile(compiled.construct_params(
            params=values, escape_names=True, _no_postcompile=True))
        processors = compiled._bind_processors
        if state.processors:
            processors = dict(processors)
            processors.update(state.processors)
        return state.statement, {key: processors[key](value) if key in processors else value
                                 for key, value in state.parameters.items()}

    def run(self, cur, **values):
        sql, params = self.render(**values)
        cur.execute(sql, params)
        return cur

    def run_many(self, cur, rows):
        """`cursor.executemany` of the statement over `rows` (one dict of its bind values each): a
        bulk insert of thousands of rows without compiling a statement per row. Returns the cursor."""
        rendered = [self.render(**row) for row in rows]
        if not rendered:
            return cur
        sql = rendered[0][0]
        if any(other != sql for other, _ in rendered):
            # an expanding IN list of another length renders other SQL
            raise ValueError("run_many needs rows that render the same statement")
        return _executemany(cur, sql, [params for _, params in rendered])


def _executemany(cur, sql, params_list):
    cur.executemany(sql, params_list)
    return cur


def prepare(stmt):
    """`stmt` (with named `bindparam()`s) as a `Prepared` statement; see `Prepared`."""
    return Prepared(stmt)


def at_zone(column, zone):
    """`<column> AT TIME ZONE '<zone>'` with the zone name inlined as a literal (not a bound parameter), so a
    SELECT-list expression and the GROUP BY that repeats it are the same text for Postgres."""
    return column.op("AT TIME ZONE")(sa.literal_column("'%s'" % zone))


def quote_identifier(name):
    """`name` quoted as a PostgreSQL identifier, for the few statements that name an object
    the database cannot take as a parameter (`CREATE DATABASE`)."""
    return _DIALECT.identifier_preparer.quote(name)


def run(cur, stmt):
    """Executes the Core statement `stmt` on the psycopg cursor `cur`; returns the
    cursor, so a caller chains `.fetchone()`, `.fetchall()` or reads `.rowcount`
    exactly as after a `cur.execute(...)`."""
    sql, params = compile_statement(stmt)
    cur.execute(sql, params)
    return cur


async def run_async(conn, stmt):
    """`run` for a psycopg `AsyncConnection` (the one place that has no cursor to hold: the
    `LISTEN` connection of the day-queue stream). Awaits the execution and returns the cursor."""
    sql, params = compile_statement(stmt)
    return await conn.execute(sql, params)


def run_many(cur, stmts):
    """`cursor.executemany` of Core statements of ONE structure: the same SQL text with different
    values (a list of `insert(t).values(...)` for a batch). The SQL is compiled once; every
    statement contributes its own parameters. Statements that do not compile to the same SQL are a
    programming error. Returns the cursor."""
    compiled = [compile_statement(stmt) for stmt in stmts]
    sql = compiled[0][0]
    if any(other != sql for other, _ in compiled):
        raise ValueError("run_many needs statements of the same structure")
    return _executemany(cur, sql, [params for _, params in compiled])


def run_ddl(cur, ddl):
    """Executes a DDL construct (`CreateIndex(...)`, `DDL("...")`, `CreateTable(...)`)
    on the psycopg cursor `cur`, compiled for the same dialect as `run`. DDL carries
    no parameters and is not cached; it is for the boot-time helpers that create
    optional objects. Returns the cursor."""
    cur.execute(str(ddl.compile(dialect=_DIALECT)))
    return cur
