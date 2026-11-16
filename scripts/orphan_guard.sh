#!/bin/bash
# orphan_guard.sh — a long test runner never leaves orphans behind (2026-10-25).
#
# Sourced by scripts/pre-commit.sh and scripts/run_routes_tests.sh. On
# 2026-10-25 the dev machine carried ~60 orphaned run_routes_tests.sh trees (PPID 1,
# unittest workers alive, load 130-300): a Claude Code background-task limit
# kills only the shell it started, so the runner was reparented to PID 1 and
# its workers ran on with nobody to stop them. The guard:
#   1. moves the runner into its own process group (perl setpgrp + exec, the
#      PID does not change) unless it already leads one or runs inside a
#      guarded runner's group (run_routes_tests.sh under pre-commit.sh);
#   2. on EXIT/HUP/INT/TERM, TERMs and then KILLs every member of that group
#      that descends from the runner or was orphaned to PID 1 — never a
#      pipeline peer such as `| tee`, which shares the group but not the tree;
#   3. starts a watcher (this file executed with --watch) that does the same
#      as soon as the runner dies (SIGKILL: no trap fires) or its parent dies
#      (the runner is reparented; the watcher TERMs it so its own EXIT trap —
#      the routes tier's database cleanup — still runs, then reaps the rest).
# `orphan_guard_run cmd…` runs a long foreground command in the background and
# waits for it: bash defers a trap until a foreground child returns, `wait`
# does not.
#
# Tunables (tests): TURNOS_ORPHAN_GUARD_INTERVAL (watcher poll, seconds,
# default 1), TURNOS_ORPHAN_GUARD_GRACE (TERM -> KILL, seconds, default 10).
# Without `ps` (or `perl` for step 1) the guard degrades to doing nothing.

_orphan_guard_file="${BASH_SOURCE[0]}"
case "$_orphan_guard_file" in
    /*) ;;
    *) _orphan_guard_file="$(pwd)/$_orphan_guard_file" ;;
esac

# Members of process group $1 to reap: descendants of $2 (not through a
# pruned pid) plus group members orphaned to PID 1. $3: space-separated pids
# whose whole subtree is spared (the reaper itself and its ps/awk); $4: pids
# spared alone (the runner while its own trap runs, its watcher). Zombies are
# skipped.
_orphan_guard_targets() {
    ps -A -o pid=,ppid=,pgid=,stat= | awk -v pgid="$1" -v root="$2" \
        -v prune=" $3 " -v skip=" $4 " '
        { pp[$1] = $2; pg[$1] = $3; st[$1] = $4 }
        END {
            if (!index(prune, " " root " ")) mine[root] = 1
            changed = 1
            while (changed) {
                changed = 0
                for (p in pp) {
                    if ((p in mine) || index(prune, " " p " ")) continue
                    if (pp[p] in mine) { mine[p] = 1; changed = 1 }
                }
            }
            for (p in pp) {
                if (pg[p] != pgid || st[p] ~ /^Z/) continue
                if (index(prune, " " p " ") || index(skip, " " p " ")) continue
                if ((p in mine) || pp[p] == 1) print p
            }
        }'
}

# Reap: TERM every target once (newcomers too), KILL what is left after the
# grace period. Runs as its own process so $$ is the reaper's pid.
_orphan_guard_reap() {
    local pgid="$1" root="$2" prune="$3 $$" skip="${4:-}"
    local grace="${TURNOS_ORPHAN_GUARD_GRACE:-10}" ticks=0 sent=" " pids p
    while :; do
        pids="$(_orphan_guard_targets "$pgid" "$root" "$prune" "$skip")"
        [ -n "$pids" ] || return 0
        if [ "$ticks" -ge $((grace * 10)) ]; then
            kill -KILL $pids 2>/dev/null
            return 0
        fi
        for p in $pids; do
            case "$sent" in
                *" $p "*) ;;
                *) kill -TERM "$p" 2>/dev/null; sent="$sent$p " ;;
            esac
        done
        sleep 0.1
        ticks=$((ticks + 1))
    done
}

# "<ppid> <stat>" of a live process, empty for a gone one or a zombie.
_orphan_guard_state() {
    ps -o ppid=,stat= -p "$1" | awk '$2 !~ /^Z/ { print $1, $2 }'
}

_orphan_guard_alive() {
    [ -n "$(_orphan_guard_state "$1")" ]
}

# Watcher: poll until the runner $2 is gone or no longer a child of $3.
_orphan_guard_watch() {
    local pgid="$1" root="$2" parent="$3"
    local interval="${TURNOS_ORPHAN_GUARD_INTERVAL:-1}"
    local grace="${TURNOS_ORPHAN_GUARD_GRACE:-10}" ticks=0 state
    while :; do
        state="$(_orphan_guard_state "$root")"
        [ "${state%% *}" = "$parent" ] || break
        sleep "$interval"
    done
    trap '' HUP INT TERM
    if _orphan_guard_alive "$root"; then
        echo "orphan_guard: runner $root lost its parent $parent; stopping it." >&2
        kill -TERM "$root" 2>/dev/null
        while _orphan_guard_alive "$root" && [ "$ticks" -lt $((grace * 10)) ]; do
            sleep 0.1
            ticks=$((ticks + 1))
        done
    fi
    _orphan_guard_reap "$pgid" "$root" ""
}

# orphan_guard_init "$0" "$@" — call once, before any `cd`.
orphan_guard_init() {
    command -v ps >/dev/null 2>&1 || return 0
    local pgid
    pgid="$(ps -o pgid= -p $$ | tr -d ' ')"
    if [ "$pgid" != "$$" ] && [ "${TURNOS_ORPHAN_GUARD_PGID:-}" != "$pgid" ] \
        && [ "${_TURNOS_ORPHAN_GUARD_REEXEC:-}" != "$$" ] \
        && command -v perl >/dev/null 2>&1; then
        export _TURNOS_ORPHAN_GUARD_REEXEC=$$
        exec perl -e 'setpgrp(0, 0); exec { $ARGV[0] } @ARGV or die "orphan_guard: exec: $!\n"' \
            "$BASH" "$@"
    fi
    pgid="$(ps -o pgid= -p $$ | tr -d ' ')"
    export TURNOS_ORPHAN_GUARD_PGID="$pgid"
    _ORPHAN_GUARD_PGRP="$pgid"
    _ORPHAN_GUARD_DONE=""
    "$BASH" "$_orphan_guard_file" --watch "$pgid" "$$" "$PPID" </dev/null >/dev/null &
    _ORPHAN_GUARD_WATCHER=$!
    trap '_orphan_guard_on_signal 1' HUP
    trap '_orphan_guard_on_signal 2' INT
    trap '_orphan_guard_on_signal 15' TERM
    trap '_orphan_guard_on_exit' EXIT
}

# Reap this runner's leftovers and stop its watcher. A runner with its own
# EXIT trap calls this first in it.
orphan_guard_finish() {
    [ -n "${_ORPHAN_GUARD_PGRP:-}" ] && [ -z "${_ORPHAN_GUARD_DONE:-}" ] || return 0
    _ORPHAN_GUARD_DONE=1
    kill -TERM "$_ORPHAN_GUARD_WATCHER" 2>/dev/null
    # The watcher is spared alone: a normal one died of the TERM above, a
    # reaping one ignores TERM and leaves once this runner is gone.
    "$BASH" "$_orphan_guard_file" --reap "$_ORPHAN_GUARD_PGRP" "$$" "" \
        "$$ $_ORPHAN_GUARD_WATCHER"
}

orphan_guard_run() {
    "$@" &
    wait $!
}

_orphan_guard_on_exit() {
    local rc=$?
    orphan_guard_finish
    exit "$rc"
}

_orphan_guard_on_signal() {
    trap '' HUP INT TERM
    orphan_guard_finish
    trap - HUP INT TERM
    exit $((128 + $1))
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-}" in
        --watch) shift; _orphan_guard_watch "$@" ;;
        --reap) shift; _orphan_guard_reap "$@" ;;
        *) echo "usage: source scripts/orphan_guard.sh; orphan_guard_init \"\$0\" \"\$@\"" >&2; exit 2 ;;
    esac
    exit $?
fi
