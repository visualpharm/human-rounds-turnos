---
title: Moving a Python file into a Tier 2 package breaks three STRING references imports don't catch
scope: repo
area: dev
tags: [tier2, modularization, worktrees, mock-patch, module-json, route-order]
created: 2026-08-07
refs: [docs/roadmap/tier2-physical-modularization-plan.md]
status: active
---

## Problem
Batch E (`ai/` package — 16 files: `ai_client`, `ai_health(+_router)`,
`ai_costs(+_router)`, `ai_cooldown`, `ai_referral`, `prompts(+_router)`,
`orden_analysis_cache`, `abuse_guard`, `chart_summary(+_router)`,
`lab_analysis`, `lab_results(+_router)`) moved cleanly on every `import X`
/ `from X import Y` call site (mechanical, AST-greppable), but three
non-import reference shapes only surface as test failures, not import
errors — and a shallow "grep for `import <name>`" pass misses all three:

1. **`mock.patch("modname.attr", ...)` string targets.** `unittest.mock`
   resolves the dotted string against `sys.modules`, not against the
   caller's local name binding — `from ai import ai_client` still binds
   the name `ai_client` locally (so `patch.object(ai_client, "x", ...)`
   needs zero changes), but a *string* `patch("ai_client.primary_key", …)`
   now resolves to nothing (`ai_client` isn't a top-level module anymore)
   unless rewritten to `"ai.ai_client.primary_key"`. 7 test files, 50
   occurrences, all silent — they don't raise ImportError, they just
   silently patch a name that doesn't exist and the test passes for the
   wrong reason (or fails downstream with a confusing assertion diff, not
   an obvious "wrong patch target" error).
2. **`module.json`'s `routes[].module`/`after` fields.** These are
   strings `importlib.import_module()`-resolved by `app_modules.py`'s
   loader and anchor-matched by `merge_routes()` against `module.__name__`
   — a route entry left as bare `"ai_costs"` after the file physically
   moved either 404s at runtime (if nothing else imports it, Python never
   loads the real module) or silently degrades to tail-append (if the
   anchor target of ANOTHER route was the bare name — `merge_routes()`
   never crashes on an unresolved anchor, see its own docstring). Same
   trap `docs/connectors.md`'s "compat shims" section names for
   `unittest.mock.patch()` strings; this is the identical failure mode
   one layer up, in first-party JSON, not shim-registry patches.
3. **`test/test_route_order.py`'s golden literal lists** — the
   pre-Tier-2 order is asserted name-for-name against a historical
   snapshot (`git show 347a9f7:routing.py`); every prior batch (C, D)
   rewrote its own five-ish entries in these lists in the SAME commit,
   documented in the file's own top docstring changelog. A batch that
   moves a route module and forgets this file gets an exact, loud
   `assertEqual` diff — the one of the three that fails LOUD, not silent.
4. **A hardcoded relative file path in a frontend test.**
   `test/ui-evals.test.mjs`'s `leer("prompts.py")` reads the file's
   *prose* (not its Python symbols) to assert the AI prompt text contains
   specific phrases — a plain `fs.readFileSync` call, invisible to any
   Python-side grep for importers. `ENOENT` on the exact literal
   `"<repo-root>/prompts.py"`, not `ai/prompts.py`.

## Fix
Before calling a batch done, run FOUR searches beyond "does the code
import cleanly", in this order (cheapest/most-likely first):
1. `git grep -rn '"<modname>\.' -- '*.py'` for every moved name, filtered
   to `patch(`/`patch.object(` context — string-target mock patches.
2. `grep -n '"<modname>"' **/module.json` — manifest route/anchor fields.
3. `grep -rn '"<modname>' test/test_route_order.py` — golden literal lists
   (only relevant if the moved file is in ANY module's `routes[]`).
4. `grep -rln '"<modname>\.py"' -- '*.mjs' '*.js'` — hardcoded relative
   file-content reads in frontend tests (distinct from `import`/`require`,
   these are literal `fs.readFileSync(path.join(ROOT, "modname.py"))`
   calls that assert on PROSE, common in eval/golden-content tests).

None of these four are caught by `python3 -m py_compile` or a bare
`import server` smoke test — only the actual test suite (frontend
included) surfaces them, and only if that suite is run to completion, not
sampled.

## Verification
`scripts/ci_local.sh` full ladder, both before (RED: frontend 929/931,
`ui-evals.test.mjs` ENOENT on `prompts.py`) and after (GREEN: backend
3306/0 skipped, frontend 931/931, routes/cassette/e2e all OK) the fourth
trap's fix — the other three traps were caught and fixed BEFORE the first
full CI run (focused test passes on the moved files' own test modules
caught traps 1-3 individually; only trap 4 slipped through to the full
ladder because `ui-evals.test.mjs` isn't named after any moved module).

## Ampliación 2026-08-07 (batches F–H): cuatro clases más

Los batches F, G y H sumaron cuatro clases nuevas a las cuatro de arriba —
ocho búsquedas en total antes de dar un batch por terminado:

5. **Import inline dentro de try/except que se traga ModuleNotFoundError**
   (`notifications/confirmations.py:101`, batch F): un `import chat_tools`
   dentro de una función, envuelto en try/except genérico, falló en
   silencio y rompió la cancelación por SMS ("no puedo") sin poner ningún
   test en rojo hasta correr la suite completa. Grep de cada nombre movido
   como import INLINE, no solo top-level.
6. **Rutas derivadas de `__file__` en los archivos movidos** (batches C, G,
   H): `os.path.dirname(os.path.abspath(__file__))` queda un nivel más
   adentro tras el movimiento — `account_photo.py` habría escrito las fotos
   de perfil en `cuentas/data/` (perdiéndolas en el próximo redeploy).
   Auditar TODOS los `__file__`/`Path(__file__)`/`getcwd()` de cada archivo
   movido; el fix es un `dirname()` extra.
7. **`__import__("<modname>")` dinámico** (`test/test_chat_agent.py`,
   batch H): `__import__("pkg.mod")` devuelve el paquete raíz, no el
   submódulo — hace falta `fromlist=[...]`. Grep `__import__(` e
   `importlib.import_module(` por cada nombre movido.
8. **Chequeos de string contra registries de rutas**
   (`app_modules.route_enabled("patient_web.site_shell")`, batch H): el nombre de
   módulo vive como dato en el registry del dispatcher; un grep de imports
   no lo ve. Grep cada nombre movido entre comillas en test/ y en el código
   que consulta `route_enabled`/`routes[].module`.

Además: el worktree fresco NO tiene `.env` (gitignored) — copiarlo del
checkout principal ANTES de las gates, o los tests con DB degradan a
skip/503 en vez de fallar fuerte (batch G corrió su baseline así y casi
valida contra una suite degradada).

## Ampliación 2026-08-07 (batches K–L): la novena clase

9. **Inyecciones de `sys.modules["<modname>"]` (y monkeypatches de
   `builtins.__import__`) en tests.** Mientras el módulo era top-level, un
   test podía interceptar un `import db` / `import hsi_client` LAZY
   reemplazando la entrada de `sys.modules`. Tras el movimiento, el consumo
   real pasa a `from <pkg> import <mod>` — y ese from-import lee el
   ATRIBUTO del paquete (getattr sobre el paquete ya importado), no
   `sys.modules["<modname>"]` ni `sys.modules["<pkg>.<modname>"]`. La
   inyección vieja deja de interceptar EN SILENCIO: el test corre contra el
   módulo real (rojo confuso, o verde por la razón equivocada — batch L
   tuvo uno de cada). Fix: `mock.patch("<pkg>.<modname>", fake,
   create=True)` — parchear el atributo del paquete, que es exactamente lo
   que el from-import lee (batch L: fakes de HSI en
   test_hsi_sync/test_hsi_resync; batch K: 6 tests con fakes de db en 4
   archivos). Grep: `sys.modules\[` y `__import__` en test/ por cada
   nombre movido — NOVENA búsqueda obligatoria antes de dar un batch por
   terminado.
