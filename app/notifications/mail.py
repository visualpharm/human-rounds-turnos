"""notifications/mail.py — the ONE entry point every email sender calls.

Which provider carries the message is the `email_transport` connector
capability (connectors/__init__.py): Mailgun (`connectors/mailgun/`, wrapping
notifications/mailgun.py in place) is the flagged default, so an installation
that configures nothing sends exactly as before; Mailjet
(`connectors/mailjet/`) is chosen with CONNECTOR_EMAIL_TRANSPORT=mailjet
(salud.pinamar.gob.ar, 2026-10-20 — the municipality's own Premium
sub-account instead of a second provider).

Shared settings, read through config.py at call time (env, .env or Settings):
  CONNECTOR_EMAIL_TRANSPORT  empty = mailgun · mailjet · off
  MAIL_FROM                  From header, "Name <address>"
  MAIL_REPLY_TO              optional Reply-To address
  MAIL_ENABLED               "0" hard-disables every provider (the demo pins it;
                             each transport checks it — `enabled()` below)

Best-effort like every transport here: no provider routed, missing keys or a
provider failure → False and a log line, never an exception.
"""

import email.utils
import json
import socket
import sys
import urllib.error
import urllib.request

from app.core import config
from app.core import launch_signals
from app.core import log_pii

_TIMEOUT = 6


def _log(msg):
    print(f"[mail] {msg}", file=sys.stderr, flush=True)


setting = config.get_text


def enabled():
    return setting("MAIL_ENABLED", "1") != "0"


def sender():
    """(display name, address) parsed from MAIL_FROM; ("", "") when unset."""
    name, addr = email.utils.parseaddr(setting("MAIL_FROM"))
    return name.strip(), addr.strip()


def reply_to():
    return setting("MAIL_REPLY_TO")


def transport():
    """(adapter | None, resolved provider id | None, source) for the
    `email_transport` capability — `source` is connectors.resolve()'s own
    answer ("default", "explicit", "off", "invalid" or None)."""
    from app import connectors
    provider, source = connectors.resolve(connectors.EMAIL_TRANSPORT)
    adapter = connectors._instantiate_adapter(provider) if provider else None
    return adapter, provider, source


def _carries_loopback_link(text, html):
    """Only the unset fallback counts: an origin set on purpose, loopback
    included (a local receiver, the browser suites), is the operator's choice."""
    from app.core import site_identity
    if str(config.get("TURNOS_PUBLIC_ORIGIN", "") or "").strip():
        return False
    if not site_identity.origin_is_loopback():
        return False
    origin = site_identity.base_url()
    return origin in str(text or "") or origin in str(html or "")


SENT, SKIPPED, FAILED = "sent", "skipped", "failed"


def deliver(to, subject, text, html=None):
    """Send one email through the selected provider and answer what happened: `SENT` (the provider accepted
    it), `SKIPPED` (the configuration kept it from leaving: its links would point at this machine, no provider
    is routed, mail is switched off with MAIL_ENABLED=0, the process runs under the outbound kill switch
    `core/outbound_guard`, or the provider lacks its keys) or `FAILED` (the provider refused it or the send
    raised). `send` is this answer as a bool; the day-before reminders read the reason, because they retry a
    failure and never a skip (the product owner's hub review, 2026-11-10). `text` always goes (the plain part every client
    can show); `html` is optional. Never raises. MAIL_ENABLED=0 and the keys are honored by each transport
    itself (mailgun._cfg, mailjet.send), exactly where Mailgun always applied them; a refusal is only read
    for its reason afterwards, from the same provider."""
    adapter = None
    try:
        if _carries_loopback_link(text, html):
            _log(f"TURNOS_PUBLIC_ORIGIN not set — links would point at this machine; "
                 f"skip send (to {log_pii.emails(to)})")
            return SKIPPED
        adapter, provider, source = transport()
        if adapter is None:
            _log(f"no email provider routed ({source or 'none'}) — skip send (to {log_pii.emails(to)})")
            return SKIPPED
        accepted = bool(adapter.send(to, subject, text, html=html))
        # Counted, never described: the launch digest reports sent and failed, `mail_failed` is the Sentry code.
        if accepted:
            launch_signals.count("mail_sent")
            return SENT
        launch_signals.handled("mail_failed")
    except Exception as exc:  # noqa: BLE001 — email never breaks a request
        _log(f"send FAILED (to {log_pii.emails(to)}): {log_pii.exc(exc)}")
        launch_signals.handled("mail_failed")
    return _refusal(adapter)


def _refusal(adapter):
    """Why a message the provider did not take did not leave: `SKIPPED` when mail is off, the outbound kill
    switch is armed or the provider lacks its keys, `FAILED` otherwise — and when that cannot be told (a
    reminder is then retried, never lost)."""
    try:
        from app.core import outbound_guard
        if not enabled() or outbound_guard.armed() or (adapter is not None and adapter.missing_config()):
            return SKIPPED
    except Exception:  # noqa: BLE001
        pass
    return FAILED


def send(to, subject, text, html=None):
    """`deliver` as True (the provider accepted it) or False — what every sender but the day-before reminders
    needs. A message whose links point at the loopback fallback origin (no TURNOS_PUBLIC_ORIGIN set) is not
    sent: the recipient could not open them. Never raises."""
    return deliver(to, subject, text, html=html) == SENT


def get_json(url, headers=None, timeout=_TIMEOUT):
    """GET `url` → (data | None, error | None). Never raises. The providers'
    live checks (Settings «Probar», «Sistema») share it."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw), None
    except urllib.error.HTTPError as e:
        e.close()
        return None, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout) as e:
        return None, f"sin conexión ({getattr(e, 'reason', e)})"
    except (ValueError, OSError) as e:
        return None, str(e)
