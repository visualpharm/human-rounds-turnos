"""test_route_order.py — golden-order proof (Codex monitor review, P0,
2026-08-05): routing.py's effective GET/POST/PUT/DELETE registries must
reproduce the EXACT pre-Tier-2 literal order, name for name, not just
"platform order preserved + app routes appended after". The Tier 2
framework commit (e272ac9) changed this without proving it — the golden
lists below started as the literal registries from
`git show 347a9f7:routing.py` (the commit immediately BEFORE the
module-contract framework landed), transcribed verbatim except for the one
route rename the phase 6b connector extraction made (`puco` ->
`connectors.puco.puco`, same module, new dotted path —
`importlib.import_module()` resolves either shape identically, see
docs/architecture/turnos-manifest.md, "Routes").

Updated 2026-08-05 merging 11 sibling commits from origin/main (merge-base
347a9f7, the same commit this golden list already used — see
`git show origin/main:routing.py` for the reference the merge had to
reproduce): `avatar_sprite` (the doctor-photo atlas, b70f16c) is a NEW
platform route the siblings added to the pre-Tier-2 GET list only, between
`doctor_photos` and `doctor_appointments` — classified turnos-owned (same
doctor-photo-directory cluster as `doctor_photos`/`doctor_appointments`,
not platform-core) in modules/turnos/module.json with
`"after": "doctor_photos"`, and added here at that exact GET position so
the golden list still matches the sibling-intended dispatch order.

Updated 2026-08-06 (Tier 4 slice 6, static fingerprinting):
`static_fingerprint` is a NEW platform-core route (GET only — it only ever
serves `/static/<hashed>.js|.css` bytes), added to `routing.GET_PLATFORM`
right after `legacy_urls` — a pure URL-shape concern, same rationale as
`legacy_urls` itself running first, unrelated to i18n/auth context. Not
owned by any module manifest (applies site-wide, admin and patient pages
alike), so it's a literal in routing.py, not spliced.

Updated 2026-08-06 (Tier 4 slice 7, `/health` + `/ready`): `health` is a NEW
platform-core route (GET only), added FIRST in `routing.GET_PLATFORM` —
ahead of even `setup` — because `setup.handle()` 303-redirects every GET to
`/setup` while the install is fresh, which would otherwise swallow
`/health`/`/ready` at exactly the moment a deploy healthcheck matters most
(a brand-new container's first boot, before the wizard has run). No other
module claims these two paths, so the insertion changes nothing for any
existing route.

Updated 2026-08-06 (Tier 2 batch C, docs/roadmap/tier2-physical-modularization-
plan.md): `admin_password_reset`, `passkeys` and `panel_login` moved into
`auth/` (`from app.auth import password_reset as admin_password_reset`, etc.).
`dispatch()` gates on `module.__name__`, which for a package submodule is
its dotted path — same rename shape `puco` already went through above, now
renamed here to `auth.password_reset`/`auth.passkeys`/`auth.panel_login`.

Updated 2026-08-06 (Tier 2 batch D): `booking_requests`, `preparation`,
`calendar_feed`, `doctor_appointments`, `appointment_offer`,
`appointment_confirm`, `searches` and `slot_holds` physically moved into
`modules/turnos/` — same rename-not-reorder shape batch C established,
now `modules.turnos.<name>` for these eight route module names only. The
other files still at root (admin_content, ai_costs, reports, etc.) are
later batches' concern and keep their bare names here.

Updated 2026-08-07 (Tier 2 batch E): `ai_costs`, `ai_health`,
`chart_summary`, `lab_results` and `prompts` physically moved into `ai/`
— same rename-not-reorder shape batch C/D established, now
`ai.<name>` for these five route module names only (their `_router`
twins aren't ASGI-mounted yet, so they don't appear in these
legacy-dispatch golden lists at all). `admin_content`, `reports`,
`doctor_photos`, etc. are later batches' concern and keep their bare
names here.

Updated 2026-08-07 (Tier 2 batch F): `chat_api`, `whatsapp_chat`, `voice`
and `mcp_api` physically moved into `modules/chat/` — same rename-not-
reorder shape batch C/D/E established, now `modules.chat.<name>` for
these four route module names only (their `_router` twins aren't in
these legacy-dispatch golden lists at all, same as batch E's).

Updated 2026-08-07 (Tier 2 batch G): `accounts`, `auth_renaper`,
`account_photo` and `ocr_dni` physically moved into `cuentas/` (a domain
package, not a module — same footing as `auth/`/`ai/`/`notifications/`)
— same rename-not-reorder shape established, now `cuentas.<name>` for
these four route module names only (their `_router` twins, where they
exist, aren't in these legacy-dispatch golden lists, same as batch E/F's).
`health_coverage` isn't in either golden list — it was never itself a
dispatched route, only a helper `accounts.py`/`admin_patients.py` import.

Updated 2026-08-07 (Tier 2 batch H): `patient_web/site_*.py`, `doctor_photos`,
`avatar_sprite`, `sitemap`, `branding` and `legal` physically moved into
`patient_web/` (a domain package, not a module — same footing as
`auth/`/`ai/`/`cuentas/`) — same rename-not-reorder shape established, now
`patient_web.<name>` for these six route module names wherever they appear
in the four golden lists (`patient_web/site_*.py`/`doctor_photos` GET+POST+DELETE
positions, `avatar_sprite`/`sitemap` GET-only, `branding` GET+POST+DELETE,
`legal` GET+PUT+DELETE); `catalog`, `knowledge`, `seo` and their `_router`
twins aren't in these legacy-dispatch golden lists at all (`catalog`/
`knowledge`/`seo` were never themselves dispatched routes, only imported
helpers; the `_router` twins aren't ASGI-mounted directly by `routing.py`,
same as every other batch's `_router` siblings).

Updated 2026-08-07 (Tier 2 batch J): `setup` and `demo_installation`
physically moved into `ops/` (a domain package, not a module — same
footing as `auth/`/`ai/`/`cuentas/`/`patient_web/`/`admin_panel/`) — same
rename-not-reorder shape established, now `ops.setup`/`ops.demo_installation`
wherever they appear in the four golden lists.

Updated 2026-08-19 (Tier 4 availability directory): the read-only
`/api/specialties` snapshot projection moved out of `server.py` into
`patient_web.availability_directory`. It is inserted immediately after
`patient_web.patient_site`, before booking requests, so legacy URL rewrites
still run first and no existing claimant loses precedence.

Updated 2026-08-07 (Tier 2 batch K — LAST batch): `health`, `legacy_urls`,
`static_fingerprint`, `i18n`, `site_identity` and `app_modules` physically
moved into `core/` (platform-core, not a module — same footing as every
other domain package above) — same rename-not-reorder shape established,
now `core.<name>` wherever they appear in the four golden lists. The other
24 files batch K also moved (`db`, `config`, `deps`, `routing` itself,
etc.) were never themselves dispatched routes, so they don't appear here.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_route_order.py"

RETIRED (native-migration final wave, docs/roadmap/
native-migration-campaign-20260826.md): `core/routing.py` — the GET/POST/
PUT/DELETE dark registry and `dispatch()` — is DELETED along with the rest
of the executable legacy topology. `TestGoldenRouteOrder` pinned that
registry's exact literal order; there is no registry left to import
`routing.GET`/`POST`/`PUT`/`DELETE` from, and the golden-order CONCEPT
itself doesn't transfer — FastAPI/Starlette match a real ASGI route table
by exact path/method, not by walking an ordered list of `handle()`
claimants, so "index N precedes index N+1" isn't a meaningful question
for the native composition. The native ordering contract that replaces it
— which router MOUNTS before which, and that the full route inventory is
complete/stable — already lives in `tests/test_app_composition.py`'s route
census (see also `test_asgi_main_dark_mode_retirement.py`'s
`TestLegacyEntrypointRetired`, which pins `core/routing.py`'s deletion at
the file level). `TestMergeRoutesSpliceContract` below stays: it exercises
`core.app_modules.merge_routes()`/`_base_app_routes()` in isolation
(mocked, no real manifest), and that module imports cleanly WITHOUT
`core.routing` (confirmed: `core/app_modules.py`'s own import list never
names it) — nothing calls `merge_routes()` in production anymore (the
router mounting order for app-modules is `merge_app_routers()`'s job now),
but the splice ALGORITHM itself is still real source, still pure, still
worth a contract test.
"""
import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # importar sin exigir Postgres
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestMergeRoutesSpliceContract(unittest.TestCase):
    """merge_routes() itself, isolated from the real manifests — proves the
    splice ALGORITHM (anchor resolution, same-pass chaining, tail default,
    unresolved-anchor degrades to tail) independent of turnos/intake ever
    changing their own route lists."""

    def setUp(self):
        from app.core import app_modules
        self.app_modules = app_modules

    def _mod(self, name):
        import types
        m = types.ModuleType(name)
        return m

    def test_route_with_no_after_appends_at_tail(self):
        from app.core import app_modules
        platform = [self._mod("plat_a"), self._mod("plat_b")]
        new_mod = self._mod("brand_new_module_route")
        import unittest.mock as mock
        with mock.patch.object(app_modules, "_base_app_routes",
                                return_value=[(new_mod, None)]):
            out = app_modules.merge_routes("GET", platform)
        self.assertEqual([m.__name__ for m in out],
                          ["plat_a", "plat_b", "brand_new_module_route"])

    def test_route_anchored_after_a_platform_module_is_spliced_in_place(self):
        from app.core import app_modules
        import unittest.mock as mock
        platform = [self._mod("plat_a"), self._mod("plat_b"), self._mod("plat_c")]
        spliced = self._mod("spliced_route")
        with mock.patch.object(app_modules, "_base_app_routes",
                                return_value=[(spliced, "plat_a")]):
            out = app_modules.merge_routes("GET", platform)
        self.assertEqual([m.__name__ for m in out],
                          ["plat_a", "spliced_route", "plat_b", "plat_c"])

    def test_same_module_chain_resolves_in_declared_order(self):
        # a -> after plat_a, b -> after a, c -> after b: declared in that
        # order, must resolve in ONE pass (same-pass fresh-scan behavior
        # the real turnos manifest's chains depend on).
        from app.core import app_modules
        import unittest.mock as mock
        platform = [self._mod("plat_a")]
        a, b, c = self._mod("a"), self._mod("b"), self._mod("c")
        with mock.patch.object(app_modules, "_base_app_routes",
                                return_value=[(a, "plat_a"), (b, "a"), (c, "b")]):
            out = app_modules.merge_routes("GET", platform)
        self.assertEqual([m.__name__ for m in out], ["plat_a", "a", "b", "c"])

    def test_unresolved_anchor_degrades_to_tail_not_a_crash(self):
        from app.core import app_modules
        import unittest.mock as mock
        platform = [self._mod("plat_a")]
        orphan = self._mod("orphan_route")
        with mock.patch.object(app_modules, "_base_app_routes",
                                return_value=[(orphan, "this_anchor_does_not_exist")]):
            out = app_modules.merge_routes("GET", platform)
        self.assertEqual([m.__name__ for m in out], ["plat_a", "orphan_route"])


if __name__ == "__main__":
    unittest.main()
