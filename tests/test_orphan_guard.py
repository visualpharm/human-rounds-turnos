"""A guarded test runner leaves no orphans, however it or its parent ends.

25.10.2026 the dev machine carried ~60 orphaned scripts/run_routes_tests.sh trees (PPID 1,
unittest workers alive, load 130-300): a background-task limit killed only the
shell that started the runner. scripts/orphan_guard.sh (sourced by
pre-commit.sh and run_routes_tests.sh) moves the runner into its own process
group, reaps it on EXIT/HUP/INT/TERM and watches for its own or its parent's
death. This drives a stub runner that sources the REAL guard and spawns the
three kinds of children the real runners have — a background worker, a
double-forked child already reparented to PID 1, a long foreground command —
then ends it four ways and checks nothing with its marker is left.
"""

import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
import uuid

import _wait

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "orphan_guard.sh"

SLEEPER = "import sys, time; open(sys.argv[1], 'w').close(); time.sleep(300)"

RUNNER = """#!/bin/bash
set -u
. "$GUARD"
orphan_guard_init "$0" "$@"
dir="$1"; marker="$2"; mode="$3"
echo $$ > "$dir/runner.pid"
python3 -c "$SLEEPER" "$dir/worker.ready" "$marker" &
( python3 -c "$SLEEPER" "$dir/orphan.ready" "$marker" & )
if [ "$mode" = exit ]; then
    while [ ! -e "$dir/worker.ready" ] || [ ! -e "$dir/orphan.ready" ]; do sleep 0.05; done
    exit 0
fi
orphan_guard_run python3 -c "$SLEEPER" "$dir/fg.ready" "$marker"
"""

# A parent the test can kill on its own: it only waits for the runner.
PARENT = "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))"


@unittest.skipUnless(shutil.which("ps") and shutil.which("perl") and shutil.which("pgrep"),
                     "the guard needs ps and perl; the check needs pgrep")
class TestOrphanGuard(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orphan-guard-"))
        self.marker = "orphan-guard-%d-%s" % (os.getpid(), uuid.uuid4().hex[:8])
        runner = self.dir / "runner.sh"
        runner.write_text(RUNNER, encoding="utf-8")
        self.runner = runner
        self.parent = None

    def tearDown(self):
        subprocess.run(["pkill", "-KILL", "-f", self.marker], check=False)
        if self.parent is not None and self.parent.poll() is None:
            self.parent.kill()
            self.parent.wait(10)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _start(self, mode):
        env = dict(os.environ, GUARD=str(GUARD), SLEEPER=SLEEPER,
                   TURNOS_ORPHAN_GUARD_INTERVAL="0.2", TURNOS_ORPHAN_GUARD_GRACE="3")
        env.pop("TURNOS_ORPHAN_GUARD_PGID", None)
        self.parent = subprocess.Popen(
            [sys.executable, "-c", PARENT, "/bin/bash", str(self.runner),
             str(self.dir), self.marker, mode],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)

    def _wait_for(self, names, timeout=60):
        _wait.until(lambda: all((self.dir / n).exists() for n in names),
                    "stub runner never started %s" % names, timeout=timeout, interval=0.05)

    def _marked(self):
        r = subprocess.run(["pgrep", "-f", self.marker], capture_output=True, text=True)
        return [int(p) for p in r.stdout.split()]

    def _assert_all_gone(self, timeout=60):
        left = self._marked()

        def reaped():
            nonlocal left
            left = self._marked()
            return not left

        try:
            _wait.until(reaped, "every process marked %s is gone" % self.marker, timeout=timeout, interval=0.1)
        except AssertionError:
            pass  # the assertion below names the survivors
        self.assertEqual(left, [], "orphans survived the runner: %s" % left)

    def _hold(self):
        self._start("hold")
        self._wait_for(["runner.pid", "worker.ready", "orphan.ready", "fg.ready"])
        runner = int((self.dir / "runner.pid").read_text())
        self.assertEqual(os.getpgid(runner), runner, "runner did not get its own process group")
        children = self._marked()
        self.assertGreaterEqual(len(children), 4, "runner, worker, orphan and foreground child")
        return runner

    def test_term_to_runner_reaps_every_child(self):
        runner = self._hold()
        os.kill(runner, signal.SIGTERM)
        self.assertEqual(self.parent.wait(60), 128 + signal.SIGTERM)
        self._assert_all_gone()

    def test_killed_parent_takes_the_runner_and_its_children(self):
        self._hold()
        os.kill(self.parent.pid, signal.SIGKILL)
        self.parent.wait(10)
        self._assert_all_gone()

    def test_killed_runner_leaves_no_children(self):
        runner = self._hold()
        os.kill(runner, signal.SIGKILL)
        self._assert_all_gone()

    def test_normal_exit_leaves_no_children(self):
        self._start("exit")
        self.assertEqual(self.parent.wait(60), 0)
        self._assert_all_gone()

    def test_long_runners_source_the_guard(self):
        for name in ("pre-commit.sh", "run_routes_tests.sh"):
            src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertRegex(src, r'\n\. "\$\(dirname "\$0"\)/orphan_guard\.sh"\n'
                                  r'orphan_guard_init "\$0" "\$@"\n', name)
        routes = (ROOT / "scripts" / "run_routes_tests.sh").read_text(encoding="utf-8")
        self.assertRegex(routes, r"_on_exit\(\) \{\n    rc=\$\?\n(?:    #[^\n]*\n)*    orphan_guard_finish\n",
                         "routes workers must die before their databases are dropped")
        precommit = (ROOT / "scripts" / "pre-commit.sh").read_text(encoding="utf-8")
        for command in ("node scripts/run_frontend_tests.mjs", "scripts/run_routes_tests.sh ||"):
            self.assertIn("orphan_guard_run " + command, precommit,
                          "a long foreground step defers the TERM trap until it returns")
        self.assertFalse(re.search(r"^\s*(?:if ! )?node scripts/run_frontend", precommit, re.M))


if __name__ == "__main__":
    unittest.main()
