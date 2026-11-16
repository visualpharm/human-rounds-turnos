"""abuse_guard.py — shared anti-abuse for sessionless AI endpoints.

`POST /api/preparacion` (preparation.py) and `POST /api/analyze-order`
(booking_requests.py) run BEFORE registration, with no session cookie — they're
the natural target for a script hammering them in a loop and burning the
OpenRouter quota. One rate-limit bucket and one Turnstile for both, same
pattern (envs, degradation) as admin_auth.py/accounts.py.

  allowed(ip) -> bool           in-memory sliding windows, best-effort
                                 (like admin_auth._throttled): 10/hour AND
                                 30/day per IP (envs TURNOS_AI_PER_HOUR/_PER_DAY).
  identity(ip, headers) -> str  the key the per-person limits count under:
                                 `ip|browser-id` (header X-Turnos-Client, an id
                                 the page keeps in localStorage) so many people
                                 behind one IP (a hospital gateway) do not share
                                 a bucket; no header = the bare IP, the strict
                                 bucket.
  ceiling_ok(ip) -> bool        the cap on the WHOLE IP (every browser together,
                                 TURNOS_AI_IP_CEILING_PER_HOUR 200 / _PER_DAY
                                 600): stops a script from rotating ids.
                                 0 (or less) = no ceiling.
  turnstile_ok(headers, peer, tok)  delegates to turnstile.py.
  client_ip(headers, peer)          resolves the client IP from explicit values.

Import-safe: stdlib only. A process restart clears the windows — it's
anti-abuse, not a hard security limit.
"""

import re

from app.auth import turnstile
from app.core import config
from app.core import proxy_trust
from app.core import rate_window

DEFAULT_PER_HOUR = 10
DEFAULT_PER_DAY = 30

_HOUR = 3600
_DAY = 86400


_int_env = config.get_int


def _per_hour():
    return _int_env("TURNOS_AI_PER_HOUR", DEFAULT_PER_HOUR)


def _per_day():
    return _int_env("TURNOS_AI_PER_DAY", DEFAULT_PER_DAY)


# ---- in-memory rate limit (sliding window per IP) ---------------------------

_HITS = rate_window.Hits(_DAY)  # ip -> [epoch, ...]  (only attempts that consumed quota)
_sweep = _HITS.sweep  # an IP that exhausts its quota and never comes back (or a scraper rotating fake
#                       IPs via X-Forwarded-For) must not stay in _HITS for the life of the process


def allowed(ip):
    """True if `ip` still has room (10/hour AND 30/day, by default); if it
    does, records the current attempt. Call once per real request (not for a
    request already rejected in an earlier step)."""
    return _HITS.allow(ip, _per_day(), also=(_HOUR, _per_hour()))


# ---- one browser, not one IP ------------------------------------------------
#
# Behind the municipal gateway (and behind Traefik on the test sites) every
# patient reaches the app from the same socket peer, so "per IP" meant "per
# hospital": ten reads in an hour by anyone locked everybody out (the reference-deployment counterpart,
# 2026-10-28, «Hiciste varias consultas seguidas»). The browser sends a random
# id it keeps in localStorage (header `X-Turnos-Client`); the strict limits
# count per `ip|id`, and a generous ceiling per IP is what stops a script that
# rotates ids. No id (an old tab, a script) keeps the strict per-IP bucket.

CLIENT_HEADER = "X-Turnos-Client"
DEFAULT_IP_CEILING_PER_HOUR = 200
DEFAULT_IP_CEILING_PER_DAY = 600

_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_IP_HITS = rate_window.Hits(_DAY)  # ip -> [epoch, ...]  (the ceiling over all browsers of one IP)


def _ceiling_per_hour():
    return _int_env("TURNOS_AI_IP_CEILING_PER_HOUR", DEFAULT_IP_CEILING_PER_HOUR)


def _ceiling_per_day():
    return _int_env("TURNOS_AI_IP_CEILING_PER_DAY", DEFAULT_IP_CEILING_PER_DAY)


def identity(ip, headers):
    """The key the per-person limits count under: `ip|browser-id` when the
    request carries a well-formed id, else the bare `ip` (strict bucket)."""
    raw = proxy_trust.header_value(headers, CLIENT_HEADER).strip()
    if _CLIENT_ID_RE.match(raw):
        return f"{ip or '?'}|{raw}"
    return ip


def ceiling_ok(ip):
    """True while the whole IP (every browser behind it together) is under the
    ceiling; records the attempt. Meant for the requests the per-browser
    limits do not see, and as the cap on id rotation. A ceiling of 0 (or less)
    is OFF, as for the installation's other limits: 0 on both records nothing
    and always answers True."""
    per_day, per_hour = _ceiling_per_day(), _ceiling_per_hour()
    if per_day <= 0 and per_hour <= 0:
        return True
    unbounded = float("inf")
    return _IP_HITS.allow(ip, per_day if per_day > 0 else unbounded,
                          also=(_HOUR, per_hour if per_hour > 0 else unbounded))


# ---- Cloudflare Turnstile (shared gate, owner: turnstile.py) ---------------

client_ip = turnstile.client_ip_from


def turnstile_ok(headers, client_host, token):
    """SOFT gate: these routes are patient-facing (uploading the referral to
    get an appointment at a public hospital). If the widget didn't resolve,
    the patient still gets through — what actually stops abuse here is the
    per-IP rate limit above, not the captcha. A hard gate left a person with
    a valid referral stuck on "We couldn't verify you're a person" with no
    way out (prod, 2026-07-20)."""
    return turnstile.verificar_from(headers, client_host, token, blando=True)
