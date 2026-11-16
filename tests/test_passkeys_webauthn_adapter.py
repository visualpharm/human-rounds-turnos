"""Compatibility tests for historical public-key rows.

The conversion uses maintained py_webauthn decoding and cbor2 encoding. No
custom COSE or signature implementation remains in production code.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cbor2  # noqa: E402
from app.auth import webauthn_adapter as adapter  # noqa: E402


class LegacyStoredKeyCompatibilityTest(unittest.TestCase):
    def test_es256_legacy_row_round_trips_as_standard_cose(self):
        row = {"alg": "ES256", "x": "AQ" * 22, "y": "Ag" * 22}
        # Use real 32-byte coordinates; the values need only form COSE here.
        row = {"alg": "ES256", "x": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE", "y": "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI"}
        cose = adapter.storage_to_public_key(row)
        decoded = cbor2.loads(cose)
        self.assertEqual(decoded[1], 2)
        self.assertEqual(decoded[3], -7)
        self.assertEqual(adapter.public_key_to_storage(cose), row)

    def test_rs256_legacy_row_round_trips_as_standard_cose(self):
        row = {"alg": "RS256", "n": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE", "e": "AQAB"}
        cose = adapter.storage_to_public_key(row)
        decoded = cbor2.loads(cose)
        self.assertEqual(decoded[1], 3)
        self.assertEqual(decoded[3], -257)
        self.assertEqual(adapter.public_key_to_storage(cose), row)

    def test_unsupported_or_incomplete_legacy_rows_fail_closed(self):
        for row in ({}, {"alg": "EdDSA", "x": "abc"}, {"alg": "ES256", "x": "abc"}):
            with self.subTest(row=row), self.assertRaises(Exception):
                adapter.storage_to_public_key(row)


if __name__ == "__main__":
    unittest.main()
