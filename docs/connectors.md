# Connectors

Phase 6b of the open-source rework (`~/.claude/plans/large-rewrok-before-open-robust-chipmunk.md`), 2026-07-30. This is the seam another country's deployment writes a PR against to plug in ITS national ID system, insurance registry, professional-license registry, EMR, third-party slot source, or messaging transport instead of Argentina's. Read `app/connectors/__init__.py` first — every rule below is enforced or documented there, this file is the walkthrough.

## The eight capabilities and Argentina's connectors

| Capability | What it does | Argentina connector(s) | Active by default |
|---|---|---|---|
| `identity_verification` | Verify a patient's identity against a national ID system | `renaper` (RENAPER/Mi Argentina OIDC + local `stub`) | **yes (default)** — the synthetic-identity login only (`app/cuentas/accounts.py:_login_test_identity`, behind the LAN/dev gate); production Mi Argentina OIDC stays `auth_renaper.py`/`TURNOS_AUTH_METHODS`, outside the registry. `off` via `conn disable identity_verification` switches that one endpoint off (404) |
| `insurance_lookup` | Look up health coverage by national ID | `puco` (SISA PUCO/WS131, with its own fake→WS131→public-fallback chain) | **yes (default)** — the registration POST's server-side coverage attach reads `adapter_for("insurance_lookup").cached(dni)`; `off` → every insurer the form sends is `declarada`, nothing breaks |
| `license_lookup` | Verify a health professional's license | `refeps` (Buscador Nacional de Profesionales de la Salud) | **yes (default)** — the admin's `GET /api/admin/refeps` check; `off` → the stale-cache-or-502 branch, exactly "REFEPS unreachable" |
| `emr_sync` | Push booked appointments into the clinic's EMR | `hsi` (temporary legacy adapter), `emr_bridge` (Enterprise HTTP boundary) | **no default** — select `hsi` or `emr_bridge` explicitly; unconfigured Core performs no vendor import or EMR traffic |
| `slot_source` | Live third-party appointment availability | `msd` (Mi Salud Digital, `"default": true`), `webturno` (operator-selectable, not the default) | **yes (default: msd)** — the wizard's "Cuándo" grid and the listing's next-appointment cell; `off` → `{}` and both fall back to the real schedule |
| `otp_transport` | Deliver an OTP code or free text to a phone | `waha` (WhatsApp via a WAHA instance — loopback dev-only), `meta` (WhatsApp Cloud API, the only production transport: free text, Utility templates, and the OTP as the Authentication template named by `META_WA_OTP_TEMPLATE`; a Meta rate limit pauses all of it) | no default (two unflagged transports): the real selection stays `otp_send.py`'s own `wa_provider()`/`OTP_PROVIDER` — see "What's wired live" below |
| `clinical_history_lookup` | Look up a patient's prior clinical history in a legacy/external record system, by national ID, for the chart summary (`chart_summary.py`) | `alepo` (digitized PDFs ~2020-2024), `marqui` (digitized PDFs ~1996-2016), and deployment-private `local_history` Postgres rows imported from normalized JSON, plus `patient_uploads` (lab results the patient uploaded from their own account, every entry flagged `tercero: true` so it reads as patient-supplied and unverified) — Alepo/Marqui remain real-mode stubs until reference-deployment counterpart identifies an access path; their fake modes serve synthetic demo histories | a **fan-out, never a single default**: every discovered archive answers, in the stable order `local_history` first then sorted by id (`chart_summary._history_connector_ids()`); `local_history` is installation-owned; Alepo/Marqui fake mode is what the demo site runs |
| `email_transport` | Deliver a transactional email (patient notices, staff invitation/reset, disk alert) | `mailgun` (Mailgun HTTP API, `"default": true`; implementation stays in `app/notifications/mailgun.py`, wrapped in place), `mailjet` (Mailjet Send API v3.1, `app/connectors/mailjet/mailjet.py`) | **yes (default: mailgun)** — every sender calls `notifications.mail.send()`; `CONNECTOR_EMAIL_TRANSPORT=mailjet` switches; `off`, a typo or missing keys → `False`, logged, nothing else changes |

"Active by default: yes (default)" is the compatibility pin of the registry rewire (2026-08-20, `tests/test_connectors.py:TestInTreeManifests`) for the open Core helpers: renaper, puco, refeps and msd. Institutional EMR connectivity is deliberately excluded: an installation must explicitly select `hsi` or `emr_bridge`. The registry is now the dispatch point, not a side catalog.

## What's wired live vs. what's the target shape (read this before assuming a rewire)

Phase 6b (2026-07-30) built the SEAM; the registry rewire (2026-08-20) put five call sites on it. Each consumer now calls `connectors.adapter_for(<capability>)` and gets the capability's ABC instance (or `None`), and keeps its exact pre-rewire degrade path:

| Call site | Through the registry | Degrade when nothing is routed |
|---|---|---|
| `app/cuentas/accounts.py:_login_test_identity` (synthetic-identity login, LAN/dev-gated) | `adapter_for("identity_verification").authenticate(dni, provider="stub", existing_user=…)` | 404 `identity_verification_no_disponible`; `ProviderUnavailable` → 503; `InvalidIdentity` or `IdentityNotFound` → client-safe 400 |
| `app/cuentas/accounts.py:_norm_coverage` (registration coverage attach) | `adapter_for("insurance_lookup").cached(dni)` | `[]` → every insurer `declarada` |
| `app/modules/turnos/booking_queue.py:_admin_refeps_core` | `adapter_for("license_lookup").search_by_license(matricula)` | stale cache or 502 (the REFEPS-unreachable branch) |
| `patient_web/patient_site.py:_slot_source_info` (wizard grid + listing cell) | `adapter_for("slot_source").slots_for_service(s)` | `{}`; `ProviderUnavailable` → `{}` |
| `app/ai/chart_summary.py:_historia_clinica` (fan-out) | `connectors._instantiate_adapter(id).lookup_by_dni(dni)` for every discovered `clinical_history_lookup` id | that archive reads `no_disponible` (silent for `ProviderUnavailable`, logged otherwise) |
| `app/notifications/mail.py:send` (every email sender, 2026-10-20) + Settings «Email» «Probar» + «Sistema» email cluster | `connectors.resolve("email_transport")` → `_instantiate_adapter(id)` → `send()` / `missing_config()` / `probe()` | `False` + a log line; the probe and the cluster say which keys are missing |
| `app/modules/turnos/hsi_journal.py:tick` + `app/modules/turnos/booking_emr.py` «HSI» admin cores (2026-08-31; in `booking_requests.py` until the cut of 2026-11-06) | `adapter_for("emr_sync")` then `run_conflict_resolution` / `conflict_panel` / `apply_conflict_resolution` — the whole conflict resolver lives on the connector side (`app/connectors/hsi/hsi_resolver.py`), Core holds no vendor import | no adapter → pass `{}`, panel `None`, apply 409 `resolucion_no_disponible`; connector defaults are the same degradation |

The four states of `connectors.resolve(capability)` → `(connector_id | None, source)`:

- **explicit** — the stored `CONNECTOR_<CAP>` names a connector that implements the capability.
- **off** — the stored value is the reserved sentinel `off` (what `manage.py conn disable` writes): nothing routed, the default is NOT consulted. `off` is reserved everywhere — never discovered as a connector, never loadable, never enable-able.
- **default** — nothing stored: the one manifest flagged `"default": true` for the capability, else the sole implementer. Several flagged, or several unflagged implementers → no default (`(None, None)`).
- **invalid** — anything else stored (unknown id, wrong capability, broken manifest, a value that is not a Python identifier): **fail closed** — `(None, "invalid")`, never the default, so an operator typo shows up as `invalid:<value>` in `manage.py conn list/info` instead of silently routing to the installation default.

One capability stays a documented exception:

- `emr_sync`: the durable journal and historical one-shot path call the generic `EmrSyncConnector` boundary. `hsi` is a temporary legacy wire adapter; `emr_bridge` uses the versioned HTTP contract in [emr-bridge-protocol.md](architecture/emr-bridge-protocol.md). The bridge is not a Python import and cannot write Core Postgres. Its v1 adapter deliberately does not expose HSI's vendor-specific automatic routing operations, so those are fail-closed while it is selected.
- `otp_transport` has TWO real transports whose selection was already solved before this framework existed: `otp_send.wa_provider()` picks the transport (Meta by explicit pin or token; WAHA only with an explicit `TURNOS_WA_PROVIDER=waha` opt-in plus a loopback dev URL; otherwise fail-closed no-op — the 2026-08-14 transport contract). Rewiring that through `CONNECTOR_OTP_TRANSPORT` would mean picking ONE active transport where today's code can pick per-call — out of scope, and `app/connectors/waha/adapter.py`'s docstring explains the full reasoning for why `otp_send.py` itself was deliberately NOT moved.

Scope honesty: "identity_verification through the registry" means the synthetic-identity login used behind the LAN/dev gate, not production login. Production Mi Argentina OIDC (`app/cuentas/auth_renaper.py`, `TURNOS_AUTH_METHODS`) never touches the connector; the RENAPER connector's `"real"` provider raises `ProviderUnavailable`. `conn disable identity_verification` switches off the synthetic-identity login endpoint only; prod is unaffected both because the gate keeps that endpoint off the public path and because the default tier resolves `renaper` anyway. Likewise puco's `POST /api/puco` (`puco_router.py` → `puco.handle()`) is the connector's OWN transport endpoint and keeps calling the module directly — a connector asking the registry about itself would be circular — and `patient_web/site_*.py`'s MSD-only fuzzy doctor matching (`msd_data.proximo_own_anywhere`/`proximo_for_medico`, no ABC analogue) stays a direct call, a harmless no-op for a non-MSD install.

`TURNOS_CONFIG_LOCK=1` (`app/core/config.py`) makes `conn enable/disable` **read-ignored, not refused**: the DB row is written, `config.get()` ignores DB values while locked (env/.env `CONNECTOR_<CAP>=…` wins meanwhile, and the `off` sentinel works there too), and the CLI prints `saved, but TURNOS_CONFIG_LOCK=1: env/.env values take precedence until the lock is removed` after the write and `(TURNOS_CONFIG_LOCK=1: DB values are ignored)` atop `conn list/info`.

## Compat shims (retired 2026-08-07) — every pre-existing `import X` kept working, for a while

Six of the eight connectors above were PHYSICAL MOVES of a pre-existing top-level module (`renaper.py`, `puco.py`, `refeps.py`, `hsi_client.py`+`hsi_sync.py`+`hsi_resolver.py`+`hsi_resync.py`, `msd_data.py`, `webturno.py`) into `app/connectors/<id>/`, done in Phase 6b (2026-07-30). (The HSI journal and its fallback later left the connector as Core-owned code — `app/modules/turnos/hsi_journal.py`, `hsi_journal_queue.py` and `hsi_fallback.py`, 2026-08-31.) For a while after that move, the repo-root file at the old path stayed as a **compat shim** — a few lines that resolved the real, moved module and then did:

```python
import sys
from connectors.renaper import renaper as _connector
sys.modules[__name__] = _connector
```

That replaced the shim's own entry in `sys.modules` with the real module object BEFORE Python's import machinery finished loading it (CPython re-reads `sys.modules[spec.name]` after `exec_module` returns, so whatever the module body last assigned there won). The result: `renaper` (however anything imported it — `import renaper`, `from renaper import X`, a LAZY `import renaper` inside a function body) and `connectors.renaper.renaper` were **the literal same module object**, not a copy and not a re-export list.

This mattered more than it sounds: the test suite monkeypatches these modules in two different ways, and both needed the real object, not a copy —

- `unittest.mock.patch("refeps._buscar_matricula_once", ...)` resolves the string `"refeps"` through `__import__`, which returns whatever is in `sys.modules['refeps']` — with the shim trick, that was the real module, so the patched attribute was the SAME one `buscar_matricula()`'s own `__globals__` looks up. (A naive shim — `from connectors.refeps.refeps import *` — would NOT have worked here: the patch would land on a copy, and the real function would never see it.)
- Direct attribute reassignment (`hsi_client.push_turno = lambda *a, **kw: {...}`, all over `tests/test_booking_requests.py`) worked for the same reason — one shared object, mutated in place, visible from anywhere that imports it.

**The nine root shims were retired in Tier 2 batch L (2026-08-07, `docs/roadmap/tier2-physical-modularization-plan.md`)**, after every domain package had already moved its own consumers off the root names. `renaper.py`, `puco.py`, `refeps.py`, `hsi_client.py`, `hsi_sync.py`, `hsi_resolver.py`, `hsi_resync.py`, `msd_data.py`, `webturno.py` no longer exist at the repo root — every importer (top-level, lazy/inline, and inside `app/connectors/hsi/`'s own cross-file lazy imports) was rewritten to `from connectors.<id> import <mod>`, and every `unittest.mock.patch("<old_name>...")` STRING target (only `refeps._buscar_matricula_once`/`refeps.time.sleep` needed this — the rest were `patch.object()` on the already-rebound local name, unaffected) was rewritten to the dotted `connectors.<id>.<mod>` path in the same commit. `webturno.py`'s CLI stays invokable as `python3 -m connectors.webturno.webturno login|clinics|doctors|slots|appointments|db|passwords` — the moved file's own `if __name__ == "__main__":` block fires correctly when run with `-m`, no re-dispatch shim needed for that path.

The retirement is verified explicitly in `tests/test_connector_shims.py`, `tests/test_hsi_connector.py`, `tests/test_slot_source_connectors.py` — each now asserts BOTH that the root name raises `ImportError` (the shim is really gone, not just unused) and that a monkeypatch made through the direct `connectors.<id>.<mod>` import is still visible to every lazy importer, the same guarantee the shim used to provide.

**Two connectors are wrapped in place instead of moved**: `waha` and `meta` (`otp_transport`) both wrap `otp_send.py`, which stays exactly where it is — see `app/connectors/waha/adapter.py`'s docstring for the three reasons (it already implements the seam this framework formalizes; `tests/test_notifications.py` monkeypatches it by direct attribute assignment that the move would have had to preserve anyway; OTP-code vs. free-text selection are two different mechanisms coexisting in one file that a mechanical split would have forced a design decision about). `tests/test_otp_transport_connectors.py` confirms `otp_send.py` carries zero diff from this work.

**`webturno` is selectable but not the default.** Its retired public clinic directory and old server calendar surface stay retired. For a service that explicitly carries WebTurno's `clinic_token` and `idme`, selecting it through `conn enable webturno` makes the shared adapter fetch and normalize its slots for the existing patient listing and appointment grid. Services without those identifiers degrade to the ordinary local schedule. Its operator CLI remains available as `python3 -m connectors.webturno.webturno`.

**No `colmed9` connector** — the provincial Colegio de Médicos lookup (has photos REFEPS lacks) lives entirely in the `doctor-lookup` operator skill, external tooling queried by a human/agent during cartilla curation, never called at runtime by the app. `app/connectors/refeps/connector.json` documents this explicitly so a future contributor doesn't go looking for code that was never there.

## Path fixes required by the move

Every moved file that computed a repo-root-relative path from its OWN `__file__` needed a one-line fix — it now lives two directories deeper (`app/connectors/<id>/file.py` vs. the old `file.py` at the repo root):

```python
import connectors as _connectors_pkg
ROOT = os.path.dirname(_connectors_pkg.ROOT)  # repo root, not this file's own dir
```

Fixed: `renaper.py` (`DEFAULT_STORE_PATH` → `data/test-identities.json`), `puco.py` (`ENV_PATH`, dead code but corrected for honesty), `msd_data.py` (`_HERE` → `data/msd_turnos.json`, the LaunchAgent-refreshed dump), `webturno.py` (`.env` lookup and the `data/availability.json` output path — `passwords.json` was deliberately left resolving next to the file itself, since that operator setting file has always lived beside the script, never at the repo root). `hsi_client.py`/`hsi_sync.py`/`hsi_resolver.py`/`hsi_resync.py` and `refeps.py` needed no fix — audited first, confirmed no `__file__`-relative paths.

`webturno.py` also needed one thing no other shim did: it's runnable as a script (`python webturno.py login|clinics|doctors|slots|appointments|db|passwords`). While the shim existed, the `sys.modules` trick didn't help there — a directly-executed script's own `__name__` is always `"__main__"` regardless of what the trick does to `sys.modules`, so the moved file's `if __name__ == "__main__":` block never fired when invoked via the shim; the shim re-dispatched explicitly (`sys.exit(_connector.main(sys.argv[1:]))`). Now that the shim is retired (see above), the CLI is invoked directly as a module: `python3 -m connectors.webturno.webturno login|clinics|doctors|slots|appointments|db|passwords` — no re-dispatch needed, `-m` sets `app/connectors/webturno/webturno.py`'s own `__name__` to `"__main__"` and its own bottom `if __name__ == "__main__":` block fires normally.

## The degradation contract

**No connector configured for a capability → that capability degrades exactly like it does today. It never crashes.** This was true before Phase 6b (every one of these six behaviors already shipped); the framework's job is to make that contract explicit and machine-checkable rather than tribal knowledge. Concretely, per capability:

- `identity_verification`: RENAPER unconfigured → `TURNOS_AUTH_METHODS`'s anti-lockout rule keeps password login on; the site never ends up with zero usable login methods.
- `insurance_lookup`: no SISA credentials and the public fallback off → `puco.disponible()` returns False, the registration form's coverage assist simply doesn't populate, the patient types their obra social by hand.
- `license_lookup`: no REFEPS reachable → the admin's order-verification "check REFEPS" assist doesn't offer a result; staff verify manually, same as always.
- `emr_sync`: `TURNOS_SKIP_HSI=1` → `push_appointment`/`push_turno` return `{"skipped": True}`, the booking still completes and persists locally (CLAUDE.md: "Approving always persists locally even if HSI fails" — do not relitigate).
- `slot_source`: missing/stale dump or no matching agenda → `{}`, the wizard falls back to `solicitud_con_orden` cleanly.
- `otp_transport`: no WAHA/Meta configured → `send()`/`enviar_otp()`/`enviar_texto()` return `False`, logged, and the calling flow (registration, password reset, the chat bot) continues without ever blocking on it.

Every capability's ABC in `app/connectors/__init__.py` documents its own version of this in its docstring — read those before writing a new connector, they're the actual spec.

## Manifest schema (`connector.json`)

```json
{
  "id": "renaper",
  "country": "AR",
  "capability": "identity_verification",
  "config_keys": ["TURNOS_FAKE_RENAPER", "TURNOS_RENAPER_CLIENT_ID", "TURNOS_RENAPER_CLIENT_SECRET"],
  "description": "..."
}
```

- `id` — must equal the directory name under `app/connectors/`; `connectors.load_manifest()` rejects a mismatch (a broken manifest degrades to "not found," same spirit as the rest of the framework).
- `country` — ISO 3166-1 alpha-2. A connector isn't required to be country-specific (a global fallback provider is conceivable), but every real one so far is.
- `capability` — one of the eight constants in `app/connectors/__init__.py` (`connectors.CAPABILITIES`).
- `config_keys` — the env/config keys a caller needs before this connector is usable. `connectors.config_key_status(id)`/`is_configured(id)` read this list; `manage.py conn list/info` display it (presence only, values are never printed — same masking contract as the Settings admin panel).
- `description` — free text; shown by `manage.py conn info`.
- `default` — optional JSON **boolean**. `true` marks this connector as the installation default for its capability when nothing is stored (`connectors.resolve()`'s default tier): renaper, puco, refeps and msd carry it. At most one per capability — two flagged means no default. Any non-boolean value (`"true"` the string, a number, `null`) makes the whole manifest invalid (`load_manifest()` returns `None`, the connector is not discovered) — fail closed, never half-honored. A capability with a single unflagged implementer resolves to it without the flag.

Every connector directory is a real Python subpackage (`__init__.py`) with an `adapter.py` that defines exactly ONE concrete subclass of the capability's ABC and delegates to its implementation module at call time (`from . import x as _impl`, then `_impl.fn(...)` inside the method — never bound at import, so tests that monkeypatch `connectors.<id>.<impl>` keep working). `connectors._instantiate_adapter(id)` imports `connectors.<id>.adapter` through the real package path and instantiates that class with no arguments; the id therefore has to be a Python identifier (the reserved name `off` is refused). Adapters forward `**kwargs` to the implementation (`renaper/adapter.py` is the cautionary example: `existing_user` must reach `renaper.authenticate()`).

## Writing a connector for a new country: the India walkthrough

Say a deployment for an Indian hospital wants to replace `identity_verification` (RENAPER doesn't exist there) and `insurance_lookup` (PUCO doesn't either) with local equivalents: Aadhaar for identity, a state health insurance scheme for coverage. `license_lookup`, `emr_sync`, `slot_source`, `otp_transport` might stay unconfigured (degrading per the contract above) until someone builds India-specific ones, or reuse existing connectors if they happen to fit (unlikely for EMR/insurance, more plausible for `otp_transport` — WhatsApp works in India too, `waha`/`meta` might Just Work).

1. **Read the capability's ABC first.** `connectors.IdentityVerificationConnector` in `app/connectors/__init__.py` — one abstract method, `authenticate(self, dni, **kwargs)`, and a docstring spelling out what "not configured" and "not found" mean for this capability. Aadhaar's identifier isn't a DNI, but the method's job is the same: take an identity number, return a record or raise `IdentityNotFound`/`ProviderUnavailable`.
2. **Create `connectors/aadhaar/`** with `connector.json`:
   ```json
   {
     "id": "aadhaar",
     "country": "IN",
     "capability": "identity_verification",
     "config_keys": ["AADHAAR_CLIENT_ID", "AADHAAR_CLIENT_SECRET", "AADHAAR_FAKE"],
     "description": "Aadhaar-based identity verification (UIDAI eKYC)."
   }
   ```
3. **Write the implementation.** `connectors/aadhaar/aadhaar.py` — the actual HTTP client, following `renaper.py`'s shape as the reference template: a fail-closed real provider, a local fake/stub mode for tests and demos (`AADHAAR_FAKE=1`, same spirit as `TURNOS_FAKE_RENAPER`), raising the framework's shared `connectors.ProviderUnavailable`/`connectors.IdentityNotFound` — not a parallel exception type, so callers that already handle those two keep working unchanged for the new country.
4. **Write `adapter.py`** implementing `IdentityVerificationConnector`:
   ```python
   from connectors import IdentityVerificationConnector
   from . import aadhaar as _impl

   class AadhaarConnector(IdentityVerificationConnector):
       def authenticate(self, dni, **kwargs):
           return _impl.authenticate(dni, **kwargs)   # forward every keyword the caller sends
   ```
   One concrete subclass per `adapter.py`, delegating at call time; `connectors._instantiate_adapter("aadhaar")` finds it by shape. Add `"default": true` to the manifest only if this deployment's build of the repo should resolve it with nothing configured (otherwise `conn enable aadhaar` is the explicit choice).
5. **A second connector, same pattern, for `insurance_lookup`**: `connectors/ayushman/` (or whatever the real scheme's name is), implementing `InsuranceLookupConnector`'s two abstract methods (`available()`, `lookup(national_id)`) and — if it keeps a recent-lookup cache the registration POST can attach from — the non-abstract `cached(national_id)` (default: `[]`), country `"IN"`.
6. **Enable them**: `python3 manage.py conn enable aadhaar --set AADHAAR_CLIENT_ID=... --set AADHAAR_CLIENT_SECRET=...`. `manage.py conn list` now shows `aadhaar` as `active (explicit)` for `identity_verification` (and `renaper` as `no`). `conn disable identity_verification` switches the capability off (`off` on every row); an id typo reads `invalid:<value>` on every row and routes nothing.
7. **The rewired call sites dispatch automatically** — the synthetic-identity login, the registration coverage attach, the admin license check, the wizard's slot source and the chart summary's history fan-out resolve through `connectors.adapter_for()`/`_instantiate_adapter()` with no glue (see "What's wired live"). Caveats, stated exactly: production login is NOT one of them (Mi Argentina OIDC stays `auth_renaper.py`; an Aadhaar deployment that wants production Aadhaar login still writes its own `auth_renaper.py`-equivalent route), `emr_sync`/`otp_transport` keep their own selection, and a brand-new capability (not one of the eight) still needs its ABC, registry constant and consumer glue.

## PR checklist for a new connector

- [ ] `app/connectors/<id>/connector.json` — `id` matches the directory name AND is a Python identifier (the package has to be importable; `off` is reserved), `capability` is one of the eight real ones, `config_keys` lists every env var the connector reads, `default` (if present) is a JSON boolean and at most one connector per capability carries `true`.
- [ ] Implementation raises `connectors.ProviderUnavailable`/`connectors.IdentityNotFound` (not a parallel exception) where the capability's ABC calls for it.
- [ ] A fake/stub/test mode exists and is documented (an env flag, following `TURNOS_FAKE_RENAPER`/`TURNOS_FAKE_PUCO`'s naming pattern) — connectors that hit a real third-party network call MUST be testable without one.
- [ ] `adapter.py` defines exactly one concrete subclass of the capability's ABC, delegates to the implementation module at call time (never binds `_impl.fn` at import), and forwards `**kwargs` to it — `connectors._instantiate_adapter(id)` must return an instance.
- [ ] `insurance_lookup` connectors that keep a recent-lookup cache implement `InsuranceLookupConnector.cached(national_id)` (non-abstract, default `[]`) — it is what the registration POST attaches coverage from.
- [ ] Tests: registry discovery finds it (`connectors.load_manifest(id)` round-trips), `connectors._instantiate_adapter(id)` instantiates it to the ABC, the adapter's degradation case (unconfigured → the capability's documented "nothing crashes" behavior), and — if it has one — the fake mode. `tests/test_connectors.py:TestInTreeManifests` already covers every in-tree id generically.
- [ ] `docs/connectors.md`'s capability table gets a new row.
- [ ] If the connector replaces a capability Argentina's stack didn't previously make swappable (rare — the six above already cover Argentina's real integrations), the real call site that used to hardcode the Argentina module gets a note in this file's "What's wired live" section, honest about whether it was actually rewired or just registered.

## Remaining work

- `otp_transport`'s two selection mechanisms (`OTP_PROVIDER` for the OTP code, `wa_provider()`/`TURNOS_WA_PROVIDER` for free text) could unify under `CONNECTOR_OTP_TRANSPORT` — deliberately not attempted (see `app/connectors/waha/adapter.py`).
- `emr_sync` is through the registry on Core's side (approval push, durable journal, and the three resolver entry points — see "What's wired live"); what remains is moving `app/connectors/hsi/` itself into the proprietary Enterprise repository, which does not exist yet. Until then the in-repo package is a temporary compatibility implementation, not a licensing boundary or a completed Enterprise split.
- A Settings admin section for connectors (today: CLI-only, per the product owner's explicit instruction in the rework plan) — `manage.py conn list/info`'s per-row status and masked config-key display is the shape a future UI would reuse.
