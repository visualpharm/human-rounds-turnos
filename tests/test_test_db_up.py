"""scripts/test-db-up.sh starts the test lane without the hub's session stamp.

28.10.2026 the colima VM that holds the test Postgres (127.0.0.1:5433) was stopped from outside at 11:02:14 and
11:08:28, six seconds after the executor that had typed `colima start` was retired: the hub stops every process carrying
the session's HUB_TREE stamp (hosts/common.Trees.end_tree), and limactl's hostagent had inherited it. The script is the
one way to start the lane; this drives it with stub colima/docker/nc and checks neither start sees HUB_TREE.
"""

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test-db-up.sh"

STUBS = {
    # colima: `status` fails (VM down) until `start` ran; `start` records the stamp it was given.
    "colima": '''#!/bin/bash
case "$1" in
  status) [ -e "$STUB_DIR/colima.started" ] ;;
  start) echo "${HUB_TREE-unset}" > "$STUB_DIR/colima.stamp"; touch "$STUB_DIR/colima.started" ;;
  ssh) echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
       echo "/dev/vdb1 61609772 58000000 3609772 ${STUB_PCT:-50}% /var/lib/docker" ;;
esac
''',
    "docker": '''#!/bin/bash
[ "$1" = start ] && { echo "${HUB_TREE-unset}" > "$STUB_DIR/docker.stamp"; touch "$STUB_DIR/docker.started"; }
exit 0
''',
    # nc: the port answers once the container was started
    "nc": '''#!/bin/bash
[ -e "$STUB_DIR/docker.started" ]
''',
}


class TestTestDbUp(unittest.TestCase):
    def _run(self, *, lane_up=False, pct=50):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            for name, body in STUBS.items():
                p = bindir / name
                p.write_text(body)
                p.chmod(p.stat().st_mode | stat.S_IXUSR)
            if lane_up:
                (bindir / "docker.started").touch()
            env = dict(os.environ, STUB_DIR=str(bindir), HUB_TREE="some-executor@/hub", STUB_PCT=str(pct),
                       PATH=f"{bindir}:{os.environ['PATH']}")
            r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
            stamps = {n: (bindir / f"{n}.stamp").read_text().strip()
                      for n in ("colima", "docker") if (bindir / f"{n}.stamp").exists()}
            return r, stamps

    def test_vm_and_container_start_without_the_session_stamp(self):
        r, stamps = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stamps, {"colima": "unset", "docker": "unset"})

    def test_lane_already_up_starts_nothing(self):
        r, stamps = self._run(lane_up=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stamps, {})

    def test_full_vm_disk_is_reported_whether_or_not_the_lane_was_up(self):
        # 07.11.2026 the VM disk reached 100% unnoticed; scripts/lane_janitor.py says so past 85%.
        for lane_up in (False, True):
            r, _stamps = self._run(lane_up=lane_up, pct=91)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("91%", r.stderr)
            r, _stamps = self._run(lane_up=lane_up, pct=50)
            self.assertNotIn("%", r.stderr)


if __name__ == "__main__":
    unittest.main()
