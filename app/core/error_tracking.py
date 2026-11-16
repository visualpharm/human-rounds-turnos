"""error_tracking.py — Sentry-compatible error tracking, Tier 4 slice 7
(docs/roadmap/tier4-replatform-design.md, "`/health` + `/ready` + graceful
shutdown + optional Sentry").

**OFF by default**, same degrade-gracefully contract as every other
optional integration in this repo (Mailgun, OpenRouter, Turnstile): no DSN
configured (`TURNOS_SENTRY_DSN` unset/empty) = fully inert. `sentry_sdk` is
imported LAZILY, inside `init()`, never at module top level — this file is
imported unconditionally from `server.main()` (dark mode, every deploy
today), so a top-level `import sentry_sdk` would make it an unconditional
cost of every boot, DSN or not. Same "sibling-file discipline" the module-
contract adapter used to keep dark mode's import graph unchanged by an
optional feature (tier4-replatform-design.md, "Module contract adapter
(slice 3)", Finding 1: a `router_attr` module is a separate file precisely
so the plain route module never pays for an import it doesn't need).
`tests/test_error_tracking.py`'s "no import without a DSN" case asserts
`sentry_sdk` never lands in `sys.modules` when the env var is absent.

**Turning this ON for real needs an explicit PII-scrubbing review FIRST**
(design doc's risk register + "Decisions for the product owner"): an error's stack
trace or breadcrumb can trivially carry a DNI, a patient's name, or a
referral filename from local variables in scope at the point of failure —
the exact "nothing that identifies a person leaves for an external
provider unless it has to" invariant `pii.py` already enforces for AI
calls, applied here to an error tracker instead. This module ships the
WIRING (env-gated on/off), not the approval to configure a real DSN in
production — that stays a separate, explicit future decision.

**The scrubbing review (2026-10-19)** — asked for by the municipality's
platform team before they connect a Turnos-only Sentry project. What leaves,
by construction, is an ALLOWLIST rebuilt from each event, never the SDK's
event with fields removed:

- SDK side (`init`): `send_default_pii=False` (no IP, cookies, user, headers),
  `include_local_variables=False` (stack frames carry no variable values —
  the main way a DNI or a name would ride along), `max_request_body_size=
  "never"`, no tracing, no profiling.
- `scrub_event` (`before_send`): keeps the exception type, its message and
  each frame's file/function/line/source lines; the message goes through
  `pii.Scrubber`'s generic sweep (emails, DNI/CUIL/phone numbers), then any
  remaining 6+-digit number (a dotted DNI) is masked, and it loses anything
  longer than 300 characters. The request keeps only the method and the URL
  without query or fragment, numeric and token-like path segments masked;
  query string, body, cookies, headers and env never leave. `user`, `extra`, `contexts`
  other than runtime/os, `modules` and `server_name` are dropped. The launch signals of
  `core/launch_signals.py` are the only additions (TASK-4, 2026-10-30): the tag `failure_code` when its
  value is one of the closed codes, `contexts.launch_digest` as closed-key integers, and a Sentry Crons
  check-in rebuilt from slug + status + numbers + schedule.
- `scrub_breadcrumb` (`before_breadcrumb`): category, level and the scrubbed
  message only — no breadcrumb `data` (HTTP URLs with query strings, SQL
  parameters, log args).

Honest limit, the same one `pii.py` states: the sweep is automatic. A
patient's name interpolated into an exception message is not caught by a
regex; the code does not do that today, and frame values never travel.
"""

import re

from app.core import settings

_MAX_TEXT = 300
_KEEP_CONTEXTS = ("runtime", "os")
_FRAME_KEYS = ("filename", "abs_path", "function", "module", "lineno", "colno",
               "in_app", "context_line", "pre_context", "post_context")
# A path segment that is an id, a DNI or a capability token (confirm, offer,
# study links) — masked so the URL still says WHICH route failed. Catalog
# slugs (`medicina-general-valeria`) are lowercase words and stay readable.
_SEGMENT_ID_RE = re.compile(r"^\d+$")
_SEGMENT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,}$")
_SLUG_RE = re.compile(r"^[a-z]+(?:-[a-z]+)*$")


def _secret_segment(seg):
    return bool(_SEGMENT_ID_RE.match(seg)
                or (_SEGMENT_TOKEN_RE.match(seg) and not _SLUG_RE.match(seg)))


# After `pii`'s sweep, any number of 6+ digits still standing — a DNI written
# with dots («24.132.941», which `pii` leaves alone on purpose as it reads like
# an amount in clinical text), an affiliate or record number — is masked too:
# an error message loses nothing it needs by losing them.
_LEFTOVER_NUMBER_RE = re.compile(r"(?<![\w])\d(?:[\d.\s-]*\d){5,}(?![\w])")


def _scrub_text(texto):
    if not texto:
        return texto
    from app.core import pii
    out = pii.Scrubber().scrub(str(texto))
    out = _LEFTOVER_NUMBER_RE.sub("[número]", out)
    return out if len(out) <= _MAX_TEXT else out[:_MAX_TEXT] + "…"


def _scrub_path(url):
    path = str(url or "").split("?", 1)[0].split("#", 1)[0]
    if "://" in path:
        scheme, rest = path.split("://", 1)
        host, _, tail = rest.partition("/")
        prefix, path = scheme + "://" + host, "/" + tail
    else:
        prefix = ""
    segs = [(":id" if _secret_segment(s) else s) for s in path.split("/")]
    return prefix + "/".join(segs)


def _scrub_frames(stacktrace):
    frames = (stacktrace or {}).get("frames") or []
    return {"frames": [{k: f[k] for k in _FRAME_KEYS if k in f} for f in frames]}


_CHECK_IN_KEYS = ("type", "event_id", "timestamp", "sdk", "check_in_id", "monitor_slug", "status", "duration",
                  "environment", "release")
_CHECK_IN_STATUSES = ("in_progress", "ok", "error")
_SLUG_CHARS_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_MONITOR_NUMBERS = ("checkin_margin", "max_runtime", "failure_issue_threshold", "recovery_threshold")
_CRONTAB_RE = re.compile(r"^[0-9*/,-]{1,30}( [0-9*/,-]{1,30}){4}$")


def _scrub_check_in(event):
    """A Sentry Crons check-in (`core/launch_signals.monitored`) rebuilt from its allowlist: a slug of
    lowercase letters, digits and dashes, one of three statuses, numbers and the schedule. Anything else drops
    the whole check-in — it carries no text by design."""
    slug = event.get("monitor_slug")
    if not isinstance(slug, str) or not _SLUG_CHARS_RE.match(slug) or event.get("status") not in _CHECK_IN_STATUSES:
        return None
    out = {k: event[k] for k in _CHECK_IN_KEYS if k in event}
    config = event.get("monitor_config")
    if isinstance(config, dict):
        kept = {k: config[k] for k in _MONITOR_NUMBERS
                if isinstance(config.get(k), int) and not isinstance(config.get(k), bool)}
        if config.get("timezone") == "UTC":
            kept["timezone"] = "UTC"
        sched = config.get("schedule")
        sched = sched if isinstance(sched, dict) else {}
        if (sched.get("type") == "crontab" and isinstance(sched.get("value"), str)
                and _CRONTAB_RE.match(sched["value"])):
            kept["schedule"] = {"type": "crontab", "value": sched["value"]}
        elif (sched.get("type") == "interval" and sched.get("unit") == "minute"
              and isinstance(sched.get("value"), int) and not isinstance(sched.get("value"), bool)):
            kept["schedule"] = {"type": "interval", "value": sched["value"], "unit": "minute"}
        out["monitor_config"] = kept
    return out


def _signal_tags(tags):
    """The one tag a launch signal may carry: `failure_code`, only when it is a code of the closed list."""
    from app.core import launch_signals
    code = (tags or {}).get("failure_code") if isinstance(tags, dict) else None
    return {"failure_code": code} if code in launch_signals.FAILURE_CODES else {}


def _signal_digest(contexts):
    """`contexts.launch_digest` as `DIGEST_KEYS -> int`, nothing else (a bool or a string is not a number)."""
    from app.core import launch_signals
    digest = (contexts or {}).get("launch_digest") if isinstance(contexts, dict) else None
    if not isinstance(digest, dict):
        return {}
    return {k: v for k, v in digest.items()
            if k in launch_signals.DIGEST_KEYS and isinstance(v, int) and not isinstance(v, bool)}


def scrub_event(event, hint=None):
    """`before_send`: the allowlisted, scrubbed copy of a Sentry event."""
    if not isinstance(event, dict):
        return None
    if event.get("type") == "check_in":
        return _scrub_check_in(event)
    out = {k: event[k] for k in ("event_id", "timestamp", "level", "platform", "logger",
                                  "transaction", "release", "environment", "sdk",
                                  "fingerprint") if k in event}
    exc = event.get("exception") or {}
    if exc.get("values"):
        out["exception"] = {"values": [dict(
            {k: v[k] for k in ("type", "module", "mechanism") if k in v},
            value=_scrub_text(v.get("value")),
            stacktrace=_scrub_frames(v.get("stacktrace")),
        ) for v in exc["values"]]}
    if event.get("logentry") or event.get("message"):
        le = event.get("logentry") or {}
        out["logentry"] = {"message": _scrub_text(le.get("formatted") or le.get("message")
                                                  or event.get("message"))}
    if event.get("threads"):
        out["threads"] = {"values": [{"id": t.get("id"), "crashed": t.get("crashed"),
                                      "stacktrace": _scrub_frames(t.get("stacktrace"))}
                                     for t in (event["threads"].get("values") or [])]}
    req = event.get("request") or {}
    if req:
        out["request"] = {k: v for k, v in (("method", req.get("method")),
                                            ("url", _scrub_path(req.get("url")))) if v}
    ctx = event.get("contexts") or {}
    kept = {k: ctx[k] for k in _KEEP_CONTEXTS if k in ctx}
    digest = _signal_digest(ctx)
    if digest:
        kept["launch_digest"] = digest
    if kept:
        out["contexts"] = kept
    tags = _signal_tags(event.get("tags"))
    if tags:
        out["tags"] = tags
    crumbs = (event.get("breadcrumbs") or {}).get("values") or []
    if crumbs and event.get("logger") != "launch_signals":   # a synthetic signal event has no story to tell
        out["breadcrumbs"] = {"values": [c for c in (scrub_breadcrumb(c) for c in crumbs) if c]}
    return out


def scrub_breadcrumb(crumb, hint=None):
    """`before_breadcrumb`: category, level, time and the scrubbed message."""
    if not isinstance(crumb, dict):
        return None
    out = {k: crumb[k] for k in ("type", "category", "level", "timestamp") if k in crumb}
    if crumb.get("message"):
        out["message"] = _scrub_text(crumb["message"])
    return out


# ---- browser errors of the staff screens -----------------------------------
#
# `templates/admin/base.html` reports `window.onerror` and unhandled promise
# rejections of the panel to `POST /api/admin/client-errors`
# (`admin_panel/client_errors_router.py`). What the browser sends is never
# trusted as clean: only five fields are read, each is scrubbed like a server
# event, every URL loses its query and fragment, and the event goes through the
# same `before_send` allowlist as a server error. The patient site does not
# report — only staff screens, where the session is known.

_BROWSER_MAX_FRAMES = 20
_URL_RE = re.compile(r"(?:https?://[^\s)'\"]+|/[A-Za-z0-9_./%:-]*[?#][^\s)'\"]*)")
# «at fn (https://host/static/x.js:12:5)» (Chromium) / «fn@https://…:12:5» (Gecko, WebKit)
_FRAME_RE = re.compile(r"^\s*(?:at\s+)?(?:(?P<fn>[^\s(@]+)\s*[@(]\s*)?\(?(?P<file>[^\s()@]+?):(?P<line>\d+)(?::(?P<col>\d+))?\)?\s*$")


def _scrub_url_text(texto):
    """Every URL inside a free text loses its query and fragment and its
    id/token path segments, before the generic sweep."""
    return _URL_RE.sub(lambda m: _scrub_path(m.group(0)), str(texto or ""))


def _browser_text(value):
    return _scrub_text(_scrub_url_text(value)) if value else ""


def _browser_frames(stack):
    frames = []
    for raw in str(stack or "").splitlines()[:_BROWSER_MAX_FRAMES]:
        m = _FRAME_RE.match(raw)
        if not m:
            continue
        frame = {"filename": _scrub_path(m.group("file")), "lineno": int(m.group("line")),
                 "function": (m.group("fn") or "?")[:80], "in_app": True}
        if m.group("col"):
            frame["colno"] = int(m.group("col"))
        frames.append(frame)
    frames.reverse()  # Sentry wants the innermost frame last
    return {"frames": frames}


def browser_event(report):
    """The Sentry event for one browser error report, already scrubbed."""
    report = report if isinstance(report, dict) else {}
    kind = "unhandledrejection" if report.get("kind") == "unhandledrejection" else "error"
    message = _browser_text(report.get("message")) or "(sin mensaje)"
    screen = _scrub_path(report.get("screen")) or "/admin"
    frames = _browser_frames(report.get("stack"))
    if not frames["frames"] and report.get("source"):
        frame = {"filename": _scrub_path(report.get("source")), "function": "?", "in_app": True}
        for key, field in (("line", "lineno"), ("col", "colno")):
            try:
                frame[field] = int(report.get(key))
            except (TypeError, ValueError):
                pass
        frames = {"frames": [frame]}
    return {
        "level": "error",
        "platform": "javascript",
        "logger": "browser",
        "transaction": screen,
        "exception": {"values": [{"type": "BrowserError", "value": message,
                                   "mechanism": {"type": kind, "handled": False},
                                   "stacktrace": frames}]},
        "request": {"method": "GET", "url": screen},
    }


def capture_browser_error(report):
    """Logs one scrubbed line and, when Sentry is on, sends the scrubbed
    event through `before_send`. Never raises; returns True if Sentry got it."""
    event = browser_event(report)
    value = event["exception"]["values"][0]
    top = (value["stacktrace"]["frames"] or [{}])[-1]
    print(f"[client-error] {event['transaction']}: {value['value']}"
          f" ({top.get('filename', '?')}:{top.get('lineno', '?')})", flush=True)
    if not enabled():
        return False
    try:
        import sentry_sdk  # lazy — see module docstring
        return bool(sentry_sdk.capture_event(event))
    except Exception:  # noqa: BLE001 — error tracking never breaks the request
        return False


def enabled():
    """True if a DSN is configured — informational only (`init()` is what
    actually decides whether to import/initialize)."""
    return bool(settings.ProcessSettings.current().sentry_dsn)


def init(signals_only=False):
    """No-op (returns False, imports nothing) unless `TURNOS_SENTRY_DSN` is
    set. `signals_only=True` (the queue worker, `core/launch_signals.init_sentry`): the client sends only what
    code hands it explicitly — the launch signals and Crons check-ins — and reports no error of its own: no
    default or auto-enabled integration (so no excepthook, logging, SQL or subprocess breadcrumbs). Safe to call more than once in the same process (e.g. dark mode's
    `server.main()`, or a future proxy-mode call from `app.py`'s own
    startup) — `sentry_sdk.init()` itself is idempotent per-process, it
    just reconfigures the global hub. Never raises: a broken DSN or a
    missing `sentry_sdk` install degrades the same way every other
    optional integration here does (caller wraps this in try/except,
    matching every other `server.main()` daemon start)."""
    process = settings.ProcessSettings.current()
    dsn = process.sentry_dsn
    if not dsn:
        return False

    import sentry_sdk  # lazy — see module docstring
    from app.core import version

    release = version.commit()
    sentry_sdk.init(
        dsn=dsn,
        environment=process.sentry_env,
        # Which build failed — the same commit `/health` answers.
        release=None if release == version.UNKNOWN else release,
        # The scrubbing review (module docstring): nothing personal by
        # default, no frame variables, no request bodies, and every event and
        # breadcrumb rebuilt from an allowlist before it leaves.
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        max_breadcrumbs=30,
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        # Error tracking only: a trace can carry the same PII risk as an
        # error breadcrumb, and nothing here asked for it.
        traces_sample_rate=0.0,
        profiles_sample_rate=0.0,
        **({"default_integrations": False, "auto_enabling_integrations": False} if signals_only else {}),
    )
    return True
