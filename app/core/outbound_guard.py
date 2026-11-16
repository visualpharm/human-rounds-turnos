"""outbound_guard.py — hard, fail-closed outbound kill switch
(TURNOS_OUTBOUND_KILL=1; Tier 4 "safe activation proof for business queues"
card, 2026-08-08).

Purpose: let the REAL waitlist/reminders handlers run on the worker
topology — against fictional or disposable data — with a guarantee that no
email, WhatsApp, webhook or any other network side effect can leave the
process, no matter which code path tries. The per-transport soft switches
(MAIL_ENABLED=0, an unconfigured WA provider) are advisory and fail OPEN: a
new code path, a configured credential, or a bug walks right past them.
This guard is one boundary at the lowest layer every Python HTTP client
shares — `socket.socket.connect` — and it fails CLOSED: default-deny
allowlist, and a worker that requests the switch but cannot arm it refuses
to run at all (worker.py exits EXIT_CONFIG).

What is allowed through when armed:
- AF_UNIX sockets (a filesystem path never reaches the network).
- The DATABASE_URL endpoint (hostname and its resolved IPs, at the URL's
  port), resolved once at arm time. In practice psycopg3 connects through
  libpq in C — BELOW Python's socket layer — so DB traffic never consults
  this guard at all; the allowance is belt-and-suspenders for any future
  pure-Python DB path, not the mechanism that keeps the DB reachable.
- Loopback destinations ONLY on a DB port. Never loopback-wide: this
  repo's own WAHA instances listen on loopback ports in real deployments
  (localhost:3000/3002/3010 on the home server), so "loopback is safe"
  would wave a real WhatsApp send straight through — the adversarial
  review (2026-08-08) caught exactly that.

Everything else raises `OutboundBlocked` (a ConnectionError, so every
best-effort transport in this repo — mailgun.send's bare except, otp_send,
notify_* — treats it exactly like a network failure: logs and moves on;
the business transition it was decorating has ALREADY persisted, per the
claim-before-send / persist-before-notify contracts those modules state).

Scope and boundary, stated honestly: the patch covers `socket.socket`'s
`connect`, `connect_ex`, `sendto` and `sendmsg` — which is every stdlib/urllib/
http.client/requests/websocket path, and asyncio's `loop.sock_connect`
(the selector event loop calls the instance's own `connect`). It does NOT
cover a C extension doing its own networking (libpq is the one such thing
in this process, deliberately) or a subprocess; the worker spawns neither.
DNS resolution (`getaddrinfo`, libc-level) is likewise outside the patch —
a blocked send may still have LOOKED UP the destination's name before the
connect was refused. A DNS query is not a message: no mail, WhatsApp,
webhook or request body can leave through it, so the card's "no outbound
side effect" bar holds; stated here so nobody reads "blocked" as "zero
packets of any kind".

The flag is read from the ENVIRONMENT ONLY (never core.config/DB-stored
settings): the guard must arm before the first DB touch, and a kill switch
whose state lives in the database it is supposed to protect would be
circular. Same `.strip()` normalization as queue_infra.worker_mode() (the
"1 " Coolify paste lesson).
"""
import os
import socket
import sys
import threading
from app.core import log_pii
from app.core import settings

FLAG = "TURNOS_OUTBOUND_KILL"

_lock = threading.Lock()
_armed = False
_orig = {}
_allowed = set()      # {(host_or_ip_lowercase, port)} from DATABASE_URL
_db_ports = set()     # loopback is allowed ONLY on these ports
_blocked = 0


class OutboundBlocked(ConnectionError):
    """Raised instead of connecting anywhere outside the allowlist."""


def _log(msg):
    print(f"[outbound_guard] {msg}", file=sys.stderr, flush=True)


def requested():
    """THE one parse of TURNOS_OUTBOUND_KILL (env only — see module
    docstring for why config.get is wrong here; `core.settings.ProcessSettings`)."""
    return settings.ProcessSettings.current().outbound_kill


def armed():
    return _armed


def blocked_count():
    """How many connects this guard refused since arming — evidence
    counter for the proof harness and the live demo run."""
    return _blocked


def _db_endpoints():
    """The DATABASE_URL endpoint as {(host, port)} pairs: the hostname
    itself plus every IP it resolves to right now. Resolution failure is
    NOT fatal (fail closed = allow less, never more): the guard arms with
    whatever resolved, and a DB that later becomes unreachable makes the
    worker exit transient exactly as it would without the guard."""
    url = ""
    try:
        from app.core import db
        url = db._db_url() or ""
    except Exception:  # noqa: BLE001 — env fallback below
        url = os.environ.get("DATABASE_URL") or ""
    if not url:
        return set()
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
        host, port = u.hostname, u.port or 5432
    except ValueError:
        return set()
    if not host:
        return set()
    out = {(host.lower(), int(port))}
    try:
        for _f, _t, _p, _cn, sa in socket.getaddrinfo(host, port,
                                                       proto=socket.IPPROTO_TCP):
            out.add((str(sa[0]).lower(), int(port)))
    except OSError as e:
        _log(f"no pude resolver el host de DATABASE_URL ({host!r}): {log_pii.exc(e)} — "
             "sigo armado igual (menos permitido, nunca más)")
    return out


def _is_loopback(host):
    h = host.lower().strip("[]")
    return (h == "localhost" or h == "::1" or h.startswith("127.")
            or h.startswith("::ffff:127."))


def _check(address):
    """Default-deny: anything not provably local or the DB endpoint is
    refused BEFORE any packet leaves."""
    global _blocked
    if isinstance(address, (str, bytes, os.PathLike)):
        return  # AF_UNIX — filesystem, not network
    if isinstance(address, tuple) and len(address) >= 2:
        host = str(address[0] or "")
        try:
            port = int(address[1])
        except (TypeError, ValueError):
            port = -1
        if (host.lower(), port) in _allowed:
            return
        # Loopback SÓLO en un puerto de DB — nunca loopback entero: WAHA
        # corre en puertos loopback en despliegues reales de este repo.
        if host and _is_loopback(host) and port in _db_ports:
            return
    with _lock:
        _blocked += 1
    _log(f"BLOQUEADO: connect a {address!r} (TURNOS_OUTBOUND_KILL=1)")
    raise OutboundBlocked(
        f"salida de red bloqueada por TURNOS_OUTBOUND_KILL=1: {address!r}")


def arm():
    """Installs the guard (idempotent). Raises if it cannot — the caller's
    contract (worker.py) is to REFUSE TO RUN in that case: a requested kill
    switch that silently failed to arm is the fail-open bug this module
    exists to prevent."""
    global _armed, _allowed, _db_ports
    with _lock:
        if _armed:
            return
        _allowed = _db_endpoints()
        _db_ports = {p for _h, p in _allowed}
        orig_connect = socket.socket.connect
        orig_connect_ex = socket.socket.connect_ex
        orig_sendto = socket.socket.sendto
        orig_sendmsg = socket.socket.sendmsg

        def guarded_connect(self, address):
            _check(address)
            return orig_connect(self, address)

        def guarded_connect_ex(self, address):
            # connect_ex normally reports errors as errno instead of
            # raising; under the kill switch a refused destination raises
            # loudly on purpose — silence is what fail-open looks like.
            _check(address)
            return orig_connect_ex(self, address)

        def guarded_sendto(self, data, *args):
            if args:  # sendto(data, address) | sendto(data, flags, address)
                _check(args[-1])
            return orig_sendto(self, data, *args)

        def guarded_sendmsg(self, buffers, *args):
            # sendmsg(buffers[, ancdata[, flags[, address]]]) — la revisión
            # adversarial (2026-08-08) probó en vivo que sin esto un UDP
            # sendmsg con destino salía sin bloquear.
            if len(args) >= 3 and args[2]:
                _check(args[2])
            return orig_sendmsg(self, buffers, *args)

        _orig.update(connect=orig_connect, connect_ex=orig_connect_ex,
                     sendto=orig_sendto, sendmsg=orig_sendmsg)
        socket.socket.connect = guarded_connect
        socket.socket.connect_ex = guarded_connect_ex
        socket.socket.sendto = guarded_sendto
        socket.socket.sendmsg = guarded_sendmsg
        _armed = True
    _log("armado: default-deny; permitido sólo AF_UNIX, el endpoint de "
         f"DATABASE_URL ({len(_allowed)} entrada(s)) y loopback únicamente "
         f"en sus puertos {sorted(_db_ports)}")


def disarm():
    """Test seam only — production never disarms (the process dies armed)."""
    global _armed, _blocked, _allowed, _db_ports
    with _lock:
        if not _armed:
            return
        socket.socket.connect = _orig["connect"]
        socket.socket.connect_ex = _orig["connect_ex"]
        socket.socket.sendto = _orig["sendto"]
        socket.socket.sendmsg = _orig["sendmsg"]
        _orig.clear()
        _armed = False
        _blocked = 0
        _allowed = set()
        _db_ports = set()
