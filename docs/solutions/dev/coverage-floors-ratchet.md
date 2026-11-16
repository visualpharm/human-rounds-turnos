---
title: The CI gate measures coverage and keeps a floor that only goes up
scope: repo
area: dev
tags: [ci, tests, coverage, ratchet]
created: 2026-10-26
refs: [scripts/run_frontend_tests.mjs, test/frontend-lanes.test.mjs, scripts/coverage_floor.py, scripts/coverage_floor.json, scripts/coveragerc, scripts/c8rc.json, scripts/requirements-ci.txt, scripts/run_backend_shards.py, scripts/check_unittest_coverage.py, scripts/ci_local.sh, .gitlab-ci.yml, test/test_coverage_floor.py]
status: active
---

## Problem
The backend gate (`check_unittest_coverage.py --minimum 1950`) counted the tests that ran, not
the code they run: with 8896 tests running, two thirds of the suite could vanish before anything
turned red, and nothing in CI measured line or branch coverage (the one measurement,
`docs/roadmap/audit-row13-coverage-evidence.md`, was taken once, at 84.1 % lines /
75.2 % branches, and has no way to notice a drop). The frontend suite had no measurement at all.

## Solution
One file, `scripts/coverage_floor.json`, holds every threshold; `scripts/coverage_floor.py` reads
and rewrites it.

- **Test count.** `min_tests` is 97 % of the tests of the last run that raised it. Every CI
  entry point (`ci_local.sh`, `.gitlab-ci.yml`, the hosted `ci.yml`) calls
  `check_unittest_coverage.py` without a number; `--minimum` is only an override. A missing or
  broken floor file is red, not green. The frontend has the same floor, `min_js_tests`: the
  `# tests N` lines of the `node --test` log, summed over the lanes (`coverage_floor.py count
  --log frontend.log`, checked with or without c8).
- **Backend coverage.** `run_backend_shards.py --coverage-dir D` runs each test file's fresh
  interpreter under `coverage run --branch` (`scripts/coveragerc`), combines the data files and
  leaves `D/coverage-summary.json` (covered/total lines and branches per package),
  `coverage-summary.txt` (the table beside the floors) and `coverage-files.json` (per file).
  CI keeps the first two as an artifact. `coverage_floor.py check --summary D/coverage-summary.json`
  turns the surface red when a package falls below its floor, when a package has no floor, or
  when the floor names a package the run no longer has. The serial run
  (`TURNOS_BACKEND_SERIAL=1`) measures nothing and says so.
- **Where the tools are required.** In CI (`$CI`, `$GITLAB_CI`, `$GITHUB_ACTIONS`, or
  `TURNOS_REQUIRE_COVERAGE=1`, which the CI machines' recipes set because the managed
  `local-ci-asgi` profile replaces the environment) `ci_local.sh` needs coverage.py and c8: a
  missing one fails the surface and `TURNOS_COVERAGE=0` is ignored. On a developer machine
  (the dev machine) a missing tool must not break the run: the measurement is skipped with a loud
  warning (on stderr, in the surface's row and at the end) and only CI checks the floors;
  `TURNOS_COVERAGE=0` skips it on purpose. `.gitlab-ci.yml` always installs both.
- **Frontend coverage.** `c8` wraps `node --test` (`scripts/c8rc.json`; devDependency, installed by
  `npm ci`) and `check --kind js` sums its `coverage-summary.json` per directory (`static/<dir>`
  split out). It counts what the node processes of the tests execute: code a test evaluates with
  `vm.runInContext` from extracted text, or loads in a Chromium page, is not attributed to its
  file (`admin-app.js` shows 0 %), so the figure is a floor to keep, not the share of the
  frontend that is tested.
- **One entry point for the frontend measurement.** The floors are measured, and checked, only
  through `c8 … node scripts/run_frontend_tests.mjs` (`ci_local.sh`, `pre-commit.sh` and the
  `frontend` job of `.gitlab-ci.yml` all do; `test/frontend-lanes.test.mjs` pins it). The runner script is
  itself a measured file in the `scripts` directory: its entry block (the two lanes, 20 lines) is
  executed only when the script is the entry point. On 27-10 the GitLab job ran `c8 … node --test
  test/*.test.mjs` instead, `scripts` read 73.10 % lines (144/197) against the floor of 82.7 %
  (164/197) and eleven pipelines on `main` were red with 2958/2958 tests green. Any new way to
  run the suite (another CI, a Makefile target) must call the runner script, or its `scripts`
  figure is not comparable with the floor.
  The runner script ends with `process.exitCode`, never an immediate exit call: with the output
  piped (`| tee frontend.log`) an exit right after the two lanes' logs cut the log at 74 KB on the
  GitLab runner, the `# tests` summary was missing and the test-count floor read «0 tests ran».
- **The ratchet.** `python3 scripts/coverage_floor.py write --summary … --report …` (and
  `--kind js`) sets each floor to the measured value minus 0.5 points (rounded down to 0.1) and
  `min_tests` (or, with `--log`, `min_js_tests`) to 97 % of the run, and never lowers either. A deliberate drop (dead code deleted,
  a package split) is `write --lower`, so the diff of the JSON shows what was given up. Same
  contract as `test/snapshots.py --write`: the fix for a stale file is the command, never a
  hand-pasted number.

## Numbers (26-10, 8896 tests)
Backend 85.7 % lines / 78.8 % branches over 462 files (core 94.8 / 89.4, modules 85.5 / 80.3,
connectors 75.7 / 63.5, scripts 74.3 / 68.9, routers 72.2 / 54.7). Not measured: code a test
starts as a subprocess (the app servers of the e2e surface, the worker) — those belong to their
own surfaces. The measurement cost the backend ~13 % of wall time in the CI container (564 s ->
636 s, machine loaded) and the frontend ~10 %.

## Rules
- A new top-level package (a directory with `__init__.py`) or frontend directory needs its floor:
  run `write` (`--kind js`) in the same commit; `test_coverage_floor` fails first.
- Coverage varies by a few lines between runs (timing-dependent branches); the 0.5 point margin
  is there for that. A floor that keeps flaking is a flaky test to fix, not a margin to widen.
- Where to take the floors from: the artifacts of a full `local-ci-asgi` run or of a green GitLab
  pipeline of `main` — both go through the same entry point, so they give the same figures. A
  floor taken from a hand-run `c8 node --test …` is wrong for the `scripts` directory. When the
  GitLab `frontend` job is red on a floor and the local run is green, diff the per-file lines of
  the two `coverage-summary.json` (the job's artifact against `frontend-coverage/`) before
  touching a number: the 27-10 case was one file, `scripts/run_frontend_tests.mjs` (59/59 locally,
  39/59 on the runner).
- Raising the floors after a good run, from the artifacts of that CI run:
  `python3 scripts/coverage_floor.py write --summary backend-coverage/coverage-summary.json
  --report backend-results.json` and `python3 scripts/coverage_floor.py write --kind js
  --summary frontend-coverage/coverage-summary.json --log frontend.log`.
- A package fell below its floor: add the missing tests, or — if the drop is deliberate (dead
  code deleted) — `write --lower` and say why in the commit.
- Frontend: `c8` comes with `npm ci` (devDependency); backend: `python3 -m pip install -r
  scripts/requirements-ci.txt`.
- `coverage` is a CI-only dependency (`scripts/requirements-ci.txt`); the Docker image
  (`requirements.txt`) does not carry it.
