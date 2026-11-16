# Public-cut preflight reds in a live worktree — stale logs and control-plane debris

Two classes of `test_public_cut_preflight` failures that are NOT repo defects,
seen 2026-08-27 while closing the sweep card
`2026-08-27-fix-2-uncovered-turnos-ci-failures-module_router_fallthrough`.

## 1. A sweep card raised off a stale CI log

The dual-topology CI (v2, 12:18–13:29) reported `secret-assignment` findings in
7 router test files plus a `module_router_fallthrough` red. Both fixes landed the
same afternoon (83377e7f at 18:27, 048b62ad/db90272d at 18:12–18:21), hours after
the log's snapshot, so the 19:52 sweep filed already-fixed failures as new work.

**Before treating a CI-log failure as open, compare the log's START timestamp
with `git log --since=<log start>` on the files the failure names.** A fix
commit newer than the log means re-run the gate, not re-fix the code.

## 2. Secret-assignment scanner floor vs fixture literals

The `secret-assignment` marker regex flags `(password|secret|token|…)\s*[:=]\s*
['"][16+ chars]['"]`. Fixture literals like `"fixture-admin-token"` (20 chars)
trip it. Keep synthetic test tokens UNDER 16 chars (`"fixture-admin"`); never
allowlist the scanner — it is intentionally shape-based and fail-closed.

On2026-09-09 a new staff Account browser fixture passed the preceding full gate
while untracked, then failed after commit. The candidate snapshot used only
`git ls-files` and omitted new tests. It now includes tracked plus nonignored
untracked files (`--cached --others --exclude-standard`), matching the intended
commit inputs. A temporary-repository regression proves a new credential-shaped
fixture is detected, tracked edits are current, and ignored local files stay out.
The real fixture now uses short explicitly synthetic tokens; no scanner waiver.

## 3. `.claude/inbox/` trips the forbidden-path check

The capture pipeline drops `.claude/inbox/` (gitignored) into whichever
checkout the session runs in. The preflight `claude-private-state` forbidden-path
rule flags it — correctly — which short-circuits later phases and turns 3
current-candidate self-tests red (`test_current_repo_exits_nonzero`,
`test_current_repo_cli_reaches_later_phases`,
`test_current_repo_has_no_forbidden_private_pack`).

**Fix the environment, not the detector:** move processed done-markers to the
non-git parent (`<workspace>/human-rounds/.claude/inbox/done/`) and
remove the empty dir. A fresh clone never has this problem; only live worktrees
running captured work do.
