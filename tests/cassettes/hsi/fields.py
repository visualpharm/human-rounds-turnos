"""tests/cassettes/hsi/fields.py — the HSI connector's own local field names for
the two whitelists `_vcr.py` needs (see its docstring). One of these files
lives next to each connector's cassette directory, per the design's "each
connector's own local names for these" (Area 3, tier3-harness-design.md).

PII_FIELDS: request/response JSON keys (body OR query) whose STRING value is
patient- or professional-identifying, regardless of whether pii.py's generic
sweep would ever flag it on its own — force-tokenized via `pii.Scrubber.token`
before a cassette is written. Value = the `pii.py` `tipo` to tag it as (falls
back to the raw string as the label text if it isn't one of pii.py's five
built-in `_ETIQUETAS`).

MATCH_FIELDS: query/body keys the replay matcher may compare when choosing
which recorded interaction answers a call. Deliberately excludes every name
in PII_FIELDS (a scrubbed cassette value can never equal a live request's real
DNI/name) and every credential/volatile field (password, the JWT, the two
patient-id-search params that are only informative alongside identificationNumber).
For this HSI pilot's single-scenario cassette these aren't even load-bearing —
static path segments already disambiguate every call — but they're populated
for real ahead of slice 6 (PUCO/RENAPER/Meta), which will need real
disambiguation once a cassette holds more than one scenario's worth of calls.
"""

PII_FIELDS = {
    "dni": "documento",
    "identificationNumber": "documento",
    "licenceNumber": "documento",
    "nombre": "nombre",
    "apellido": "nombre",
    "firstName": "nombre",
    "lastName": "nombre",
    "middleNames": "nombre",
    "otherLastNames": "nombre",
    "mothersLastName": "nombre",
    "nameSelfDetermination": "nombre",
    "fullName": "nombre",
    "telefono": "telefono",
    "phoneNumber": "telefono",
    "email": "email",
    "patientEmail": "email",
    "applicantHealthcareProfessionalEmail": "email",
    "username": "email",
    "cuil": "documento",
    "birthDate": "nacimiento",
    "password": "credencial",
}

MATCH_FIELDS = {
    "identificationTypeId",
    "genderId",
    "healthcareProfessionalId",
    "includeExpired",
    "from",
    "to",
    "diaryId",
    "date",
    "hour",
    "openingHoursId",
    "patientId",
    "overturn",
    "modality",
}
