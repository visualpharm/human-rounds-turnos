# Contributing

Human Rounds is a Python-stdlib + vanilla-JS + Postgres medical platform
for public health networks, born at Hospital de Pinamar (Argentina) and
built to run for any town willing to swap in its own data pack. Read
[CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md) before your first PR —
they carry the invariants this file assumes you already know.

Contributions accepted into this repository are licensed under AGPL-3.0.
Contributors retain their copyright unless they separately assign it. Read
[LICENSING.md](docs/policies/LICENSING.md) for the AGPL Core and Enterprise-services boundary;
an existing Core component or contribution is not made proprietary by use from
a separate commercial service.

## Dev setup

```bash
git clone <repository-url>
cd sana-turnos
python3 -m pip install --break-system-packages 'psycopg[binary]' 'Pillow>=11.0'
cp .env.example .env   # set DATABASE_URL at minimum
DATABASE_URL=postgresql://user:pass@localhost:5432/sana_turnos TURNOS_ASGI=1 PORT=7700 python3 -m app
```

Full options (Docker Compose, systemd/launchd) → [docs/INSTALL.md](docs/INSTALL.md).

Node is only needed for the frontend test suite — no build step, no
`npm install` for the app itself (vanilla JS, no bundler).

## Before you touch anything

- Check [docs/solutions/INDEX.md](docs/solutions/INDEX.md) and the
  [architecture doc](CLAUDE.md#where-the-detail-lives-read-before-touching-that-area)
  for the area you're changing. A lot of "obvious" redesigns are already
  documented as deliberately-not-done, with the reason.
- `app/asgi_main.py` (`python3 -m app`) is the sole process entrypoint. A new feature belongs in a
  module that owns its FastAPI router or in the platform package that already
  owns the surface; do not introduce a second transport or composition root.
- Data is layered and swappable (`packs/`, `data_packs.py`) — never
  hand-build a `webdata/`-relative path; always `sitedata.path(fname)`.
  See [docs/architecture/data-packs.md](docs/architecture/data-packs.md).

## Tests — both suites gate every change

```bash
TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_*.py"
node --test tests/*.test.mjs
```

Always use `discover -s tests` — `python3 -m unittest tests.test_x` does not
put `tests/` on `sys.path`, so the helpers a test imports are missing, and the
standard library ships its own `test` package, which `-s test` (the directory's
name before 2026-11) would discover instead. `TURNOS_FAKE_AI=1` stubs every OpenRouter call so the
suite runs with no network and no API key.

### The golden-page gate

`tests/test_golden_pages.py` renders the home page, one service page, and
one center page two ways — live pack-chain mode, and a reconstructed
pre-pack `webdata/` snapshot — and asserts byte-identical output. If you
move a file between pack levels, extend its `_MOVED_TO_*` tuple (symlink
the moved file back into the snapshot) in the same commit, or this test
fails with a diff instead of a pass. A second class,
`TestExampleTownChainIsGeneric`, boots `PACK_CHAIN=global,example-town` and
asserts the home/service/center pages render **zero** "Pinamar" strings —
this is the genericity check: if a change makes the engine leak Pinamar's
own identity into a generic install, this is what catches it.

Neither suite needs a real Postgres for most tests (an in-memory fake DB
backs the unit tests); a handful of integration tests skip gracefully
without `DATABASE_URL` set.

### Run ladder — pre-commit hook

`scripts/install-hooks.sh` (run once) wires `scripts/pre-commit.sh` into
`.git/hooks/pre-commit`. Every `git commit` then runs the frontend suite +
the routes tier (`scripts/run_routes_tests.sh`, Tier 3 Area 2) — measured
~2s combined, well inside the ~20s budget — and **blocks the commit** on
red. Emergency bypass: `SKIP_PRECOMMIT=1 git commit ...` (don't use it on a
final pre-push commit). A lighter, non-blocking version of the same check
(`test_module_manifest.py` + the routes-tier canary) also runs as an
advisory `Stop` hook after every Claude Code turn — see
`.claude/hooks/stop-smoke-guard.py` and
`docs/roadmap/tier3-harness-design.md`, Area 4.

## Commit style

Commit messages are in **Spanish**, matching the existing history:

```
<component> (fase N, x/N): short description of what changed

Longer body if needed — why, not just what. Spanish, same voice as the
subject line.

Co-Authored-By: <your agent, if applicable>
```

Look at `git log` for the exact texture before your first commit. Docs,
code comments facing developers, and this file are **English** — see
"Language" in [AGENTS.md](AGENTS.md). Patient-facing UI copy stays Spanish
(or whatever locale it's authored in — see [docs/architecture/i18n.md](docs/architecture/i18n.md)).

## English identifiers

New Python/JS identifiers (modules, functions, variables, DB columns) are
**English**. The codebase forked from an Argentine Spanish-named project
and is mid-migration — [docs/rename-map.md](docs/rename-map.md) tracks
what's renamed and what's still pending. Don't add new Spanish identifiers
to a file that's already been renamed; if you're touching a file that's
still Spanish-named end to end, match its existing convention rather than
mixing languages in the same file — a full rename is its own dedicated
slice, not a drive-by in an unrelated PR.

## Cache busting

Served JS/CSS is content-hash fingerprinted (`app/core/static_fingerprint.py`):
every first-party asset in `static_fingerprint.MIGRATED` is served at
`/static/<name>.<hash8>.<ext>`, immutable, and its URL changes with its
content — changing a file needs no manual step. Reference an asset with
`static_fingerprint.url('x.js')` (Python f-string pages),
`{{ static('x.js') }}` (Jinja templates) or `{{static.x.js}}` (legacy
`admin.html`); a static JS file that loads another asset at runtime looks
it up in `window.SANA_STATIC` (the `CLIENT_LOADED` cohort, published by the
page shell via `static_fingerprint.manifest_script()`), fail-closed — never
a hardcoded URL. A new served asset goes into `MIGRATED` (or `CLIENT_LOADED`
when another static file loads it); a new `?v=` reference fails
`tests/test_listado_consistencia.py`.

First-party JS/CSS never uses manual `?v=` cache-busting. Platform assets use
`MIGRATED`; module-owned files opt in through `{path, fingerprint: true}` and
render through the module fingerprint helpers. Details: [deploy contract](docs/architecture/deploy.md).

## Adding a connector (new country's identity/insurance/license/EMR/slot/OTP integration)

A connector is a horizontal integration with ONE external system,
implementing ONE of six capabilities (identity verification, insurance
lookup, license lookup, EMR sync, slot source, OTP transport). Read
[docs/connectors.md](docs/connectors.md) in full first — it walks through
writing one for a new country end to end. PR checklist:

- [ ] `app/connectors/<id>/connector.json` — `id` matches the directory name,
      `capability` is one of the six real ones, `config_keys` lists every
      env var the connector reads.
- [ ] Implementation raises `connectors.ProviderUnavailable`/
      `connectors.IdentityNotFound` (the shared exceptions, not a parallel
      type) where the capability's ABC calls for it.
- [ ] A fake/stub/test mode exists and is documented — a connector that
      hits a real third-party network call MUST be testable without one.
- [ ] `adapter.py` implements the capability's ABC.
- [ ] Tests: registry discovery finds it, the degradation case
      (unconfigured → the capability's documented "nothing crashes"
      behavior), and the fake mode if it has one.
- [ ] `docs/connectors.md`'s capability table gets a new row.

## Adding a data pack (new town, province, or country)

Read [docs/architecture/data-packs.md](docs/architecture/data-packs.md)
first — it explains the pack chain, override vs. merge resolution, and the
cache-key contract. PR checklist:

- [ ] `packs/<id>/pack.json` — `id`, `level` (1–5), `parent`, `language`.
- [ ] The files your level actually owns, matching an existing pack's shape
      (`packs/example-town/` is the smallest complete example — copy it).
- [ ] If you're adding a new town: `PACK_CHAIN=global,ar,ar-b,<your-id>`
      boots and the site renders with your data, not a crash or a blank page.
- [ ] If your file composes across levels (a taxonomy meant to extend a
      higher level's content, not replace it), use
      `data_packs.merged()`; if it fully replaces a lower level's version,
      use `data_packs.path()`/`resolve_path()`. Read "Wiring
      especialidades.json/clusters.json through merged() — ATTEMPTED AND
      REVERTED" in data-packs.md before assuming `merged()` is always the
      right call — it composes the WHOLE chain additively, which is wrong
      for a pack that wants to fully own its own taxonomy.
- [ ] Golden-page test extended if you moved an existing file between
      levels (see "The golden-page gate" above).

## Adding a module (a new vertical feature, not an integration)

A module is a whole feature surface (routes + admin UI + DB tables + nav),
not a single integration — see [docs/modules.md](docs/modules.md) for the
`turnos` module's own manifest as the reference shape, and the two roadmap
modules (`docs/roadmap/teleconsulta.md`, `docs/roadmap/resumen-historia-clinica.md`)
as real candidates for what a second module looks like.

## Security

Found a vulnerability? Don't open a public issue — see [SECURITY.md](SECURITY.md).
