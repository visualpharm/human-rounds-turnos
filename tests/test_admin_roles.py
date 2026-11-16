"""Matriz de permisos de los cuatro roles internos.

El gate central decide en UN solo lugar quién pasa. Antes NADA ejercitaba el
rechazo cruzado — una sesión 'medico' contra un endpoint solo_admin, o un
'operador' contra reportes/usuarios/contenido. Estos casos son exactamente los
403 que separan lo que ve el personal administrativo de lo que ve la Secretaría
y de lo que ve un médico (que sólo mira sus propios turnos).
"""
import io
import base64
import hashlib
import hmac
import json
import os
import sys
import unittest
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import admin_auth  # noqa: E402
from app.core import config  # noqa: E402
from app.ops import demo_installation  # noqa: E402
from app.core import permissions as perm  # noqa: E402
import _access_stub  # noqa: E402, F401
import _env  # noqa: E402

_AUTH_ENV = {
    "TURNOS_ADMIN_SECRET": "test-only-admin-role-matrix-signing-key",
    "TURNOS_CONFIG_LOCK": "1",
}
_stub_context = None


def setUpModule():
    """Keep this module's auth fixture out of sibling test modules."""
    global _stub_context
    unittest.addModuleCleanup(config._reset_cache_para_tests)  # registered first: runs once the env is back
    _env.set_env_for_module(_AUTH_ENV)
    config._reset_cache_para_tests()
    # Patch access_for with the stub for DB-less testing
    _stub_context = _access_stub.patch_access_for_with_stub()
    _stub_context.__enter__()


def tearDownModule():
    global _stub_context
    # Restore the original access_for
    if _stub_context:
        _stub_context.__exit__()


class FakeHandler:
    def __init__(self, cookie=None, path="", command="GET"):
        self.headers = {"Cookie": "st_admin=" + cookie} if cookie else {}
        self.wfile = io.BytesIO()
        self.sent_headers = {}
        self.status = None
        self.client_address = ("127.0.0.1", 0)
        self.path = path
        self.command = command

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.sent_headers[k] = v

    def end_headers(self):
        pass


def _h(rol):
    """Handler con una sesión válida de ese rol."""
    return FakeHandler(cookie=admin_auth._mint_token("u_" + rol, rol))


def _legacy_h(rol):
    """Adversarial fixture for the retired hand-written cookie format."""
    payload = json.dumps(
        {"v": admin_auth.TOKEN_VERSION, "u": "u_" + rol, "r": rol,
         "exp": int(time.time()) + 3600},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    signature = hmac.new(
        admin_auth._signing_key(), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return FakeHandler(cookie=f"{encoded}.{signature}")


def _require_admin(request, **flags):
    """Exercise the value-only gate while preserving this file's assertions."""
    gate = admin_auth.evaluate_admin_gate(
        admin_auth._parse_cookie_token(request.headers.get("Cookie")),
        request.command,
        request.path,
        **flags,
    )
    if gate.ok:
        return gate.identity
    request.status = gate.status
    request.wfile.write(json.dumps(gate.body).encode("utf-8"))
    return None


class TestSinSesion(unittest.TestCase):
    def test_sin_cookie_es_401(self):
        h = FakeHandler()
        self.assertIsNone(_require_admin(h))
        self.assertEqual(h.status, 401)

    def test_token_basura_es_401(self):
        h = FakeHandler(cookie="no.es.un.token")
        self.assertIsNone(_require_admin(h))
        self.assertEqual(h.status, 401)


class TestRoleSecurityMatrix(unittest.TestCase):
    """Pin the complete maintained-cookie role matrix and retired format."""

    def test_current_cookie_matrix(self):
        cases = (
            ("admin", {}, True, None),
            ("medico", {}, False, 403),
            ("medico", {"permitir_medico": True}, True, None),
            ("admisionista", {}, False, 403),
            ("admisionista", {"permitir_admisionista": True}, True, None),
            ("reportes", {}, False, 403),
            ("reportes", {"solo_admin": True, "permitir_reportes": True}, True, None),
            ("reportes", {"permitir_reportes": True}, True, None),
        )
        for rol, flags, allowed, status in cases:
            with self.subTest(rol=rol, flags=flags):
                handler = _h(rol)
                identity = _require_admin(handler, **flags)
                self.assertEqual(identity is not None, allowed)
                self.assertEqual(handler.status, status)

    def test_retired_cookie_is_401_for_every_live_role(self):
        for rol in ("admin", "medico", "admisionista", "reportes"):
            with self.subTest(rol=rol):
                handler = _legacy_h(rol)
                self.assertIsNone(
                    _require_admin(
                        handler,
                        solo_admin=True,
                        permitir_medico=True,
                        permitir_admisionista=True,
                        permitir_reportes=True,
                    )
                )
                self.assertEqual(handler.status, 401)


class TestPermisivo(unittest.TestCase):
    """require_admin() sin flags: sólo admin pasa."""

    def test_admin_pasa(self):
        h = _h("admin")
        ident = _require_admin(h)
        self.assertEqual(ident.get("rol"), "admin")
        self.assertIsNone(h.status)

    def test_admisionista_rechazado_por_default(self):
        h = _h("admisionista")
        self.assertIsNone(_require_admin(h))
        self.assertEqual(h.status, 403)

    def test_admisionista_pasa_cuando_el_endpoint_lo_declara(self):
        h = _h("admisionista")
        ident = _require_admin(h, permitir_admisionista=True)
        self.assertEqual(ident.get("rol"), "admisionista")

    def test_medico_rechazado_por_default(self):
        h = _h("medico")
        self.assertIsNone(_require_admin(h))
        self.assertEqual(h.status, 403)

    def test_medico_pasa_con_permitir_medico(self):
        h = _h("medico")
        ident = _require_admin(h, permitir_medico=True)
        self.assertEqual(ident.get("rol"), "medico")
        self.assertIsNone(h.status)


class TestSoloAdmin(unittest.TestCase):
    """solo_admin=True (usuarios / reportes / contenido-POST / centros-PUT):
    sólo el rol 'admin'."""

    def test_admin_pasa(self):
        h = _h("admin")
        self.assertIsNotNone(_require_admin(h, solo_admin=True))
        self.assertIsNone(h.status)

    def test_admisionista_es_403(self):
        h = _h("admisionista")
        self.assertIsNone(_require_admin(h, solo_admin=True))
        self.assertEqual(h.status, 403)

    def test_medico_es_403(self):
        h = _h("medico")
        self.assertIsNone(_require_admin(h, solo_admin=True))
        self.assertEqual(h.status, 403)

    def test_atajo_require_rol_admin_rechaza_admisionista(self):
        h = _h("admisionista")
        self.assertIsNone(_require_admin(h, solo_admin=True))
        self.assertEqual(h.status, 403)


class TestRolReportes(unittest.TestCase):
    """'reportes' (reunión hospital 2026-07-24): estadísticas y nada más. Es el
    único rol que atraviesa solo_admin, y sólo donde se lo habilita."""

    def test_entra_a_reportes_aunque_sean_solo_admin(self):
        h = _h("reportes")
        ident = _require_admin(h, solo_admin=True, permitir_reportes=True)
        self.assertEqual(ident.get("rol"), "reportes")
        self.assertIsNone(h.status)

    def test_no_entra_a_ningun_otro_endpoint_solo_admin(self):
        # usuarios, alta/baja de catálogo, centros: 403.
        h = _h("reportes")
        self.assertIsNone(_require_admin(h, solo_admin=True))
        self.assertEqual(h.status, 403)

    def test_tampoco_entra_a_los_permisivos(self):
        # La cola de solicitudes y el catálogo son permisivos (admin+operador):
        # 'reportes' NO ve pacientes, así que también le responden 403.
        h = _h("reportes")
        self.assertIsNone(_require_admin(h))
        self.assertEqual(h.status, 403)

    def test_ni_siquiera_donde_pasan_medico_o_admisionista(self):
        for kwargs in ({"permitir_medico": True}, {"permitir_admisionista": True}):
            h = _h("reportes")
            self.assertIsNone(_require_admin(h, **kwargs), kwargs)
            self.assertEqual(h.status, 403, kwargs)

    def test_puede_resolver_su_identidad(self):
        # whoami: sin esto el panel no arranca para esa sesión.
        h = _h("reportes")
        ident = _require_admin(h, permitir_medico=True,
                                         permitir_admisionista=True,
                                         permitir_reportes=True)
        self.assertEqual(ident.get("rol"), "reportes")

    def test_los_otros_roles_no_se_cuelan_por_la_puerta_de_reportes(self):
        for rol in ("medico", "admisionista"):
            h = _h(rol)
            self.assertIsNone(
                _require_admin(h, solo_admin=True, permitir_reportes=True), rol)
            self.assertEqual(h.status, 403, rol)


class TestRolesRetirados(unittest.TestCase):
    def test_operador_y_marca_invalidan_la_sesion(self):
        for rol in ("operador", "marca"):
            h = _h(rol)
            self.assertIsNone(_require_admin(h))
            self.assertEqual(h.status, 401)


class TestDemoReadOnly(unittest.TestCase):
    """La sesión demo (público, /admin/demo-access) mintea rol="admin" a
    propósito — ve TODO. El único gate real es acá: toda mutación (POST/PUT/
    DELETE) que no sea logout responde 403 demo_solo_lectura, sin importar
    qué endpoint sea (ver admin_auth.require_admin, gate al final)."""

    def setUp(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1")

    def _demo_handler(self, command="GET", path="/api/admin/requests"):
        cookie = admin_auth._mint_token(demo_installation.demo_user(), "admin")
        return FakeHandler(cookie=cookie, path=path, command=command)

    def test_get_pasa_los_gates_solo_admin(self):
        h = self._demo_handler(command="GET", path="/api/admin/users")
        ident = _require_admin(h, solo_admin=True)
        self.assertEqual(ident, {"usuario": demo_installation.demo_user(), "rol": "admin"})
        self.assertIsNone(h.status)

    def test_post_es_403_demo_solo_lectura(self):
        for method in ("POST", "PUT", "DELETE"):
            with self.subTest(method=method):
                h = self._demo_handler(command=method, path="/api/admin/requests/1/approve")
                self.assertIsNone(_require_admin(h))
                self.assertEqual(h.status, 403)
                body = json.loads(h.wfile.getvalue().decode("utf-8"))
                self.assertEqual(body["error"], "demo_solo_lectura")

    def test_logout_sigue_permitido(self):
        h = self._demo_handler(command="POST", path="/api/admin/logout")
        ident = _require_admin(h)
        self.assertEqual(ident, {"usuario": demo_installation.demo_user(), "rol": "admin"})
        self.assertIsNone(h.status)

    def test_mismo_usuario_sin_demo_mode_es_admin_normal_y_muta(self):
        # Si alguien real se llamara "demo" (o quien sea TURNOS_DEMO_ADMIN_USER)
        # fuera de TURNOS_DEMO_MODE, es una cuenta admin cualquiera: nada la
        # distingue ni la bloquea. _es_cuenta_demo es doblemente opt-in.
        with _env.patched_env({"TURNOS_DEMO_MODE": "0"}, clear=False):
            h = self._demo_handler(command="POST", path="/api/admin/requests/1/approve")
            ident = _require_admin(h)
            self.assertEqual(ident, {"usuario": demo_installation.demo_user(), "rol": "admin"})
            self.assertIsNone(h.status)


if __name__ == "__main__":
    unittest.main()
