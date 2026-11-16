"""connectors/mailjet/mailjet.py — transactional email through Mailjet's
Send API v3.1 (salud.pinamar.gob.ar, 2026-10-20: the municipality sends from
a dedicated sub-account of its Premium account, not through a second
provider).

Same best-effort contract as notifications/mailgun.py: missing config → a
logged skip, any HTTP/transport/provider error → False, never an exception.
The shared MAIL_ENABLED=0 switch is checked here, like notifications/mailgun.py
does for Mailgun.

Config (read through config.py at call time — env, .env or Settings):
  MAILJET_API_KEY     API key of the (sub-)account — Basic auth user
  MAILJET_API_SECRET  its secret key — Basic auth password
  MAIL_FROM           "Name <address>"; the address, or its whole domain,
                      must be a validated sender in that Mailjet account
  MAIL_REPLY_TO       optional Reply-To address

Open and click tracking are disabled on every message: in health email a
rewritten link reads like phishing (same reason the Mailgun domain runs
without tracking).
"""

import base64
import json
import sys
import urllib.error
import urllib.request

from app.notifications import mail
from app.core import log_pii

API_BASE = "https://api.mailjet.com"
_TIMEOUT = 8


def _log(msg):
    print(f"[mailjet] {msg}", file=sys.stderr, flush=True)


def _auth_header():
    key = mail.setting("MAILJET_API_KEY")
    secret = mail.setting("MAILJET_API_SECRET")
    token = base64.b64encode(f"{key}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def missing_config():
    out = [k for k in ("MAILJET_API_KEY", "MAILJET_API_SECRET") if not mail.setting(k)]
    if not mail.sender()[1]:
        out.append("MAIL_FROM")
    return out


def _message(to, subject, text, html):
    name, address = mail.sender()
    msg = {
        "From": {"Email": address, **({"Name": name} if name else {})},
        "To": [{"Email": to}],
        "Subject": subject,
        "TextPart": text,
    }
    if html:
        msg["HTMLPart"] = html
    if mail.reply_to():
        msg["ReplyTo"] = {"Email": mail.reply_to()}
    msg["TrackOpens"] = "disabled"
    msg["TrackClicks"] = "disabled"
    return msg


def send(to, subject, text, html=None):
    """True when Mailjet answered 2xx AND reported the message as
    "success"; False otherwise. Never raises."""
    to = (to or "").strip()
    if not mail.enabled():
        _log(f"MAIL_ENABLED=0 — skip send (to {log_pii.emails(to)})")
        return False
    faltan = missing_config()
    if faltan:
        _log(f"unconfigured ({', '.join(faltan)}) — skip send (to {log_pii.emails(to)})")
        return False
    if not to:
        return False
    body = json.dumps({"Messages": [_message(to, subject, text, html)]}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/v3.1/send", data=body, method="POST",
        headers={"Content-Type": "application/json", **_auth_header()})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            status = getattr(r, "status", 200)
            raw = r.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        _log(f"send FAILED (to {log_pii.emails(to)}): HTTP {exc.code}")
        return False
    except Exception as exc:  # noqa: BLE001 — best-effort, log and move on
        _log(f"send FAILED (to {log_pii.emails(to)}): {log_pii.exc(exc)}")
        return False
    try:
        result = (json.loads(raw.decode("utf-8", "replace")).get("Messages") or [{}])[0]
    except (ValueError, AttributeError, IndexError):
        result = {}
    ok = 200 <= status < 300 and result.get("Status") == "success"
    _log(f"sent (to {log_pii.emails(to)}) -> HTTP {status} {result.get('Status')!r}")
    return ok


def probe():
    """Is the MAIL_FROM address — or its whole domain (`*@domain`) — an
    active sender in this Mailjet account? Never raises."""
    address = mail.sender()[1]
    if missing_config():
        return {"target": address, "state": None, "error": None}
    data, err = mail.get_json(f"{API_BASE}/v3/REST/sender?Limit=1000", headers=_auth_header())
    if err:
        return {"target": address, "state": None, "error": f"Mailjet: {err}"}
    domain = address.rpartition("@")[2].lower()
    wanted = {address.lower(), f"*@{domain}"}
    states = [str(s.get("Status") or "").lower() for s in (data or {}).get("Data") or []
              if str(s.get("Email") or "").lower() in wanted]
    if not states:
        return {"target": address, "state": "not_found", "error": None}
    state = "active" if "active" in states else states[0] or None
    return {"target": address, "state": state, "error": None}
