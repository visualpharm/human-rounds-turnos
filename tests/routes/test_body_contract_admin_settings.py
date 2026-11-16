"""tests/routes/test_body_contract_admin_settings.py — the panel's settings and
content JSON routes: the settings screen and its probe, the theme / sign-in
mode / Turnstile triad, branding (colour, logo), the legal texts, the content
catalog (create, edit, knowledge properties), the interview settings, the
chat voice model and the AI prompts: every answer to the body battery of
`_body_contract.py`, pinned against `tests/golden/body_contract_admin_settings.json`
(TASK-24, Pydantic stage 7).

Each case starts from a truncated database with an admin; branding files go to
a temporary directory.
"""

import base64
import io
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _body_contract as bc  # noqa: E402

from app.admin_panel import api_models as p  # noqa: E402
from app.admin_panel import settings_admin_router  # noqa: E402
from app.ai import prompts_router  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.modules.chat import voice_router  # noqa: E402
from app.modules.intake import api_models as i  # noqa: E402
from app.modules.turnos import api_models as t  # noqa: E402
from app.patient_web import api_models as w  # noqa: E402

_ADMIN = "staff.ajustes"
_GOLDEN = bc.Golden("admin_settings")


def _png_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (20, 90, 160)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _center():
    """A centre of the public example pack, as the panel lists it."""
    from app.patient_web import catalog
    item = dict(catalog.admin_list("centros")[0])
    return {k: v for k, v in item.items() if not k.startswith("_")}


class TestAdminSettingsBodyContract(RouteTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.patient_web import branding
        cls._branding_attrs = {k: getattr(branding, k) for k in dir(branding)
                               if k.endswith("_DIR") and isinstance(getattr(branding, k), str)}
        cls._tmp = tempfile.mkdtemp(prefix="body-contract-branding-")
        for k in cls._branding_attrs:
            setattr(branding, k, cls._tmp)

    @classmethod
    def tearDownClass(cls):
        from app.patient_web import branding
        for k, v in cls._branding_attrs.items():
            setattr(branding, k, v)
        shutil.rmtree(cls._tmp, ignore_errors=True)
        if bc.WRITE:
            _GOLDEN.save()
        super().tearDownClass()

    def _staff(self):
        from app.patient_web import catalog
        self.db.truncate_all()
        catalog.invalidate()
        db_operators.operator_create(_ADMIN, password="clave-staff-12", rol="admin")
        return admin_cookie(usuario=_ADMIN, rol="admin")

    def _pin(self, path, valid, method="POST", docs=None, key=None, reads=None):
        bc.pin_route(self, _GOLDEN, method, path, valid, reset=self._staff, auth=True,
                     docs=docs, key=key, reads=reads)

    def test_no_body_crashes_a_route(self):
        """Since stage 7 a body the panel never sends is a 400 of the family, not
        a 500: the golden file holds no 500 at all."""
        crashes = [(route, case) for route, cases in _GOLDEN.sections.items()
                   for case, got in cases.items() if got["status"] == 500]
        self.assertEqual(crashes, [])

    def test_settings(self):
        self._pin("/api/admin/settings", {"grupo": "chat", "valores": {}, "borrar": []},
                  docs=(p.SettingsIn, p.SettingsOut), reads=settings_admin_router._lenient)

    def test_settings_probe(self):
        self._pin("/api/admin/settings/probar", {"integracion": "hsi"},
                  docs=(p.SettingsProbeIn, p.ProbeOut), reads=settings_admin_router._lenient)

    def test_theme(self):
        self._pin("/api/admin/theme", {"tema": "humanrounds"},
                  docs=(t.ThemeIn, t.ThemeOut))

    def test_auth_mode(self):
        self._pin("/api/admin/auth-mode", {"auth_mode": "password"},
                  docs=(t.AuthModeIn, t.AuthModeOut))

    def test_turnstile(self):
        self._pin("/api/admin/turnstile", {"on": False},
                  docs=(t.TurnstileIn, t.TurnstileOut))

    def test_branding_color(self):
        self._pin("/api/admin/branding", {"color": "#336699"},
                  docs=(w.BrandingColorIn, w.BrandingColorOut))

    def test_branding_logo(self):
        self._pin("/api/admin/branding/logo", {"data_b64": "data:image/png;base64," + _png_b64()},
                  docs=(w.BrandingLogoIn, w.BrandingLogoOut))

    def test_legal(self):
        self._pin("/api/admin/legal/terminos", {"cuerpo": "Texto de prueba de los términos."},
                  method="PUT",
                  docs=(w.LegalTextIn, w.LegalTextOut))

    def test_content_create(self):
        center = _center()
        center[next(k for k in ("slug", "id") if k in center)] = "centro-de-prueba"
        self._pin("/api/admin/content/centers", center,
                  docs=(p.ContentItemIn, p.ContentItemOut))

    def test_content_edit(self):
        center = _center()
        key = next(k for k in ("slug", "id") if k in center)
        self._pin(f"/api/admin/content/centers/{center[key]}", center, method="PUT",
                  key="PUT /api/admin/content/centers/{item_id}",
                  docs=(p.ContentItemIn, p.ContentItemOut))

    def test_knowledge_properties(self):
        self._pin("/api/admin/content/knowledge/properties",
                  {"content": "La guardia atiende las 24 horas."},
                  docs=(p.KnowledgePropertiesIn, p.KnowledgeActionOut))

    def test_intake_settings(self):
        self._pin("/api/admin/intake/settings", {"sistema": True},
                  docs=(i.IntakeSettingsIn, None))

    def test_intake_my_settings(self):
        self._pin("/api/admin/intake/my-settings", {"override": True},
                  docs=(i.IntakeMySettingsIn, None))

    def test_chat_voice(self):
        self._pin("/api/admin/chat-voice", {"model": "fal-ai/wizper"},
                  docs=(voice_router.ChatVoiceIn, voice_router.ChatVoiceOut))

    def test_prompt_save(self):
        self._pin("/api/admin/prompts/chat_sistema/guardar",
                  {"texto_es": "Sos el asistente del hospital.", "texto_en": None},
                  docs=(prompts_router.PromptSaveIn, None), reads=prompts_router._BODY.decoder)


if __name__ == "__main__":
    unittest.main()
