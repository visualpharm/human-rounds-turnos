"""Password hashing through maintained libraries, with one-way legacy upgrade.

New hashes use pwdlib's recommended Argon2 hasher. The only legacy path parses
the historical ``scrypt$<salt-hex>$<digest-hex>`` storage envelope and asks
``cryptography`` to verify the KDF; successful verification always returns a
fresh Argon2 hash for an atomic compare-and-swap by the owning repository.
"""

import base64

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError
from argon2.profiles import get_default_parameters
from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError


_PASSWORD_HASH = PasswordHash.recommended()
_CURRENT_PARAMETERS = get_default_parameters()
_LEGACY_N = 16384
_LEGACY_R = 8
_LEGACY_P = 1
_LEGACY_SALT_BYTES = 16
_LEGACY_DIGEST_BYTES = 32


def hash_password(password: str | bytes) -> str:
    """Create the sole current password format (Argon2id)."""
    if not isinstance(password, (str, bytes)):
        raise TypeError("password must be str or bytes")
    return _PASSWORD_HASH.hash(password)


def is_current_hash(stored: str) -> bool:
    """True only for a complete Argon2id hash at the current parameters."""
    try:
        if not isinstance(stored, str):
            return False
        parts = stored.split("$")
        if len(parts) != 6 or parts[0] or not parts[4] or not parts[5]:
            return False
        salt = base64.b64decode(parts[4] + "=" * (-len(parts[4]) % 4), validate=True)
        digest = base64.b64decode(parts[5] + "=" * (-len(parts[5]) % 4), validate=True)
        return (
            extract_parameters(stored) == _CURRENT_PARAMETERS
            and len(salt) == _CURRENT_PARAMETERS.salt_len
            and len(digest) == _CURRENT_PARAMETERS.hash_len
        )
    except (InvalidHashError, TypeError, ValueError):
        return False


def _verify_legacy_scrypt(stored: str, password: str) -> bool:
    """Verify the retired envelope using cryptography, never our own KDF."""
    try:
        algorithm, salt_hex, digest_hex = str(stored).split("$", 2)
        if algorithm != "scrypt":
            return False
        if len(salt_hex) != _LEGACY_SALT_BYTES * 2:
            return False
        if len(digest_hex) != _LEGACY_DIGEST_BYTES * 2:
            return False
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(digest_hex)
        if len(salt) != _LEGACY_SALT_BYTES or len(digest) != _LEGACY_DIGEST_BYTES:
            return False
        password_bytes = password if isinstance(password, bytes) else password.encode("utf-8")
        Scrypt(
            salt=salt,
            length=_LEGACY_DIGEST_BYTES,
            n=_LEGACY_N,
            r=_LEGACY_R,
            p=_LEGACY_P,
        ).verify(password_bytes, digest)
        return True
    except (InvalidKey, TypeError, ValueError):
        return False


def verify_and_update(stored: str, password: str | bytes) -> tuple[bool, str | None]:
    """Verify a password and return an Argon2 replacement when required."""
    if not isinstance(stored, str) or not isinstance(password, (str, bytes)):
        return False, None
    value = stored
    if value.startswith("scrypt$"):
        valid = _verify_legacy_scrypt(value, password)
        return (valid, hash_password(password) if valid else None)
    try:
        return _PASSWORD_HASH.verify_and_update(password, value)
    except (PwdlibError, TypeError, ValueError):
        return False, None


# A missing/inactive account performs the same current KDF as a wrong password.
# This is synthetic, public padding material, not a credential.
_DUMMY_HASH = hash_password("synthetic-non-account-password")

def dummy_verify(password):
    verify_and_update(_DUMMY_HASH, password)
