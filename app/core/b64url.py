"""b64url.py - base64url without padding, the form a passkey challenge, a credential id and a COSE key travel in.

The passkey flow (`auth/passkeys.py`) and the WebAuthn adapter (`auth/webauthn_adapter.py`, which needs
optional packages and so cannot be the one imported by the other) each carried the same pair. Both keep their
own names for it (`b64u`, `b64u_dec` and `_b64u`, `_b64u_dec`); `tests/test_repeated_tails_contract.py` pins what
each answered before they became one.
"""

import base64


def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode(value: str) -> bytes:
    value = str(value or "")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
