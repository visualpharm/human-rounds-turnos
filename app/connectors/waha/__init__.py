"""connectors/waha — an `otp_transport` connector (WhatsApp via a WAHA
instance). See connector.json and adapter.py — unlike every other Phase 6b
connector, this one does NOT relocate its implementation: it wraps
otp_send.py (unmoved, still at the repo root) in place. Why, in adapter.py's
docstring.
"""
