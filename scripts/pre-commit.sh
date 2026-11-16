#!/bin/bash
# pre-commit.sh — Tier 3 slice 7, run-ladder rung 2 (Area 4 of
# docs/roadmap/tier3-harness-design.md). BLOCKING: a non-zero exit here
# fails `git commit`. Runs the frontend suite (836+ tests, stdlib
# `node --test`) + exact Accounts render contracts + the full routes tier (Area 2,
# scripts/run_routes_tests.sh, parallel, Postgres advisory-lock pool) — the
# design's own budget is "under ~20s target", measured combined runtime is
# ~2s (see docs/roadmap/tier3-harness-design.md §Execution slices, slice 7).
#
# Does NOT run the full backend `unittest discover` suite (2900+ tests,
# minutes) — that stays a CI/nightly-rung concern per the design's own
# cost-tiering: pre-commit only re-runs what's fast enough that nobody
# reaches for `--no-verify` out of impatience.
#
# Bypass for genuine emergencies (documented escape hatch, per the design's
# own test plan — "never used on a final pre-push commit"):
#   SKIP_PRECOMMIT=1 git commit ...
#
# Installed via scripts/install-hooks.sh into .git/hooks/pre-commit.

set -u
# No orphans (2026-10-25): own process group, trap + watcher reap every child
# when this hook ends or its parent dies — see scripts/orphan_guard.sh.
. "$(dirname "$0")/orphan_guard.sh"
orphan_guard_init "$0" "$@"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1

if [ "${SKIP_PRECOMMIT:-}" = "1" ]; then
    echo "pre-commit.sh: SKIP_PRECOMMIT=1 — se salta el run ladder (no usar antes de un push final)."
    exit 0
fi

STATUS=0
if ! node scripts/check-retired-client-sources.mjs; then
    echo "pre-commit.sh: blocked by a physically restored retired client source."
    exit 1
fi

echo "pre-commit.sh: Alembic single-head guard (fast DB-free check)..."
if ! python3 scripts/check_alembic_single_head.py; then
    echo "pre-commit.sh: blocked by multiple Alembic heads."
    exit 1
fi
REPORT_DIR="${TURNOS_TEST_ARTIFACT_DIR:-${HOME}/.claude/artifacts/human-rounds/verification}"
mkdir -p "$REPORT_DIR" || exit 2
LOG="$(mktemp "$REPORT_DIR/frontend.XXXXXX")" || exit 2

echo "pre-commit.sh: frontend suite (node --test)..."
if ! orphan_guard_run node scripts/run_frontend_tests.mjs >"$LOG" 2>&1; then
    echo "---- frontend suite FAILED ----"
    tail -n 40 "$LOG"
    echo "Full failing frontend report preserved at: $LOG"
    STATUS=1
else
    rm -f "$LOG"
fi

echo "pre-commit.sh: exact Accounts renders (shared asset manifest contract)..."
ACCOUNT_LOG="$(mktemp "$REPORT_DIR/accounts.XXXXXX")" || exit 2
if ! orphan_guard_run env TURNOS_FAKE_AI=1 TURNOS_ENV_FILE=/dev/null python3 -W error::ResourceWarning \
    -m unittest discover -s tests -p test_accounts_jinja.py >"$ACCOUNT_LOG" 2>&1; then
    echo "---- Accounts render suite FAILED ----"
    tail -n 40 "$ACCOUNT_LOG"
    echo "Full failing Accounts report preserved at: $ACCOUNT_LOG"
    STATUS=1
else
    rm -f "$ACCOUNT_LOG"
fi

# The colima test lane's disk (2026-11-07: 100% full, every routes tier red with
# DiskFull): drop day-old idle test databases, warn past 85%. Never blocks.
orphan_guard_run python3 scripts/lane_janitor.py || true

echo "pre-commit.sh: routes tier (scripts/run_routes_tests.sh)..."
if [ -z "${TEST_DATABASE_URL:-}" ] && [ -z "${DATABASE_URL:-}" ]; then
    orphan_guard_run python3 scripts/turnos_managed_exec.py --profile routes || STATUS=1
else
    orphan_guard_run scripts/run_routes_tests.sh || STATUS=1
fi

if [ "$STATUS" -ne 0 ]; then
    echo "pre-commit.sh: BLOQUEADO — hay rojo arriba. Bypass de emergencia:"
    echo "  SKIP_PRECOMMIT=1 git commit ..."
else
    echo "pre-commit.sh: OK — frontend + Accounts + routes tier verdes."
fi

exit $STATUS
