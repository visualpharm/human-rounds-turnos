"""tests/cassettes/puco/fields.py — PUCO's own local field names for the two
whitelists `_vcr.py` needs (see tests/cassettes/hsi/fields.py's docstring for
the shared contract; this file follows the same pattern for the WS131 REST
call in connectors/puco/puco.py's `_consultar_ws131`).

PII_FIELDS: the WS131 request body is `{"usuario", "clave"}` (a SISA service
account, not a patient) — both force-tokenized as "credencial", same
treatment HSI's fields.py gives `password`. The WS131 response is XML, not
JSON (see puco.py's own docstring: "OJO: pese a la tabla de la doc,
`denominacion` trae el NOMBRE del ciudadano" — the raw response text is never
parsed into a dict by `_vcr.py`'s `_decode_body`, so PII_FIELDS' dict-key
matching can't reach into it; the citizen's name (`denominacion`) and
document (`nrodoc`) inside that XML blob fall through to pii.py's generic
regex sweep like any other free text. `consulta_ok.json`'s "source" note
documents why this scenario uses the repo's own synthetic test fixtures
(never a real citizen) rather than depending on that generic sweep alone.

MATCH_FIELDS: WS131 has no query params and the request body carries only
the service-account credentials (already denylisted above, never usable for
matching per _vcr.py's own rule) — empty on purpose, same as it would be for
any single-scenario cassette (see hsi/fields.py's note: path already
disambiguates every call when a cassette holds one scenario).

Known gap, not solved here (documented per Area 3's "reuse _vcr.py UNCHANGED
unless genuinely required" — this one wasn't, because the pilot never
records a REAL citizen's DNI): PUCO's WS131 URL embeds the DNI directly in
the PATH (`POST /sisa/services/rest/puco/{dni}`), not in a query/body field.
pii.py's generic numeric sweep deliberately excludes digits immediately
preceded by `/` (its `_NUM_RE`'s `(?<![\\w/])` lookbehind — built to avoid
mangling a URL, not to protect one), so a digit run sitting right after a
path separator is NOT caught by `_scrub_interaction`'s generic pass. This
pilot's cassette sidesteps the gap by construction — the DNI recorded here
is one of this repo's own synthetic test fixtures, never a real patient's —
but a FUTURE live WS131 recording (`scripts/record_cassette.py --connector
puco`) would leak a real DNI into the committed path verbatim. Fixing this
for real needs a path-templating whitelist analogous to MATCH_FIELDS/
PII_FIELDS (e.g. a connector-declared `PATH_ID_RE` normalized both at
scrub-time and at replay-match-time) — out of scope for this slice, which
only needed the pilot scenario to be genuinely safe, not the general case.
"""

PII_FIELDS = {
    "usuario": "credencial",
    "clave": "credencial",
}

MATCH_FIELDS = set()
