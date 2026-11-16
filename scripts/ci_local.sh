#!/bin/bash
# ci_local.sh — the complete Tier 3 run ladder, one command.
#
# Runs the same five surfaces hosted `ci.yml` runs — backend, frontend,
# routes, cassette replay + audit, e2e — with the same env shape (see each
# surface's inline comment for the exact parity claim). This is now the
# ENFORCING path: hosted CI's `push`/`pull_request` triggers are disabled
# (cost decision, product owner 2026-08-05 — see .github/workflows/ci.yml's header),
# so nothing runs this ladder except a developer's pre-commit hook
# (scripts/pre-commit.sh — frontend + routes only, its own faster subset)
# and this script, either by hand or via the dev machine nightly LaunchAgent
# (scripts/nightly-local.plist).
#
# Design: run ALL FIVE surfaces every time, never stop at the first red —
# a nightly run (or a developer deciding whether to push) needs the whole
# picture, not just "the first thing that broke." Each surface is still
# "fail-fast" internally (its own runner exits as soon as IT fails; no
# surface's failure changes how the next surface runs — they don't share
# state). The script's own exit code is non-zero if ANY surface failed,
# and the final summary table names exactly which ones.
#
# Usage:
#   scripts/ci_local.sh
#
# Requires: a reachable Postgres injected by a fixed managed local-CI profile
# (`local-ci` for legacy mode or `local-ci-asgi` for executor-fixed ASGI mode;
# DATABASE_URL/TEST_DATABASE_URL), Node 20+, Playwright's Chromium
# installed (npx playwright install chromium). A missing prerequisite
# FAILS the relevant surface loudly — this script never silently skips one
# (mirrors the e2e gate's own requireOk() doctrine, extended to all five).

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1

export TURNOS_FAKE_AI="${TURNOS_FAKE_AI:-1}"

# This runner intentionally never opens the checkout .env. Local secret
# material arrives only from scripts/turnos_managed_exec.py's scrubbed
# local-CI profiles; production does not use this local runner.
if [ -z "${DATABASE_URL:-}" ] && [ -z "${TEST_DATABASE_URL:-}" ]; then
    echo "ci_local.sh: DATABASE_URL/TEST_DATABASE_URL must be injected by the managed local-ci profile." >&2
    exit 2
fi
if [ -z "${DATABASE_URL:-}" ]; then
    export DATABASE_URL="$TEST_DATABASE_URL"
fi
if [ -z "${TEST_DATABASE_URL:-}" ]; then
    export TEST_DATABASE_URL="$DATABASE_URL"
fi

RESULTS=()
FAILED=0

# Coverage measurement (coverage.py for the backend, c8 for the frontend; scripts/coverage_floor.json
# holds the floors). In CI — $CI / $GITLAB_CI / $GITHUB_ACTIONS, or TURNOS_REQUIRE_COVERAGE=1, which the
# managed local-ci-asgi recipes of the CI machines set because that profile replaces the environment —
# the tools are REQUIRED: a missing one fails the surface, and TURNOS_COVERAGE=0 is ignored. On a
# developer machine (the dev machine) a missing tool must not break the run: the measurement is skipped with a
# loud warning, and the test-count gates still apply. TURNOS_COVERAGE=0 skips it on purpose.
COVERAGE_REQUIRED=0
if [ -n "${CI:-}" ] || [ -n "${GITLAB_CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ] \
        || [ "${TURNOS_REQUIRE_COVERAGE:-}" = "1" ]; then
    COVERAGE_REQUIRED=1
fi
COVERAGE_WARNINGS=""
UNAVAILABLE_NOTE=""
_coverage_unavailable() {
    # _coverage_unavailable <surface> <what is missing and how to get it>
    # Returns 1 (surface fails) when coverage is required, 0 (skip) otherwise.
    if [ "$COVERAGE_REQUIRED" -eq 1 ]; then
        echo "$1: coverage REQUIRED here (CI) but $2" >&2
        return 1
    fi
    echo "" >&2
    echo "!!!! WARNING: $1 coverage NOT MEASURED — $2 (floors in scripts/coverage_floor.json NOT checked; CI will check them)" >&2
    echo "" >&2
    UNAVAILABLE_NOTE="$1-coverage-skipped"
    COVERAGE_WARNINGS="$COVERAGE_WARNINGS $UNAVAILABLE_NOTE"
    return 0
}

# Keep every surface's evidence outside the checkout at creation time. A short
# failure tail is useful on screen, but must never be the only surviving log.
umask 077
DEFAULT_ARTIFACT_ROOT=~/.claude/artifacts/human-rounds/local-ci
ARTIFACT_ROOT="${TURNOS_CI_ARTIFACTS_DIR:-$DEFAULT_ARTIFACT_ROOT}"
mkdir -p "$ARTIFACT_ROOT" || exit 2
RUN_ARTIFACTS="$(mktemp -d "$ARTIFACT_ROOT/run.XXXXXX")" || exit 2
echo "Full surface logs: $RUN_ARTIFACTS"

_section() {
    echo ""
    echo "==> $1"
}

_record() {
    # _record <label> <exit_code> <seconds> <detail>
    RESULTS+=("$1|$2|$3|$4")
    [ "$2" -ne 0 ] && FAILED=1
}

# ---- 1) Backend (stdlib unittest, Postgres-backed) -------------------
# Default: scripts/run_backend_shards.py splits tests/test_*.py into parallel
# shards (TURNOS_BACKEND_SHARDS, default 5), each on its own database, and
# merges them into the same result report the coverage guard reads.
# TURNOS_BACKEND_SERIAL=1 keeps the one-process run (unittest discover over
# tests/test_*.py) — the reference when a sharded failure needs ruling out.
# The sharded run is also measured: every test file runs under coverage.py (branch mode),
# the data is combined into $COVERAGE_DIR and scripts/coverage_floor.py check turns a package
# that fell below its floor (scripts/coverage_floor.json) into a red surface. The serial run
# and TURNOS_COVERAGE=0 (a machine without coverage.py: pip install -r
# scripts/requirements-ci.txt) measure nothing and say so.
t0=$(date +%s)
LOG="$RUN_ARTIFACTS/backend.log"
REPORT="$RUN_ARTIFACTS/backend-results.json"
COVERAGE_DIR="$RUN_ARTIFACTS/backend-coverage"
COVERAGE_ARGS=(--coverage-dir "$COVERAGE_DIR")
backend_setup_exit=0
UNAVAILABLE_NOTE=""
if [ "${TURNOS_BACKEND_SERIAL:-}" = "1" ]; then
    COVERAGE_ARGS=()
elif [ "${TURNOS_COVERAGE:-1}" = "0" ] && [ "$COVERAGE_REQUIRED" -eq 0 ]; then
    COVERAGE_ARGS=()
    _coverage_unavailable backend "TURNOS_COVERAGE=0"
elif ! python3 -c "import coverage" >/dev/null 2>&1; then
    COVERAGE_ARGS=()
    _coverage_unavailable backend "coverage.py is not installed (python3 -m pip install -r scripts/requirements-ci.txt)" \
        || backend_setup_exit=1
fi
backend_note="$UNAVAILABLE_NOTE"
if [ "$backend_setup_exit" -ne 0 ]; then
    echo "coverage.py is missing and coverage is required" >"$LOG"
    backend_exit=1
elif [ "${TURNOS_BACKEND_SERIAL:-}" = "1" ]; then
    _section "Backend suite, serial (scripts/run_backend_tests.py)"
    python3 -W error::ResourceWarning scripts/run_backend_tests.py --report "$REPORT" >"$LOG" 2>&1
    backend_exit=$?
else
    _section "Backend suite, sharded (scripts/run_backend_shards.py)"
    # The full output goes to $LOG; fd 3 is this script's own stdout, where the runner also writes
    # its heartbeats (every TURNOS_BACKEND_HEARTBEAT s), the death of a file with its signal or
    # exit code, the stop (TURNOS_BACKEND_DEADLINE) and the verdict — a backgrounded run is never
    # silent for an hour (05.11).
    python3 scripts/run_backend_shards.py --report "$REPORT" \
        --timings-out "$RUN_ARTIFACTS/backend-timings.json" \
        --seed-out "$RUN_ARTIFACTS/backend-timings-seed.json" --progress-fd 3 \
        --log-dir "$RUN_ARTIFACTS/backend-shards" ${COVERAGE_ARGS[@]+"${COVERAGE_ARGS[@]}"} 3>&1 >"$LOG" 2>&1
    backend_exit=$?
fi
t1=$(date +%s)
if [ "$backend_exit" -eq 0 ]; then
    python3 scripts/check_unittest_coverage.py "$LOG" --result-json "$REPORT"
    backend_exit=$?
fi
if [ "$backend_exit" -eq 0 ]; then
    if [ "${#COVERAGE_ARGS[@]}" -gt 0 ]; then
        python3 scripts/coverage_floor.py check --summary "$COVERAGE_DIR/coverage-summary.json" \
            --report "$REPORT"
        backend_exit=$?
    else
        echo "coverage floor: NOT checked (serial run, or coverage skipped above)"
    fi
fi
ran=$(grep -oE "Ran [0-9]+ tests?" "$LOG" | tail -1 | grep -oE "[0-9]+")
ran="${ran:-0}"
skipped=$(grep -oE "skipped=[0-9]+" "$LOG" | tail -1 | grep -oE "[0-9]+")
skipped="${skipped:-0}"
detail="ran=$ran skipped=$skipped result_report=$REPORT${backend_note:+ WARNING:$backend_note}"
if [ "$backend_exit" -ne 0 ]; then
    tail -n 60 "$LOG"
fi
echo "backend: exit=$backend_exit $detail ($((t1 - t0))s)"
_record "backend" "$backend_exit" "$((t1 - t0))" "$detail"

# ---- 2) Frontend (node --test, stdlib only) -------------------------------
_section "Frontend suite (scripts/run_frontend_tests.mjs: browser files one at a time)"
# Measured with c8 (scripts/c8rc.json; devDependency, `npm ci`): what the node processes of the tests
# execute, summed per directory against scripts/coverage_floor.json (js). TURNOS_COVERAGE=0 skips it.
t0=$(date +%s)
LOG="$RUN_ARTIFACTS/frontend.log"
FRONTEND_COVERAGE_DIR="$RUN_ARTIFACTS/frontend-coverage"
UNAVAILABLE_NOTE=""
if [ "${TURNOS_COVERAGE:-1}" = "0" ] && [ "$COVERAGE_REQUIRED" -eq 0 ]; then
    _coverage_unavailable frontend "TURNOS_COVERAGE=0"
    node scripts/run_frontend_tests.mjs >"$LOG" 2>&1
    frontend_exit=$?
elif [ ! -x node_modules/.bin/c8 ]; then
    if _coverage_unavailable frontend "node_modules/.bin/c8 is missing (npm ci)"; then
        node scripts/run_frontend_tests.mjs >"$LOG" 2>&1
        frontend_exit=$?
    else
        echo "frontend: node_modules/.bin/c8 is missing and coverage is required (npm ci)" >"$LOG"
        frontend_exit=1
    fi
else
    node_modules/.bin/c8 --config scripts/c8rc.json --reports-dir "$FRONTEND_COVERAGE_DIR" \
        --temp-directory "$RUN_ARTIFACTS/frontend-v8" node scripts/run_frontend_tests.mjs >"$LOG" 2>&1
    frontend_exit=$?
    if [ "$frontend_exit" -eq 0 ]; then
        python3 scripts/coverage_floor.py check --kind js \
            --summary "$FRONTEND_COVERAGE_DIR/coverage-summary.json" >>"$LOG" 2>&1
        frontend_exit=$?
    fi
fi
frontend_note="$UNAVAILABLE_NOTE"
# The test-count floor (min_js_tests) applies with or without coverage.
if [ "$frontend_exit" -eq 0 ]; then
    python3 scripts/coverage_floor.py count --log "$LOG" >>"$LOG" 2>&1
    frontend_exit=$?
fi
t1=$(date +%s)
detail=$(grep -E "^# (tests|pass|fail) " "$LOG" | tr '\n' ' ')${frontend_note:+ WARNING:$frontend_note}
[ "$frontend_exit" -ne 0 ] && tail -n 60 "$LOG"
echo "frontend: exit=$frontend_exit $detail ($((t1 - t0))s)"
_record "frontend" "$frontend_exit" "$((t1 - t0))" "$detail"

# ---- 3) Routes tier (parallel, disposable fixtures) -----------------------
_section "Routes tier (scripts/run_routes_tests.sh)"
t0=$(date +%s)
LOG="$RUN_ARTIFACTS/routes.log"
scripts/run_routes_tests.sh >"$LOG" 2>&1
routes_exit=$?
t1=$(date +%s)
detail=$(tail -1 "$LOG")
[ "$routes_exit" -ne 0 ] && tail -n 60 "$LOG"
echo "routes: exit=$routes_exit $detail ($((t1 - t0))s)"
_record "routes" "$routes_exit" "$((t1 - t0))" "$detail"

# ---- 4) Cassette replay + staleness/shape audit ---------------------------
_section "Cassette replay (HSI, PUCO, RENAPER, Meta)"
t0=$(date +%s)
LOG="$RUN_ARTIFACTS/cassette-replay.log"
python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_*_cassette.py" >"$LOG" 2>&1
cassette_exit=$?
t1=$(date +%s)
detail=$(grep -E "^Ran [0-9]+ tests" "$LOG" | tail -1)
[ "$cassette_exit" -ne 0 ] && tail -n 60 "$LOG"
echo "cassette-replay: exit=$cassette_exit $detail ($((t1 - t0))s)"
_record "cassette-replay" "$cassette_exit" "$((t1 - t0))" "$detail"

t0=$(date +%s)
LOG="$RUN_ARTIFACTS/cassette-audit.log"
python3 scripts/audit_cassettes.py >"$LOG" 2>&1
audit_exit=$?
t1=$(date +%s)
detail=$(tail -1 "$LOG")
[ "$audit_exit" -ne 0 ] && cat "$LOG"
echo "cassette-audit: exit=$audit_exit $detail ($((t1 - t0))s)"
_record "cassette-audit" "$audit_exit" "$((t1 - t0))" "$detail"

# ---- 5) Browser e2e (Playwright, gate mode — missing prereq FAILS) --------
_section "E2E gate (npm run e2e)"
t0=$(date +%s)
LOG="$RUN_ARTIFACTS/e2e.log"
# Playwright Test (playwright.config.mjs): the HTML report and the per-test
# traces/screenshots go beside the surface logs, never into the checkout.
# Four files at a time: one per core (18 on the dev machine, each file its own app server,
# database and browser, Postgres in a small VM) timed out 14 tests in the first
# full run on 25.10; four is what the MR 68 measurements ran clean under load.
PW_WORKERS="${PW_WORKERS:-4}" \
PW_HTML_DIR="$RUN_ARTIFACTS/e2e-report" PW_OUTPUT_DIR="$RUN_ARTIFACTS/e2e-results" \
    npm run e2e >"$LOG" 2>&1
e2e_exit=$?
t1=$(date +%s)
detail=$(grep -E "^ +[0-9]+ (passed|failed|flaky|skipped|did not run)" "$LOG" | sed -E 's/^ +//' | tr '\n' ' ')
[ "$e2e_exit" -ne 0 ] && tail -n 80 "$LOG"
echo "e2e: exit=$e2e_exit $detail ($((t1 - t0))s)"
_record "e2e" "$e2e_exit" "$((t1 - t0))" "$detail"

# ---- Summary ---------------------------------------------------------------
echo ""
echo "==================== ci_local.sh summary ===================="
printf "%-18s %-6s %-8s %s\n" "SURFACE" "EXIT" "TIME" "DETAIL"
for r in "${RESULTS[@]}"; do
    IFS='|' read -r label code secs detail <<<"$r"
    verdict="OK"
    [ "$code" -ne 0 ] && verdict="FAIL"
    printf "%-18s %-6s %-8s %s\n" "$label ($verdict)" "$code" "${secs}s" "$detail"
done
echo "================================================================"

if [ -n "$COVERAGE_WARNINGS" ]; then
    echo "ci_local.sh: WARNING — coverage was NOT measured ($COVERAGE_WARNINGS ): the floors of scripts/coverage_floor.json were not checked here; CI checks them."
    echo "             backend: python3 -m pip install -r scripts/requirements-ci.txt   frontend: npm ci"
fi
if [ "$FAILED" -ne 0 ]; then
    echo "ci_local.sh: RED — see the surface(s) marked FAIL above."
    exit 1
fi
echo "ci_local.sh: GREEN — all five surfaces passed."
exit 0
