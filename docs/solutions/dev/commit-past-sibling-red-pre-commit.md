---
title: Committing your own hunks when a sibling session's WIP makes pre-commit red
scope: repo
area: dev
tags: [git, worktree, pre-commit, parallel-sessions]
created: 2026-09-05
refs:
  - .claude/hooks
status: active
---

## Problem

This repo's pre-commit hook runs the FULL frontend + routes suites on the
working tree. With 60+ live worktrees and frequent parallel Claude sessions on
the same shared checkout, a sibling session's unrelated uncommitted files (or
even a red test already committed at HEAD by the sibling) block YOUR commit,
even though your own change is correct. `SKIP_PRECOMMIT=1` doesn't help — it's
blocked by the auto-mode classifier.

## Root cause

Pre-commit correctness is tree-wide, not diff-scoped, so any red anywhere in
the working tree (yours or a sibling's) fails every commit attempt until the
tree is clean. `git add -A` or committing through the dirty shared tree risks
shipping the sibling's broken WIP to `main`.

## Solution

Isolate your staged change into a clean worktree, commit there, push, then
fast-forward the shared checkout:

1. `git stash push --staged -m "<label>"` (git >=2.35: stashes ONLY staged
   hunks, leaves the sibling's unstaged WIP untouched in the shared tree).
2. `git worktree add --detach /private/tmp/claude-501/wt-<name> HEAD`
3. In the new worktree: `git stash apply`, then
   `ln -s <repo>/node_modules node_modules` (fresh worktree has none).
4. Commit there — pre-commit runs on a clean tree and passes.
5. `git push origin HEAD:main`.
6. Back in the main checkout: `git fetch && git merge --ff-only origin/main`
   (refuses if any locally-modified file overlaps — safety check).
7. `git stash list` — confirm `stash@{0}` is still YOURS, then
   `git stash drop`.
8. `git worktree remove --force <path>`.

If HEAD itself is red (a sibling committed a genuine regression, e.g. a
frontend test vs. a locale file mismatch), fix it as part of your own commit
so the hook can pass — there is no way around actually fixing it.

## How to apply

Reach for this recipe whenever pre-commit fails and `git status` shows files
you didn't touch. Never `git add -A` in this checkout (see
`feedback_never_git_add_all_in_shared_checkout` memory) and never delete or
stash a sibling's unstaged work.
