#!/bin/bash
# run_routes_tests.sh — parallel driver for the routes tier
# (tests/routes/test_*.py, Tier 3 Area 2, docs/roadmap/tier3-harness-design.md
# slice 3). Thin wrapper, NOT a new test-runner framework: it discovers the
# test files, splits them round-robin across N worker processes, and runs
# each worker's slice with plain `python3 -m unittest`. Every worker gets
# its OWN Postgres database (`sana_turnos_test_routes_<run>_w<N>`, run-scoped
# off TURNOS_ROUTES_RUN_ID — see tests/_routes_fixture.py) via
# TURNOS_ROUTES_WORKER=<N> in its environment — that's what makes running
# them at the same time safe, both across workers of one run AND across two
# concurrent invocations (each gets a disjoint run token, so neither can drop
# the other's live worker DB). This run's databases are dropped on exit.
#
# Usage:
#   scripts/run_routes_tests.sh [--workers N]
#
# Exit code is non-zero if ANY worker's slice fails — same fail-loud
# doctrine as the e2e harness's requireOk(): no silent partial green.

set -u
# No orphans (2026-10-25): own process group, trap + watcher reap every worker
# when this run ends or its parent dies — see scripts/orphan_guard.sh.
. "$(dirname "$0")/orphan_guard.sh"
orphan_guard_init "$0" "$@"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1

if [ -z "${TEST_DATABASE_URL:-}" ] && [ -z "${DATABASE_URL:-}" ]; then
    echo "run_routes_tests.sh: a managed test database is required; skipped routes are not a green gate." >&2
    exit 2
fi

# Cross-run isolation (2026-08-08): a token unique to THIS invocation, shared
# by all of this run's workers. The worker DB names are run-scoped off it
# (tests/_routes_fixture.py:_worker_db_name), so two concurrent full-CI runs
# get disjoint database namespaces and neither can DROP the other's live
# worker DB (the AdminShutdown collision on the old fixed w0..w3 names).
# Within-run parallelism is unchanged — workers still differ by index.
#
# UNCONDITIONALLY minted, regardless of anything already in the environment:
# a `if [ -z "${TURNOS_ROUTES_RUN_ID:-}" ]` guard here would let a CI shell
# that exports one fixed value for its whole job (a real pattern — job-level
# env, not per-step) make every invocation within that job inherit the SAME
# token, recreating the exact shared-database collision this mechanism
# exists to prevent. `$$` (this script's own PID) plus `$RANDOM` are unique
# per process launch even when two invocations share a parent shell's
# exported environment, so this always disagrees with a sibling invocation.
#
# The leading `t<hex-epoch>` stamps the token with its creation time so a
# LATER invocation can reclaim namespaces orphaned by a SIGKILL'd runner
# (an EXIT trap never fires on SIGKILL/power loss) purely from the database
# NAME — see reclaim_stale_run_databases in tests/_routes_fixture.py. Hex
# keeps the separator letters `p`/`r` out of the stamp's alphabet, so the
# token parses unambiguously, and the whole token stays under the fixture
# sanitizer's 24-char cap.
TURNOS_ROUTES_RUN_ID="t$(printf '%x' "$(date +%s)")p$$r${RANDOM}"
export TURNOS_ROUTES_RUN_ID

# No stale buildup: drop THIS run's run-scoped databases on exit. The helper
# filters by the exact run prefix, so it only ever drops our own databases,
# never a concurrent sibling run's. Cleanup is OBSERVABLE (prints what it
# dropped) and FAIL-LOUD. Exit-status policy so neither gate can false-green:
#   - tests failed          -> return the test failure code (dominates);
#   - tests green, cleanup ok-> 0;
#   - tests green, cleanup failed -> nonzero (a leftover-database failure is a
#     real "no stale buildup" gate failure, not a warning to shrug off).
cleanup_run_dbs() {
    # The token and repo path travel as DATA (environment variables), never
    # interpolated into Python source: a hostile-looking externally supplied
    # TURNOS_ROUTES_RUN_ID (quotes, semicolons, newlines) cannot break or
    # inject this command — Python reads it via os.environ, and the fixture's
    # own _sanitize_token reduces it to [a-z0-9] before any SQL use. The
    # single-quoted heredoc-style -c source contains NO shell expansions.
    TURNOS_ROUTES_REPO="$REPO" python3 -c '
import os, sys
repo = os.environ["TURNOS_ROUTES_REPO"]
sys.path[:0] = [repo, os.path.join(repo, "tests")]
import _routes_fixture as f
f.drop_run_databases(os.environ.get("TURNOS_ROUTES_RUN_ID"))
'
}
_on_exit() {
    rc=$?
    # Workers first: a run's databases drop only once no worker holds them.
    orphan_guard_finish
    if cleanup_run_dbs; then
        exit "$rc"
    fi
    echo "run_routes_tests.sh: cleanup of run $TURNOS_ROUTES_RUN_ID's databases FAILED (see above); run-scoped databases may remain." >&2
    if [ "$rc" -ne 0 ]; then
        exit "$rc"      # a test failure still dominates the report
    fi
    exit 1              # tests were green but cleanup failed -> not a clean run
}
trap _on_exit EXIT

# Reclaim namespaces a SIGKILL'd/power-lost sibling runner left behind (its
# EXIT trap never fired). Narrowly bounded — only stamped tokens older than
# the fixture's max age, with zero live sessions, dropped without FORCE; the
# current run, fresh runs, legacy fixed names and lookalikes all survive (see
# reclaim_stale_run_databases). An unreachable Postgres only warns — the
# tests below need that same server and fail with a real test signal — but
# any error PAST the connect is a red run, not a shrugged warning (same
# fail-loud doctrine as the exit cleanup).
reclaim_stale_dbs() {
    TURNOS_ROUTES_REPO="$REPO" python3 -c '
import os, sys
repo = os.environ["TURNOS_ROUTES_REPO"]
sys.path[:0] = [repo, os.path.join(repo, "tests")]
import _routes_fixture as f
f.reclaim_stale_run_databases()
'
}
if ! reclaim_stale_dbs; then
    echo "run_routes_tests.sh: stale-database reclamation FAILED (see above)." >&2
    exit 1
fi

WORKERS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --workers=*)
            WORKERS="${1#--workers=}"
            shift
            ;;
        *)
            echo "run_routes_tests.sh: opción desconocida: $1" >&2
            exit 2
            ;;
    esac
done

if [ -z "$WORKERS" ]; then
    WORKERS=$(python3 -c "import os; print(max(1, min(os.cpu_count() or 1, 4)))")
fi

# `mapfile` needs bash 4+; macOS ships bash 3.2 as /bin/bash, so this reads
# lines the portable way.
FILES=()
while IFS= read -r line; do
    [ -n "$line" ] && FILES+=("$line")
done < <(find tests/routes -maxdepth 1 -name 'test_*.py' -print | xargs -n1 basename | sort)

if [ "${#FILES[@]}" -eq 0 ]; then
    echo "run_routes_tests.sh: no hay archivos en tests/routes/test_*.py — nada que correr."
    exit 1
fi

echo "run_routes_tests.sh: ${#FILES[@]} archivo(s), $WORKERS worker(s)."

# Round-robin by FILENAME (not by test count) — simplest correct split,
# per the design doc. Each bucket runs its files as separate
# `unittest discover -s tests/routes -p <filename>` calls (unittest's
# path-to-module loader needs a real package, which tests/routes
# deliberately isn't — discover's own module loader handles a bare
# filename fine, so a bucket is a small sequential loop, not one call).
declare -a BUCKETS
for ((i = 0; i < WORKERS; i++)); do
    BUCKETS[$i]=""
done
idx=0
for f in "${FILES[@]}"; do
    w=$((idx % WORKERS))
    BUCKETS[$w]="${BUCKETS[$w]} $f"
    idx=$((idx + 1))
done

TMPDIR_LOGS=$(mktemp -d)
declare -a PIDS
declare -a PIDS_WORKER

for ((w = 0; w < WORKERS; w++)); do
    files="${BUCKETS[$w]}"
    if [ -z "${files// /}" ]; then
        continue
    fi
    (
        export TURNOS_ROUTES_WORKER="$w"
        export TURNOS_FAKE_AI=1
        rc=0
        for fname in $files; do
            file_log="$TMPDIR_LOGS/${w}_${fname}.log"
            python3 -W error::ResourceWarning -m unittest discover \
                -s tests/routes -p "$fname" -v >"$file_log" 2>&1
            fw=$?
            cat "$file_log"
            if [ "$fw" -eq 0 ]; then
                python3 scripts/check_unittest_coverage.py "$file_log" --minimum 1
                fw=$?
            fi
            if [ "$fw" -ne 0 ]; then
                rc=$fw
            fi
        done
        exit $rc
    ) >"$TMPDIR_LOGS/worker_$w.log" 2>&1 &
    PIDS+=("$!")
    PIDS_WORKER+=("$w")
done

STATUS=0
for i in "${!PIDS[@]}"; do
    pid="${PIDS[$i]}"
    w="${PIDS_WORKER[$i]}"
    if ! wait "$pid"; then
        echo "---- worker $w FAILED (log: $TMPDIR_LOGS/worker_$w.log) ----"
        cat "$TMPDIR_LOGS/worker_$w.log"
        STATUS=1
    fi
done

if [ "$STATUS" -eq 0 ]; then
    echo "run_routes_tests.sh: todos los workers OK."
    # Only the logs of a clean run are throwaway; a failing run's logs were
    # already printed above for the human to read.
    rm -rf "$TMPDIR_LOGS"
else
    echo "run_routes_tests.sh: logs completos en $TMPDIR_LOGS"
fi

exit $STATUS
