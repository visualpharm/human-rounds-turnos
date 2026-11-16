"""connectors/mailjet/adapter.py — Mailjet as an `email_transport`.
Delegates to mailjet.py at call time (tests patch
`connectors.mailjet.mailjet.send`)."""
from __future__ import annotations

from app.connectors import EmailTransportConnector
from app.notifications import mail_transport
from . import mailjet as _impl


class MailjetConnector(EmailTransportConnector):
    """Wraps connectors/mailjet/mailjet.py's Send API v3.1 transport."""

    def send(self, to, subject, text, html=None):
        return mail_transport.send(_impl, to, subject, text, html)

    def missing_config(self):
        return _impl.missing_config()

    def probe(self):
        return _impl.probe()
