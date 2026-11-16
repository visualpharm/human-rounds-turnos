"""scripts/lane_janitor.py keeps the colima test lane (127.0.0.1:5433) from filling its disk.

07.11.2026 23:40 the colima VM disk was 100% full and every routes tier failed with DiskFull: 281 anonymous docker
volumes left by another project's disposable Postgres, plus ~300 routes databases of one night. The janitor drops
throwaway test databases nobody uses that are older than a day, returns the freed blocks to the Mac (fstrim) and warns
when the VM disk passes 85%. It drives docker/colima, so the tests stub both.
"""

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "lane_janitor.py"
sys.path.insert(0, str(ROOT / "scripts"))

import lane_janitor  # noqa: E402

NOW = 1_800_000_000
DAY = 24 * 3600

# docker: `exec … psql … -c <sql>` answers the listing from $STUB_DIR/rows and records every DROP it is given.
DOCKER = '''#!/bin/bash
sql="${@: -1}"
case "$sql" in
  DROP*) echo "$sql" >> "$STUB_DIR/drops" ;;
  *) cat "$STUB_DIR/rows" ;;
esac
'''
# colima: `ssh -- df …` prints a df with the capacity in $STUB_DIR/pct; `ssh -- sudo fstrim …` is recorded.
COLIMA = '''#!/bin/bash
case "$*" in
  *fstrim*) echo "$*" >> "$STUB_DIR/trims"; echo "/var/lib/docker: 1 GiB (1073741824 bytes) trimmed" ;;
  *df*) echo "Filesystem     1024-blocks     Used Available Capacity Mounted on"
        echo "/dev/vdb1         61609772 36793452  21654312      $(cat "$STUB_DIR/pct")% /var/lib/docker" ;;
esac
'''


class TestSelection(unittest.TestCase):
    def test_only_old_sessionless_throwaway_test_databases(self):
        rows = [
            ("sana_turnos_test_routes_t6aefefc6p6460r9342_w0", NOW - 2 * DAY, 0),  # old, idle: dropped
            ("sana_turnos_test_e2e_journey_j15_p457_mw3ljhjv_1", NOW - 3 * DAY, 0),  # killed e2e run: dropped
            ("sana_turnos_test_focused_abc", NOW - DAY - 1, 0),  # just over a day: dropped
            ("sana_turnos_test_routes_w0", NOW - 3600, 0),  # fresh
            ("sana_turnos_test_queue_worker_x", NOW - 2 * DAY, 1),  # someone is connected
            ("hsi-db", NOW - 90 * DAY, 0),  # the HSI database
            ("sana_turnos", NOW - 90 * DAY, 0),  # not a throwaway one
            ("postgres", NOW - 90 * DAY, 0),
            ("sana_turnos_test", NOW - 90 * DAY, 0),  # no suffix: not a throwaway name
            ('sana_turnos_test_x"; DROP DATABASE "hsi-db', NOW - 2 * DAY, 0),  # not a plain name
        ]
        self.assertEqual(lane_janitor.stale(rows, NOW), [
            "sana_turnos_test_e2e_journey_j15_p457_mw3ljhjv_1",
            "sana_turnos_test_focused_abc",
            "sana_turnos_test_routes_t6aefefc6p6460r9342_w0",
        ])

    def test_the_session_count_leaves_out_autovacuum_workers_like_the_test_sweeps(self):
        # Same rule as _dbtest/_routes_fixture (tests/_pg_sessions.py), written as SQL for psql.
        sys.path.insert(0, str(ROOT / "tests"))
        import _pg_sessions
        self.assertIn(_pg_sessions.NOT_A_RUN_SQL, lane_janitor.LIST_SQL.replace("a.backend_type", "backend_type"))

    def test_disk_percent_reads_the_capacity_column(self):
        df = ("Filesystem     1024-blocks     Used Available Capacity Mounted on\n"
              "/dev/vdb1         61609772 36793452  21654312      63% /var/lib/docker\n")
        self.assertEqual(lane_janitor.disk_percent(df), 63)
        self.assertIsNone(lane_janitor.disk_percent(""))


class TestJanitorRun(unittest.TestCase):
    def _run(self, *, rows, pct):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bindir = Path(tmp.name)
        for name, body in (("docker", DOCKER), ("colima", COLIMA)):
            p = bindir / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        (bindir / "rows").write_text("".join(f"{n}|{c}|{s}\n" for n, c, s in rows))
        (bindir / "pct").write_text(str(pct))
        env = dict(os.environ, STUB_DIR=str(bindir), HUB_TREE="some-executor@/hub",
                   PATH=f"{bindir}:{os.environ['PATH']}", TURNOS_LANE_JANITOR_NOW=str(NOW))
        r = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)

        def read(name):
            f = bindir / name
            return f.read_text().splitlines() if f.exists() else []
        return r, read("drops"), read("trims")

    def test_drops_old_test_databases_then_trims(self):
        r, drops, trims = self._run(rows=[
            ("sana_turnos_test_routes_t6aefefc6p6460r9342_w0", NOW - 2 * DAY, 0),
            ("sana_turnos_test_routes_t6af00b5bp43986r31259_w0", NOW - 600, 0),
            ("hsi-db", NOW - 90 * DAY, 0),
        ], pct=63)
        self.assertEqual(r.returncode, 0, r.stderr)
        # No FORCE: a session racing in makes Postgres refuse instead of being terminated.
        self.assertEqual(drops, ['DROP DATABASE IF EXISTS "sana_turnos_test_routes_t6aefefc6p6460r9342_w0"'])
        self.assertEqual(len(trims), 1)
        self.assertIn("sudo fstrim -av", trims[0])
        self.assertNotIn("85%", r.stderr)

    def test_nothing_old_nothing_dropped_no_trim(self):
        r, drops, trims = self._run(rows=[("sana_turnos_test_routes_w0", NOW - 60, 0)], pct=63)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((drops, trims), ([], []))

    def test_warns_when_the_vm_disk_passes_85_percent(self):
        r, _drops, _trims = self._run(rows=[], pct=91)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("91%", r.stderr)
        self.assertIn("colima ssh -- sudo fstrim -av", r.stderr)
        self.assertIn("docker volume ls -f dangling=true", r.stderr)

    def test_no_docker_no_colima_is_a_quiet_skip(self):
        env = dict(os.environ, PATH="/nonexistent")
        r = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
