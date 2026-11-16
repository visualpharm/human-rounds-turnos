"""db.py — persistencia en Postgres de Human Rounds — Turnos.

This module is the CONNECTION: the pool, `_connect`, the web process's timeouts and `migrations_status`. The statements
live in `core/db_<domain>.py`; the value helpers, the wire-format rename, the advisory locks and the schema bootstrap
live in `db_values`, `db_wire`, `db_locks` and `db_schema`, the layers below this one. Whatever is not the connection is
reached through the module that owns it (`db_booking.approve(...)`), see `tests/test_db_domain_modules.py`.

Elección explícita del responsable del producto: Postgres, NO SQLite. Driver: psycopg3
(`psycopg[binary]`). El driver se importa DENTRO de las funciones (lazy): si
falta el paquete o la base no está disponible, se levanta `DbUnavailable` y la
API responde 503 — el resto del sitio sigue arrancando (server.py importa
`solicitudes` al inicio, así que este módulo NUNCA debe fallar al importarse).

Esquema (auto-creado en el primer uso):
  solicitudes(id, dni, nombre, apellido, nacimiento, contacto, servicio_id,
              centro_slug, estado, orden_path, orden_mime, ai_json, motivo_rechazo,
              turno_dt, hsi_patient_id, hsi_appointment_id, hsi_estado, created)

Pool de conexiones (2026-08-06, auditoría): el código de base de datos es
síncrono; hoy (FastAPI + uvicorn, `asgi_main.py`) los handlers `async def` lo
llaman con `run_in_threadpool`, así que cada request que toca la base corre en
un hilo del pool de hilos (antes del 2026-08-27, del `ThreadingHTTPServer`,
un hilo por request) — antes de este cambio cada uno de los ~234
call sites de `_connect()` abría una conexión TCP nueva a Postgres, algo que
escala mal apenas hay tráfico concurrente real. `_connect()` ahora devuelve
una conexión CHECKEADA de un `psycopg_pool.ConnectionPool` module-level (uno
por `DATABASE_URL` — ver `_get_pool`), pero el contrato en cada call site NO
cambió ni un carácter: sigue siendo `with _connect() as conn, conn.cursor()
as cur: ...`, commitea (o rollbackea si hubo excepción) y ahora DEVUELVE la
conexión al pool en vez de cerrarla (`_PooledConn` abajo). `TURNOS_DB_POOL`
(default 15) fija el tamaño y `TURNOS_DB_POOL_TIMEOUT` (default 3 s) cuánto
espera un request una conexión libre antes del 503; `check=ConnectionPool.check_connection` hace que
una conexión que murió bajo nosotros (reinicio de Postgres) se descarte y
se re-abra SOLA en el próximo checkout, nunca se entrega rota.
"""

import contextlib
import threading
import time

import sqlalchemy as sa

from app.core import db_schema
from app.core import schema_model as sm
from app.core import settings
from app.core import sqlcore


# Pool de conexiones, uno por DATABASE_URL (no un único global): los tests
# Postgres-backed (tests/_dbtest.py) apuntan DATABASE_URL a una base de test
# nueva por módulo y corren los ~30 módulos en el MISMO proceso — si el pool
# fuera un singleton, el segundo módulo heredaría conexiones abiertas contra
# la base del PRIMERO. `_dbtest.PerModuleDb` cierra la entrada de este dict
# que le corresponde en `set_up`/`tear_down` (ver `close_pool`) — igual que
# ya hace con `db_schema._ensured.clear()`.
_pools = {}
_pools_lock = threading.Lock()

# A pool still open when the interpreter exits is GUARANTEED noise under
# Python 3.14: `ConnectionPool.__del__` tries to join its background threads,
# which is no longer allowed during finalization — it prints
# `PythonFinalizationError: cannot join thread at interpreter shutdown` with a
# stack trace even though the process exits 0. `_get_pool` registers
# `close_pools` in atexit ONCE (when the first pool is created): atexit runs
# before finalization, while joining is still legal, so every live pool gets
# closed explicitly — server, worker and test suites alike.
_atexit_registered = False

# Negative cache: url -> timestamp hasta el cual _connect() falla RÁPIDO sin
# tocar la red (2026-08-06, ver _diagnose_pool_timeout/_BROKEN_URL_TTL). Sin
# esto, un DATABASE_URL genuinamente roto (base caída, base dropeada) hace
# que CADA _connect() -aunque sea el llamador #200 al mismo url roto en el
# mismo segundo- arme un ConnectionPool nuevo y pague el getconn(timeout=3)
# completo antes de descartarlo: un test module sin DB propia que corre
# después de que tests/_dbtest.py deja DATABASE_URL apuntando a una base
# recién dropeada (leak documentado ahí, no relitigar) convertía cada uno de
# sus asserts en un ~3s de espera + varias líneas de log de psycopg_pool
# reintentando — con ~90 test modules así en la corrida completa, el suite
# entero se volvía impracticable. En producción es la misma protección: si
# Postgres se cae, todo request concurrente paga el mismo 3s en vez de
# fallar al toque.
_broken_urls = {}
_BROKEN_URL_TTL = 5.0


class DbUnavailable(Exception):
    """La base no está disponible (driver ausente o conexión fallida)."""


class SchemaRefused(DbUnavailable):
    """The boot refused this database's schema (`db_schema.UnknownRevision`: it is
    stamped with an Alembic revision this code does not know). A `DbUnavailable`, so
    every route answers it as a database that is down; the message names the revision
    and what to do, and `/ready`, «Sistema» and the panel login card show it."""


def schema_refusal():
    """The message of the refusal this process holds for its database, or None. Reads
    no database: it is what the last connection attempt decided."""
    refused = db_schema._refused.get(_db_url() or "")
    return str(refused) if refused is not None else None


class DbTimeout(DbUnavailable):
    """The web pool's `statement_timeout` or `lock_timeout` cut a statement. A
    `DbUnavailable`, so every route that already answers 503 for a database that does not
    answer answers it for this too; the message says which limit it was."""


class DbConnectionLost(DbUnavailable):
    """The database stopped answering under a statement: Postgres restarted or crashed, the backend
    was terminated, the connection dropped. A `DbUnavailable`, so it is the same 503 and the same
    `db_unavailable` signal as a pool that could not hand out a connection (the product owner's hub review,
    2026-11-10); the message names only psycopg's error type."""


class DbCommitUnknown(DbConnectionLost):
    """The connection was lost under the commit itself: the database may or may not have written the transaction.
    Still a database that does not answer (503, `db_unavailable`), but the answer does not promise that asking again
    is safe — `core.deps.answer_db_unavailable` says the outcome is unknown and the uncaught handler drops
    `Retry-After` (the product owner's hub review, 2026-11-11)."""


def _timeout_error(exc):
    """A `DbTimeout` for a psycopg error raised by a limit, None for any other error."""
    try:
        from psycopg import errors
    except Exception:  # noqa: BLE001
        return None
    if isinstance(exc, errors.LockNotAvailable):
        return DbTimeout("espera de bloqueo cortada por TURNOS_DB_LOCK_TIMEOUT")
    if isinstance(exc, errors.QueryCanceled):
        return DbTimeout("consulta cortada por TURNOS_DB_STATEMENT_TIMEOUT")
    return None


# The SQLSTATE classes of a psycopg `OperationalError` that mean the database stopped answering: 08 connection
# exception, 53 insufficient resources (too many connections, disk full, out of memory), 57 operator intervention
# (shutdown, crash, cannot connect now, database dropped), 58 system error, F0 configuration file error. An
# `OperationalError` with no SQLSTATE is the client losing the server (a closed socket, a dropped network). Every
# other class of `OperationalError` is the database answering about the statement — 40 deadlock or serialization
# failure, 54 program limit, 55 object state, 2F/38/39 routines, HV foreign data — and is not an outage. Inside
# class 08, 08P01 protocol violation is the client sending what the protocol does not allow — a bug of ours, not an
# outage (the product owner's hub review, 2026-11-11).
_LOST_CLASSES = ("08", "53", "57", "58", "F0")
_NOT_LOST_STATES = ("08P01",)


def _unavailable_error(exc, *, on_commit=False):
    """The `DbUnavailable` that `exc` means — `DbTimeout` for a limit, `DbConnectionLost` for a database that
    stopped answering (`DbCommitUnknown` when it was the commit, `on_commit`) — or None for anything else."""
    timeout = _timeout_error(exc)
    if timeout is not None:
        return timeout
    try:
        import psycopg
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(exc, psycopg.OperationalError):
        return None
    state = getattr(exc, "sqlstate", None) or ""
    if state and (state[:2] not in _LOST_CLASSES or state in _NOT_LOST_STATES):
        return None
    if on_commit:
        return DbCommitUnknown(f"la base dejó de responder al confirmar ({type(exc).__name__})")
    return DbConnectionLost(f"la base dejó de responder ({type(exc).__name__})")


def _unavailable_errors():
    # A statement or lock wait cut by the web pool's limits (`enable_web_timeouts`), or a database that stopped
    # answering: the pooled `with` turns it into a `DbUnavailable`, but a limit hit outside such a block arrives
    # as psycopg's own error. Same 503 either way.
    try:
        from psycopg import errors
    except Exception:  # noqa: BLE001 — no driver, no limits to report
        return (DbUnavailable,)
    return (DbUnavailable, errors.QueryCanceled, errors.LockNotAvailable)


# What «the database does not answer» is, for every place that has to tell it from other failures: the
# routes' 503 (`core.deps.DB_UNAVAILABLE_ERRORS`) and the access log's refusal (`core.access_log`). Anything
# else is that place's own error (the product owner's hub, 2026-11-10: db_unavailable means the database is unavailable).
UNAVAILABLE_ERRORS = _unavailable_errors()


def _db_url():
    """`DATABASE_URL`: the environment, then the env file, stripped; "" when neither sets it
    (`core.settings.DatabaseSettings`)."""
    return settings.DatabaseSettings.current().database_url


POOL_SIZE_DEFAULT = settings.POOL_SIZE_DEFAULT
POOL_TIMEOUT_DEFAULT = settings.POOL_TIMEOUT_DEFAULT
POOL_TIMEOUT_MAX = settings.POOL_TIMEOUT_MAX


def _pool_size():
    return settings.ProcessSettings.current().db_pool


def _pool_timeout():
    """Segundos que un request espera una conexión libre del pool antes de
    `DbUnavailable` (503). `TURNOS_DB_POOL_TIMEOUT`, default 3; un valor
    ilegible, no finito o <= 0 cae al default y uno mayor a 60 s se recorta a
    60 (nunca "esperar para siempre") — `core.settings.ProcessSettings`."""
    return settings.ProcessSettings.current().db_pool_timeout


# Limits of the WEB process's connections (2026-10-28, hospital TASK-40). Postgres cuts a
# statement that runs longer than `statement_timeout` and a statement that waits for a
# lock longer than `lock_timeout`; without them a stuck lock (a migration, a runaway
# query) holds every pooled connection until it clears (docs/architecture/capacity.md,
# «While the database stalls»). They are set on the connections of the pool the WEB
# process opens and on no other: the migrations, the backups, the queue worker and the
# scripts take no limit. The values are seconds; measured on 98k requests the longest
# statement of the panel took 0.1 s and the longest request 0.7 s, so 30 s and 5 s are
# a wide margin and still shorter than the 60 s a proxy waits.
WEB_STATEMENT_TIMEOUT_DEFAULT = settings.WEB_STATEMENT_TIMEOUT_DEFAULT
WEB_LOCK_TIMEOUT_DEFAULT = settings.WEB_LOCK_TIMEOUT_DEFAULT
WEB_TIMEOUT_MAX = 600.0

# None = this process is not the web process (or the web process switched the limits off):
# no connection of its pool carries a limit. `enable_web_timeouts()` is the only way in,
# and `asgi_main.py` the only caller.
_web_timeouts = None


def _timeout_ms(seconds):
    """Milliseconds of a limit given in seconds (`0` means «no limit»), capped at 600 s. The
    seconds come parsed from `core.settings.ProcessSettings`: unreadable, not finite or
    negative is already the default there."""
    return int(min(seconds, WEB_TIMEOUT_MAX) * 1000)


def enable_web_timeouts():
    """Called once by the web entrypoint, before the first connection: every connection
    the pool opens from now on gets `TURNOS_DB_STATEMENT_TIMEOUT` (default 30 s) and
    `TURNOS_DB_LOCK_TIMEOUT` (default 5 s), `0` = no limit. Returns `(statement_ms,
    lock_ms)`."""
    global _web_timeouts
    process = settings.ProcessSettings.current()
    _web_timeouts = (_timeout_ms(process.db_statement_timeout), _timeout_ms(process.db_lock_timeout))
    return _web_timeouts


def disable_web_timeouts():
    """Back to the default of every other process: no limit (the tests use it)."""
    global _web_timeouts
    _web_timeouts = None


def web_timeouts():
    """`(statement_ms, lock_ms)` the web pool applies, or None."""
    return _web_timeouts


def _set_timeouts(conn, statement_ms, lock_ms):
    """Session-level `statement_timeout` / `lock_timeout` on `conn`, committed so a later
    rollback of the pool cannot take them back."""
    with conn.cursor() as cur:
        sqlcore.run(cur, sa.select(sa.func.set_config("statement_timeout", str(statement_ms), False)))
        sqlcore.run(cur, sa.select(sa.func.set_config("lock_timeout", str(lock_ms), False)))
    conn.commit()


def _configure_web_connection(conn):
    """`configure` of the web pool: runs once on every connection it opens (and re-opens)."""
    if _web_timeouts is not None:
        _set_timeouts(conn, *_web_timeouts)


@contextlib.contextmanager
def without_timeouts(conn):
    """Lifts the web pool's limits on `conn` for the block, then puts them back. For the
    work that is allowed to be slow or to wait on purpose: the schema bootstrap (one
    `pg_advisory_lock` held across Alembic, which another replica may be waiting on) and
    the backup's session lock. `conn` must have no open transaction on the way in and the
    block must leave none (it is committed on the way out, to keep the restored limits).
    A no-op in a process whose connections carry no limit."""
    limits = _web_timeouts
    if limits is None:
        yield conn
        return
    _set_timeouts(conn, 0, 0)
    completed = False
    try:
        yield conn
        completed = True
    finally:
        try:
            if not completed:
                conn.rollback()  # the block failed: whatever it left open goes, the limits come back
            _set_timeouts(conn, *limits)
        except Exception:  # noqa: BLE001
            conn.close()  # a connection without its limits must not go back to the pool
            if completed:
                raise


def _get_pool(url):
    """`psycopg_pool.ConnectionPool` para esta `url`, creado perezosamente
    (una sola vez por url, cacheado en `_pools`). Mismo `kwargs` que el
    `psycopg.connect()` de antes (`row_factory=dict_row`, `connect_timeout=5`)
    — la única diferencia de comportamiento es de dónde sale el socket TCP,
    nunca cómo corre una query. `check=ConnectionPool.check_connection` corre
    un `SELECT ''` barato en CADA checkout: si Postgres se reinició y esa
    conexión ya está muerta, el pool la descarta y redial-ea ahí mismo — el
    caller nunca recibe una conexión rota, y el proceso se recupera solo, sin
    reiniciar.

    `reconnect_timeout=10` (el default de psycopg_pool es 300s): si la base
    no existe o es inalcanzable, el hilo de fondo del pool reintenta con
    backoff durante ESTE tiempo antes de rendirse y loguear — a los 300s por
    defecto, un `DATABASE_URL` mal escrito dejaba ~30 hilos de fondo
    machacando Postgres con reintentos silenciosos por 5 minutos en cada
    módulo de test (y haría lo mismo en producción al arrancar). Bajarlo a
    10s NO empeora la recuperación de un blip transitorio real: cada
    `getconn()` que encuentra el pool vacío dispara un intento de crecimiento
    NUEVO (`_maybe_grow_pool`, propio de psycopg_pool), así que un Postgres
    que vuelve a los 30s se sigue re-conectando solo en el próximo checkout,
    sin reiniciar el proceso — `reconnect_timeout` sólo acota cuánto
    machaca UN intento antes de ceder el turno al siguiente."""
    pool = _pools.get(url)
    if pool is not None:
        return pool
    with _pools_lock:
        pool = _pools.get(url)
        if pool is not None:
            return pool
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except Exception as e:  # noqa: BLE001
            raise DbUnavailable(f"psycopg no disponible: {e}")
        pool_options = {}
        if _web_timeouts is not None:
            pool_options["configure"] = _configure_web_connection
        try:
            pool = ConnectionPool(
                url,
                kwargs={"row_factory": dict_row, "connect_timeout": 5},
                min_size=1,
                max_size=_pool_size(),
                timeout=_pool_timeout(),  # checkout: bloquea hasta N s (default 3) y después PoolTimeout
                reconnect_timeout=10,  # ver docstring — no 300s por defecto
                check=ConnectionPool.check_connection,
                open=True,
                **pool_options,
            )
        except Exception as e:  # noqa: BLE001
            raise DbUnavailable(f"no se pudo abrir el pool de conexiones: {e}")
        _pools[url] = pool
        global _atexit_registered
        if not _atexit_registered:
            # See the `_atexit_registered` comment above. One registration
            # per process is enough: close_pools() iterates whatever is in
            # `_pools` AT EXIT, pools created later included.
            import atexit
            atexit.register(close_pools)
            _atexit_registered = True
        return pool


def close_pools():
    """Cierra y descarta TODOS los pools cacheados. No lo llama el server en
    marcha (el pool vive toda la vida del proceso) — es para tests/_dbtest.py,
    que apunta DATABASE_URL a una base de test nueva por módulo y corre
    ~30 módulos en el mismo proceso: sin esto, el pool del módulo anterior
    se queda con hilos de fondo reintentando conexión contra una base que
    `tear_down` ya dropeó. También limpia `_broken_urls` (ver su docstring):
    el próximo `_dbtest.PerModuleDb.set_up()` apunta DATABASE_URL a una base
    de test recién creada — sana — que no debe heredar el "esto está roto"
    de la base anterior."""
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
        _broken_urls.clear()
    for pool in pools:
        try:
            pool.close()
        except Exception:  # noqa: BLE001
            pass


class _PooledConn:
    """Lo que devuelve `_connect()`. `with _connect() as conn, conn.cursor()
    as cur:` sigue andando exactamente igual en los ~234 call sites: `conn`
    adentro del `with` es la conexión psycopg REAL (nunca se envuelve —
    `conn.cursor()` no cambia), y lo único que cambia es el `__exit__` de
    ACÁ (no el de `conn`): commitea o rollbackea reusando
    `psycopg.Connection.__exit__` tal cual (mismo código que corría sin
    pool), y en vez de dejarlo cerrar la conexión (se lo salta solo porque
    el pool ya le puso `conn._pool`), la devuelve al pool con `putconn` —
    `putconn` resetea sola cualquier transacción que haya quedado abierta y
    descarta la conexión si terminó rota, sin que este código tenga que
    saberlo."""

    __slots__ = ("_pool", "_conn", "_returned")

    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn
        self._returned = False

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if self._returned:
            return False
        conn = self._conn
        try:
            conn.__exit__(exc_type, exc, tb)
        except Exception as failed:  # noqa: BLE001
            if exc is None:
                # The commit itself: a database that stopped answering is one here too, and what it wrote is unknown.
                unavailable = _unavailable_error(failed, on_commit=True)
                if unavailable is not None:
                    raise unavailable from failed
                raise
            # The rollback after the body's own error: that error is why the request failed and goes on; the
            # rollback's is attached to it (psycopg already swallows its own rollback errors, with a warning).
            exc.add_note(f"rollback failed too: {type(failed).__name__}")
        finally:
            self._return_to_pool()
        unavailable = _unavailable_error(exc) if exc is not None else None
        if unavailable is not None:
            raise unavailable from exc
        return False

    def _return_to_pool(self):
        if self._returned:
            return
        self._returned = True
        try:
            self._pool.putconn(self._conn)
        except Exception:  # noqa: BLE001
            pass

    def close(self):
        """Compat para código/tests que llaman `conn.close()` en vez de
        usar el `with` (p.ej. `conn = db._connect(); conn.close()` en los
        tests de bootstrap). Equivale a salir del `with` sin excepción:
        commitea vía `psycopg.Connection.__exit__` y devuelve la conexión
        al pool. Idempotente — un segundo `close()`, o un `__exit__`
        posterior sobre el mismo objeto (p.ej. `with db._connect() as c:`
        seguido de un `c.close()` manual adentro), no hace un doble
        `putconn`."""
        if self._returned:
            return
        conn = self._conn
        try:
            conn.__exit__(None, None, None)
        finally:
            self._return_to_pool()


def _diagnose_pool_timeout(url, pool, pool_timeout_error):
    """Un `PoolTimeout` en `getconn()` puede significar dos cosas MUY
    distintas: (a) el pool está sano pero agotado (todas las conexiones
    ocupadas — un blip real, se resuelve solo) o (b) la base ni siquiera
    existe/está inalcanzable, y el hilo de fondo del pool va a seguir
    fallando en silencio para siempre — el caller nunca ve el error REAL de
    Postgres (p.ej. `invalid_catalog_name`), sólo "pool agotado", lo que
    hacía perder minutos de troubleshooting. UN intento de conexión directa
    (fuera del pool, acotado a `connect_timeout=3`) distingue los dos casos:
    si la base está mal, esto falla CASI AL INSTANTE con el mensaje real de
    Postgres (Postgres rechaza una base inexistente enseguida, no espera el
    timeout); si la base está sana, esto se conecta bien — se cierra sin
    usarse (no es una conexión del pool) y devolvemos el mensaje de
    agotamiento original.

    Caso (b) además DESCARTA y CIERRA este pool ahora mismo (lo saca de
    `_pools`): si no, sus hilos de fondo siguen machacando Postgres con
    reintentos cada vez que otro `_connect()` (de OTRO caller) vuelve a
    tocar la misma url rota — bajo carga real eso degrada las respuestas de
    Postgres para todo el mundo, incluida esta misma sonda, y el costo por
    llamada empieza a crecer en vez de quedarse acotado. La próxima llamada
    a `_connect()` con esta url arma un pool NUEVO desde cero — vuelve a
    pagar el mismo ~3s acotado, nunca más. Salvo que ESTA función haya
    confirmado caso (b): ahí además marca `_broken_urls[url]` (ver su
    docstring) para que las próximas llamadas, por `_BROKEN_URL_TTL`
    segundos, ni siquiera lleguen a armar el pool nuevo — fallan al toque."""
    try:
        import psycopg
    except Exception:  # noqa: BLE001
        return f"pool de conexiones agotado: {pool_timeout_error}"
    try:
        probe = psycopg.connect(url, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        msg = f"no se pudo conectar a la base: {e}"
        with _pools_lock:
            if _pools.get(url) is pool:
                del _pools[url]
            _broken_urls[url] = (time.time() + _BROKEN_URL_TTL, msg)
        try:
            pool.close(timeout=1)
        except Exception:  # noqa: BLE001
            pass
        return msg
    try:
        probe.close()
    except Exception:  # noqa: BLE001
        pass
    return f"pool de conexiones agotado: {pool_timeout_error}"


def _connect():
    """Conexión (del pool) psycopg3 con row_factory=dict_row y el esquema
    garantizado. Levanta DbUnavailable si el driver o la base no están
    disponibles, o si el pool está agotado (todas las conexiones ocupadas
    más de `timeout` segundos — ver `_get_pool`). Un error FATAL de conexión
    (base inexistente, credenciales inválidas, host inalcanzable) sale a la
    luz en ~3s — antes del pool, `psycopg.connect()` fallaba al instante para
    ese mismo caso; con el pool, sin este acotamiento, cada `_connect()`
    pagaba el timeout completo del `getconn()` UNA Y OTRA VEZ mientras el
    hilo de fondo reintentaba para siempre (ver `_get_pool`). Antes de tocar
    el pool, chequea `_broken_urls`: si ESTA url falló hace menos de
    `_BROKEN_URL_TTL` segundos, falla directo con el mismo mensaje — sin
    abrir un `ConnectionPool` nuevo ni pagar el `getconn(timeout=3)` de
    nuevo (ver docstring de `_broken_urls` arriba y de
    `_diagnose_pool_timeout`)."""
    url = _db_url()
    if not url:
        raise DbUnavailable("DATABASE_URL no configurada")
    with _pools_lock:
        broken = _broken_urls.get(url)
        if broken is not None:
            expires_at, msg = broken
            if time.time() < expires_at:
                raise DbUnavailable(msg)
            del _broken_urls[url]
    pool = _get_pool(url)
    try:
        from psycopg_pool import PoolTimeout
    except Exception as e:  # noqa: BLE001
        raise DbUnavailable(f"psycopg no disponible: {e}")
    try:
        conn = pool.getconn(timeout=_pool_timeout())
    except PoolTimeout as e:
        raise DbUnavailable(_diagnose_pool_timeout(url, pool, e))
    except Exception as e:  # noqa: BLE001
        raise DbUnavailable(f"no se pudo conectar a la base: {e}")
    try:
        if url not in db_schema._ensured:
            # The first connection brings the schema to head: it may wait for another
            # replica's migration lock and run DDL for minutes, so it takes no limit.
            with without_timeouts(conn):
                db_schema._ensure_schema(conn, url)
    except db_schema.UnknownRevision as e:
        pool.putconn(conn)
        raise SchemaRefused(str(e))
    except Exception as e:  # noqa: BLE001
        pool.putconn(conn)
        raise DbUnavailable(f"no se pudo crear el esquema: {e}")
    return _PooledConn(pool, conn)


def migrations_status(conn):
    """Tier 4 slice 7 (docs/roadmap/tier4-replatform-design.md, "/health +
    /ready..."): (al_dia, actual, head) — `al_dia` is True when the
    database's `alembic_version` row matches the checkout's own script-
    directory head, i.e. "no pending migrations" for `/ready` and
    `system_status.probe_db()`. Read-only, cheap (one query + an in-memory
    scan of `alembic/versions/`, same script dir `_alembic_config()`
    already resolves for `_run_alembic_upgrade` — never a second source of
    truth for what "head" means). `actual`/`head` are `None` when the table
    doesn't exist yet (a database `_ensure_schema` hasn't finished
    bootstrapping) — that alone makes `al_dia` False."""
    from alembic.script import ScriptDirectory

    url = _db_url()
    cfg = db_schema._alembic_config(url)
    head = ScriptDirectory.from_config(cfg).get_current_head()
    actual = None
    if db_schema._alembic_version_present(conn):
        with conn.cursor() as cur:
            row = sqlcore.run(cur, sa.select(sm.alembic_version.c.version_num).limit(1)).fetchone()
            actual = row["version_num"] if row else None
    return (actual is not None and actual == head, actual, head)
