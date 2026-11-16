#!/usr/bin/env python3
"""Run ONE backend test file -- or the whole one-process suite -- on a database of its own.

`scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_x.py` runs
this with the file's name, `--profile backend-full` with `test_*.py` (every file in one
interpreter, as before, only no longer on the shared database). Test files that build their database through `tests/_dbtest.py` already get a
throwaway one, but a file without it -- and anything a file touches before its
`setUpModule`, or after its teardown -- talked to `DATABASE_URL`, the shared development
database, and inherited its Alembic revision. Another branch's tests move that database
forward, so the same file passed or hung depending on which branch ran last (2026-11-06:
the shared database was stamped with a revision main does not have).

This does for one file what `scripts/run_backend_shards.py` does for each lane: a fresh
database on the test server (`TEST_DATABASE_URL`, else `DATABASE_URL`), both variables
pointed at it, dropped when the file ends. The name has the `_dbtest` run-scoped shape,
so a run killed before the drop is reclaimed by `_dbtest.reclaim_stale_databases`. If
the server cannot be reached the file runs as before, with a line saying so (its Postgres
tests then skip, as they always did there).
"""
import os
import signal
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import run_backend_shards as shards  # noqa: E402

DATABASE_BASE = "sana_turnos_test_focused"


def own_database(base_url):
    """(maintenance url, own url, database name) of a fresh database, or None when the
    server cannot be reached. Never prints a URL: it carries the password."""
    database = f"{DATABASE_BASE}_{shards._run_token()}"
    maint, own = shards._urls(base_url, database)
    try:
        shards._maint_exec(maint, [f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)',
                                   f'CREATE DATABASE "{database}"'])
    except Exception as exc:  # noqa: BLE001 - reported, the file still runs
        print(f"run_backend_focused: no own database ({type(exc).__name__}); "
              "the file runs on DATABASE_URL as given", file=sys.stderr, flush=True)
        return None
    return maint, own, database


def drop(maint, database):
    try:
        shards._maint_exec(maint, [f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'])
    except Exception as exc:  # noqa: BLE001 - reclaimed later by _dbtest
        print(f"run_backend_focused: could not drop {database} ({type(exc).__name__})",
              file=sys.stderr, flush=True)


def command(pattern):
    return [sys.executable, "-W", "error::ResourceWarning", "-m", "unittest",
            "discover", "-s", "tests", "-p", pattern]


ALL = "test_*.py"


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or "/" in args[0] or not args[0].endswith(".py") or (
            "*" in args[0] and args[0] != ALL):
        print("usage: run_backend_focused.py <test_file.py | 'test_*.py'>", file=sys.stderr)
        return 2
    env = dict(os.environ)
    base_url = env.get("TEST_DATABASE_URL") or env.get("DATABASE_URL")
    created = own_database(base_url) if base_url else None
    if created:
        env["DATABASE_URL"] = env["TEST_DATABASE_URL"] = created[1]
    # The child shares the terminal's process group: Ctrl-C (and `kill -INT` on the
    # unittest process, which prints its stack) reach it directly; this process only
    # waits for it and drops the database afterwards. A handler, not SIG_IGN: an
    # ignored signal stays ignored across exec, and the child must still stop on it.
    previous = signal.signal(signal.SIGINT, lambda _s, _f: None)
    try:
        child = subprocess.Popen(command(args[0]), cwd=REPO, env=env)
        signal.signal(signal.SIGTERM, lambda _s, _f: child.terminate())
        code = child.wait()
    finally:
        signal.signal(signal.SIGINT, previous)
        if created:
            drop(created[0], created[2])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
