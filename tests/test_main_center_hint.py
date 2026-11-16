"""The installation's main center in the patient's hints (the product owner 23.10, voice).

Where a problem needs someone in person, the patient is pointed to ONE named
place — the installation's main center (Settings «Centro principal», over
sitio.json `main_center`) with its address and its page — never «tu centro
de salud». Proves: the resolver (pack default, Settings override, unknown
slug → None), the portal refusal's config, the two booking rejections that
used to say «el centro de salud», and the Settings field (listed centers,
unknown slug refused).

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_main_center_hint.py
"""

import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config, site_identity  # noqa: E402
from app.patient_web import site_centers

_CENTERS = [
    {"slug": "hospital-x", "name": "Hospital X", "address": "Av. Uno 100",
     "phone": "(011) 4000-0001"},
    {"slug": "caps-y", "name": "CAPS Y", "address": "Calle Dos 200",
     "whatsapp": "2254 60-2222"},
]


def _main(slug):
    real = site_identity.get
    return patch.object(site_identity, "get",
                        side_effect=lambda k, *a, **kw: slug if k == "main_center" else real(k, *a, **kw))


class _Centers(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(site_centers, "_centers", return_value=[dict(c) for c in _CENTERS]))


class TestResolver(_Centers):
    def test_names_the_configured_center_with_address_and_page(self):
        with _main("hospital-x"):
            place = site_centers.main_center_place()
            self.assertEqual(place["nombre"], "Hospital X")
            self.assertEqual(place["direccion"], "Av. Uno 100")
            self.assertEqual(place["url"], "/center/hospital-x")
            self.assertTrue(place["telefono"])
            self.assertEqual(site_centers.main_center_text(), "Hospital X, Av. Uno 100")

    def test_unknown_or_unset_center_is_none(self):
        for slug in ("", "no-existe"):
            with self.subTest(slug=slug), _main(slug):
                self.assertIsNone(site_centers.main_center_place())
                self.assertEqual(site_centers.main_center_text(), "")

    def test_settings_override_wins_over_the_pack(self):
        self.assertIn("main_center", site_identity.OVERRIDABLE_KEYS)
        with patch("app.core.config.get", side_effect=lambda k, *a, **kw: "caps-y" if k == "SITE_ID_MAIN_CENTER" else None):
            self.assertEqual(site_identity.get("main_center"), "caps-y")


class TestBookingRejections(_Centers):
    def test_horizon_rejection_names_the_main_center(self):
        from app.modules.turnos import booking_horizon
        with _main("hospital-x"):
            detalle = booking_horizon.rejection({"id": "s1"})["detalle"]
        self.assertIn("Hospital X, Av. Uno 100", detalle)
        self.assertNotIn("centro de salud", detalle)

    def test_horizon_rejection_without_main_center_keeps_the_generic_text(self):
        from app.modules.turnos import booking_horizon
        with _main(""):
            detalle = booking_horizon.rejection({"id": "s1"})["detalle"]
        self.assertIn("centro de salud", detalle)

    def test_no_show_block_names_the_main_center(self):
        from app.modules.turnos import booking_requests as bookings
        from app.modules.turnos import booking_create
        with _main("hospital-x"), \
             patch.object(bookings.store, "attendance_summary_dni",
                          return_value={"bloqueado_web": True, "faltas": 3}):
            status, payload = booking_create.create_request(
                {"dni": "30111224", "nombre": "Ana", "apellido": "Prueba"},
                {"servicio_id": "s1", "centro_slug": "hospital-x"})
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "bloqueado_por_ausencias")
        self.assertIn("Hospital X, Av. Uno 100", payload["detalle"])
        self.assertEqual(payload["centro"]["url"], "/center/hospital-x")


class TestPortalRefusal(_Centers):
    def test_portal_screen_carries_the_main_center(self):
        from app.cuentas import accounts
        # cuentas reads it through core (a domain package never imports the
        # public site); the public site registers its resolver at import.
        self.assertIs(site_identity._main_center_place, site_centers.main_center_place)
        with _main("hospital-x"):
            screen = accounts._portal_screen(None)
        self.assertEqual(screen["centro_principal"]["nombre"], "Hospital X")
        self.assertEqual(screen["centro_principal"]["url"], "/center/hospital-x")
        with _main(""):
            self.assertIsNone(accounts._portal_screen(None)["centro_principal"])

    def test_password_recovery_config_carries_the_main_center_too(self):
        """TASK-50 (26.10): recovery is by email only; its screen names the
        admission desk where an account without email gets one — in every
        auth mode, not only the portal's."""
        from app.cuentas import accounts
        with _main("hospital-x"), \
                patch.object(accounts, "_effective_auth_mode", return_value="password"):
            result = accounts._config_core({}, "127.0.0.1")
        body = result.json_body
        self.assertEqual(body["auth_mode"], "password")
        self.assertEqual(body["centro_principal"]["nombre"], "Hospital X")
        self.assertIn("ayuda_whatsapp", body)
        self.assertNotIn("portal_login_url", body)


class TestSettingsField(_Centers):
    def setUp(self):
        super().setUp()
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        self.saved = {}
        self.enterContext(patch.object(config, "guardar", side_effect=lambda ov: self.saved.update(ov) or self.saved))
        from app.admin_panel import settings_admin
        self.sa = settings_admin
        self.enterContext(patch.object(settings_admin.data_packs, "invalidate", lambda: None))

    def test_payload_lists_the_centers_to_choose_from(self):
        payload = self.sa._location_payload()
        self.assertEqual([c["slug"] for c in payload["centros"]], ["hospital-x", "caps-y"])
        self.assertIn("main_center", payload["identidad"])

    def test_unknown_center_is_refused_and_a_known_one_saved(self):
        out, err = self.sa._guardar_location({"SITE_ID_MAIN_CENTER": "no-existe"})
        self.assertIsNone(out)
        self.assertTrue(err)
        self.assertNotIn("SITE_ID_MAIN_CENTER", self.saved)
        out, err = self.sa._guardar_location({"SITE_ID_MAIN_CENTER": "caps-y"})
        self.assertIsNone(err)
        self.assertEqual(self.saved["SITE_ID_MAIN_CENTER"], "caps-y")


if __name__ == "__main__":
    unittest.main()
