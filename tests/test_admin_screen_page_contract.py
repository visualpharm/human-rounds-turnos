"""test_admin_screen_page_contract.py - every server-routed admin screen is put together the same way (2026-10-28).

Second pass of the architecture audit (section 6): twenty-odd `*_screen.py` repeat the same hundred nodes -
render the template with the nav and the active section, tag the language, add the client catalogs, run the
theme, answer `text/html` with `Cache-Control: no-store`. They differ in what they add to the template
context, in the extra catalog namespaces, in who may open them and in a few early exits (a hidden section is a
404, a role is a 403...). Before sharing the assembly, this pins what every screen does for every role:

  - the status, the content type, the cache header and the redirect target;
  - which template is rendered, with which context names (a scalar by value, a container by shape),
    which language and which catalog namespaces are injected, and in which order the steps happen.

The template, the language tagging, the catalogs and the theme are replaced by markers, so the answer depends
on how the screen is assembled and on nothing in the templates, the locales or the static files.
The recorded file is written with the code that exists today and is not touched by the change that shares
the assembly: `python3 tests/test_admin_screen_page_contract.py --write`.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_admin_screen_page_contract.py
"""
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1",
                      TURNOS_ADMIN_PASS="admin-screen-page-contract-fixture-pass")

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.admin_panel import doctor_workspace  # noqa: E402
from app.core import ai_register, db, i18n, jinja_env, permissions, screen_nav, theme  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.modules.turnos import doctor_appointments  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden", "admin_screen_pages.json")
_REAL_NAV_ITEMS = screen_nav.nav_items

PATHS = [
    "/admin/account", "/admin/ai-register", "/admin/ai-register/print", "/admin/appearance", "/admin/centers",
    "/admin/chats", "/admin/cola", "/admin/consult", "/admin/doctors", "/admin/entrevistas", "/admin/hsi",
    "/admin/knowledge", "/admin/legal", "/admin/mis-turnos", "/admin/my-patients", "/admin/my-schedules",
    "/admin/onboarding", "/admin/pacientes", "/admin/plan", "/admin/preparations", "/admin/prompts",
    "/admin/reportes", "/admin/roles", "/admin/services", "/admin/settings", "/admin/solicitudes",
    "/admin/system", "/admin/users",
]
ROLES = ("admin", "medico", "admisionista", "reportes")
USER = "screen-page-contract"


def _summary(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        # a small dict by its scalar values too (the day of a queue is the clock's, so it is not recorded)
        scalars = {str(k): v for k, v in value.items()
                   if k != "dia" and (v is None or isinstance(v, (str, int, float, bool)))}
        return {"keys": sorted(str(k) for k in value), "scalars": scalars}
    if isinstance(value, (list, tuple)):
        return {"len": len(value)}
    return "<%s>" % type(value).__name__


class _Steps:
    """Stand-ins for the four collaborators of the assembly; they write down what they were asked."""

    def __init__(self):
        self.calls = []

    def render(self, template, **context):
        self.calls.append(["render", template, {k: "<nav>" if k == "nav_items" else _summary(v)
                                                for k, v in sorted(context.items())}])
        return "<!doctype html><html><head></head><body>%s</body></html>" % template

    def inject_html_lang(self, html, lang):
        self.calls.append(["html_lang", lang])
        return html + "<!--lang:%s-->" % lang

    def inject_bootstrap(self, html, request, lang, namespaces=None):
        self.calls.append(["bootstrap", request, lang, list(namespaces) if namespaces is not None else None])
        return html + "<!--ns:%s-->" % ",".join(namespaces or [])

    def theme_inject(self, html):
        self.calls.append(["theme"])
        return html + "<!--theme-->"

    def nav_items(self, role=None, *args, **kwargs):
        # the navigation middleware asks too (with `permissions=`); only the screens' own call is a step
        if not args and not kwargs:
            self.calls.append(["nav", role])
        return _REAL_NAV_ITEMS(role, *args, **kwargs)


def _one(client, path, role, theme_name, hsi_section=False):
    steps = _Steps()
    patches = [
        mock.patch.object(jinja_env, "render", steps.render),
        mock.patch.object(i18n, "inject_html_lang", steps.inject_html_lang),
        mock.patch.object(i18n, "inject_bootstrap", steps.inject_bootstrap),
        mock.patch.object(i18n, "active", lambda: "es"),
        mock.patch.object(i18n, "client_namespaces", lambda: ("common", "nav")),
        mock.patch.object(theme, "inject", steps.theme_inject),
        mock.patch.object(theme, "current", lambda: theme_name),
        mock.patch.object(screen_nav, "nav_items", steps.nav_items),
        # no database, whether or not the machine has one: the store answers «down» in every place a screen asks
        mock.patch.object(permissions, "access_for", side_effect=db.DbUnavailable),
        mock.patch.object(doctor_appointments, "day_queue_data", side_effect=db.DbUnavailable),
        mock.patch.object(doctor_workspace, "workspace_data", side_effect=db.DbUnavailable),
        mock.patch.object(db_chat, "chat_conversations", side_effect=db.DbUnavailable),
        # one real register row (the screen localizes it), without the runtime probes that read config and modules
        mock.patch.object(ai_register, "dossier", lambda: {
            "caracter\u00edsticas": [dict(ai_register.FUNCTIONS[0], retention_risk="No verificado",
                                          runtime_state="deshabilitada")],
            "modificaciones": [], "estado_actual": {"k": "v"}}),
    ]
    if hsi_section:  # the HSI section is hidden unless the EMR sync is on; once as it is, once forced visible
        patches.append(mock.patch.object(screen_nav, "has_section", lambda *a, **k: True))
    for p in patches:
        p.start()
    try:
        response = client.get(path, cookies={"st_admin": admin_auth._mint_token(USER, role)}, follow_redirects=False)
    finally:
        for p in reversed(patches):
            p.stop()
    text = response.text
    return {
        "status": response.status_code,
        "content-type": response.headers.get("content-type"),
        "cache-control": response.headers.get("cache-control"),
        "location": response.headers.get("location"),
        "body": text if response.status_code == 200 else text[:200],
        "steps": steps.calls,
    }


def _record():
    config._reset_cache_para_tests()
    client = TestClient(app_module.app)
    out = {}
    for path in PATHS:
        for role in ROLES:
            for theme_name in ("default", "pinamar"):
                out["%s|%s|%s" % (path, role, theme_name)] = _one(client, path, role, theme_name)
                if path == "/admin/hsi":
                    out["%s|%s|%s|section-visible" % (path, role, theme_name)] = _one(
                        client, path, role, theme_name, hsi_section=True)
    config._reset_cache_para_tests()
    return json.loads(json.dumps(out, sort_keys=True))


def _write():
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, indent=1)
        fh.write("\n")


class AdminScreenPageContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as fh:
            cls.want = json.load(fh)
        cls.got = _record()

    def test_every_case_is_recorded(self):
        self.assertEqual(sorted(self.got), sorted(self.want))

    def test_what_each_screen_answers_and_how_it_is_assembled(self):
        for key in sorted(self.want):
            self.assertEqual(self.got[key], self.want[key], key)

    def test_the_recording_reaches_the_assembly(self):
        """Guards the guard: if the routes moved or the stand-ins stopped being called, the file would go
        on matching an empty record."""
        rendered = [k for k, v in self.want.items() if v["status"] == 200]
        self.assertGreaterEqual(len(rendered), 40)
        for key in rendered:
            steps = [s[0] for s in self.want[key]["steps"]]
            self.assertIn("render", steps, key)
            self.assertIn("theme", steps, key)
            self.assertEqual(self.want[key]["cache-control"], "no-store", key)
            self.assertTrue(self.want[key]["content-type"].startswith("text/html"), key)


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("grabado", GOLDEN)
    else:
        unittest.main()
