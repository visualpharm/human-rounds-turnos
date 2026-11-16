"""tests/cassettes/mailjet/fields.py — Mailjet Send API v3.1's own field names
for the two whitelists `_vcr.py` needs (see tests/cassettes/hsi/fields.py's
docstring for the shared contract). Covers `connectors/mailjet/mailjet.py`'s
`send()`.

PII_FIELDS: the recipient address travels as `Messages[].To[].Email`; the
response echoes it back as `Messages[].To[].Email` too. Both are tokenized as
"email" if a cassette is ever genuinely recorded.

MATCH_FIELDS: `Messages` — the WHOLE outbound message list is part of the
replay key, so a cassette pins the exact request the transport builds (sender,
recipient, subject, both bodies, Reply-To, tracking switches). A send whose
JSON drifts from the recorded one is an unmatched call and fails the test
instead of being answered. A recorder run would keep the field byte-identical
for the same reason (MATCH_FIELDS values bypass the scrubber), so a real
recording must use fixture addresses only.
"""

PII_FIELDS = {}

MATCH_FIELDS = {"Messages"}
