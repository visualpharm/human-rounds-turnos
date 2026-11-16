"""core/db_schema.py — how the schema of a database is brought to head: the frozen baseline text, the settings that
point at the database and the schema owner, the refusal of a pre-Alembic database, the refusal of a revision this code does not know, the Alembic upgrade, the optional trigram indexes and the
built-in roles, all run once per database by `db._connect()` through `_ensure_schema`.

Split out of `core/db.py` (the DB split of 2026-10-30): it imports no other `core/db_*.py` except `db_locks`, and
`core/db.py` calls it, never the other way round.
"""

import logging
import os
import threading

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import config as _config
from app.core import paths
from app.core import db_locks
from app.core import log_pii
from app.core import schema_model as sm
from app.core import settings as _settings
from app.core import sqlcore

ROOT = paths.PROJECT_ROOT

_lock = threading.Lock()
_ensured = set()  # URLs cuyo esquema ya garantizamos en este proceso
# URL -> UnknownRevision: databases this process refused because their Alembic stamp
# names a revision the checkout has no script for (2026-11-06). The decision is taken
# once, under the migrations lock; every later connection re-reads the stamp alone
# (one SELECT, no lock, no Alembic) and is refused again while it is unchanged.
_refused = {}
_log = logging.getLogger("turnos.schema")


class UnknownRevision(RuntimeError):
    """The database is stamped with an Alembic revision this code does not know:
    a newer release migrated it and an older one is now running against it."""

    def __init__(self, stored, head):
        self.stored = tuple(stored)
        self.head = head
        super().__init__(
            "La base está en la revisión de Alembic %s, que esta versión del sistema no conoce "
            "(la más nueva que conoce es %s): la migró una versión más nueva. No se atiende "
            "hasta resolverlo: desplegá de nuevo esa versión (o una posterior), o restaurá el "
            "respaldo de antes de esa migración. Bajar la base de revisión se hace con la "
            "versión nueva (alembic downgrade), nunca con esta. docs/UPGRADING.md"
            % (", ".join(self.stored), head))


# The schema's FIRST Alembic revision, frozen (2026-10-25). It is the text
# `_SCHEMA_SQL` used to be, byte for byte, now a file next to the revisions so
# nobody edits it by accident: `test_alembic_revisions.TestBaselineFrozen` pins
# its hash. A schema change is a NEW revision under `alembic/versions/` and
# nothing else -- see docs/architecture/deploy.md. `0001_baseline.py` reads the
# same file.
BASELINE_FILE = os.path.join(ROOT, "alembic", "baseline", "0001_schema.sql")


def _read_baseline():
    with open(BASELINE_FILE, encoding="utf-8", newline="") as f:
        return f.read()


BASELINE_SQL = _read_baseline()


def _load_env_file():
    """Bootstrap values from config.py's one effective environment file.

    ``TURNOS_ENV_FILE`` therefore controls database bootstrap as well as the
    rest of runtime configuration.  Do not add a second checkout-``.env``
    reader here: the managed executor deliberately points this at an empty
    file for hermetic local profiles.
    """
    return _config.dotenv_values()


def _migration_url():
    """`TURNOS_DB_MIGRATION_URL`: the schema owner's login, used ONLY to bring
    the schema to head at boot (Alembic) and for the optional trigram index.
    Unset = `DATABASE_URL` does both, as always. Set it when the app runs as
    a role without DDL rights (scripts/db_app_role.py, docs/architecture/
    registro-de-accesos.md): the app then cannot alter the schema it serves.
    Read like `DATABASE_URL` (`core.settings.DatabaseSettings`)."""
    return _settings.DatabaseSettings.current().migration_url


def _owner_conn_same_db(conn, owner_url):
    """A connection as the schema owner, refusing a URL that points at another
    database than the app's own — a typo there would migrate the wrong one.
    Same name is not enough (two servers can each have a `turnos`): the
    database's oid and the server's start time must match too — both
    readable by any role."""
    import psycopg
    from psycopg.rows import dict_row
    probe = sa.select(
        sa.func.current_database().label("d"),
        sa.cast(sa.select(_pg_database.c.oid).where(
            _pg_database.c.datname == sa.func.current_database()).scalar_subquery(),
            sa.Text).label("o"),
        sa.cast(sa.func.pg_postmaster_start_time(), sa.Text).label("s"))
    owner = psycopg.connect(owner_url, row_factory=dict_row, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            mine = dict(sqlcore.run(cur, probe).fetchone())
        with owner.cursor() as cur:
            theirs = dict(sqlcore.run(cur, probe).fetchone())
        if mine != theirs:
            raise RuntimeError(
                "TURNOS_DB_MIGRATION_URL points at another database (%r) than "
                "DATABASE_URL (%r)" % (theirs["d"], mine["d"]))
    except Exception:
        owner.close()
        raise
    return owner


def _ensure_schema(conn, url):
    if url in _ensured:
        return
    _raise_if_still_refused(conn, url)
    with _lock:
        if url in _ensured:
            return
        _raise_if_still_refused(conn, url)
        # Cross-PROCESS lock (2026-08-05 tier 14 fix), held on THIS
        # connection/session across the whole bootstrap: the pre-Alembic
        # refusal and the Alembic upgrade. Against a genuinely empty
        # database, two processes bootstrapping at the same time (two
        # replicas of one deploy hitting a freshly created database) could
        # otherwise both reach the DDL together: Postgres does not treat
        # `IF NOT EXISTS` as atomic across concurrent sessions, so one
        # process would crash with `UniqueViolation: duplicate key value
        # violates unique constraint "pg_type_typname_nsp_index"` instead of
        # finishing clean.
        with conn.cursor() as cur:
            sqlcore.run(cur, sa.select(sa.func.pg_advisory_lock(
                sa.cast(db_locks._lock_key("turnos:migrations"), sa.BigInteger))))
        try:
            _refuse_pre_alembic(conn)
            _upgrade_to_head(conn, url)
        finally:
            with conn.cursor() as cur:
                sqlcore.run(cur, sa.select(sa.func.pg_advisory_unlock(
                    sa.cast(db_locks._lock_key("turnos:migrations"), sa.BigInteger))))
            conn.commit()
        owner_url = _migration_url()
        if owner_url:
            with _owner_conn_same_db(conn, owner_url) as owner:
                _apply_trigrams(owner)
        else:
            _apply_trigrams(conn)
        _seed_builtin_roles(conn)
        _ensured.add(url)


_known = {}  # "revisions" -> frozenset of every revision id of this checkout, "head" -> its head


def _known_revisions():
    """(every revision id the checkout has a script for, the head) — read once from
    `alembic/versions/`, the same script directory `_run_alembic_upgrade` uses."""
    if not _known:
        from alembic.script import ScriptDirectory
        script = ScriptDirectory.from_config(_alembic_config("postgresql://"))
        _known["revisions"] = frozenset(s.revision for s in script.walk_revisions())
        _known["head"] = script.get_current_head()
    return _known["revisions"], _known["head"]


def _stored_revisions(conn):
    """The revision ids stamped in `alembic_version`, sorted; () when the table is missing."""
    with conn.cursor() as cur:
        if not sqlcore.run(cur, sa.select(
                sa.func.to_regclass(sa.literal_column("'public.alembic_version'"))
                .is_not(None).label("has_av"))).fetchone()["has_av"]:
            return ()
        return tuple(sorted(row["version_num"] for row in sqlcore.run(
            cur, sa.select(sm.alembic_version.c.version_num)).fetchall()))


def _raise_if_still_refused(conn, url):
    """A database this process already refused is refused again, with one SELECT and
    no lock, while its stamp is the one refused; a changed stamp (a restore, the newer
    release rolled the schema back) drops the refusal and the normal boot runs."""
    refused = _refused.get(url)
    if refused is None:
        return
    stored = _stored_revisions(conn)
    conn.rollback()
    if stored == refused.stored:
        raise UnknownRevision(refused.stored, refused.head)
    _refused.pop(url, None)


def _refuse_unknown_revision(conn, url):
    """Refuses, before any DDL, a database stamped with a revision this checkout does
    not know. Alembic would fail there anyway ("Can't locate revision"), but only after
    the lock and the environment load, on every connection, with a message that does
    not say what happened; this names the revision, the newest one known and the way out,
    logs it once and remembers it for the process."""
    stored = _stored_revisions(conn)
    known, head = _known_revisions()
    if not stored or all(r in known for r in stored):
        return
    refusal = UnknownRevision(stored, head)
    _refused[url] = refusal
    _log.error("%s", refusal)
    raise UnknownRevision(stored, head)


# Tables whose presence WITHOUT an `alembic_version` row marks a database from
# before Alembic (2026-08-06): the old numbered runner's bookkeeping table, the
# base table under its English name, and every Spanish-era table name the old
# migration 001 renamed. A fresh database has none of them; an Alembic database
# has its `alembic_version` row and is never looked at.
PRE_ALEMBIC_TABLES = (
    "schema_migrations", "booking_requests",
    "solicitudes", "operadores", "usuarios", "dependientes", "sesiones", "catalogo",
    "asistencia_eventos", "admin_preferencias", "otp_codigos", "otp_verificados",
    "lista_espera", "ofertas_turno", "busquedas", "turno_holds", "costos_ia",
    "panel_login_eventos", "chat_mensajes", "prompts_ia", "prompts_ia_historial",
)

# The git tag of the last release that still carries the one-time bridge from
# the pre-Alembic schema (the `migrations/` package, removed 2026-11-05).
BRIDGE_RELEASE = "bridge-last"

_information_tables = sa.table(
    "tables", sa.column("table_schema"), sa.column("table_name"), schema="information_schema")


def _refuse_pre_alembic(conn):
    """Refuses to boot a database that predates Alembic.

    Every known installation went through the bridge long ago; such a database
    can only come back from an old dump. Replaying the baseline over it would
    create empty English tables next to the Spanish ones that hold the data, so
    the boot stops before any DDL and names the way through: one boot with the
    `BRIDGE_RELEASE` image converts it, then this release takes it from there
    (docs/UPGRADING.md, "A database from before Alembic")."""
    if _alembic_version_present(conn):
        return
    t = _information_tables
    with conn.cursor() as cur:
        present = sorted(row["table_name"] for row in sqlcore.run(cur, sa.select(t.c.table_name).where(
            t.c.table_schema == "public", t.c.table_name.in_(PRE_ALEMBIC_TABLES))).fetchall())
    if present:
        raise RuntimeError(
            "this database predates Alembic (tables %s, no alembic_version row): boot it once with "
            "the release tagged %r, which converts it, then with this one -- docs/UPGRADING.md"
            % (", ".join(present), BRIDGE_RELEASE))


def _upgrade_to_head(conn, url):
    """Alembic `upgrade(head)` (as the schema owner when one is configured) on its
    own connection, after closing `conn`'s read transaction."""
    _refuse_unknown_revision(conn, url)
    # Close `conn`'s read transaction BEFORE Alembic runs: Alembic upgrades on
    # its OWN connection, and an open transaction here would hold AccessShare
    # locks the upgrade's DDL waits on while this session waits for Alembic --
    # a mutual deadlock. The advisory lock is session-level, not
    # transaction-level, so committing here keeps holding it.
    conn.commit()
    # With a schema owner configured (TURNOS_DB_MIGRATION_URL) Alembic
    # runs as the owner; the advisory lock `_ensure_schema` took is database-wide, so
    # it still serializes every process whatever role each one uses.
    owner_url = _migration_url()
    if owner_url:
        _owner_conn_same_db(conn, owner_url).close()
        conn.commit()
    _run_alembic_upgrade(conn, owner_url or url)


def _sqlalchemy_url(url):
    """`DATABASE_URL` here is `postgresql://...` — psycopg3 itself accepts
    that scheme directly, but SQLAlchemy defaults a bare `postgresql://` to
    the psycopg2 dialect, which this repo never installs (psycopg3-only,
    see the module docstring). Force the `+psycopg` suffix so SQLAlchemy
    picks the SAME driver `db.py` already uses everywhere else."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _alembic_config(url):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    cfg.set_main_option("sqlalchemy.url", _sqlalchemy_url(url))
    return cfg


def _alembic_version_present(conn):
    with conn.cursor() as cur:
        if not sqlcore.run(cur, sa.select(
                sa.func.to_regclass(sa.literal_column("'public.alembic_version'"))
                .is_not(None).label("has_av"))).fetchone()["has_av"]:
            return False
        return sqlcore.run(cur, sa.select(sm.alembic_version.c.version_num).limit(1)
                           ).fetchone() is not None


def _run_alembic_upgrade(conn, url):
    """Brings the schema to Alembic's `head`. Called from `_ensure_schema`
    with `conn`'s cross-process `pg_advisory_lock` already held — Alembic
    runs on its OWN separate connection/engine (`alembic/env.py`), which is
    still fully protected: every OTHER process reaching `_ensure_schema` at
    the same time blocks on `pg_advisory_lock` itself, before it ever gets
    here, so nobody else can touch the schema while this function runs.

    A no-op at head, the baseline's DDL and then every revision on a fresh
    database, and exactly the pending forward revisions on an existing one.
    The baseline body never re-runs against an existing schema: a database
    with tables but no `alembic_version` row was refused just before."""
    from alembic import command

    command.upgrade(_alembic_config(url), "head")


# La búsqueda de pacientes del mostrador (`admin_patients`) es
# ILIKE '%texto%': con el comodín adelante ningún btree entra, y pg_trgm es
# exactamente el índice para eso. Va APARTE del schema principal y en su propia
# transacción porque `CREATE EXTENSION` pide permisos que un Postgres
# administrado puede no dar: si falla, la búsqueda sigue funcionando (escanea),
# y lo que NO puede pasar es que el arranque entero se caiga por un índice
# opcional — el resto del schema ya se commiteó arriba.
#
# The statements are Core DDL (`sqlcore.run_ddl`). They are described on a throwaway
# `users` table of their own, NOT on `schema_model.users`: these indexes are outside the
# model and the schema snapshot on purpose (the model would create them with
# `create_all` and `alembic check` would want a revision for them).
_trigram_users = sa.Table(
    "users", sa.MetaData(),
    sa.Column("first_name", sa.Text), sa.Column("last_name", sa.Text), sa.Column("dni", sa.Text))


_TRIGRAM_DDL = (
    sa.DDL("CREATE EXTENSION IF NOT EXISTS pg_trgm"),
    *(sa.schema.CreateIndex(
        sa.Index("users_%s_trgm_idx" % column, _trigram_users.c[column],
                 postgresql_using="gin", postgresql_ops={column: "gin_trgm_ops"}),
        if_not_exists=True) for column in ("first_name", "last_name", "dni")),
)


def _seed_builtin_roles(conn):
    """Siembra los roles de fábrica DESPUÉS del esquema, nunca dentro de él.

    `BASELINE_SQL` es, textualmente, el cuerpo congelado de la baseline de
    Alembic: no lleva datos. Acá el seed es
    código, idempotente (ON CONFLICT DO NOTHING) y derivado de
    `permissions.BUILTIN_ROLES`, así que no puede desincronizarse del preset.
    Las instalaciones existentes lo reciben por la revisión Alembic
    0012_roles_management, que conserva su propio seed."""
    from app.core import permissions as _perm  # tardío: permissions importa db
    filas = [(slug, d["name"], d.get("description", ""), True,
              list(d.get("permissions", ())))
             for slug, d in _perm.BUILTIN_ROLES.items()]
    t = sm.roles
    try:
        with conn.cursor() as cur:
            for slug, name, description, builtin, permissions in filas:
                sqlcore.run(cur, _pg.insert(t).values(
                    slug=slug, name=name, description=description, builtin=builtin,
                    permissions=permissions,
                ).on_conflict_do_nothing(index_elements=[t.c.slug]))
        conn.commit()
    except Exception as e:  # noqa: BLE001 — el panel sigue arrancando
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        print(f"[db] no se pudieron sembrar los roles de fábrica: {log_pii.exc(e)}")


def _apply_trigrams(conn):
    try:
        with conn.cursor() as cur:
            for ddl in _TRIGRAM_DDL:
                sqlcore.run_ddl(cur, ddl)
        conn.commit()
    except Exception as e:  # noqa: BLE001 — índice opcional, nunca bloquea
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        print(f"[db] pg_trgm no disponible, la búsqueda de pacientes "
              f"escanea: {log_pii.exc(e)}")


# A catalog table, described only as far as `_owner_conn_same_db` reads it (Alembic's
# own table is `schema_model.alembic_version`).
_pg_database = sa.table("pg_database", sa.column("oid"), sa.column("datname"))
