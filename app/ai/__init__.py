"""ai/ — AI-backed clinical assistance: model client, cost accounting,
health/circuit-breaker checks, referral triage, prompt management, chart
summaries and lab-result analysis.

Tier 2 batch E (docs/roadmap/tier2-physical-modularization-plan.md): a
domain package, not a module — shared infra other code (modules/turnos/,
modules/intake/, chat_agent.py/chat_tools.py, admin screens) calls into,
never disabled at runtime. Deliberately minimal: no re-export facade.
Importers use explicit submodules:

    from ai import ai_client               # model call, retries, primary/fallback key
    from ai import ai_health                # circuit-breaker + /api/ai-status
    from ai import ai_health_router          # FastAPI twin (not yet ASGI-mounted)
    from ai import ai_costs                  # per-call cost accounting + /api/admin/costs
    from ai import ai_costs_router           # FastAPI twin (not yet ASGI-mounted)
    from ai import ai_cooldown               # per-actor rate limiting for AI calls
    from ai import ai_referral               # medical-order 3-pass referral analysis
    from ai import prompts                   # prompt registry, overrides, admin CRUD
    from ai import prompts_router            # FastAPI twin (not yet ASGI-mounted)
    from ai import orden_analysis_cache      # caches ai_referral's analysis per order
    from ai import abuse_guard               # Turnstile-backed anti-abuse gate
    from ai import chart_summary             # /api/admin/patient-summary
    from ai import chart_summary_router      # FastAPI twin (not yet ASGI-mounted)
    from ai import lab_analysis              # lab-result value extraction/classification
    from ai import lab_results               # /api/account/lab-results
    from ai import lab_results_router        # FastAPI twin (not yet ASGI-mounted)

`lab_results.py` imports `accounts` and `patient_web/site_*.py` (both pre-existing,
not yet relocated — Batch G/`cuentas/` and Batch H/`patient_web/`
respectively); this is a known, accepted forward-reference until those
batches land, registered as an exception in
tests/test_domain_dependency_direction.py, same pattern as batch B's
now-resolved notifications/send.py exception.
"""
