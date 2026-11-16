"""Gestión / Pacientes report fixes from the analytics review of 13.11: the extra-hours advice needs a
real volume, a growing wait is not the norm, and insurers are counted without case or stray spaces.
Pure functions with the store mocked: no database."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.admin_panel import reports  # noqa: E402


class TestHoursAdviceNeedsVolume(unittest.TestCase):
    def test_below_twenty_requests_no_advice(self):
        fila = {"solicitudes": 19, "horas_semana": 2.0}
        self.assertIsNone(reports._hours_suggestion(fila, 1.0))

    def test_from_twenty_requests_advice_stands(self):
        fila = {"solicitudes": 20, "horas_semana": 2.0}
        self.assertEqual(reports._hours_suggestion(fila, 1.0), 18.0)

    def test_hero_names_no_investment_with_few_requests(self):
        # 8 requests, no hours at all: the sentence «una hora más rinde más» would rest on 8 requests.
        poco = {"clusters": [{"nombre": "Cardiología", "solicitudes": 8, "sin_oferta": True,
                              "solicitudes_por_hora": None, "sugerencia_horas": None}], "equilibrio": 1.0}
        self.assertIsNone(reports._hero_investment(poco))
        mucho = {"clusters": [dict(poco["clusters"][0], solicitudes=24)], "equilibrio": 1.0}
        self.assertEqual(reports._hero_investment(mucho)["tono"], "critico")


class TestWaitMovementTone(unittest.TestCase):
    def _hero(self, actual, previa):
        with mock.patch.object(reports.store, "reports_median_wait_prior", return_value=previa):
            return reports._hero_wait(30, {"mediana_dias_turno": actual})

    def test_a_longer_wait_is_attention(self):
        self.assertEqual(self._hero(9.0, 5.0)["tono"], "atencion")

    def test_a_steady_or_shorter_wait_is_the_norm(self):
        self.assertEqual(self._hero(5.0, 5.2)["tono"], "ok")
        self.assertEqual(self._hero(3.0, 5.0)["tono"], "ok")


class TestInsurersWithoutCaseOrSpaces(unittest.TestCase):
    def test_one_row_with_the_most_frequent_spelling(self):
        filas = [
            {"obras_sociales": [{"nombre": "IOMA"}]},
            {"obras_sociales": [{"nombre": "ioma "}]},
            {"obras_sociales": [{"nombre": " IOMA"}]},
            {"obras_sociales": [{"nombre": "Ioma"}]},
            {"obras_sociales": [{"nombre": "OSDE  Binario"}]},
            {"obras_sociales": [{"nombre": "osde binario"}, {"nombre": "OSDE Binario"}]},
        ]
        with mock.patch.object(reports.store, "reports_patients_coverage", return_value=filas):
            out = reports._coverage_report(10)
        self.assertEqual(out["top_obras_sociales"],
                         [{"nombre": "IOMA", "cantidad": 4}, {"nombre": "OSDE Binario", "cantidad": 2}])

    def test_the_same_insurer_twice_in_one_account_counts_once(self):
        filas = [{"obras_sociales": [{"nombre": "IOMA"}, {"nombre": "ioma"}]}]
        with mock.patch.object(reports.store, "reports_patients_coverage", return_value=filas):
            out = reports._coverage_report(1)
        self.assertEqual(out["top_obras_sociales"], [{"nombre": "IOMA", "cantidad": 1}])


def _fila(nombre, dni, asistencia="presente"):
    return {"dni": dni, "asistencia": asistencia, "turno_dt": None,
            "cobertura": {"obras_sociales": [{"nombre": nombre}]}}


class TestRecoveryGroupsInsurersLikeCoverage(unittest.TestCase):
    def test_ioma_three_spellings_one_row_with_sum_and_top_spelling(self):
        filas = [_fila("IOMA", "1"), _fila("ioma ", "2", "ausente"), _fila("IOMA", "3"), _fila("Ioma", "1"),
                 _fila("PAMI", "4")]
        out = reports._by_insurer(filas)
        self.assertEqual(out[0], {"nombre": "IOMA", "consultas": 4, "presentes": 3, "pacientes": 3})
        self.assertEqual([r["nombre"] for r in out], ["IOMA", "PAMI"])

    def test_accents_do_not_split_an_insurer_in_either_report(self):
        filas = [_fila("Medifé", "1"), _fila("Medife", "2"), _fila("MEDIFÉ ", "3"), _fila("Medifé", "4")]
        out = reports._by_insurer(filas)
        self.assertEqual(out, [{"nombre": "Medifé", "consultas": 4, "presentes": 4, "pacientes": 4}])
        cob = [f["cobertura"] for f in filas]
        with mock.patch.object(reports.store, "reports_patients_coverage", return_value=cob):
            top = reports._coverage_report(4)["top_obras_sociales"]
        self.assertEqual(top, [{"nombre": "Medifé", "cantidad": 4}])

    def test_payload_hero_names_the_merged_row(self):
        filas = [_fila("ioma ", "1"), _fila("IOMA", "2"), _fila("Ioma", "3")]
        with mock.patch.object(reports.store, "reports_consultations_completed", return_value=filas):
            out = reports._cost_recovery_payload(30)
        self.assertEqual(len(out["por_obra_social"]), 1)
        self.assertEqual(out["por_obra_social"][0]["consultas"], 3)
        self.assertEqual(out["por_obra_social"][0]["nombre"], "IOMA")

    def test_sumar_does_not_group_insurers_and_treats_spellings_alike(self):
        # SUMAR+ is the population WITHOUT any insurer: every spelling of IOMA is out of it.
        for n in ("IOMA", "ioma ", "Ioma"):
            self.assertFalse(reports._is_public_only({"obras_sociales": [{"nombre": n}]}))


if __name__ == "__main__":
    unittest.main()
