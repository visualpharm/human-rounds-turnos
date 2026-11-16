"""The process keys are pydantic-settings classes (core/settings.py) and nothing else reads them (2026-11-11).

Three things are held here:

* product code reads a declared key only through its class — never `os.environ` / `os.getenv` /
  `config.environ_text` on the name itself (the two documented exceptions below);
* every migrated reader answers what it answered before: each is compared, over a battery of raw values,
  with a copy of the reader as it was on main 571f7bd3 (`_Before` below), including the env-file layer;
* reading is lazy: a change of the environment or of the env file is seen on the next read, with no reset.
"""
import ast
import importlib.util
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # no Postgres needed
_env.process_defaults(TURNOS_FAKE_AI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pydantic  # noqa: E402

from app.core import concurrency, config, data_packs, db, db_schema, error_tracking, launch_signals  # noqa: E402
from app.core import outbound_guard, proxy_trust, queue_infra, settings, sitedata, tz  # noqa: E402
from app.core import admin_csrf_middleware  # noqa: E402

_spec = importlib.util.spec_from_file_location("env_registry", os.path.join(ROOT, "scripts", "env_registry.py"))
env_registry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(env_registry)

# Raw values every reader is tried with (None = the variable is not set).
RAW = (None, "", " ", "  \t", "abc", "0", "1", " 1", "1 ", "-1", "2.5", " 12 ", "7_0", "0x10", "nan", "inf", "-inf",
       "1e999", "61", "600", "60", "+5", "external", "postgres://u@h/db", "  postgres://u@h/db  ",
       "https://salud.pinamar.gob.ar", "salud.pinamar.gob.ar", "http://[::1", "All", "hsi, digest;all")
# Values of the same key in the env file (None = absent from the file).
FILE = (None, "", "postgres://f@h/file", " https://x.gob.ar ", "pack-a,pack-b")

# The reads that stay on the environment, with the reason.
ALLOWED_DIRECT = {
    ("app/worker.py", "TURNOS_WORKER_STALLED_S"): "the exit-2 log line echoes the raw text that is not a number",
    ("app/core/outbound_guard.py", "DATABASE_URL"): "the fallback for when core.db itself cannot be imported",
}
# Not product code that runs in the application: tooling that sets the environment for its children.
_NOT_RUNTIME = ("scripts/", "scripts/tools/")


def _same(a, b):
    """Equality that treats two NaNs as the same answer."""
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b and type(a) is type(b)


def _outcome(fn):
    """("value", result) or ("raised", exception type, message): an error must be the same error."""
    try:
        return ("value", fn())
    except ValueError as error:
        return ("raised", type(error), str(error))


class _Before:
    """The readers as they were on main 571f7bd3, verbatim but for the names."""

    @staticmethod
    def env_size(name, default):
        raw = (os.environ.get(name) or "").strip()
        try:
            n = int(raw) if raw else default
        except ValueError:
            n = default
        return max(1, n)

    @staticmethod
    def pool_timeout():
        raw = (os.environ.get("TURNOS_DB_POOL_TIMEOUT") or "").strip()
        try:
            n = float(raw) if raw else 3.0
        except ValueError:
            n = 3.0
        if not n > 0 or n == float("inf"):
            n = 3.0
        return min(n, 60.0)

    @staticmethod
    def timeout_ms(name, default):
        raw = (os.environ.get(name) or "").strip()
        try:
            n = float(raw) if raw else default
        except ValueError:
            n = default
        if not n >= 0 or n == float("inf"):
            n = default
        return int(min(n, 600.0) * 1000)

    @staticmethod
    def graceful():
        try:
            return float(os.environ.get("TURNOS_WORKER_GRACEFUL_TIMEOUT", "20"))
        except ValueError:
            return 20.0

    @staticmethod
    def health_port():
        try:
            return int(os.environ.get("TURNOS_WORKER_HEALTH_PORT", "7710"))
        except ValueError:
            return 7710

    @staticmethod
    def stalled():
        raw = (os.environ.get("TURNOS_WORKER_STALLED_S") or "").strip()
        if not raw:
            return 30.0
        return float(raw)

    @staticmethod
    def worker_mode():
        valor = (os.environ.get("TURNOS_QUEUE_WORKER") or "").strip()
        return valor if valor == "1" else ""

    @staticmethod
    def host_port():
        return os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "7700"))

    @staticmethod
    def db_url(file):
        url = (os.environ.get("DATABASE_URL") or "").strip()
        if url:
            return url
        return (file.get("DATABASE_URL") or "").strip()

    @staticmethod
    def migration_url(file):
        url = (os.environ.get("TURNOS_DB_MIGRATION_URL") or "").strip()
        if url:
            return url
        return (file.get("TURNOS_DB_MIGRATION_URL") or "").strip()

    @staticmethod
    def public_origin(file):
        return str(os.environ.get("TURNOS_PUBLIC_ORIGIN") or file.get("TURNOS_PUBLIC_ORIGIN", "")).strip()

    @staticmethod
    def boot_setting(key, file):
        value = os.environ.get(key)
        if value not in (None, ""):
            return value
        return file.get(key)

    @staticmethod
    def features():
        raw = os.environ.get("TURNOS_SENTRY_SIGNALS", "").lower()
        names = {part.strip() for part in raw.replace(";", ",").split(",") if part.strip()}
        if "all" in names:
            return frozenset(launch_signals.FEATURES)
        return frozenset(names & set(launch_signals.FEATURES))


def _with_raw(name, raw):
    return _env.patched_env(**{name: raw})


# ---- nothing reads a declared key around its class ----------------------------------------------------------

def _module_constants(tree):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def _is_environ(node):
    return (isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name)
            and node.value.id in ("os", "_os"))


def direct_reads(rel, text, declared):
    """[(rel, name, line)] of the declared names this file reads from the environment itself."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    consts = _module_constants(tree)

    def name_of(arg):
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.Name):
            return consts.get(arg.id)
        return None

    found = []
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Call) and node.args:
            f = node.func
            reader = (
                (isinstance(f, ast.Attribute) and f.attr in ("get", "pop", "setdefault") and _is_environ(f.value))
                or (isinstance(f, ast.Attribute) and f.attr == "getenv")
                or (isinstance(f, ast.Attribute) and f.attr == "environ_text")
                or (isinstance(f, ast.Name) and f.id in ("getenv", "environ_text")))
            if reader:
                key = name_of(node.args[0])
        elif isinstance(node, ast.Subscript) and _is_environ(node.value):
            key = name_of(node.slice)
        elif isinstance(node, ast.Compare) and any(_is_environ(c) for c in node.comparators):
            key = name_of(node.left)
        if key in declared:
            found.append((rel, key, node.lineno))
    return found


def _runtime_python_files():
    for rel in env_registry._files():
        if rel.endswith(".py") and env_registry.is_code(rel) and not rel.startswith(_NOT_RUNTIME) \
                and rel != "app/core/settings.py":
            yield rel


class TestNoDirectReads(unittest.TestCase):
    def test_product_code_reads_the_process_keys_only_through_core_settings(self):
        declared = set(settings.declared_names())
        hits = []
        for rel in _runtime_python_files():
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                hits.extend(direct_reads(rel, fh.read(), declared))
        unexpected = sorted(f"{rel}:{line} {name}" for rel, name, line in hits if (rel, name) not in ALLOWED_DIRECT)
        self.assertEqual(unexpected, [], "read it from its class in core/settings.py")
        stale = sorted(set(ALLOWED_DIRECT) - {(rel, name) for rel, name, _ in hits})
        self.assertEqual(stale, [], "an exception no code needs any more: delete it from ALLOWED_DIRECT")

    def test_the_scanner_sees_every_form_of_a_read(self):
        source = ('import os\nFLAG = "TURNOS_ASGI"\n'
                  'a = os.environ.get("PORT")\nb = os.environ["HOST"]\nc = "DATABASE_URL" in os.environ\n'
                  'd = os.getenv(FLAG)\ne = config.environ_text("TURNOS_TIMEZONE")\nf = os.environ.get("OTHER")\n')
        names = [n for _, n, _ in direct_reads("m.py", source, set(settings.declared_names()))]
        self.assertEqual(names, ["PORT", "HOST", "DATABASE_URL", "TURNOS_ASGI", "TURNOS_TIMEZONE"])
        self.assertEqual(direct_reads("bad.py", "def (:\n", {"PORT"}), [])

    def test_every_declared_name_is_unique_and_in_the_registry(self):
        names = settings.declared_names()
        self.assertEqual(len(names), len(set(names)))
        listed = env_registry.registry_names()
        self.assertEqual(sorted(set(names) - listed), [])


# ---- every migrated reader answers what it answered before -----------------------------------------------

class TestSameAnswersAsBefore(unittest.TestCase):
    def _each(self, name):
        for raw in RAW:
            with self.subTest(name=name, raw=raw), _with_raw(name, raw):
                yield raw

    def test_pool_size_and_threadpool(self):
        for _ in self._each("TURNOS_DB_POOL"):
            self.assertEqual(db._pool_size(), _Before.env_size("TURNOS_DB_POOL", 15))
        for _ in self._each("TURNOS_THREADPOOL_SIZE"):
            self.assertEqual(concurrency.threadpool_size(), _Before.env_size("TURNOS_THREADPOOL_SIZE", 40))

    def test_size_or_is_the_old_env_size(self):
        for raw in RAW:
            with self.subTest(raw=raw), _with_raw("TURNOS_SIZE_PROBE", raw):
                self.assertEqual(settings.size_or(raw, 7), _Before.env_size("TURNOS_SIZE_PROBE", 7))

    def test_pool_timeout(self):
        for _ in self._each("TURNOS_DB_POOL_TIMEOUT"):
            self.assertTrue(_same(db._pool_timeout(), _Before.pool_timeout()))

    def test_web_statement_and_lock_limits(self):
        self.addCleanup(db.disable_web_timeouts)
        for name, default in (("TURNOS_DB_STATEMENT_TIMEOUT", 30.0), ("TURNOS_DB_LOCK_TIMEOUT", 5.0)):
            for _ in self._each(name):
                got = db.enable_web_timeouts()
                want = (_Before.timeout_ms("TURNOS_DB_STATEMENT_TIMEOUT", 30.0),
                        _Before.timeout_ms("TURNOS_DB_LOCK_TIMEOUT", 5.0))
                self.assertEqual(got, want)

    def test_worker_graceful_health_port_and_stalled(self):
        for _ in self._each("TURNOS_WORKER_GRACEFUL_TIMEOUT"):
            self.assertTrue(_same(settings.ProcessSettings.current().worker_graceful_timeout, _Before.graceful()))
            want = (max(_Before.graceful(), queue_infra.POOL_OPEN_TIMEOUT_S) + queue_infra.POOL_CLOSE_TIMEOUT_S
                    + queue_infra.CORE_DB_POOL_CLOSE_TIMEOUT_S + queue_infra.SUPERVISOR_SIGKILL_MARGIN_S)
            self.assertTrue(_same(queue_infra.supervisor_sigkill_budget(), want))
        for _ in self._each("TURNOS_WORKER_HEALTH_PORT"):
            self.assertEqual(settings.ProcessSettings.current().worker_health_port, _Before.health_port())
        from app import worker
        for _ in self._each("TURNOS_WORKER_STALLED_S"):
            got, want = _outcome(worker._stalled_seconds), _outcome(_Before.stalled)
            self.assertEqual(len(got), len(want))
            self.assertTrue(all(_same(a, b) for a, b in zip(got, want)), (got, want))

    def test_queue_worker_mode_and_its_raw_text(self):
        for raw in self._each("TURNOS_QUEUE_WORKER"):
            self.assertEqual(queue_infra.worker_mode(), _Before.worker_mode())
            self.assertEqual(settings.ProcessSettings.current().queue_worker, raw)

    def test_switches_that_are_exactly_one(self):
        for raw in self._each("TURNOS_ASGI"):
            self.assertEqual(proxy_trust._asgi_activo(), raw == "1")
        for raw in self._each("TURNOS_CONFIG_LOCK"):
            self.assertEqual(config.locked(), raw == "1")
        for raw in self._each("TURNOS_OUTBOUND_KILL"):
            self.assertEqual(outbound_guard.requested(), (raw or "").strip() == "1")

    def test_paths_and_names_taken_as_written(self):
        for raw in self._each("TURNOS_ENV_FILE"):
            self.assertEqual(config._env_path(), raw or config.ENV_PATH)
        for raw in self._each("TURNOS_WEBDATA_DIR"):
            want = os.path.abspath(raw) if raw else os.path.join(sitedata._DIR, "webdata")
            self.assertEqual(sitedata.webdata_dir(), want)
            if raw:
                self.assertEqual(data_packs.resolve_path("x.json"), os.path.join(os.path.abspath(raw), "x.json"))
                self.assertIsNone(data_packs.active_chain())
        with mock.patch.object(data_packs, "value", return_value="Europe/Madrid"):
            for raw in self._each("TURNOS_TIMEZONE"):
                self.assertEqual(tz._load_tz_name(), raw or "Europe/Madrid")

    def test_sentry_keys(self):
        for raw in self._each("TURNOS_SENTRY_DSN"):
            self.assertEqual(error_tracking.enabled(), bool((raw or "").strip()))
        for raw in self._each("TURNOS_SENTRY_ENV"):
            self.assertEqual(settings.ProcessSettings.current().sentry_env, "production" if raw is None else raw)
        for _ in self._each("TURNOS_SENTRY_SIGNALS"):
            self.assertEqual(launch_signals.features(), _Before.features())

    def test_host_and_port(self):
        for name in ("HOST", "PORT"):
            for _ in self._each(name):
                got = _outcome(lambda: (settings.ServerSettings.current().host, settings.ServerSettings.current().port))
                self.assertEqual(got, _outcome(_Before.host_port))

    def test_a_port_that_is_not_a_number_stops_the_boot_with_the_same_error(self):
        with _with_raw("PORT", "abc"), self.assertRaises(ValueError) as caught:
            settings.ServerSettings.current()
        self.assertNotIsInstance(caught.exception, pydantic.ValidationError)
        with self.assertRaises(ValueError) as before:
            int("abc")
        self.assertEqual(str(caught.exception), str(before.exception))

    def test_a_validation_error_without_a_value_error_is_raised_as_it_is(self):
        with self.assertRaises(pydantic.ValidationError):
            _StrictPort.current()


class _StrictPort(settings.ServerSettings):
    """A port typed without the old parse: pydantic's own error, no ValueError behind it."""
    port: int = settings.Field(7700, validation_alias="PORT")

    @classmethod
    def _inputs(cls):
        return ("not-cached",)

    def __init__(self, **values):
        super().__init__(PORT=[1], **values)


class TestSameAnswersWithTheEnvFile(unittest.TestCase):
    """The keys that fall back to the env file, over every pair (environment value, file value)."""

    def _pairs(self, name):
        for raw in RAW:
            for in_file in FILE:
                file = {} if in_file is None else {name: in_file}
                with self.subTest(name=name, raw=raw, in_file=in_file), _with_raw(name, raw), \
                        mock.patch.object(config, "dotenv_values", return_value=dict(file)):
                    yield file

    def test_database_url_and_migration_url(self):
        for file in self._pairs("DATABASE_URL"):
            self.assertEqual(db._db_url(), _Before.db_url(file))
        for file in self._pairs("TURNOS_DB_MIGRATION_URL"):
            self.assertEqual(db_schema._migration_url(), _Before.migration_url(file))

    def test_public_origin(self):
        for file in self._pairs("TURNOS_PUBLIC_ORIGIN"):
            origin = _Before.public_origin(file)
            self.assertEqual(settings.InstallationSettings.current().public_origin, origin)
            self.assertEqual(config.installation_host(), _host_before(origin))
            self.assertEqual(config.government_installation(), _government_before(origin))
            self.assertEqual(admin_csrf_middleware._public_origin_key(), _origin_key_before(origin))

    def test_install_owned_pack_keys(self):
        for name in ("TURNOS_PRIVATE_PACKS_DIR", "TURNOS_DATA_DIR", "TURNOS_REQUIRED_PACKS"):
            for file in self._pairs(name):
                value = _Before.boot_setting(name, file)
                if name == "TURNOS_PRIVATE_PACKS_DIR":
                    self.assertEqual(data_packs.private_packs_dir(), os.path.abspath(value) if value else None)
                elif name == "TURNOS_DATA_DIR":
                    self.assertEqual(data_packs._data_dir(),
                                     os.path.abspath(value) if value else os.path.join(data_packs._DIR, "data"))
                else:
                    want = () if not value else tuple(dict.fromkeys(
                        p.strip() for p in value.split(",") if p.strip()))
                    self.assertEqual(data_packs._required_pack_ids(), want)
                field = {"TURNOS_PRIVATE_PACKS_DIR": "private_packs_dir", "TURNOS_DATA_DIR": "data_dir",
                         "TURNOS_REQUIRED_PACKS": "required_packs"}[name]
                self.assertEqual(getattr(settings.InstallationSettings.current(), field), value)


def _host_before(origin):
    from urllib.parse import urlsplit
    if "//" not in origin:
        origin = "//" + origin
    try:
        return (urlsplit(origin).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _government_before(origin):
    from urllib.parse import urlsplit
    if "//" not in origin:
        origin = "//" + origin
    try:
        host = (urlsplit(origin).hostname or "").rstrip(".")
    except ValueError:
        return True
    return host == "gob.ar" or host.endswith(".gob.ar")


def _origin_key_before(origin):
    from urllib.parse import urlsplit
    if not origin:
        return None
    try:
        parts = urlsplit(origin)
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    if scheme not in admin_csrf_middleware._DEFAULT_PORT or not parts.netloc:
        return None
    return scheme, admin_csrf_middleware._host_key(parts.netloc, scheme)


# ---- reading is lazy ------------------------------------------------------------------------------------

class TestLazyReading(unittest.TestCase):
    def test_a_changed_variable_is_seen_on_the_next_read_without_a_reset(self):
        with _env.patched_env(TURNOS_DB_POOL="4"):
            self.assertEqual(settings.ProcessSettings.current().db_pool, 4)
            with _env.patched_env(TURNOS_DB_POOL="9"):
                self.assertEqual(settings.ProcessSettings.current().db_pool, 9)
            self.assertEqual(settings.ProcessSettings.current().db_pool, 4)

    def test_the_same_inputs_give_the_same_frozen_instance(self):
        with _env.patched_env(TURNOS_DB_POOL="4"):
            first = settings.ProcessSettings.current()
            self.assertIs(settings.ProcessSettings.current(), first)
            with self.assertRaises(pydantic.ValidationError):
                first.db_pool = 5

    def test_a_changed_env_file_is_seen_on_the_next_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "turnos.env")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("DATABASE_URL=postgres://one@h/db\nTURNOS_PUBLIC_ORIGIN=https://a.example\n")
            with _env.patched_env(TURNOS_ENV_FILE=path, DATABASE_URL=None, TURNOS_PUBLIC_ORIGIN=None):
                config._reset_cache_para_tests()
                self.addCleanup(config._reset_cache_para_tests)
                self.assertEqual(settings.DatabaseSettings.current().database_url, "postgres://one@h/db")
                self.assertEqual(config.installation_host(), "a.example")
                self.assertTrue(config.save_env_keys({"DATABASE_URL": "postgres://two@h/db"}))
                self.assertEqual(db._db_url(), "postgres://two@h/db")
                with _env.patched_env(DATABASE_URL="  postgres://env@h/db "):
                    self.assertEqual(db._db_url(), "postgres://env@h/db")

    def test_the_constructor_takes_typed_values_by_variable_name(self):
        built = settings.ProcessSettings(TURNOS_DB_POOL=3, TURNOS_ASGI=True, TURNOS_WORKER_GRACEFUL_TIMEOUT=2.5)
        self.assertEqual((built.db_pool, built.asgi, built.worker_graceful_timeout), (3, True, 2.5))
        self.assertEqual(settings.WorkerSettings(TURNOS_WORKER_STALLED_S=12.5).stalled_s, 12.5)

    def test_an_unreadable_stalled_threshold_breaks_no_other_read(self):
        with _env.patched_env(TURNOS_WORKER_STALLED_S="abc", TURNOS_QUEUE_WORKER="1"):
            self.assertEqual(queue_infra.worker_mode(), "1")
            with self.assertRaises(ValueError):
                settings.WorkerSettings.current()

    def test_a_value_that_moves_while_the_instance_is_built_is_not_memoized_under_the_old_key(self):
        with _env.patched_env(TURNOS_DB_POOL="8"):
            stale = settings.ProcessSettings._inputs()
        with _env.patched_env(TURNOS_DB_POOL="9"):
            fresh = settings.ProcessSettings._inputs()
            with mock.patch.object(settings.ProcessSettings, "_inputs", side_effect=[stale, fresh, fresh]):
                self.assertEqual(settings.ProcessSettings.current().db_pool, 9)
            self.assertEqual(settings._MEMO[settings.ProcessSettings][0], fresh)
        with _env.patched_env(TURNOS_DB_POOL="8"):
            self.assertEqual(settings.ProcessSettings.current().db_pool, 8)

    def test_the_env_file_is_opened_only_when_a_field_is_not_set_in_the_environment(self):
        broken = mock.Mock(side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"))
        with mock.patch.object(config, "dotenv_values", broken):
            with _env.patched_env(DATABASE_URL="postgres://e@h/db", TURNOS_DB_MIGRATION_URL="postgres://m@h/db"):
                self.assertEqual(db._db_url(), "postgres://e@h/db")
            with _env.patched_env(TURNOS_PUBLIC_ORIGIN="https://a.example", TURNOS_PRIVATE_PACKS_DIR="/p",
                                  TURNOS_DATA_DIR="/d", TURNOS_REQUIRED_PACKS="x"):
                self.assertEqual(config.installation_host(), "a.example")
            self.assertEqual(broken.call_count, 0)
            with _env.patched_env(DATABASE_URL="  "), self.assertRaises(UnicodeDecodeError):
                db._db_url()

    def test_an_env_file_edited_from_outside_is_seen_without_a_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "turnos.env")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("TURNOS_PUBLIC_ORIGIN=https://one.example\n")
            with _env.patched_env(TURNOS_ENV_FILE=path, TURNOS_PUBLIC_ORIGIN=None):
                config._reset_cache_para_tests()
                self.addCleanup(config._reset_cache_para_tests)
                self.assertEqual(config.installation_host(), "one.example")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("TURNOS_PUBLIC_ORIGIN=https://two.example\n")
                later = os.path.getmtime(path) + 5
                os.utime(path, (later, later))
                self.assertEqual(config.installation_host(), "two.example")

    def test_only_the_exact_variable_name_counts(self):
        with _env.patched_env(PORT=None, port="81", turnos_db_pool="2", db_pool="3", TURNOS_DB_POOL=None):
            self.assertEqual(settings.ServerSettings.current().port, 7700)
            self.assertEqual(settings.ProcessSettings.current().db_pool, 15)

    def test_only_the_environment_counts_for_the_process_keys(self):
        """A process key in the env file does not move ProcessSettings / ServerSettings."""
        file = {"TURNOS_DB_POOL": "2", "PORT": "9", "TURNOS_CONFIG_LOCK": "1", "TURNOS_ASGI": "1"}
        with _env.patched_env(TURNOS_DB_POOL=None, PORT=None, TURNOS_CONFIG_LOCK=None, TURNOS_ASGI=None), \
                mock.patch.object(config, "dotenv_values", return_value=file):
            process = settings.ProcessSettings.current()
            self.assertEqual((process.db_pool, process.config_lock, process.asgi), (15, False, False))
            self.assertEqual(settings.ServerSettings.current().port, 7700)


if __name__ == "__main__":
    unittest.main()
