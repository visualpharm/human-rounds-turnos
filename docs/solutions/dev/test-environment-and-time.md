---
title: Tests set the environment through a fixture that puts it back, and ask a clock the test can freeze
scope: repo
area: dev
tags: [tests, flaky, environment, clock, ratchet]
created: 2026-10-27
refs: [test/_env.py, test/_wait.py, test/helpers/wait.mjs, test/_clock.py, core/clock.py, scripts/hygiene_ratchet.py, scripts/hygiene_floor.json, test/test_hygiene_ratchet.py, test/test_env_fixture.py, test/test_clock.py, modules/turnos/booking_horizon.py]
status: active
---

## Problem
Two kinds of flaky test kept coming back, about seven fixes a day in the last audit window:

- **The neighbour.** `os.environ` is one dict for the whole interpreter. A test that writes it and
  does not undo the write hands the value to the next test, or to a daemon thread an earlier module
  left running. At 2026-10-27 the suite had ~2500 such writes in ~350 test files.
- **The clock.** A test that reads the machine's clock (`datetime.now()`, the timezone of the node
  process, a fixed `sleep`) is red only at some hours: `_at(2)` straddling a second boundary, "today"
  being yesterday in Buenos Aires between 00:00 and 03:00 UTC, "the first free time" having passed.

## Solution
Three small pieces, none of which asks for a mass rewrite:

- `test/_env.py` - `set_env(self, KEY="v", OTHER=None)` writes for the running test (None removes the
  key) and puts every key back after it, whatever the test did; `patched_env(...)` is the same as a
  `with` block; `set_env_for_module(...)` is for `setUpModule`; `process_defaults(...)` is the
  import-time `setdefault` for the switches a module needs before it imports the application.
- `core/clock.py` - the application's one "now": `clock.now()` (installation timezone), `clock.utcnow()`,
  `clock.today()`. `test/_clock.py` - `freeze(self, "2026-10-27T01:30:00Z")` pins it for the test and
  returns a clock to `advance(hours=2)`; `freeze(self)` pins the wall clock once, in whole seconds.
  A module moves to `core.clock` when a test needs to pin it; the first is `booking_horizon`.
- `scripts/hygiene_ratchet.py check` (run by `test/test_hygiene_ratchet.py`) counts, per test file,
  the writes to `os.environ`, the `patch.dict` calls and the blind pauses (`time.sleep`,
  `waitForTimeout`, `setTimeout(resolve, <ms>)`). `scripts/hygiene_floor.json` holds how many each
  file may keep; a file with more, or a new file with any, is red. Numbers only go down:
  `python3 scripts/hygiene_ratchet.py write` lowers the snapshot after you removed sites.

## How to apply
- Need a variable in a test: `_env.set_env(self, TURNOS_X="1")` in `setUp` or in the test. Never
  `os.environ[...] = ...`, and never a hand-written backup in `setUp` and restore in `tearDown`.
  If a cache reads the variable (`config`, `data_packs`, `i18n`), register its reset with `addCleanup`
  BEFORE the `set_env` call: cleanups run last-in first-out, so the cache is dropped after the
  environment is back.
- Need a value at import time (before `from core import ...`): `_env.process_defaults(...)`. A
  value forced at import time, not a default, cannot be undone by a fixture: keep it out of the module,
  or leave that file's count where it is.
- Test depends on "now": freeze it (`_clock.freeze`) and compute expected values from the frozen
  instant, never from `datetime.now()`. Pick instants that straddle the installation's midnight
  (01:30 UTC and 15:00 UTC for Buenos Aires). SQL `now()` is Postgres' clock and cannot be frozen
  from Python: mask the value in the test (`test/routes/_body_contract.py`) or compare relative to
  `SELECT now()`.
- Browser tests: freeze the page with `page.clock.setFixedTime(...)` (see
  `test/admin-create-appointment-native.test.mjs`); wait for a condition with a retrying `expect` or
  `holdsFor`, never `waitForTimeout`. A journey whose answer depends on the server's time of day (the
  first free time today) books on a later day instead.
- A value forced at import time, not a default (the module must NOT inherit the developer's value):
  `_env.process_set(KEY="v", OTHER=None)`. It overwrites and is never undone; it is only for what the
  module needs before it imports the application. A key whose name contains `SECRET`, `TOKEN`,
  `PASSWORD` or `KEY` with a value of 16+ characters goes in the dict form
  (`_env.process_set({"TURNOS_ADMIN_SECRET": "..."})`): written as a keyword the public-cut preflight
  reads it as an assigned credential (`secret-assignment`).
- `patched_env` puts the WHOLE environment back (like `patch.dict`); use it, with
  `self.enterContext(...)`, when the code under test writes other keys of its own.
- A test waits for a condition, not for seconds: `_wait.until(lambda: ..., "what")` (JS:
  `test/helpers/wait.mjs`) polls on an event with a 30 s ceiling that only a hang reaches;
  `_wait.stays(cond, ...)` checks that something keeps holding for a window and fails at the first
  violation. A bare sleep that "lets time pass" for the code under test is a missing clock seam: inject
  the clock instead.
- A wait on something that runs at the machine's own speed (a real `worker.py` booting: imports, pool,
  recovery) takes `_wait.until(..., timeout=<quiet-machine ceiling>, scale_with_load=True)`: the ceiling
  is multiplied by the 1-minute load per CPU (1 to 8, re-read while waiting). `WorkerProc.wait_line`
  does this for every log line it waits on, and every poll of `test/test_queue_worker.py` goes through
  its `_until`. A ceiling handed to something that is not a poll (`subprocess.run(timeout=...)`,
  `proc.wait(...)`, a test hold the worker must outlast) is `_wait.scaled(<quiet seconds>)`. A fixed
  20 s was red at load ~90 on 18 CPUs (2026-11-06).
- Stage 2 (2026-10-30) moved every `os.environ` write of the suite to the fixture (2417 to 0), all
  but ten `patch.dict` calls on dicts that are not the environment (`sys.modules`, tool registries)
  and about a hundred pauses. The remaining pauses are in the floor with their reason: the manual
  `test/usecases/run.mjs` runner and a few windows that prove "nothing happens".
- A branch that adds a site is red in `test_hygiene_ratchet`. Use the fixture; do not raise a number.
  Raising one is a reviewed hand edit of `scripts/hygiene_floor.json` with the reason in the commit.
