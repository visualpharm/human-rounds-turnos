# Development environment and tests: the detail moved out of CLAUDE.md

`CLAUDE.md` keeps the rule of each item below (what to run, what is forbidden) and links here for the rest. The text below is the former `CLAUDE.md` wording, moved on 2026-11-06 to keep that file under its size limit (`tests/test_claude_md_size.py`).

## Postgres lanes

- Postgres: two local lanes on the dev machine, both loopback-only. `DATABASE_URL` (app/dev lane) is the Homebrew Postgres on `localhost:5432` — trust-auth, so its password is a non-secret placeholder. `TEST_DATABASE_URL` (test lane, what `_dbtest.py` runs against) is HSI's `postgresql` docker container on `127.0.0.1:5433` — SCRAM auth, vault-managed; see CLAUDE.local.md. Schema auto-creates.

## The test lane and the hub (`colima`)

Rule in `CLAUDE.md`: start the test lane only with `bash scripts/test-db-up.sh`. Why:

> The hub stops every process carrying a session's `HUB_TREE` stamp when that executor is retired (`end_tree`), and a hostagent started from an executor's shell inherits it: the VM and the test Postgres die with the executor, under everyone else's pre-commit (28.10: four stops). The script strips the stamp. Detail → [colima-dies-with-the-executor.md](../solutions/dev/colima-dies-with-the-executor.md)

The lane's disk (2026-11-07: the VM disk at 100%, every routes tier red with DiskFull). `scripts/lane_janitor.py` runs from `test-db-up.sh` and from `pre-commit.sh` before the routes tier and never fails either one. It drops idle `sana_turnos_test_<suffix>` databases older than a day, without FORCE, and then runs `colima ssh -- sudo fstrim -av`, so the freed blocks go back to the Mac. Past 85% full it prints the steps to free the disk. The routes runner reclaims the databases of a run whose runner pid is gone after 5 minutes rather than 2 hours (`_routes_fixture._DEAD_RUNNER_GRACE_SECONDS`): pre-commit's reaper KILLs the runner 10 s after TERM, often before the runner's own drop. A disposable Postgres container anywhere on this VM is removed with `docker rm -f -v` or `docker stop`. A bare `docker rm -f` of a `--rm` container skips its auto-remove and leaves its anonymous data volume; that is how 281 volumes from Lira's local CI filled the disk. Tests: `tests/test_lane_janitor.py`, `tests/test_test_db_up.py`, `tests/test_routes_run_isolation.py`.

## Browser e2e (`npm run e2e`)

Workers, retries and the failure artifacts (the fixed part of the `CLAUDE.md` line is the flaky-fails rule):

> One worker per core for a bare run (`PW_WORKERS`; local-ci, the `e2e-asgi` profile and the GitLab e2e job cap it at 4), `retries: 2`, and a test that fails once and passes on a retry still FAILS the run (`PW_FAIL_ON_FLAKY=0` lets it pass; the HTML report in `playwright-report/`, and in `test-results/` the screenshot and page snapshot of every failed attempt and the trace of the first retry, stay).

The specs, the config and the fixtures:

> `npm run e2e` is **@playwright/test** (`playwright.config.mjs`; specs `tests/e2e/*.spec.mjs`; fixtures `tests/e2e/lib/fixtures.mjs` on top of `lib/harness.mjs`)

Running one file, and the developer variant:

> One file: `npx playwright test tests/e2e/<name>.spec.mjs --workers=1`. `npm run e2e:dev` skips instead of failing when Chromium/Postgres are missing.

## Backend shards

> A test helper that connects or hashes per row is what made the heaviest files slow, not the files.

## Coverage floors

> `coverage` comes from `scripts/requirements-ci.txt` (CI only) and c8 from `npm ci`; in CI both are required, on a developer machine `ci_local.sh` skips the measurement with a loud warning when one is missing (`TURNOS_COVERAGE=0` skips it on purpose). The frontend test count has its own floor, `min_js_tests`.

## Generated snapshots

What `tests/snapshots.json` holds (the sentence after «holds the values tests compare with what the code produces:»):

> holds the values tests compare with what the code produces: one hash per server-rendered page with the content-hashed asset URLs masked (the URLs themselves are checked against the files), the route census counters, who owns each table (a module's `db_tables`, else platform), the connector list, and the permission each admin route needs. After

## Storybook

> A panel screen is only story-able once its render lives in a module that owns its own `css` string; extracting the module IS the work. Config in `.storybook/`, stories in `stories/`. Visual regression → Chromatic (`npm run chromatic`, skips itself without `CHROMATIC_PROJECT_TOKEN`).
