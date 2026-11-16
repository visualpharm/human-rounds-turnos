"""test_ai_referral_verdict_steps.py — the verdict `ai_referral._verdict_3pass` builds from the model's readings.

The function is pure: the readings of the model, the account's request, the model name and the cost go in, the `ai_json`
the desk sees comes out. Each case compares the fields that matter to the reader of that case (verdict, summary
sentence, confidence, the `coincide` of a field, the document block) by dotted path, and the function must leave its
inputs alone. Nothing here depends on how the verdict is split into steps.
"""

import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")

from app.ai import ai_referral  # noqa: E402

_SOL = {"dni": "13452513", "nombre": "Mónica", "apellido": "Alustiza", "especialidad": "Cardiología"}
_OK = "Los campos analizables parecen correctos: DNI coincide, especialidad Cardiología, firma Dr. Gómez MP 1234."


def r(**kw):
    """A legible order with signature and date; `kw` laid over it (None drops the key)."""
    out = {"dni": "13.452.513", "especialidad_solicitada": "Cardiología", "paciente": "Mónica Alustiza",
           "es_orden_medica": True, "legible": True, "medico_firmante": "Dr. Gómez", "matricula": "MP 1234",
           "fecha": "01/08/2026", **kw}
    return {k: v for k, v in out.items() if v is not None}


def conflict(text):
    return {"verdict": "dudosa", "confianza": 0.5, "resumen": "Revisar: " + text + "."}


# (name, readings, request, {dotted path: value}[, model, cost])
CASES = [
    ("everything_matches", [r()], _SOL, {"verdict": "parece_correcta", "confianza": 0.9, "resumen": _OK, "pases": 1,
        "campos.dni.coincide": True, "campos.paciente.coincide": True, "campos.especialidad.coincide": True,
        "documento.campos_faltantes": [], "modelo": "m1", "campos.dni.acuerdo": 1.0}),
    ("agreeing_passes_say_how_many_agree", [r(), r(), r()], _SOL, {"verdict": "parece_correcta",
        "resumen": _OK + " 3/3 lecturas coinciden.", "pases": 3, "campos.dni.acuerdo": 1.0}),
    ("the_majority_value_wins", [r(), r(), r(dni="99999999")], _SOL, {"verdict": "parece_correcta",
        "resumen": _OK + " 2/3 lecturas coinciden.", "campos.dni.valor": "13.452.513", "campos.dni.acuerdo": 0.667}),
    ("passes_agree_after_normalising", [r(matricula="MP 1234"), r(matricula="1234"), r(medico_firmante="DR. GOMEZ")], _SOL,
        {"campos.matricula.acuerdo": 1.0, "campos.medico.acuerdo": 1.0, "campos.medico.valor": "Dr. Gómez"}),
    ("a_member_number_is_compared_by_its_digits", [r(nro_afiliado="12-345 678"), r(nro_afiliado="12345678")], _SOL,
        {"campos.afiliado.acuerdo": 1.0, "campos.afiliado.valor": "12-345 678"}),
    ("insurer_is_read", [r(obra_social_o_prepaga="OSDE 210")], _SOL, {"campos.prepaga.valor": "OSDE 210"}),
    ("practices_are_merged_without_duplicates",
        [r(practicas=["Glucemia", "glucemia", "Hemograma"]), r(practicas=["Hemograma"])], _SOL,
        {"campos.practicas.valor": ["Hemograma", "Glucemia"]}),
    # --- not an order, illegible, other jurisdiction ---
    ("a_document_that_is_not_an_order", [{"es_orden_medica": False, "tipo_documento": "un ticket"}], _SOL,
        {"verdict": "documento_equivocado", "confianza": 0.85, "resumen": "El documento no parece una orden médica: parece un ticket.",
         "documento.es_orden": False, "documento.tipo": "un ticket", "documento.campos_faltantes": []}),
    ("a_wrong_document_counts_the_passes_that_agree_on_it", [{"es_orden_medica": False, "tipo_documento": "factura"}] * 3, _SOL,
        {"resumen": "El documento no parece una orden médica: parece factura. 3/3 lecturas coinciden."}),
    ("the_type_is_not_shown_for_an_order", [r(tipo_documento="receta")], _SOL, {"documento.tipo": None}),
    ("a_document_that_is_not_an_order_without_a_type", [{"es_orden_medica": False}], _SOL,
        {"verdict": "documento_equivocado", "resumen": "El documento no parece una orden médica."}),
    ("not_an_order_beats_illegible", [{"es_orden_medica": False, "legible": False}], _SOL, {"verdict": "documento_equivocado"}),
    ("explicitly_illegible", [r(legible=False)], _SOL, {"verdict": "ilegible", "confianza": 0.2,
        "resumen": "La orden no se pudo leer con claridad — revisala a mano."}),
    ("nothing_read_is_illegible", [{"legible": True}], _SOL, {"verdict": "ilegible"}),
    ("no_readings_is_illegible", [], _SOL, {"verdict": "ilegible", "pases": 0, "campos.dni.acuerdo": 0.0}),
    ("illegible_lists_no_missing_fields", [r(legible=False, medico_firmante=None, matricula=None, fecha=None)], _SOL,
        {"verdict": "ilegible", "documento.campos_faltantes": []}),
    ("other_jurisdiction", [r(jurisdiccion_valida=False, jurisdiccion_detectada="Córdoba")], _SOL,
        {"verdict": "dudosa", "confianza": 0.85, "documento.jurisdiccion_valida": False, "documento.jurisdiccion_detectada": "Córdoba",
         "resumen": "La orden parece emitida en otra jurisdicción (Córdoba), no en la de este centro."}),
    ("other_jurisdiction_unnamed", [r(jurisdiccion_valida=False)], _SOL,
        {"resumen": "La orden parece emitida en otra jurisdicción, no en la de este centro."}),
    ("other_jurisdiction_beats_a_dni_conflict", [r(jurisdiccion_valida=False, dni="99999999")], _SOL, {"confianza": 0.85}),
    ("an_unspecified_jurisdiction_is_valid", [r(jurisdiccion_detectada="Pinamar")], _SOL,
        {"documento.jurisdiccion_valida": True, "documento.jurisdiccion_detectada": None, "verdict": "parece_correcta"}),
    # --- comparison with the request ---
    ("dni_mismatch", [r(dni="99999999")], _SOL, {**conflict("el DNI de la orden (99.999.999) no coincide con el de la cuenta (13.452.513)"),
        "campos.dni.coincide": False}),
    ("the_requests_dni_is_compared_by_its_digits", [r()], {**_SOL, "dni": "13.452.513"}, {"campos.dni.coincide": True}),
    ("an_unread_dni_is_not_a_conflict", [r(dni=None)], _SOL, {"verdict": "parece_correcta", "campos.dni.coincide": None,
        "campos.paciente.coincide": True, "resumen": "Los campos analizables parecen correctos: el nombre del paciente coincide, "
        "especialidad Cardiología, firma Dr. Gómez MP 1234."}),
    ("an_unasked_dni_is_not_a_conflict", [r(dni="99999999")], {"especialidad": "Cardiología"}, {"campos.dni.coincide": None,
        "verdict": "parece_correcta"}),
    ("name_mismatch", [r(dni=None, paciente="Juan Pérez")], _SOL, {**conflict(
        "la orden está a nombre de Juan Pérez, no del titular de la cuenta (Mónica Alustiza)"), "campos.paciente.coincide": False}),
    ("the_surname_alone_names_the_account_holder", [r(dni=None, paciente="Alustiza")], {"especialidad": "Cardiología", "apellido": "Alustiza"},
        {"campos.paciente.coincide": True}),
    ("no_account_name_means_no_name_comparison", [r(dni=None)], {"especialidad": "Cardiología"}, {"campos.paciente.coincide": None}),
    ("specialty_mismatch", [r(especialidad_solicitada="Cirugía Plástica")], _SOL, {**conflict(
        "la especialidad de la orden (Cirugía Plástica) no coincide con la solicitada (Cardiología)"), "campos.especialidad.coincide": False}),
    ("an_unread_specialty_is_not_a_conflict", [r(especialidad_solicitada=None)], _SOL, {"campos.especialidad.coincide": None,
        "verdict": "parece_correcta"}),
    ("the_service_id_stands_for_the_specialty", [r(especialidad_solicitada="Ergometría")],
        {"dni": "13452513", "servicio_id": "cardiologia"}, {"campos.especialidad.coincide": False}),
    ("a_requested_practice_rescues_the_specialty", [r(practicas=["Ergometría", None, ""])],
        {"dni": "13452513", "especialidad": "Ergometría"}, {"campos.especialidad.coincide": True, "verdict": "parece_correcta"}),
    ("all_three_conflicts_are_listed", [r(dni="99999999", paciente="Juan Pérez", especialidad_solicitada="Cirugía Plástica")], _SOL,
        {"verdict": "dudosa", "confianza": 0.5, "resumen": "Revisar: el DNI de la orden (99.999.999) no coincide con el de la cuenta "
         "(13.452.513); la orden está a nombre de Juan Pérez, no del titular de la cuenta (Mónica Alustiza); la especialidad de la orden "
         "(Cirugía Plástica) no coincide con la solicitada (Cardiología)."}),
    # --- completeness ---
    ("no_signature_and_no_licence", [r(medico_firmante=None, matricula=None)], _SOL, {"verdict": "dudosa", "confianza": 0.6,
        "resumen": "Revisar: no se lee la firma ni la matrícula del médico.", "documento.campos_faltantes": ["firma_matricula"]}),
    ("no_date", [r(fecha=None)], _SOL, {"resumen": "Revisar: la orden no tiene fecha de emisión.", "documento.campos_faltantes": ["fecha"]}),
    ("neither_signature_nor_date", [r(medico_firmante=None, matricula=None, fecha=None)], _SOL, {"resumen": "Revisar: no se lee la firma ni la "
        "matrícula del médico; la orden no tiene fecha de emisión.", "documento.campos_faltantes": ["firma_matricula", "fecha"]}),
    ("a_licence_alone_signs", [r(medico_firmante=None)], _SOL, {"verdict": "parece_correcta", "documento.campos_faltantes": []}),
    ("a_signer_alone_signs", [r(matricula=None)], _SOL, {"verdict": "parece_correcta", "documento.campos_faltantes": []}),
    ("a_conflict_beats_incompleteness", [r(dni="99999999", fecha=None)], _SOL, {"confianza": 0.5}),
    ("incomplete_passes_say_how_many_agree", [r(fecha=None)] * 3, _SOL, {"resumen": "Revisar: la orden no tiene fecha de emisión. 3/3 lecturas coinciden."}),
    ("a_wrong_document_lists_no_missing_fields", [{"es_orden_medica": False}], _SOL, {"documento.campos_faltantes": []}),
    # --- defaults and rounding ---
    ("nothing_to_compare_with_is_no_conflict", [r(dni=None, paciente=None, especialidad_solicitada=None)], {},
        {"verdict": "parece_correcta", "campos.dni.coincide": None, "campos.paciente.coincide": None, "campos.especialidad.coincide": None,
         "resumen": "Los campos analizables parecen correctos: firma Dr. Gómez MP 1234."}),
    ("a_missing_is_order_flag_means_an_order", [r(es_orden_medica=None)], _SOL, {"documento.es_orden": True, "verdict": "parece_correcta"}),
    ("cost_rounds_to_six_places", [r()], _SOL, {"costo_usd": 0.0, "modelo": "gemini"}, "gemini", 0.0000004999),
    ("cost_rounds_up", [r()], _SOL, {"costo_usd": 1.234568}, "gemini", 1.23456789),
]


def _get(out, path):
    for part in path.split("."):
        out = out[part]
    return out


def _make(readings, request, expected, model, cost):
    def test(self):
        given = copy.deepcopy((readings, request))
        out = ai_referral._verdict_3pass(copy.deepcopy(readings), copy.deepcopy(request), model, cost)
        for path, value in expected.items():
            self.assertEqual(_get(out, path), value, path)
        self.assertEqual((readings, request), given)
    return test


class TestVerdict3Pass(unittest.TestCase):
    maxDiff = None


for _case in CASES:
    _name, _readings, _request, _expected = _case[:4]
    _model, _cost = (_case[4], _case[5]) if len(_case) > 4 else ("m1", 0.0)
    setattr(TestVerdict3Pass, "test_" + _name, _make(_readings, _request, _expected, _model, _cost))


if __name__ == "__main__":
    unittest.main()
