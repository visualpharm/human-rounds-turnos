"""test_health_coverage.py — which coverage wins when there's more than one (2026-07-24).

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_health_coverage.py"
(WATCH OUT: `python3 -m unittest test.test_x` does NOT resolve — see CONTRIBUTING.md.)

No database needed: health_coverage.py is a pure function over the jsonb.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.cuentas import health_coverage as cobertura  # noqa: E402

# The names exactly as the national registry returns them (SISA/PUCO), with
# dots and parens: that's the real shape they'll arrive in.
IOMA = {"nombre": "O.S.P. BUENOS AIRES (I.O.M.A.)", "origen": "puco"}
OSECAC = {"nombre": "O.S. DE EMPLEADOS DE COMERCIO (OSECAC)", "origen": "puco"}
PAMI = {"nombre": "PAMI", "origen": "declarada"}


class EleccionTestCase(unittest.TestCase):

    def test_el_caso_de_ivan_la_prepaga_le_gana_a_dos_obras_sociales(self):
        # "if I have two obras sociales and Swiss Medical, obviously Swiss
        # Medical is my default one" (the product owner, 2026-07-24)
        cob = {"obras_sociales": [IOMA, OSECAC], "prepaga": {"nombre": "Swiss Medical"}}
        self.assertEqual(cobertura.choose(cob), "prepaga")
        self.assertEqual(cobertura.primary(cob)["nombre"], "Swiss Medical")

    def test_lo_marcado_a_mano_le_gana_al_ranking(self):
        # "But people could set the default to another one as well"
        cob = {"obras_sociales": [IOMA], "prepaga": {"nombre": "OSDE"},
               "principal": IOMA["nombre"]}
        self.assertEqual(cobertura.choose(cob), IOMA["nombre"])

    def test_un_principal_que_quedo_colgado_no_rompe_nada(self):
        # that coverage got removed and it's left pointing at nothing: recompute
        cob = {"obras_sociales": [IOMA], "principal": "OSDE"}
        self.assertEqual(cobertura.choose(cob), IOMA["nombre"])

    def test_los_puntos_del_padron_no_esconden_a_ioma(self):
        # "O.S.P. BUENOS AIRES (I.O.M.A.)" has to be recognized as IOMA:
        # otherwise it tied with any other and won by load order.
        self.assertLess(cobertura.score(IOMA["nombre"]),
                        cobertura.score(OSECAC["nombre"]))
        cob = {"obras_sociales": [IOMA, OSECAC, PAMI]}
        self.assertEqual(cobertura.choose(cob), OSECAC["nombre"])

    def test_quien_solo_tiene_pami_ve_pami(self):
        # the ranking is a tiebreaker, not a way to leave anyone without coverage
        cob = {"obras_sociales": [PAMI]}
        self.assertEqual(cobertura.choose(cob), "PAMI")
        self.assertEqual(cobertura.primary(cob)["tipo"], "obra_social")

    def test_sin_ninguna_cobertura_no_hay_principal(self):
        for vacio in ({}, None, {"obras_sociales": []}):
            self.assertIsNone(cobertura.choose(vacio))
            self.assertIsNone(cobertura.primary(vacio))

    def test_cualquier_prepaga_le_gana_a_cualquier_obra_social(self):
        # even one that's not in the table: what orders first is the TYPE
        cob = {"obras_sociales": [OSECAC], "prepaga": {"nombre": "Una Prepaga Chica"}}
        self.assertEqual(cobertura.choose(cob), "prepaga")

    def test_normalizar_deja_el_principal_escrito(self):
        # it's saved resolved: what's persisted doesn't depend on the ranking
        # never changing again
        cob = cobertura.normalize({"obras_sociales": [IOMA, OSECAC]})
        self.assertEqual(cob["principal"], OSECAC["nombre"])
        # and with no coverages there's no leftover phantom principal
        self.assertNotIn("principal", cobertura.normalize({"principal": "OSDE"}))

    def test_el_afiliado_viaja_con_la_principal(self):
        cob = {"obras_sociales": [dict(OSECAC, afiliado="777")]}
        self.assertEqual(cobertura.primary(cob)["afiliado"], "777")


if __name__ == "__main__":
    unittest.main()
