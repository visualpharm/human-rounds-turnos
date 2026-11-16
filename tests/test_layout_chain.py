"""test_layout_chain.py — replaying the layout chain, porting a branch across it, old leftovers.

scripts/layout/apply.py, port_branch.py and clean_stale.py on a throwaway repository that carries
its own copy of scripts/layout (the chain runs the move program it contains): the replay on the
chain's own base reproduces the chain's tree exactly, a replay on a newer main moves the newer
code too, a branch written on the old layout lands on the new one as its own change only, and a
checkout's stale caches go while its untracked files stay.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "layout"))

import apply as layout_apply  # noqa: E402
import clean_stale  # noqa: E402
import port_branch  # noqa: E402
from test_layout_move import FIXTURE  # noqa: E402

TOOLS = ("plan.py", "move.py", "apply.py", "port_branch.py", "clean_stale.py")
IDENTITY = ("-c", "user.email=t@example.invalid", "-c", "user.name=t")


def git(root, *args):
    return subprocess.run(["git", *IDENTITY, *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


def write(root, rel, text):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def commit(root, message):
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


class ChainRepo:
    """base = the fixture + the layout tools; chain = hand edit, mechanical app stage, hand edit."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="layout-chain-")
        try:
            self._build()
        except BaseException:
            # a failed build must not leave the directory behind (tearDown never runs for it)
            self.close()
            raise

    def _build(self):
        for rel, content in FIXTURE.items():
            write(self.root, rel, content)
        write(self.root, ".gitignore", "__pycache__/\n")
        os.makedirs(os.path.join(self.root, "scripts", "layout"), exist_ok=True)
        for tool in TOOLS:
            shutil.copy(os.path.join(ROOT, "scripts", "layout", tool),
                        os.path.join(self.root, "scripts", "layout", tool))
        git(self.root, "init", "-q", "-b", "main")
        self.base = commit(self.root, "fixture")
        git(self.root, "checkout", "-q", "-b", "chain")
        write(self.root, "core/db.py", FIXTURE["core/db.py"] + "\nPREP = 1\n")
        commit(self.root, "Prep: a hand edit before the move")
        proc = subprocess.run([sys.executable, os.path.join(self.root, "scripts/layout/move.py"),
                               "--stage", "app", "--root", self.root], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        # exactly what move.py staged, as the layout procedure commits it
        git(self.root, "commit", "-q", "-m", "Layout: move the code into app/\n\nLayout-Stage: app\n")
        self.mechanical = git(self.root, "rev-parse", "HEAD")
        write(self.root, "app/core/db.py",
              Path(self.root, "app/core/db.py").read_text(encoding="utf-8") + "AFTER = 2\n")
        self.chain = commit(self.root, "Fix: a hand edit after the move")
        git(self.root, "checkout", "-q", "main")

    def clone(self, ref):
        path = tempfile.mkdtemp(prefix="layout-chain-work-")
        os.rmdir(path)
        try:
            git(self.root, "worktree", "add", "-q", "--detach", path, ref)
        except BaseException:
            shutil.rmtree(path, ignore_errors=True)
            raise
        return path

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class ReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = ChainRepo()
        cls.addClassCleanup(cls.repo.close)

    def worktree(self, ref):
        path = self.repo.clone(ref)
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def test_commits_and_stages_of_the_chain(self):
        commits = layout_apply.chain_commits(self.repo.root, self.repo.base, "chain")
        self.assertEqual([stage for _sha, stage in commits], [None, "app", None])

    def test_replay_on_its_base_reproduces_the_chain(self):
        work = self.worktree(self.repo.base)
        out = io.StringIO()
        with redirect_stdout(out):
            code = layout_apply.main(["--root", work, "--base", self.repo.base,
                                      "--chain", "chain", "--verify"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("verified", out.getvalue())
        self.assertEqual(git(work, "rev-parse", "HEAD^{tree}"), git(self.repo.root, "rev-parse", "chain^{tree}"))

    def test_replay_on_a_newer_main_moves_the_newer_code(self):
        work = self.worktree(self.repo.base)
        write(work, "core/newer.py", "from core import db\n")
        commit(work, "Newer main")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(layout_apply.main(["--root", work, "--base", self.repo.base,
                                                "--chain", "chain"]), 0)
        self.assertEqual(Path(work, "app/core/newer.py").read_text(encoding="utf-8"),
                         "from app.core import db\n")
        db = Path(work, "app/core/db.py").read_text(encoding="utf-8")
        self.assertIn("PREP = 1", db)
        self.assertIn("AFTER = 2", db)

    def test_verify_refuses_a_checkout_off_base(self):
        work = self.worktree("chain")
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(layout_apply.main(["--root", work, "--base", self.repo.base,
                                                "--chain", "chain", "--verify"]), 2)

    def test_a_conflicting_hand_edit_stops_the_replay(self):
        work = self.worktree(self.repo.base)
        write(work, "core/db.py", FIXTURE["core/db.py"] + "\nPREP = 99\n")
        commit(work, "Conflicting main")
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(layout_apply.main(["--root", work, "--base", self.repo.base,
                                                "--chain", "chain"]), 1)
        self.assertIn("cherry-pick", err.getvalue())
        self.assertIn("core/db.py", err.getvalue())

    def test_a_merge_or_an_unknown_stage_in_the_chain_is_refused(self):
        work = self.worktree(self.repo.base)
        git(work, "checkout", "-q", "-b", "side")
        write(work, "side.txt", "side\n")
        commit(work, "side")
        git(work, "checkout", "-q", "--detach", self.repo.base)
        write(work, "other.txt", "other\n")
        commit(work, "other")
        git(work, "merge", "-q", "--no-ff", "-m", "merge", "side")
        with self.assertRaises(layout_apply.ReplayError) as caught:
            layout_apply.chain_commits(work, self.repo.base, "HEAD")
        self.assertIn("merge commit", str(caught.exception))
        git(work, "commit", "-q", "--allow-empty", "-m", "bad\n\nLayout-Stage: nope\n")
        with self.assertRaises(layout_apply.ReplayError):
            layout_apply.chain_commits(work, "HEAD~1", "HEAD")


class PortTest(unittest.TestCase):
    def setUp(self):
        self.repo = ChainRepo()
        self.addCleanup(self.repo.close)
        git(self.repo.root, "checkout", "-q", "-b", "feature", self.repo.base)
        write(self.repo.root, "core/feature.py", "from core.db import connect\nVALUE = connect()\n")
        write(self.repo.root, "worker.py", FIXTURE["worker.py"] + "\nFEATURE = True\n")
        commit(self.repo.root, "Feature: something of its own")
        git(self.repo.root, "checkout", "-q", "main")

    def test_the_branch_lands_on_the_new_layout(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = port_branch.main(["feature", "--repo", self.repo.root, "--pre", self.repo.base,
                                     "--post", "chain", "--chain", "chain"])
        self.assertEqual(code, 0)
        self.assertIn("created feature-layout", out.getvalue())
        work = self.repo.clone("feature-layout")
        self.addCleanup(shutil.rmtree, work, True)
        self.assertEqual(Path(work, "app/core/feature.py").read_text(encoding="utf-8"),
                         "from app.core.db import connect\nVALUE = connect()\n")
        self.assertIn("FEATURE = True", Path(work, "app/worker.py").read_text(encoding="utf-8"))
        self.assertIn("AFTER = 2", Path(work, "app/core/db.py").read_text(encoding="utf-8"))
        self.assertFalse(os.path.exists(os.path.join(work, "core")))
        changed = git(self.repo.root, "diff", "--name-only", "chain", "feature-layout").split()
        self.assertEqual(sorted(changed), ["app/core/feature.py", "app/worker.py"])
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(port_branch.main(["feature", "--repo", self.repo.root, "--pre", self.repo.base,
                                               "--post", "chain", "--chain", "chain"]), 1)
        self.assertIn("already exists", err.getvalue())

    def test_a_branch_with_nothing_of_its_own(self):
        git(self.repo.root, "branch", "empty", self.repo.base)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(port_branch.main(["empty", "--repo", self.repo.root, "--pre", self.repo.base,
                                               "--post", "chain", "--chain", "chain"]), 1)
        self.assertIn("nothing of its own", err.getvalue())


class CleanStaleTest(unittest.TestCase):
    def test_caches_go_and_files_stay(self):
        repo = ChainRepo()
        self.addCleanup(repo.close)
        git(repo.root, "checkout", "-q", "chain")
        write(repo.root, "core/__pycache__/db.cpython-312.pyc", "x")
        write(repo.root, "core/sub/__pycache__/y.pyc", "x")
        write(repo.root, "connectors/webturno/passwords.json", "{}")
        caches, leftovers = clean_stale.scan(repo.root)
        self.assertEqual(len(caches), 2)
        self.assertEqual(leftovers, [("connectors/webturno/passwords.json",
                                      "app/connectors/webturno/passwords.json")])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(clean_stale.main(["--root", repo.root, "--apply"]), 1)
        self.assertFalse(os.path.exists(os.path.join(repo.root, "core")))
        self.assertTrue(os.path.exists(os.path.join(repo.root, "connectors/webturno/passwords.json")))
        self.assertIn("leftover connectors/webturno/passwords.json", out.getvalue())
        os.remove(os.path.join(repo.root, "connectors/webturno/passwords.json"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(clean_stale.main(["--root", repo.root, "--apply"]), 0)
        self.assertFalse(os.path.exists(os.path.join(repo.root, "connectors")))

    def test_an_unmoved_checkout_has_no_old_locations(self):
        repo = ChainRepo()
        self.addCleanup(repo.close)
        self.assertNotIn("core", clean_stale.old_locations(clean_stale._tracked(repo.root)))


if __name__ == "__main__":
    unittest.main()
