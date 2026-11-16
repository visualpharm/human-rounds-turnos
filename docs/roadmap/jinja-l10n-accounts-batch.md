# Jinja localization cleanup — next bounded batch: the four `cuentas` account/auth HTML pages

**Kind:** historical roadmap / execution packet; implementation is present in the
2026-08-19 integration candidate.
**Status:** IMPLEMENTED AND VERIFIED. The body below preserves the final v8 plan and
its pre-execution constraints as historical evidence; the four templates, native
router paths, parity coverage, and standing test named by the plan are now present.
**2026-08-20 convergence follow-up:** dark and native transports now call the
same Accounts-owned body renderers over those four templates. The four inline
HTML constants and their 76 legacy `{{t.*}}` plus 15 `{{static.*}}` references
are retired. Historical dual-track instructions below remain evidence, not the
current architecture contract.
v1 (2026-08-16) → v2 after the first independent review
(§0 items 1-6). v3 applies the second verification pass — every count, cite and mechanism
re-derived independently again (§8), two gates sharpened, one gate added, the themed-run mechanism
corrected, and the dependency-lane revalidation duty made unconditional (§0 items 7-11). v4
applies the third independent review's four defect findings (§0 items 12-15): the parity run's
two peers now satisfy the SAME test-auth rule (proven by running both peers at base, §8 v4),
the themed pass is hermetic to a per-run throwaway database, every non-200 contract is pinned to
exact bytes/headers with a proven env recipe, and the stale `account-form.js` count is corrected.
v5 applies the fourth independent review's verdict `FIX` (§0 items 16-21): the parity peers are
now the SAME loopback address on both transports (v4's recipe still leaned on a peer-independent
env bypass while the gate-off rows kept comparing `203.0.113.5` against TestClient's `testclient`),
a hidden 3-second `auth_mode` cache that silently reverts any enabled-state run to gate-off was
found and pinned, every non-200 wire contract is pinned to exact bytes AND exact header
presence/absence on BOTH wires, the themed pass may seed ONLY inside the throwaway-DB branch,
the `account-form.js` count is reconciled against the reviewer's raw-regex 150, and implementation
is now BLOCKED until the doc-truth lane freezes.
v6 (2026-08-18, doc-only correction pass) changes the EXECUTION MODEL and nothing else in the
reviewed design (§0 items 22-23): every instruction to commit, push, merge or land is removed —
the batch ends as a frozen diff handed to Codex — and the §6 whole-script parity run is replaced
by an accounts-only offline proof that cannot inherit or touch any real `DATABASE_URL`. Executed
by z.ai GLM, model `glm-5.3`, through the Claude Code harness (GLM fleet config).
v7 (2026-08-18, doc-only correction) applies the fifth independent review's single finding
(§0 item 24): the v6 offline proof could still inherit `TURNOS_ENV_FILE`, letting `core.config`
read a real env file even with the script's `config.ENV_PATH` shield — the driver now pins
`TURNOS_ENV_FILE` to an explicitly nonexistent scratch path before any repo import. The
accounts-only route-parity path and the frozen-diff/no-commit handoff are unchanged.
v8 (2026-08-18, doc-only correction) applies the sixth independent review's two findings
(§0 items 25-26): the freeze step's residual `git add -N` was itself a Git mutation (an index
write) and is REMOVED — the frozen local diff is now produced read-only (`git diff` +
`git diff --no-index`, zero writes to Git state) — and the offline proof's `DATABASE_URL`
port-9 sentinel OVERWRITE is replaced by explicit REMOVAL (`os.environ.pop`) of both inherited
DB variables, so no-contact holds by construction (`db._connect` raises `DbUnavailable` before
any pool or socket) rather than by port choice.
**Base:** `232e33b4` ("Retire server.py's dead private-directory dispatch surface"), branch
`glm/jinja-next-batch-prep`. **Symbols are primary; line numbers are pinned to this base and WILL
drift** — see the live-lane warning in §3 before executing.

---

## 0. Review corrections applied in v2 (items 1-6), v3 (items 7-11), v4 (items 12-15), v5 (items 16-21), v6 (items 22-23), v7 (item 24) and v8 (items 25-26) — audit trail

1. **Translation-key counts were wrong.** v1 claimed "76 tokens, **57 unique** keys (49 `account.*`
   + 7 `footer.*`)" and "`account-form.js`'s 35 `account.dash.*` keys". Re-derived by AST parse of
   the four constants: **76 tokens, 51 unique keys — 45 `account.*` + 6 `footer.*`** (§2, §8). The
   34 catalog keys not used server-side are consumed by **inline `T()` calls inside
   `cuentas/accounts.py` itself** (25 of them `account.dash.*`) — `account-form.js` contains **zero**
   catalog-key references (§2).
2. **Existing auth parity scenarios were misdescribed as absent.** `scripts/route_parity_check.py`'s
   `ESCENARIOS_ACCOUNTS` (L587-612) **already rows** `GET /account`, `GET /account/reset`,
   `GET /auth/test` and `GET /auth/sso` (added by the Tier 4 slice 8 closing batch), and
   `test/test_accounts.py` already gates the no-session render (L359), the authenticated
   first-response bootstrap+cookie (L369), the cookie contract (L149), demo banners (L397/409/418)
   and all four pages in BOTH languages under test-auth enabled (L607-760). The real gap is not
   missing rows: it is that the `/auth/test`//`/auth/sso` rows run **gate-off** under the default
   password auth mode (both sides 404 — a green that proves nothing) and that no scenario compares
   the Jinja twin to legacy. §6 now separates "exists, stays green" from "genuinely new".
3. **Acceptance gates were incomplete.** §6 now enumerates the full acceptance matrix: status
   (200/302/404 per gate state), headers (the parity script compares **header key sets only** and
   deliberately ignores `date`/`server`/`content-length`/`set-cookie`, so value-level `Content-Type`
   and `Cache-Control` assertions move to the unittest gate), redirect (`Location` equality on the
   no-session renaper case), cookie (presence/name parity on the bootstrap path — `Set-Cookie` values
   are non-comparable by design), bootstrap escaping (`<` → `\\u003c` both sides, no double-escape),
   and demo (banner on/off per route; `/account/demo-access` keeps reaching the legacy renderer).
4. **Planned file scope was omitted.** §4.1 said "Edited (1 file)" while §6 planned to extend
   `scripts/route_parity_check.py` and §9 planned to update this doc's own §1 table. The edited set
   is now stated as **two code files + this doc** (§4.1).
5. **Live reference-lane dependencies were unpinned.** A `git diff --name-only 232e33b4` scan over
   every live lane worktree (§3) found `glm/meta-doc-truth` **already modifying
   `cuentas/accounts.py`** — every line cite in this packet drifts when that lane lands — and
   `glm/admin-static-fingerprint-final` modifying `core/static_fingerprint.py` (a read-only
   dependency here; `MIGRATED` only grows). §3 now carries the scan result and the
   execution-time re-verification duty.
6. **A false SSO theme divergence.** v1's risk R1 claimed running `theme.inject` on the SSO page
   "would inject a block the legacy path never emits → byte divergence on branded installs". False:
   `theme.inject` **early-returns unchanged when the text contains none of `{{tema}}`/`{{fuentes}}`/
   `{{favicon}}`** (`core/theme.py` L237-240 bytes, L249-251 str — the positional branding `<style>`
   drop at L245-247/L255-257 sits *inside* the token-present branch), and `_SSO_HTML` contains none
   of those tokens. The same holds for `site_identity.render` on SSO (no `{{sitio.*}}` tokens, and
   none of the 8 `account.sso.*` catalog values embeds one). The genuinely divergent steps are the
   **demo banner** (positional, would change bytes if run on `/auth/test`//`/auth/sso`), the
   **pre-render gates**, the **bootstrap splice** and **`Set-Cookie`** — R1/§4.2 corrected
   accordingly.

7. **The adversarial bootstrap name was too weak (gate sharpened).** v2's escaping case was "a name
   containing `<` and non-ASCII". The bootstrap payload is spliced inside a `<script>` block, so
   the string that actually breaks out is `</script>` — §6(b) now requires a `bootstrap_user` name
   containing `</script>`, a bare `<`, and non-ASCII, with `<` → `\\u003c` on both sides, no raw
   `<` or `</script>` inside the serialized payload, and no double-escape.
8. **The language-switch redirect was specified but never gated.** §4.2 requires the twin to reuse
   `_maybe_lang_switch` (`core/site_identity_router.py` L72-78, which runs `i18n.handle` on a
   `LegacyHandlerShim`), but v2's §6 gated only cookie-driven renders — and NO existing test
   drives the switch on any of the four routes (`test_accounts.py` sets languages via
   `Cookie: lang=` only, `_LangHandler` L625-629; zero `?lang`/`302`/`Location` assertions in the
   file). §6(b) now gates the switch itself. Mechanism re-pinned: the intercept is
   `_maybe_switch_lang` (`core/i18n.py` L591-611, entered via `handle` L614-625 — v1/v2's
   "L544-551" cite is `inject_bootstrap`, not the switch): GET + `?lang=<code>` +
   `switcher_mode() != "off"` + ≥2 enabled languages → 302 to the same path (or "/") with
   `Set-Cookie: lang=<lang>; Max-Age=1y; Path=/; SameSite=Lax`; switcher off or a single language
   falls through to the handler — the gate drives the enabled shape (`LANGUAGES=es,en`).
   (v4: the cookie's real wire value is `Max-Age=31536000` and the switcher needs
   `LANG_SWITCHER_MODE` too — §0.13.)
9. **The themed parity run was aimed at the wrong unit.** The branding `<style>` is not a pack
   attribute: the active theme id is the `catalogo` row `config/tema` (`core/theme.py` L85-88,
   L103-105) and the accent color is the `catalogo` row `config/marca` (`patient_web/branding.py`
   — storage docstring L30: `{"color": "#rrggbb"|None, "logo": bool}`); `_branding_style()`
   (`core/theme.py` L111-115) returns "" unless that color is set, and NO pack in `packs/`
   carries theme keys at all (checked every `*/sitio.json`). §5.1/§6(c) now seed those rows for
   the themed pass; the parity script already owns the pattern to copy (an env block set/popped
   around a family run — `LANGUAGES` in `_correr_escenarios_paginas`,
   `scripts/route_parity_check.py` L843-859; the accounts family is invoked at L871).
   (v4: the seeding's home moved again — §6(b)'s throwaway-DB unittest case, never the script —
   §0.14.)
10. **Two §6(a) mechanism sub-claims were imprecise** (anchors correct, coverage wrong). The
   authenticated first-response test (L369-379) asserts the bootstrap embed but NOT the cookie —
   the first-response `Set-Cookie` is asserted by the demo-URL tests (L388/393) and
   `test_cookie_contract` (L149) pins `_set_cookie`'s output directly; and
   `test_cache_headers.py` never touches `/account` (static assets only), so §6(b)'s
   `Cache-Control: no-store` assertion is net-new coverage. Also re-pinned while there:
   `test_seo.py:113-121` reads the four `_*_HTML` **constants** (`from cuentas import accounts`
   L8/L115-118), not template files — invisible to the twin by construction; the both-languages
   block is class `TestI18nWiring` (L650-761, comment L603-609) with the env recipe at L656-668
   (`_ENV_KEYS` L656-658; `LANGUAGES=es,en` L662, `TURNOS_AUTH_MODE=renaper` L663,
   `TURNOS_RENAPER_PROVIDER=stub` L664, `TURNOS_PROXY_TRUST_SECRET` L668); `ESCENARIOS_ACCOUNTS`
   rows sit at L588/L601/L602/L603 (block L587-613).
11. **The 51-key inventory is now reconciled to the token** (§2, §8): per-constant unique keys
   10 + 28 + 8 + 11 = 57; the 6 `footer.*` keys (`about`/`credit`/`mission`/`news`/`privacy`/
   `terms`) recur in BOTH `_ACCOUNT_HTML` and `_RESET_HTML` → **51** global unique (45
   `account.*`, disjoint per constant: 4+28+8+5); the 76−57 token surplus is 19 repeats inside
   `_TEST_AUTH_HTML` alone (47 tokens → 28 unique). Client side: the inline `T()` calls use 36
   distinct `account.*` keys (25 `account.dash.*`), ALL present in the catalog, of which 2
   (`account.register.lede`, `account.register.title`) are ALSO server-side — the 34 catalog keys
   unused server-side are exactly those 36 minus the shared 2. And `account-form.js` does carry
   `T()` calls (count corrected in v4 — §0.15), but every one of their keys is OUTSIDE
   `account.json` (inline Spanish fallbacks) — "zero catalog-key references" holds, for a
   different reason than "no T() usage".

12. **The parity run's two peers did not satisfy the same test-auth rule (v4; §6(c) recipe was
   unrunnable as written).** v3's enabled-state recipe (`TURNOS_AUTH_MODE=renaper` +
   `TURNOS_RENAPER_PROVIDER=stub` + `TURNOS_PROXY_TRUST_SECRET`) opens the LEGACY peer but NOT
   the FastAPI TestClient peer — run both peers at base with exactly `_run_legacy_intake`'s shim
   shape (Host `testserver`, client `203.0.113.5`) vs the real TestClient round-trip: `/auth/test`
   legacy **200** vs FastAPI **404**, permanently (§8 v4 probe A). Why: on the FastAPI side
   `LegacyHandlerShim.from_request` carries the ASGI client
   host, which under TestClient is the literal `"testclient"` — not an IP, `ipaddress.ip_address`
   raises, the gate returns False — and no signed `X-Turnos-Proxy-Peer` marker can ever arrive on
   the router path (`app.py`'s catch-all is the ONLY signer; `legacy_shim` never injects one;
   `proxy_trust` fails closed — its own docstring). On the legacy side the synthetic peer decides
   (on the probe's Python 3.14, `203.0.113.5` is TEST-NET-3 = non-global and Host `testserver`
   is a dev host, so the second peer branch passes and legacy renders 200; a Python that
   classified TEST-NET-3 as global would instead give 404/404 — v3's worthless green again).
   Either way v3's recipe proves nothing; the proxy secret opens neither FastAPI outcome and is
   dropped from the parity recipe. The ONE transport-independent branch
   is `_stub_desplegado()` (`TURNOS_RENAPER_STUB_DEPLOYED=1`, `cuentas/accounts.py` L511-524,
   consulted at L541 BEFORE any peer resolution) — both peers therefore evaluate the gate
   identically.
   Probe-proven at base: with it set, all four pages are 200 with byte-identical bodies and
   header key sets on both peers, in BOTH languages. §6(c)/§5 R4 corrected. Note the asymmetry
   is BY DESIGN in production (fail-closed under the proxy) — the stub-deployed env is the
   harness's legitimate way to say "this run IS the simulator host", which is exactly what the
   env means on a deployed install.
13. **The language-switch gate could have proven nothing (§6(b)).** v3 said "with
   `LANGUAGES=es,en`, switcher enabled" without naming the switcher env. `switcher_mode()`
   (`core/i18n.py` L284-286) reads `LANG_SWITCHER_MODE` and defaults to **"off"** — with only
   `LANGUAGES` set, `i18n.handle` on `GET /account?lang=en` returns False, sends nothing, and
   the "gate" would compare a 200 render against a 200 render (the same worthless-green class
   as §0.2's gate-off rows). Probe-proven at base: with `LANGUAGES=es,en` +
   `LANG_SWITCHER_MODE=secret`, all four routes return 302, `Location` = the same path, EMPTY
   body, `Set-Cookie: lang=en; Max-Age=31536000; Path=/; SameSite=Lax` (§8 v4 probe C). §4.2/§6(b)
   now name the env and pin the exact attributes (v3's "Max-Age=1y" was a placeholder, not the
   wire value).
14. **The themed pass could write to an inherited real database (§5.1/§6(c) mechanism
   replaced).** v3 seeded `catalogo` rows "at runtime from within the script" — but
   `db._db_url()` (`core/db.py` L1139-1143) resolves `os.environ.get("DATABASE_URL")` FIRST and
   the env file only second, so the parity script's `config.ENV_PATH = ""` shield does NOT
   protect against an exported `DATABASE_URL`: on a dev machine with the real app/dev lane
   exported, `db.catalog_upsert("config", "marca"/"tema")` would write the accent color and
   theme id into the REAL lane's database, where both rows persist (3 s-cached reads) and change
   every subsequent local render. The parity script also promises "No necesita Postgres"
   (its own docstring, L119) — an in-script seeded DB breaks that contract. v4 moves the themed
   proof ENTIRELY into §6(b): the unittest's own per-run throwaway database
   (`test/_dbtest.py`'s `PerModuleDb` — the same pattern ~28 DB-backed test files use), seeded
   through the modules' own writers (`branding.save_color()`, `theme.save()` — both invalidate
   their own 3 s caches), visibly skipped when the test DB lane is unavailable. The parity
   script takes on no NEW database duty — the accounts family is DB-free at base (run proven,
   §8 v4), and its "No necesita Postgres" docstring (L119) is ALREADY drifted for later families
   (the script dies at `ESCENARIOS_APPOINTMENT_OFFER` with no DB), so it is not a license to add
   more.
15. **`account-form.js`'s `T()`-call count was stale (§0.11/§2).** v3 said "~120 `T()` calls".
   Re-derived by regex over the file (excluding the `T` definition and comments): **146 call
   sites, all `T("…")` double-quoted, 109 unique literal keys — 144 `account.*` calls + 2
   `date.*` prefix-template calls (`T("date.month_full." + n)`)**; 0 of the 109 keys exist in
   `locales/es/account.json`, so "zero catalog-key references" is unchanged. §0.11/§2 now carry
   the exact numbers.

16. **The parity comparison's two peers must be the SAME loopback address on BOTH transports
   (v5; §5 R4/§6(c) rewritten).** v4 fixed the *enabled* state by env bypass
   (`TURNOS_RENAPER_STUB_DEPLOYED=1`, evaluated before any peer resolution) but left the
   comparison itself asymmetric: the script's legacy runner still synthesizes peer
   `203.0.113.5` (`_run_legacy_intake`, `scripts/route_parity_check.py` L374) while the FastAPI
   half goes through the default TestClient, whose ASGI client host is the literal `testclient`
   (`legacy_shim.py` `from_request` L129-138 maps `request.client.host` → `client_address[0`;
   `ipaddress.ip_address("testclient")` raises → `_test_auth_enabled` returns False,
   `cuentas/accounts.py` L543-549). Probe-proven at base (§8 v5 probes B/C): with
   `TURNOS_AUTH_MODE=renaper` + `TURNOS_RENAPER_PROVIDER=stub` that pairing yields
   `/auth/test` legacy **200** vs FastAPI **404** — a permanent DIFF (the review's named defect,
   reproduced live); and the legacy half's 200 rides the Python-version-dependent TEST-NET-3
   branch (`not peer.is_global` + dev Host), so a future `ipaddress` reclassification flips it
   to 404/404 — a green either way for reasons that are not the migration. **The v5 rule: the
   accounts family compares the SAME loopback peer on both sides** — legacy shim
   `("127.0.0.1", 54321)` AND a dedicated `TestClient(app, client=("127.0.0.1", 54321))`
   (installed starlette 1.4.1 supports the `client=` override, verified) — so
   `_test_auth_enabled` takes the `peer.is_loopback` branch (L550-551) identically on both
   transports, with NO secret, NO stub-deployed, NO `host_de_dev` dependence. Probe-proven:
   renaper+stub alone → all four pages 200/200, byte-identical bodies and header key sets, in
   BOTH languages. `TURNOS_RENAPER_STUB_DEPLOYED=1` is demoted to an OPTIONAL second enabled
   pass (still valid — it short-circuits before peer resolution, so it is compatible with any
   peer choice), never the primary. The signed-marker alternative is legitimate ONLY where a
   marker can actually arrive and verify — the unittest's `_LangHandler` (loopback socket peer
   + `proxy_trust.sign` marker + real secret; valid under `TURNOS_ASGI` both off and on); in
   the parity script it is structurally unavailable: the TestClient path contains no signer
   (the one signer is `services/legacy_proxy.py` L167 — v4's "app.py's catch-all is the ONLY
   signer" misattributed the file; app.py wires it, the service signs it), and with
   `TURNOS_ASGI` unset `proxy_trust.real_peer` ignores markers outright
   (`core/proxy_trust.py` L165-169).
17. **A hidden 3-second `auth_mode` cache silently reverts any enabled-state run to gate-off
   (v5; found live while re-probing §0.16).** `_auth_mode()` is NOT a plain env read: since
   2026-07-19 it delegates to `auth/mode.py::current()` — DB row `config/auth_mode` first,
   env `TURNOS_AUTH_MODE` as default, **cached ~3 s in a module-level `_cache`** (L32, L47-66).
   `config._reset_cache_para_tests()` (`core/config.py` L647-652) resets config's caches but
   NOT `auth_mode._cache`, so an env block that flips `TURNOS_AUTH_MODE` within 3 s of a
   previous `current()` call inherits the stale value — exactly what the first v5 probe run
   did: state B (renaper+stub) served gate-off 404/404 on `/auth/test`//`/auth/sso` because
   state A had cached "password" seconds earlier. The repo's own pattern for this is
   `test/test_auth_mode.py` L38-39 (`auth_mode._cache["v"] = None; ["t"] = 0.0`). §6(b)/§6(c)
   env recipes now name that reset as a MANDATORY step of every block that touches
   `TURNOS_AUTH_MODE`; without it the "enabled" parity run degenerates into the same worthless
   404/404 green §0.2 condemns.
18. **Every non-200 contract is pinned to exact bytes AND exact header presence/absence on BOTH
   wires (v5; §6(b) bullets rewritten, §8 v5 probes E/F/G).** v4 pinned bodies and the two
   named headers but not the full header SET of the two 302s. Probe-proven byte- and
   header-exact on legacy AND on the FastAPI wire at base: **gate-off 404** — body exactly
   `{"error": "test_auth_no_disponible"}` (36 bytes, `json.dumps` default separators → space
   after the colon), `Content-Type: application/json; charset=utf-8`, `Cache-Control:
   no-store`, `Content-Length: 36`, and NO other header (no Set-Cookie); a twin answering
   FastAPI's `HTTPException(404, …)` instead breaks all three (the `{"detail": …}` envelope
   class, already met once at `scripts/route_parity_check.py` L377-392). **Auth 302**
   (`_redirect`, `cuentas/accounts.py` L1742-1746) — `Location: /auth/renaper?next=%2Faccount`
   exactly, `Content-Length: 0`, EMPTY body, and `Content-Type`/`Cache-Control`/`Set-Cookie`
   all ABSENT. **Language-switch 302** (`_maybe_switch_lang`, `core/i18n.py` L591-611) —
   `Location` = the request path, `Set-Cookie: lang=en; Max-Age=31536000; Path=/; SameSite=Lax`
   exactly, `Content-Length: 0`, EMPTY body, `Content-Type`/`Cache-Control` ABSENT; the twin's
   wire shape was proven by driving the pilot's REAL twin path (`site_identity_router`
   `_maybe_lang_switch` → `shim.to_response()`) on `/about.html?lang=en` — identical three
   headers, Starlette adding `content-length: 0` exactly as the legacy sends it explicitly.
19. **The themed pass may seed ONLY inside the throwaway-DB branch (v5; §5.1/§6(c) tightened).**
   v4 moved the seeding to `PerModuleDb` but did not pin the guard ORDER. `db._db_url()`
   (`core/db.py` L1139-1143) prefers an exported `DATABASE_URL` over everything, so the binding
   rule is: `branding.save_color()`/`theme.save()` run ONLY after `PerModuleDb.set_up()` set
   `have_db = True` — i.e. against the run-scoped throwaway (`test/_dbtest.py` `run_scoped`
   L87-101: `<base>_<run-token>` name, unique per run) — and a skip (`have_db` False, whatever
   `DATABASE_URL` a shell exported) means NO database write of any kind, visible `skipTest`.
   `PerModuleDb.set_up()` (L207-236) never writes rows into a real database even when the
   maintenance URL resolves to the dev lane's server: its only server-level DDL is
   `DROP IF EXISTS`/`CREATE` of the token-named throwaway, after which it repoints
   `os.environ["DATABASE_URL"]` at the throwaway; `tear_down()` (L238-275) closes pools and
   drops it `WITH (FORCE)` — deterministic teardown, with crash leftovers reclaimed by the
   next run's `reclaim_stale_databases` (age + zero-live-sessions gated, non-FORCE first,
   L109-166). The explicit alternative stays: pure mocks (patch the reads, never touch a DB).
   The parity script keeps NO new database duty (§0.14 stands).
20. **Implementation is BLOCKED until the doc-truth lane freezes (v5; §9.1 hardened).** §3
   records `glm/meta-doc-truth` as already modifying `cuentas/accounts.py` — this packet's
   byte-frozen parity reference. v4 made re-derivation a duty "whenever the lane lands"; v5
   makes it a PRECONDITION: steps 2+ do not start until that lane has frozen/merged and the
   §8 count/cite re-derivation has re-pinned this packet to the new base (lane re-verified
   still open at v5 freeze, `git worktree list`). In the implementation itself
   `cuentas/accounts.py` stays READ-ONLY — byte-frozen reference AND the `TURNOS_ASGI`-off
   production implementation; the batch writes templates, `accounts_router.py`, the parity
   script and the new test only (§4.1).
21. **`account-form.js`'s count reconciled against the reviewer's 150 (v5; §0.15/§2).** The
   fourth review counted **150 `T(` occurrences** by raw regex; a line-by-line classification
   of every occurrence (§8 v5) decomposes the 150 as **146 real call sites + 1 function
   definition (`function T(key, vars, fallback)`, `account-form.js` L45) + 3 comment mentions
   (L134, L135, L894)**. The 146 calls are all `T("…")` double-quoted (144 `account.*` + 2
   `date.*` prefix templates `T("date.month." + n)` / `T("date.month_full." + mesN)`), 109
   unique literal keys, 0 of them in `locales/es/account.json` — so v4's numbers stand as the
   CALL-SITE truth and "zero catalog-key references" is unchanged; the raw 150 is now stated
   alongside so the two counts cannot be mistaken for a discrepancy again. This packet
   standardizes on: call sites = 146, raw `\bT(` occurrences = 150 (1 def + 3 comments).

22. **The batch no longer lands itself — it hands a frozen diff to Codex (v6; §4.1/§6(c)/§9
   rewritten).** v5's §9 ended "Commit message notes: … Push, land." under a "one worktree, one
   PR" frame, §4.1 said §1's table "gains the batch's row in the batch's own commit", and §6(c)
   said "attach the empty diff to the migration commit". All of those were instructions to land,
   and all are REMOVED, not reworded: the implementation ends at a FROZEN DIFF — every written
   file left in the worktree, nothing committed/pushed/merged — handed to Codex with the parity
   evidence; review, commit and landing belong to Codex and the product owner. The functional design those
   sentences carried survives unchanged: the §1 table still gains its row (inside the frozen
   diff), the empty parity diff still travels as evidence (attached to the handoff, honoring the
   script's own evidence contract, `scripts/route_parity_check.py` L10-16), and the checklist is
   still ordered and single-worktree. The dependency-lane freeze/merge mentions in §0.20/§3/§9.1
   are state observations about OTHER lanes, not landing instructions, and stay.
23. **The §6 whole-script parity run was non-hermetic — replaced by an accounts-only OFFLINE
   proof (v6; §6 Commands/§6(c)/§9 step 6).** `python3 scripts/route_parity_check.py` runs
   `main()` — EVERY family, in order — and past the accounts family it is not DB-free at base:
   the script dies at `ESCENARIOS_APPOINTMENT_OFFER` with no database, and on a machine that
   exports a real `DATABASE_URL` those later rows run against that REAL database (§8 v4) — the
   exact inheritance §0.14/§0.19 ban. The replacement is a scratch driver in the executor's job
   dir (not committed — the §8 probe convention) that, IN THIS ORDER: (1) scrubs the DB env
   AND the env-file channel BEFORE any repo import (`os.environ.pop("TEST_DATABASE_URL", None)`,
   then `os.environ["DATABASE_URL"] = "postgresql://parity@127.0.0.1:9/parity"` — port 9 is
   discard: nothing listens, so ANY accidental database touch fails closed, and the inherited
   value is overwritten before any repo module can read it; the §8 v5 probe shield promoted to
   the proof's first step — plus, since v7, `os.environ["TURNOS_ENV_FILE"]` pinned to an
   explicitly nonexistent scratch path in the same step, §0.24 — and, since v8, the port-9
   `DATABASE_URL` OVERWRITE is itself replaced by an explicit `os.environ.pop("DATABASE_URL",
   None)`: removal fails closed at URL RESOLUTION (`db._connect` raises before any socket),
   the overwrite only failed closed at connect time, §0.26); (2) imports the script
   out-of-tree via
   `importlib.util.spec_from_file_location` (its own module-level shields — `TURNOS_FAKE_AI`,
   `TURNOS_SKIP_HSI`, `config.ENV_PATH = ""`, L134-138 — then apply on exec); (3) builds the
   §6(c) matched-peer client `TestClient(app_module.app, client=("127.0.0.1", 54321))`
   (`import app as app_module` at L190; starlette 1.4.1 supports `client=`, §0.16); (4) runs
   ONLY the accounts family — the runner §6(c)'s edit adds, both gate states inside — and
   NEVER `main()`. At packet-freeze time (before that edit exists) the same driver validates
   the harness through the seam `main()` itself calls at L871:
   `_correr_escenarios_intake(client, ESCENARIOS_ACCOUNTS)`. The unittest and frontend suites
   stay in §6's command list unchanged (the repo's own lanes — `TEST_DATABASE_URL`-scoped and
   pure-node); only the whole-script invocation is banned.

24. **The offline proof still inherited `TURNOS_ENV_FILE` — the env-file channel stayed open
   (v7; §0.23 step 1 / §6 Commands steps 1-2 amended).** The script's module-level shield
   `config.ENV_PATH = ""` (`scripts/route_parity_check.py` L138) blanks only the module-global
   FALLBACK: `core/config.py`'s `_env_path()` (L80-87) resolves
   `os.environ.get("TURNOS_ENV_FILE") or ENV_PATH`, so an inherited `TURNOS_ENV_FILE` REPLACES
   the effective env-file path outright — by design (its own docstring, L37-46: the E2E
   harness's hermetic mechanism, and `Dockerfile` L65 ships
   `TURNOS_ENV_FILE=/app/data/turnos.env` in production images, so an inherited value is real,
   not hypothetical). `_dotenv()` (L100-129) then opens and parses that real file, and `get()`
   (L584-627) accepts file values for every key the driver does not itself export —
   `os.environ` wins only for the keys step 1 scrubs (`DATABASE_URL`: the environ consult
   L610-616 precedes the file consult L618-625), so gate-shaping and harness keys (`LANGUAGES`,
   auth/demo/HSI/mail, pack roots) could still arrive invisibly from a real `.env` — the same
   worthless-green class §0.2/§0.17 ban. Fix, folded into the proof's FIRST step so
   `_dotenv_cache` (L76) cannot have cached the real file either:
   `os.environ["TURNOS_ENV_FILE"] = os.path.join(<job dir>, "turnos-env-file-absent")` — an
   explicitly nonexistent scratch path inside the executor's job dir (never in the repo — the
   §8 probe convention). Fail-closed by construction: `os.path.getmtime` on it raises `OSError`
   → `_dotenv()` returns `{}` (L106-108, L115-116) — the exact "(o a uno inexistente)" pattern
   `core/config.py`'s own docstring blesses (L44-46). The accounts-only route-parity path
   (§6 Commands — accounts family only, never `main()`) and the frozen-diff/no-commit handoff
   (§0.22/§9 step 7) are unchanged.

25. **The freeze step still mutated Git — `git add -N` writes the index (v8; §9 step 7
   rewritten).** v6/§0.22 removed every commit/push/merge/land instruction but left one
   apparently-harmless index touch: `git add -N` (intent-to-add) so the new files would show in
   `git diff`. Intent-to-add IS a Git mutation — it writes the index, Git state the executor
   does not own, and "no ref mutation" that edits the index is not no-mutation. The v8 rule:
   the executor performs ZERO writes to Git state — no `git add` in ANY form (intent-to-add
   included), no stash, no commit, nothing that changes `git status` output. The frozen local
   diff is produced READ-ONLY: `git diff 232e33b4 -- cuentas/accounts_router.py
   scripts/route_parity_check.py docs/roadmap/jinja-l10n-accounts-batch.md` for the modified
   tracked files, and `git diff --no-index -- /dev/null <file>` for each of the five new files
   (the four templates + `test/test_accounts_jinja.py`; exit 1 = "differences found" is the
   EXPECTED success path — `/dev/null` baselines a new file with no index involvement),
   concatenated into ONE frozen diff artifact in the executor's job dir, with
   `git status --porcelain` captured before and after to prove the worktree's Git state is
   byte-identical across the freeze. The handoff is unchanged in substance: frozen diff + this
   packet + parity evidence to the coordinator (Codex); review/commit/landing stay Codex's and
   The product owner's.
26. **The offline proof MASKED `DATABASE_URL` instead of removing it — and the mask still
   dialed a socket (v8; §0.23 step 1 / §6 Commands step 1 amended).** The v6/v7 scrub
   OVERWROTE the inherited value with `postgresql://parity@127.0.0.1:9/parity` (port 9 =
   discard). Two defects: (a) the inherited value is replaced, not removed — masking is the
   same ambient-inheritance shape the correction was meant to eliminate, and a reader/flag
   that only checks "is DATABASE_URL set" sees a set variable; (b) "fail closed" held only at
   CONNECT time — an accidental DB touch still opened a real TCP connect attempt to
   `127.0.0.1:9` and paid the pool's retry backoff (`reconnect_timeout=10`, `core/db.py` L1197
   and its docstring L1166-1177) before failing: contact-attempted by construction,
   no-contact by port luck. The v8 recipe REMOVES both inherited variables explicitly —
   `os.environ.pop("DATABASE_URL", None)` AND `os.environ.pop("TEST_DATABASE_URL", None)` —
   before any repo import, with §0.24's `TURNOS_ENV_FILE` nonexistent-pin unchanged. With both
   channels closed, `db._db_url()` (`core/db.py` L1139-1143: exported var, else env-file
   value, else EMPTY — there is no hardcoded localhost default) returns `""` and
   `db._connect()` raises `DbUnavailable("DATABASE_URL no configurada")` (L1361-1363) BEFORE
   any pool or socket is created — the audit cannot contact or mutate ANY database, live or
   development, by construction. Removal is not a new regime for these rows: the §8 v4 probe's
   own offline run (`env -u DATABASE_URL`) was green through all 21 accounts-family rows with
   the variable ABSENT. The four account/auth route contracts and header proofs are unchanged
   (§6(b)/§6(c): matched loopback peers, both gate states, byte- and header-identity).

## 1. What "Jinja localization cleanup" means in this repo, and where the series stands

The legacy rendering pipeline substitutes `{{t.*}}` / `{{sitio.*}}` / `{{legal.*}}` / `{{static.*}}`
tokens by regex over raw HTML strings, then applies post-steps (`inject_html_lang`,
`inject_bootstrap`, `theme.inject`, demo banner). The Tier 4 re-platform introduced a second,
Jinja2-based mechanism (`core/jinja_env.py`, globals `t`/`sitio`/`legal_doc`/`static`) and migrates
serving surfaces to it one bounded batch at a time, each with a byte-parity gate — the strangler
pattern: the legacy implementation keeps running unchanged for `TURNOS_ASGI` off (production today).

Batches already landed:

| Surface | Mechanism today | Where |
|---|---|---|
| The four public pages (`/`, `/index.html`, `/about.html`, `/privacy.html`, `/terms.html` — four pages, five paths) | Jinja twin live on ASGI (`core/site_identity_router.py`); legacy twin (`core/site_identity.py:render_page`, reads the root-level `.html` files) still serves prod | Tier 4 slice 2 + slice 8 |
| Admin screens `cola`, `pacientes`, `mis-turnos`, `reportes`, `solicitudes` | Server-routed Jinja screens off `templates/admin/base.html` | Tier 5 slice 4/5 |
| `modules/intake` `#entrevistas` screen | Server-routed Jinja screen | Tier 5 slice 4 pilot |
| The four `cuentas` account/auth HTML pages (`/account`, `/account/reset`, `/auth/test`, `/auth/sso`) | Jinja twin live on ASGI (`cuentas/accounts_router.py`); legacy `_serve_*` in `cuentas/accounts.py` still serves `TURNOS_ASGI` off | this batch |

The remaining executable legacy-token surfaces are inventoried in §2. **This batch
adds the four `cuentas` HTML pages** — the largest remaining legacy `{{t.*}}` surface that no active lane
owned (§3), following a pattern proven twice (§5).

## 2. Inventory on `232e33b4` — executable `{{t.*}}` / translation-placeholder usage, by serving owner

"Executable" = the token is substituted at request time by a real serving path. Classification:
**live** (renders to real visitors), **inert-by-design** (literal placeholder text a visitor is
*meant* to see verbatim), **dead** (documentation/prose never rendered), **test** (fixture text).
Counts below were re-derived 2026-08-17 by AST-parsing the exact string constants (method in §8).

| # | Serving owner | Artifact (verified location) | Executable tokens (at base) | Class | Disposition |
|---|---|---|---|---|---|
| 1 | `core/site_identity.py` (`render_page`, `handle`) | root `index.html`, `about.html`, `privacy.html`, `terms.html` | 68 `{{t.*}}` + 26 `{{sitio.*}}` + 2 `{{legal.*}}` + 26 `{{static.*}}` + lang/tema/favicon/fuentes/i18n_bootstrap passthroughs | live (prod path; Jinja twin exists since Tier 4 slice 2) | dual-track by design — legacy stays until the prod `TURNOS_ASGI` cutover decision. **Not a cleanup batch**; do not relitigate |
| 2 | `cuentas/accounts.py` — `_serve_account`, `_serve_test_auth`, `_serve_sso`, `_serve_reset` | `_ACCOUNT_HTML` L1273-1739, `_TEST_AUTH_HTML` L1825-1990, `_SSO_HTML` L2021-2159, `_RESET_HTML` L2183-2258 | **76 `{{t.*}}`** (10+47+8+11; per-constant unique 10/28/8/11 = 57, the 6 `footer.*` keys shared by `_ACCOUNT_HTML`+`_RESET_HTML` → **51 unique: 45 `account.*` + 6 `footer.*`** — §0.11) + **15 `{{static.*}}`** (11 distinct assets) + passthroughs per §4.2's last row | live in BOTH topologies — `cuentas/accounts_router.py` L41/52/53/54 forwards these GETs to legacy `handle()` | **THE BATCH (§4)** |
| 3 | `admin_panel/` legacy shell (`admin.html`, root) | `admin.html` (1 417 lines) | 68 `{{t.*}}` + 42 `{{static.*}}` | live; counts drift-prone (§3: admin-nav + admin-static lanes own this file) | excluded — Tier 5 screen-by-screen decomposition owns it and the **admin-nav/admin-static lanes** are active there (§3) |
| 4 | `patient_web/patient_site.py` — `_shell()` renders `_FOOTER` | `_FOOTER` (starts L1044) | **7** `{{t.footer.*}}` | live, but canonical-language only (no handler/lang threaded through `_shell()` — the sitewide-bootstrap gap, i18n.md "Remaining" item 3); counts drift-prone (§3: cache lane owns this file) | future batch; **cache-lane adjacent** (§3) — deliberately not chosen now |
| 5 | root `admin-legal.js` L134-136 | legal-editor help text | 5 `{{sitio.*}}` tokens inside `<code>` shown to staff | **inert-by-design** — the tokens are the *content* being documented, never substituted | not a target; do not "fix" |
| 6 | `modules/turnos/module.json` L88 | `admin_sections_note` prose | mentions `{{t.admin.shell.nav.reportes}}` in a documentation string | **dead** (manifest note field; rendered nowhere) | not a target |
| 7 | `locales/<lang>/*.json` | translated *values* embedding `{{sitio.*}}` | by namespace (es = en): about ×8, home ×6, privacy ×2, terms ×2, **account ×4**, footer ×2 | live **data**, correctly shaped — resolved by the deliberate second substitution pass (`jinja_env._t` L108-123; legacy order note at `core/site_identity.py:301-305`) | not a target — the mechanism, not the payload. **Batch-relevant:** the 4 in `account.json` are `account.meta.title`/`account.meta.description`/`account.reset.title`/`account.reset.meta_description` — keys used by `/account` and `/account/reset`, both of which DO run `site_identity.render` in legacy (§4.2), so they resolve; none of the 8 `account.sso.*` values embeds one (which is why skipping `site_identity.render` on SSO is safe — §0.6) |
| 8 | test fixtures: `test/test_i18n.py`, `test/test_seo.py`, `test/test_usability_fixes.py`, `test/test_mobile_public_ui.py`, `test/admin-navigation.test.mjs` | inline `{{t.*}}`/`{{static.*}}` strings | none — fixture text | **test** | gates, not targets |

Client-side catalog lookups are a *different* mechanism (the browser-side `T()` twin — i18n.md "Why
JS literals are still untouched"), out of scope for the Jinja series — but the packet must locate
them correctly because they live **inside this batch's own constants**: `_ACCOUNT_HTML`'s inline
`<script>` calls `T('account.dash.…')` etc. — **36 distinct `account.*` keys (25 of them
`account.dash.*`), ALL present in the catalog, 2 of them (`account.register.lede`/
`account.register.title`) also used server-side, so the 34 `locales/*/account.json` keys no
server-side `{{t.*}}` uses are exactly these 36 minus the shared 2** (§0.11). `_RESET_HTML`
carries its own inline `T()` shim (4 keys), `_SSO_HTML` has 3, and `admin-app.js` L77-78 does
date-key lookups. `account-form.js` contains **zero** catalog-key references — but NOT because it
skips `T()`: its **146 `T()` call sites (109 unique literal keys — 144 `account.*` + 2 `date.*`
prefix templates; a raw `\bT(` grep counts 150 = the 146 calls + 1 definition at L45 + 3 comment
mentions at L134/L135/L894 — §0.21)** all use keys OUTSIDE `account.json`, with inline Spanish
fallbacks (§0.15).
Porting rule (§4.3):
the inline `T()` calls are client-side JavaScript — they pass through the Jinja port **byte-for-byte**
and keep working unchanged.

## 3. Lane exclusions and live-lane dependencies (what this packet deliberately does not touch)

Active parallel lanes were identified by a **worktree scan** (`git worktree list`) plus a
**collision scan** (`git diff --name-only 232e33b4` against every live lane worktree, restricted to
this batch's read/write file set), both run 2026-08-17:

| Lane | Branch / worktree(s) at `232e33b4` | Files it owns → excluded here |
|---|---|---|
| cache (services cache) | `glm/services-cache` (`turnos-glm-services-cache`); benchmarks `benchmark/{fable5,glm53,grok46}-services-cache` (`turnos-bench-*`) | `patient_web/catalog.py`, `patient_web/patient_site.py` services rendering, any services-cache module — excludes surface #4 |
| data-pack schema | `glm/data-pack-schema-prep` | `data_packs.py`, `packs/**`, `sitedata.py`, pack schema — untouched (`locales/` is platform-core UI catalog, loaded from repo root by `core/i18n.py`, not pack data) |
| admin nav | `glm/admin-screen-nav-helper` | `admin.html`, root `admin-*.js`, `templates/admin/**`, `admin_panel/**` — excludes surface #3 |
| admin static fingerprinting | `glm/admin-static-fingerprint-final` (worktree `turnos-glm-admin-static-final`, still open at this packet's freeze, re-verified 2026-08-17) | `core/static_fingerprint.py` (grows `MIGRATED`), `templates/admin/solicitudes.html` — **read-only dependency of this batch** (§4.3); landing only ADDS `MIGRATED` entries, so "all 11 assets fingerprinted" survives as a superset, but re-verify unconditionally at execution time (§9.1) |
| **doc truth** | `glm/meta-doc-truth` (worktree `turnos-glm-meta-doc-truth`, base `6c34573e` — still open at this packet's freeze, re-verified 2026-08-17) | **already modifies `cuentas/accounts.py`** (+ `locales/{es,en}/service.json`). This batch treats `accounts.py` as its byte-frozen parity reference — no conflict in files written, but **every line cite below drifts the moment that lane lands**. Execution duty (unconditional, §9.1): after it freezes/merges, re-run the §8 count/cite script before touching anything; symbol names in this packet stay valid |
| server retirement | `glm/server-retirement-slice2` (`turnos-server-retirement-slice2`), `fable/server-retirement-slice3`, `glm/server-retirement-slice4-prep` | `server.py` (also excluded by the task) |

Collision-scan result for the files this batch **writes**: no live lane touches
`cuentas/accounts_router.py`, root `templates/` (outside `templates/admin/`), `test/test_accounts.py`,
`test/test_accounts_jinja.py`, `scripts/route_parity_check.py`, `core/jinja_env.py`,
`core/site_identity_router.py`, `core/theme.py`, `core/i18n.py`, `locales/*/account.json`,
`locales/*/footer.json`, or `ops/demo_installation.py`. `cuentas/**` is written by none of them
(read-only overlap: the doc-truth lane above).

## 4. The batch — exact ownership and scope

**Owner:** the `cuentas` package (platform-core, `admin_panel`-style domain package — not an app
module; `cuentas/accounts_router.py` is platform-core, registered in `routers/platform.py`'s
`ORDERED` (L48) and mounted by the `app.py` loop at L75 — no `module.json` change).
**Shape:** exactly the `core/site_identity_router.py` pattern (Tier 4 slice 2), extended for
per-request context the pilot didn't have (`jinja_env.render(**context)`, added for Tier 5 slice 4).

### 4.1 Files

**Created (4 templates + 1 test):**

1. `templates/account.html` — port of `_ACCOUNT_HTML` (`cuentas/accounts.py` L1273-1739 at base).
2. `templates/account-reset.html` — port of `_RESET_HTML` (L2183-2258).
3. `templates/auth-test.html` — port of `_TEST_AUTH_HTML` (L1825-1990).
4. `templates/auth-sso.html` — port of `_SSO_HTML` (L2021-2159).
   (Flat under root `templates/` — the first `jinja_env.TEMPLATE_DIRS` entry, `core/jinja_env.py`
   L105 (`_DIR` = repo root at L84; `TEMPLATE_DIRS` = [`<repo>/templates`, `<repo>`] —
   `core/templates/` does not exist; the four public-page twins
   `templates/{index,about,privacy,terms}.html` and `templates/admin/*.html` live there today).
   These are public patient pages, not admin screens, so they do not extend
   `templates/admin/base.html`.)
5. `test/test_accounts_jinja.py` — the standing gate, mirroring `test/test_jinja_pilot.py`
   (`_render_legacy` L63 / `_render_new` L74; byte-identity assertions L114-159, both languages,
   plus an "actually English" spot-check like L150-159). Acceptance matrix in §6.

**Edited (2 code files + this doc):**

- `cuentas/accounts_router.py` — replace the four *forwarding* GET registrations
  (`/account` L41, `/account/reset` L52, `/auth/test` L53, `/auth/sso` L54 — all currently
  `module_router.route_via_handle(accounts)` from L39) with native Jinja-rendering endpoints.
  All API routes (L42-49, 55-62) stay forwarding, untouched. The legacy constants and serve
  functions in `cuentas/accounts.py` stay byte-for-byte unchanged — they remain the `TURNOS_ASGI`-off
  implementation and the byte-parity reference.
- `scripts/route_parity_check.py` — the accounts-family gate-state fix in §6(c) (the enabled-run
  env block inside this script; this is a real second edited file — v1 omitted it from this
  list). **v4 re-check: nothing in §6/§9 implies any further file** — the themed pass seeds
  `config/marca`/`config/tema` on the TEST file's own throwaway database (§6(b), §0.14), the
  script gains no new database duty, §6(b) is the created test file, and the only doc edit is
  this one.
- `docs/roadmap/jinja-l10n-accounts-batch.md` (this file) — §1's table gains the batch's row; the
  edit ships inside the frozen diff handed to Codex (§9 step 7, v6 §0.22 — this batch makes no
  commit of its own).

**Explicitly NOT edited:** `cuentas/accounts.py` (any line — including by the doc-truth lane's
conflicts, which resolve by rebase, not by editing here), `server.py`, `core/jinja_env.py` (needs
nothing — `render(**context)` L172 and all four globals already exist), `core/i18n.py`,
`core/theme.py`, `core/static_fingerprint.py` (read-only dependency; see §3),
`locales/**`, `ops/demo_installation.py`, `account-form.js` (needs nothing — zero catalog keys).

### 4.2 The render pipelines to reproduce (the byte-parity contract)

Each page has its OWN post-token pipeline; they are NOT uniform. Verified from the four serve
functions (`_serve_account` L1780-1820, `_serve_test_auth` L1993-2010, `_serve_sso` L2162-2178,
`_serve_reset` L2261-2276):

| Step | `/account` | `/account/reset` | `/auth/test` | `/auth/sso` |
|---|---|---|---|---|
| pre-render gate | `auth_redirect_target(handler, "/account")` → 302 when no session AND renaper mode live (L1781-1785; `auth_redirect_target` L1749) | none | `_test_auth_enabled` else 404 JSON (L1994-1996) | `_test_auth_enabled` else 404 JSON (L2163-2165) |
| `i18n.render` (`{{t.*}}`) | ✓ | ✓ | ✓ | ✓ |
| `static_fingerprint.render` | ✓ | ✓ | ✓ | ✓ |
| `site_identity.render` (`{{sitio.*}}`) | ✓ | ✓ | ✓ | ✗ |
| `inject_html_lang` (`{{lang}}`) | ✓ | ✓ | ✓ | ✓ |
| `inject_bootstrap` (`{{i18n_bootstrap}}`) | ✓ | ✓ | ✓ | ✓ |
| per-request bootstrap inject | **✓** (`__ACCOUNT_BOOTSTRAP__` + `__ACCOUNT_CANONICAL_PATH__` spliced before `</head>`, L1792-1809) | ✗ | ✗ | ✗ |
| `theme.inject` | ✓ | ✓ | ✓ | ✗ |
| demo banner (`demo_installation.inject_html(body, "public", lang=lang, switchable=True)`) | ✓ (L1811) | ✓ (L2269) | **✗** | **✗** |
| response headers | `text/html; charset=utf-8`, `Content-Length`, `Cache-Control: no-store`, optional `Set-Cookie` | same, no cookie | same, no cookie | same, no cookie |
| passthrough tokens in source | lang, tema, i18n_bootstrap, favicon, fuentes | lang, tema, i18n_bootstrap, favicon, fuentes | lang, tema, i18n_bootstrap | lang, i18n_bootstrap |

**The SSO column's ✗s are no-ops, not traps** (§0.6): `theme.inject` early-returns unchanged when
none of `{{tema}}`/`{{fuentes}}`/`{{favicon}}` is present (`core/theme.py` L237-240/L249-251 — the
positional branding `<style>` drop sits inside the token-present branch), and `site_identity.render`
is pure token substitution with no `{{sitio.*}}` left in SSO's output (none of its 8 catalog values
embeds one, §2 row 7). A uniform pipeline that runs both on SSO stays byte-identical. The steps that
are **NOT** interchangeable: the demo banner (positional injection — running it on `/auth/test`/
`/auth/sso` changes bytes), the pre-render gates, the bootstrap splice, and `Set-Cookie`.

Under ASGI, `I18nMiddleware` (via `app.py`) binds `i18n.active()` for the whole request — the twin
reads it once and passes it to every injector, exactly as `site_identity_router._render`
(`core/site_identity_router.py` L81-99) does; `i18n.resolve_lang(handler)` and the middleware must
agree (risk R5).

Language switch: in legacy dispatch `i18n` sits BEFORE `accounts` (`core/routing.py` `GET_PLATFORM`
L82: `… legacy_urls, static_fingerprint, demo_installation, i18n, accounts, …`), so an enabled
`/account?lang=en` never reaches `accounts.handle()` — the intercept is `_maybe_switch_lang`
(`core/i18n.py` L591-611, entered via `handle` L614-625): GET + `?lang=` + `switcher_mode() !=
"off"` + ≥2 enabled languages → 302 to the same path (or "/") with `Set-Cookie: lang=<lang>;
Max-Age=31536000; Path=/; SameSite=Lax` and an EMPTY body; switcher off or a single language
falls through to the handler. **`switcher_mode()` defaults to "off"** (`core/i18n.py` L284-286)
— `LANGUAGES=es,en` alone does NOT enable the switch; the gate must also set
`LANG_SWITCHER_MODE=secret` (or `nav`), §0.13. The twin must reproduce that with the same shim
the pilot uses (`_maybe_lang_switch`,
`core/site_identity_router.py` L72-78, which runs `i18n.handle` on a `LegacyHandlerShim`) — reuse
the function, do not reimplement `_maybe_switch_lang`. Gated in §6(b) (v3: the switch redirect
itself, not just cookie-driven renders).

### 4.3 Token conversion table (per template)

| Legacy token | Jinja form | Notes |
|---|---|---|
| `{{t.*}}` (76 total; 51 unique) | `{{ t('account.meta.title', '<the exact Spanish string>') }}` | All **51** unique keys (45 `account.*` + 6 `footer.*`) verified present in BOTH `locales/es/account.json` + `locales/en/account.json` and `locales/{es,en}/footer.json` — zero missing, zero empty-string values. Fallback = the exact current Spanish so a catalog miss cannot change bytes. Note `_t`'s second pass (`jinja_env._t` L108-123) resolves the `{{sitio.*}}` embedded in 4 of those catalog VALUES (§2 row 7) exactly as legacy's `t`-then-`sitio` order does |
| `{{static.*}}` (15 total; 11 distinct assets: auth-test.js, chat-ui.js, chat-widget.js, dates.js, dependents.js, human-rounds.css, i18n.js, lab-results.js, my-requests.js, nav.js, prep.js) | `{{ static('human-rounds.css') }}` | All 11 verified in `core/static_fingerprint.MIGRATED` at base (imported the module, diffed the sets) — clean drop-in. Superset-safe if the admin-static lane lands first (§3) |
| `{{lang}}` `{{tema}}` `{{i18n_bootstrap}}` `{{favicon}}` `{{fuentes}}` | `{{ '{{lang}}' }}` etc. (escaped-literal passthrough) | Same shape as the pilot templates (e.g. `templates/privacy.html` L2, L6, L23, L46); the post-steps in §4.2 still substitute them |
| inline `<script>`'s client-side `T('account.dash.…')` calls (36 distinct keys) | keep byte-identical | Client-side i18n twin, resolved in the browser — Jinja never touches them (§2) |
| `text.replace("</head>", "<script>window.__ACCOUNT_BOOTSTRAP__=…")` | context vars rendered into a `<script>` block before `</head>` in the template | See risk R2 |
| `/account-form.js?v=20` (accounts.py L1403 AND L2228 — the reference appears in BOTH `_ACCOUNT_HTML` and `_RESET_HTML`) and any other literal `?v=` reference | keep byte-identical | `account-form.js` (repo root) is NOT in `MIGRATED`; fingerprinting it is OUT of scope (the `?v=` ritual + `vbump-drift-guard.py` still own it) |

## 5. Parity risks (each with its failure mode)

1. **Non-uniform steps — but only the positional ones bite.** The four pipelines differ in
   `site_identity.render`/`theme.inject` (SSO skips both) — harmless, because both are no-ops on a
   token-free text (§4.2). The differences that DO change bytes if uniformized: demo banner on
   `/auth/test`//`/auth/sso` (positional inject, absent today), the bootstrap splice (`/account`
   only), `Set-Cookie` (`/account` only), and the pre-render gates. → Encode the §4.2 matrix per
   route; the gate must run at least once with the `catalogo` config rows seeded (`config/marca`
   color set — and optionally a non-default `config/tema`) so `_branding_style()` is non-empty and
   the branding `<style>` branch is exercised on the three themed pages (§0.9: theme and color
   are DB rows, not pack attributes). **The seeding lives in §6(b)'s hermetic themed case ONLY** —
   the unittest's per-run throwaway DB, never the parity script and never whatever `DATABASE_URL`
   a shell happens to export (§0.14) — and only AFTER `PerModuleDb.set_up()` reports success:
   no `have_db`, no write at all (§0.19).
2. **Bootstrap JSON escaping.** The legacy splice pre-escapes the payload
   (`json.dumps(...).replace("<", "\\u003c")`, L1804-1805) and injects it verbatim. Jinja runs with
   `autoescape=False` (`core/jinja_env.py` L155), so a context string renders raw — the twin MUST
   keep the `<` → `\\u003c` replacement at the call site (XSS-relevant: the payload contains
   user-typed names) and must not double-escape. Gate: §6(b) escaping case.
3. **Auth redirect semantics — already gated on the legacy side; the twin must match.** `/account`
   with no session on a renaper-enabled site 302s to the RENAPER entry (`auth_redirect_target`,
   `cuentas/accounts.py` L1749+; anti-lockout note); password-mode sites render the form — and that
   no-session render is ALREADY gated by `test/test_accounts.py::test_cuenta_page_html` (L359) and
   the authenticated first-response bootstrap by
   `test_cuenta_autenticada_embebe_usuario_y_turnos_en_la_primera_respuesta` (L369). The NEW gate
   adds the twin's side of both, plus `Location` equality on the 302 (§6(b)) — a naive twin renders
   where legacy redirects (or vice versa), only on renaper-enabled installs (the exact recipe that
   makes `metodos()["renaper"]` True is §6(b)'s Status bullet — v4).
4. **Gated pages in the parity harness — rows exist but run gate-off; the comparison must use
   the SAME loopback peer on both sides (v5, §0.16/§0.17).** `/auth/test` and `/auth/sso`
   ARE rows in `ESCENARIOS_ACCOUNTS` (`scripts/route_parity_check.py` L587-612) — v1 wrongly said
   they were absent — but under the default password auth mode `_test_auth_enabled` is False
   (`cuentas/accounts.py` L526-552: requires `_auth_mode()=="renaper"` AND
   `_renaper_provider()=="stub"`, or `_stub_desplegado()`, plus a loopback/dev-host peer), so both
   halves 404 today: green and worthless. Fix (v5): the accounts family runs with **matched
   loopback peers** — an accounts-scoped legacy shim peer `("127.0.0.1", 54321)` (NOT the shared
   `_run_legacy_intake`'s `203.0.113.5`, whose TEST-NET-3 classification is Python-version
   dependent) AND a dedicated `TestClient(app, client=("127.0.0.1", 54321))` for the FastAPI
   half (NOT the shared default TestClient, whose peer is the literal `testclient`) — in BOTH
   gate states, with the enabled pass setting only `TURNOS_AUTH_MODE=renaper` +
   `TURNOS_RENAPER_PROVIDER=stub` (both peers then take the `peer.is_loopback` branch — no
   secret, no stub-deployed, no `host_de_dev`). Probe-proven at base: enabled → all four pages
   200/200 byte-identical in BOTH languages; the mismatched pairing the review banned
   (`203.0.113.5` vs `testclient`) instead yields legacy 200 vs FastAPI 404 forever — the
   permanent DIFF reproduced live (§8 v5 probe C). NEVER compare those two peers. Two MANDATORY
   side conditions: (a) every env block that flips `TURNOS_AUTH_MODE` must ALSO clear
   `auth_mode._cache` (`auth_mode._cache["v"] = None; ["t"] = 0.0`, the `test/test_auth_mode.py`
   L38-39 pattern) — `config._reset_cache_para_tests()` does NOT reset it and a stale
   `password` cached within the last 3 s silently reverts the run to gate-off (§0.17); (b) the
   block is set/popped around the family run, the same shape as `_correr_escenarios_paginas`'s
   `LANGUAGES` (L843-861), with the cache cleared at both edges. `TURNOS_RENAPER_STUB_DEPLOYED=1`
   is an OPTIONAL second enabled pass (peer-independent — it short-circuits before peer
   resolution, so it validates under any pairing, but proves nothing about peer symmetry).
   The unittest side MAY keep the `_LangHandler` loopback+marker recipe — there both renders go
   through the SAME handler object in one process, so the rule is the same for both by
   construction (the one place a signed marker is a legitimate "explicitly valid" peer proof,
   §0.16).
5. **Language resolution equality.** Legacy calls `i18n.resolve_lang(handler)`; the ASGI twin
   inherits `i18n.active()` bound by `I18nMiddleware`. Any cookie/Accept-Language case where the two
   disagree shifts the whole page to the other catalog. `route_parity_check` drives only the
   canonical (es) case — the es+en byte-identity gate in the unittest (both driven the way
   `test_jinja_pilot.py` L142-159 drives both languages) is the proof; assert en renders actually
   contain English (pilot L150-159) so a silent fallback to es fails loudly. The four pages' legacy
   en/es behavior is already pinned by `test_accounts.py` L681-760.
6. **Demo-access flow must keep working unchanged.** `ops/demo_installation.py` L356-360 calls
   `accounts._serve_account(handler, bootstrap_user=..., set_cookie=..., canonical_path="/account")`
   directly — through the ASGI catch-all proxy it still reaches the LEGACY renderer
   (`/account/demo-access` is not in `accounts_router`'s route list; and `demo_installation`
   precedes `accounts` in legacy dispatch — the `/account/demo-access` route is claimed at
   `ops/demo_installation.py:347`). Exactly two production callers of `_serve_account` exist
   (`cuentas/accounts.py:432` and this one; the wider repo census adds only test-side references —
   a direct call at `test/test_accounts.py:385` and the mock in
   `test/test_demo_installation.py:140`). Since the batch leaves `accounts.py` untouched, this
   flow is safe by construction — but the router change must NOT register `/account/demo-access`,
   and the twin's `/account` endpoint must accept the bootstrap/canonical/set-cookie context so a
   later batch can migrate demo-access natively without a second template.
7. **Header comparison scope — know what the parity script does and does not prove.**
   `route_parity_check.py` compares **status + normalized body + header KEY SETS** (L418-428) and
   never header values; `_IGNORAR_HEADERS = {date, server, content-length, set-cookie}` (L193-197).
   So `Content-Type` and `Cache-Control` are compared as *present-on-both-sides keys* only, and
   `Set-Cookie`/`Content-Length` are excluded by design (FastAPI computes the length; the cookie
   token embeds a timestamp). The value-level assertions — `Content-Type: text/html; charset=utf-8`
   exact, `Cache-Control: no-store` exact, cookie name/attributes on the bootstrap path — belong to
   the unittest gate (§6(b)); do not trust a green parity run alone for headers.

## 6. Gates

**(a) Existing scenarios — exist today, stay green, untouched.** `test/test_accounts.py`:
no-session page render (L359), authenticated first-response **bootstrap embed** (L369-379 — the
cookie on that response is asserted by the demo-URL tests at L388/393, and
`test_cookie_contract` L149 pins `_set_cookie`'s output directly), demo banner on `/account` and
`/account/reset` + the no-banner case (L397/409/418), and class `TestI18nWiring` (L650-761;
section comment L603-609) driving **all four pages in BOTH languages** with the env recipe at
L656-668 (`_ENV_KEYS` L656-658; `LANGUAGES=es,en` L662, `TURNOS_AUTH_MODE=renaper` L663,
`TURNOS_RENAPER_PROVIDER=stub` L664, `TURNOS_PROXY_TRUST_SECRET` L668; loopback peer via
`_LangHandler` L625-629), incl. `test_test_auth_y_sso_resuelven_sus_assets_fingerprinted` L715 —
which therefore sees real HTML under the enabled gate, not 404s. `test/test_seo.py` L113-121
asserts all four account/auth pages are `noindex`, reading the four `_*_HTML` **constants**
(`from cuentas import accounts` L8/L115-118) — not template files, so the twin cannot break it.
`test/test_demo_installation.py` mocks `accounts._serve_account` (signature unchanged).
`scripts/route_parity_check.py` `ESCENARIOS_ACCOUNTS` (L587-613) already rows all four GETs
(`/account` L588, `/account/reset` L601, `/auth/test` L602, `/auth/sso` L603). Also stay green:
`test/test_renaper_auth.py`, `test/test_cache_headers.py` (static assets only — it has NO
`/account` coverage, so §6(b)'s `Cache-Control` assert is net-new), `test/test_i18n.py`,
`test/test_jinja_pilot.py`, `test/test_admin_screen_contract.py`. NO existing test anywhere
drives the `?lang=` switch redirect on these four routes (§0.8) — §6(b)'s switch gate is net-new
too.

**(b) NEW standing gate: `test/test_accounts_jinja.py`**, mirroring `test/test_jinja_pilot.py` —
for each of the four pages, legacy serve-function output vs `jinja_env.render()` + §4.2 steps,
with this acceptance matrix:

- **Byte parity:** byte-identical output in canonical Spanish AND in English (`Cookie: lang=en`,
  driven the way the pilot drives both languages — `test/test_jinja_pilot.py` L64/L79); one
  "actually English" assertion per page (pilot L150-159 shape). This includes `/auth/test`//
  `/auth/sso` rendered under the enabled recipe (either the `_LangHandler` recipe or the
  stub-deployed env — the symmetry bullet below) — the explicit test-auth-enabled HTML coverage,
  not just a status code.
- **Status:** 200 happy path on all four — explicitly including **password-mode anonymous
  `/account`, which renders the form HTML with status 200 and NO redirect on both sides**; 302
  for no-session `/account` on a renaper-ENTRY site, and 404 JSON for `/auth/test`//`/auth/sso`
  with the gate off, 200 with it on. The 302 needs MORE than "renaper+stub": `auth_redirect_target`
  returns None unless `auth_renaper.metodos()["renaper"]` is True, i.e. `TURNOS_AUTH_METHODS`
  includes `renaper` AND (`TURNOS_FAKE_RENAPER=1` OR real client id+secret) —
  `cuentas/auth_renaper.py` L123-147. Probe-proven at base (§8 v4 probe B): with only
  AUTH_MODE=renaper + provider=stub the page renders 200 (no redirect); adding
  `TURNOS_AUTH_METHODS=password,renaper` + `TURNOS_FAKE_RENAPER=1` yields, byte-equal on both
  sides: status 302, `Location: /auth/renaper?next=%2Faccount`, EMPTY body, `Content-Length: 0`,
  and NO `Set-Cookie` (the lang-switch 302 below is the only redirect that sets a cookie).
  **The 302's full header set is pinned (v5, §0.18): exactly `Location` + `Content-Length: 0`,
  nothing else — `Content-Type`, `Cache-Control` and `Set-Cookie` all ABSENT** (probe-proven
  identical on the legacy wire and the FastAPI wire at base, §8 v5 probe E — `_redirect`
  `cuentas/accounts.py` L1742-1746).
- **404 contract, exact bytes and exact headers (v5, §0.18):** gate-off `/auth/test`//
  `/auth/sso` return, byte-equal on both sides, body `{"error": "test_auth_no_disponible"}`
  (36 bytes, that exact spacing — `_json` uses `json.dumps` default separators,
  `cuentas/accounts.py` L119-128), `Content-Type: application/json; charset=utf-8`,
  `Cache-Control: no-store`, `Content-Length: 36`, and NO other header (no `Set-Cookie`).
  Probe-proven byte- and header-exact on BOTH wires at base (§8 v5 probe F). A twin that raises
  FastAPI's `HTTPException(404, ...)` breaks this silently — the framework wraps `detail` in a
  `{"detail": ...}` envelope, the exact drift class the parity script already met once
  (`scripts/route_parity_check.py` L377-392).
- **Headers:** exact-value equality for `Content-Type: text/html; charset=utf-8` and
  `Cache-Control: no-store` on all four (net-new — no existing test asserts `Cache-Control` on
  `/account`); `Set-Cookie` PRESENCE + name/attribute parity on the bootstrap path (values
  intentionally non-comparable — mirror the parity script's rationale). Together with the two
  bullets above, every response dimension the parity script cannot prove is asserted: status,
  body bytes, content-type, cache-control, location, set-cookie.
- **Test-auth rule symmetry (v5, §0.16/§0.17):** in THIS unittest both renders go through the
  SAME handler object in one process, so the existing `_LangHandler` recipe (loopback socket
  peer + signed marker + `TURNOS_PROXY_TRUST_SECRET`, env recipe L656-668) satisfies the rule
  identically for both sides — valid under `TURNOS_ASGI` off (socket peer rules, marker
  ignored) AND on (the marker verifies — the one legitimate signed-proxy-marker proof, §0.16);
  the two-peer asymmetry that forces the parity script onto matched synthetic peers cannot
  arise here. Any setUp/tearDown that flips `TURNOS_AUTH_MODE` must ALSO clear
  `auth_mode._cache` (`auth_mode._cache["v"] = None; ["t"] = 0.0` — `test/test_auth_mode.py`
  L38-39's pattern; `config._reset_cache_para_tests()` does not touch it, §0.17). Optionally
  also run one pass with the stub-deployed env to mirror §6(c)'s optional second enabled run.
- **Language-switch redirect:** for each of the four routes, `GET <route>?lang=en` with
  **`LANGUAGES=es,en` AND `LANG_SWITCHER_MODE=secret`** (§0.13 — `LANGUAGES` alone leaves the
  switcher off and the gate compares 200-vs-200) → the SAME 302 on both sides: `Location` = the
  requested path, EMPTY body, `Set-Cookie: lang=en; Max-Age=31536000; Path=/; SameSite=Lax`
  (probe-proven on all four routes, §8 v4 probe C); **full header set pinned (v5, §0.18):
  exactly `Location` + that `Set-Cookie` + `Content-Length: 0` — `Content-Type` and
  `Cache-Control` ABSENT** (probe-proven identical on the legacy wire and on the pilot twin's
  real `_maybe_lang_switch` → `to_response()` path, §8 v5 probe G); and the post-switch render
  uses the en catalog (the byte-parity bullet's en case). No existing test covers this on any of
  the four routes (§0.8).
- **Themed pass (hermetic, v5 — §0.14/§0.19):** on the module's OWN per-run throwaway database
  (`test/_dbtest.py`'s `PerModuleDb`, the pattern every DB-backed test file uses), seed through
  the modules' own writers — `branding.save_color("#0a5c36")` and `theme.save("humanrounds")`
  (`patient_web/branding.py` L117-129, `core/theme.py` L98-108; both invalidate their own 3 s
  caches, so no manual cache reset) — then assert all four pages byte-identical in BOTH
  languages with `_branding_style()` non-empty and the non-default theme id active (exercises
  the branding `<style>` drop and the `{{tema}}`/`{{fuentes}}` branches, §5.1). **Guard order
  is the contract (v5): the writers run ONLY after `PerModuleDb.set_up()` set `have_db = True`
  — i.e. strictly against the run-scoped throwaway (`run_scoped`, `test/_dbtest.py` L87-101,
  token-unique per run; `set_up` L207-236 never writes rows into a real database — its only
  server-level DDL is the DROP/CREATE of the token-named throwaway, after which
  `os.environ["DATABASE_URL"]` points at it; `tear_down` L238-275 drops it WITH (FORCE) after
  closing the pools — deterministic teardown, crash leftovers reclaimed by the next run's
  `reclaim_stale_databases` L109-166). No `have_db` → visible `skipTest` and ZERO database
  writes — it must NEVER fall back to seeding an inherited or exported `DATABASE_URL` (which
  `db._db_url()` `core/db.py` L1139-1143 prefers over everything, §0.14). The explicit
  alternative: pure mocks (patch the reads — no database of any kind).**
- **Bootstrap escaping:** a `bootstrap_user` whose name contains `</script>`, a bare `<`, and
  non-ASCII renders the payload with `<` → `\\u003c` on both sides, byte-identical — no raw `<` or
  `</script>` inside the serialized payload, no double-escape (a literal `\\u003c` sequence
  re-escaped). The splice point sits inside a `<script>` block, so `</script>` is the actual
  breakout string, not a theoretical one.
- **Demo:** with demo mode on, the banner is byte-identical on `/account` and `/account/reset` and
  ABSENT on `/auth/test`//`/auth/sso`; `/account/demo-access` keeps reaching the legacy renderer
  (no such route in the router).

**(c) One-shot migration audit: `scripts/route_parity_check.py`** — the accounts family's four GET
rows (already present: `/account` L588, `/account/reset` L601, `/auth/test` L602, `/auth/sso` L603
inside `ESCENARIOS_ACCOUNTS` L587-613) must run in BOTH gate states with **the SAME loopback peer
on both sides (v5, §0.16)**: an accounts-scoped legacy runner (or a peer parameter on
`_run_legacy_intake`) building the shim with client `("127.0.0.1", 54321)` — never the shared
`203.0.113.5` — and a dedicated `TestClient(app, client=("127.0.0.1", 54321))` for the family
(never the shared default TestClient at `main()` L866, whose peer is the literal `testclient`;
the installed starlette 1.4.1 supports `client=`). The two runs: (1) as today, password mode
(gate-off — both sides then 404 on `/auth/test`//`/auth/sso` for the RIGHT reason: the auth-mode
check, not a peer accident); (2) enabled, with ONLY `TURNOS_AUTH_MODE=renaper` +
`TURNOS_RENAPER_PROVIDER=stub` — both peers then take `peer.is_loopback` and the rows compare
real HTML. Clone the script's existing env-block pattern (`_correr_escenarios_paginas` sets/pops
`LANGUAGES` at L843-861; the accounts family is invoked at L871) and add, at BOTH edges of the
block, `config._reset_cache_para_tests()` AND the `auth_mode._cache` clear
(`auth_mode._cache["v"] = None; ["t"] = 0.0`, §0.17 — without it a `password` cached within the
previous 3 s silently reverts the "enabled" run to gate-off 404/404). An optional third run with
`TURNOS_RENAPER_STUB_DEPLOYED=1` (peer-independent cross-check) may be added but proves nothing
about peer symmetry. **Never compare `203.0.113.5` against `testclient`** — probe-proven at base
that under renaper+stub that pairing is a permanent legacy-200-vs-FastAPI-404 DIFF (§8 v5 probe
C), and probe-proven that the matched-peer enabled run is 200/200 byte-identical with header key
sets equal on all four rows, in BOTH languages (§8 v5 probe B). **The script gains NO new
database duty** (v4, §0.14; v5 §0.19 keeps it): no `catalogo` seeding here — an in-script upsert
could land in an inherited real `DATABASE_URL` (which `db._db_url()` prefers over the script's
`config.ENV_PATH` shield), and the accounts family is already DB-free at base (all 21 rows green
with no DB, §8 v4). The themed proof is §6(b)'s hermetic unittest case alone. The rows keep
passing once the routes render natively — they then exercise the Jinja path. The audit is RUN
through the accounts-only offline proof (§0.23) — never a bare `python3
scripts/route_parity_check.py` (`main()`, every family, non-hermetic, §8 v4) — and its empty
diff travels with the frozen-diff handoff (§0.22), honoring the script's own evidence contract
(L10-16) without any commit by this batch.

**Commands** (full suite, per repo convention):
`TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s test -p "test_*.py"`
and `node --test test/*.test.mjs`; then the **accounts-only offline parity proof (v6, §0.23)** — a
scratch driver in the executor's job dir, NEVER a bare `python3 scripts/route_parity_check.py`
(that is `main()`, every family — and past the accounts family its rows touch an inherited real
`DATABASE_URL`, §8 v4). The driver, in this exact order:

1. Scrub the DB env AND pin the env-file channel BEFORE any repo import — REMOVE both
   inherited database variables explicitly: `os.environ.pop("DATABASE_URL", None)` and
   `os.environ.pop("TEST_DATABASE_URL", None)` (v8, §0.26 — removal, not masking: the v6/v7
   port-9 sentinel still dialed `127.0.0.1:9` and only failed at connect time), then
   `os.environ["TURNOS_ENV_FILE"] = os.path.join(<job dir>, "turnos-env-file-absent")` — an
   explicitly nonexistent scratch path that overwrites any inherited value before any repo
   import, so `core.config._env_path()` (`os.environ.get("TURNOS_ENV_FILE") or ENV_PATH`,
   `core/config.py` L80-87) can never resolve a real env file and `_dotenv()` parses nothing
   (v7, §0.24). Net effect, by construction: `db._db_url()` (`core/db.py` L1139-1143 —
   exported var, else env-file value, else empty; no hardcoded default) resolves EMPTY and
   `db._connect()` raises `DbUnavailable("DATABASE_URL no configurada")` (L1361-1363) before
   any pool or socket — the audit cannot contact or mutate any live or development database.
2. Import the script out-of-tree — `importlib.util.spec_from_file_location("rpc",
   "scripts/route_parity_check.py")` (its module-level shields `TURNOS_FAKE_AI`/
   `TURNOS_SKIP_HSI`/`config.ENV_PATH = ""`, L134-138, apply on exec — necessary but NOT
   sufficient: `config.ENV_PATH = ""` blanks only the fallback side of `_env_path()`'s `or`,
   so step 1's `TURNOS_ENV_FILE` pin is what actually closes the env-file channel, §0.24).
3. Build the §6(c) matched-peer client — `TestClient(rpc.app_module.app, client=("127.0.0.1",
   54321))` (starlette 1.4.1 verified, §0.16).
4. Run ONLY the accounts family — the runner §6(c)'s edit adds (both gate states inside); at
   packet-freeze time, before that edit exists, the same proof validates the harness via
   `rpc._correr_escenarios_intake(client, rpc.ESCENARIOS_ACCOUNTS)` (the standalone seam
   `main()` itself calls at its L871) — and NEVER `rpc.main()`.

Exit 0 with zero `DIFF` lines = the empty-diff evidence carried into the §9 step-7 handoff.
Frontend suites are unaffected (no JS/CSS changes, no `?v=` bumps, no `MIGRATED` additions).

## 7. Non-goals (kept working / out of scope)

- `cuentas/accounts.py`'s constants and serve functions stay — they ARE the legacy implementation
  and the parity reference until the prod `TURNOS_ASGI` cutover.
- The 34 client-side `account.*` catalog keys (25 `account.dash.*`) consumed by the inline `T()`
  calls inside `_ACCOUNT_HTML`/`_SSO_HTML`/`_RESET_HTML`, and the JS `T()` mechanism — untouched,
  ported verbatim.
- Fingerprinting `account-form.js` (drops the `?v=20`) — separate concern, not this batch.
- `patient_site.py` footer/lang threading, `admin.html`, the four public pages' legacy twin —
  later batches / other lanes (§2, §3).
- No `module.json`, `packs/`, `locales/`, migration, or schema change of any kind.

## 8. Validation log (how each cited fact was checked)

**v8 pass, 2026-08-18, on `232e33b4`** (doc-only correction — the sole edit is this file; nothing
staged/committed/pushed/merged, no stash/reset, no index write of any kind, no network, no live
service, no database contacted or mutated (the v8 pass runs no repo code — static reads and doc
edits only), and no secret, PHI or credential read or used):

- **Executor metadata (recorded for the handoff):** z.ai GLM, model `glm-5.3`, driven through
  the Claude Code CLI harness (GLM fleet config `~/.claude-glm`), background session on worktree
  `turnos-glm-jinja-next-batch-prep` (branch `glm/jinja-next-batch-prep`; base `232e33b4`
  re-confirmed via `git log --oneline -1`). Model id as reported by the session environment; no
  API keys inspected.
- **Mechanism grounding for §0.26 (static reads at base):** `core/db.py` — `_db_url()` L1139-1143
  (`os.environ.get("DATABASE_URL")` first; only if empty, the env-file value; else returns EMPTY
  — no hardcoded localhost default anywhere in the resolution), `_connect()` L1361-1363 (`url =
  _db_url(); if not url: raise DbUnavailable("DATABASE_URL no configurada")` — the raise sits
  BEFORE `_get_pool`, so no pool and no socket exist when the URL is empty), `_get_pool`
  `reconnect_timeout=10` L1197 + docstring L1166-1177 (the retry backoff a set-but-dead URL pays
  — exactly what the port-9 sentinel still caused; a popped variable never reaches this code);
  `_load_env_file()` L1136 (the only env-file channel `_db_url` consults, and §0.24's
  `TURNOS_ENV_FILE` pin already closes it); `core/config.py` L80-87/L100-129 re-read, unchanged
  from v7. `scripts/route_parity_check.py` re-read at the same lines v6 pinned — shields
  L134-138, `import app as app_module` L190, `_correr_escenarios_intake` def L396, `main()` L864
  with the shared default TestClient at L866 and the accounts family invoked at L871 — so every
  cite §6 Commands steps 2-4 reuse still holds at base. Empirical compatibility of a popped
  `DATABASE_URL` with the accounts family: the §8 v4 record's own offline run (`env -u
  DATABASE_URL`) was green through all 21 accounts rows with the variable absent.
- **Static checks on the corrected doc (safe, offline):** (a) instruction sweep re-run
  (`rg -ni "commit|push|merge|land|deploy|stash|reset|git add"` over the final file, after the
  last edit) — every hit inside v8-added text is a prohibition/preservation statement ("never
  commits, pushes, merges, deploys or writes ANY Git state", "no `git add` in any form", the §0.25
  rule and its rationale, this block's own preamble); every other hit is pre-existing and already
  classified by the v6/v7 sweeps (lane-state observations, historical audit records,
  removal/prohibition statements, the `TURNOS_RENAPER_STUB_DEPLOYED` env-var name); zero
  operative landing or Git-mutation instructions; (b) the only freeze mechanics now specified
  are read-only (`git diff`, `git diff --no-index`, `git status --porcelain`, `git log
  --oneline -1` — none writes the index, refs or worktree Git state); (c) `git status
  --porcelain` — exactly one untracked file (`docs/roadmap/jinja-l10n-accounts-batch.md`), no
  production or test file touched, base unchanged at `232e33b4`; (d) lane re-verification —
  `git worktree list` shows BOTH dependency lanes STILL OPEN (`glm/meta-doc-truth` at `6c34573e`,
  `glm/admin-static-fingerprint-final` at `232e33b4`), so §9.1's blocking precondition and
  fresh-count re-derivation duty stand exactly as v5 froze them.
- **Design preservation check:** the v8 pass edited only these locations — the Status header's
  version and appended v8 sentence, §0's heading, §0.23's step-(1) supersession clause, the new
  §0.25/§0.26, §6 Commands step 1, this §8 v8 block, and §9 step 7. Everything else — §1-§7
  functional content, the accepted 51-key/static inventory (§2/§4.3), the §4.2 parity pipeline
  matrix, every §5 risk, every §6(b)/§6(c) gate and probe record, §9 steps 1-6 with the
  Meta/admin-static blocking precondition, and every §0.1-§0.24 correction — is v7 text carried
  over untouched. The accounts-only route-parity path (§6 Commands steps 2-4) keeps its
  structure; only step 1's scrub mechanics changed (pop-both + env-file pin, sentinel removed).

**v7 pass, 2026-08-18, on `232e33b4`** (doc-only correction — the sole edit is this file; nothing
staged/committed/pushed/merged, no stash/reset, no network, no live service, no database touched
(the v7 pass runs no repo code — static reads and doc edits only), and no secret, PHI or
credential read or used):

- **Executor metadata (recorded for the handoff):** z.ai GLM, model `glm-5.3`, driven through
  the Claude Code CLI harness (GLM fleet config `~/.claude-glm`), background session on worktree
  `turnos-glm-jinja-next-batch-prep` (branch `glm/jinja-next-batch-prep`; base `232e33b4`).
  Model id as reported by the session environment; no API keys inspected.
- **Mechanism grounding for §0.24 (static reads at base):** `core/config.py` — `_env_path()`
  L80-87 (`return os.environ.get("TURNOS_ENV_FILE") or ENV_PATH`: the process env var REPLACES
  the effective env-file path; blanking the `ENV_PATH` module global does not touch the left
  operand), `env_file_path()` L90-97 (the public accessor), `_dotenv()` L100-129 (opens
  `_env_path()`'s result and caches by (path, mtime) in `_dotenv_cache` L76; `os.path.getmtime`
  OSError → mtime None → `env = {}` at L106-108/L115-116 — the fail-closed shape the pin relies
  on), `get()` L584-627 (environ consult L610-616 PRECEDES the file consult L618-625, so only
  keys the driver itself exports are safe from an inherited file; DB-override tier L594-608
  sits above both and is irrelevant offline), module docstring L37-46 (the E2E-harness purpose
  of `TURNOS_ENV_FILE` and the "(o a uno inexistente)" nonexistent-file pattern). Adoption
  evidence that an inherited value is real, not hypothetical: `Dockerfile` L65 ships
  `ENV TURNOS_ENV_FILE=/app/data/turnos.env` in production images, and `test/e2e/lib/
  harness.mjs` L17/L251/L300 is the setting's intended user. `scripts/route_parity_check.py`
  shields re-read at L134-138 — L138 is the `config.ENV_PATH = ""` line, and an rg over the
  script returns ZERO `TURNOS_ENV_FILE` mentions: the script's shield provably cannot close
  this channel, exactly as the review stated.
- **Static checks on the corrected doc (safe, offline):** (a) instruction sweep re-run
  (`rg -ni "commit|push|merge|land|deploy"` over the final file, after the last edit) — every
  hit inside v7-added text is a preservation or prohibition statement (the Status header's and
  §0.24's "frozen-diff/no-commit handoff … unchanged", and this block's own preamble and
  classification language); every other hit is pre-existing and already classified by the v6
  sweep (lane-state observations, historical audit records, removal/prohibition statements, and
  the `TURNOS_RENAPER_STUB_DEPLOYED` env-var name); zero operative landing instructions; (b) new-span audit — the only path-like mentions v7 introduces are `Dockerfile`
  (exists at repo root) and the `<job dir>/turnos-env-file-absent` scratch pin (deliberately
  nonexistent by design, outside the repo); no new repo path; (c) `git status --porcelain` —
  exactly one untracked file (`docs/roadmap/jinja-l10n-accounts-batch.md`), no production or
  test file touched; (d) preservation — §0.22, §9 steps 6-7, §6 Commands steps 3-4 and §6(c)
  are byte-identical to v6 (the accounts-only route-parity path and the frozen-diff/no-commit
  handoff carry over unchanged), and §6 Commands step 1 now performs BOTH scrubs (DB env +
  `TURNOS_ENV_FILE` pin) before step 2's first repo import.
- **Design preservation check:** the v7 pass edited only these locations — the Status header's
  version and appended v7 sentence, §0's heading, §0.23's step-(1) sentence, the new §0.24,
  §6's Commands steps 1-2, and this §8 v7 block. Everything else — §1-§7 functional content,
  all gates, all probe records, every §0.1-§0.23 correction — is v6 text carried over
  untouched.

**v6 pass, 2026-08-18, on `232e33b4`** (doc-only correction — the sole edit is this file; nothing
staged/committed/pushed/merged, no stash/reset, no network, no live service, no database touched
(the v6 pass runs no repo code — static reads and doc edits only), and no secret, PHI or
credential read or used):

- **Executor metadata (recorded for the handoff):** z.ai GLM, model `glm-5.3`, driven through
  the Claude Code CLI harness (GLM fleet config `~/.claude-glm`), background session on worktree
  `turnos-glm-jinja-next-batch-prep` (branch `glm/jinja-next-batch-prep`; base `232e33b4`
  confirmed via `git log --oneline -1`; `git status --porcelain` shows this file as the only
  change, untracked). Model id as reported by the session environment; no API keys inspected
  (env probes for model variables are guard-blocked on this host — the harness metadata is the
  source).
- **Mechanism grounding for §0.23's driver (static reads of `scripts/route_parity_check.py` at
  base):** shields at L134-138 (`TURNOS_FAKE_AI`/`TURNOS_SKIP_HSI` setdefault +
  `config.ENV_PATH = ""`); `import app as app_module` at L190; the generic family runner
  `_correr_escenarios_intake(client, escenarios=...)` defined L396-437; the accounts family
  invoked as `_correr_escenarios_intake(client, ESCENARIOS_ACCOUNTS)` at L871 inside `main()`
  (L864+) — i.e. an accounts-only, no-`main()` seam already exists in the current script, so
  §0.23's proof needs no script change to run at freeze time; the §6(c) edit remains what makes
  it cover BOTH gate states.
- **Static checks on the corrected doc (safe, offline):** (a) instruction sweep —
  `grep -niE 'commit|push|merge|land|deploy|PR'` over the final file; every remaining hit is
  one of: a dependency-lane state observation (§0.20/§3/§9.1 — other lanes' freeze/merge), a
  historical v1-v5 audit record, or a v6 removal/prohibition statement; zero operative landing
  instructions remain; (b) backticked-path existence sweep — 470 unique spans, 73 path-like,
  5 §4.1 planned creations, and 17 "missing" that are all bare-filename/route-path prose forms
  (`accounts.py`, `account.json`, `/about.html`, …) whose directory-qualified twins all resolve
  (every repo-relative path v6 itself touches exists; no new path was introduced beyond ones
  already present); (c) `git status --porcelain` — exactly one untracked file
  (`docs/roadmap/jinja-l10n-accounts-batch.md`), no production or test file touched; (d) a
  `route_parity_check.py`-mention audit — every occurrence is a line cite, the §0.23/§6(c)/§6
  Commands ban context, §4.1/§9-step-5 edit instructions, or a §8 historical record; no
  operative whole-script invocation remains anywhere in the packet.
- **Design preservation check:** the v6 pass edited only these locations — the Status header's
  version line and appended v6 sentence, §0's heading, the new §0.22/§0.23, §4.1's doc bullet,
  §6(c)'s closing sentences, §6's Commands block, this §8 v6 block, one supersession note inside
  the §8 v4 record, and §9's title + steps 6-7. Everything else — §1-§8 functional content, all
  gates, all probe records, every §0.1-§0.21 correction — is v5 text carried over untouched.

**v5 pass, 2026-08-17, on `232e33b4`** (verification-only — the sole edit is this file; every
probe below ran OFFLINE and in-process from the session job dir, none committed, no network, no
live service, `DATABASE_URL` pinned to a dead loopback port (`127.0.0.1:9`) so no real database
was reachable, `TEST_DATABASE_URL` unset; the probes replicate `scripts/route_parity_check.py`'s
own shields — `TURNOS_FAKE_AI`, `TURNOS_SKIP_HSI`, `config.ENV_PATH = ""` — and drive BOTH real
transports: the legacy `accounts.handle()` on a `LegacyHandlerShim`, and the FastAPI round-trip
through `app.app` on a TestClient):

- **Peer/transport matrix (§0.16).** Six states probed, all four pages × both languages where
  noted: **(A)** password default + matched loopback peers → 200/200 `/account`+`/account/reset`
  (34564/7620 bytes, equal), 404/404 byte-identical JSON on `/auth/test`+`/auth/sso`; **(B)**
  renaper+stub ONLY + matched loopback peers (`127.0.0.1:54321` both sides) → all four pages
  200/200, byte-identical bodies (11987/8039 bytes on test/sso) and equal header key sets, BOTH
  languages; **(C)** the review's banned pairing (legacy `203.0.113.5` vs TestClient default
  `testclient`) under renaper+stub → `/auth/test`+`/auth/sso` legacy **200** vs FastAPI **404**,
  permanent DIFF reproduced live — and the legacy 200 rides the TEST-NET-3 non-global + dev-Host
  branch (Python-version-dependent); **(D)** v4's stub-deployed recipe under the mismatched
  peers → all green (peer-independent, demoted to optional); **starlette 1.4.1 verified to
  accept `TestClient(client=…)`** — the FastAPI peer under (B) is genuinely `127.0.0.1` via
  `LegacyHandlerShim.from_request` L129-138.
- **The `auth_mode` cache discovery (§0.17).** The FIRST run of state B returned 404/404 on
  `/auth/test`//`/auth/sso` — gate-off despite the env. Root cause read out of the source:
  `cuentas/accounts.py` L491-498 delegates to `auth/mode.py::current()` (L47-66), whose
  module-level `_cache` (L32) holds the previous value for ~3 s; `config._reset_cache_para_tests`
  (`core/config.py` L647-652) resets config's caches but NOT that one. After clearing
  `auth_mode._cache` (the `test/test_auth_mode.py` L38-39 pattern) state B flipped to the 200/200
  of the matrix above — the trap and its escape both reproduced deterministically.
- **Non-200 wire contracts, FULL header enumeration on both wires (§0.18).** **(E)** no-session
  `/account` under renaper-ENTRY (`TURNOS_AUTH_METHODS=password,renaper` +
  `TURNOS_FAKE_RENAPER=1`): legacy `[(Location, /auth/renaper?next=%2Faccount), (Content-Length,
  0)]`, body `b""`; FastAPI through the real router: exactly `location` + `content-length: 0`,
  body `b""` — no content-type, no cache-control, no set-cookie on either wire. **(F)** gate-off
  `/auth/test`: both wires body `{"error": "test_auth_no_disponible"}` (36 bytes, space after
  the colon), `application/json; charset=utf-8` + `no-store` + `content-length: 36`, nothing
  else. **(G)** `LANGUAGES=es,en` + `LANG_SWITCHER_MODE=secret`: legacy `i18n.handle` on
  `/about.html?lang=en` → `[(Location, /about.html), (Set-Cookie, lang=en; Max-Age=31536000;
  Path=/; SameSite=Lax), (Content-Length, 0)]`, body `b""`; the pilot twin's REAL path
  (`_maybe_lang_switch` → `shim.to_response()`) through TestClient → exactly those three headers
  (Starlette's own `content-length: 0`), body `b""` — no content-type, no cache-control on
  either wire.
- **`account-form.js` recount, occurrence-classified (§0.21).** Every `\bT(` occurrence
  classified line-by-line: 150 total = **146 call sites** (all `T("…")` double-quoted; 144
  `account.*` + 2 `date.*` prefix templates `T("date.month." + n)` L896 /
  `T("date.month_full." + mesN)` L130; 109 unique literal keys; a multiline-safe whole-file
  regex confirms no `T(` with the literal on the next line) + **1 definition** (`function
  T(key, vars, fallback)` L45) + **3 comment mentions** (L134, L135, L894). 0 of the 109 keys
  exist in `locales/es/account.json` (re-checked by flattening the catalog, 80 keys).
- **DB-inheritance and teardown reads (§0.19).** `core/db.py` L1139-1143 (`_db_url`: exported
  `DATABASE_URL` first, env file second); `test/_dbtest.py` `run_scoped` L87-101 (token-unique
  per-run name, raises past 63 chars), `PerModuleDb.set_up` L207-236 (maintenance-URL
  DROP/CREATE of the throwaway only — never rows in a real database; repoints
  `os.environ["DATABASE_URL"]`; `db._ensured.clear()` + `close_pools()`), `tear_down` L238-275
  (pool close, then `DROP … WITH (FORCE)` of the throwaway; the deliberate broken-URL leak it
  leaves is documented in its own comment — do-not-relitigate), `reclaim_stale_databases`
  L109-166 (age + zero-live-sessions, non-FORCE first). `scripts/route_parity_check.py`'s
  "No necesita Postgres" docstring (L119) re-read; `branding.save_color` L117 /
  `theme.save` L98 re-pinned.
- **Lane scan re-run at v5 freeze:** `git worktree list` shows `glm/meta-doc-truth`
  (`turnos-glm-meta-doc-truth`, base `6c34573e`) STILL OPEN — hence §0.20's blocking
  precondition — and `glm/admin-static-fingerprint-final` still open. The signer attribution
  re-verified: `proxy_trust.sign` is called at `services/legacy_proxy.py` L167 (the catch-all's
  transport; app.py wires it at L49/L66) — §0.16 corrects v4's "app.py's catch-all" wording.

**v4 pass, 2026-08-17, on `232e33b4`** (verification-only — the sole edit is this file; every
probe below ran OFFLINE and in-process from the session job dir, none committed, no network, no
live service, `DATABASE_URL` pinned to a dead loopback port so no real database was reachable;
the probes replicate `scripts/route_parity_check.py`'s own shields — `TURNOS_FAKE_AI`,
`TURNOS_SKIP_HSI`, `config.ENV_PATH = ""` — and its exact peer shapes):

- **Probe A — the two test-auth peers (§0.12).** Drove `/auth/test`//`/auth/sso`//`/account`
  through BOTH real peers: the legacy direct call with `_run_legacy_intake`'s shim (Host
  `testserver`, client `203.0.113.5`) and the FastAPI TestClient round-trip through `app.app`.
  Results: v3's recipe (renaper+stub+proxy-secret) → `/auth/test` legacy **200** (real HTML) vs
  FastAPI **404** (`{"error": "test_auth_no_disponible"}`) — permanent DIFF; corrected recipe
  (+ `TURNOS_RENAPER_STUB_DEPLOYED=1`) → all four pages **200 both peers, full-body
  byte-identical, header key sets equal, in BOTH languages** (Cookie `lang=en` driven too);
  password default → 404 both peers, byte-identical bodies.
- **Probe B — the no-session `/account` 302 (§6(b) Status).** With only renaper+stub: 200 both
  peers (NO redirect — `auth_redirect_target` returns None, confirming the recipe gap). Adding
  `TURNOS_AUTH_METHODS=password,renaper` + `TURNOS_FAKE_RENAPER=1`: 302 both peers, byte-equal,
  `Location: /auth/renaper?next=%2Faccount`, empty body, no `Set-Cookie`; `/account/reset`
  unaffected (200). Also confirmed `_json`'s exact 404 wire shape on both peers: body
  `{"error": "test_auth_no_disponible"}`, `Content-Type: application/json; charset=utf-8`,
  `Cache-Control: no-store`.
- **Probe C — the `?lang=` switch (§0.13).** `i18n.handle` on the shim for all four routes +
  `/`: with only `LANGUAGES=es,en` → `handled=False`, nothing sent (switcher off by default).
  With `LANG_SWITCHER_MODE=secret` added → 302 on every route, `Location` = same path, empty
  body, `Set-Cookie: lang=en; Max-Age=31536000; Path=/; SameSite=Lax`.
- **`account-form.js` recount (§0.15).** Regex over the file excluding the `T` definition and
  comments: 146 call sites (all `T("…")`), 109 unique literal keys (144 `account.*` + 2
  `date.*` prefix templates); 0 of them present in `locales/es/account.json`.
- **DB-inheritance read (§0.14).** `core/db.py` L1139-1143 (`_db_url`: exported
  `DATABASE_URL` first, env file second) + `scripts/route_parity_check.py`'s shields (L134-138:
  `config.ENV_PATH = ""` neutralizes only the env FILE) + the script's "No necesita Postgres"
  docstring (L119) + `test/_dbtest.py`'s `PerModuleDb` per-run throwaway-database pattern (its
  own docstring) + `branding.save_color`/`theme.save` self-invalidation reads
  (`patient_web/branding.py` L112-129, `core/theme.py` L98-108).
- **The parity script itself, run offline at base** (`env -u DATABASE_URL python3
  scripts/route_parity_check.py`): every family up to and including `ESCENARIOS_ACCOUNTS` is
  green — all 21 accounts rows OK (`/account` 200, `/account/reset` 200, `/auth/test` +
  `/auth/sso` 404/404, the API rows' same-response statuses) — so the harness the batch extends
  is runnable with no database at all through the accounts family. The run then DIES at
  `ESCENARIOS_APPOINTMENT_OFFER` (`waitlist_store.offer_by_token` → `db._connect` →
  `DbUnavailable`, uncaught): the L119 "No necesita Postgres" docstring is already drifted at
  base — pre-existing, out of this batch's scope (and on a machine that exports a real
  `DATABASE_URL` the script runs those rows against that real DB, which is exactly the
  inheritance §0.14 keeps this batch out of). The executor attaching the §6(c) empty diff runs
  the script in their usual environment; the v4 requirement is only that the ACCOUNTS family
  adds no new DB dependency. (Superseded by v6: §0.23 — the executor never runs the whole script
  in any environment, accounts-family offline proof only; §0.22 — the empty diff attaches to the
  frozen-diff handoff, not to a commit.)

**v3 pass, 2026-08-17, on `232e33b4`** (verification-only — the sole edit is this file; scripts run
from the session job dir, none committed):

- Token/catalog reconciliation RE-RUN by AST parse of `cuentas/accounts.py`: constant spans
  L1273-1739/L1825-1990/L2021-2159/L2183-2258 confirmed; `{{t.*}}` tokens 10/47/8/11 = 76,
  per-constant unique 10/28/8/11 = 57 → 51 global (45 `account.*` + 6 `footer.*`, the 6 shared by
  `_ACCOUNT_HTML`+`_RESET_HTML`); `{{static.*}}` 10/1/1/3 = 15 tokens, 11 distinct, ALL inside
  `static_fingerprint.MIGRATED` (module import, set-diff empty); all 51 keys present AND non-empty
  in BOTH `locales/{es,en}/account.json` (80 keys) + `footer.json` (8 keys); the 4
  `{{sitio.*}}`-embedding catalog values are exactly `account.meta.title`/
  `account.meta.description`/`account.reset.title`/`account.reset.meta_description` (both
  languages); inline `T()` union 36 distinct `account.*` (25 dash) with unused-34 ⊆ T-36,
  T−catalog = ∅, T∩server = the 2 shared keys; `account-form.js`: zero catalog-key mentions,
  ~120 `T()` calls all outside the catalog (count stale — corrected to 146/109 in the v4 pass,
  §0.15).
- Three parallel read-only verifier passes (accounts core / tests+parity script / jinja+i18n
  infra — GLM subagents off the repo, 43 claims total): 40 exact-OK; the 3 drifts and every cite
  correction are folded in above — `_test_auth_enabled` L526-552, theme str drop L255-257,
  `?v=20` also at L2228, `admin.html` 1 417 lines (v2 said 1 418), the i18n switch re-pinned to
  `_maybe_switch_lang` L591-611 + `handle` L614-625 (v2's L544-551 is `inject_bootstrap`),
  `TestI18nWiring` L650-761 + env recipe L656-668, `ESCENARIOS_ACCOUNTS` rows L588/L601/L602/L603,
  seo test reads constants, cache-headers has no `/account` row, `templates/` already holds the
  four public-page twins + `admin/*`, `app.py` mounts via `routers/platform.py:48` (`ORDERED`) at
  `app.py:75`. The §4.2 pipeline matrix itself SURVIVED verification — one verifier expectation
  was wrong, not the doc: `/auth/test` really runs six steps incl. `site_identity.render` L2000 +
  `theme.inject` L2003 (its HTML carries `{{tema}}` at L1826); the bare four-step pipeline is
  `/auth/sso`'s alone (L2167-2170).
- Themed-run unit corrected from pack to `catalogo` rows: `config/tema` (`core/theme.py`
  L85-88/L103-105), `config/marca` (`patient_web/branding.py`, docstring L30: one accent color,
  storage row), `_branding_style()` L111-115; every `packs/*/sitio.json` checked — none carries
  theme keys.
- Lane scan re-run at freeze: `git worktree list` shows BOTH dependency lanes still open —
  `glm/meta-doc-truth` (`turnos-glm-meta-doc-truth`, base `6c34573e`) and
  `glm/admin-static-fingerprint-final` (`turnos-glm-admin-static-final`, base `232e33b4`) — hence
  the unconditional §9.1 duty.
- Link/diff checks at freeze: every backticked repo path in this doc exists or is a §4.1 planned
  creation; the pre/post-edit snapshot diff is confined to this file; `git status` shows no other
  change (no production/test file touched).

**v2 pass, 2026-08-17, on `232e33b4`** (all numbers below are from this pass; scripts kept out of
the repo, run from the session job dir):

- Token counts per constant: AST parse of `cuentas/accounts.py` (`ast.Assign` spans, hyphen-safe
  token regex) — `_ACCOUNT_HTML` 10 t + 10 static, `_TEST_AUTH_HTML` 47 t (28 unique) + 1 static,
  `_SSO_HTML` 8 t + 1 static, `_RESET_HTML` 11 t + 3 static → totals **76 t (51 unique: 45
  `account.*` + 6 `footer.*`) + 15 static (11 distinct)**. (v1's 57-unique / 49+7 breakdown was a
  miscount — §0.1.)
- Catalog coverage: all 51 keys present in both `locales/{es,en}/account.json` (80 keys incl.
  `_comment`) and `locales/{es,en}/footer.json` (8 keys); zero missing; zero empty-string values
  (i18n defect class 4 checked deliberately).
- Client-side consumption: all 34 catalog keys unused by server-side tokens are consumed by inline
  `T()` calls inside `cuentas/accounts.py` (cross-check script: unused-set ⊆ T()-call-set, zero
  unaccounted); `account-form.js` has zero catalog-key references; `admin-app.js` L77-78 are date
  lookups.
- All 11 `{{static.*}}` assets verified inside `core/static_fingerprint.MIGRATED` (imported the
  module, diffed the sets).
- Serve-function pipelines: read `_serve_account`/`_serve_test_auth`/`_serve_sso`/`_serve_reset`
  in full; `auth_redirect_target` L1749; `theme.inject`'s no-token early return read in both the
  bytes and str branches (`core/theme.py` L229-258) — the basis of §0.6/§4.2/§5.1.
- Router forwarding state: `cuentas/accounts_router.py` read in full (L41/52/53/54 + API routes
  L42-49, L55-62; no `/account/demo-access` route).
- Parity harness: `scripts/route_parity_check.py` `_IGNORAR_HEADERS` L193-197, key-set comparison
  L418-428, `ESCENARIOS_ACCOUNTS` L587-612 (all four GET rows present), generic runner L396-437.
- Existing auth scenarios: `test/test_accounts.py` method list read (L149, L359, L369, L397-418,
  L607-760); `test/test_seo.py` L113; `_test_auth_enabled` L526-546 + env recipe L657-664.
- Dispatch order (`demo_installation, i18n, accounts`): `core/routing.py` `GET_PLATFORM` L82-85.
- Demo-access dependency: `ops/demo_installation.py` L356-360; repo-wide `_serve_account` search —
  exactly two production callers.
- Lane scan: `git worktree list` + `git diff --name-only 232e33b4` over 18 live lane worktrees
  restricted to the batch's file set — collisions only as reported in §3 (meta-doc-truth →
  `cuentas/accounts.py`; admin-static-final → `core/static_fingerprint.py`,
  `templates/admin/solicitudes.html`).
- Whole-tree token sweep re-checked at the row level: public pages 68 t + 26 sitio + 2 legal +
  26 static; `admin.html` 1 418 lines, 68 t + 42 static; `_FOOTER` 7 footer tokens (starts L1044);
  `admin-legal.js` 5 sitio tokens at L134-136; `locales/*/about.json` ×8 sitio-in-values.

**v1 pass, 2026-08-16** — superseded where §0 says so; the pilot-pattern cites it established
(`test/test_jinja_pilot.py` L63/74/114-159, `core/site_identity_router.py` L72-78/81-99,
`core/jinja_env.py` L105/L108-123/L155/L172, `core/i18n.py` L544-551, `templates/privacy.html`
L2/L6/L23/L46) were re-confirmed in the v2 pass.

## 9. Execution checklist (ordered, one worktree — ends as a frozen diff handed to Codex, never self-landed; v6 §0.22)

1. **Pre-flight — BLOCKING (v5, §0.20):** both dependency lanes were still OPEN when this
   packet froze (§3, re-verified at freeze and again at the v5 freeze) — `glm/meta-doc-truth`
   rewrites `cuentas/accounts.py`, this packet's byte-frozen parity reference, and
   `glm/admin-static-fingerprint-final` grows `static_fingerprint.MIGRATED`, the §4.3 drop-in's
   basis. **Steps 2-7 do NOT start until `glm/meta-doc-truth` has frozen/merged**: then re-run
   the §8 derivation against the new base and re-pin this packet (counts, `MIGRATED` membership,
   every line cite) BEFORE any template is written. The admin-static lane is a re-verify-only
   dependency (`MIGRATED` only grows — superset-safe). Symbols in this packet are stable; line
   numbers are not. **And throughout the implementation `cuentas/accounts.py` stays READ-ONLY**
   (§4.1) — byte-frozen parity reference AND the `TURNOS_ASGI`-off production implementation.
2. Create the four templates by mechanical port of the constants using §4.3's table (keep every
   byte that is not a token — the inline `<script>` bodies including their `T()` calls, styles, and
   `?v=` references verbatim).
3. Add `test/test_accounts_jinja.py` FIRST against the not-yet-migrated router: it must FAIL
   (router still forwards → native render comparison cannot run) — then implement step 4 and make
   it pass. Do not write the twin and the gate in the same breath.
4. Rewrite the four GET registrations in `cuentas/accounts_router.py` as native endpoints:
   per-route §4.2 pipeline, `_maybe_lang_switch` reuse, `auth_redirect_target` + `current_user`
   via `LegacyHandlerShim`, bootstrap/canonical context with R2's escaping, per-route headers,
   acceptance matrix §6(b).
5. Extend the accounts family in `scripts/route_parity_check.py` per §6(c): BOTH gate states on
   **matched loopback peers** (accounts-scoped shim peer `127.0.0.1` + a scoped
   `TestClient(client=("127.0.0.1", 54321))` — never `203.0.113.5` vs `testclient`, §0.16), the
   enabled pass setting only renaper+stub with `config._reset_cache_para_tests()` AND the
   `auth_mode._cache` clear at both edges of the env block (§0.17); the stub-deployed run is
   optional. The themed pass is NOT here — it is §6(b)'s throwaway-database unittest case
   (§0.14/§0.19).
6. Full backend + frontend suites green; the accounts-only offline parity proof (§0.23) green —
   its clean output plus the empty diff are the parity evidence carried into step 7. Never run
   the parity script bare.
7. **Freeze and hand off to the coordinator (v6 §0.22, v8 §0.25 — this batch never commits,
   pushes, merges, deploys or writes ANY Git state, the index included):** leave every written
   file in the worktree and produce the frozen local diff READ-ONLY — `git diff 232e33b4 --
   cuentas/accounts_router.py scripts/route_parity_check.py
   docs/roadmap/jinja-l10n-accounts-batch.md` for the modified tracked files, plus
   `git diff --no-index -- /dev/null <file>` for each of the four templates and
   `test/test_accounts_jinja.py` (exit 1 = differences found = the EXPECTED success path; NO
   `git add` in any form — not even intent-to-add, §0.25), concatenated into one frozen diff
   artifact in the executor's job dir. Capture `git status --porcelain` before and after the
   freeze: the two captures must be byte-identical (proof the freeze wrote no Git state). Hand
   the coordinator (Codex) the frozen diff, this packet, and the parity evidence
   (offline-proof output, empty diff, suite results), stating: batch scope, parity evidence,
   that `accounts.py` is intentionally untouched, and the §1 table update in this doc.
   Review/commit/merge/deploy decisions are Codex's and the product owner's — no step of this packet
   instructs any of them.
