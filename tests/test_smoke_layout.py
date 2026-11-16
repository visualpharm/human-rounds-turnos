"""Both post-deploy smokes measure the public lists' appointment cell in a browser
(scripts/smoke_layout.py → scripts/smoke_listing_layout.mjs)."""

import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("smoke_layout_t", ROOT / "scripts/smoke_layout.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _done(stdout, code=0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="")


OK6 = "\n".join(f"[OK] celda de turnos /p{i} @w (3 filas)" for i in range(6))


class SmokeLayoutTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def run_layout(self, stdout, code=0, auth=None):
        calls = []

        def fake_run(argv, **kw):
            if argv[0] == "git":
                return _done(str(ROOT / ".git") + "\n")
            calls.append((argv, kw["env"]))
            return _done(stdout, code)

        with patch.object(self.mod.subprocess, "run", side_effect=fake_run):
            result = self.mod.listing_layout("https://x.test", ("/a", "/b", "/c"), auth)
        return result, calls

    def test_six_measurements_are_green(self):
        (fails, lines), _ = self.run_layout(OK6)
        self.assertEqual(fails, 0)
        self.assertEqual(len(lines), 6)

    def test_a_silent_run_is_not_green(self):
        (fails, lines), _ = self.run_layout("")
        self.assertEqual(fails, 1)
        self.assertIn("0 de 6", lines[-1])

    def test_a_broken_cell_fails(self):
        (fails, _), _ = self.run_layout(OK6 + "\n[FALLA] celda de turnos /a @390: fila 1: x", code=1)
        self.assertEqual(fails, 1)

    def test_the_credential_rides_in_the_environment_never_in_argv(self):
        secret = "user:" + "not-a-real-one"
        _, calls = self.run_layout(OK6, auth=secret)
        argv, env = calls[0]
        self.assertNotIn(secret, " ".join(argv))
        self.assertEqual(env["SMOKE_LAYOUT_AUTH"], secret)

    def test_a_hang_keeps_what_was_measured(self):
        def hang(argv, **kw):
            if argv[0] == "git":
                return _done("")
            raise subprocess.TimeoutExpired(argv, 1, output=b"[OK] celda de turnos /a @390 (3 filas)\n")

        with patch.object(self.mod.subprocess, "run", side_effect=hang):
            fails, lines = self.mod.listing_layout("https://x.test", ("/a",))
        self.assertEqual(fails, 1)
        self.assertTrue(lines[0].startswith("[OK]"))
        self.assertIn("tiempo agotado", lines[-1])

    def test_every_kind_of_table_on_both_sites(self):
        for pages in (self.mod.PAGES_PROD, self.mod.PAGES_DEMO):
            self.assertTrue(pages[0].startswith("/center/"))
            self.assertIn("/d/cardiologia", pages)
            self.assertIn("/doctors", pages)

    def test_both_smokes_call_it(self):
        prod = (ROOT / "scripts/smoke_prod.py").read_text(encoding="utf-8")
        demo = (ROOT / "scripts/smoke_demo.py").read_text(encoding="utf-8")
        self.assertIn("smoke_layout.PAGES_PROD", prod)
        self.assertIn("smoke_layout.PAGES_DEMO", demo)


if __name__ == "__main__":
    unittest.main()
