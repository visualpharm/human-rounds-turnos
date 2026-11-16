"""Keep the real app lifespan's background threads out of unit tests.

Entering the composed app's lifespan (`with TestClient(app_module.app)`) runs
`ops.web_runtime.start_background_services`: ai-health, cache-warmup,
housekeeping, reminders, waitlist, order retention and chart-summary
pre-generation. They are daemon loops and nothing ever stops them, so a module
that starts them for real leaves them running for the rest of its process.
In the one-process backend run (TURNOS_BACKEND_SERIAL=1) that reaches every
later module: cache-warmup re-reads auth_mode and config every 2 s and
publishes what it read, so a read begun before a later test pinned its own
mode overwrites the pin (test_renaper_auth: 404 test_auth_no_disponible), and
the sweeps touch whatever database the next module has just created.

A module that enters the lifespan calls `no_background_services()` from its
setUpModule; the services have their own tests (test_web_runtime and friends).
"""
import threading
import unittest
from unittest import mock

LIFESPAN_THREADS = frozenset({
    "ai-health", "avatar-warmup", "cache-warmup", "chart-summary-pregen", "housekeeping",
    "recordatorios", "retencion-ordenes", "waitlist",
})


def leaked_threads():
    return sorted(t.name for t in threading.enumerate() if t.name in LIFESPAN_THREADS and t.is_alive())


def _assert_none_left():
    left = leaked_threads()
    assert not left, "this module left app background threads running: %s" % left


def no_background_services():
    """Call from setUpModule: patch start_background_services away for the module and
    fail it at the end if any lifespan thread is still alive."""
    patcher = mock.patch("app.ops.web_runtime.start_background_services")
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    unittest.addModuleCleanup(_assert_none_left)  # LIFO: checked first, then the patch is undone
