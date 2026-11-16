#!/usr/bin/env python3
"""lane_janitor.py — keep the colima test lane (127.0.0.1:5433) from filling its disk (2026-11-08).

On 2026-11-07 at 23:40 the colima VM disk was 100% full and every routes tier failed with DiskFull. There were two
causes. First, 281 anonymous docker volumes left by another project's disposable Postgres (`docker rm -f` without
`-v`). Second, ~300 throwaway test databases from one night. Each runner drops its own databases, but a runner that
is killed cannot do that. This script is the backstop. It runs from scripts/test-db-up.sh and scripts/pre-commit.sh
and never fails the caller. It does three things:

1. Drops throwaway test databases (`sana_turnos_test_<suffix>`) with no session that were created more than a day
   ago (an autovacuum worker is not a session: DROP stops it itself). The creation time comes from the database's PG_VERSION file. The DROP has no FORCE, so a session racing in
   makes Postgres refuse rather than be terminated. `hsi-db`, `sana_turnos` and `postgres` never match the name.
2. After a drop, runs `colima ssh -- sudo fstrim -av`, so the VM disk returns the freed blocks to the Mac
   (hub-dev, 2026-11-08: 25.7 GB came back this way).
3. Warns when the VM's docker disk passes 85%.

Without docker, the `postgresql` container or colima (another machine, CI), every step is a quiet skip.
"""

import argparse
import os
import re
import subprocess
import sys
import time

CONTAINER = "postgresql"
NAME = re.compile(r"^sana_turnos_test_[a-z0-9_]+$")
MAX_AGE_SECONDS = 24 * 3600
DISK_WARN_PERCENT = 85
CALL_TIMEOUT = 30

LIST_SQL = (
    "SELECT d.datname,"
    " extract(epoch FROM (pg_stat_file('base/' || d.oid || '/PG_VERSION')).modification)::bigint,"
    " (SELECT count(*) FROM pg_stat_activity a WHERE a.datname = d.datname"
    " AND a.backend_type <> 'autovacuum worker')"  # not a run: DROP stops it (tests/_pg_sessions.py)
    " FROM pg_database d WHERE d.datname ~ '^sana_turnos_test_'"
)


def stale(rows, now, max_age=MAX_AGE_SECONDS):
    """Names to drop out of (name, created_epoch, sessions) rows, sorted."""
    return sorted(name for name, created, sessions in rows
                  if NAME.match(name) and not sessions and now - created > max_age)


def parse_rows(text):
    rows = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        try:
            rows.append((parts[0], int(parts[1]), int(parts[2])))
        except ValueError:
            continue
    return rows


def disk_percent(df_text):
    """Use% of the last line of `df -P` output, or None."""
    lines = [ln for ln in df_text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    for field in lines[-1].split():
        if field.endswith("%") and field[:-1].isdigit():
            return int(field[:-1])
    return None


def _call(argv):
    # Without the hub's session stamp: a process carrying HUB_TREE is stopped with the session (test-db-up.sh).
    env = {k: v for k, v in os.environ.items() if k != "HUB_TREE"}
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=CALL_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _psql(sql):
    return _call(["docker", "exec", CONTAINER, "psql", "-U", "postgres", "-d", "postgres",
                  "-At", "-F", "|", "-c", sql])


def sweep(now, max_age):
    listing = _psql(LIST_SQL)
    if listing is None or listing.returncode != 0:
        return []
    dropped = []
    for name in stale(parse_rows(listing.stdout), now, max_age):
        r = _psql('DROP DATABASE IF EXISTS "%s"' % name)
        if r is not None and r.returncode == 0:
            dropped.append(name)
    if dropped:
        print("lane_janitor: dropped %d test database(s) older than %d h with no session"
              % (len(dropped), max_age // 3600))
    return dropped


def trim():
    r = _call(["colima", "ssh", "--", "sudo", "fstrim", "-av"])
    if r is not None and r.returncode == 0 and r.stdout.strip():
        print("lane_janitor: fstrim — " + r.stdout.strip().splitlines()[-1])


def check_disk():
    r = _call(["colima", "ssh", "--", "df", "-P", "/var/lib/docker"])
    pct = disk_percent(r.stdout) if r is not None and r.returncode == 0 else None
    if pct is not None and pct > DISK_WARN_PERCENT:
        print(
            "lane_janitor: WARNING — the colima VM disk is at %d%% (more than %d%%); at 100%% every test that writes"
            " to Postgres fails with DiskFull. To free it: `python3 scripts/lane_janitor.py --max-age-hours 2`"
            " (idle test databases), `docker volume ls -f dangling=true` (anonymous volumes of removed containers"
            " — keep the ones of postgresql, turnos-db-1, orthanc, historia-clinica), `docker container prune`"
            " (stopped containers), then `colima ssh -- sudo fstrim -av` returns the freed blocks to the Mac."
            % (pct, DISK_WARN_PERCENT),
            file=sys.stderr)
    return pct


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-age-hours", type=float, default=MAX_AGE_SECONDS / 3600)
    args = ap.parse_args(argv)
    now = int(os.environ.get("TURNOS_LANE_JANITOR_NOW") or time.time())
    if sweep(now, int(args.max_age_hours * 3600)):
        trim()
    check_disk()
    return 0


if __name__ == "__main__":
    sys.exit(main())
