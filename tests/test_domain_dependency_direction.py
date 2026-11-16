"""Tier 2 (docs/roadmap/tier2-physical-modularization-plan.md): a domain
package at root — `notifications/` (batch B), `auth/` (batch C), more to
follow — is shared infra other code calls into, never a disable-able
module. The plan's dependency-direction rule: a domain package may from app import core infra (db, config, tz, i18n, format, sitedata, site_identity, theme,
proxy_trust, module_router, demo_installation, prep_model...)
but never server, app, patient_web (the public site — batch H, 2026-08-07;
was `patient_site` before it physically moved), admin_* screens,
booking_requests, notifications.*, or modules.*. Those are all CONSUMERS of
a domain package, and a package that reached back into its own consumers
would be a real (if quiet) circular coupling.

Generalized (originally test_notifications_dependency_direction.py, batch B
only) so each new domain package registers itself in `_PACKAGES` below
instead of getting its own copy-pasted AST-walking test file.

Batch D (2026-08-06): `notifications/send.py`'s `("send.py",
"booking_requests")` exception below is GONE, resolved for real, not
re-excepted — exactly what this docstring told batch D to do the moment
`booking_requests.py`/`prep_model.py` physically moved into
modules/turnos/. `notifications/send.py` no longer statically imports
either one; each registers itself into `notifications.send.register_turnos()`
at its own import time instead (the allowed direction — a module may
import a domain package). `TestTurnosModuleDependencyDirection` below adds
the new package's own half of the rule: a MODULE must never import a
composition root (server/app) or a foreign admin_* screen either.

Batch F (2026-08-07): `TestChatModuleDependencyDirection` adds the same
MODULE-side half of the rule for `modules/chat/` — chat_agent, chat_api(+
_router), chat_language, chat_tools, whatsapp_chat, voice(+_router),
mcp_api physically moved there. `chat_tools.py`'s `from app.modules.turnos
import booking_requests` is a real, declared `requires: ["turnos"]`
dependency (module -> module, the allowed direction), not checked by this
rule — same treatment intake's declared `requires` edges (turnos, chat)
get.

Batch G (2026-08-07): `cuentas` registers as a fourth domain package —
accounts(+_router), account_photo(+_router), auth_renaper,
health_coverage, ocr_dni(+_router) physically moved there. Two genuine,
pre-existing forward references, same treatment ai/lab_results.py's
`accounts`/`patient_site` exceptions got: `accounts.py` lazily imports
`from app.notifications import mailgun as email_mailgun` (password-recovery
email) and `from app.notifications import otp_send` (WhatsApp OTP) — a
domain package sending its own notifications, not a `notifications.*`
CONSUMER reaching backward; and `accounts.py`'s `_serve_account()`
lazily imports `from app.modules.turnos import booking_requests` to bootstrap
the account page's "Mis turnos" tab — the same kind of forward reference
`confirmations.py` has into `modules.chat` (notifications' own accepted
exception above). Neither is resolved for real in this batch (no
register-a-callback refactor attempted) — revisit if either domain is
touched again.

Batch H (2026-08-07): `patient_web` registers as a fifth domain package —
patient_site(+_router), catalog, knowledge, legal(+_router), seo,
sitemap(+_router), branding(+_router), avatar_sprite(+_router),
doctor_photos(+_router) physically moved there. Every OTHER package's own
predicate that used to forbid the bare name `patient_site` (the public
site is a consumer of every lower domain package, same footing as
`booking_requests`/`admin_*`) now forbids `patient_web` instead — the AST
top-level name changed the moment the file moved, exactly like batch G's
own `accounts` -> `cuentas` rename. Two files elsewhere in the tree had a
real, pre-existing forward reference whose top-level name changed the
same way and needed their exception tuple renamed, not newly created:
`ai/lab_results.py` (`patient_site` -> `patient_web`, already excepted)
and `notifications/send.py`, which gained one (`catalog`/`branding` were
never forbidden as bare names before the move — they only became
`patient_web` CONSUMER imports once physically relocated). `ai/
chart_summary.py`'s own `from app.patient_web import catalog` is a NEW
exception, same reasoning, different file. Within `patient_web` itself,
`patient_site.py`'s forward references into `cuentas` (`accounts`, is-the-
visitor-logged-in) and `modules.turnos` (`prep_model`/`slot_holds`,
wizard/slot-hold bootstrap) and `catalog.py`'s into `modules.turnos`
(`prep_model`/`booking_limits`) are genuine, pre-existing, and accepted
the same way every prior batch's forward references were — not resolved
for real in this batch.

Batch J (2026-08-07): `ops` registers as a seventh domain package —
setup, data_backups, demo_installation, demo_refresh, demo_refresh_store,
housekeeping, cache_warmup, translate_warmup, and runtime orchestration live
there. Like
`admin_panel`, `ops` sits at the TOP of the dependency stack: it's the
lifecycle/orchestration layer (first-run wizard, demo gating/day-rollover,
DB hygiene, cache/translation warmers, the directory-watcher daemon,
off-box backups, legacy HTTP lifecycle) and legitimately reaches into every lower package —
`setup.py` needs `admin_panel.admin_users` (staff account creation) and
`auth.admin_auth`; `demo_installation.py` needs `cuentas.accounts` and
`auth.admin_auth`; `housekeeping.py` needs `admin_panel.system_status`,
`auth.admin_auth` and `notifications.mailgun`; `cache_warmup.py` and
`translate_warmup.py` need `patient_web.patient_site`/`branding`/`catalog`
(warming the public site's own caches); `legacy_web_server.py` composes only
lower-level runtime dependencies. None of `ops`'s eleven files import
`server`/`app` (audited before writing the predicate), so
`_forbidden_ops` only forbids the composition roots, same reasoning
`_forbidden_admin_panel` already established.

Batch I (2026-08-07): `admin_panel` registers as a sixth domain package —
admin_appointments(+_router), admin_patients(+_router), admin_content(+
_router), admin_users(+_router), admin_account(+_router), admin_preferences(+
_router), admin_cola_screen, admin_mis_turnos_screen, admin_pacientes_screen,
doctor_patients(+_router), reports(+_router), system_status(+_router),
settings_admin(+_router) physically moved there. Unlike the other five
domain packages, `admin_panel` sits at the TOP of the dependency stack —
staff screens legitimately reach into every lower package (`ai`, `auth`,
`cuentas`, `patient_web`, `notifications`, `modules.*`) to show/manage
patient, booking, AI-cost and report data, so `_forbidden_admin_panel` only
forbids the composition roots (`server`/`app`) — none of the other
packages' broader "no modules/no notifications/no cuentas" rules apply
here, and none of `admin_panel`'s own 23 files import `server`/`app`
(audited before writing the predicate — a real empty exceptions set, not
an unaudited guess). The five OTHER packages' `top.startswith("admin_")`
checks already forbid the new top-level name "admin_panel" for free — it's
a prefix match, not an exact-name match, so no code change was needed
there; only `_PACKAGES` gained the new entry.
"""
import ast
import os
import unittest

from _layout import APP_PREFIX, in_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _forbidden_notifications(top):
    if top in {"server", "main", "routers", "services", "patient_web",
               "booking_requests"}:
        return True
    if top.startswith("admin_"):
        return True
    if top == "modules" or top.startswith("modules."):
        return True
    return False


def _forbidden_auth(top):
    if top in {"server", "main", "routers", "services", "patient_web",
               "booking_requests"}:
        return True
    if top.startswith("admin_"):
        # `admin_*` SCREENS (admin_users, admin_content, admin_patients...)
        # are consumers. `admin_auth`/`admin_auth_router` live INSIDE auth/
        # itself and are only ever reached via `from auth import admin_auth`
        # (top-level name "auth"), never as a bare "admin_auth" import from
        # another file in this package — so this predicate never actually
        # fires on auth/'s own submodules, it only catches a real screen.
        return True
    if top == "notifications" or top.startswith("notifications."):
        return True
    if top == "modules" or top.startswith("modules."):
        return True
    return False


def _forbidden_ai(top):
    if top in {"server", "main", "routers", "services", "patient_web",
               "booking_requests", "cuentas"}:
        return True
    if top.startswith("admin_"):
        return True
    if top == "notifications" or top.startswith("notifications."):
        return True
    if top == "modules" or top.startswith("modules."):
        return True
    return False


def _forbidden_cuentas(top):
    if top in {"server", "main", "routers", "services", "patient_web",
               "booking_requests"}:
        return True
    if top.startswith("admin_"):
        return True
    if top == "notifications" or top.startswith("notifications."):
        return True
    if top == "modules" or top.startswith("modules."):
        return True
    return False


def _forbidden_patient_web(top):
    if top in {"server", "main", "routers", "services", "booking_requests"}:
        return True
    if top.startswith("admin_"):
        return True
    if top == "notifications" or top.startswith("notifications."):
        return True
    if top == "modules" or top.startswith("modules."):
        return True
    if top == "cuentas" or top.startswith("cuentas."):
        return True
    return False


def _forbidden_ops(top):
    # ops sits at the TOP of the dependency stack alongside admin_panel —
    # the lifecycle/demo layer legitimately imports admin_panel/auth/
    # cuentas/patient_web/notifications/modules.* (see the module
    # docstring's batch J note), so only the composition roots (and their
    # Tier 4 composition packages routers/ and services/) are forbidden
    # here.
    if top in {"server", "main", "routers", "services"}:
        return True
    return False


def _forbidden_services(top):
    # services/ (Tier 4 composition extraction, 2026-08-08) is the
    # transport/lifecycle layer UNDER the composition root: core infra and
    # third-party transport libraries only. Everything above it — the
    # composition roots, routers/, and every domain/screen/module package —
    # is a CONSUMER; a service reaching upward would recreate exactly the
    # coupling the extraction removed from app.py.
    if top in {"server", "main", "routers", "booking_requests"}:
        return True
    if top.startswith("admin_"):
        return True
    for pkg in ("admin_panel", "auth", "ai", "cuentas", "patient_web",
                "notifications", "modules", "ops"):
        if top == pkg or top.startswith(pkg + "."):
            return True
    return False


def _forbidden_routers(top):
    # routers/ (Tier 4 composition extraction, 2026-08-08) composes domain
    # `*_router` modules and calls into services/ — both allowed directions.
    # Only the composition roots are forbidden: server.py/app.py are this
    # package's consumers, never its dependencies.
    if top in {"server", "main"}:
        return True
    return False


def _forbidden_admin_panel(top):
    # admin_panel sits at the TOP of the dependency stack — staff screens
    # legitimately import ai/auth/cuentas/patient_web/notifications/
    # modules.* to show/manage patient, booking, AI-cost and report data,
    # so only the composition roots (and their Tier 4 composition packages
    # routers/ and services/) are forbidden here.
    if top in {"server", "main", "routers", "services"}:
        return True
    return False


# package dir name -> (forbidden predicate, known pre-existing exceptions as
# {(filename, forbidden_top_name), ...}, minimum expected file count)
_PACKAGES = {
    "notifications": (
        _forbidden_notifications,
        {
            # confirmations.py's "no puedo" (can't make it) SMS/WhatsApp
            # reply frees the slot by calling the SAME cancel path the
            # patient's own chat/MCP/widget use (chat_tools.TOOL_FNS
            # ["cancel_request"], inline-imported, wrapped in try/except so
            # a genuinely missing chat module degrades to "not cancelled"
            # rather than crashing the reminder pipeline). This import was
            # already forward-looking (confirmations.py -> chat_tools.py)
            # before Tier 2 batch F physically moved chat_tools.py into a
            # disable-able module — the move only made the existing forward
            # reference visible to this rule (chat_tools.py used to be a
            # bare root file, not `modules.*`). Accepted the same way batch
            # B accepted send.py -> booking_requests until batch D resolved
            # it for real (register_turnos()); revisit with the same
            # register-a-callback pattern if this notification path is
            # touched again.
            ("confirmations.py", "modules"),
            # send.py's reminder/confirmation emails reuse the public site's
            # OWN date-formatting catalog template (`from patient_web import
            # catalog`, for the same `date.long` phrasing patient_site.py
            # uses) and its OWN branding colors (`from patient_web import
            # branding`, for the email's accent color) — a domain package
            # formatting its own outbound messages with the site's shared
            # content/branding, not a `patient_web` CONSUMER reaching
            # backward into notifications. Batch H (2026-08-07): both
            # imports' AST top-level name changed from bare "catalog"/
            # "branding" to "patient_web" when those files physically moved
            # into `patient_web/`; this exception did not exist before the
            # move because neither bare name was ever forbidden. Not
            # resolved for real in this batch, same treatment as every
            # other accepted forward reference above.
            ("send.py", "patient_web"),
        },
        6,  # send, mailgun, confirmations, reminders, otp_send, __init__
            # (gmail_confirm retired 2026-08-16 with server.py's WebTurno
            # client — see test_server_webturno_retirement.py)
    ),
    "auth": (
        _forbidden_auth,
        set(),  # batch C: no pre-existing forbidden imports found
        14,  # admin_auth(+_router), panel_login(+_router), passkeys(+_router),
             # webauthn_adapter, password_reset(+_router), mode, turnstile,
             # site_auth, mcp_guard, ip_exempt, __init__
    ),
    "ai": (
        _forbidden_ai,
        {
            # lab_results.py needs the account owner + public-site catalog to
            # resolve whose lab results these are and to link back to the
            # service page. `accounts` -> `cuentas` (Batch G, 2026-08-07:
            # accounts.py physically moved into cuentas/, so the import site
            # is now `from cuentas import accounts` and the AST top-level
            # name changed with it) — still a genuine forward reference,
            # not resolved for real. `patient_site` -> `patient_web` (Batch
            # H, 2026-08-07: patient_site.py physically moved into
            # patient_web/, so the import site is now `from patient_web
            # import patient_site` and the AST top-level name changed the
            # same way). Accepted the same way batch B accepted send.py ->
            # booking_requests until batch D resolved it for real.
            ("lab_results.py", "cuentas"),
            ("lab_results.py", "patient_web"),
            # lab_results_router.py (F8 native-route migration, 2026-08-26):
            # the native endpoint calls `accounts.current_user(request)`
            # directly (a Starlette `Request`'s `.headers` already duck-types
            # what that function needs) — same genuine forward
            # reference as `lab_results.py`'s own exception above, just
            # exercised from the sibling router file.
            ("lab_results_router.py", "cuentas"),
            # chart_summary.py lazily imports the public-site catalog
            # (`from patient_web import catalog`, inside a helper that
            # resolves a service's display name for the chart) — same
            # shape as lab_results.py's own catalog/patient_site forward
            # references above, just a different file in the same package.
            # AST top-level name is "patient_web" for the same reason.
            ("chart_summary.py", "patient_web"),
        },
        17,  # ai_client, ai_health(+_router), ai_costs(+_router), ai_cooldown,
             # ai_referral, prompts(+_router), orden_analysis_cache,
             # abuse_guard, chart_summary(+_router), lab_analysis,
             # lab_results(+_router), __init__
    ),
    "cuentas": (
        _forbidden_cuentas,
        {
            # accounts.py's own password-recovery email and WhatsApp-OTP
            # send are genuine, pre-existing forward references — a domain
            # package sending its own notifications via `notifications/`
            # (both lazy imports, `_send_email`/`_send_otp_whatsapp`), not
            # a `notifications.*` CONSUMER reaching backward into cuentas.
            # Accepted the same way notifications/confirmations.py accepted
            # its own forward reference into `modules.chat` below.
            ("accounts.py", "notifications"),
            # accounts.py's `_serve_account()` lazily imports `from
            # modules.turnos import booking_requests` to bootstrap the
            # account page's own "Mis turnos" tab (`solicitudes` in the
            # bootstrap payload) — the same shape confirmations.py's
            # `modules.chat` forward reference already has an accepted
            # exception for above. Revisit with the same register-a-
            # callback pattern (`notifications.send.register_turnos()`) if
            # this path is touched again.
            ("accounts.py", "modules"),
        },
        9,  # accounts(+_router), account_photo(+_router), auth_renaper,
            # health_coverage, ocr_dni(+_router), __init__
    ),
    "patient_web": (
        _forbidden_patient_web,
        {
            # The page code of the public site (`site_*.py`, the former `patient_site.py` split
            # by domain in TASK-32) checks whether the visitor is logged in (to personalize the wizard, show
            # "Mis turnos", etc.) via `from cuentas import accounts` (lazy call sites in the shell
            # and the service pages — the public site importing the account domain package it sits
            # on top of, not a `cuentas` CONSUMER reaching backward).
            ("site_page_service.py", "cuentas"),
            ("site_shell.py", "cuentas"),
            # The native sibling router resolves the same patient session and
            # auth mode from the real Starlette request before passing plain
            # values into the site_*.py page code. This is transport composition, not
            # a domain callback or compatibility handler.
            ("patient_site_router.py", "cuentas"),
            # The same page code bootstraps wizard/slot-hold data via `from modules.turnos import
            # prep_model` (module level) and `from modules.turnos import slot_holds` (lazy call
            # sites) — the site's own page routes reaching into the booking module that owns the
            # flow those pages render, same shape confirmations.py's `modules.chat` forward
            # reference has an accepted exception for in `notifications` above.
            ("site_availability.py", "modules"),
            ("site_page_service.py", "modules"),
            ("site_preparation.py", "modules"),
            # catalog.py (the editable content overlay the patient site
            # renders) needs `from modules.turnos import prep_model`
            # (module level, preparation-instruction defaults) and `from
            # modules.turnos import booking_limits` (lazy, admin content
            # validation) — same shape as the patient site's own forward
            # reference into modules.turnos above.
            ("catalog.py", "modules"),
        },
        29,  # patient_site_router, 14 site_* domain modules (site_base included), catalog, knowledge,
             # legal(+_router), seo, sitemap(+_router), branding(+_router),
             # avatar_sprite(+_router), doctor_photos(+_router), __init__
    ),
    "admin_panel": (
        _forbidden_admin_panel,
        set(),  # batch I: no pre-existing forbidden imports found (audited)
        25,  # admin_appointments(+_router), admin_patients(+_router),
             # admin_content(+_router), admin_users(+_router),
             # admin_account(+_router), admin_preferences(+_router),
             # admin_cola_screen, admin_mis_turnos_screen,
             # admin_pacientes_screen, admin_reportes_screen,
             # doctor_patients(+_router), reports(+_router),
             # system_status(+_router), settings_admin(+_router), __init__
    ),
    "ops": (
        _forbidden_ops,
        set(),  # batch J: no pre-existing forbidden imports found (audited)
        10,  # setup, data_backups, demo_installation, demo_refresh,
            # demo_refresh_store, housekeeping, cache_warmup,
            # translate_warmup, web_runtime, __init__
            # (watcher retired 2026-08-16 with the WebTurno client;
            # legacy_web_server retired with the legacy topology,
            # native-migration final wave)
    ),
    "services": (
        _forbidden_services,
        set(),  # Tier 4 composition extraction: born clean, keep it clean
        2,  # static_assets, __init__ (legacy_http/legacy_static retired
            # with the legacy topology, native-migration final wave)
    ),
    "routers": (
        _forbidden_routers,
        set(),  # Tier 4 composition extraction: born clean, keep it clean
        4,  # health, platform, native_terminal, __init__
            # (legacy_http_transport retired with the legacy topology)
    ),
}


def _imported_names(tree):
    """Imported module names inside the application package (`core.db`, `connectors`, `main`):
    `from app import connectors` is the package `connectors`, `from app import main` the
    composition root."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(in_app(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module and APP_PREFIX and node.module == APP_PREFIX.rstrip(".") and node.level == 0:
                names.extend(alias.name for alias in node.names)
            elif node.module:
                names.append(in_app(node.module))
    return names


class TestDomainDependencyDirection(unittest.TestCase):
    def test_package_files_exist(self):
        for pkg, (_forbidden, _exceptions, min_files) in _PACKAGES.items():
            pkg_dir = os.path.join(ROOT, "app", pkg)
            files = [f for f in os.listdir(pkg_dir) if f.endswith(".py")]
            self.assertGreaterEqual(
                len(files), min_files,
                "%s/ has fewer .py files than expected (%d < %d)"
                % (pkg, len(files), min_files))

    def test_no_domain_file_imports_a_consumer(self):
        offenders = []
        for pkg, (forbidden, exceptions, _min_files) in _PACKAGES.items():
            pkg_dir = os.path.join(ROOT, "app", pkg)
            for fname in sorted(os.listdir(pkg_dir)):
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(pkg_dir, fname)
                with open(path, encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source, filename=path)
                for name in _imported_names(tree):
                    top = name.split(".")[0]
                    if (fname, top) in exceptions:
                        continue
                    if forbidden(top):
                        offenders.append("%s/%s imports %r" % (pkg, fname, name))
        self.assertEqual(
            offenders, [],
            "a domain package imported from a consumer (server/app/"
            "patient_site/admin_*/booking_requests/notifications.*/"
            "modules.*) — dependency direction violation:\n  "
            + "\n  ".join(offenders))


class TestTurnosModuleDependencyDirection(unittest.TestCase):
    """Tier 2 batch D's own half of the dependency-direction rule (see
    module docstring): a MODULE (modules/turnos/, disable-able) must never
    import a composition root (server.py/app.py — those compose modules,
    never the reverse) or a foreign admin_* screen file (a module reaches
    the admin shell via its manifest's `admin_sections`, never by
    importing another domain's screen module directly).

    History note: booking_requests.py/doctor_appointments.py USED to reach
    into modules/intake/ via app_modules.is_enabled()-gated imports
    ("entrevista previa") — that reach was REMOVED in the 2026-08-16
    hook-boundary slice (named hooks owned by modules/intake; the static
    no-imports check now lives in tests/test_intake_hook_boundary.py, which
    covers admin_panel/doctor_patients.py and ai/chart_summary.py too).
    Nothing in modules/turnos imports modules/intake today. The rule below
    still deliberately doesn't police module-to-module imports: the only
    remaining direction between the two is intake→turnos, a declared,
    allowed edge (modules/intake/module.json's `requires: ["turnos",
    "chat"]`)."""

    PKG_DIR = os.path.join(ROOT, "app", "modules", "turnos")

    def test_package_has_the_batch_d_files(self):
        files = [f for f in os.listdir(self.PKG_DIR) if f.endswith(".py")]
        self.assertGreaterEqual(
            len(files), 23,
            "modules/turnos/ has fewer .py files than batch D's claim (%d < 23)"
            % len(files))

    def test_no_turnos_file_imports_a_composition_root_or_foreign_screen(self):
        offenders = []
        for fname in sorted(os.listdir(self.PKG_DIR)):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(self.PKG_DIR, fname)
            with open(path, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=path)
            for name in _imported_names(tree):
                top = name.split(".")[0]
                if top in {"server", "main", "routers", "services"}:
                    offenders.append("modules/turnos/%s imports %r" % (fname, name))
                elif top.startswith("admin_"):
                    offenders.append("modules/turnos/%s imports %r" % (fname, name))
        self.assertEqual(
            offenders, [],
            "a module imported a composition root (server/app) or a "
            "foreign admin_* screen — dependency direction violation:\n  "
            + "\n  ".join(offenders))


class TestChatModuleDependencyDirection(unittest.TestCase):
    """Tier 2 batch F (2026-08-07): `modules/chat/`'s own half of the same
    rule `TestTurnosModuleDependencyDirection` established for
    modules/turnos/ — a MODULE must never import a composition root
    (server.py/app.py) or a foreign admin_* screen file. Reaching into
    `modules.turnos` (chat_tools.py's real `requires: ["turnos"]`
    dependency, `from app.modules.turnos import booking_requests`) is NOT a
    violation — that's the declared, allowed module-to-module direction
    (modules/chat/module.json's own `requires`), same direction as
    intake's declared `requires` edges on turnos and chat."""

    PKG_DIR = os.path.join(ROOT, "app", "modules", "chat")

    def test_package_has_the_batch_f_files(self):
        files = [f for f in os.listdir(self.PKG_DIR) if f.endswith(".py")]
        self.assertGreaterEqual(
            len(files), 10,
            "modules/chat/ has fewer .py files than batch F's claim (%d < 10)"
            % len(files))

    def test_no_chat_file_imports_a_composition_root_or_foreign_screen(self):
        offenders = []
        for fname in sorted(os.listdir(self.PKG_DIR)):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(self.PKG_DIR, fname)
            with open(path, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=path)
            for name in _imported_names(tree):
                top = name.split(".")[0]
                if top in {"server", "main", "routers", "services"}:
                    offenders.append("modules/chat/%s imports %r" % (fname, name))
                elif top.startswith("admin_"):
                    offenders.append("modules/chat/%s imports %r" % (fname, name))
        self.assertEqual(
            offenders, [],
            "a module imported a composition root (server/app) or a "
            "foreign admin_* screen — dependency direction violation:\n  "
            + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()
