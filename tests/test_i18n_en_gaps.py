"""The English interface has every string the Spanish one has.

`locales/es/` is the source and `locales/en/` is kept by hand, so a string
added to Spanish and forgotten in English renders Spanish in the English
panel — and its author, browsing in Spanish, never sees it. The on-site tester's
follow-ups (21/10) found ~70 of them at once: the 2FA reminder, the panel
menu, the lab tabs of the patient account, the whole first-run installer.
`scripts/i18n_en_gaps.py` lists both shapes of the gap; this pins it at zero.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_i18n_en_gaps.py"
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import i18n_en_gaps  # noqa: E402
from app.core import i18n  # noqa: E402


class TestEnglishCatalogHasEveryKey(unittest.TestCase):
    def test_every_spanish_key_has_its_english(self):
        missing, _ = i18n_en_gaps.gaps()
        self.assertEqual(missing, {}, "keys in locales/es without locales/en — add the English")

    def test_every_key_the_code_asks_for_is_in_a_catalog(self):
        _, unkeyed = i18n_en_gaps.gaps()
        self.assertEqual(unkeyed, {}, "keys the code renders that no catalog has — add them to es AND en")

    def test_every_settings_label_has_its_keys(self):
        # Settings builds its label keys from the config name
        # (admin.srv.settings.field.<key>), so the literal scan above can't
        # see them: a field added to INTEGRATIONS without its catalog entries
        # rendered Spanish in English (the WhatsApp notice switches, 22/10).
        from app.admin_panel import settings_admin
        es, en = i18n_en_gaps.catalog("es"), i18n_en_gaps.catalog("en")
        missing = []
        for grupo in settings_admin.INTEGRATIONS:
            keys = ["admin.srv.settings.integration.%s.titulo" % grupo["id"]]
            keys += ["admin.srv.settings.field.%s" % key.lower() for key, _label, _tipo in grupo["campos"]]
            missing += [k for k in keys if k not in es or k not in en]
        self.assertEqual(missing, [], "Settings labels without es/en catalog entries")

    def test_the_scan_sees_every_call_shape(self):
        lines = {
            "t_or": 'i18n.t_or("admin.x.one", "Uno", lang)',
            "T": 'T("admin.x.two", null, "Dos")',
            "i18n.t": 'i18n.t("admin.x.three", lang)',
            "jinja": "{{ t('admin.x.four', 'Cuatro') }}",
            "placeholder": "<b>{{t.admin.x.five}}</b>",
        }
        for shape, line in lines.items():
            found = [m.group(1) for pat in i18n_en_gaps._PATTERNS for m in pat.finditer(line)]
            self.assertTrue(found and found[0].startswith("admin.x."), shape)


class TestEnglishRendersEnglish(unittest.TestCase):
    def _in(self, lang, fn):
        _, token = i18n.bind_lang(lang)
        try:
            return fn()
        finally:
            i18n.unbind(token)

    def test_setup_wizard_follows_the_request_language(self):
        from app.ops import setup
        en = self._in("en", setup.page_welcome)
        es = self._in("es", setup.page_welcome)
        self.assertIn("Let&#x27;s set up your installation", en)
        self.assertNotIn("Vamos a configurar", en)
        self.assertIn("Vamos a configurar tu instalación", es)

    def test_2fa_reminder_date_is_one_phrase_per_language(self):
        from app.auth import admin_auth
        admin_auth.bind_2fa({"tiene_2fa": False, "2fa_deadline": "2026-11-03T15:00:00+00:00"})
        try:
            self.assertEqual(self._in("en", admin_auth.active_2fa)["fecha"], "November 3")
            self.assertEqual(self._in("es", admin_auth.active_2fa)["fecha"], "3 de noviembre")
        finally:
            admin_auth.bind_2fa({})


if __name__ == "__main__":
    unittest.main()
