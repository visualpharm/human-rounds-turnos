"""modules/intake/app.py — intake's Tier 2 lifecycle entrypoint
(module.json's "entrypoint": "modules.intake.app").

startup(ctx)/shutdown(ctx) are what let this module own its OWN
integration points instead of platform files scattering an
`app_modules.is_enabled("intake")` + `from modules.intake import ...`
inline: startup() registers named hooks that callers invoke by NAME,
generically, with no knowledge that intake exists.
app_modules.hooks.call() itself filters by "is intake enabled AND healthy
RIGHT NOW" (same live, no-restart contract every other module gate in this
repo has) — so the caller doesn't need its own enablement check, and this
file doesn't need to re-check it either.

The first hook ("interview_reminder", the day-before reminder
notifications.py renders) was the Tier 2 framework's proof migration. The
2026-08-16 hook-boundary slice finished the sweep: the four remaining
PRODUCTION scatter sites (turnos' booking_requests.py and
doctor_appointments.py, admin_panel's doctor_patients.py, ai's
chart_summary.py) now call their hooks by name too, and no production
code outside modules/intake imports it anymore (the only remaining
importers are dev tooling: scripts/route_parity_check.py, which imports
every route module on purpose, and scripts/seed_demo_all.py, which
reuses intake's analysis for demo seeding) — the bodies live in
modules/intake/hooks.py (see its docstring for the behavior-preserving
— not verbatim — extraction provenance of each one)."""


def _handle_interview_reminder(row, handler=None):
    """The hook body: unchanged behavior, just no longer reached via a
    direct cross-module import from reminders.py. notifications.py stays
    platform code (it renders the SAME email/WhatsApp templates every
    other reminder uses) — this hook only decides WHETHER to reach it. Hands
    back its answer: False (it failed) makes the sweep retry."""
    from app.notifications import send as notifications
    return notifications.notify_interview_reminder(row, handler)


def startup(ctx):
    """Registers every named hook intake owns: the reminder plus the whole
    integration table in modules/intake/hooks.py (ONE place binds a hook
    name to its body — see that module's REGISTRY)."""
    from app.modules.intake import hooks as integration_hooks
    ctx.register_hook("interview_reminder", _handle_interview_reminder)
    for name, fn in integration_hooks.REGISTRY:
        ctx.register_hook(name, fn)


def shutdown(ctx):
    """Nothing to release: the hooks registry itself already stops
    firing these callables once intake is disabled (app_modules.hooks.call()
    checks is_enabled() live, at call time) — no separate unregistration
    needed for correctness."""
    pass
