"""test_graceful_shutdown.py — Tier 4 slice 7 (docs/roadmap/
tier4-replatform-design.md, "`/health` + `/ready` + graceful shutdown +
optional Sentry"). Design gate, verbatim: "a live SIGTERM during a request
in flight completes that request before exiting (measured, not assumed)".

Spawns real legacy and ASGI subprocesses on ephemeral ports. The legacy
proof holds health.py's test-only `/__test/slow` response open. The ASGI
proof deliberately drains a large native static response, without exposing
that legacy-only test hook through the terminal compatibility boundary.
Both send a genuine `os.kill(pid, SIGTERM)` mid-flight and assert three
things with a stopwatch, not an assumption:

  1. The in-flight request still gets a real 200 response, not a severed
     connection -- proving server.main()'s new SIGTERM handler drains
     before exiting instead of hitting the OS default (immediate kill,
     today's behavior for every deploy before this slice).
  2. The whole shutdown is BOUNDED -- the process exits within a few
     seconds of SIGTERM, not hanging indefinitely on a slow drain.
  3. `app_modules.shutdown_all()` actually ran (grepped from the
     subprocess's own stdout) -- the "stops daemons ... through the Tier 2
     lifecycle shutdown_all()" half of this slice's brief.

A second test proves the COMMON case (zero in-flight requests, what every
real e2e/routes-tier teardown actually looks like) exits fast, so the new
drain logic never adds latency when there's nothing to wait for.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_graceful_shutdown.py"
"""
import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import tempfile
import unittest
import urllib.error
import urllib.request

import _wait

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SHUTDOWN_BOUND_SECONDS = 15  # SIGTERM -> exit must finish well inside Docker's own stop grace period


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(base_url, path, timeout=10):
    req = urllib.request.Request(base_url + path)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        with e:
            return e.code, json.loads(e.read().decode("utf-8"))


def _spawn_server(port, script="asgi_main.py", extra_env=None):
    # asgi_main.py is the one process entrypoint (server.py retired with the
    # legacy topology, native-migration final wave) and refuses to boot
    # without TURNOS_ASGI=1.
    env = dict(os.environ)
    env.update({
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "TURNOS_ASGI": "1",
        "DATABASE_URL": "",
        "TURNOS_FAKE_AI": "1",
        "TURNOS_SKIP_HSI": "1",
        "TURNOS_HSI_SYNC_OFF": "1",
        "TURNOS_REMINDERS_OFF": "1",
        "TURNOS_WAITLIST_OFF": "1",
        "TURNOS_BACKUP_OFF": "1",
        "TURNOS_AI_HEALTH_OFF": "1",
    })
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, script],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_ready(base_url, deadline_s=15):
    last_err = None

    def _ready():
        nonlocal last_err
        try:
            status, _ = _get(base_url, "/health", timeout=2)
            return status == 200
        except Exception as e:  # noqa: BLE001
            last_err = e
            return False

    try:
        _wait.until(_ready, "the server answers /health", timeout=deadline_s, interval=0.1)
    except AssertionError:
        raise AssertionError(f"server never became ready: {last_err}") from None


# TestGracefulShutdownDrainsInFlightRequest retired with its subject: it
# drove the LEGACY server's `/__test/slow` endpoint (core/health.py's
# TURNOS_TEST_SLOW_ENDPOINT branch, never natively routed) to prove the
# legacy drain. The native drain proof — a response demonstrably in flight
# when SIGTERM lands completing with exact bytes, plus
# `app_modules.shutdown_all()` running on the SIGTERM path — lives in
# TestGracefulShutdownNativeMode below.


class TestGracefulShutdownFastWhenIdle(unittest.TestCase):
    """No in-flight request -- the case every real e2e/routes-tier teardown
    actually hits. Must stay fast: this is exactly what the harness's own
    3s SIGTERM-then-SIGKILL fallback (tests/e2e/lib/harness.mjs) depends on."""

    def test_sigterm_with_nothing_in_flight_exits_fast(self):
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        proc = _spawn_server(port)
        try:
            _wait_ready(base_url)
            t0 = time.monotonic()
            os.kill(proc.pid, signal.SIGTERM)
            returncode = proc.wait(timeout=10)
            elapsed = time.monotonic() - t0
            # uvicorn drains, runs the lifespan shutdown, then RE-RAISES the
            # signal so orchestrators see the true termination cause — the
            # graceful proof is the completed-shutdown log line, not exit 0
            # (same accepted pair as TestGracefulShutdownNativeMode).
            self.assertIn(returncode, (0, -signal.SIGTERM))
            self.assertLess(elapsed, 2.0, "idle shutdown should not wait out the drain window")
            out = proc.stdout.read()
            self.assertIn("Ciclo de vida de app modules: apagado.", out,
                           "app_modules.shutdown_all() never ran on the idle SIGTERM path")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            if proc.stdout is not None:
                proc.stdout.close()


class TestGracefulShutdownNativeMode(unittest.TestCase):
    """The ASGI terminal drains a native response before lifecycle exit.

    The large response cannot fit in the socket buffer while the reader is
    paused, so it is demonstrably in flight when SIGTERM arrives.
    """

    def setUp(self):
        self.port = _free_port()
        self.retired_internal_port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.proc = _spawn_server(
            self.port, script="asgi_main.py",
            # A retired setting must not create a second listener.
            extra_env={
                "TURNOS_ASGI": "1",
                "TURNOS_ASGI_LEGACY_PORT": str(self.retired_internal_port),
            },
        )
        _wait_ready(self.base_url, deadline_s=20)
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", self.retired_internal_port))
        finally:
            probe.close()

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=10)
        if self.proc.stdout is not None:
            self.proc.stdout.close()

    def test_sigterm_during_native_stream_completes_exact_bytes(self):
        block = b"native-static-drain-proof\n" * 40960
        expected = hashlib.sha256()
        # under static/: the fallback serves only its public list, not any file of the checkout
        tmp = tempfile.NamedTemporaryFile(
            prefix="native-drain-", suffix=".js", dir=os.path.join(ROOT, "static"), delete=False
        )
        try:
            for _ in range(32):
                tmp.write(block)
                expected.update(block)
            tmp.close()
            expected_size = len(block) * 32
            request_path = "/static/" + os.path.basename(tmp.name)
            result = {}
            response_started = threading.Event()
            sigterm_sent = threading.Event()

            def _hold_open():
                t0 = time.monotonic()
                digest = hashlib.sha256()
                received = 0
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
                try:
                    conn.request("GET", request_path)
                    resp = conn.getresponse()
                    result["status"] = resp.status
                    first = resp.read(1024)
                    digest.update(first)
                    received += len(first)
                    response_started.set()
                    # Hold the reader paused until SIGTERM has really been sent.
                    sigterm_sent.wait(timeout=10)
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        received += len(chunk)
                        time.sleep(0.002)
                    result["size"] = received
                    result["digest"] = digest.hexdigest()
                except Exception as e:  # noqa: BLE001
                    result["error"] = repr(e)
                    response_started.set()
                finally:
                    conn.close()
                    result["elapsed"] = time.monotonic() - t0

            holder = threading.Thread(target=_hold_open)
            holder.start()
            self.assertTrue(response_started.wait(timeout=10),
                            "native response never started")

            t_signal = time.monotonic()
            os.kill(self.proc.pid, signal.SIGTERM)
            sigterm_sent.set()

            holder.join(timeout=20)
            self.assertFalse(holder.is_alive(), "the in-flight native response never returned")
            self.assertNotIn("error", result,
                             f"in-flight native response failed: {result.get('error')}")
            self.assertEqual(result["status"], 200)
            self.assertEqual(result["size"], expected_size)
            self.assertEqual(result["digest"], expected.hexdigest())

            returncode = self.proc.wait(timeout=15)
            total = time.monotonic() - t_signal
            out = self.proc.stdout.read()
            self.assertIn(returncode, (0, -signal.SIGTERM),
                          f"unexpected exit status on SIGTERM:\n{out}")
            self.assertIn("Ciclo de vida de app modules: apagado.", out,
                          "app_modules.shutdown_all() never ran on the SIGTERM path")
            self.assertIn("Finished server process", out,
                          "uvicorn's own graceful shutdown never completed")
            self.assertLess(total, _SHUTDOWN_BOUND_SECONDS,
                            "native shutdown took longer than the bounded drain window allows")
        finally:
            try:
                tmp.close()
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
