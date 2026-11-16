"""ABC-conforming adapter for the meta OTP transport.

See connectors/waha/adapter.py's docstring for why otp_send.py is wrapped in
place rather than physically extracted.
"""
from __future__ import annotations

from app.connectors import OtpTransportConnector
from app.notifications import otp_send as _impl


class MetaConnector(OtpTransportConnector):
    """Wraps otp_send.py's Meta WhatsApp Cloud API transport. Free-text
    only, within the 24h service window a patient opens by writing first —
    see otp_send.py's module docstring; never used for the OTP code itself."""

    def send(self, destination, text):
        return _impl._enviar_meta(destination, text)

    def send_template(self, destination, name, language, params):
        """An approved Utility template — the only shape Meta delivers for a
        hospital-initiated notice outside the 24h window (otp_send.py's
        `enviar_notificacion` is the gated caller)."""
        return _impl._enviar_meta_plantilla(destination, name, language, params)
