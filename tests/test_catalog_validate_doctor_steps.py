"""test_catalog_validate_doctor_steps.py — what the catalog's doctor validation answers, at its public boundary.

`catalog.validate("medicos", doc, is_new)` returns `(document, None)` or `(None, refusal text)` and never touches the
caller's dict. These cases feed one document each and compare the answer: the refusal text, or the keys the saved
document must carry (`has`) and the ones it must not (`lacks`). Nothing here depends on how the validation is split
into steps. The center and specialty catalogs are stubbed, so no database or webdata is needed.
"""

import copy
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")

from app.patient_web import catalog  # noqa: E402

_CENTERS = [{"slug": "hospital", "name": "Hospital de Pinamar"}, {"slug": "caps-1", "name": "CAPS 1"}, {"slug": "sin-nombre"}]
_SPECIALTIES = [{"nombre": "Cardiología", "cluster": "cardiologia"}, {"nombre": "Dermatología", "cluster": "dermatologia"}]
_BAND = {"dias": [1, 2, 3], "desde": "09:00", "hasta": "12:00", "cada_min": 20}
_BASE = {"nombre_pila": "Ana", "apellido": "Sobre", "especialidad": "Cardiología", "centro": "hospital"}
_SPEC_UNKNOWN = "especialidad desconocida: 'Astrología' (no está en especialidades.json)"


def doc(**kw):
    """A complete doctor with `kw` laid over it; a None value removes the key."""
    out = {**_BASE, **kw}
    return {k: v for k, v in out.items() if v is not None}


def validate(document, is_new):
    given = copy.deepcopy(document)
    with mock.patch.object(catalog, "centers", return_value=copy.deepcopy(_CENTERS)), \
            mock.patch.object(catalog, "specialties", return_value=copy.deepcopy(_SPECIALTIES)), \
            mock.patch.object(catalog.secrets, "token_hex", return_value="abc123"):
        saved, err = catalog.validate("medicos", given, is_new)
    return saved, err, given


# (name, document, is_new, refusal text | (has, lacks))
CASES = [
    ("new_empty_is_saved_incomplete", {}, True,
     ({"nombre": "", "centros": [], "incompleto": True, "id": "medico-abc123"}, ["centro", "especialidad", "agenda"])),
    ("edit_empty_is_refused", {}, False, "falta nombre"),
    ("flagged_edit_may_stay_empty", {"incompleto": True}, False, ({"incompleto": True, "id": "medico-abc123"}, [])),
    ("complete_new_gets_name_cluster_center_and_id", doc(), True,
     ({"nombre": "Ana Sobre", "cluster": "cardiologia", "id": "ana-sobre", "centro": "hospital",
       "centros": [{"centro": "hospital", "franjas": [], "excepciones": []}]}, ["incompleto", "agenda"])),
    ("a_complete_doctor_loses_a_stale_incomplete_flag", doc(id="med-ana", incompleto=True), False,
     ({"id": "med-ana"}, ["incompleto"])),
    ("legacy_agenda_becomes_the_primary_center_hours", doc(agenda=_BAND), True,
     ({"centros": [{"centro": "hospital", "franjas": [_BAND], "excepciones": []}],
       "agenda": {"franjas": [_BAND], "excepciones": []}}, [])),
    # --- name ---
    ("name_is_trimmed_and_composed", doc(nombre_pila="  Ana  ", apellido=" Sobre "), True,
     ({"nombre": "Ana Sobre", "nombre_pila": "Ana", "apellido": "Sobre"}, [])),
    ("plain_name_is_kept_and_the_parts_dropped", doc(nombre_pila="Ana", apellido=None, nombre="Dr. Pérez"), False,
     ({"nombre": "Dr. Pérez", "id": "dr-perez"}, ["nombre_pila", "apellido"])),
    ("parts_beat_the_plain_name", doc(nombre="Otro Nombre"), True, ({"nombre": "Ana Sobre"}, [])),
    ("surname_alone_names_the_doctor", doc(nombre_pila=None, apellido="Pérez"), True, ({"nombre": "Pérez"}, ["apellido"])),
    ("edit_without_a_name_is_refused", doc(nombre_pila=None, apellido=None), False, "falta nombre"),
    # --- specialty ---
    ("unknown_specialty_is_refused", doc(especialidad="Astrología"), True, _SPEC_UNKNOWN),
    ("the_cluster_is_derived_never_taken_from_the_client", doc(cluster="trucho"), False, ({"cluster": "cardiologia"}, [])),
    ("missing_specialty_on_edit_is_refused", doc(especialidad=None), False, "falta especialidad"),
    ("blank_specialty_on_a_flagged_edit_drops_specialty_and_cluster", doc(especialidad="  ", cluster="x", incompleto=True), False,
     ({"incompleto": True}, ["especialidad", "cluster"])),
    ("second_specialty_unknown", doc(especialidad_2="Astrología"), True,
     "especialidad_2 desconocida: 'Astrología' (no está en especialidades.json)"),
    ("second_specialty_needs_the_first", doc(especialidad=None, especialidad_2="Dermatología"), True,
     "especialidad_2 necesita la primera especialidad"),
    ("second_specialty_must_differ", doc(especialidad_2="Cardiología"), True, "especialidad_2 debe ser distinta de la primera"),
    ("second_specialty_gets_its_cluster", doc(especialidad_2="Dermatología"), True,
     ({"especialidad_2": "Dermatología", "cluster_2": "dermatologia"}, [])),
    ("no_second_specialty_drops_a_stale_cluster_2", doc(cluster_2="x"), True, ({}, ["especialidad_2", "cluster_2"])),
    # --- email and search words ---
    ("email_without_at_is_refused", doc(email="no-es-mail"), True, "email inválido"),
    ("email_without_a_domain_dot_is_refused", doc(email="a@b"), True, "email inválido"),
    ("email_is_lowercased", doc(email="  Ana@Hospital.GOB.ar "), True, ({"email": "ana@hospital.gob.ar"}, [])),
    ("blank_email_is_dropped", doc(email="   "), True, ({}, ["email"])),
    ("words_must_be_a_list", doc(palabras="uno"), True, "palabras debe ser una lista de strings"),
    ("blank_words_are_dropped", doc(palabras=[" a ", "", None, "b"]), True, ({"palabras": ["a", "b"]}, [])),
    ("excluded_words_must_be_a_list", doc(palabras_excluidas={"a": 1}), True, "palabras_excluidas debe ser una lista de strings"),
    ("blank_excluded_words_are_dropped", doc(palabras_excluidas=["x", " ", "y "]), True, ({"palabras_excluidas": ["x", "y"]}, [])),
    # --- centers ---
    ("missing_center_on_edit_is_refused", doc(centro=None), False, "falta centro"),
    ("missing_center_on_new_is_saved_incomplete", doc(centro=None), True, ({"incompleto": True, "centros": []}, ["centro"])),
    ("centers_must_be_a_list", doc(centro=None, centros="hospital"), True,
     "centros debe ser una lista no vacía de {centro, franjas, excepciones}"),
    ("a_center_entry_must_be_an_object", doc(centro=None, centros=["hospital"]), True,
     "centro 1: cada centro debe ser un objeto {centro, franjas, excepciones}"),
    ("an_unknown_center_is_named_by_position", doc(centro=None, centros=[{"centro": "nada"}]), True,
     "centro 1: el centro 'nada' no existe"),
    ("a_center_error_names_the_center_the_person_sees",
     doc(centro=None, centros=[{"centro": "hospital"}, {"centro": "caps-1", "franjas": "mal"}]), True,
     "CAPS 1: franjas debe ser una lista de {dias, desde, hasta, cada_min}"),
    ("the_first_center_is_the_primary",
     doc(centro=None, centros=[{"centro": "caps-1", "franjas": [_BAND]}, {"centro": "hospital"}]), True,
     ({"centro": "caps-1", "agenda": {"franjas": [_BAND], "excepciones": []}}, [])),
    ("a_primary_center_without_hours_drops_the_flat_agenda",
     doc(centro="x", agenda=_BAND, centros=[{"centro": "hospital"}]), True, ({"centro": "hospital"}, ["agenda"])),
    ("exceptions_alone_make_an_agenda",
     doc(centro=None, centros=[{"centro": "hospital", "excepciones": [
         {"tipo": "cerrado", "desde_fecha": "2030-01-01", "hasta_fecha": "2030-01-05", "motivo": "vacaciones"}]}]), True,
     ({"agenda": {"franjas": [], "excepciones": [
         {"tipo": "cerrado", "desde_fecha": "2030-01-01", "hasta_fecha": "2030-01-05", "motivo": "vacaciones"}]}}, [])),
    ("the_booking_window_of_a_center_is_a_number",
     doc(centro=None, centros=[{"centro": "hospital", "dias_anticipacion": "30"}]), True,
     ({"centros": [{"centro": "hospital", "franjas": [], "excepciones": [], "dias_anticipacion": 30}]}, [])),
    ("the_booking_window_of_a_center_is_bounded",
     doc(centro=None, centros=[{"centro": "hospital", "dias_anticipacion": "999"}]), True,
     "Hospital de Pinamar: dias_anticipacion va de 1 a 365"),
    # --- id, note and flags ---
    ("a_given_id_is_trimmed_and_kept", doc(id="  med-x "), True, ({"id": "med-x"}, [])),
    ("a_nameless_doctor_gets_a_random_id", {}, True, ({"id": "medico-abc123"}, [])),
    ("the_note_is_trimmed_and_the_demo_note_dropped", doc(nota="  hola  ", demo_nota="viejo"), True,
     ({"nota": "hola"}, ["demo_nota"])),
    ("a_blank_note_is_dropped", doc(nota="  "), True, ({}, ["nota"])),
    ("the_demo_flag_is_a_boolean", doc(demo="si"), True, ({"demo": True}, [])),
    ("the_door_token_is_trimmed", doc(entrevista_qr_token=" tok "), True, ({"entrevista_qr_token": "tok"}, [])),
    ("a_blank_door_token_is_dropped", doc(entrevista_qr_token=""), True, ({}, ["entrevista_qr_token"])),
    ("an_interview_override_must_be_a_boolean", doc(entrevista_habilitada=False), True, ({"entrevista_habilitada": False}, [])),
    ("another_interview_override_is_dropped", doc(entrevista_habilitada="si"), True, ({}, ["entrevista_habilitada"])),
    # --- booking limits ---
    ("a_window_outside_1_365_is_refused", doc(dias_anticipacion="0"), True, "dias_anticipacion va de 1 a 365"),
    ("a_window_and_an_opening_hour_are_kept", doc(dias_anticipacion="14", apertura_turnos="08:00"), True,
     ({"dias_anticipacion": 14, "apertura_turnos": "08:00"}, [])),
    ("a_blank_window_is_dropped", doc(dias_anticipacion=" "), True, ({}, ["dias_anticipacion"])),
    ("a_bad_opening_hour_is_refused", doc(apertura_turnos="8am"), True, "apertura_turnos debe tener formato HH:MM"),
    ("the_active_limit_must_be_a_number", doc(turnos_activos_max="muchos"), True, "turnos_activos_max tiene que ser un número"),
    ("a_doctor_limit_of_one_is_below_the_minimum", doc(turnos_activos_max="1"), True, "turnos_activos_max va de 2 a 60"),
    ("the_active_limit_is_stored_as_a_number", doc(turnos_activos_max="3"), True, ({"turnos_activos_max": 3}, [])),
    ("a_blank_limit_is_dropped", doc(turnos_activos_max=" "), True, ({}, ["turnos_activos_max"])),
    # --- the first refusal wins ---
    ("name_beats_specialty", doc(nombre_pila=None, apellido=None, especialidad="Astrología"), False, "falta nombre"),
    ("specialty_beats_email", doc(especialidad="Astrología", email="mal"), True, _SPEC_UNKNOWN),
    ("email_beats_center", doc(email="mal", centro="nada"), True, "email inválido"),
]


def _make(document, is_new, expected):
    def test(self):
        original = copy.deepcopy(document)
        saved, err, given = validate(document, is_new)
        self.assertEqual(given, original, "the caller's document is never mutated")
        if isinstance(expected, str):
            self.assertEqual((saved, err), (None, expected))
            return
        has, lacks = expected
        self.assertIsNone(err)
        for key, value in has.items():
            self.assertEqual(saved.get(key), value, key)
        for key in lacks:
            self.assertNotIn(key, saved)
    return test


class TestValidateDoctor(unittest.TestCase):
    maxDiff = None


for _name, _doc, _new, _expected in CASES:
    setattr(TestValidateDoctor, "test_" + _name, _make(_doc, _new, _expected))


if __name__ == "__main__":
    unittest.main()
