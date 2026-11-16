# Worker signal ownership: `Worker.stop()`, never `task.cancel()`

`worker.py` owns the process signal contract. A first SIGTERM/SIGINT asks
for graceful shutdown; a second exits immediately with
`EXIT_FORCE_KILL` (3). Neither signal is allowed to escape through an OS
default disposition while `main()` is still live.

## Why task cancellation was wrong

Cancelling the top-level task can inject `CancelledError` into arbitrary
unprotected awaits: pool creation, startup recovery, startup defers, or
Procrastinate's own `Worker.run()` bootstrap
(`prune_stalled_workers`/`register_worker`). That can abandon a partially
created resource without a cleanup path.

The worker is therefore constructed directly with
`install_signal_handlers=False`. Once it exists, the asyncio signal callback
uses its own `Worker.stop()` flag. It never cancels the worker task.

## Current ownership model

`main()` creates one `_SignalState` per invocation. The plain handler used
before the event loop and the asyncio callback used after it starts share
that state and its one signal counter:

1. The first signal records `stop_requested`.
2. A bounded operation already in progress may finish, but every later
   startup side-effect boundary exits cleanly before it begins.
3. If a Worker exists, only the asyncio callback calls `Worker.stop()`.
   The synchronous handler never does, avoiding the construction-to-
   `Worker.run()` stop-event-clear race.
4. A second SIGTERM or SIGINT always calls
   `_force_exit(EXIT_FORCE_KILL, signum)`. It cannot become a raw SIGTERM
   death or `KeyboardInterrupt`.

The main-body boundaries cover outbound-guard arming, configuration and URL
validation, schema initialization, app construction, job registration,
health-server startup, and `asyncio.run()`. A first early signal therefore
does not keep booting through DDL or startup services.

At async teardown, `loop.remove_signal_handler()` would normally restore
SIGTERM to `SIG_DFL` and SIGINT to `default_int_handler`. Before that real
removal, `_restore_main_signal_handlers()` blocks both signals with
`pthread_sigmask`; it restores a supplied target disposition before
unblocking. `main()` supplies its shared early handler, then its outer
`finally` restores the process's pre-call dispositions. A direct
`_boot_and_run()` caller instead snapshots its own exact prior SIGTERM and
SIGINT dispositions before installing asyncio callbacks and supplies that
snapshot to the same masked remove-and-restore handoff. A signal delivered
in either handoff interval remains pending and reaches the intended handler,
never an asyncio-installed default.

The deferred `from core import ...` in `main()` is inside that same outer
`try`/`finally`. If it fails after the early handlers are installed, both
pre-call dispositions still restore before the import error propagates.

## A synchronous task body hears the stop through `core.worker_stop`

`Worker.stop()` stops fetching and waits for the jobs in flight; it never
interrupts a synchronous task body (procrastinate runs it in a thread), and
`asyncio.run` then waits for that thread. A long sync sweep therefore held
the whole stop: the catalog translation ran 190 s past a SIGTERM and the
supervisor SIGKILLed the worker at its 45 s budget (2026-11-06).

The first signal now also calls `worker_stop.request()`, which sets one
event opened per run in `_boot_and_run` (`begin`/`end`). A sweep takes
`stop = worker_stop.check()` once at its start and asks
`worker_stop.halted(stop)` between two units of work, then returns what it
finished: translate_warmup between languages, batches and fallback strings,
chart_summary between pairs, reminders before each claim, hsi_sync between
its phases. What is left is covered by each job's `rerun` contract. Nothing
outside the worker passes a stop (`halted(None)` is always False). The one
thing still not interruptible is a single provider call in flight.

Proof: `test/test_worker_stop.py` (each sweep halts at its boundary) and
`TestSweepStopsOnSignal` in `test/test_queue_worker.py` (a real SIGTERM in
the middle of a translation, exit 0 inside the budget, the next run
finishes the catalog), using `TURNOS_TEST_SWEEP_STEP_HOLD_S` to hold each
boundary.

## Honest boundary

Python cannot install a handler before interpreter startup and this module's
top-level imports have run. A SIGTERM sent immediately after `Popen()` may
therefore still return raw `-15` only in that pre-`main()` window. The
worker must still exit in bounded time and must not leave registration state
there; it does not claim to turn that pre-main result into graceful exit 0.

## Regression proof

`test/test_queue_worker.py` covers the contract with real subprocesses and
in-process direct-call seams:

- `test_early_signal_before_schema_skips_every_later_side_effect` sends a
  real SIGTERM during DB-URL lookup and proves no schema/build/register/health
  side effect starts.
- `test_early_signal_inside_schema_finishes_schema_then_stops` sends it from
  fake schema and proves schema completes, no later side effect starts, and
  the process exits 0.
- `test_signal_after_real_async_handler_removal_forces_exit_3` injects the
  second signal immediately after the real asyncio removal and proves exit
  code 3, not raw 143.
- `test_signal_during_run_calls_worker_stop_not_task_cancel`,
  `test_early_sigterm_aborts_bootstrap_before_worker_exists`, and
  `test_second_signal_forces_exit_not_default_disposition` retain the
  direct-call ownership and cancellation contracts.
- `test_direct_boot_restores_exact_prior_signal_handlers` installs custom
  SIGTERM/SIGINT handlers around a direct coroutine call and proves both
  exact caller dispositions return after real asyncio removal.
- `test_main_restores_handlers_when_deferred_core_import_fails` forces the
  deferred core import to fail and proves `main()` restores both custom
  caller dispositions on that exception path.

Focused shutdown tests passed. The required complete worker-suite run on
2026-08-09 took 325.089 s and ran 144 tests. One unrelated residual failed:
`TestAiCacheExecutionLock.test_startup_vs_startup_two_workers_never_overlap`
left the second chart-summary job `doing` instead of `succeeded` after its
60-second deadline. It is outside this signal-ownership change.
