---
title: The backend suite runs as parallel shards, one fresh interpreter per test file
scope: repo
area: dev
tags: [ci, tests, parallelism, postgres]
created: 2026-10-25
refs: [scripts/run_backend_shards.py, scripts/update_backend_timings.py, scripts/backend_test_timings.json, scripts/ci_local.sh, .gitlab-ci.yml, test/test_run_backend_shards.py, test/test_update_backend_timings.py, test/_workerproc.py, test/_subject_fixture.py, test/_password_hash.py]
status: active
---

## Problem
The backend surface of the CI ladder was one `unittest discover` process over
~6900 tests: ~21 minutes on the CI runners, the longest surface by far. Running
it in parallel by hand corrupted the shared maintenance database (`postgres`:
language settings, caches) that every file without a database of its own talks
to.

## Solution
`scripts/run_backend_shards.py` (the default in `scripts/ci_local.sh` and in the
`backend` job of `.gitlab-ci.yml`; `TURNOS_BACKEND_SERIAL=1` keeps the
one-process `scripts/run_backend_tests.py`):

- the `test/test_*.py` files are split into N lanes (`TURNOS_BACKEND_SHARDS` /
  `--shards`, default 5), longest first onto the least-loaded lane, from the
  per-file seconds in `scripts/backend_test_timings.json` (a file missing from
  it is weighed by its size, so a new test file needs no seed edit); the plan is
  a pure function of its inputs, so every machine runs the same partition;
- every lane has its **own database** (created before, dropped after;
  `DATABASE_URL` and `TEST_DATABASE_URL` point at it) and its own `TMPDIR`, and
  both are emptied again between two files;
- every file runs in its **own interpreter**, as `unittest discover -p
  test_x.py` — the documented way to run one file. A single process shared
  across lanes would make a file's result depend on which files are its
  neighbours: the serial run only passes some files because an earlier one left
  `os.environ`, a cache or a dead `DATABASE_URL` behind (see "What it exposed");
- the per-lane reports are merged into the serial report schema, so
  `check_unittest_coverage.py --result-json ...` (minimum: `min_tests` of
  `scripts/coverage_floor.json`) gates a sharded run exactly as before, and
  `--coverage-dir` measures it ([coverage-floors-ratchet.md](coverage-floors-ratchet.md)); the run's own per-file seconds go to `--timings-out`
  (kept as a CI artifact; `--update-seed` folds them into the seed);
- `SERIAL_ONLY` in the runner lists files that must not share the machine with
  other suite processes; they run alone, last. It is empty: nothing needed it.

The slowest file, `test_queue_worker.py`, sits alone in a lane and is the floor
of the wall time (~4 minutes on a quiet machine, ~6 on a shared runner); splitting
it by class would lower it further, but it only matters once the lanes are even
(see "The seed must be recent").

## The seed must be recent
The job is as long as its slowest lane, and the lanes are only as even as the
seconds the planner was given. On 2026-10-27 the seed held 414 of 497 files and
summed to 731 s while the runners measured ~5800 s: the 83 missing files (including
the slowest, `test_subject_erase` ~440 s, `test_subject_export` ~230 s,
`test_seed_demo_store` ~160 s) were weighed by file size, one lane held ~1750 s
of work, another ~370 s, and `backend` took 1833 s on pipeline 183442 where an
even split of the same work is ~1170 s.

After the fixes below and the new seed, pipeline 183486 (commit `e5b4cee1`) ran
`backend` in 1025 s: lanes of 913-951 s, wall 952.7 s (was 1751 s), the same 100
skipped tests. The seed in the repo was then refreshed from that pipeline.

Refresh the seed from the runners' own measurements with one command:

    python3 scripts/update_backend_timings.py              # last 5 green main pipelines (needs glab)
    python3 scripts/update_backend_timings.py --since <sha>   # only pipelines of <sha> and after
    python3 scripts/update_backend_timings.py --pipeline 183442 --pipeline 183372   # exactly these
    python3 scripts/update_backend_timings.py a.json b.json   # artifacts you already have
    python3 scripts/update_backend_timings.py --dry-run    # print what it would do, write nothing

It takes the **median per file** over the runs (one busy runner is noise), keeps
the old value of a file no run measured, drops files that no longer exist,
prints the slowest lane the old and the new seed would give on every run it
read, and rewrites `scripts/backend_test_timings.json`. Commit that file alone,
with the command line in the message: the seed is then reproducible from the same
pipelines. Use the runners' numbers, not a laptop's: the same file costs a
different multiple on another machine (here, per file, p10 1.9x to p90 3.3x, median
2.4x), so a seed made locally balances the lanes of that machine only.

**After a change that makes files much faster or slower** (like the one below), the
runs from before it still outvote the first runs after it in a median of five. Use
`--since <the commit>` (it keeps the pipelines of that commit and its descendants;
`git fetch` first) with a smaller `--pipelines`, until enough runs have gathered.
`--assume FILE=SECONDS` sets a value by hand when no run can have measured it yet; the
seed of 2026-10-28 was made that way for the eight files this change sped up (their
local seconds after the change times 2.43, the ratio of runner to local seconds over the
488 unchanged files), and the first refresh after the change replaces them with
measurements.

`--update-seed` of the runner itself folds ONE run into the seed and is for a local
experiment only.

Every sharded run ends with a `lane balance:` line (slowest lane against an even
split). When the slowest lane is more than 25 % over it and is not explained by a
single long file, the line names the command above. Refresh when you see it, and
after a change that makes many files faster or slower. Not automated in CI on
purpose: a job that rewrites a tracked file needs a token that can push to `main`
(or open a merge request) and a decision on who reviews it.

## What made the slowest test files slow
Profiled with `cProfile` against a local Postgres, one file at a time:

- `test_subject_erase`, `test_subject_export` (and `test_subject_store_pins`,
  `test_subject_erase_journal`, `test_subject_requests`, `test_subject_export_route`):
  `_subject_fixture.Fixture.insert` opened a connection for the `information_schema`
  lookup of NOT NULL columns, another for the `id` check and a third for the insert, for each of the
  ~70 rows of the two synthetic persons, in every test (3469 inserts, 7155 connections
  in `test_subject_erase`: 64 of 72 s). A fixture now holds one connection for its test (closed by
  `addCleanup`, hence `Fixture(url, case)`) and reads the shape of a table once per database.
  `test_subject_erase` 71 s to 4 s locally.
- `test_seed_demo_store`, `test_manage_demo_seed`: the demo seeder creates ~95 accounts per
  seed with the same public login, each one an Argon2 hash (~25 ms on a quiet machine), and the files seed ten times: 951 hashes, 24 of 29 s. `test/_password_hash.py`
  `reuse_hashes()` returns the hash the real function made the first time for a repeated password,
  so stored values stay real, verifiable hashes. Do not use it in a test that is about hashing.

Both changes keep every assertion; they were checked by mutating `core/subject_erase.py`
(skipped DELETE steps, ignored blockers, skipped key move), `core/subject_export.py` (no subject
filter, secrets not removed) and `scripts/seed_demo_all.py` (marked accounts, searches and
requests not deleted, no patient accounts): each mutation turns the file red.

## A dead file stops the run, loudly (2026-11-05)
Three background local-ci runs went silent on the "Backend suite, sharded" banner for an hour and
were then found with no runner, no shards and no exit code. The cause was the outer one: the core
stops a background command after two hours, under load 100-250 the gate (queued behind other
pre-commits) did not fit, and the runner died with nothing on record — `ci_local.sh` sends its whole
output to `backend.log`, that file was block-buffered, the lane databases were dropped before
anything was printed, and the SIGKILL that follows the SIGTERM came first. A file killed by a
signal was also reported as "killed on timeout", and the other lanes ran on for up to an hour.
Now:

- a file that ends **without its result report** stops every lane at once. The lane prints, flushed,
  `shardN: test_x died: killed by SIGKILL after 3m10s (file 12/60)` (or `exit code 3`, or
  `killed after the 1500s per-file timeout`) and the last 20 lines that file wrote; the failure block
  lists it with the same cause, the files the stop cut short as `stopped while running`, the rest as
  `never ran`, and the verdict is `FAILED (run stopped: <reason>)`. A lane that raises (its database
  cannot be reset) stops the run the same way: `shardN stopped: <exception>`;
- the run has a **deadline** (`--deadline` / `TURNOS_BACKEND_DEADLINE`, default 5400 s, under the
  two-hour limit; 0 = none) and stops with `deadline of Ns reached` and what was running;
- a **heartbeat** every `--heartbeat` / `TURNOS_BACKEND_HEARTBEAT` seconds (default 120):
  `heartbeat 14m05s load 123 | shard1 35/106 test_x 1m20s | shard2 done 98/98 | ...`;
- `--progress-fd N` copies the announcements, heartbeats and the verdict to fd N; `ci_local.sh`
  passes its own stdout (`--progress-fd 3 ... 3>&1 >"$LOG"`), so a backgrounded run shows them;
- SIGINT/SIGTERM print `stopping every lane after Ns: interrupted by SIGTERM; running: ...` at once,
  stdout is line-buffered into the log, the report comes before the databases are dropped, a killed
  file is reaped for at most 60 s, and DROP/CREATE DATABASE carry a 300 s statement timeout.

A silent backend surface is therefore no longer "the runner hung": no heartbeat for minutes means
the runner process itself is gone (look at who held the outer timeout); a dead file names itself.
Pinned by `test/test_run_backend_shards_run.py` (SIGKILL, deadline, heartbeat, lane exception) and
`test/test_run_backend_shards.py` (SIGTERM into a log file).

## What it exposed
Files that passed only because of a neighbour (each now sets up what it needs):
`test_passkeys`, `test_passkey_challenge_store`, `test_module_nav` (no admin
signing secret of their own), `test_f5_native_routers`,
`test_slot_holds_router_native` (`setdefault("DATABASE_URL", "")` is a no-op
once a database is configured; the "no database" tests only passed after an
earlier file's teardown had left a dead URL in the environment).

The suite-wide worker leak gate was `pgrep -f <checkout>/worker.py`, which sees
the live worker another lane is driving; `_workerproc.leaked_worker_pids()`
counts only this process's descendants and orphans.

## Rules
- A test file must pass alone: `python3 -m unittest discover -s test -p
  test_x.py`. Anything it needs from `os.environ` it sets itself, for the test,
  and restores.
- Never rely on a database URL left over by another file. A "no database" test
  pins `DATABASE_URL=""` with `mock.patch.dict(os.environ, ...)`.
- A global process check must be scoped to this process's own children.
- Tests still write fixture photos under the checkout's `data/` (they are
  untracked; never `git add -A` blindly).

## What is left
After the seed and the two fixes the even split of the work is ~960 s on the runners and the
longest files are inherent: `test_queue_worker` (real worker subprocesses, ~370 s) and
`test_alembic_cutover` (builds and compares databases, ~320 s) fit in a lane. They only
become the floor if the suite gets much faster; then split them by class.
