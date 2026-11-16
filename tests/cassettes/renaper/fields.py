"""tests/cassettes/renaper/fields.py — RENAPER's own local field names for the
two whitelists `_vcr.py` needs (see tests/cassettes/hsi/fields.py's docstring
for the shared contract). Covers `auth_renaper.py`'s OIDC token+userinfo
exchange (`_post_form`, called from `_claims_reales`) — this is the ONE
network-touching path in the identity_verification capability; the
`connectors/renaper/renaper.py` "stub" provider (what `TURNOS_FAKE_RENAPER`
and every other RENAPER test in this repo already exercise) never leaves the
process, so it has nothing for a cassette to record (see docs/roadmap/
tier3-harness-design.md's "the seam" note in Area 3 for why `auth_renaper.py`
is the file, not `connectors/renaper/renaper.py`).

PII_FIELDS: the two OIDC calls are form-urlencoded on the way out (`_vcr.py`'s
`_decode_body` has no dedicated "form" kind, so the request body is captured
as one opaque text blob, not a dict — `client_secret`/`code` never reach the
generic sweep as named fields, only as substrings) and JSON on the way back.
The response side is where a real IDP would hand back identity data, so
that's where the denylist is load-bearing: `id_token`/`access_token` (each a
JWT/opaque bearer whose payload can itself carry claims — force-tokenized
whole, same treatment HSI's fields.py gives `password`) plus every claim
`auth_renaper._extraer_datos` reads off the userinfo response (`dni`,
`nro_documento`, `document_number`, `cuil`, `cuit`, `given_name`, `family_name`,
`first_name`, `last_name`, `nombre`, `apellido`, `email`, `birthdate`,
`fecha_nacimiento`).

MATCH_FIELDS: empty — the token and userinfo calls are disambiguated by path
alone in this single-scenario cassette (same reasoning as hsi/fields.py and
puco/fields.py); every field that COULD disambiguate a second scenario later
(grant_type, code) is either constant across a scenario or PII-shaped
(`code` is the one-time OIDC authorization code, deliberately excluded).
"""

PII_FIELDS = {
    "id_token": "credencial",
    "access_token": "credencial",
    "refresh_token": "credencial",
    "dni": "documento",
    "nro_documento": "documento",
    "document_number": "documento",
    "cuil": "documento",
    "cuit": "documento",
    "given_name": "nombre",
    "family_name": "nombre",
    "first_name": "nombre",
    "last_name": "nombre",
    "nombre": "nombre",
    "apellido": "nombre",
    "email": "email",
    "birthdate": "nacimiento",
    "fecha_nacimiento": "nacimiento",
}

MATCH_FIELDS = set()
