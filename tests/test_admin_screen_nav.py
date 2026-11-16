"""tests/test_admin_screen_nav.py — the contract for core/
screen_nav.py, the ONE shared builder of the server-routed admin
screens' sidebar navigation (the refactor that replaced the six
byte-identical `_nav_items(rol)` copies the direct-screen modules each
carried).

Three properties, one per class below:

1. TestSharedNavGolden — VISIBLE NAVIGATION, FROZEN. The golden payload
   was recorded from the SIX pre-refactor builders BEFORE the
   extraction (first proven byte-identical to each other, then
   re-proven equal to the new helper head-to-head, with the old code
   pulled straight from git HEAD) — and the FULL matrix that
   head-to-head proved is embedded below: 3 enablement configs x 4
   roles x 2 languages = all 24 combinations, no subset. Every entry —
   ids, hrefs, labels, ORDER — must keep matching exactly. Contextual
   label-less screens such as consulta are deliberately absent: their
   route contract remains enabled, but an internal id must never leak into
   the rendered menu. The two-language label split and the rows that repeat
   across enablement configs are observable behavior, not noise.

2. TestEveryScreenRendersNavThroughTheSharedHelper — WIRING. A sentinel
   monkeypatched into screen_nav.nav_items must reach the served HTML
   of every direct screen (and the real nav must be gone from it), proving
   each screen's sidebar actually renders through the shared helper —
   a behavioral claim, not a source grep. This is also the guard
   against a later screen re-growing its own copy that silently
   drifts: any /admin/<screen> not wired here stops being covered, and
   the local-builder ban below makes the regression loud.

3. TestNoPrivateModuleDependency — BOUNDARY. The helper is platform-
   core only: its imports (AST-parsed, so a `modules.*` or
   sibling-screen import fails the build) resolve to `core.*` alone,
   a virgin interpreter importing it loads zero `modules.*`, and the
   consumers' one repo-internal non-core import is exactly the
   helper — no module-side or cross-module private dependency rides
   along with the dedup.

Same synthetic discipline as test_admin_screen_contract.py: no
network, no Postgres (config._db_overrides monkeypatch), no PHI — the
golden payload is catalog/manifest data only. File reads go through
_read() so the suite stays clean under the repo's canonical
`-W error::ResourceWarning` gate (an unclosed open() is a hard failure
there, not GC noise).

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_admin_screen_nav.py"
"""
import ast
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
_PREVIOUS_CONNECTOR = os.environ.get("CONNECTOR_EMR_SYNC")
_env.process_set(CONNECTOR_EMR_SYNC="hsi")
_env.process_defaults(TURNOS_ADMIN_PASS="admin-screen-nav-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
from app.core import theme  # noqa: E402
config.ENV_PATH = ""  # no depender del .env real del checkout corriendo esto

from app.auth import admin_auth  # noqa: E402
from app import main as app_module  # noqa: E402
from app.core import screen_nav  # noqa: E402
from app.core import screen_page  # noqa: E402
from _layout import full, in_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_env.process_set(CONNECTOR_EMR_SYNC=_PREVIOUS_CONNECTOR)  # None = was not set: the import is done, put it back


def setUpModule():
    _env.set_env_for_module(CONNECTOR_EMR_SYNC="hsi")  # put back by the module cleanup

# The twenty direct-screen consumer modules (twenty-two direct URLs:
# admin_content_screen serves /admin/services, /admin/centers and
# /admin/doctors). Thirteen are module-manifest screen modules; System,
# Settings, Users, Account, Legal, Appearance, and the Pinamar-only Plan are
# platform-core direct screens using
# the same shared navigation.
CONSUMERS = (
    "app.admin_panel.admin_system_screen",
    "app.admin_panel.admin_settings_screen",
    "app.admin_panel.admin_users_screen",
    "app.admin_panel.admin_account_screen",
    "app.admin_panel.admin_legal_screen",
    "app.admin_panel.admin_appearance_screen",
    "app.admin_panel.admin_plan_screen",
    "app.admin_panel.admin_hsi_screen",
    "app.admin_panel.admin_solicitudes_screen",
    "app.admin_panel.admin_cola_screen",
    "app.admin_panel.admin_mis_turnos_screen",
    "app.admin_panel.admin_doctor_patients_screen",
    "app.admin_panel.admin_pacientes_screen",
    "app.admin_panel.admin_reportes_screen",
    "app.admin_panel.admin_preparations_screen",
    "app.admin_panel.admin_content_screen",
    "app.admin_panel.admin_knowledge_screen",
    "app.admin_panel.admin_prompts_screen",
    "app.modules.intake.admin_entrevistas_screen",
    "app.modules.intake.admin_consult_screen",
    "app.modules.chat.admin_chats_screen",
)
CONSUMER_PATHS = {
    dotted: os.path.join(ROOT, *dotted.split(".")) + ".py" for dotted in CONSUMERS
}


def _read(path):
    """The only way this suite reads a file: closed on exit, so
    -W error::ResourceWarning never sees an unclosed handle."""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# "<enabled>|<rol>|<lang>" -> visible (id, href, label) rows in the stable
# operational order. The matrix pins all role/module/language combinations;
# the helper below also pins each row's visual group metadata.
GOLDEN = {
    # --- ENABLED_MODULES='turnos,intake' (everything on) ---
    # «Mis pacientes» is the doctor's list; the admin opens it by direct link
    # but has Pacientes in the menu (pinamar-turnos 2026-11-03).
    "turnos,intake|admin|es": (
        ('solicitudes', '/admin/solicitudes', 'Solicitudes'),
        ('cola', '/admin/cola', 'Pacientes de hoy'),
        ('pacientes', '/admin/pacientes', 'Pacientes'),
        ('medicos', '/admin/doctors', 'Médicos'),
        ('servicios', '/admin/services', 'Servicios'),
        ('centros', '/admin/centers', 'Centros'),
        ('preparaciones', '/admin/preparations', 'Cómo prepararse'),
        ('knowledge', '/admin/knowledge', 'Wiki'),
        ('prompts', '/admin/prompts', 'Instrucciones de la IA'),
        ('reportes', '/admin/reportes', 'Reportes'),
        ('plan', '/admin/plan', 'Hoja de ruta'),
        ('configuracion', '/admin/appearance','Apariencia y acceso'),
        ('settings', '/admin/settings', 'Datos e idiomas'),
        ('legal', '/admin/legal', 'Términos y privacidad'),
        ('entrevistas', '/admin/entrevistas', 'Captura de síntomas'),
        ('sistema', '/admin/system', 'Estado'),
        ('hsi', '/admin/hsi', 'HSI'),
        ('usuarios', '/admin/users', 'Usuarios'),
        ('roles', '/admin/roles', 'Roles y permisos'),
        ('ai-register', '/admin/ai-register', 'Registro de IA'),
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "turnos,intake|admin|en": (
        ('solicitudes', '/admin/solicitudes', 'Requests'),
        ('cola', '/admin/cola', "Today's patients"),
        ('pacientes', '/admin/pacientes', 'Patients'),
        ('medicos', '/admin/doctors', 'Doctors'),
        ('servicios', '/admin/services', 'Services'),
        ('centros', '/admin/centers', 'Centers'),
        ('preparaciones', '/admin/preparations', 'How to prepare'),
        ('knowledge', '/admin/knowledge', 'Wiki'),
        ('prompts', '/admin/prompts', 'AI instructions'),
        ('reportes', '/admin/reportes', 'Reports'),
        ('plan', '/admin/plan', 'Roadmap'),
        ('configuracion', '/admin/appearance','Appearance and access'),
        ('settings', '/admin/settings', 'Data and languages'),
        ('legal', '/admin/legal', 'Terms and privacy'),
        ('entrevistas', '/admin/entrevistas', 'Symptom intake'),
        ('sistema', '/admin/system', 'Status'),
        ('hsi', '/admin/hsi', 'HSI'),
        ('usuarios', '/admin/users', 'Users'),
        ('roles', '/admin/roles', 'Roles and permissions'),
        ('ai-register', '/admin/ai-register', 'AI register'),
        ('cuenta', '/admin/account', 'Account'),
    ),
    # consulta/entrevistas are admin-gated intake sections: the three
    # non-admin roles below see NO intake entry at all, so these rows
    # equal their 'turnos'-only counterparts further down.
    "turnos,intake|medico|es": (
        ('cola', '/admin/cola', 'Pacientes de hoy'),
        ('mis-turnos', '/admin/mis-turnos', 'Mis turnos'),
        ('mis-pacientes', '/admin/my-patients', 'Mis pacientes'),
        ('my-schedules', '/admin/my-schedules', 'Mis agendas'),
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "turnos,intake|medico|en": (
        ('cola', '/admin/cola', "Today's patients"),
        ('mis-turnos', '/admin/mis-turnos', 'My appointments'),
        ('mis-pacientes', '/admin/my-patients', 'My patients'),
        ('my-schedules', '/admin/my-schedules', 'My schedules'),
        ('cuenta', '/admin/account', 'Account'),
    ),
    "turnos,intake|admisionista|es": (
        ('solicitudes', '/admin/solicitudes', 'Solicitudes'),
        ('cola', '/admin/cola', 'Pacientes de hoy'),
        ('pacientes', '/admin/pacientes', 'Pacientes'),
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "turnos,intake|admisionista|en": (
        ('solicitudes', '/admin/solicitudes', 'Requests'),
        ('cola', '/admin/cola', "Today's patients"),
        ('pacientes', '/admin/pacientes', 'Patients'),
        ('cuenta', '/admin/account', 'Account'),
    ),
    "turnos,intake|reportes|es": (
        ('reportes', '/admin/reportes', 'Reportes'),
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "turnos,intake|reportes|en": (
        ('reportes', '/admin/reportes', 'Reports'),
        ('cuenta', '/admin/account', 'Account'),
    ),
    # --- ENABLED_MODULES='turnos' (intake off: its sections drop,
    # and its /admin/entrevistas href goes with them) ---
    "turnos|admin|es": (
        ('solicitudes', '/admin/solicitudes', 'Solicitudes'),
        ('cola', '/admin/cola', 'Pacientes de hoy'),
        ('pacientes', '/admin/pacientes', 'Pacientes'),
        ('medicos', '/admin/doctors', 'Médicos'),
        ('servicios', '/admin/services', 'Servicios'),
        ('centros', '/admin/centers', 'Centros'),
        ('preparaciones', '/admin/preparations', 'Cómo prepararse'),
        ('knowledge', '/admin/knowledge', 'Wiki'),
        ('prompts', '/admin/prompts', 'Instrucciones de la IA'),
        ('reportes', '/admin/reportes', 'Reportes'),
        ('plan', '/admin/plan', 'Hoja de ruta'),
        ('configuracion', '/admin/appearance','Apariencia y acceso'),
        ('settings', '/admin/settings', 'Datos e idiomas'),
        ('legal', '/admin/legal', 'Términos y privacidad'),
        ('sistema', '/admin/system', 'Estado'),
        ('hsi', '/admin/hsi', 'HSI'),
        ('usuarios', '/admin/users', 'Usuarios'),
        ('roles', '/admin/roles', 'Roles y permisos'),
        ('ai-register', '/admin/ai-register', 'Registro de IA'),
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "turnos|admin|en": (
        ('solicitudes', '/admin/solicitudes', 'Requests'),
        ('cola', '/admin/cola', "Today's patients"),
        ('pacientes', '/admin/pacientes', 'Patients'),
        ('medicos', '/admin/doctors', 'Doctors'),
        ('servicios', '/admin/services', 'Services'),
        ('centros', '/admin/centers', 'Centers'),
        ('preparaciones', '/admin/preparations', 'How to prepare'),
        ('knowledge', '/admin/knowledge', 'Wiki'),
        ('prompts', '/admin/prompts', 'AI instructions'),
        ('reportes', '/admin/reportes', 'Reports'),
        ('plan', '/admin/plan', 'Roadmap'),
        ('configuracion', '/admin/appearance','Appearance and access'),
        ('settings', '/admin/settings', 'Data and languages'),
        ('legal', '/admin/legal', 'Terms and privacy'),
        ('sistema', '/admin/system', 'Status'),
        ('hsi', '/admin/hsi', 'HSI'),
        ('usuarios', '/admin/users', 'Users'),
        ('roles', '/admin/roles', 'Roles and permissions'),
        ('ai-register', '/admin/ai-register', 'AI register'),
        ('cuenta', '/admin/account', 'Account'),
    ),
    "turnos|medico|es": (
        ('cola', '/admin/cola', 'Pacientes de hoy'),
        ('mis-turnos', '/admin/mis-turnos', 'Mis turnos'),
        ('mis-pacientes', '/admin/my-patients', 'Mis pacientes'),
        ('my-schedules', '/admin/my-schedules', 'Mis agendas'),
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "turnos|medico|en": (
        ('cola', '/admin/cola', "Today's patients"),
        ('mis-turnos', '/admin/mis-turnos', 'My appointments'),
        ('mis-pacientes', '/admin/my-patients', 'My patients'),
        ('my-schedules', '/admin/my-schedules', 'My schedules'),
        ('cuenta', '/admin/account', 'Account'),
    ),
    "turnos|admisionista|es": (
        ('solicitudes', '/admin/solicitudes', 'Solicitudes'),
        ('cola', '/admin/cola', 'Pacientes de hoy'),
        ('pacientes', '/admin/pacientes', 'Pacientes'),
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "turnos|admisionista|en": (
        ('solicitudes', '/admin/solicitudes', 'Requests'),
        ('cola', '/admin/cola', "Today's patients"),
        ('pacientes', '/admin/pacientes', 'Patients'),
        ('cuenta', '/admin/account', 'Account'),
    ),
    "turnos|reportes|es": (
        ('reportes', '/admin/reportes', 'Reportes'),
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "turnos|reportes|en": (
        ('reportes', '/admin/reportes', 'Reports'),
        ('cuenta', '/admin/account', 'Account'),
    ),
    # --- ENABLED_MODULES='intake' (turnos off: every non-admin role
    # is left exactly [cuenta] — the only section left for them is a
    # platform one) ---
    "intake|admin|es": (
        ('plan', '/admin/plan', 'Hoja de ruta'),
        ('configuracion', '/admin/appearance','Apariencia y acceso'),
        ('settings', '/admin/settings', 'Datos e idiomas'),
        ('legal', '/admin/legal', 'Términos y privacidad'),
        ('entrevistas', '/admin/entrevistas', 'Captura de síntomas'),
        ('sistema', '/admin/system', 'Estado'),
        ('usuarios', '/admin/users', 'Usuarios'),
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "intake|admin|en": (
        ('plan', '/admin/plan', 'Roadmap'),
        ('configuracion', '/admin/appearance','Appearance and access'),
        ('settings', '/admin/settings', 'Data and languages'),
        ('legal', '/admin/legal', 'Terms and privacy'),
        ('entrevistas', '/admin/entrevistas', 'Symptom intake'),
        ('sistema', '/admin/system', 'Status'),
        ('usuarios', '/admin/users', 'Users'),
        ('cuenta', '/admin/account', 'Account'),
    ),
    "intake|medico|es": (
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "intake|medico|en": (
        ('cuenta', '/admin/account', 'Account'),
    ),
    "intake|admisionista|es": (
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "intake|admisionista|en": (
        ('cuenta', '/admin/account', 'Account'),
    ),
    "intake|reportes|es": (
        ('cuenta', '/admin/account', 'Cuenta'),
    ),
    "intake|reportes|en": (
        ('cuenta', '/admin/account', 'Account'),
    ),
}


_EXPECTED_GROUPS = {
    "care": {"solicitudes", "cola", "mis-turnos", "mis-pacientes", "my-schedules", "pacientes"},
    "organization": {"medicos", "servicios", "centros", "preparaciones"},
    "chat": {"knowledge", "prompts", "chats"},
    "system": {"reportes", "plan", "configuracion", "settings", "legal", "entrevistas", "sistema", "hsi", "usuarios", "roles", "ai-register"},
    "account": {"cuenta"},
}
_EXPECTED_GROUP_LABELS = {
    "es": {"care": "Atención", "organization": "Organización", "chat": "Chat", "system": "Sistema", "account": None},
    "en": {"care": "Care", "organization": "Organization", "chat": "Chat", "system": "System", "account": None},
}


def _golden_dicts(rows, lang):
    group_for_id = {
        section_id: group
        for group, section_ids in _EXPECTED_GROUPS.items()
        for section_id in section_ids
    }
    return [
        {
            "id": section_id,
            "href": href,
            "label": label,
            "group": group_for_id[section_id],
            "group_label": _EXPECTED_GROUP_LABELS[lang][group_for_id[section_id]],
        }
        for section_id, href, label in rows
    ]


def _enabled_setup(test_case):
    """Same config._db_overrides monkeypatch test_admin_screen_contract.py
    uses — lets ENABLED_MODULES flip mid-test with no Postgres reachable."""
    _env.set_env(test_case, DATABASE_URL="", TURNOS_ENV_FILE="/dev/null")
    config._reset_cache_para_tests()
    store = {}

    def fake_overrides():
        return dict(store)

    patcher = patch.object(config, "_db_overrides", side_effect=fake_overrides)
    patcher.start()
    test_case.addCleanup(patcher.stop)
    test_case.addCleanup(config._reset_cache_para_tests)

    def _set_enabled(raw):
        store["ENABLED_MODULES"] = raw
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0

    return _set_enabled


def _admin_cookies(rol):
    return {"st_admin": admin_auth._mint_token("screen-nav-contract-test", rol)}


class TestSharedNavGolden(unittest.TestCase):
    """The shared helper's output IS the six pre-refactor builders'
    output — same entries, same labels, same order, for every recorded
    role/language/enablement combination."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._theme_cache = dict(theme._cache)
        theme._cache.update({"v": "pinamar", "t": float("inf")})
        self.addCleanup(theme._cache.update, self._theme_cache)

    def test_every_recorded_combination_matches_the_frozen_golden(self):
        from app.core import i18n
        for key, rows in GOLDEN.items():
            enabled, rol, lang = key.split("|")
            self._set_enabled(enabled)
            _lang, token = i18n.bind_lang(lang)
            try:
                got = screen_nav.nav_items(rol)
            finally:
                i18n.unbind(token)
            self.assertEqual(got, _golden_dicts(rows, lang), f"{key} drifted")


class TestEveryScreenRendersNavThroughTheSharedHelper(unittest.TestCase):
    """Every direct screen's sidebar is built by
    screen_nav.nav_items — proven by a sentinel, not by reading source.
    The (path, rol) pairs each use a role that screen's own gate admits
    (mis-turnos needs medico — admin 403s by design; reportes uses the
    'reportes' role for the same reason)."""

    SCREENS = (
        ("/admin/system", "admin"),
        ("/admin/settings", "admin"),
        ("/admin/users", "admin"),
        # Account is universal to authenticated staff: a non-admin role
        # proves that branch of its gate renders the same shared nav.
        ("/admin/account", "admisionista"),
        ("/admin/appearance", "admin"),
        ("/admin/plan", "admin"),
        ("/admin/hsi", "admin"),
        ("/admin/solicitudes", "admin"),
        ("/admin/cola", "admin"),
        ("/admin/mis-turnos", "medico"),
        ("/admin/my-patients", "admin"),
        ("/admin/pacientes", "admisionista"),
        ("/admin/reportes", "reportes"),
        ("/admin/entrevistas", "admin"),
        ("/admin/preparations", "admin"),
        ("/admin/services", "admin"),
        ("/admin/centers", "admin"),
        ("/admin/doctors", "admin"),
        ("/admin/knowledge", "admin"),
        ("/admin/prompts", "admin"),
        ("/admin/consult", "medico"),
    )

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos,intake")
        self._theme_cache = dict(theme._cache)
        theme._cache.update({"v": "pinamar", "t": float("inf")})
        self.addCleanup(theme._cache.update, self._theme_cache)
        self.client = TestClient(app_module.app)

    def test_sentinel_nav_reaches_every_screens_html(self):
        sentinel = [{"id": "zz-nav-proof", "href": "/admin/zz-nav-proof",
                     "label": "ZZ nav proof"}]
        with patch.object(screen_nav, "nav_items", return_value=sentinel):
            for path, rol in self.SCREENS:
                with self.subTest(screen=path, rol=rol):
                    r = self.client.get(path, cookies=_admin_cookies(rol))
                    self.assertEqual(r.status_code, 200, r.text[:300])
                    # base.html emits data-nav= ONLY in its sidebar loop,
                    # so the sentinel appearing there IS the nav; every
                    # real nav starts with cuenta or configuracion, so its
                    # absence proves the whole list came from the patch,
                    # not the catalog.
                    self.assertIn('data-nav="zz-nav-proof"', r.text)
                    self.assertNotIn('data-nav="cuenta"', r.text)
                    self.assertIn(">ZZ nav proof</a>", r.text)

    def test_no_consumer_keeps_a_local_nav_builder(self):
        """The dedup itself is pinned: none of the consumers may re-grow an own nav builder, and each
        must reach the shared one — since 2026-10-28 through `core.screen_page`, which is the only
        caller of `screen_nav.nav_items` in the screens (pinned below)."""
        for dotted, path in CONSUMER_PATHS.items():
            with self.subTest(consumer=dotted):
                src = _read(path)
                tree = ast.parse(src)
                nav_defs = [
                    n.name for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)
                    and ("nav_items" in n.name or n.name.endswith("_nav"))
                ]
                self.assertEqual(
                    nav_defs, [],
                    f"{dotted} defines its own nav builder ({nav_defs}) — "
                    "the shared helper is core/screen_nav.nav_items")
                self.assertTrue(
                    "screen_page.admin_page(" in src or "screen_page.admin_html(" in src,
                    f"{dotted} no longer renders its page through core.screen_page")
        self.assertIn(
            "screen_nav.nav_items(", _read(os.path.join(ROOT, "app", "core", "screen_page.py")),
            "core/screen_page.py no longer renders the sidebar through the shared helper")


class TestNoPrivateModuleDependency(unittest.TestCase):
    """The helper stays platform-core: no modules.* import, no
    admin_panel sibling-screen import, nothing outside core — checked
    on the AST (a new forbidden import fails THIS build) and on a
    virgin interpreter (importing it loads zero modules.*). Consumers bind
    that helper directly; their own feature dependencies remain independent
    of this navigation boundary."""

    HELPER = os.path.join(ROOT, "app", "core", "screen_nav.py")

    def test_retired_helper_has_no_file_or_import_alias(self):
        import importlib.util

        self.assertFalse(os.path.exists(os.path.join(ROOT, "app", "admin_panel", "screen_nav.py")))
        self.assertIsNone(importlib.util.find_spec("app.admin_panel.screen_nav"))
        self.assertEqual(screen_nav.__name__, "app.core.screen_nav")
        self.assertEqual(os.path.realpath(screen_nav.__file__), os.path.realpath(self.HELPER))

    def test_helper_imports_platform_core_only(self):
        tree = ast.parse(_read(self.HELPER))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(in_app(alias.name).split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(in_app(node.module).split(".")[0])
        self.assertEqual(
            roots, {"core"},
            "core/screen_nav.py must import core.* only — a shared "
            "screen helper that grows a modules.* or sibling dependency "
            "makes every consumer inherit it")

    def test_virgin_import_loads_no_private_module(self):
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": "",
            "TURNOS_FAKE_AI": "1",
            "TURNOS_SKIP_HSI": "1",
            "TURNOS_ENV_FILE": "",
        })
        code = (
            "import sys; sys.path.insert(0, {root!r}); "
            "import app.core.screen_nav; "
            "leaked = [n for n in sys.modules "
            "if n == {mods!r} or n.startswith({mods!r} + '.')]; "
            "print('LEAN' if not leaked else 'LEAKED:' + ','.join(leaked))"
        ).format(root=ROOT, mods=full("modules"))
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            env=env, cwd=ROOT, timeout=60,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"importing the helper failed in a virgin interpreter: {proc.stderr}")
        self.assertEqual(
            proc.stdout.strip(), "LEAN",
            "importing core.screen_nav pulled private module code in")

    def test_consumers_bind_the_helper_without_importing_sibling_screens(self):
        import importlib
        for dotted, path in CONSUMER_PATHS.items():
            with self.subTest(consumer=dotted):
                tree = ast.parse(_read(path))
                helper_imports = []
                sibling_imports = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and in_app(node.module) in {"core", "admin_panel"}:
                        names = {alias.name for alias in node.names}
                        if in_app(node.module) == "core" and "screen_page" in names:
                            helper_imports.append(node)
                        if in_app(node.module) == "admin_panel":
                            sibling_imports.extend(
                                name for name in names
                                if name.endswith("_screen") or name == "screen_nav"
                            )
                self.assertEqual(
                    len(helper_imports), 1,
                    f"{dotted} must import app.core.screen_page exactly once")
                self.assertEqual(
                    sibling_imports, [],
                    f"{dotted} imports sibling screen modules: {sibling_imports}")
                mod = importlib.import_module(dotted)
                self.assertIs(
                    mod.screen_page, screen_page,
                    f"{dotted} must bind the shared page module (screen_page)")

    def test_the_page_module_binds_the_nav_helper_once(self):
        """`core.screen_page` is the one place the screens' sidebar is asked for."""
        tree = ast.parse(_read(os.path.join(ROOT, "app", "core", "screen_page.py")))
        bound = [alias.name for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and in_app(node.module) == "core" for alias in node.names]
        self.assertEqual(bound.count("screen_nav"), 1)
        self.assertIs(screen_page.screen_nav, screen_nav)


if __name__ == "__main__":
    unittest.main()
