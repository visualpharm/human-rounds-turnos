"""tests/cassettes/meta/fields.py — Meta WhatsApp Cloud API's own local field
names for the two whitelists `_vcr.py` needs (see tests/cassettes/hsi/
fields.py's docstring for the shared contract). Covers `otp_send.py`'s
`_enviar_meta` (the `connectors/meta/adapter.py` capability's
implementation — see that adapter's docstring, it's a thin wrap over this
exact function).

PII_FIELDS: the outbound request carries the destination phone in `to`; the
response echoes it back in `contacts[].input`/`contacts[].wa_id` (Meta's own
Cloud API response shape — see docs/architecture/chat-y-whatsapp.md's "Meta
send" note). All three are the patient's phone number, force-tokenized as
"telefono" same as HSI's fields.py treats `phoneNumber`. `text.body` (the
free-form message text) is NOT denylisted here on purpose — it's staff-
authored template copy (`otp_send._mensaje`/the chat engine's own reply),
never patient-entered free text, so it carries no PII to begin with; pii.py's
generic sweep still runs over it as a safety net regardless.

MATCH_FIELDS: empty — this pilot's single scenario is disambiguated by path
(`/{phone_number_id}/messages`) alone, same reasoning as the other three
connectors' fields.py.
"""

PII_FIELDS = {
    "to": "telefono",
    "input": "telefono",
    "wa_id": "telefono",
}

MATCH_FIELDS = set()
