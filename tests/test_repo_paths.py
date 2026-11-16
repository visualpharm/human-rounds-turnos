"""test_repo_paths.py — core/paths.py is the one place a module finds the repository on disk.

Pins what the roots point at, that the root web assets are where the static server and the
fingerprint registry look for them, and — the ratchet — that no other module of the application
derives the repository root from its own `__file__` (`dirname(dirname(__file__))`,
`Path(__file__).parents[1]`, `dirname(pkg.ROOT)`): a module that does so writes its data to a
different place the day it moves (in August a moved `account_photo.py` nearly stored profile
photos under `cuentas/data/`).
"""

import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1")

from app.core import paths  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.services import static_assets  # noqa: E402


def _application_files():
    """Every .py of the application packages (a top-level directory with an __init__.py), and
    the application package's own modules once the code has a package of its own."""
    if paths.CODE_DIR != ".":
        for f in sorted(os.listdir(paths.CODE_ROOT)):
            if f.endswith(".py"):
                yield os.path.join(paths.CODE_ROOT, f)
    for top in sorted(os.listdir(paths.CODE_ROOT)):
        base = os.path.join(paths.CODE_ROOT, top)
        if not os.path.isfile(os.path.join(base, "__init__.py")) or top in ("test", "tests", "scripts"):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for f in filenames:
                if f.endswith(".py"):
                    yield os.path.join(dirpath, f)


def _mentions_file(node):
    return any(isinstance(n, ast.Name) and n.id == "__file__" for n in ast.walk(node))


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def root_derivations(source):
    """(line, what) for every way `source` climbs from a module's location to the repository."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _call_name(node) == "dirname" and node.args:
            inner = node.args[0]
            if isinstance(inner, ast.Call) and _call_name(inner) == "dirname" and _mentions_file(inner):
                found.append((node.lineno, "dirname(dirname(__file__))"))
            elif isinstance(inner, ast.Attribute) and inner.attr in ("ROOT", "_ROOT"):
                found.append((node.lineno, "dirname(<package>.%s)" % inner.attr))
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "parents" and _mentions_file(node.value):
            found.append((node.lineno, "Path(__file__).parents[...]"))
        elif isinstance(node, ast.Attribute) and node.attr == "parent" \
                and isinstance(node.value, ast.Attribute) and node.value.attr == "parent" \
                and _mentions_file(node.value):
            found.append((node.lineno, "Path(__file__).parent.parent"))
    # one finding per line: an outer dirname wraps the inner one
    return sorted(set(found))


class TestRoots(unittest.TestCase):
    def test_the_roots(self):
        self.assertTrue(os.path.isfile(os.path.join(paths.PROJECT_ROOT, "Dockerfile")))
        self.assertTrue(os.path.isfile(os.path.join(paths.PROJECT_ROOT, "requirements.txt")))
        self.assertTrue(os.path.isfile(os.path.join(paths.CODE_ROOT, "core", "paths.py")))
        self.assertEqual(os.path.normpath(os.path.join(paths.PROJECT_ROOT, paths.CODE_DIR)),
                         paths.CODE_ROOT)
        self.assertEqual(paths.project_path("data"), os.path.join(paths.PROJECT_ROOT, "data"))
        self.assertEqual(paths.code_path("modules"), os.path.join(paths.CODE_ROOT, "modules"))

    def test_every_root_web_asset_is_where_the_server_looks(self):
        for name in sorted(paths.ROOT_WEB_ASSETS):
            self.assertTrue(os.path.isfile(paths.root_web_asset(name)), name)
        with self.assertRaises(ValueError):
            paths.root_web_asset("app/core/db.py")

    def test_the_fingerprint_registry_and_the_asset_list_agree(self):
        flat = {name for name in static_fingerprint.MIGRATED if "/" not in name}
        self.assertEqual(sorted(flat - paths.ROOT_WEB_ASSETS), [])
        self.assertEqual(sorted(set(static_fingerprint._PASSTHROUGH) - paths.ROOT_WEB_ASSETS), [])
        for name in flat:
            self.assertEqual(static_fingerprint._physical_path(name), paths.root_web_asset(name))

    def test_a_public_path_finds_its_directory(self):
        code = os.path.normpath(os.path.join(static_assets.APP_ROOT, paths.CODE_DIR))
        assets = os.path.normpath(os.path.join(static_assets.APP_ROOT, paths.ROOT_WEB_ASSETS_DIR))
        self.assertEqual(static_assets.disk_root("modules/turnos/confirm.mjs"), code)
        self.assertEqual(static_assets.disk_root("admin-app.js"), assets)
        self.assertEqual(static_assets.disk_root("static/platform/nav.mjs"), static_assets.APP_ROOT)
        self.assertEqual(static_assets.disk_root("vendor/lottie/lottie_light.min.js"), static_assets.APP_ROOT)


# The entry modules may be run as files (`python3 app/worker.py`): before any import they put the
# project root first on sys.path, and only then (`if not __package__:`). Nothing else may.
ENTRY_BOOTSTRAP = "    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))"
ENTRY_MODULES = ("asgi_main.py", "worker.py", "manage.py")


class TestNoOtherRootDerivation(unittest.TestCase):
    def test_only_paths_py_derives_the_repository_root(self):
        own = os.path.abspath(paths.__file__)
        bad = []
        for path in _application_files():
            if os.path.abspath(path) == own:
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            lines = text.split("\n")
            entry = paths.CODE_DIR != "." and os.path.dirname(os.path.abspath(path)) == paths.CODE_ROOT \
                and os.path.basename(path) in ENTRY_MODULES
            for line, what in root_derivations(text):
                if entry and lines[line - 1] == ENTRY_BOOTSTRAP and lines[line - 2].startswith("if not __package__:"):
                    continue
                bad.append("%s:%d %s" % (os.path.relpath(path, ROOT), line, what))
        self.assertEqual(bad, [], "read the root from core/paths.py (PROJECT_ROOT / CODE_ROOT)")

    def test_importing_an_entry_module_leaves_sys_path_alone(self):
        import importlib
        for name in ("worker", "manage", "asgi_main"):
            module = "%s.%s" % (paths.__name__.rsplit(".", 2)[0], name) if paths.CODE_DIR != "." else name
            with self.subTest(module=module):
                before = list(sys.path)
                importlib.import_module(module)
                self.assertNotIn(paths.CODE_ROOT, [p for p in sys.path if p not in before] if
                                 paths.CODE_DIR != "." else [])

    def test_the_guard_goes_red_on_each_shape(self):
        for source in (
                "import os\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n",
                "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n",
                "from pathlib import Path\nROOT = Path(__file__).parent.parent\n",
                "import os\nfrom app import connectors as c\nROOT = os.path.dirname(c.ROOT)\n",
        ):
            with self.subTest(source=source):
                self.assertTrue(root_derivations(source))
        for source in (
                "import os\nHERE = os.path.dirname(os.path.abspath(__file__))\n",
                "from pathlib import Path\nDEF = Path(__file__).with_name('x.json')\n",
                "from app.core import paths\nROOT = paths.PROJECT_ROOT\n",
        ):
            with self.subTest(source=source):
                self.assertEqual(root_derivations(source), [])


if __name__ == "__main__":
    unittest.main()
