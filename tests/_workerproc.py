"""_workerproc.py — shared real-subprocess harness for `python3 worker.py`
suites (extracted VERBATIM from test_queue_worker.py the day a second
consumer appeared: test_business_queue_proof.py needs the exact same
process-safety discipline — own session, killpg escalation, DEVNULL
stdout — and copying it would fork the very lessons it encodes; see
docs/solutions/deploy/worker-topology-fuera-del-proceso-web.md lesson 6).

`WorkerProc` takes the COMPLETE child env (each suite owns its
per-module test DB URL, so the env can't live here); `worker_env()`
builds the hermetic baseline both suites share.
"""
import os
import signal
import subprocess
import sys
import threading
import time

import _wait

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def worker_env(test_url, extra=None):
    """Hermetic env for a real worker.py subprocess: the caller's
    per-module test DB, no repo .env (TURNOS_ENV_FILE points at an empty
    file), ephemeral health port."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "DATABASE_URL": test_url,
        "TURNOS_ENV_FILE": os.devnull,
        # Round-6 gate: worker.py refuses to run without an explicit
        # topology mode. The harness simulates the supervised child, so
        # the intended mode is "1"; a suite proving the unset/invalid
        # rejection overrides this via `extra`.
        "TURNOS_QUEUE_WORKER": "1",
        "TURNOS_FAKE_AI": "1",
        "TURNOS_SKIP_HSI": "1",
        "TURNOS_WORKER_HEALTH_PORT": "0",
        # The wait for a pooled connection (3 s by default, core/db.py) is a ceiling sized on a
        # quiet machine like every other wait here: at load ~180 the worker's first connection
        # took longer, `ensure_schema` raised DbUnavailable and the worker exited 1 (transient,
        # a supervisor restarts it) before its health line (2026-11-07). Read once, at spawn.
        "TURNOS_DB_POOL_TIMEOUT": "%g" % _wait.scaled(3.0),
    }
    env.update(extra or {})
    return env


def leaked_worker_pids(ps_output=None, me=None):
    """PIDs of `worker.py` processes of this checkout that THIS test process
    left running: its descendants, or orphans already reparented to PID 1.

    The suite-wide leak gate used to be a bare `pgrep -f <ROOT>/worker.py`,
    which sees every worker of the checkout — including the live, healthy one
    another suite process (a parallel backend shard) is driving at that very
    moment, so the gate failed a module for a worker it never started. A live
    worker of a sibling process hangs off THAT process, so it is neither a
    descendant of ours nor an orphan; a genuine leak still is one of the two.

    `ps_output` (`pid ppid command` lines) and `me` exist for the unit test.
    """
    marker = os.path.join(ROOT, "app", "worker.py")
    out = ps_output if ps_output is not None else subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    parent, workers = {}, []
    for line in out.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 2 or not (fields[0].isdigit() and fields[1].isdigit()):
            continue
        pid, ppid = int(fields[0]), int(fields[1])
        parent[pid] = ppid
        if len(fields) == 3 and marker in fields[2]:
            workers.append(pid)
    me = os.getpid() if me is None else me

    def ours(pid):
        seen = set()
        while pid in parent and pid not in seen:
            seen.add(pid)
            pid = parent[pid]
            if pid == me:
                return True
        return False

    return [pid for pid in workers if parent[pid] == 1 or ours(pid)]


class WorkerProc:
    """`python3 worker.py` + a stderr reader thread. `wait_line(substr)`
    blocks until the worker logs it (or the deadline passes).

    Process-safety (2026-08-08, after a real hang): the child runs in its
    OWN session (`start_new_session=True`) so cleanup can `killpg` the
    whole group, and stdout goes to DEVNULL — a worker inheriting the test
    harness's stdout pipe keeps that pipe open forever if it outlives the
    runner, hanging whatever reads it. Every spawn path here must leave
    NOTHING running after the suite (cleanup() kills the group, and stop()
    escalates SIGTERM → killpg SIGKILL on timeout)."""

    # What CPython's faulthandler writes first, per thread, when the worker
    # gets SIGUSR2 (see stop()): the thread-stack dump is made of these.
    THREAD_DUMP_HEADER = "(most recent call first):"
    # Directory with the test-only sitecustomize that registers that handler.
    _FH_SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fh_site")

    def __init__(self, env, diagnostic=False):
        self.diagnostic = diagnostic
        self._diagnostic_ready = threading.Event()
        # Every worker child registers faulthandler on SIGUSR2 (test-only, a
        # sitecustomize reached through PYTHONPATH), so a worker that overruns
        # its shutdown budget can be asked for the Python stack of EVERY
        # thread before the SIGKILL — the evidence of where it hangs (a lock,
        # a pool close, a signal handler). It writes only on that signal, so
        # a passing worker's stderr is unchanged. The signal is non-fatal on
        # purpose: SIGABRT made macOS write a crash report and show "Python
        # quit unexpectedly" for every dump (2026-11-11). py-spy would do the
        # same from outside, but on macOS it needs root.
        env = dict(env)
        env["PYTHONPATH"] = os.pathsep.join(
            [self._FH_SITE] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        command = [sys.executable, os.path.join(ROOT, "app", "worker.py")]
        if diagnostic:
            command = [sys.executable,
                       os.path.join(ROOT, "tests", "worker_diagnostics_bootstrap.py"),
                       os.path.join(ROOT, "app", "worker.py")]
        self.proc = subprocess.Popen(
            command,
            cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            start_new_session=True)
        self.lines = []
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def request_diagnostic_dump(self):
        """Ask the test-only bootstrap for a value-free await-chain snapshot."""
        if self.diagnostic and self._diagnostic_ready.is_set() and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGUSR1)
            return True
        return False

    def _failure_evidence(self):
        lines = list(self.lines)
        # A bounded dump may itself be longer than the historical 40-line
        # tail. Retain its beginning too: the owner task can be the first
        # task, not one of the final polling coroutines.
        starts = [i for i, line in enumerate(lines)
                  if line == "[worker-diagnostic] await-chain:begin"]
        snapshot = ""
        if starts:
            snapshot = "\n".join(lines[starts[-1]:starts[-1] + 194]) + "\n"
        return snapshot + ("\n".join(lines[-40:]) or "<no worker stderr>")

    def _thread_dump(self, wait=10):
        """The Python stack of every worker thread, taken after the shutdown
        budget is already spent and before the SIGKILL: SIGUSR2 makes the
        child's faulthandler (see __init__) write all thread stacks to stderr
        without ending the process (SIGABRT would, and macOS shows a crash
        window for it). `wait` bounds the wait for the stacks on a loaded
        machine; the worker has failed by then, so it buys it nothing."""
        before = len(self.lines)
        try:
            os.kill(self.proc.pid, signal.SIGUSR2)
        except (ProcessLookupError, PermissionError):
            return "<no thread dump: the worker was already gone>"
        def _header_or_exit():
            return (any(self.THREAD_DUMP_HEADER in line for line in self.lines[before:])
                    or self.proc.poll() is not None)

        try:
            _wait.until(_header_or_exit, "the faulthandler thread dump header", timeout=wait)
            # The other threads' stacks follow the first header: the dump is complete
            # once the stderr line count has held still (the reader thread appends them).
            mark = [len(self.lines), time.monotonic()]

            def _quiet():
                now = len(self.lines)
                if now != mark[0]:
                    mark[0], mark[1] = now, time.monotonic()
                return time.monotonic() - mark[1] >= 0.3
            _wait.until(_quiet, "the thread dump to stop growing", timeout=5)
        except AssertionError:
            pass
        lines = list(self.lines[before:])
        starts = [i for i, line in enumerate(lines) if self.THREAD_DUMP_HEADER in line]
        if not starts:
            return "<no thread dump: faulthandler wrote nothing>"
        return "\n".join(lines[starts[0]:starts[0] + 300])

    def _killpg(self):
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def _read(self):
        for line in self.proc.stderr:
            line = line.rstrip("\n")
            self.lines.append(line)
            if line == "[worker-diagnostic] ready":
                self._diagnostic_ready.set()

    def wait_line(self, substr, timeout=20):
        """The worker's own log line `substr` (the readiness flip logs "corriendo" in the same
        synchronous step that sets `fase`, see worker._run_opened_pool), or its exit — whichever comes
        first. `timeout` is the ceiling on a QUIET machine: a booting worker imports, opens its pool and
        recovers at the machine's speed, so the ceiling grows with the load (`_wait.load_factor()`).
        A fixed 20 s was red at load ~90 on 18 CPUs, where the same boot took 20+ s (2026-11-06)."""
        def seen():
            return next((line for line in self.lines if substr in line), None)

        # Stop waiting as soon as the line is there or the worker has exited.
        waited = None
        try:
            _wait.until(lambda: seen() is not None or self.proc.poll() is not None,
                        f"worker logs {substr!r} or exits", timeout=timeout,
                        scale_with_load=True)
        except AssertionError as exc:
            waited = str(exc)
        line = seen()
        if line is not None:
            return line
        raise AssertionError(
            f"worker nunca logueó {substr!r} (exit={self.proc.poll()}; {waited or 'exited'}); stderr:\n"
            + "\n".join(self.lines))

    def output_after_exit(self, timeout=10):
        """Everything the worker wrote to stderr, once it has exited. `proc.wait()` returns when the
        process is gone, not when the reader thread has taken its last pipe lines off the pipe: a
        line read straight after the exit (the force-exit notice, the last line before `os._exit`)
        can still be on its way into `lines` when the machine is busy. The pipe closes at the exit,
        so the reader finishes promptly."""
        self._reader.join(timeout)
        return "\n".join(self.lines)

    def health_port(self):
        line = self.wait_line("salud en http://")
        return int(line.rsplit(":", 1)[1].split("/", 1)[0])

    def stop(self, sig=signal.SIGTERM, timeout=None):
        # `timeout` defaults to the repo's OWN worker shutdown budget —
        # queue_infra.supervisor_sigkill_budget(), the SAME ceiling
        # asgi_main._terminar grants a supervised worker: the longest
        # interruptible phase, both sequential pool closes, and the final
        # supervisor margin. A prior
        # hard-coded 25s was SHORTER than POOL_OPEN_TIMEOUT_S (30s), so a
        # worker stuck opening its Postgres pool during a startup blip got
        # SIGKILLed by the harness before it could honor the graceful
        # request — exactly the death production supervision deliberately
        # does NOT inflict (round-9 review finding). Resolved at CALL time,
        # not import time, so a per-spawn TURNOS_WORKER_GRACEFUL_TIMEOUT
        # (the way the real suites vary the drain) is honored. Callers with
        # a tighter, test-specific ceiling (a force-kill headroom proof)
        # still pass `timeout=` explicitly.
        if timeout is None:
            from app.core import queue_infra as _queue_infra
            timeout = _queue_infra.supervisor_sigkill_budget()
        if self.proc.poll() is None:
            self.proc.send_signal(sig)
        deadline = time.monotonic() + timeout
        diagnostic = self.diagnostic
        dump_requested = False
        try:
            # Diagnostic children receive SIGUSR1 shortly before the original
            # ceiling, then get only the remaining time.  Normal WorkerProc
            # retains its one wait call and exact historical behaviour.
            if diagnostic:
                first_wait = max(0.0, timeout - min(0.25, timeout / 2))
                try:
                    self.proc.wait(first_wait)
                except subprocess.TimeoutExpired:
                    # Request the dump *inside* the original deadline, then
                    # retain only the remaining slice before the unchanged
                    # SIGKILL escalation.  Diagnostics cannot buy time.
                    self.request_diagnostic_dump()
                    dump_requested = True
                return self.proc.wait(max(0.0, deadline - time.monotonic()))
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired as exc:
            # Capture the child's last declared phase before cleanup. The old
            # bare re-raise destroyed the only evidence needed to distinguish
            # startup, worker drain, async-pool close, and atexit-pool close.
            pid = self.proc.pid
            if diagnostic and not dump_requested:
                self.request_diagnostic_dump()
                # Do not extend the supervisor contract for diagnostics: the
                # bounded await-chain wait below is the only one.
            if diagnostic:
                # The await-chain dump runs as a loop callback: give a live
                # loop a moment to write it before the abort below ends the
                # process (a blocked loop never writes it; the thread stacks
                # then show what blocks it).
                try:
                    _wait.until(lambda: "[worker-diagnostic] await-chain:end" in self.lines,
                                "await-chain dump", timeout=5)
                except AssertionError:
                    pass
            tail = self._failure_evidence()
            stacks = self._thread_dump()
            self._killpg()
            self.proc.wait(10)
            raise AssertionError(
                f"worker pid {pid} exceeded the {timeout:g}s shutdown budget; "
                f"stderr tail before SIGKILL:\n{tail}\n"
                f"stacks of every worker thread (faulthandler, SIGUSR2):\n{stacks}") from exc

    def cleanup(self):
        if self.proc.poll() is None:
            self._killpg()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                pass
        self.proc.stderr.close()
