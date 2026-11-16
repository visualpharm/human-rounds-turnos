"""test_asgi_main_dark_mode_retirement.py — pins the NEW contract for
asgi_main.py after the rollback-artifact retirement (native-migration
campaign, family W3b, docs/roadmap/native-migration-campaign-20260826.md):
this entrypoint no longer has a dark-mode fallback.

Before: `TURNOS_ASGI` unset/not "1" made `asgi_main.main()` `import server`
and call `server.main()` directly (byte-compatible rollback topology).
After: any value other than "1" is a hard configuration error — `main()`
raises instead, and this MODULE never imports `server` at all. The final
campaign wave then retired `server.py` itself along with the rest of the
legacy dispatch topology — asgi_main.py is the ONE process entrypoint, and
`TestLegacyEntrypointRetired` below pins that.

`tests/test_transport_shapes.py` is the source-level version of the same check
(no module imports the retired `server`, by any alias, anywhere in production
source); this test pins the RUNTIME behavior on top of that.
"""

import os
import re
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app import asgi_main  # noqa: E402


class TestDarkModeFallbackRetired(unittest.TestCase):
    def test_module_has_no_dark_mode_function(self):
        self.assertFalse(hasattr(asgi_main, "_dark_mode"))

    def test_module_source_never_imports_server(self):
        """The load-bearing rollback check — matched as an actual `import`
        statement (regex, not a bare substring test): both
        `_verify_required_packs()`'s "Do not import server here" comment and
        `_start_queue_worker_supervisor()`'s docstring (describing what
        `server.py`'s OWN `main()` does when run standalone — unrelated to
        this file's call graph) legitimately contain the words "import
        server"/"server.main()" without either being present as executable
        code here."""
        with open(asgi_main.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertIsNone(re.search(r"^\s*import server\s*$", source, re.MULTILINE))
        self.assertNotIn("def _dark_mode", source)

    def test_main_refuses_without_turnos_asgi_and_never_touches_server(self):
        _env.set_env(self, TURNOS_ASGI=None)
        with patch.object(asgi_main, "_verify_required_packs") as verify:
            with patch.object(asgi_main, "_start_queue_worker_supervisor") as worker:
                with patch.object(asgi_main, "_asgi_mode") as asgi_mode:
                    with patch.dict(sys.modules, {"server": None}):
                        with self.assertRaises(SystemExit):
                            asgi_main.main()
        verify.assert_called_once_with()
        worker.assert_not_called()
        asgi_mode.assert_not_called()

    def test_main_refuses_on_any_non_1_value(self):
        for bad in ("0", "true", "yes", ""):
            with self.subTest(value=bad):
                _env.set_env(self, TURNOS_ASGI=bad)
                with patch.object(asgi_main, "_verify_required_packs"):
                    with patch.object(asgi_main, "_start_queue_worker_supervisor") as worker:
                        with patch.object(asgi_main, "_asgi_mode") as asgi_mode:
                            with self.assertRaises(SystemExit):
                                asgi_main.main()
                worker.assert_not_called()
                asgi_mode.assert_not_called()

    def test_main_still_serves_asgi_when_flag_is_set(self):
        _env.set_env(self, TURNOS_ASGI="1")
        with patch.object(asgi_main, "_verify_required_packs") as verify:
            with patch.object(asgi_main, "_start_queue_worker_supervisor") as worker:
                with patch.object(asgi_main, "_asgi_mode") as asgi_mode:
                    asgi_main.main()
        self.assertEqual(verify.call_count, 1)
        worker.assert_called_once_with()
        asgi_mode.assert_called_once_with()


class TestLegacyEntrypointRetired(unittest.TestCase):
    """The final campaign wave deleted the whole executable legacy topology:
    entrypoint, process lifecycle, transport handler, shim, and dispatcher.
    A file reappearing here would silently re-arm a rollback topology that
    counts against the refactor's completion (`test_transport_shapes.py`
    also fails on any import of these modules) — fail loudly instead."""

    def test_legacy_topology_files_stay_deleted(self):
        for relative in (
            "server.py",
            "ops/legacy_web_server.py",
            "routers/legacy_http_transport.py",
            "legacy_shim.py",
            "services/legacy_http.py",
            "services/legacy_static.py",
            "core/routing.py",
            "core/module_router.py",
        ):
            with self.subTest(file=relative):
                self.assertFalse(os.path.exists(os.path.join(ROOT, relative)))


if __name__ == "__main__":
    unittest.main()
