"""settings.py — the process keys, declared as typed pydantic-settings classes.

A process key decides how THIS process runs, before the database is open or without it: where the web
process listens, which database it opens and with how many connections, whether the queue worker runs,
where the data package lives, whether Sentry is on. These keys are read from the process environment
(two classes also from the effective env file, `config.env_file_path()`) and NEVER from the panel's
database overrides: the installation settings an admin may change from «Settings» stay in
`core.config.get()`, which owns that layer, its 3-second cache and `TURNOS_CONFIG_LOCK`.

One class per read policy. pydantic validates a class as a whole, so a key whose unusable value must
stop the process (`PORT`, `TURNOS_WORKER_STALLED_S`) lives apart from the keys every process reads, whose
unusable value falls back to the default:

    ServerSettings        HOST, PORT                         the web entrypoint; a non-numeric PORT stops the boot
    ProcessSettings       topology, pools, limits, paths     every process; an unusable value means the default
    WorkerSettings        the stalled-worker threshold       the queue worker; a non-number is exit 2
    DatabaseSettings      DATABASE_URL, the migration URL    environment, then the env file (a blank value is unset)
    InstallationSettings  public origin, required packs      environment, then the env file (an empty value is unset)

A parse that raises raises the very ValueError its reader raised before (`int()`'s, `float()`'s), not
pydantic's wrapper. The env file is opened only when a field of the class is not set in the environment.

Reading is lazy. `XSettings.current()` looks at the environment (and the env file) on every call, so a
test or an operator that changes a variable is seen on the next read, with no reset; the model is only
rebuilt when one of its raw values changed (the memo is keyed by the raw values themselves, and an
instance is memoized only when the values did not move while it was built). The instances are frozen,
so the shared one cannot be edited by a caller.

Every field keeps the parse its call site had before this module (2026-11-11, TASK-76 item I): the
validators below are those parses moved here, and `tests/test_process_settings.py` holds each one against
a copy of the old reader. A new process key is a field here plus its row in `docs/env-registry.md`
(`python3 scripts/env_registry.py --write`); `test_process_settings` fails when product code reads a
declared key from `os.environ` itself.
"""
from __future__ import annotations

import os
from typing import Annotated, ClassVar

from pydantic import BeforeValidator, Field, ValidationError
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict

# The values the application has always run with when a key is unset or unusable.
PORT_DEFAULT = 7700
THREADPOOL_SIZE_DEFAULT = 40  # AnyIO's own default, kept explicit
POOL_SIZE_DEFAULT = 15
POOL_TIMEOUT_DEFAULT = 3.0
POOL_TIMEOUT_MAX = 60.0
WEB_STATEMENT_TIMEOUT_DEFAULT = 30.0
WEB_LOCK_TIMEOUT_DEFAULT = 5.0
WORKER_GRACEFUL_TIMEOUT_DEFAULT = 20.0
WORKER_HEALTH_PORT_DEFAULT = 7710
WORKER_STALLED_DEFAULT = 30.0


# ---- the parses (each one is what its call site did before) ----------------------------------------------

def _raw_text(parse):
    """Apply `parse` to a value read from a source (text); a value handed to the constructor as a typed
    Python value is validated by its annotation alone."""
    return BeforeValidator(lambda v: parse(v) if isinstance(v, str) else v)


def _stripped(raw):
    return raw.strip()


def _exactly_one(raw):
    return raw == "1"


def _one_after_strip(raw):
    return raw.strip() == "1"


def size_or(raw, default):
    """A count: `default` when unset, blank or not an integer, and never below 1."""
    raw = (raw or "").strip()
    try:
        n = int(raw) if raw else default
    except ValueError:
        n = default
    return max(1, n)


def _size(default):
    return _raw_text(lambda raw: size_or(raw, default))


def _number_or(convert, default):
    """`convert(raw)`, or `default` when the text is not a number of that kind."""
    def parse(raw):
        try:
            return convert(raw)
        except ValueError:
            return default
    return _raw_text(parse)


def _pool_wait(raw):
    """Seconds to wait for a free pooled connection: blank, unreadable, not finite or <= 0 is the
    default; above the maximum it is capped (never «wait forever»)."""
    raw = raw.strip()
    try:
        n = float(raw) if raw else POOL_TIMEOUT_DEFAULT
    except ValueError:
        n = POOL_TIMEOUT_DEFAULT
    if not n > 0 or n == float("inf"):
        n = POOL_TIMEOUT_DEFAULT
    return min(n, POOL_TIMEOUT_MAX)


def _limit(default):
    """Seconds of a statement/lock limit: `0` = no limit; blank, unreadable, not finite or negative is
    the default (the cap is `core.db`'s)."""
    def parse(raw):
        raw = raw.strip()
        try:
            n = float(raw) if raw else default
        except ValueError:
            n = default
        if not n >= 0 or n == float("inf"):
            n = default
        return n
    return _raw_text(parse)


def _stalled(raw):
    """Seconds without a heartbeat before a worker counts as stalled; blank is the default and a text
    that is not a number raises `float()`'s ValueError — the worker turns it into exit 2, never a
    silent default."""
    raw = raw.strip()
    if not raw:
        return WORKER_STALLED_DEFAULT
    return float(raw)


# ---- sources ---------------------------------------------------------------------------------------------

class _CountedEnvSource(EnvSettingsSource):
    """The process environment, keeping only the values the class counts as set (`_env_counts`)."""

    def _load_env_vars(self):
        counts = self.settings_cls._env_counts
        return {k: v for k, v in super()._load_env_vars().items() if counts(v)}


class _EnvFileSource(EnvSettingsSource):
    """The effective env file (`TURNOS_ENV_FILE` or the checkout's `.env`), through config's own parser
    (`config.dotenv_values()`), so there is one reader of that file and its cache. Opened only when a
    field of the class is not set in the environment — the old readers looked at the file only then."""

    def _load_env_vars(self):
        cls = self.settings_cls
        if not cls._needs_file(tuple(map(os.environ.get, cls.env_names()))):
            return {}
        from app.core import config
        return config.dotenv_values()


# ---- the classes -----------------------------------------------------------------------------------------

_MEMO = {}


class _ProcessKeys(BaseSettings):
    """Shared behaviour: exact (case-sensitive) names, frozen instances, environment only, lazy `current()`."""

    # Only the exact variable name counts: no case folding and no field name (`port`, `timezone`) as a
    # second spelling, so a constructor takes the variable names too (`ProcessSettings(TURNOS_DB_POOL=3)`).
    model_config = SettingsConfigDict(case_sensitive=True, frozen=True, extra="ignore")

    # Whether a value missing from the environment is looked up in the env file, and which environment
    # values count as set (the others fall through to the file, or to the default).
    _reads_env_file: ClassVar[bool] = False

    @staticmethod
    def _env_counts(value):
        return value is not None

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings,
                                   file_secret_settings):
        if not cls._reads_env_file:
            return init_settings, env_settings
        return (init_settings, _CountedEnvSource(settings_cls),
                _EnvFileSource(settings_cls, env_ignore_empty=False))

    @classmethod
    def env_names(cls):
        """The variable names of the fields, in declaration order."""
        return tuple(f.validation_alias for f in cls.model_fields.values())

    @classmethod
    def _needs_file(cls, env_values):
        return cls._reads_env_file and not all(map(cls._env_counts, env_values))

    @classmethod
    def _inputs(cls):
        """Every raw value the instance is built from: the environment, and the env file when it is read."""
        names = cls.env_names()
        values = tuple(map(os.environ.get, names))
        if cls._needs_file(values):
            from app.core import config
            in_file = config.dotenv_values()
            values += tuple(map(in_file.get, names))
        return values

    @classmethod
    def current(cls):
        """The settings as the environment (and the env file) say right now."""
        inputs = cls._inputs()
        while True:
            hit = _MEMO.get(cls)
            if hit is not None and hit[0] == inputs:
                return hit[1]
            try:
                built = cls()
            except ValidationError as error:
                raise _original(error) from None
            # The sources read the values again: memoize only an instance built from the inputs of its key.
            after = cls._inputs()
            if after == inputs:
                _MEMO[cls] = (inputs, built)
                return built
            inputs = after


def _original(error):
    """The ValueError a field's parse raised — the very error its reader raised before this module (a
    non-numeric PORT: `int()`'s own message) — or the ValidationError itself when there is none."""
    for item in error.errors():
        cause = (item.get("ctx") or {}).get("error")
        if isinstance(cause, ValueError):
            return cause
    return error


class ServerSettings(_ProcessKeys):
    """Where the web process listens (`asgi_main.py`). Read once, at the start of serving: a PORT that is
    not an integer stops the boot with `int()`'s own ValueError, as it always did."""

    # The address the public listener binds.
    host: str = Field("0.0.0.0", validation_alias="HOST")
    # The port of the public listener.
    port: Annotated[int, _raw_text(int)] = Field(PORT_DEFAULT, validation_alias="PORT")


class ProcessSettings(_ProcessKeys):
    """How this process runs. Every field falls back to its default when the value is unusable, so any
    process may read any of them."""

    # Native ASGI serving; `asgi_main.py` refuses to start without it (`TURNOS_ASGI=1`).
    asgi: Annotated[bool, _raw_text(_exactly_one)] = Field(False, validation_alias="TURNOS_ASGI")
    # The queue worker's topology, as written (`core.queue_infra.worker_mode()` is its one parse).
    queue_worker: str | None = Field(None, validation_alias="TURNOS_QUEUE_WORKER")
    # Threads of AnyIO's default limiter (core/concurrency.py).
    threadpool_size: Annotated[int, _size(THREADPOOL_SIZE_DEFAULT)] = Field(
        THREADPOOL_SIZE_DEFAULT, validation_alias="TURNOS_THREADPOOL_SIZE")
    # Connections in this process's database pool (core/db.py).
    db_pool: Annotated[int, _size(POOL_SIZE_DEFAULT)] = Field(POOL_SIZE_DEFAULT, validation_alias="TURNOS_DB_POOL")
    # Seconds a request waits for a free pooled connection before the 503 (core/db.py).
    db_pool_timeout: Annotated[float, _raw_text(_pool_wait)] = Field(
        POOL_TIMEOUT_DEFAULT, validation_alias="TURNOS_DB_POOL_TIMEOUT")
    # Statement limit of the web process's connections, in seconds, 0 = none (core/db.py `enable_web_timeouts`).
    db_statement_timeout: Annotated[float, _limit(WEB_STATEMENT_TIMEOUT_DEFAULT)] = Field(
        WEB_STATEMENT_TIMEOUT_DEFAULT, validation_alias="TURNOS_DB_STATEMENT_TIMEOUT")
    # Lock-wait limit of the web process's connections, in seconds, 0 = none (core/db.py `enable_web_timeouts`).
    db_lock_timeout: Annotated[float, _limit(WEB_LOCK_TIMEOUT_DEFAULT)] = Field(
        WEB_LOCK_TIMEOUT_DEFAULT, validation_alias="TURNOS_DB_LOCK_TIMEOUT")
    # Seconds the queue worker drains in-flight jobs on SIGTERM; the supervisor's SIGKILL budget follows it.
    worker_graceful_timeout: Annotated[float, _number_or(float, WORKER_GRACEFUL_TIMEOUT_DEFAULT)] = Field(
        WORKER_GRACEFUL_TIMEOUT_DEFAULT, validation_alias="TURNOS_WORKER_GRACEFUL_TIMEOUT")
    # Loopback port of the queue worker's own health endpoint (worker.py).
    worker_health_port: Annotated[int, _number_or(int, WORKER_HEALTH_PORT_DEFAULT)] = Field(
        WORKER_HEALTH_PORT_DEFAULT, validation_alias="TURNOS_WORKER_HEALTH_PORT")
    # The fail-closed outbound kill switch (core/outbound_guard.py).
    outbound_kill: Annotated[bool, _raw_text(_one_after_strip)] = Field(False, validation_alias="TURNOS_OUTBOUND_KILL")
    # `config.get()`: env/.env win over the panel's overrides (core/config.py `locked`).
    config_lock: Annotated[bool, _raw_text(_exactly_one)] = Field(False, validation_alias="TURNOS_CONFIG_LOCK")
    # The file that counts as the env file, replacing the checkout's `.env` (core/config.py `_env_path`).
    env_file: str | None = Field(None, validation_alias="TURNOS_ENV_FILE")
    # IANA zone of the installation, over the data package's `country.json` (core/tz.py).
    timezone: str | None = Field(None, validation_alias="TURNOS_TIMEZONE")
    # The legacy single-directory data package, over the pack chain (core/sitedata.py, core/data_packs.py).
    webdata_dir: str | None = Field(None, validation_alias="TURNOS_WEBDATA_DIR")
    # Sentry DSN; unset = error tracking off and `sentry_sdk` never imported (core/error_tracking.py).
    sentry_dsn: Annotated[str, _raw_text(_stripped)] = Field("", validation_alias="TURNOS_SENTRY_DSN")
    # The environment name Sentry files the events under (core/error_tracking.py).
    sentry_env: str = Field("production", validation_alias="TURNOS_SENTRY_ENV")
    # The launch signals the queue worker sends: `failures`, `digest`, `crons` or `all` (core/launch_signals.py).
    sentry_signals: str = Field("", validation_alias="TURNOS_SENTRY_SIGNALS")


class WorkerSettings(_ProcessKeys):
    """The queue worker's recovery threshold, alone in its class: a value that is not a number is a
    configuration error (`float()`'s ValueError, exit 2 in `worker.main`), and it must not break the keys
    every other process reads."""

    # Seconds without a heartbeat before a worker's jobs count as stalled (worker.py `_stalled_seconds`).
    stalled_s: Annotated[float, _raw_text(_stalled)] = Field(
        WORKER_STALLED_DEFAULT, validation_alias="TURNOS_WORKER_STALLED_S")


class DatabaseSettings(_ProcessKeys):
    """Which database this process opens: the environment, then the env file; a value of only whitespace
    counts as unset, and the result is stripped ("" = not configured)."""

    _reads_env_file: ClassVar[bool] = True

    @staticmethod
    def _env_counts(value):
        return value is not None and bool(value.strip())

    # The application's database (core/db.py); "" = not configured.
    database_url: Annotated[str, _raw_text(_stripped)] = Field("", validation_alias="DATABASE_URL")
    # The schema owner's login for the migrations at boot (core/db_schema.py); "" = DATABASE_URL does both.
    migration_url: Annotated[str, _raw_text(_stripped)] = Field("", validation_alias="TURNOS_DB_MIGRATION_URL")


class InstallationSettings(_ProcessKeys):
    """What this installation is, decided before the database layer: the environment, then the env file;
    an empty value counts as unset (whitespace does not). Nothing edited from the panel can move these."""

    _reads_env_file: ClassVar[bool] = True

    @staticmethod
    def _env_counts(value):
        return value not in (None, "")

    # The installation's public origin; `*.gob.ar` marks the municipal installation (core/config.py).
    public_origin: Annotated[str, _raw_text(_stripped)] = Field("", validation_alias="TURNOS_PUBLIC_ORIGIN")
    # Root of the install-owned private packs (core/data_packs.py); read before the database, never through it.
    private_packs_dir: str | None = Field(None, validation_alias="TURNOS_PRIVATE_PACKS_DIR")
    # The installation's persistent data directory, over `<checkout>/data` (core/data_packs.py).
    data_dir: str | None = Field(None, validation_alias="TURNOS_DATA_DIR")
    # Comma-separated pack ids that must verify before either server accepts a request (core/data_packs.py).
    required_packs: str | None = Field(None, validation_alias="TURNOS_REQUIRED_PACKS")


class AnalyticsSettings(_ProcessKeys):
    """The anonymous analytics stream (turnos/analytics_export.py). Off unless `TURNOS_ANALYTICS=1`: on, the
    installation's own database collects page/search/step events and a daily public-schedule snapshot, and
    the Reportes screens and the read-only export API read them. Nothing leaves the installation by itself.
    Every field has a usable default so any process may read the class."""

    # 1 = collect page/search/step events and the daily public schedule snapshot into the database.
    enabled: Annotated[bool, _raw_text(_exactly_one)] = Field(False, validation_alias="TURNOS_ANALYTICS")
    # Days an event is kept (the schedule snapshots keep a third of it).
    retention_days: Annotated[int, _number_or(int, 400)] = Field(
        400, validation_alias="TURNOS_ANALYTICS_RETENTION_DAYS")


CLASSES = (ServerSettings, ProcessSettings, WorkerSettings, DatabaseSettings, InstallationSettings,
           AnalyticsSettings)


def declared_names():
    """Every variable name the classes declare."""
    return tuple(n for cls in CLASSES for n in cls.env_names())
