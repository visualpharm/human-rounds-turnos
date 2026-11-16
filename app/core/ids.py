"""ids.py — the digits of an identifier, in one place (2026-10-25).

The same `re.sub(r"\\D", "", str(v or ""))` was written inline 44 times and wrapped six more times
(`_digits`, `_dni`, `_digitos`, `_norm_dni`): the DNI a patient types, the one OCR reads, the one the
account stores and the phone/code digits all go through it. `dni()` is the name a DNI call site
reads by — the one place to add a DNI-only rule (length, check) if one is ever decided; today it
is exactly `digits()`.

What is NOT this: `format._digits` (a `0` is the value «0» there, not «nothing») and the two `isdigit()`
copies in `ai_referral` / `lab_results` (`isdigit()` keeps «²»; `\\D` drops it). `tests/test_shared_helpers_contract.py`
pins all of them, those apart included.
"""

import re


def digits(value):
    """Only the decimal digits of `value` («12.345.678» → «12345678»); «» for a falsy value."""
    return re.sub(r"\D", "", str(value or ""))


def dni(value):
    """The digits of a DNI as typed, scanned or stored."""
    return digits(value)


_DNI_AS_WRITTEN = re.compile(r"[\d.\s-]*\d[\d.\s-]*")


def dni_query(text):
    """The digits of a search text that is a DNI as people write it — «30.009.042», «30 009 042» —
    or «» when the text has anything else in it (a name, «A. Gómez»). The panel shows DNIs with dots
    and the desk copies them that way; the stored DNI is digits only."""
    text = str(text or "").strip()
    return digits(text) if _DNI_AS_WRITTEN.fullmatch(text) else ""
