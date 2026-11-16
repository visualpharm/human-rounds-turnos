"""_livehttp.py — shared "boot the real native app on an ephemeral port"
boilerplate (Tier 3, Area 2's foundation, `tier3-harness-design.md`).

Historically this served `server.Handler` (the legacy stdlib transport) over
`ThreadingHTTPServer`. The native-migration campaign retired that topology
(docs/roadmap/native-migration-campaign-20260826.md): the one executable
transport is the composed FastAPI app (`app.app`), so `LiveServer` now runs
THAT over a real 127.0.0.1 socket via uvicorn in a background thread — same
`with LiveServer() as base_url:` contract, same ephemeral port, same
class-scoped `LiveServerMixin` shape on top.

Lifespan is deliberately OFF: the legacy `LiveServer` booted the transport
only — no reminder/waitlist/HSI daemons — and the routes tier's fixtures
were authored under that assumption. Background services stay owned by the
real process entrypoint (`asgi_main.py`, exercised by the e2e suite), not
by unit/routes-tier boots. `TURNOS_ASGI=1` is set for the server's lifetime
(and restored after): serving the ASGI composition IS asgi mode — booting
it with the flag unset would exercise a configuration no deployment runs.

Waiting for an answer is a CEILING, not a speed check (2026-11-06). A test
asserts what the server answers; how long it takes is not part of any
assertion here. A fixed 5 s socket timeout turned a loaded machine (load
100+, several builds at once) into red tests that had nothing wrong with
them, so every request of this tier waits up to `response_ceiling()` —
120 s by default, `TURNOS_TEST_HTTP_CEILING` overrides it — and only a
server that does not answer at all reaches it. Reaching it raises
`NoAnswer` (an AssertionError, so unittest reports a FAIL, not an ERROR)
naming the request, the time waited and the machine's load, instead of a
bare `TimeoutError: timed out`. Requests go through `call()` (urllib, follows
redirects) or `LiveServerMixin._get()` (http.client, does not); both share
the ceiling and the message. Pinned by `tests/test_livehttp_ceiling.py`.

Not named test_*.py on purpose, same reason as `_dbtest.py`: unittest's
discovery (`-p "test_*.py"`) must not try to collect this as a test module.
"""

import contextlib
import http.client
import os
import threading
import time
import urllib.error
import urllib.request

import _env
import _wait

CEILING_ENV = "TURNOS_TEST_HTTP_CEILING"
DEFAULT_CEILING_S = 120.0


def response_ceiling():
    """Seconds a test waits for the server before calling it a hang:
    `TURNOS_TEST_HTTP_CEILING` when it is a positive number, else 120."""
    raw = os.environ.get(CEILING_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_CEILING_S
    except ValueError:
        value = DEFAULT_CEILING_S
    return value if value > 0 else DEFAULT_CEILING_S


class NoAnswer(AssertionError):
    """The server did not answer a request within the response ceiling."""


def _load_1m():
    try:
        return "%.1f" % os.getloadavg()[0]
    except OSError:
        return "unknown"


@contextlib.contextmanager
def no_answer_is_a_failure(method, url, ceiling):
    """Turn a socket timeout inside the block into `NoAnswer` naming the
    request, the time waited and the load, so the report says which request
    hung instead of `TimeoutError: timed out`."""
    started = time.monotonic()
    try:
        yield
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, urllib.error.URLError) and not isinstance(exc.reason, TimeoutError):
            raise
        raise NoAnswer(
            "no answer to %s %s within the %g s ceiling (waited %.1f s, load average %s); "
            "the ceiling already covers a loaded machine, so this is a server that hangs. "
            "%s raises the ceiling." % (method, url, ceiling, time.monotonic() - started,
                                        _load_1m(), CEILING_ENV)) from exc


def call(base_url, method, path, data=None, headers=None, ceiling=None):
    """One real HTTP request: `(status, headers, body)`. A non-2xx status is
    the answer (urllib's HTTPError is read and closed), a server silent for
    longer than the ceiling is `NoAnswer`."""
    ceiling = response_ceiling() if ceiling is None else ceiling
    req = urllib.request.Request(base_url + path, data=data, headers=headers or {}, method=method)
    with no_answer_is_a_failure(method, path, ceiling):
        try:
            with urllib.request.urlopen(req, timeout=ceiling) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            # HTTPError IS the response; unlike a `with`-managed response it is
            # not closed for us, and an unclosed one trips the ResourceWarning gate.
            with e:
                return e.code, e.headers, e.read()


class LiveServer:
    """The composed native app on a real ephemeral 127.0.0.1 port, for the
    lifetime of a `with` block.

        with LiveServer() as base_url:
            urllib.request.urlopen(base_url + "/doctors")

    The app import is lazy (inside `__enter__`) so importing this module
    never boots the composition — a caller that only wants the mixin
    shouldn't be forced to import the whole app just to import this file.
    """

    def __init__(self, lifespan="off"):
        self._lifespan = lifespan
        self._server = None
        self.thread = None
        self.port = None
        self.base_url = None
        self._env = contextlib.ExitStack()

    def __enter__(self):
        self._env.enter_context(_env.patched_env(TURNOS_ASGI="1"))
        import uvicorn
        from app import main as _app

        config = uvicorn.Config(
            _app.app, host="127.0.0.1", port=0,
            lifespan=self._lifespan, log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._server.run, daemon=True)
        self.thread.start()
        try:
            _wait.until(lambda: self._server.started or not self.thread.is_alive(),
                        "LiveServer: uvicorn reaches started",
                        timeout=max(30.0, response_ceiling()))
        except AssertionError as exc:
            raise RuntimeError("LiveServer: uvicorn never reached started") from exc
        if not self._server.started:
            raise RuntimeError("LiveServer: uvicorn thread died during boot")
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        return self.base_url

    def __exit__(self, *exc_info):
        self._server.should_exit = True
        self.thread.join(timeout=10)
        self._env.close()
        return False


class LiveServerMixin:
    """Class-scoped `LiveServer`: one real server for the whole TestCase
    class, up in `setUpClass`, down in `tearDownClass` — including the
    `_get()` helper the historical `test_http_server.py` call sites use.
    """

    @classmethod
    def setUpClass(cls):
        cls._live = LiveServer()
        cls._base_url = cls._live.__enter__()
        cls._port = cls._live.port

    @classmethod
    def tearDownClass(cls):
        cls._live.__exit__(None, None, None)

    def _get(self, path, headers=None):
        ceiling = response_ceiling()
        conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=ceiling)
        try:
            with no_answer_is_a_failure("GET", path, ceiling):
                conn.request("GET", path, headers=headers or {})
                r = conn.getresponse()
                body = r.read()
            return r.status, (r.getheader("Content-Type") or ""), body
        finally:
            conn.close()
