"""The repository root holds exactly the list in docs/LAYOUT.md: what a tool expects at the root
(Docker, git, CI, npm, Alembic), the documents a reader looks for first, the application package
`app/` and `tests/`. A new root file or directory, a new top-level Python package, or a `.py` other
than the three temporary shims is red — put it where docs/LAYOUT.md says, or change that list here
and there together."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _archive  # noqa: E402

DIRECTORIES = {
    ".claude", ".github", ".storybook", "alembic", "app", "charts", "docs", "extensions", "locales",
    "packs", "scripts", "static", "stories", "templates", "tests", "vendor", "webdata",
}
FILES = {
    ".dockerignore", ".env.example", ".gitattributes", ".gitignore", ".gitlab-ci.yml", ".gitleaks.toml",
    ".gitleaksignore", ".local-ci.json", ".ui-text-density-allow", "AGENTS.md", "CHANGELOG.md", "CLAUDE.md",
    "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "Dockerfile", "LICENSE", "README.md", "SECURITY.md",
    "alembic.ini", "docker-compose.yml", "docker-compose.release.yml", "install.sh", "package.json",
    "package-lock.json", "playwright.config.mjs", "requirements.txt",
}
# run-only shims for start commands written before the move; removed by a later slice
SHIMS = {"asgi_main.py", "worker.py", "manage.py"}
# written at the root by design skills of an older layout: their place is docs/design/
FORMER_DESIGN_PLACES = ("design-plans", "design-qa.md")


def _root_entries():
    """{name: "tree" | "blob"} of what git tracks at the root (an untracked local file is not the repo's)."""
    out = subprocess.run(["git", "ls-tree", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    entries = {}
    for line in out.stdout.splitlines():
        meta, name = line.split("\t", 1)
        entries[name] = meta.split()[1]
    staged = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=A"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.split()
    for path in staged:
        top = path.split("/", 1)[0]
        entries.setdefault(top, "tree" if "/" in path else "blob")
    return entries


class RepositoryRootTest(unittest.TestCase):
    def setUp(self):
        # the public cut leaves out .claude/, CLAUDE.md and the rest export-ignore lists
        _archive.skip_in_archive_copy(self)
        try:
            self.entries = _root_entries()
        except (OSError, subprocess.CalledProcessError) as e:
            self.fail("the root list is read from git, and this checkout has none: %s" % e)

    def test_the_root_holds_exactly_the_list(self):
        dirs = {n for n, kind in self.entries.items() if kind == "tree"}
        files = {n for n, kind in self.entries.items() if kind != "tree"}
        self.assertEqual(sorted(dirs - DIRECTORIES), [], "a new directory at the root: see docs/LAYOUT.md")
        self.assertEqual(sorted(files - FILES - SHIMS), [], "a new file at the root: see docs/LAYOUT.md")
        self.assertEqual(sorted(DIRECTORIES - dirs), [], "a listed directory is gone: update the list")
        self.assertEqual(sorted(FILES - files), [], "a listed file is gone: update the list")

    def test_no_python_at_the_root_but_the_shims(self):
        python = {n for n, kind in self.entries.items() if kind != "tree" and n.endswith(".py")}
        self.assertEqual(python, SHIMS & set(self.entries))

    def test_the_application_is_the_only_python_package(self):
        packages = sorted(n for n, kind in self.entries.items()
                          if kind == "tree" and os.path.isfile(os.path.join(ROOT, n, "__init__.py")))
        self.assertEqual(packages, ["app"])

    def test_design_notes_are_not_committed_at_their_former_root_places(self):
        for name in FORMER_DESIGN_PLACES:
            with self.subTest(name=name):
                self.assertNotIn(name, self.entries, "design notes live in docs/design/ (docs/LAYOUT.md)")

    def test_the_former_design_places_stay_out_of_the_public_cut(self):
        with open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8") as fh:
            attributes = fh.read()
        for name in FORMER_DESIGN_PLACES:
            with self.subTest(name=name):
                self.assertIn("/%s export-ignore" % name, attributes)


def _tracked_scripts(suffix):
    out = subprocess.run(["git", "ls-files", "scripts"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [p for p in out.stdout.split() if p.endswith(suffix) and os.path.isfile(os.path.join(ROOT, p))]


def _from_own_place(path):
    """[(line, kind, resolved)] of the paths a Python script derives from its own `__file__`: a `sys.path`
    entry, or an `os.path.join(<from __file__>, "..", ...)` that climbs out of its folder."""
    import ast
    from pathlib import Path
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    namespace = {"os": os, "Path": Path, "__file__": os.path.join(ROOT, path)}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        func = ast.unparse(node.func)
        if func in ("sys.path.insert", "sys.path.append") and node.args:
            expr, kind = node.args[-1], "sys.path"
        elif func == "os.path.join" and node.args and any(
                isinstance(a, ast.Constant) and a.value == ".." for a in node.args):
            expr, kind = node, "climb"
        else:
            continue
        if "__file__" not in ast.unparse(expr):
            continue
        found.append((node.lineno, kind,
                      os.path.normpath(str(eval(compile(ast.Expression(expr), path, "eval"), dict(namespace))))))
    return found


class ScriptsFindTheRootTest(unittest.TestCase):
    """A script moved one folder deeper (tools/ -> scripts/tools/) still climbs as many folders as before:
    no test runs it, so the miss only shows when the LaunchAgent or a person runs it."""

    def test_a_script_puts_the_root_or_its_own_folder_on_sys_path(self):
        checked = []
        for path in _tracked_scripts(".py"):
            for line, kind, resolved in _from_own_place(path):
                if kind != "sys.path":
                    continue
                checked.append(path)
                with self.subTest(script=path, line=line):
                    self.assertIn(resolved, (ROOT, os.path.dirname(os.path.join(ROOT, path))),
                                  "a sys.path entry is neither the repository root nor the script's folder")
        self.assertIn("scripts/tools/seed_hsi_demo.py", checked, "the scan read no script: check it")

    def test_a_path_climbing_from_a_script_lands_in_the_repository(self):
        checked = []
        for path in _tracked_scripts(".py"):
            for line, kind, resolved in _from_own_place(path):
                if kind != "climb":
                    continue
                checked.append(path)
                with self.subTest(script=path, line=line):
                    # data/ and logs/ are runtime folders at the root, not in git
                    runtime = any(resolved.startswith(os.path.join(ROOT, d) + os.sep) for d in ("data", "logs"))
                    self.assertTrue(runtime or os.path.isdir(os.path.dirname(resolved)),
                                    "%s: the folder it climbs to does not exist" % resolved)
        self.assertIn("scripts/tools/msd_pull.py", checked, "the scan read no script: check it")

    def test_a_shell_script_that_cds_from_its_own_place_lands_at_the_root_or_its_folder(self):
        import re
        pattern = re.compile(r'cd\s+"\$\(dirname\s+"(?:\$0|\$\{BASH_SOURCE\[0\]\})"\)(/[^"]*)?"')
        checked = []
        for path in _tracked_scripts(".sh"):
            with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            for number, text in enumerate(lines, 1):
                match = pattern.search(text)
                if not match:
                    continue
                checked.append(path)
                folder = os.path.dirname(os.path.join(ROOT, path))
                resolved = os.path.normpath(folder + (match.group(1) or ""))
                with self.subTest(script=path, line=number):
                    self.assertIn(resolved, (ROOT, folder), "the script runs from %s, neither the repository root "
                                  "nor its own folder" % os.path.relpath(resolved, ROOT))
        self.assertIn("scripts/tools/msd_refresh.sh", checked, "the scan read no script: check it")


if __name__ == "__main__":
    unittest.main()
