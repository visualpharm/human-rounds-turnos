"""connectors/mailgun/adapter.py — Mailgun as the default `email_transport`.

Wrapped in place, like waha/meta over otp_send.py: notifications/mailgun.py
stays where it is because the suite patches `notifications.mailgun.send` /
`_cfg` / `urllib.request.build_opener` all over, and every one of those
patches must keep reaching the real send. The adapter looks `_impl.send`
up at CALL time, so they do.
"""
from __future__ import annotations

from app.connectors import EmailTransportConnector
from app.notifications import mail_transport
from app.notifications import mailgun as _impl


class MailgunConnector(EmailTransportConnector):
    """Wraps notifications/mailgun.py's HTTP transport."""

    def send(self, to, subject, text, html=None):
        return mail_transport.send(_impl, to, subject, text, html)

    def missing_config(self):
        return _impl.missing_config()

    def probe(self):
        return _impl.probe()
