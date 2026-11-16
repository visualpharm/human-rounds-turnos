"""Regression test: analyze_referral_3pass without an attached order.

In fake-AI mode, a booking request with NO attached referral should NOT
show a fabricated AI reading. This test ensures that with no order attached,
no AI reading is produced or shown at all.
"""

import os
import sys
import unittest

# raíz del repo en el path para importar los módulos
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Deterministic fake mode BEFORE importing modules
import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.ai import ai_referral


class TestAnalyzeReferralNoOrder(unittest.TestCase):
    """Regression test for the bug where fake AI mode returns a fabricated
    reading even when no order is attached."""

    def test_no_file_path_returns_manual_review(self):
        """With no file path (None), analyze_referral_3pass should return
        'revision_manual', not a fabricated fake result."""
        solicitud = {
            "dni": "13452513",
            "nombre": "Mónica",
            "apellido": "Alustiza",
            "servicio_id": "cardiologia",
        }
        result = ai_referral.analyze_referral_3pass(None, "image/png", solicitud)

        # With no order attached, verdict should be revision_manual
        self.assertEqual(result["verdict"], "revision_manual",
                         "No order should result in manual review, not a fake reading")

        # Should not contain any fabricated fields
        self.assertNotEqual(result["campos"].get("medico", {}).get("valor"), "Dr. Ejemplo",
                           "Should not show fake doctor when no order attached")
        self.assertEqual(result["pases"], 0,
                        "Should have 0 passes when no file to analyze")
        self.assertEqual(result["confianza"], 0.0,
                        "Confidence should be 0 when no order analyzed")

    def test_missing_file_returns_manual_review(self):
        """With a file path that doesn't exist, analyze_referral_3pass should
        return 'revision_manual', not a fabricated fake result."""
        solicitud = {
            "dni": "13452513",
            "nombre": "Mónica",
            "apellido": "Alustiza",
            "servicio_id": "cardiologia",
        }
        result = ai_referral.analyze_referral_3pass(
            "/no/such/path/orden.jpg", "image/jpeg", solicitud
        )

        # With a missing file, verdict should be revision_manual
        self.assertEqual(result["verdict"], "revision_manual",
                         "Missing order file should result in manual review, not a fake reading")

        # Should not contain any fabricated fields
        self.assertNotEqual(result["campos"].get("medico", {}).get("valor"), "Dr. Ejemplo",
                           "Should not show fake doctor when order file missing")
        self.assertEqual(result["pases"], 0,
                        "Should have 0 passes when file doesn't exist")
        self.assertEqual(result["confianza"], 0.0,
                        "Confidence should be 0 when no order analyzed")

    def test_with_file_fake_mode_returns_fake_result(self):
        """In fake mode, a request WITH an attached file should still return
        a fabricated reading (the behavior that IS expected with a file)."""
        import tempfile

        # Create a temporary file to represent an attached order
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            temp_path = f.name
            f.write(b"fake image data")

        try:
            solicitud = {
                "dni": "13452513",
                "nombre": "Mónica",
                "apellido": "Alustiza",
                "servicio_id": "cardiologia",
            }
            result = ai_referral.analyze_referral_3pass(temp_path, "image/jpeg", solicitud)

            # WITH a file, fake mode SHOULD return a positive fake result
            self.assertEqual(result["verdict"], "parece_correcta",
                            "With file in fake mode, should return positive reading")
            self.assertEqual(result["pases"], 1,
                            "Should have 1 pass when file exists in fake mode")
            self.assertGreater(result["confianza"], 0.5,
                              "Confidence should be high for fake positive result")
        finally:
            os.unlink(temp_path)


if __name__ == "__main__":
    unittest.main()
