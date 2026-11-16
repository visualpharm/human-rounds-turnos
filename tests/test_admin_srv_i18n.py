"""test_admin_srv_i18n.py — i18n de la unidad 28 (strings server-rendered del
panel admin: booking_requests.py [sólo superficie admin], reports.py,
settings_admin.py, admin_users.py), namespace admin.srv.

Dos cosas se prueban acá:

1. **El valor de un rol nunca se traduce.** `admin_usuarios._ROLES` vive en
   TRES lugares (acá, el CHECK de `operators.role` en db.py, `applyRol` en
   admin.html) — traducir el VALOR (no la etiqueta) rompe la conexión de esa
   cuenta sin ningún error visible. Nada más lo probaba antes de esto: se
   agrega acá para que un futuro cambio que reemplace `_ROLES` por claves
   i18n falle fuerte en CI, no en producción.
2. **Las oraciones/labels que sí se tradujeron responden en inglés** cuando
   `i18n.bind_lang("en")` está activo, y en castellano — BYTE A BYTE contra
   el texto de siempre — cuando no hay idioma bindeado (instalación de un
   solo idioma, el caso por defecto). No pega contra la base: usa sólo
   funciones puras (sin DB) de cada módulo.
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app.core import i18n  # noqa: E402
from app.admin_panel import admin_users    # noqa: E402
from app.admin_panel import reports        # noqa: E402


class TestRolValorNuncaTraducido(unittest.TestCase):
    """admin_usuarios._ROLES (unit 28) <-> operators.role CHECK (db.py) <->
    applyRol (admin.html, otra unidad). Si algún lado se traduce y los otros
    no, crear esa cuenta revienta la conexión sin avisar (admin_panel/AGENTS.md,
    invariante "una cuenta se cae en silencio")."""

    def test_roles_son_exactamente_los_valores_de_wire(self):
        # Los valores en sí (nunca las etiquetas que los muestran) — si
        # alguien los reemplaza por "Médico"/"Admisión"/etc. esto explota acá,
        # no en el alta de una cuenta real.
        self.assertEqual(admin_users._ROLES, ("medico", "admisionista", "reportes", "admin"))
        for rol in admin_users._ROLES:
            self.assertRegex(rol, r"^[a-z]+$", f"rol {rol!r} no es un identificador ascii en minúsculas")

    def test_roles_coinciden_con_el_check_de_db_py(self):
        # La verdad vigente del CHECK es el esquema de una instalación nueva en
        # head (`alembic/schema-head.txt`, generado del catálogo): el texto de
        # la baseline está congelado y un rol nuevo entra por una revisión.
        with open(os.path.join(ROOT, "alembic", "schema-head.txt"), encoding="utf-8") as f:
            contenido = f.read()
        m = re.search(r"CONSTRAINT operators_role_check CHECK \(\(role = ANY \(ARRAY\[([^\]]+)\]\)\)\)", contenido)
        self.assertIsNotNone(m, "no encontré el CHECK operators_role_check en alembic/schema-head.txt")
        roles_sql = set(re.findall(r"'([a-z]+)'::text", m.group(1)))
        self.assertEqual(roles_sql, set(admin_users._ROLES),
                          "el CHECK de operators.role (alembic/schema-head.txt) quedó desincronizado "
                          "de admin_users._ROLES — agregar/sacar un rol va en los dos lados")


class TestReportsI18n(unittest.TestCase):
    """Funciones puras de reports.py (sin DB) que antes eran constantes de
    módulo congeladas en español — ahora se resuelven por i18n.active()."""

    def setUp(self):
        self._token = None

    def tearDown(self):
        if self._token is not None:
            i18n.unbind(self._token)

    def _bind(self, lang):
        _, self._token = i18n.bind_lang(lang)

    def test_otro_motivo_es_por_defecto(self):
        # Sin idioma bindeado (instalación de un solo idioma): sigue siendo
        # el texto de siempre, byte a byte.
        self.assertEqual(reports._other_reason_label(), "Otro motivo")

    def test_otro_motivo_en(self):
        self._bind("en")
        self.assertEqual(reports._other_reason_label(), "Other reason")

    def test_group_labels_en(self):
        self._bind("en")
        labels = reports._group_labels()
        self.assertEqual(labels["prevenible_antes"], "Avoidable before sending")
        self.assertEqual(labels["capacidad"], "Capacity or schedule gap")
        self.assertEqual(labels["otro"], "Other reason")

    def test_group_labels_es_por_defecto(self):
        labels = reports._group_labels()
        self.assertEqual(labels["prevenible_antes"], "Evitable antes de enviar")
        self.assertEqual(labels["capacidad"], "Falta de capacidad o agenda")

    def test_tracer_labels_en(self):
        self._bind("en")
        labels = reports._tracer_labels()
        self.assertEqual(labels, {"embarazo": "Pregnancy", "nino_sano": "Well-child",
                                    "otros": "Other services"})

    def test_tracer_labels_es_forzado_para_csv(self):
        # El CSV de SUMAR+ (handoff a un programa de gobierno) fuerza "es"
        # aunque el admin esté navegando en inglés.
        self._bind("en")
        self.assertEqual(reports._tracer_labels("es")["embarazo"], "Embarazo")

    def test_month_label_en(self):
        self._bind("en")
        self.assertEqual(reports._month_label(2026, 1), "January 2026")

    def test_month_label_es_por_defecto(self):
        self.assertEqual(reports._month_label(2026, 1), "enero de 2026")

    def test_weekday_names_en_convencion_js_domingo_primero(self):
        self._bind("en")
        nombres = reports._weekday_names()
        self.assertEqual(nombres[0], "sunday")
        self.assertEqual(nombres[1], "monday")

    def test_hour_band_en(self):
        self._bind("en")
        self.assertEqual(reports._hour_band(9), "morning")
        self.assertEqual(reports._hour_band(15), "afternoon")
        self.assertEqual(reports._hour_band(20), "evening")

    def test_plural_en(self):
        self._bind("en")
        self.assertEqual(
            reports._plural(1, "admin.srv.reports.noun.solicitud", "solicitud", "solicitudes"),
            "request")
        self.assertEqual(
            reports._plural(2, "admin.srv.reports.noun.solicitud", "solicitud", "solicitudes"),
            "requests")

    def test_plural_es_por_defecto(self):
        self.assertEqual(
            reports._plural(1, "admin.srv.reports.noun.solicitud", "solicitud", "solicitudes"),
            "solicitud")
        self.assertEqual(
            reports._plural(3, "admin.srv.reports.noun.solicitud", "solicitud", "solicitudes"),
            "solicitudes")

    def test_cluster_sin_especialidad_en(self):
        self._bind("en")
        self.assertEqual(reports._cluster_label(None), "No specialty")

    def test_cluster_sin_especialidad_es_por_defecto(self):
        self.assertEqual(reports._cluster_label(None), "Sin especialidad")


class TestCatalogoAdminSrv(unittest.TestCase):
    """El catálogo admin.srv en sí: mismas claves en es/en, y el namespace
    "admin" queda excluido del bundle que ve un paciente (mismo criterio que
    "setup" — ver i18n.py:_CLIENT_EXCLUDE)."""

    def test_mismas_claves_es_en(self):
        es = {k for k in i18n._load_catalog("es") if k.startswith("admin.srv.")}
        en = {k for k in i18n._load_catalog("en") if k.startswith("admin.srv.")}
        self.assertTrue(es, "el catálogo es debería tener claves admin.srv.*")
        self.assertEqual(es, en)

    def test_namespace_admin_nunca_va_al_paciente(self):
        claves = i18n._client_catalog("en")
        self.assertFalse(
            [k for k in claves if k.startswith("admin.")],
            "una clave admin.* se coló en el bundle del paciente")


if __name__ == "__main__":
    unittest.main()
