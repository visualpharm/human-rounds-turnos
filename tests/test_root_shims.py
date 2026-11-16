"""test_root_shims.py — the old root commands still start the same programs (docs/LAYOUT.md).

`python3 asgi_main.py`, `python3 worker.py ...` and `python3 manage.py ...` keep working through
three root files that run the application package's module as the main program, exactly as
`python3 -m app.<x>` would; `python3 -m app` is the image's own entrypoint. Importing a shim by
its old module name is an error, so nothing patches or reads the shim instead of the real module.
"""

import importlib.util
import os
import runpy
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SHIMS = {
    "asgi_main.py": "app.asgi_main",
    "worker.py": "app.worker",
    "manage.py": "app.manage",
}


class TestRootShims(unittest.TestCase):
    def test_each_shim_runs_its_module_as_the_main_program(self):
        for name, module in SHIMS.items():
            with self.subTest(shim=name), mock.patch("runpy.run_module") as run:
                runpy.run_path(os.path.join(ROOT, name), run_name="__main__")
            run.assert_called_once_with(module, run_name="__main__", alter_sys=True)

    def test_a_runner_with_its_own_run_name_still_starts_it(self):
        # the local dev runner executes asgi_main.py through runpy under its own name
        with mock.patch("runpy.run_module") as run:
            runpy.run_path(os.path.join(ROOT, "asgi_main.py"), run_name="preview_runner")
        run.assert_called_once_with("app.asgi_main", run_name="__main__", alter_sys=True)

    def test_importing_a_shim_by_its_old_name_is_an_error(self):
        for name, module in SHIMS.items():
            old_name = name[:-3]
            spec = importlib.util.spec_from_file_location(old_name, os.path.join(ROOT, name))
            with self.subTest(shim=name), mock.patch("runpy.run_module") as run:
                with self.assertRaises(ImportError) as caught:
                    spec.loader.exec_module(importlib.util.module_from_spec(spec))
                self.assertIn(module, str(caught.exception))
                run.assert_not_called()

    def test_python_dash_m_app_runs_the_entrypoint(self):
        with mock.patch("runpy.run_module") as run:
            runpy.run_path(os.path.join(ROOT, "app", "__main__.py"), run_name="__main__")
        run.assert_called_once_with("app.asgi_main", run_name="__main__", alter_sys=True)

    def test_the_package_import_is_light(self):
        # `import app` loads nothing of the application by itself: a docstring and no code
        import ast
        with open(os.path.join(ROOT, "app", "__init__.py"), encoding="utf-8") as fh:
            body = ast.parse(fh.read()).body
        self.assertEqual([type(node).__name__ for node in body], ["Expr"])


if __name__ == "__main__":
    unittest.main()
