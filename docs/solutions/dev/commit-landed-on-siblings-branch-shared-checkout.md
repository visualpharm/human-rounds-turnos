# My commits landed on a sibling session's branch in the shared checkout

**Date:** 2026-08-08 · **Area:** dev/git workflow

## Symptom
`git push origin main` answers "Everything up-to-date" right after committing.
`git status -sb` reveals the current branch is NOT main — a branch you never
created (`Chat`). Meanwhile `origin/main` has a commit you don't recognize.

## What actually happened
Two live sessions shared this checkout. The sibling ran `git checkout -b Chat`
mid-session — **a branch switch in a shared working tree switches HEAD for
every session using it**. They committed their work on `Chat`, `reset HEAD^`,
re-committed elsewhere and pushed to `origin/main`; my later commits then
landed silently on the abandoned `Chat` branch. Bonus confusion: their
uncommitted `?v=` bumps sat in the tree during my greps, so I briefly
"observed" reference drift (v=49 vs v=50) that was really their WIP.

## Fix that worked
1. `git reflog` decodes the whole interleaving (checkout/reset/commit lines).
2. Confirm no session is live (`claude-stop --list`), then inspect the
   sibling's pushed commit for leaked WIP before building on it — grep it for
   your own constructs; context lines vs +/- lines matter.
3. `git checkout main && git merge --ff-only origin/main`, then cherry-pick
   your commits from the stray branch. Resolve conflicts by KEEPING BOTH
   INTENTS (their new test survived; their `?v=` bumps were superseded by the
   fingerprint migration — stated explicitly, not silently dropped).
4. Re-run the full gate on the MERGED tree before pushing (the union had
   never been tested). Delete the stray branch after re-landing.

## Prevention
- **Before every commit in this repo: `git status -sb` and read the branch
  name.** A branch you didn't create = stop, reflog, find the sibling.
- Two sessions mutating one repo = one worktree each (CLAUDE.md "Parallel
  code agents"); the shared-checkout shortcut is what bit here.
- "Everything up-to-date" after fresh commits is never fine — it means the
  commits are not on the ref you think you're pushing.
