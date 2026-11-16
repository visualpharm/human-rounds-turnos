# Branch consolidation — 2026-08-08

Inventory of every genuinely unmerged branch (local + remote) against
`origin/main` = `daf4c8f6` ("Novedades: 7 de agosto de 2026"), taken on the
consolidation worktree `turnos-branch-consolidation`
(branch `claude/hr-branch-consolidation`). tracking card:
`2026-08-08-capture-human-rounds-consolidate-and-safely-land-unmerged-`.

Classification vocabulary: **MERGE** (land now), **SUPERSEDED** (intent already
in main via another path — safe to delete later), **STALE** (obsolete base,
architecture moved on), **BLOCKED-WIP** (owned by a live session or an external
blocker — do not touch), **OWNER-ONLY** (needs the product owner's decision).

Branches checked out in verify-only detached worktrees (`batchd-verify-main`,
`vfy2`, `verify-origin-main`) are ancestors of main and hold nothing. All other
worktree branches (tier2/3/4/6, i18n integration, unit27/28, cola-design,
sticky-nav `3542a85a`, ~45 `agent-*` leftovers) are fully merged into main —
only the 13 below have unique commits.

## MERGE — consolidated by this branch

| Branch | Unique work | Evidence |
|---|---|---|
| `agent/image2code-hr-frontpage` | `c8e19698` "Flecha de 'esto lleva a algo' en las cards de Más buscadas" (2026-08-05 22:54) | Design-QA'd image2code fidelity pass vs the Claude Design home mockup; arrow affordance absent from main (`dir-card-arrow` has zero hits). +12/−4 plus two icon files. One trivial conflict (index.html `?v=` line); `directory.js` goes to v=16 because branch and main reached v=15 separately with different bodies (the vbump-collision case). Plus one grafted test assertion (`stznav-account-section`, from the codex mobile-nav twin) that main's test lacked. |

## The mobile-nav pair — SUPERSEDED, proven by merge equivalence

`worktree-agent-a8d33b43d3083baa5` (`50d43204`, 2026-08-06 21:36) and
`codex/mobile-nav-account-cards` (`55174297` + merge `fb2524dd`, 2026-08-06
19:41) are the card's "overlapping mobile-nav branches": the SAME
implementation ("Nav móvil: chat flotante y cuenta agrupada") at two bases —
interdiff 41 lines, all `?v=` numbering, comment language, and two test
assertions. Initially classified MERGE and merged (a8d33b43 as primary, codex's
extra assertion grafted); the merge itself then proved the whole feature was
ALREADY re-landed on main by the 2026-08-07 batch — `4155e210` "Chat: botón
flotante de lanzamiento…" (the floating launcher, announced in that day's
Novedades), `6e086b9e` "Mis turnos: nombres normales…" (the my-requests layout
rework), and siblings — plus `205b947f`'s greeting work: after reconciling, the
NET delta of the entire mobile-nav merge vs `origin/main` was one comment's
wording in `chat-widget.js` and nothing else. That residue was dropped and
`chat-widget.js` restored byte-identical to main (so main's live `?v=50` needs
no bump). Both branches (and their origin twins) are therefore **SUPERSEDED**;
the only surviving piece is the grafted test assertion above.

## SUPERSEDED — intent already in main, nothing to land

| Branch | Evidence that main already has it |
|---|---|
| `fix-missing-logo-svg` (+origin) | `logo.svg`, `human-rounds-mark-32.png`, `-dark.png` blob-hash-identical in main; `scripts/build_brand_marks.py` has the luminance-alpha fix; `admin.html:6-9` has the favicon pair; `stories/BrandMark.stories.js` present. |
| `paleta-natural` (+origin) | Branch proposed humanrounds `--accent:#a15030 --accent-soft:#ffe7dd --accent-bd:#f4d0c2` — main's `tokens.css:129` carries those exact values. Only hover differs: branch wanted darker `#853815`; main's `#b0501f` follows the NEWER 2026-08-07 hover-is-brighter invariant (ui-evals eval 8). Branch predates the tokens.css extraction (edits `human-rounds.css`). |
| `tier2-e2e-hermetic` (+origin) | Main built its own hermetic gate 34 min after this branch: `9eec0272` (2026-08-05 18:04) "Gate E2E hermético y exigente: TURNOS_ENV_FILE, fallo ruidoso, pack sintético, sin DSN en logs", evolved further in `ceb9fa1c`. Branch targets the pre-`core/` layout (root `config.py`/`db.py`/`env_file.py`). Its 452-line `test_hermetic_gate.py` enforcement suite was not adopted; main enforces via its own gate design. |
| `worktree-agent-a0ec86c50d94a0c15` | `fb7ec8ec` (pool + scoped query) landed: main's `core/db.py` has `psycopg_pool.ConnectionPool` (`TURNOS_DB_POOL`, line 1056) and `list_approved_future_for_doctor` (line 2515, comment cites the 2026-08-06 audit); all three test files (`test_db_pool.py`, `test_list_approved_future_for_doctor.py`, `_dbtest.py`) in main with only 2-4 lines of later drift. |
| `origin/claude/language-switcher-burger-menu-fadtb3` | Main solved switcher placement deliberately and later: language links live INSIDE the menu as code pills (`nav.js:293` "Selector de idioma: ADENTRO del menú, última posición", `langSwitchHtml()`); the branch's full-names-in-burger approach was not adopted — compact codes were a later, documented decision. |
| `origin/claude/refine-local-plan-77j0h5` | All four commits re-landed better: estudios-preparación exists as layered pack data (`packs/global/estudios-preparacion.json`, per-center overrides, `pack_l10n` localization, `patient_site.py:3311+`) with the research retained at `scratch-estudios-prep-report.md` (dropped from the tree 2026-11-10, still in history); the Argentine clock is the `tz.py` invariant; Novedades moved past 29 de julio. Branch targets the retired `webdata/` layout. |

## STALE

| Branch | Why |
|---|---|
| `origin/claude/pinamar-turnos-reports-s6lor8` | 2026-07-16, **972 commits behind**, targets root `db.py`/`reportes.py` which no longer exist (core/ move, batch K). Reports were rebuilt story-driven (`admin_panel/reports.py`: hero, oferta_demanda, demanda_semanal, sin_oferta, palancas, funnel, lista_espera). Residual ideas not 1:1 present in main — "top pacientes" ranking and a per-patient "riesgo de ausentismo" score — belong to a future report-design decision (that skill's doctrine is closed), not to a merge of this base. |

## BLOCKED-WIP — not mine to land

| Branch | Owner / blocker |
|---|---|
| `Chat` | Live session in the shared checkout `<workspace>/human-rounds/turnos`, actively committing (tip `0089251d` "Chat: mostrar el saludo antes de cargar el historial" created 07:27 **today**, ref in flux, currently == origin/main). |
| `tier5-frontend` | The next-tiers session's tier work (wt-tier5): 3 "Tier 5 pilot repair" commits for the `TURNOS_ASGI=1` dev-only screens. Lands with its owner's tier slice. |
| `codex/whatsapp-cloud-otp` (+origin) | Explicit per the tracking card; the commit itself says "bloqueado en el alta de la app en Meta". 937 behind. Must not land because it exists. |

## Consolidation decisions

1. Mobile-nav was merged from `worktree-agent-a8d33b43d3083baa5` and then
   proven fully superseded (see above) — the branch's final net contribution is
   the grafted `stznav-account-section` test assertion. No code from the pair
   ships beyond what main already has.
2. `?v=` handling per
   `docs/solutions/deploy/vbump-collision-after-parallel-merge.md`: a version
   changes iff the body a cached client may hold differs. `directory.js` → 16
   (collision: two v=15 bodies), `my-requests.js` → 36 (body gains the v=16
   loader line). `chat-widget.js` stays at main's live v=50 — after dropping
   the comment residue the body is byte-identical to main's, and a spurious
   bump would force every visitor to refetch an unchanged file.
3. Landing waits for the active roadmap/demo-pack owner to push, then this
   branch reconciles with latest `origin/main` and lands without force.
