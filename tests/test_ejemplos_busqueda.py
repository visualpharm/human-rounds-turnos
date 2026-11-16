"""test_ejemplos_busqueda.py — los ejemplos "Probá:" de cada página de listado
(site_listing.py: _example_locality / _example_doctor / _example_service / _example_specialty /
_example_dedup) son PROPIOS de la página y VÁLIDOS: enseñan los tres modos de búsqueda
(localidad · nombre de un médico presente · servicio/estudio) y cada uno cae en
datos que la página realmente tiene. Reemplaza el set fijo "Clínica médica ·
Hospital · Ostende" que aparecía hasta en Odontología (responsable del producto, 2026-07-23).

Correr:  python3 -m unittest test.test_ejemplos_busqueda -v   (desde la raíz)
Sin base ni red: opera sobre webdata/*.json.
"""
import unittest
from unittest.mock import patch

from app.patient_web import site_base, site_catalog, site_centers, site_listing


class TestHelpersEjemplos(unittest.TestCase):
    def test_localidad_de_los_centros(self):
        # This is a pure helper contract. Pin the candidate list rather than
        # inheriting whichever deployment pack happens to be active.
        with patch.object(site_centers, "_EXAMPLE_LOCALITIES", ["Sampleton", "Riverside"]):
            self.assertEqual(
                site_listing._example_locality(["Sampleton"]),
                "Sampleton",
            )
            self.assertEqual(
                site_listing._example_locality(["Riverside Community Clinic"]),
                "Riverside",
            )
            self.assertIsNone(site_listing._example_locality(["Centro X"]))

    def test_medico_nombre_completo(self):
        # entre apellidos presentes, prefiere uno inequívoco y familiar
        meds = [{"nombre": "Bruno Lopez"}, {"nombre": "Ana Perez"}]
        self.assertEqual(site_listing._example_doctor(meds), "Bruno Lopez")
        # el nombre entero, sin título y sin gritar: un apellido corto solo
        # («Conti») se leía como un chip cortado por el borde
        self.assertEqual(site_listing._example_doctor([{"nombre": "Dra. AGUSTINA CONTI"}]),
                         "Agustina Conti")
        # título sin punto también fuera; el nombre entero, nunca un recorte
        # («Carlos Brandt», «la Fuente»): el buscador busca la frase seguida
        self.assertEqual(site_listing._example_doctor([{"nombre": "Dr Juan Carlos Brandt"}]),
                         "Juan Carlos Brandt")
        self.assertEqual(site_listing._example_doctor([{
            "nombre": "Dra. MARÍA CECILIA URTASUN VIALE",
            "nombre_pila": "MARÍA CECILIA", "apellido": "URTASUN VIALE"}]),
            "María Cecilia Urtasun Viale")
        # ignora filas de rol (Enfermería/Técnico/Administrativo) y vacíos
        self.assertIsNone(site_listing._example_doctor([{"nombre": "X Y", "rol": True}]))
        self.assertIsNone(site_listing._example_doctor([]))

    def test_servicio_sufijo_y_prefijo(self):
        svs = [{"nombre": "Odontología"}, {"nombre": "Odontología - Urgencias"}]
        # el que difiere del título del cluster, y el sufijo tras ' - '
        self.assertEqual(site_listing._example_service(svs, "Odontología"), "Urgencias")
        # 'Consultorio [de] X' → X (más corto, sigue siendo substring válido)
        svs2 = [{"nombre": "Consultorio de niño enfermo"}]
        self.assertEqual(site_listing._example_service(svs2, ""), "niño enfermo")
        self.assertEqual(
            site_listing._example_service([{"nombre": "Clínica Médica"}], ""),
            "Medicina general",
        )

    def test_dedup_ordenado_sin_vacios(self):
        self.assertEqual(site_listing._example_dedup("Ostende", None, "Ostende", "Echegaray"),
                         ["Ostende", "Echegaray"])


class TestEjemplosValidosEnLaPagina(unittest.TestCase):
    """Cada ejemplo de una página REAL tiene que devolver resultados en los datos
    de esa página — un ejemplo que no matchea nada es exactamente el bug."""

    def _norm(self, s):
        return site_base._norm_name(s)

    def test_odontologia_urgencias_presente(self):
        cid = "odontologia"
        svs = [s for s in site_catalog._services() if s.get("cluster") == cid]
        meds = [m for m in site_catalog._professionals() if m.get("cluster") == cid]
        centro_nombres = [(site_centers._centers_by_slug().get(s.get("centro")) or {}).get("name") or ""
                          for s in svs]
        loc = site_listing._example_locality(centro_nombres)
        med = site_listing._example_doctor(meds)
        srv = site_listing._example_service(svs, "Odontología")
        # localidad: aparece en algún centro de la página
        self.assertTrue(loc and any(self._norm(loc) in self._norm(cn) for cn in centro_nombres))
        # médico: el nombre de uno presente en la página
        self.assertTrue(med and any(self._norm(med) in self._norm(m.get("nombre")) for m in meds))
        # servicio: es substring de algún servicio de la página
        self.assertTrue(srv and any(self._norm(srv) in self._norm(s.get("nombre")) for s in svs))

    def test_doctors_localidad_valida_por_columna_centro(self):
        # /doctors ganó la columna Centro (2026-07-23): la localidad vuelve a ser
        # un ejemplo válido porque el centro pasa a ser texto buscable.
        meds = site_catalog._professionals()
        rows = [site_listing._doctor_listing_row(m) for m in meds]
        self.assertTrue(any(r.get("centro") for r in rows),
                        "las filas de /doctors deben traer el centro")
        loc = site_listing._example_locality([r.get("centro") or "" for r in rows])
        self.assertTrue(loc)
        self.assertTrue(any(self._norm(loc) in self._norm(r.get("centro")) for r in rows))


if __name__ == "__main__":
    unittest.main()
