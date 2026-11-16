"""Maintained WebAuthn verification and legacy credential conversion.

The ``webauthn`` package owns every registration and authentication
ceremony. The database predates that change and stores inspectable ES256 or
RS256 JSON instead of raw COSE bytes, so this compatibility boundary uses
the package's transitive ``cbor2`` dependency only to reconstruct standard
COSE. It does not parse attestations, curves, or signatures itself.
"""

import cbor2
from webauthn import verify_authentication_response, verify_registration_response
from webauthn.helpers import decode_credential_public_key
from webauthn.helpers.cose import COSEAlgorithmIdentifier, COSECRV, COSEKTY

from app.core import b64url


_b64u = b64url.encode
_b64u_dec = b64url.decode


def public_key_to_storage(cose_key: bytes) -> dict:
    """Project the maintained library's COSE bytes into the legacy DB shape."""
    decoded = decode_credential_public_key(cose_key)
    if decoded.kty == COSEKTY.EC2 and decoded.alg == COSEAlgorithmIdentifier.ECDSA_SHA_256:
        if decoded.crv != COSECRV.P256:
            raise ValueError("curva no soportada")
        return {"alg": "ES256", "x": _b64u(decoded.x), "y": _b64u(decoded.y)}
    if decoded.kty == COSEKTY.RSA and decoded.alg == COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256:
        return {"alg": "RS256", "n": _b64u(decoded.n), "e": _b64u(decoded.e)}
    raise ValueError("algoritmo de clave no soportado")


def storage_to_public_key(public_key: dict) -> bytes:
    """Reconstruct standard COSE bytes for legacy ES256/RS256 DB rows."""
    public_key = public_key or {}
    if public_key.get("alg") == "ES256":
        return cbor2.dumps({
            1: int(COSEKTY.EC2), 3: int(COSEAlgorithmIdentifier.ECDSA_SHA_256),
            -1: int(COSECRV.P256), -2: _b64u_dec(public_key["x"]),
            -3: _b64u_dec(public_key["y"]),
        })
    if public_key.get("alg") == "RS256":
        return cbor2.dumps({
            1: int(COSEKTY.RSA), 3: int(COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256),
            -1: _b64u_dec(public_key["n"]), -2: _b64u_dec(public_key["e"]),
        })
    raise ValueError("algoritmo de clave no soportado")


def verify_registration(*, credential: dict, expected_challenge: bytes,
                        expected_rp_id: str, expected_origin: str):
    """Verify ``navigator.credentials.create`` with the maintained library."""
    return verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
        require_user_presence=True,
        require_user_verification=False,
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )


def verify_authentication(*, credential: dict, expected_challenge: bytes,
                          expected_rp_id: str, expected_origin: str,
                          public_key: dict, current_sign_count: int):
    """Verify ``navigator.credentials.get`` with the maintained library."""
    return verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
        credential_public_key=storage_to_public_key(public_key),
        credential_current_sign_count=int(current_sign_count or 0),
        require_user_verification=False,
    )
