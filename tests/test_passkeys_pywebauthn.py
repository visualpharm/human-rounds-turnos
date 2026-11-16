"""Behavior tests for the library-owned WebAuthn ceremony boundary.

These tests deliberately exercise py_webauthn's public verify functions, not
the former custom parser or crypto helpers. They do not require PostgreSQL.
"""

import hashlib
import json
import os
import sys
import unittest
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _env  # noqa: E402
from app.auth import passkeys as pk  # noqa: E402
from app.auth import webauthn_adapter as wa  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from test_passkeys import Autenticador, ORIGEN  # noqa: E402


class PyWebauthnCeremonyTest(unittest.TestCase):
    def setUp(self):
        if "TURNOS_ADMIN_SECRET" not in os.environ:
            _env.set_env(self, {"TURNOS_ADMIN_SECRET": "test-only-secret"})
        admin_auth._ATTEMPTS.clear()
        self.authenticator = Autenticador()
        self.registration_challenge = pk.b64u(b"library-registration-challenge")
        registration = self.authenticator.alta(self.registration_challenge, ORIGEN)
        self.registration = registration
        self.verified_registration = wa.verify_registration(
            credential=pk._registration_credential(registration),
            expected_challenge=pk.b64u_dec(self.registration_challenge),
            expected_rp_id="localhost", expected_origin=ORIGEN,
        )
        self.public_key = wa.public_key_to_storage(self.verified_registration.credential_public_key)

    def _login(self, *, origin=ORIGEN, count=1, flags=0x01, authenticator=None):
        challenge = pk.b64u(b"library-login-challenge-" + str(count).encode("ascii"))
        device = authenticator or self.authenticator
        client_data = device._client_data("webauthn.get", challenge, origin)
        auth_data = device._auth_data(False, flags=flags, count=count)
        return challenge, {
            "challenge": challenge,
            "id": pk.b64u(device.credential_id),
            "response": {
                "clientDataJSON": pk.b64u(client_data),
                "authenticatorData": pk.b64u(auth_data),
                "signature": pk.b64u(device.firmar(auth_data + hashlib.sha256(client_data).digest())),
            },
        }

    def _verify_login(self, challenge, credential, *, expected_origin=ORIGEN, expected_rp_id="localhost", current=0):
        return wa.verify_authentication(
            credential=pk._authentication_credential(credential),
            expected_challenge=pk.b64u_dec(challenge), expected_rp_id=expected_rp_id,
            expected_origin=expected_origin, public_key=self.public_key,
            current_sign_count=current,
        )

    def test_stored_es256_shape_round_trips_through_library(self):
        self.assertEqual(set(self.public_key), {"alg", "x", "y"})
        challenge, credential = self._login()
        verified = self._verify_login(challenge, credential)
        self.assertEqual(verified.credential_id, self.authenticator.credential_id)
        self.assertEqual(verified.new_sign_count, 1)

    def test_historical_rs256_row_verifies_through_library(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private_key.public_key().public_numbers()
        public_key = {
            "alg": "RS256",
            "n": pk.b64u(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
            "e": pk.b64u(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
        }
        challenge = pk.b64u(b"legacy-rs256-library-challenge")
        client_data = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": ORIGEN}).encode()
        auth_data = hashlib.sha256(b"localhost").digest() + b"\x01" + (1).to_bytes(4, "big")
        signature = private_key.sign(auth_data + hashlib.sha256(client_data).digest(),
                                     padding.PKCS1v15(), hashes.SHA256())
        credential = {
            "challenge": challenge, "id": pk.b64u(b"legacy-rs256-id"),
            "response": {"clientDataJSON": pk.b64u(client_data),
                         "authenticatorData": pk.b64u(auth_data), "signature": pk.b64u(signature)},
        }
        verified = wa.verify_authentication(
            credential=pk._authentication_credential(credential),
            expected_challenge=pk.b64u_dec(challenge), expected_rp_id="localhost",
            expected_origin=ORIGEN, public_key=public_key, current_sign_count=0,
        )
        self.assertEqual(verified.new_sign_count, 1)

    def test_wrong_registration_challenge_type_origin_and_rp_fail(self):
        wrong_challenge = pk.b64u(b"wrong-registration-library-challenge")
        with self.assertRaises(Exception):
            wa.verify_registration(credential=pk._registration_credential(self.registration),
                                   expected_challenge=pk.b64u_dec(wrong_challenge),
                                   expected_rp_id="localhost", expected_origin=ORIGEN)
        bad_type = json.loads(json.dumps(self.registration))
        payload = json.loads(pk.b64u_dec(bad_type["response"]["clientDataJSON"]))
        payload["type"] = "webauthn.get"
        bad_type["response"]["clientDataJSON"] = pk.b64u(json.dumps(payload).encode())
        with self.assertRaises(Exception):
            wa.verify_registration(credential=pk._registration_credential(bad_type),
                                   expected_challenge=pk.b64u_dec(self.registration_challenge),
                                   expected_rp_id="localhost", expected_origin=ORIGEN)
        with self.assertRaises(Exception):
            wa.verify_registration(credential=pk._registration_credential(self.registration),
                                   expected_challenge=pk.b64u_dec(self.registration_challenge),
                                   expected_rp_id="wrong.example", expected_origin=ORIGEN)
        with self.assertRaises(Exception):
            wa.verify_registration(credential=pk._registration_credential(self.registration),
                                   expected_challenge=pk.b64u_dec(self.registration_challenge),
                                   expected_rp_id="localhost", expected_origin="https://wrong.example")

    def test_login_wrong_challenge_origin_rp_up_signature_and_counter_fail(self):
        challenge, credential = self._login()
        wrong_challenge = pk.b64u(b"wrong-login-library-challenge")
        with self.assertRaises(Exception):
            self._verify_login(wrong_challenge, credential)
        with self.assertRaises(Exception):
            self._verify_login(challenge, credential, expected_origin="https://wrong.example")
        with self.assertRaises(Exception):
            self._verify_login(challenge, credential, expected_rp_id="wrong.example")
        no_up_challenge, no_up = self._login(flags=0)
        with self.assertRaises(Exception):
            self._verify_login(no_up_challenge, no_up)
        impostor = Autenticador(credential_id=self.authenticator.credential_id)
        foreign_challenge, foreign = self._login(authenticator=impostor)
        with self.assertRaises(Exception):
            self._verify_login(foreign_challenge, foreign)
        replay_challenge, replay = self._login(count=1)
        with self.assertRaises(Exception):
            self._verify_login(replay_challenge, replay, current=1)

    def test_library_failure_is_not_replaced_by_custom_crypto(self):
        # ``login_result`` first authenticates the signed outer ceremony
        # token; verification then fails before it attempts durable consume.
        challenge = pk.reto_wire(pk.nuevo_reto("login"))
        client_data = self.authenticator._client_data("webauthn.get", challenge, ORIGEN)
        auth_data = self.authenticator._auth_data(False, flags=0x01, count=1)
        credential = {
            "challenge": challenge,
            "id": pk.b64u(self.authenticator.credential_id),
            "response": {
                "clientDataJSON": pk.b64u(client_data),
                "authenticatorData": pk.b64u(auth_data),
                "signature": pk.b64u(self.authenticator.firmar(
                    auth_data + hashlib.sha256(client_data).digest())),
            },
        }
        from app.core import db_auth
        row = {"usuario": admin_auth._admin_user(), "public_key": self.public_key, "sign_count": 0}
        with mock.patch.object(wa, "verify_authentication", side_effect=RuntimeError("library unavailable")), \
             mock.patch.object(db_auth, "passkey_by_credential", return_value=row):
            result = pk.login_result({"Host": "localhost:7700"}, "127.0.0.1", json.dumps(credential).encode())
        self.assertEqual(result.status, 401)
        self.assertEqual(result.body, {"error": "respuesta_webauthn_invalida"})
        self.assertIsNone(result.set_cookie)
        self.assertFalse(hasattr(pk, "verificar_firma"))
        self.assertFalse(hasattr(pk, "cbor_decode"))


if __name__ == "__main__":
    unittest.main()
