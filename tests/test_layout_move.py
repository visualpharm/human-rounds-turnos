"""test_layout_move.py — the mechanical layout move (scripts/layout/plan.py, move.py, import_all.py).

Every case runs the real stage in a throwaway git repository shaped like this one: the rename
rules, what is reported instead of rewritten, what is never touched (queue task names, i18n keys,
module asset names, historical records), idempotence and determinism.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "layout"))

import import_all  # noqa: E402
import move  # noqa: E402
import plan  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout


FIXTURE = {
    "core/__init__.py": "",
    "core/db.py": textwrap.dedent('''\
        import os
        ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def connect():
            # pinned by test/test_x.py (see `test/helpers/`); every test/test_*.py.
            return "real"
        '''),
    "core/paths.py": "",
    "connectors/__init__.py": "",
    "connectors/hsi/__init__.py": "",
    "connectors/hsi/hsi_client.py": "def push():\n    return 1\n",
    "modules/__init__.py": "",
    "modules/turnos/__init__.py": "",
    "modules/turnos/waitlist_queue.py": '_TASK_NAME = "modules.turnos.waitlist.sweep"\n',
    "modules/turnos/confirm.mjs": "export const x = 1;\n",
    "modules/turnos/module.json": textwrap.dedent('''\
        {
          "id": "turnos",
          "entrypoint": "modules.turnos.waitlist_queue",
          "routes": [
            {"module": "modules.turnos.waitlist_queue", "methods": ["GET"], "after": {"GET": "core.db"}}
          ],
          "admin_sections": [{"id": "x", "label_key": "core.db", "label": "X"}],
          "assets": [{"path": "modules/turnos/confirm.mjs", "fingerprint": true}]
        }
        '''),
    "app.py": "app = object()\n",
    "worker.py": textwrap.dedent('''\
        import sys
        from core import db
        _MAINT = ("worker.maintenance.remove_old_jobs",)
        def main():
            return db.connect()
        '''),
    "asgi_main.py": "import app as asgi_app\n",
    "test/test_x.py": textwrap.dedent('''\
        import os
        import sys
        from pathlib import Path
        from unittest import mock

        ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        import app as app_module
        import worker
        import connectors
        import connectors.hsi.hsi_client as hsi_client
        from core import db
        from core.db import connect
        from modules.turnos import waitlist_queue

        SOURCE = os.path.join(ROOT, "core", "db.py")
        SOURCE2 = os.path.join(ROOT, "core/db.py")
        SOURCE3 = Path(ROOT) / "core" / "db.py"
        TEXT = (Path(ROOT) / "core" / "db.py").read_text()
        INVENTORY = [("core/db.py", "connect")]
        LISTED = {"core/db.py:connect": "why"}
        REFS = ["core.db:connect", "modules.turnos.waitlist_queue"]
        KEY = "ai.title"
        TASK = "modules.turnos.waitlist.sweep"
        ASSET = "modules/turnos/confirm.mjs"
        HELPERS = os.path.join(ROOT, "test", "helpers")
        PREFIX_OK = "x".startswith("modules/")

        def local_root():
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            return os.path.join(root, "modules", "turnos", "confirm.mjs")
        SCRIPT = "import sys; import core.{first}; from core import db, x; m = sys.modules['core.' + n]"
        LISTED_IMPORTS = "import os, core.db, worker\\nprint(1)"
        CODE = "from core.db import connect\\nimport connectors\\nimport app\\nimport worker as w\\n"
        FCODE = f"""
            sys.path.insert(0, {ROOT!r})
            import worker
            from core import db
        """

        @mock.patch("core.db.connect")
        def case(fake):
            sys.modules["connectors.hsi.hsi_client"] = None
            sys.modules.get("core.db")
            with mock.patch.dict(sys.modules, {"core.db": None}):
                pass
            mock.patch("worker.main")
            return mock.patch("app.app")
        '''),
    "test/helpers/__init__.py": "",
    "test/golden/refs.json": '{"refs": ["core.db:connect", "ai.title", "modules.turnos.waitlist.sweep", "core.db"]}\n',
    "test/e2e/x.test.mjs": textwrap.dedent('''\
        const src = path.join(ROOT, "core", "db.py");
        const rel = new URL("../../core/db.py", import.meta.url);
        const pkg = new URL("../../core/", import.meta.url);
        const story = text.replace('import x from "../static/platform/x.mjs";', "");
        const files = ["modules/turnos/confirm.mjs", "core/db.py"];
        const owner = { module: "modules.turnos.waitlist_queue", key: "ai.title" };
        const py = `import sys
        from core import db
        print(db)`;
        const cont = String.raw`x = (1, \\
        2)
        from auth import admin_auth`;
        import fs from "node:fs";
        '''),
    "ai/__init__.py": "",
    "ai/prompts.py": "PROMPTS = {}\n",
    "routers/__init__.py": "",
    "admin_panel/__init__.py": "",
    "auth/__init__.py": "",
    "cuentas/__init__.py": "",
    "notifications/__init__.py": "",
    "ops/__init__.py": "",
    "patient_web/__init__.py": "",
    "services/__init__.py": "",
    "manage.py": "from core import db\n",
    ".gitlab-ci.yml": "script:\n  - python3 scripts/run.py test/test_x.py core/db.py\n",
    ".gitattributes": "/design-qa.md export-ignore\n/test/test_x.py export-ignore\n",
    "README.md": "See [db](core/db.py) and `core/db.py`; prose core/db.py stays.\n",
    "docs/decisions/2026-11.md": "Historical: core/db.py and `core/db.py`.\n",
    "docs/architecture/x.md": "Link [db](../../core/db.py).\n",
    "scripts/run.py": "print(1)\n",
    "design-qa.md": "notes\n",
    "DATA_MAP.md": "map [db](core/db.py)\n",
    "admin-app.js": "1;\n",
    "human-rounds-mark-padded.png": "png",
    "tools/msd_pull.py": "x = 1\n",
    "test/test_root_assets.py": textwrap.dedent('''\
        import os
        ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        DOC = os.path.join(ROOT, "DATA_MAP.md")
        ASSET = os.path.join(ROOT, "admin-app.js")
        NAME = "admin-app.js"
        PLANS = os.path.join(ROOT, "design-plans")
        with open("admin-app.js") as fh:
            BY_HELPER = _read("DATA_MAP.md")
        CLIMB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "admin-app.js")
        SHORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin-app.js")
        PARENTS = Path(__file__).resolve().parents[1] / "DATA_MAP.md"
        '''),
    "stories/Chart.stories.js": 'import css from "../admin-app.js?raw";\n',
    "static/platform/page.mjs": 'const href = "../../admin-app.js";\n',
    "static/platform/served.mjs": "// pinned by test/test_x.py\nexport const x = 1;\n",
    "templates/served.html": "<!-- test/test_x.py -->\n",
    "test/root-assets.test.mjs": textwrap.dedent('''\
        const css = read("admin-app.js");
        const mod = read('modules/turnos/confirm.mjs');
        const plain = "admin-app.js";
        '''),
    "design-plans/a.md": "plan\n",
    "extensions/x/README.md": "run test/a.test.mjs\n",
    "CONTRIBUTING.md": "python3 -m unittest discover -s test -p \"test_*.py\"; --start-directory=test\n",
    "extensions/x/test/a.test.mjs": "1;\n",
    "test/ext.test.mjs": 'const page = leer("test/a.test.mjs"); // the extension\'s own test/\n',
}


class LayoutRepo:
    """A throwaway repository with FIXTURE committed."""

    def __init__(self, files=FIXTURE):
        self.root = tempfile.mkdtemp(prefix="layout-move-")
        try:
            self._build(files)
        except BaseException:
            self.close()
            raise

    def _build(self, files):
        for rel, content in files.items():
            p = Path(self.root, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-A")
        _git(self.root, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
             "commit", "-q", "-m", "fixture")

    def read(self, rel):
        return Path(self.root, rel).read_text(encoding="utf-8")

    def tree(self):
        _git(self.root, "add", "-A")
        return _git(self.root, "write-tree").strip()

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class PlanTest(unittest.TestCase):
    def test_map_path_moves_entries_whole(self):
        self.assertEqual(plan.map_path("app", "core/db.py"), "app/core/db.py")
        self.assertEqual(plan.map_path("app", "core"), "app/core")
        self.assertEqual(plan.map_path("app", "app.py"), "app/main.py")
        self.assertEqual(plan.map_path("app", "worker.py"), "app/worker.py")
        self.assertIsNone(plan.map_path("app", "corel/x.py"))
        self.assertEqual(plan.map_path("tests", "test/e2e/a.mjs"), "tests/e2e/a.mjs")
        self.assertEqual(plan.map_path("root", "DATA_MAP.md"), "docs/policies/DATA_MAP.md")
        self.assertEqual(plan.map_path("root", "GOVERNANCE.md"), ".github/GOVERNANCE.md")
        self.assertEqual(plan.map_path("root", "admin-app.js"), "static/root/admin-app.js")
        self.assertEqual(plan.map_path("root", "design-plans/a.md"), "docs/design/plans/a.md")

    def test_map_dotted_and_frozen_task_names(self):
        self.assertEqual(plan.map_dotted("app", "core.db.connect"), "app.core.db.connect")
        self.assertEqual(plan.map_dotted("app", "app"), "app.main")
        self.assertEqual(plan.map_dotted("app", "app.app"), "app.main.app")
        self.assertEqual(plan.map_dotted("app", "worker.main"), "app.worker.main")
        self.assertIsNone(plan.map_dotted("app", "fastapi.FastAPI"))
        for name in plan.FROZEN_STRINGS:
            self.assertIsNone(plan.map_dotted("app", name), name)
        self.assertIsNone(plan.map_dotted("tests", "core.db"))

    def test_unknown_stage_is_refused(self):
        with self.assertRaises(ValueError):
            plan.stage_moves("nope")

    def test_every_frozen_name_is_a_real_task_name(self):
        """The frozen list is exactly the queue's task identities on this tree (worker + queues)."""
        names = set()
        for rel in ("worker.py", "app/worker.py"):
            path = os.path.join(ROOT, rel)
            if os.path.exists(path):
                text = Path(path).read_text(encoding="utf-8")
                names.update(n for n in plan.FROZEN_STRINGS if '"%s"' % n in text)
        for pkg_root in (ROOT, os.path.join(ROOT, "app")):
            for sub in ("ops", "modules", "notifications", "ai"):
                base = os.path.join(pkg_root, sub)
                for dirpath, _dirs, files in os.walk(base):
                    for f in files:
                        if f.endswith("_queue.py"):
                            text = Path(dirpath, f).read_text(encoding="utf-8")
                            names.update(n for n in plan.FROZEN_STRINGS if '"%s"' % n in text)
        self.assertEqual(names, set(plan.FROZEN_STRINGS))

    def test_classifiers(self):
        self.assertTrue(plan.is_historical("docs/decisions/2026-11.md"))
        self.assertFalse(plan.is_historical("docs/architecture/x.md"))
        self.assertTrue(plan.is_skipped("vendor/x.js"))
        self.assertTrue(plan.is_text("Dockerfile"))
        self.assertTrue(plan.is_text("a/b.py"))
        self.assertFalse(plan.is_text("a/b.png"))
        self.assertEqual(len(plan.all_moves()), sum(len(plan.MOVES[s]) for s in plan.STAGES))


class AppStageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = LayoutRepo()
        cls.addClassCleanup(cls.repo.close)
        cls.report = move.run_stage(cls.repo.root, "app")
        cls.test_src = cls.repo.read("test/test_x.py")

    def test_files_move_into_the_package(self):
        for rel in ("app/__init__.py", "app/core/db.py", "app/main.py", "app/worker.py",
                    "app/asgi_main.py", "app/manage.py", "app/modules/turnos/module.json"):
            self.assertTrue(os.path.exists(os.path.join(self.repo.root, rel)), rel)
        for gone in ("core", "app.py", "worker.py"):
            self.assertFalse(os.path.exists(os.path.join(self.repo.root, gone)), gone)

    def test_imports_keep_their_local_names(self):
        src = self.test_src
        self.assertIn("from app import main as app_module", src)
        self.assertIn("from app import worker\n", src)
        self.assertIn("from app import connectors\n", src)
        self.assertIn("from app.connectors.hsi import hsi_client\n", src)
        self.assertIn("from app.core import db\n", src)
        self.assertIn("from app.core.db import connect\n", src)
        self.assertIn("from app.modules.turnos import waitlist_queue\n", src)
        self.assertIn("from app.core import db\n", self.repo.read("app/worker.py"))
        self.assertIn("from app import main as asgi_app\n", self.repo.read("app/asgi_main.py"))

    def test_string_targets_follow_the_module(self):
        src = self.test_src
        self.assertIn('@mock.patch("app.core.db.connect")', src)
        self.assertIn('sys.modules["app.connectors.hsi.hsi_client"]', src)
        self.assertIn('sys.modules.get("app.core.db")', src)
        self.assertIn('{"app.core.db": None}', src)
        self.assertIn('mock.patch("app.worker.main")', src)
        self.assertIn('mock.patch("app.main.app")', src)
        self.assertIn('"app.core.db:connect", "app.modules.turnos.waitlist_queue"', src)

    def test_paths_from_the_root_follow_the_file(self):
        src = self.test_src
        self.assertIn('os.path.join(ROOT, "app", "core", "db.py")', src)
        self.assertIn('os.path.join(ROOT, "app/core/db.py")', src)
        self.assertIn('Path(ROOT) / "app" / "core" / "db.py"', src)
        self.assertIn('return os.path.join(root, "app", "modules", "turnos", "confirm.mjs")', src)
        self.assertIn('PREFIX_OK = "x".startswith("modules/")', src)
        self.assertIn('TEXT = (Path(ROOT) / "app" / "core" / "db.py").read_text()', src)
        self.assertIn('[("app/core/db.py", "connect")]', src)
        self.assertIn('{"app/core/db.py:connect": "why"}', src)
        self.assertIn('os.path.join(ROOT, "test", "helpers")', src)  # not this stage's
        js = self.repo.read("test/e2e/x.test.mjs")
        self.assertIn('path.join(ROOT, "app", "core", "db.py")', js)
        self.assertIn('"../../app/core/db.py"', js)
        self.assertIn('new URL("../../app/core/", import.meta.url)', js)  # still a directory
        self.assertIn('["app/modules/turnos/confirm.mjs", "app/core/db.py"]', js)
        # a test's reader reads the module's file from disk: its new place
        self.assertIn("read('app/modules/turnos/confirm.mjs')",
                      self.repo.read("test/root-assets.test.mjs"))

    def test_code_inside_strings_follows_the_module(self):
        src = self.test_src
        self.assertIn('"import os; import app.core.db; from app import worker\\nprint(1)"', src)
        self.assertIn("import sys; import app.core.{first}; from app.core import db, x; "
                      "m = sys.modules['app.core.' + n]", src)
        self.assertIn('"from app.core.db import connect\\nfrom app import connectors\\n'
                      'from app import main as app\\nfrom app import worker as w\\n"', src)
        self.assertIn("    from app import worker\n    from app.core import db\n", src)  # an f-string's text
        js = self.repo.read("test/e2e/x.test.mjs")
        self.assertIn("from app.core import db", js)
        self.assertIn("from app.auth import admin_auth`", js)  # past a backslash line continuation
        self.assertIn('{ module: "app.modules.turnos.waitlist_queue", key: "ai.title" }', js)
        self.assertIn('import fs from "node:fs";', js)

    def test_durable_names_and_public_names_never_change(self):
        src = self.test_src
        self.assertIn('KEY = "ai.title"', src)
        self.assertIn('TASK = "modules.turnos.waitlist.sweep"', src)
        self.assertIn('ASSET = "modules/turnos/confirm.mjs"', src)
        self.assertIn('"modules.turnos.waitlist.sweep"', self.repo.read("app/modules/turnos/waitlist_queue.py"))
        self.assertIn('"worker.maintenance.remove_old_jobs"', self.repo.read("app/worker.py"))
        manifest = json.loads(self.repo.read("app/modules/turnos/module.json"))
        self.assertEqual(manifest["routes"][0]["module"], "app.modules.turnos.waitlist_queue")
        self.assertEqual(manifest["routes"][0]["after"], {"GET": "app.core.db"})
        self.assertEqual(manifest["entrypoint"], "app.modules.turnos.waitlist_queue")
        self.assertEqual(manifest["assets"][0]["path"], "modules/turnos/confirm.mjs")
        # a translation key that happens to look like a module stays a translation key
        self.assertEqual(manifest["admin_sections"][0]["label_key"], "core.db")
        self.assertEqual(json.loads(self.repo.read("test/golden/refs.json"))["refs"],
                         ["app.core.db:connect", "ai.title", "modules.turnos.waitlist.sweep", "app.core.db"])

    def test_text_files_and_documents(self):
        self.assertIn("test/test_x.py app/core/db.py", self.repo.read(".gitlab-ci.yml"))
        readme = self.repo.read("README.md")
        self.assertIn("[db](app/core/db.py)", readme)
        self.assertIn("`app/core/db.py`", readme)
        self.assertIn("prose core/db.py stays", readme)
        self.assertEqual(self.repo.read("docs/decisions/2026-11.md"),
                         FIXTURE["docs/decisions/2026-11.md"])
        self.assertIn("[db](../../app/core/db.py)", self.repo.read("docs/architecture/x.md"))

    def test_report_lists_what_was_left_alone(self):
        kinds = {(p, k, t) for p, _line, k, t in self.report.notes}
        self.assertIn(("test/test_x.py", "path", "modules/turnos/confirm.mjs"), kinds)
        text = self.report.render()
        self.assertTrue(text.startswith("files rewritten: "))
        self.assertIn("NOTE test/test_x.py:", text)
        self.assertIn("rewrote test/test_x.py", text)

    def test_the_stage_does_not_run_twice(self):
        self.assertEqual(move.preflight(self.repo.root, "app"),
                         "the working tree has uncommitted changes")
        _git(self.repo.root, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
             "commit", "-q", "-am", "moved")
        self.assertIn("already applied", move.preflight(self.repo.root, "app"))


class StageTest(unittest.TestCase):
    def test_root_stage(self):
        repo = LayoutRepo()
        self.addCleanup(repo.close)
        report = move.run_stage(repo.root, "root")
        for rel in ("docs/policies/DATA_MAP.md", "static/root/admin-app.js",
                    "docs/design/design-qa.md", "docs/design/plans/a.md", "scripts/tools/msd_pull.py"):
            self.assertTrue(os.path.exists(os.path.join(repo.root, rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(repo.root, "human-rounds-mark-padded.png")))
        src = repo.read("test/test_root_assets.py")
        self.assertIn('os.path.join(ROOT, "docs", "policies", "DATA_MAP.md")', src)
        self.assertIn('os.path.join(ROOT, "static", "root", "admin-app.js")', src)
        self.assertIn('os.path.join(ROOT, "docs", "design", "plans")', src)
        self.assertIn('NAME = "admin-app.js"', src)  # a public name, reported not rewritten
        self.assertIn('with open("static/root/admin-app.js") as fh:', src)
        self.assertIn('BY_HELPER = _read("docs/policies/DATA_MAP.md")', src)
        self.assertIn('os.path.abspath(__file__))), "static", "root", "admin-app.js")', src)
        # one dirname less is the test directory, not the root: left alone
        self.assertIn('SHORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin-app.js")', src)
        self.assertIn('parents[1] / "docs" / "policies" / "DATA_MAP.md"', src)
        js = repo.read("test/root-assets.test.mjs")
        self.assertIn('read("static/root/admin-app.js")', js)
        self.assertIn('const plain = "admin-app.js";', js)
        # a story imports the file from disk; the app's own JavaScript speaks URLs and stays
        self.assertEqual(repo.read("stories/Chart.stories.js"),
                         'import css from "../static/root/admin-app.js?raw";\n')
        self.assertEqual(repo.read("static/platform/page.mjs"), FIXTURE["static/platform/page.mjs"])
        self.assertIn(("test/test_root_assets.py", "root-asset-name", "admin-app.js"),
                      {(p, k, t) for p, _l, k, t in report.notes})
        self.assertIn("/docs/design/design-qa.md export-ignore", repo.read(".gitattributes"))
        self.assertIn("map [db](../../core/db.py)", repo.read("docs/policies/DATA_MAP.md"))

    def test_tests_stage(self):
        repo = LayoutRepo()
        self.addCleanup(repo.close)
        move.run_stage(repo.root, "tests")
        self.assertTrue(os.path.exists(os.path.join(repo.root, "tests/test_x.py")))
        self.assertIn('os.path.join(ROOT, "tests", "helpers")', repo.read("tests/test_x.py"))
        self.assertIn("python3 scripts/run.py tests/test_x.py core/db.py", repo.read(".gitlab-ci.yml"))
        self.assertIn("/tests/test_x.py export-ignore", repo.read(".gitattributes"))
        self.assertIn('from core import db', repo.read("tests/test_x.py"))
        # a test path in a comment follows the directory; the prose name of a module does not move
        self.assertIn("# pinned by tests/test_x.py (see `tests/helpers/`); every tests/test_*.py.",
                      repo.read("core/db.py"))
        self.assertEqual("python3 -m unittest discover -s tests -p \"test_*.py\"; --start-directory=tests\n",
                         repo.read("CONTRIBUTING.md"))
        # a relative string that names nothing (text another file is searched for) is left as written
        self.assertIn('text.replace(\'import x from "../static/platform/x.mjs";\'', repo.read("tests/e2e/x.test.mjs"))
        # a nested project's own test/ is not the repository's
        self.assertIn("run test/a.test.mjs", repo.read("extensions/x/README.md"))
        # nor is a path our test/ never had, read relative to that project from one of our tests
        self.assertIn('leer("test/a.test.mjs")', repo.read("tests/ext.test.mjs"))
        self.assertIn("prose core/db.py stays", repo.read("README.md"))
        # a served file keeps every byte: its comments are in its fingerprint and its URL
        for rel in ("static/platform/served.mjs", "templates/served.html"):
            self.assertEqual(repo.read(rel), FIXTURE[rel], rel)

    def test_dry_run_changes_nothing(self):
        repo = LayoutRepo()
        self.addCleanup(repo.close)
        before = repo.tree()
        report = move.run_stage(repo.root, "app", dry_run=True)
        self.assertGreater(sum(report.changed.values()), 0)
        self.assertEqual(repo.tree(), before)

    def test_the_move_is_deterministic(self):
        trees = []
        for _ in range(2):
            repo = LayoutRepo()
            self.addCleanup(repo.close)
            move.run_stage(repo.root, "app")
            trees.append(repo.tree())
        self.assertEqual(trees[0], trees[1])

    def test_cli(self):
        repo = LayoutRepo()
        self.addCleanup(repo.close)
        out = io.StringIO()
        report_file = os.path.join(repo.root, "..", os.path.basename(repo.root) + "-report.txt")
        self.addCleanup(lambda: os.path.exists(report_file) and os.remove(report_file))
        with redirect_stdout(out):
            self.assertEqual(move.main(["--stage", "app", "--root", repo.root, "--dry-run",
                                        "--report", report_file]), 0)
        self.assertIn("files rewritten:", out.getvalue())
        self.assertIn("NOTE", Path(report_file).read_text(encoding="utf-8"))
        Path(repo.root, "core", "db.py").write_text("changed\n", encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(move.main(["--stage", "app", "--root", repo.root]), 1)
        self.assertIn("uncommitted changes", err.getvalue())

    def test_unparsable_python_is_reported_not_fatal(self):
        files = dict(FIXTURE)
        files["core/broken.py"] = "def (:\n"
        repo = LayoutRepo(files)
        self.addCleanup(repo.close)
        report = move.run_stage(repo.root, "app", dry_run=True)
        self.assertIn("core/broken.py", {p for p, _l, k, _t in report.notes if k == "unparsed"})


class ImportAllTest(unittest.TestCase):
    def test_compile_and_import_findings(self):
        files = {
            "core/__init__.py": "",
            "core/good.py": "X = 1\n",
            "core/bad_import.py": "import does_not_exist_anywhere\n",
            "core/bad_syntax.py": "def (:\n",
            "app.py": "from core import good\n",
            "scripts/tool.py": "print(1)\n",
        }
        repo = LayoutRepo(files)
        self.addCleanup(repo.close)
        py = import_all.tracked_python(repo.root)
        self.assertEqual(import_all.compile_errors(repo.root, py).keys(), {"core/bad_syntax.py"})
        mods = import_all.application_modules(py)
        self.assertIn("app", mods)
        self.assertNotIn("scripts.tool", mods)
        errors = import_all.import_errors(repo.root, ["core.good", "core.bad_import", "app"])
        self.assertEqual(set(errors), {"core.bad_import"})
        self.assertIn("ModuleNotFoundError", errors["core.bad_import"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = import_all.main(["--root", repo.root,
                                    "--json", os.path.join(repo.root, "result.json")])
        self.assertEqual(code, 1)
        self.assertIn("compile errors 1", out.getvalue())

    def test_names_a_manifest_loads_at_run_time_are_imported(self):
        files = {
            "core/__init__.py": "",
            "core/good.py": "X = 1\n",
            "modules/x/module.json": json.dumps({
                "id": "x", "entrypoint": "modules.x.gone", "label_key": "core.nothing",
                "routes": [{"module": "core.good", "after": {"GET": "core.good"}}]}),
        }
        repo = LayoutRepo(files)
        self.addCleanup(repo.close)
        names = import_all.manifest_names(repo.root, import_all.tracked(repo.root))
        self.assertEqual(names, ["core.good", "modules.x.gone"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = import_all.main(["--root", repo.root])
        self.assertEqual(code, 1)
        self.assertIn("manifest modules.x.gone ModuleNotFoundError", out.getvalue())

    def test_moved_tree_skips_run_only_root_files(self):
        mods = import_all.application_modules(["app/__init__.py", "app/__main__.py", "app/core/db.py",
                                               "worker.py"])
        self.assertEqual(mods, ["app", "app.core.db"])


if __name__ == "__main__":
    unittest.main()
