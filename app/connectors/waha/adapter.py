"""ABC-conforming adapter for the waha OTP transport.

Deliberately does NOT SPLIT otp_send.py's implementation per-connector,
unlike every other Phase 6b extraction (it DID move as a whole file into
notifications/otp_send.py under Tier 2 batch B — that's a domain-package
relocation, not the per-connector split this note argues against). Reasons,
found while surveying the module for this slice:

1. otp_send.py's own `wa_provider()` ALREADY implements exactly the seam
   this framework formalizes — Meta by explicit pin or token, WAHA only as
   an explicit loopback dev opt-in, "" (fail-closed no-op) otherwise (the
   2026-08-14 transport contract; before that it auto-detected WAHA as a
   fallback). Physically splitting it into two connector packages would
   duplicate that selection logic instead of clarifying it, and risk two
   places disagreeing about which transport is "active".
2. tests/test_notifications.py monkeypatches `otp_send.wa_provider` and
   `otp_send.enviar_texto` by DIRECT ATTRIBUTE ASSIGNMENT on the imported
   module object (not mock.patch string, not the sys.modules-replacement
   shim other extractions rely on) — every importer sharing the same
   `notifications.otp_send` module object (never a per-connector copy)
   keeps that working with zero risk, no shim trick needed at all.
3. OTP-code sending (`enviar_otp`, gated by OTP_PROVIDER=whatsapp|sms) and
   free-text sending (`enviar_texto`, gated by `wa_provider()`) are TWO
   different selection mechanisms that already coexist in one file — see
   otp_send.py's own docstring. Splitting the file would force a decision
   about unifying them that's out of scope for a mechanical extraction.

So: otp_send.py stays ONE file, untouched internally (moved but not split).
This adapter (and connectors/meta/adapter.py) just wrap its two send
functions for the registry/manage.py's sake — connector.json's config_keys
document what OTP_WAHA_* needs, but the
CONNECTOR_OTP_TRANSPORT config key this framework would normally read is
informational only for this capability; the real runtime selection stays
otp_send.py's own env-driven `wa_provider()`/OTP_PROVIDER, unchanged. See
docs/connectors.md for the full accounting.
"""
from __future__ import annotations

from app.connectors import OtpTransportConnector
from app.notifications import otp_send as _impl


class WahaConnector(OtpTransportConnector):
    """Wraps otp_send.py's WAHA sendText transport."""

    def send(self, destination, text):
        return _impl._enviar_whatsapp(destination, text)
