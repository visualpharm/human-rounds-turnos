"""chart_summary_queue.py — the patient chart-summary pre-generation sweep
as a procrastinate periodic task (Tier 4 periodic-daemon batch 3,
docs/roadmap/tier4-replatform-design.md, "Worker topology"): the SAME
`chart_summary.tick()` the daemon thread runs every
TURNOS_SUMMARY_TICK_MIN minutes (default 60), registered on a queue app
instead. First job of the batch that is OUTBOUND-CAPABLE by design: the
tick's `generate()` may call the external AI provider (OpenRouter) for
each stale (dni, medico) pair — which is why batch 2 deliberately left it
behind (its design-doc deferral note: "call external AI providers") and
why its registry entry declares `outbound=True` and is proven under the
fail-closed kill switch (test_ai_cache_queue_proof.py).

Deliberately a SEPARATE sibling module from `chart_summary.py`, never
imported at that module's top level — same reasoning as
`waitlist_queue.py` vs `waitlist.py`: `chart_summary.py` is imported by
the web process unconditionally (routing), so anything at ITS top level
becomes an unconditional cost of the default (no queue) deployment
shape. Only `worker.py` imports this module, and only when the worker
process actually runs.

Behavior notes:

- First-run contract PRESERVED, not approximated: the thread loop runs
  its first tick immediately at boot, and so does the queue topology —
  `worker._defer_startup_jobs` defers one immediate sweep per boot
  (durable Postgres row, deduplicated by a queueing lock), declared by
  the registry entry's `startup=True`. A cold install never waits up to
  TURNOS_SUMMARY_TICK_MIN for the first cron boundary. On-demand
  `generate()` in the request path stays the same-day/walk-in fallback
  in every topology regardless.
- The task body may reach the external AI provider. Under
  TURNOS_FAKE_AI=1, a missing key, or a tripped ai_health breaker,
  `generate()` degrades to the deterministic build exactly like the
  thread — the sweep itself never needs the provider to succeed.
"""
import sys

_QUEUE = "chart_summary"
_TASK_NAME = "ai.chart_summary.pregen_sweep"


def _log(msg):
    print(f"[chart_summary_queue] {msg}", file=sys.stderr, flush=True)


def register(app, interval_min, cron=None):
    """Registers the periodic task on a procrastinate App built by the
    CALLER (worker.py; tests build their own). Registration mechanics
    (cron derivation, the `cron` test seam, the double-gated mid-task
    hold the crash suites kill through) live ONCE in
    `queue_infra.register_periodic_sweep` — this module only owns the
    sweep body."""
    from app.core import queue_infra

    def _cuerpo(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        from app.ai import chart_summary
        from app.core import worker_stop
        # tick() never raises (its own contract): a DB outage returns 0
        # and the next tick retries; a per-pair failure is logged and
        # skipped — the same guarantees the thread loop relies on. A
        # worker stop ends it before the next summary (core.worker_stop).
        hechos = chart_summary.tick(stop=worker_stop.check())
        if hechos:
            _log(f"barrido: {hechos} resumen(es) pre-generado(s)")
        return {"generados": hechos}

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
