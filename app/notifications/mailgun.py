"""email_mailgun.py — transactional email transport (Mailgun HTTP API).

Reuses the product owner's existing Mailgun account (same account backing inglesconjenny
and lira — see credential vault "Mailgun" item, AI vault; ported from
<workspace>/inglesconjenny/app/email.py). Sending is BEST-EFFORT and never
raises: a mail failure must never break a request. If the config env vars
are missing, or the sending domain isn't DNS-verified yet, this degrades to
a logged no-op — same shape as hsi_client when the HSI module is absent.

The default `email_transport` connector (connectors/mailgun/ wraps this file
in place); callers go through `notifications.mail.send()`, never here.

Env (read through config.py at call time — env, .env or Settings):
  MAILGUN_API_KEY   Mailgun private API key (api:<key> basic auth)
  MAILGUN_DOMAIN    sending domain, e.g. mg.sana-turnos.brieflysites.com
  MAILGUN_API_BASE  region endpoint; empty = US (https://api.mailgun.net),
                    EU = https://api.eu.mailgun.net
  MAIL_FROM         From header, e.g. "Pinamar Turnos <no-reply@example.test>"
  MAIL_REPLY_TO     optional Reply-To
  MAIL_ENABLED      set to "0" to hard-disable even when creds are present
"""

import base64
import sys
import urllib.parse
import urllib.request

from app.core import config
from app.core import log_pii

_DEFAULT_API_BASE = "https://api.mailgun.net"


_get = config.get_text


def api_base():
    """The configured region's API root, read per call — the send, the
    Settings «Probar» and «Sistema» all hit the SAME region (an EU domain
    does not exist on the US endpoint)."""
    return (_get("MAILGUN_API_BASE") or _DEFAULT_API_BASE).rstrip("/")


def missing_config():
    return [k for k in ("MAILGUN_API_KEY", "MAILGUN_DOMAIN") if not _get(k)]


def probe():
    """Live domain state on the configured region. Never raises."""
    from app.notifications import mail
    key, domain = _get("MAILGUN_API_KEY"), _get("MAILGUN_DOMAIN")
    if not key or not domain:
        return {"target": domain, "state": None, "error": None}
    auth = base64.b64encode(f"api:{key}".encode()).decode()
    data, err = mail.get_json(f"{api_base()}/v3/domains/{domain}",
                              headers={"Authorization": f"Basic {auth}"})
    if err:
        return {"target": domain, "state": None, "error": f"Mailgun: {err}"}
    state = (((data or {}).get("domain") or {}).get("state") or "").lower() or None
    return {"target": domain, "state": state, "error": None}


def _cfg():
    from app.core import theme
    key = _get("MAILGUN_API_KEY")
    domain = _get("MAILGUN_DOMAIN")
    frm = _get("MAIL_FROM") or (
        f"{theme.brand_name()} <no-responder@{domain}>" if domain else ""
    )
    enabled = _get("MAIL_ENABLED", "1") != "0"
    return key, domain, frm, enabled


def _log(msg):
    print(f"[email_mailgun] {msg}", file=sys.stderr, flush=True)


def send(to, subject, text, html=None):
    """Send an email. `text` is ALWAYS included (the plain-text fallback
    every mail client that doesn't render HTML still shows); `html` is
    optional — when given, Mailgun sends both parts in the same message
    (its API just takes an extra `html` form field, no MIME building needed
    here). Returns True on Mailgun 200, False otherwise. Never raises —
    callers treat email as fire-and-forget."""
    key, domain, frm, enabled = _cfg()
    to = (to or "").strip()
    if not enabled or not key or not domain:
        # Never the subject: it can carry the patient's name (core/log_pii.py).
        _log(f"disabled/unconfigured — skip send to {log_pii.emails(to)}")
        return False
    if not to:
        return False
    fields = {
        "from": frm,
        "to": to,
        "subject": subject,
        "text": text,
        **({"h:Reply-To": _get("MAIL_REPLY_TO")} if _get("MAIL_REPLY_TO") else {}),
    }
    if html:
        fields["html"] = html
    data = urllib.parse.urlencode(fields).encode()
    url = f"{api_base()}/v3/{domain}/messages"
    auth = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    auth.add_password(None, url, "api", key)
    opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(auth))
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with opener.open(req, timeout=8) as r:
            ok = 200 <= r.status < 300
            _log(f"sent to {log_pii.emails(to)} -> HTTP {r.status}")
            return ok
    except Exception as exc:  # noqa: BLE001 — best-effort, log and move on
        _log(f"send FAILED to {log_pii.emails(to)}: {log_pii.exc(exc)}")
        return False
