# server.py retirement — slice 4 (prep): the private clinics page surface

**Status: FROZEN preparation document (rev 2, 2026-08-17).** Rev 1 was
corrected after independent review: route-proof precision (the parity
script pins same-response, not 302/Location — a native ASGI standing test
is now specified), alias/count semantics are PINNED as they behave today
instead of silently fixed, the false `--help`/smoke gates are replaced by
a hermetic writer run and explicit status+Location assertions,
`_KNOWN_CITIES`' real consumer is `_clean_dir` (not `_sentence_case`), the
surviving `/clinica` links and the docs references are inventoried instead
of claimed absent, and the execution protocol is a held, frozen, no-stash
handoff with rollback — no commit/push/deploy/live steps in this plan.

No production code and no test changes ship with this document. The diff
of the freezing session is exactly this one file (uncommitted). All line
numbers and call-site anchors below are pinned at `232e33b4` — the tip of
the accepted slice-3 chain, branch `glm/server-retirement-slice4-prep`.

**Execution is HELD.** That chain sits on local main `7298a10a`, while
`origin/main` had already advanced 61 commits to `6c34573e` at freeze time
(strictly ahead, no divergence). Every anchor in §§3-5 must be re-verified
against the reconciled tree before any line is deleted (§9).

**Series context.** The bounded server.py retirement series deletes dispatch
surface that is proven dead because another module already owns the path:

| Round | Commit | Surface removed | Pinning test |
|---|---|---|---|
| 1 | `11b1606a` | `/cobertura/<slug>` + `/obras-sociales` coverage pages | `test/test_server_cobertura_retirement.py` |
| 3 | `232e33b4` | `/medicos` + `/medico/<slug>` private doctor directory | `test/test_server_directorio_retirement.py` |

Slice 4 is the direct sibling of both: the fork's private **clinics directory**
pages that `patient_site` de-linked from the product. The phase-8 audit
quoted in [docs/architecture/data-packs.md](../architecture/data-packs.md)
already named this family dead (every read site of `clinicas.json`'s
page-fields inside `_render_clinica_page` / `_render_clinicas_index`).
That quote is historical: as of this slice its helper list is wrong in
both directions (`_canonical_clinic_slug`, `all_clinicas` and
`_load_clinicas` are LIVE keepers — §4 — while the renderers are deleted),
and §7 of this plan schedules the doc correction.

---

## 1. The boundary, in one sentence

**server.py stops owning the private clinics page surface — the `/clinicas`
index and the `/clinica/<slug>` detail route, plus the renderer functions and
helpers that exist only to feed them — because `patient_site` already owns both
paths in both transports (302 → `/`, the fork de-link), while the live clinics
DATA path (`especialidades_tree()` → `/api/specialties` and `/datos`;
`all_clinicas()` → the offline Brila writer) is owned by functions that stay.**
Alias and count semantics of the surviving data path are pinned exactly as
they behave today (§6.4) — this slice deletes dead dispatch, it does not
repair data-path quirks.

## 2. Why the removed code is dead (the proof, both transports)

`server.py:2797-2798` runs the full routing registry before any of its own
route code; server.py's blocks only execute when `routing.dispatch()` returns
False for the path. (Note for contrast: `path` is computed at `server.py:2789`,
BEFORE dispatch — that ordering is what keeps the `/api/medicos` block
reachable in some configs; see the end of this section.)

* **Legacy transport (prod, `pinamar.humanrounds.org`, `TURNOS_ASGI`
  unset).** `patient_web/patient_site.py:5111-5125` — the de-link block at
  the very top of `handle()`'s GET branch — claims `/clinicas`, `/clinicas/`
  (`:5115`) and every `/clinica/<x>` (`path.startswith("/clinica/")`,
  `:5121`) via `_redirect(handler, "/")` (`:5124`), which sends **302 +
  `Location: /`** (`patient_site.py:1669-1674`), and returns True. Its own
  comment states the intent: "Runs before server.py's inherited routes, so
  it intercepts them without touching server.py." `patient_site` is the
  first app-module route in the merged GET registry
  (`modules/turnos/module.json` routes[0]: `patient_web.patient_site`, GET
  `after: core.i18n`; the platform list it splices after is
  `core/routing.py:80-86`) — long before server.py's own code. Result:
  `/clinicas` and `/clinica/<slug>` can never reach `server.py:2809` /
  `server.py:2955` on the live site.
* **ASGI transport (demo + dev, `TURNOS_ASGI=1`).**
  `patient_web/patient_site_router.py:90-91,100` registers `/clinicas`,
  `/clinicas/` and `/clinica/{rest:path}` natively (`_endpoint =
  module_router.route_via_handle(patient_site)`, `:74` — the same
  `handle()` call `routing.dispatch()` makes, through `LegacyHandlerShim`),
  so FastAPI answers them with the same 302 and the catch-all proxy never
  forwards them to the legacy server. The Tier 4 route-parity table
  (`scripts/route_parity_check.py:473-474,483`) lists both paths — but that
  script is a ONE-SHOT legacy-vs-FastAPI diff auditor whose acceptance is
  "same response, whatever it is"; it does NOT pin 302/`Location`. That is
  why §6.3-6.4 add standing, explicit status+Location tests in BOTH
  transports.

**Module-gating honesty (same trade as round 1, stated more precisely).**
The claimant is a turnos-manifest route, not platform-core
(`core/app_modules.py:527-535`: `route_enabled("patient_web.patient_site")`
resolves the owner manifest and gates on `is_enabled(owner)`). With the
`turnos` module disabled (`manage.py mod`), the registry does not claim
`/clinicas`, and today server.py's blocks WOULD run. That configuration has
no patient site at all (no `/doctors`, no `/service/*`, no booking), so
serving the fork's private clinics directory there is a resurrect-the-fork
bug, not a feature. Round 1 accepted exactly this trade for
`/cobertura/<slug>` and `/obras-sociales`; slice 4 accepts it for the
clinics pair. Off the live site the behavior change is **404 instead of a
stale 200**, by this exact mechanism (verified at `232e33b4`):
`_KNOWN_PREFIXES` (`server.py:2976-2978`) contains `clinica` but NOT
`clinicas` — a post-removal `/clinicas` falls into the `/<esp-slug>`
matcher, where `medicos_by_esp` (`:739-742`) is an EXACT `esp_slug`
equality match and no doctor's `esp_slug` equals `clinicas`, so
`_render_especialidad_page` returns None and the path ends at static
serving (404); a post-removal `/clinica/<x>` is skipped by the `clinica`
prefix guard and also ends at static serving (404). On the live site
(turnos enabled): identical 302 → `/` before and after.

Contrast with `/api/medicos` (deliberately NOT in this slice — §7):
`legacy_urls` REWRITES that path in place and returns False
(`core/legacy_urls.py:119`), and server.py computes its local `path`
before dispatch (`server.py:2789`), so in a turnos-disabled install the
rewritten-away local still matches the old block. Its deadness mechanism
is genuinely different (rewrite + answer, not answer), and it belongs to
the doctors family slice 3 scoped around — not to the clinics boundary.

## 3. Exact removal inventory (pinned at `232e33b4`; re-verify post-reconciliation)

Two route blocks in `Handler.do_GET`, six module-level functions, one loader +
its two module constants. ≈ 878 lines including separator blanks; server.py
goes 3832 → ~2954. Pure deletion — zero edits to any surviving line except
the blank-line joins where blocks are excised.

| Construct | Lines | Notes |
|---|---|---|
| `if path == "/clinicas" or path == "/clinicas/":` block | `server.py:2809-2816` | calls `_render_clinicas_index()` |
| `if path.startswith("/clinica/"):` block | `server.py:2955-2971` | alias 301 to canonical (`:2958-2963`) + `_render_clinica_page(slug)`; its unknown-slug 302 to `/medicos` (`:2965-2969`) targets a path `legacy_urls` now 301s to `/doctors` (slice 3) — a chained redirect that dies with the block |
| `medicos_by_clinica(clinic_slug)` | `server.py:757-762` | only callers are the two renderers (`1342`, `1979`, `2080`) — repo-wide rg, zero other hits |
| `_render_clinica_page(clinic_slug)` | `server.py:1333-1798` | nested `_card` (`1422`), `_brila_section` (`1427`) die with it |
| `_FALLBACK_KNOWN_CITIES` + `_load_known_cities()` + `_KNOWN_CITIES` | `server.py:1883-1898` | **`_KNOWN_CITIES`' only consumer is `_clean_dir`** (`:1925`, inside `_clean_dir`'s body — NOT `_sentence_case`; rev 1 misattributed this). `_sentence_case` has no cities dependency. Side effect: `packs/ar-b-pinamar/known-cities.json` loses its only code reader — the file stays (data layer, §7) |
| `_sentence_case(s)` | `server.py:1901-1915` | only callers are the two renderers (`1533-1534`, `2016-2017`, `2053`, `2110`) |
| `_clean_dir(s)` | `server.py:1918-1927` | only callers are the two renderers (`1543`, `2042`, `2078`) |
| `_gen_clinica_desc(name, esps, count)` | `server.py:1930-1943` | only callers are the two renderers (`1536`, `2029`) |
| `_render_clinicas_index()` | `server.py:1946-2263` | nested `_info` (`1961`), `_has_brila` (`1967`), `_reviews` (`1970`), `_faces` (`1976`), `_maps_url` (`2009`), `_card` (`2014`), `_small_row` (`2072`), `_importancia` (`2126`), `_es_grande` (`2128`) die with it; its `_public_shell` call (`2249`) is one of that LIVE helper's two call sites — the other survives (§4) |

## 4. Exact keep inventory (each with its live consumer — the load-bearing half)

| Construct | Lines | Why it stays |
|---|---|---|
| `_canonical_clinic_slug()` | `server.py:744-754` | `medicos_by_clinica` (dies) BUT ALSO `all_clinicas()` (`:770`) and `especialidades_tree()` (`:879`) — both live |
| `all_clinicas()` | `server.py:764-789` | offline Brila writer `scripts/generate_brila_clinics.py:75` (`server.all_clinicas()`); uses `_rating_of` (`:787`) — exact precedent: slice 3 kept `public_medicos` for `scripts/build_doctor_photo_batches.py:90` |
| `_load_clinicas()` | `server.py:1321-1332` | `especialidades_tree()` (`:876`), `all_clinicas()` (`:766`), `_canonical_clinic_slug()` (`:745`) — the pack-chain reader for `clinicas.json` |
| `especialidades_tree()` | `server.py:803-898` | THREE live consumers (rev 1 credited one): `GET /api/specialties` (`:2841`), `_render_datos_page()` → `/datos`, LIVE (`:2323`), and the offline writer `scripts/build_featured_sprite.py:186` |
| `_rating_of()` | `server.py:374-375` | `public_medicos()` (`:682`), `all_clinicas()` (`:787`), `especialidades_tree()` (`:883`, `:888`); the `:1408` call site dies with `_render_clinica_page` |
| `public_medicos()`, `medico_by_url()`, `_turno_friendly()`, `_os_logo_tag()` | `server.py:629+`, `:906+` | pinned LIVE by slice 3's own test; `_render_medico_page` (`:906`, the live `/<esp-slug>/<name-slug>` route) / `_render_especialidad_page` (`:1197`, same matcher) still use them |
| `_public_shell()` | `server.py:1799+` | `_render_datos_page()` (`:2370`, def `:2300`) — the renderer-internal call at `:2249` dies with the index |
| `_load_cartilla()`, `_bar_chart()` | `server.py:2265`, `:2279` | `_render_datos_page()` (`:2302`, `:2309/2320/2331`) — `/datos` is unclaimed by any registry module, i.e. LIVE |
| the `/datos` route block | `server.py:2817-2824` | live (see above); untouched by this slice |
| `_KNOWN_PREFIXES` guard set | `server.py:2976-2978` | left untouched, matching slices 1/3 (which left their own stale `medico`/`cobertura` entries in place) — a skip-list entry for a now-unrouted prefix is inert, and removing `clinica` from it would change the `/<esp-slug>` matcher's behavior for `/clinica/<x>` inputs |

`clinicas.json` itself, its offline writers
(`scripts/generate_brila_clinics.py`, `scripts/sync_brila_clinics.rb`) and the
pack-layer plumbing are untouched: the file stays a live data source through
`_load_clinicas()` (aliases, ratings, counts); only its Brila-rich page fields
(title/subtitle/photos/sections) lose their last in-server reader, which the
phase-8 audit already recorded as acceptable.

## 5. Consumers verified absent — and the ones that SURVIVE (checked at `232e33b4`)

* **Tests:** no file under `test/` references `_render_clinica_page`,
  `_render_clinicas_index`, `medicos_by_clinica`, `_sentence_case`,
  `_clean_dir`, `_gen_clinica_desc`, `_load_known_cities` or `_KNOWN_CITIES`
  (repo-wide rg outside server.py: zero hits). The parity tests key on
  `patient_site`, not server.py.
* **Surviving `/clinica` hrefs — rev 1 wrongly claimed "nothing links INTO
  the de-linked pages".** There are exactly two sources, both fine to leave:
  1. `server.py:1136` and `server.py:1151`, inside `_render_medico_page`
     (`:906`) — the LIVE `/<esp-slug>/<name-slug>` profile route emits
     `href="/clinica/<slug>"` for a doctor's clinic. On the live site those
     links already resolve to the patient_site 302 → `/` today and keep
     doing so after the slice (patient_site claims `/clinica/*` before any
     server.py code); in a turnos-disabled install they become 404s instead
     of stale 200s. Cleaning them up means editing a LIVE renderer's markup
     — out of scope (§7), recorded here so the executor does not "discover"
     it mid-slice.
  2. `app.html:884` (`clinicHref`) — the WebTurno SPA shell's clinic links.
     `/app` itself is de-linked by the same patient_site block
     (`patient_site.py:5113`), so the SPA is unreachable on the live site;
     its links are dead-in-dead. The `/app`+`/panel` alias blocks that serve
     it are slice 5a's surface, not this one (§7).
* **Sitemap:** `patient_web/sitemap.py` has no `clinica` entry (verified).
* **Scripts:** `scripts/generate_brila_clinics.py` (kept working via
  `all_clinicas`, §4) and `scripts/sync_brila_clinics.rb` /
  `scripts/pack_gap_report.py` (operate on the JSON, not the functions).
* **Docs that reference this surface and must be touched by the EXECUTING
  slice (rev 1 omitted them):** `docs/rename-map.md:333-334` (cites the
  dead route family at "lines 2940–3140" — stale line refs after deletion);
  `docs/architecture/routing-y-modulos.md:9` ("The WebTurno code in
  server.py stays dormant" — the clinics half is now deleted, sentence
  needs a dated update); `docs/architecture/data-packs.md:211` (phase-8
  audit parenthetical lists `_canonical_clinic_slug`/`all_clinicas` as dead
  read sites — post-slice they are live keepers; add a dated correction
  rather than rewriting the historical entry).

## 6. Verification gates for the executing slice

New file `test/test_server_clinicas_retirement.py`, hermetic preamble
verbatim from `test/test_server_directorio_retirement.py:39-46`
(`TURNOS_ENV_FILE=/dev/null`, empty `DATABASE_URL`, `TURNOS_FAKE_AI=1`,
`TURNOS_SKIP_HSI=1`, basic-auth vars popped — set BEFORE importing server),
five parts:

1. **STATIC** — source absence: each `def <name>(` from §3 plus
   `path == "/clinicas"` and `path.startswith("/clinica/")` absent from
   `server.py`; source presence: `def _load_clinicas(`, `def
   _canonical_clinic_slug(`, `def all_clinicas(`, `def medicos_by_esp(`,
   `def especialidades_tree(`, `def _render_datos_page(`,
   `path == "/datos"` all still present.
2. **RUNTIME** — `hasattr(server, name)` False for every §3 name; True for
   `all_clinicas`, `_load_clinicas`, `_canonical_clinic_slug`,
   `especialidades_tree`.
3. **DISPATCH, legacy transport — explicit status + Location:** a
   `_FakeHandler` (same shape as the slice-3 test's) through
   `routing.dispatch(h, "GET")` for `/clinicas`, `/clinicas/`, `/clinica/x`,
   `/clinica/x/`: returns True, `h.status == 302`,
   `h.headers["Location"] == "/"`. Gating-honesty pin alongside (public API
   only): `app_modules.route_enabled("patient_web.patient_site")` is True
   and `app_modules.is_enabled("turnos")` is True under defaults, and
   `"patient_web.patient_site"` appears in `[m.__name__ for m in
   routing.GET]` — the claim is module-gated, dead on the live site
   (round-1 framing, stated in the test docstring).
4. **DISPATCH, native ASGI — explicit status + Location (NEW, the parity
   gap rev 1 left):** `fastapi.testclient.TestClient` against the real
   `app.app` (in-process, no socket — the exact pattern of
   `test/test_module_contract_asgi.py`: `config.ENV_PATH = ""`, empty
   `DATABASE_URL`, import `app`, `TestClient(app.app)`), requests
   `client.get("/clinicas", follow_redirects=False)` — and `/clinicas/`,
   `/clinica/x` — asserting `response.status_code == 302` and
   `response.headers["location"] == "/"`. This is the standing native-ASGI
   pin that `scripts/route_parity_check.py` deliberately is not (§2).
5. **DATA PATH — pin CURRENT alias/count semantics, do NOT fix them.** Patch
   `_load_db` with synthetic medicos AND `_load_clinicas` with an `alias_of`
   pair (deterministic; no pack dependency). Two contracts, pinned as they
   behave at `232e33b4`:
   * `all_clinicas()` merges counts under the CANONICAL slug: rows are
     `{name, slug, count, rating}`, and doctors whose raw `clinica` names
     differ but slug-canonicalize to the same entry contribute to ONE row's
     count.
   * `especialidades_tree()["clinicas"]` does NOT merge: counts are keyed by
     the RAW `clinica` name string (`server.py:832-834`), so alias partners
     stay as SEPARATE rows with separate counts; each row is
     `{name, count, rating, maps_url}` (no `slug` field), with the canonical
     slug resolved per-row only for the rating/maps lookup (`:879-889`).
     The test pins this asymmetry ON PURPOSE — if a future change makes the
     tree canonicalize, that change must arrive as its own reviewed diff,
     not ride along inside a retirement slice.

Full gates the executor must run green before reporting done (all local and
hermetic — NOTHING in this plan touches a live deployment):

* `TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s test -p "test_*.py"`
* `node --test test/*.test.mjs`
* **Hermetic offline-writer run (replaces rev 1's false `--help` gate —
  argparse answers `--help` and exits BEFORE `clinic_targets()` ever imports
  server, so it proved nothing):** with the same hermetic env preamble,
  `python3 scripts/generate_brila_clinics.py` with NO flags. The preview
  path calls `clinic_targets()` → `import server` → `server.all_clinicas()`
  and prints targets WITHOUT any network access (`api_request` runs only
  under `--execute`/`--status`, which need `BRILA_ACCESS_TOKEN`; preview
  checks neither). Gate: exit 0 and the stderr summary
  `"N clinics ready for Brila generation"`.
* Import audit on server.py after deletion (`python3 -m pyflakes server.py`
  or `python3 -c "import server"` under the preamble): no orphaned
  module-level imports expected (`re` has many other uses, `statistics` is
  used by `especialidades_tree` at `:875`), but verify, don't assume.

## 7. Explicitly out of scope (adjacent candidates, later slices)

* **`/app` + `/panel` SPA aliases** (`server.py:2800-2806`) — same
  patient_site de-link family, 7 lines, but a different surface (WebTurno
  SPA / legacy analytics panel, and `app.html`'s own `clinicHref` links live
  there). Natural micro-slice 5a.
* **`GET /api/medicos`** (`server.py:2827-2832`) — dead on the live site via
  `legacy_urls`' in-place rewrite to `/api/doctors` + `patient_site` answering
  the rewritten path; NOT unconditionally dead (§2 gating note). The doctors
  family's API half, deliberately not taken by slice 3 nor here.
* **`_KNOWN_PREFIXES` prune** — cosmetic; the series' precedent is to leave it.
* **`clinicas.json` Brila fields / offline writers / `known-cities.json`** —
  data layer, not server.py dispatch; out of series scope.
* **Fixing the tree's alias non-merge** (`especialidades_tree` raw-name
  counts, §6.5) and **cleaning the surviving `/clinica` hrefs** (§5) —
  behavior changes in live renderers; only ever as separately reviewed diffs.

## 8. Execution protocol — HELD, frozen handoff, rollback (replaces rev 1's commit/push/deploy checklist)

**Held.** Nothing in this section is permission to execute now. Prerequisite:
the slice-3 chain is reconciled with current `origin/main` (at freeze:
origin/main `6c34573e`, 61 commits ahead of the chain's base `7298a10a`,
strictly ahead). Reconciliation — rebase or merge, the product owner's call at that
moment — happens BEFORE this slice's executor starts, and every pinned
anchor in §§3-5 is re-verified against the reconciled tree (line numbers
will have drifted; the SYMBOL inventory is the stable contract).

**Frozen, no-stash handoff.** The executing session starts from a clean
checkout of the reconciled chain: `git status` empty, no untracked debris,
and **no stash involvement** — a stash exists on main ("reconcile
2026-08-06: 121 dirty paths pre-ff", 121 paths of stale-revert shape +
screenshot debris) that is NOT this slice's business; popping it into the
execution tree is forbidden. If the checkout is not clean, stop and report
rather than "working around" debris.

**Mechanical order.**

1. Delete the six functions + cities constants + two route blocks of §3
   (pure excision; join blank lines at the seam points).
2. Add `test/test_server_clinicas_retirement.py` per §6 (five parts).
3. Update the three docs of §5's docs list.
4. Run both full suites + the hermetic gates of §6.
5. ONE commit, the series' message shape: "Retire server.py's dead
   private-clinics page surface" — body names the de-link owner, both
   transports, the module-gating caveat, the kept live data path
   (`all_clinicas` for the Brila writer; `especialidades_tree` for
   `/api/specialties`, `/datos` and the featured-sprite writer), and the
   pinned-not-fixed alias semantics.

**Rollback.** The slice is pure deletion, so the commit reverts cleanly:
`git revert <slice-commit>` restores the excised blocks and tests verbatim.
Nothing else is needed — but see the stop conditions.

**Stop conditions (hard):** a §4 keep-symbol turns out to have NO live
consumer in the reconciled tree; a §6.5 pin fails meaning current behavior
differs from what §6.5 pinned (re-pin the doc first, then re-review); any
test outside the new file goes red. In each case: stop, report, no
improvisation.

**Out of bounds for the executing session:** push, merge to main, deploy,
smoke against `pinamar.humanrounds.org` / `demo.humanrounds.org`, or any
live-service contact. Whether/when the chain ships is a separate decision
with its own verification round — this plan's gates are hermetic by design.

**Acceptance:** server.py contains no `/clinicas`·`/clinica/` dispatch and no
orphaned renderer; every surviving symbol in §4 still has its live consumer;
the three docs of §5 are corrected; all suites and hermetic gates green
(explicit 302 + `Location: /` in BOTH transports); the alias/count asymmetry
of §6.5 pinned, not changed.
