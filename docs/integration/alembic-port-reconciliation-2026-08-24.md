# Alembic-authority port — reconciliation brief against `origin/main` @ `664cf0b6`

**Date:** 2026-08-24 · **Lane:** `fable/alembic-port-current` (worktree
`<workspace>/human-rounds/turnos-fable-alembic-port-current`)
**Supersedes:** the freeze order of 2026-08-19, which asked for a fresh brief
against `85386d49` before any integration.

## Verdict

**Do not port. The candidate already landed upstream, in a strictly more
advanced form. Retire the lane.**

The freeze was correct at the time and it paid off: while the lane sat frozen,
the same work was integrated on `origin/main` by `d38e7a5b` ("Complete
open-source readiness candidate", 2026-08-19 16:21 ART), and then carried
further than the candidate ever went. Rebasing or merging the candidate now
would be a **regression**, not an integration — it would walk `migrations/`
back to an earlier revision head and drop two upstream refinements.

## What changed under the lane

| | value |
|---|---|
| Candidate | `7a084f72` "Make Alembic the schema authority" (patch-id `cb9a00a3…`) |
| Candidate base | `be5ada0c` |
| Brief-of-record base at freeze | `85386d49` |
| `origin/main` today | `664cf0b6` (2026-08-21 07:40 ART) |
| Commits gained since the candidate base | 36 |
| Lane position | 1 ahead / 36 behind `origin/main` |

The freeze note assumed `85386d49` was the reconciliation target and that the
4 intervening commits had **zero path overlap** with the candidate. That
measurement is now stale. `origin/main` moved past `85386d49` by 31 more
commits, and one of them — `d38e7a5b` — overlaps the candidate on **all 16 of
its paths**.

## Supersession proof

The candidate adds **4 566 lines across 16 files**. Measured line-for-line
against the `origin/main` tree (every added, whitespace-normalized, non-blank
line looked up in the upstream file of the same path):

| file | added lines not found upstream |
|---|---|
| `alembic/versions/0001_baseline.py` | 0 |
| `alembic/versions/0006_legacy_runner_tables.py` | 0 |
| `app/core/db.py` | 0 |
| `docs/INSTALL.md`, `docs/UPGRADING.md`, `docs/architecture/deploy.md` | 0 |
| `docs/solutions/INDEX.md` + both solution notes | 0 |
| `app/modules/chat/module.json`, `tests/test_module_manifest.py`, `tests/test_translate.py` | 0 |
| `migrations/__init__.py` | 35 |
| `tests/test_alembic_cutover.py` | 17 |
| `tests/test_integrity_races.py` | 2 |
| `CLAUDE.md` | 1 |

**55 unmatched lines out of 4 566 (1.2 %).** Every one of them was read: they
are docstring and comment prose that upstream reworded, plus test-internal
constants that upstream advanced (see next section). There are **zero
candidate-only top-level definitions** in `migrations/__init__.py`:

```
comm -23 <(git show 7a084f72:migrations/__init__.py | grep -o '^def [a-z_]*' | sort -u) \
         <(git show origin/main:migrations/__init__.py | grep -o '^def [a-z_]*' | sort -u)
→ (empty)
```

Spot-checked upstream presence of the candidate's load-bearing symbols —
`legacy_state`, `repair_shape_gaps`, `_diff_shape`, `_read_catalog`,
`check_constraints`, `_parse_column_def` — all present in
`origin/main:migrations/__init__.py`.

## Where upstream is strictly ahead

1. **Revision head moved 0006 → 0007.** `origin/main` carries
   `alembic/versions/0007_existing_install_shape.py`, which the candidate does
   not have. The candidate's `tests/test_alembic_cutover.py` still pins
   `_HEAD = "0006_legacy_runner_tables"`; upstream pins
   `_HEAD = "0007_existing_install_shape"`. Merging the candidate would fight
   that pin.
2. **CHECK-constraint matching got a real fix.** The candidate matches inline
   CHECKs by quoted-literal set. Upstream replaced that with **server-canonical
   expression equality** plus a `_CHECK_NAME_ALLOWLIST` mapping each
   `(table, column)` to both its English and its legacy Spanish constraint name
   (`booking_requests_attendance_check` / `solicitudes_asistencia_check`, and
   four more). The candidate's own comments concede the literal-set model was a
   deliberate approximation — "SEMANTICS beyond the quoted-literal set are
   deliberately NOT modeled … that is fix4b2's remaining half." Upstream closed
   that half. The candidate did not.
3. **File size.** `migrations/__init__.py` is 2 107 lines upstream vs 1 825 in
   the candidate — a superset, not a divergence.
4. **The doctrine paragraph in `CLAUDE.md`** is upstream verbatim except for
   those two upgrades ("matched by server-canonical expression equality",
   "same as 0002–0007 do").

## Semantic-contact / merge-tree re-derivation

The freeze order asked for merge-tree proof re-derived on the current tree. It
was run, and the answer is the one that makes the port moot:

```
git merge-tree --write-tree --name-only origin/main fable/alembic-port-current
→ CONFLICT (content): CLAUDE.md
   CONFLICT (content): migrations/__init__.py
   CONFLICT (content): tests/test_alembic_cutover.py
   CONFLICT (content): tests/test_integrity_races.py
```

All four conflicts are on the exact files where upstream is ahead (§ "Where
upstream is strictly ahead"). Resolving them means choosing between the
candidate's 0006/literal-set model and upstream's 0007/canonical-expression
model — and the candidate loses on every hunk.

The wider integration lane tells the same story. Local `main` (which carries
this same patch-id as `1f0153b6`, plus five sibling slices) merge-trees against
`origin/main` with `add/add` conflicts on `admin_panel/screen_nav.py`,
`tests/test_admin_screen_nav.py` and `tests/test_intake_hook_boundary.py` —
upstream already contains the very files those lanes thought they were
introducing (all six were landed by the same `d38e7a5b`). There is no clean tree to port onto
because the destination already holds the work.

## Actions taken

- Lane branch `fable/alembic-port-current` left at `7a084f72`, untouched.
- Archive tag `archive/alembic-port-current-7a084f72` created so the candidate
  stays reachable regardless of what happens to the branch.
- No rebase, no merge, no push to `origin/main`. Nothing in the candidate is
  needed there.

## Recommended next steps (not executed — they are the product owner's call)

1. Delete `fable/alembic-port-current` and `fable/alembic-authority`, and
   remove their two worktrees. Evidence for the delete is this brief; the
   archive tag keeps the commits reachable.
2. Same for the sibling frozen lane `fable/server-retirement-slice3`
   (`be5ada0c`) if it is only holding the candidate's base.
3. If anything in the candidate's *prose* is worth keeping — the
   `migrations/__init__.py` docstrings are more explicit than upstream's about
   why constraints are matched structurally rather than by name — port those
   comments only, as a docs-only commit. Nothing else.
