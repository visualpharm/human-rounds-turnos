"""La auditoría de sólo lectura de aprobadas con un médico de otro servicio
(scripts/auditar_medico_servicio.py, 2026-10-23) usa la MISMA regla que la
aprobación: el pool del servicio (schedule_policy.belongs). Decisión pura,
sin base."""

import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "auditar_medico_servicio", os.path.join(ROOT, "scripts", "auditar_medico_servicio.py"))
auditar_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auditar_mod)

SERVICIOS = {
    "eco-cardio": {"id": "eco-cardio", "nombre": "Ecocardiograma", "cluster": "cardio", "centro": "hospital"},
    "trauma": {"id": "trauma", "nombre": "Traumatología", "cluster": "trauma", "centro": "hospital"},
}
MEDICOS = [
    {"id": "clara", "nombre": "Clara Latido", "especialidad": "Cardióloga", "cluster": "cardio", "centro": "hospital"},
    {"id": "tomas", "nombre": "Tomás Hueso", "especialidad": "Traumatólogo", "cluster": "trauma", "centro": "hospital"},
]


def _fila(i, servicio, medico="", medico_id=""):
    return {"id": i, "servicio_id": servicio, "medico_asignado": medico,
            "medico_asignado_id": medico_id, "turno_dt": "2026-10-20T10:00-03:00"}


class TestAuditarMedicoServicio(unittest.TestCase):
    def _run(self, filas):
        return auditar_mod.auditar(filas, MEDICOS, lambda sid: SERVICIOS.get(sid, {}))

    def test_lista_solo_las_de_otra_especialidad(self):
        out = self._run([
            _fila(1, "eco-cardio", "Tomás Hueso", "tomas"),   # traumatólogo en un eco → hallazgo
            _fila(2, "eco-cardio", "Clara Latido", "clara"),  # su servicio → bien
            _fila(3, "trauma", "Tomás Hueso"),                # sólo nombre, su servicio → bien
            _fila(4, "trauma"),                               # sin médico → no se juzga
        ])
        self.assertEqual([(h["id"], h["motivo"]) for h in out], [(1, "medico_no_atiende_servicio")])

    def test_por_nombre_cuando_no_hay_id(self):
        out = self._run([_fila(5, "trauma", "Clara Latido")])
        self.assertEqual([(h["id"], h["motivo"]) for h in out], [(5, "medico_no_atiende_servicio")])

    def test_nombre_fuera_del_catalogo_y_servicio_desconocido(self):
        out = self._run([_fila(6, "trauma", "Dr. Inventado"), _fila(7, "borrado", "Clara Latido")])
        self.assertEqual([(h["id"], h["motivo"]) for h in out],
                         [(6, "medico_fuera_del_catalogo"), (7, "servicio_desconocido")])


if __name__ == "__main__":
    unittest.main()
