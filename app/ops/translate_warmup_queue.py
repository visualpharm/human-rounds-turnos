"""translate_warmup_queue.py — the pack-content translation-cache warmup
as a procrastinate periodic task (Tier 4 periodic-daemon batch 3,
docs/roadmap/tier4-replatform-design.md, "Worker topology"): the SAME
per-language `translate_warmup.sweep()` pass the daemon thread runs every
6 hours (translate_warmup._interval_min() — a module constant, not an env
knob), registered on a queue app instead. OUTBOUND-CAPABLE by design: a
sweep with stale strings calls the external AI provider (OpenRouter) —
the reason batch 2 deferred it, and why its registry entry declares
`outbound=True` and is proven under the fail-closed kill switch
(test_ai_cache_queue_proof.py).

Deliberately a SEPARATE sibling module from `translate_warmup.py`, never
imported at that module's top level — same reasoning as every other
`*_queue.py` twin: only `worker.py` imports this, and only when the
worker process actually runs.

Behavior notes:

- First-run contract PRESERVED verbatim, not approximated: the thread
  twin sweeps FIRST_RUN_DELAY_S (30 s) after boot, and so does the
  queue topology — `worker._defer_startup_jobs` defers one startup
  sweep per worker boot with `startup_delay_s=30` carried in the job
  row's scheduled_at (`schedule_at`, core.clock's boot instant + 30 s): durable (a Postgres row, never a
  sleep), queued-row-deduplicated, declared by the registry entry. A
  cold install never waits up to 6 h for the first cron boundary; the
  render path never blocks on this daemon either way — an unfilled
  cache shows the pack's own language until the sweep lands.
- Language eligibility is re-resolved EVERY tick (`pending_languages()`),
  never at registration: the registry's `enabled()` is the OFF flag
  alone, so the job stays registered and no-ops per tick with no second
  language — enabling one from the panel reaches the next sweep without
  a worker restart, same as the thread loop re-reading it each pass.
- Provider failures degrade, never fail the job: `translate_batch`
  swallows TranslationUnavailable per batch/string, so a sweep with the
  provider down (or the outbound kill switch armed) returns 0 translated
  and the next tick retries — the thread's exact contract.
- A worker stop ends the sweep at the next batch (`core.worker_stop`):
  procrastinate cannot cancel a synchronous body, and a whole-catalog
  sweep outlasts the supervisor's stop budget. What is left stays stale
  and the next sweep translates it.
"""
import sys

_QUEUE = "translate_warmup"
_TASK_NAME = "ops.translate_warmup.sweep"


def _log(msg):
    print(f"[translate_warmup_queue] {msg}", file=sys.stderr, flush=True)


def register(app, interval_min, cron=None):
    """Registers the periodic task on a procrastinate App built by the
    CALLER (worker.py; tests build their own). Registration mechanics
    live ONCE in `queue_infra.register_periodic_sweep` — this module
    only owns the sweep body."""
    from app.core import queue_infra

    def _cuerpo(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        from app.ops import translate_warmup
        from app.core import worker_stop
        stop = worker_stop.check()
        out = {}
        for lang in translate_warmup.pending_languages():
            if worker_stop.halted(stop):
                _log("stop requested: the languages left wait for the next sweep")
                break
            total, done = translate_warmup.sweep(lang, stop=stop)
            out[lang] = {"strings": total, "translated": done}
            if done:
                _log(f"{lang}: {done} of {total} pack strings translated")
        return out

    # exclusive: an outbound-capable sweep never runs twice concurrently —
    # the task registers procrastinate's native per-job lock as its
    # default (queue_infra.task_lock_name), so cron, startup and recovery
    # invocations all serialize at fetch time; a concurrent second job
    # WAITS and runs after, where the run-twice-proven fingerprint/hash
    # guard makes it a no-op on unchanged sources — lock plus guard close
    # the double-spend path.
    return queue_infra.register_periodic_sweep(
        app, queue=_QUEUE, task_name=_TASK_NAME,
        interval_min=interval_min, cron=cron, body=_cuerpo, exclusive=True)
