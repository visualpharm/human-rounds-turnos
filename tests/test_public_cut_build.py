"""scripts/public_cut_build.py: the public cut is a fresh one-commit tree built
from `git archive`, checked by the preflight, and summarised value-free.

Fixtures are throwaway git repositories; nothing is pushed anywhere."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _archive  # noqa: E402
import _env  # noqa: E402
import public_cut_build as build  # noqa: E402
from check_unittest_coverage import GITLEAKS_ABSENT_SKIP  # noqa: E402

POLICY = SCRIPTS / "public_cut_policy.json"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), env=build._clean_env(),
                          capture_output=True, text=True, check=True).stdout


class _Repo(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main", ".")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "T")
        _git(self.repo, "config", "commit.gpgsign", "false")

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self):
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "fixture")
        return _git(self.repo, "rev-parse", "HEAD").strip()

    def fixture(self):
        self.write(".gitattributes", "/notes export-ignore\n/notes/** export-ignore\n"
                                     "/CLAUDE.md export-ignore\n")
        self.write("notes/ops.md", "private operations notes\n")
        self.write("CLAUDE.md", "agent instructions\n")
        self.write("app/main.py", "print('hello')\n")
        # Names an excluded file: reported as a dangling reference, by name.
        self.write("app/readme_check.py", "DOC = 'CLAUDE.md'\n")
        # Credential-shaped value in a public file: the summary keeps class,
        # path and line only.
        self.secret = "synthetic" + "-value-" + "0123456789"
        self.write("app/cfg.py", "pass" + "word = '" + self.secret + "'\n")
        return self.commit()

    def build(self, ref, **kw):
        dest = kw.pop("dest", None)
        return build.build(self.repo, ref, dest, POLICY, kw.pop("worktree_attributes", False),
                           kw.pop("gitleaks", False))


class TestCut(_Repo):
    def test_cut_is_a_fresh_single_commit_without_excluded_paths(self):
        sha = self.fixture()
        summary = self.build(sha)
        dest = Path(summary["dest"])
        self.addCleanup(shutil.rmtree, dest, True)
        self.assertEqual(summary["commit"], sha)
        self.assertEqual(summary["excluded"], ["CLAUDE.md", "notes/ops.md"])
        self.assertFalse((dest / "notes").exists())
        self.assertFalse((dest / "CLAUDE.md").exists())
        self.assertTrue((dest / "app" / "main.py").is_file())
        self.assertEqual(_git(dest, "rev-list", "--count", "HEAD").strip(), "1")
        self.assertEqual(_git(dest, "remote").strip(), "")
        self.assertEqual(_git(dest, "branch", "--show-current").strip(), "main")
        self.assertEqual(_git(dest, "log", "-1", "--format=%an <%ae>").strip(),
                         "Human Rounds <noreply@humanrounds.invalid>")
        self.assertEqual(summary["dangling_references"], {"app/readme_check.py": ["CLAUDE.md"]})

    def test_every_export_ignored_test_exists_in_the_working_repository(self):
        # The tests `.gitattributes` keeps out of the cut (they test the agents'
        # hooks and other private files) must name real files, or the list rots.
        _archive.skip_in_archive_copy(self)
        root = SCRIPTS.parent
        lines = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
        tests = [ln.split()[0].lstrip("/") for ln in lines
                 if ln.startswith("/tests/") and ln.endswith(" export-ignore")]
        self.assertTrue(tests)
        for rel in tests:
            self.assertTrue((root / rel).is_file(), rel)

    def test_summary_names_classes_and_paths_never_values(self):
        sha = self.fixture()
        summary = self.build(sha)
        self.addCleanup(shutil.rmtree, summary["dest"], True)
        self.assertFalse(summary["passed"])
        self.assertIn("secret_marker:secret-assignment", summary["preflight"]["by_class"])
        out = self.base / "summary"
        paths = build.write_summary(summary, out)
        self.assertEqual(sorted(p.name for p in paths),
                         ["public-cut-summary.json", "public-cut-summary.md"])
        for p in paths:
            text = p.read_text(encoding="utf-8")
            self.assertNotIn(self.secret, text)
            self.assertNotIn("private operations notes", text)
        data = json.loads((out / "public-cut-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(data["gitleaks"], {"findings": None, "skipped": True})

    def test_worktree_attributes_check_an_older_ref_against_todays_exclusions(self):
        self.write("app/main.py", "print('hello')\n")
        self.write("notes/ops.md", "private operations notes\n")
        old = self.commit()
        self.write(".gitattributes", "/notes export-ignore\n/notes/** export-ignore\n")
        plain = self.build(old)
        self.addCleanup(shutil.rmtree, plain["dest"], True)
        self.assertEqual(plain["excluded"], [])
        today = self.build(old, worktree_attributes=True)
        self.addCleanup(shutil.rmtree, today["dest"], True)
        self.assertEqual(today["excluded"], ["notes/ops.md"])

    def test_destination_must_be_new_empty_and_outside_the_repo(self):
        with self.assertRaises(build.BuildError):
            build.prepare_dest(self.repo / "cut", self.repo)
        busy = self.base / "busy"
        busy.mkdir()
        (busy / "x").write_text("x", encoding="utf-8")
        with self.assertRaises(build.BuildError):
            build.prepare_dest(busy, self.repo)
        fresh = build.prepare_dest(self.base / "fresh", self.repo)
        self.assertTrue(fresh.is_dir())

    def test_unknown_ref_is_a_build_error(self):
        self.fixture()
        with self.assertRaises(build.BuildError):
            self.build("no-such-ref")

    @unittest.skipUnless(shutil.which("gitleaks"), GITLEAKS_ABSENT_SKIP)
    def test_gitleaks_reports_rule_path_line_and_honours_the_cuts_ignore_file(self):
        key = "".join(["a7", "Kq", "93", "Zx", "Lm", "04", "Rt", "Yu", "88", "Pq", "Wv", "12"])
        self.write("app/one.py", "api" + "_key = \"" + key + "\"\n")
        self.write("app/two.py", "api" + "_key = \"" + key[::-1] + "\"\n")
        sha = self.commit()
        # An ambient config would outrank the cut's own; the build ignores it.
        _env.set_env(self, GITLEAKS_CONFIG=str(self.base / "no-such-config.toml"))
        first = self.build(sha, gitleaks=True)
        self.addCleanup(shutil.rmtree, first["dest"], True)
        found = {(i["path"], i["line"]) for i in first["gitleaks"]["items"]}
        self.assertIn(("app/one.py", 1), found)
        self.assertNotIn(key, json.dumps(first["gitleaks"]))
        self.write(".gitleaksignore", "app/one.py:generic-api-key:1\n")
        second = self.build(self.commit(), gitleaks=True)
        self.addCleanup(shutil.rmtree, second["dest"], True)
        paths = {i["path"] for i in second["gitleaks"]["items"]}
        self.assertNotIn("app/one.py", paths)
        self.assertIn("app/two.py", paths)


class TestRepositoryCutRules(unittest.TestCase):
    def test_reviewed_gitleaks_ignores_name_existing_lines(self):
        # Each reviewed fingerprint must still point at a real file and line;
        # a stale entry would silently hide a future finding at that spot.
        root = SCRIPTS.parent
        for raw in (root / ".gitleaksignore").read_text(encoding="utf-8").splitlines():
            if not raw.strip() or raw.startswith("#"):
                continue
            path, _rule, line = raw.rsplit(":", 2)
            with self.subTest(entry=raw):
                lines = (root / path).read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(int(line), len(lines))


if __name__ == "__main__":
    unittest.main()
