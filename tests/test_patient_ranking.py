"""Guardas del ranking de asistencia (patient_ranking.py).

Lo que se fija acá no son los números exactos —los pesos son política y se
recalibran— sino las PROPIEDADES que el responsable del producto pidió y que la fórmula tiene que
seguir cumpliendo después de cualquier ajuste:

  1. sin historia = el promedio del hospital, nunca 0;
  2. el volumen no sube el techo: venir a 30 turnos no rankea mejor que la
     propia tasa de asistencia;
  3. sacar turnos de mentira BAJA el puntaje (cancelar en masa no premia,
     duplicar el mismo servicio castiga);
  4. lo viejo pesa menos que lo reciente;
  5. una serie legítima del mismo servicio (kinesiología) no es duplicación.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.modules.turnos import patient_ranking as rp    # noqa: E402
import _env  # noqa: E402

AHORA = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def turno(dias_atras, asistencia=None, estado="aprobada", servicio="cardiologia",
          confirmacion=None, cancelada_en=None, sid=None):
    """Una fila de `solicitudes` como la devuelve db_booking.booking_requests_for_ranking."""
    cuando = AHORA - timedelta(days=dias_atras)
    return {
        "id": sid if sid is not None else int(dias_atras * 1000),
        "dni": "30111222", "servicio_id": servicio, "estado": estado,
        "turno_dt": cuando, "asistencia": asistencia,
        "confirmacion": confirmacion, "cancelada_en": cancelada_en,
        "created": cuando - timedelta(days=10),
    }


def puntaje(rows):
    return rp.from_requests(rows, AHORA)["puntaje"]


class SinHistoria(unittest.TestCase):
    def test_nadie_arranca_en_cero(self):
        """Quien nunca pisó el hospital no es 'poco confiable': es desconocido.
        Arranca en el promedio del hospital, que es lo único honesto."""
        r = rp.from_requests([], AHORA)
        self.assertEqual(r["puntaje"], round(100 * rp.PRIOR()))
        self.assertTrue(r["sin_historial"])
        self.assertEqual(r["nivel"], "none")

    def test_turnos_sin_marcar_no_dicen_nada(self):
        """Nadie registró asistencia = no se sabe qué pasó. No se asume."""
        rows = [turno(d, asistencia=None) for d in (30, 60, 90)]
        self.assertTrue(rp.from_requests(rows, AHORA)["sin_historial"])

    def test_una_sola_visita_no_da_cien(self):
        """1 de 1 no es 100 %: es un dato. El prior lo sostiene cerca del
        promedio hasta que haya evidencia."""
        r = rp.from_requests([turno(10, "presente")], AHORA)
        self.assertLess(r["puntaje"], 95)
        self.assertGreater(r["puntaje"], round(100 * rp.PRIOR()))


class ElVolumenNoEsMerito(unittest.TestCase):
    """La mitad de la consigna: 'que no premie sacar muchos turnos'."""

    def test_el_puntaje_nunca_pasa_la_tasa_propia(self):
        """Ni con 60 turnos perfectos el puntaje supera el 100 % real: el
        techo es la propia tasa, el volumen sólo saca la duda."""
        muchos = [turno(d, "presente", sid=d) for d in range(1, 61)]
        self.assertLessEqual(puntaje(muchos), 100)

    def test_mismo_comportamiento_mismo_orden(self):
        """Dos personas con la misma tasa (80 %) no se ordenan por quién sacó
        más turnos: la de más volumen puede estar apenas por encima (menos
        incertidumbre), nunca por un múltiplo."""
        pocos = ([turno(d, "presente", sid=d) for d in range(1, 5)]
                 + [turno(5, "ausente", sid=5)])
        muchos = ([turno(d, "presente", sid=d) for d in range(1, 25)]
                  + [turno(d, "ausente", sid=d) for d in range(25, 31)])
        self.assertLessEqual(abs(puntaje(muchos) - puntaje(pocos)), 8)

    def test_faltar_baja_mas_de_lo_que_venir_sube(self):
        limpio = [turno(d, "presente", sid=d) for d in range(1, 11)]
        con_falta = limpio + [turno(11, "ausente", sid=11)]
        self.assertLess(puntaje(con_falta), puntaje(limpio))


class TurnosDeMentira(unittest.TestCase):
    """La otra mitad: que la conducta de desperdiciar lugares se note."""

    def test_cancelar_todo_no_da_un_puntaje_excelente(self):
        """Diez turnos sacados y cancelados —aun avisando— no pueden rankear
        como diez turnos cumplidos. Cancelar no es venir."""
        canceló = [turno(d, estado="cancelada", sid=d,
                         cancelada_en=AHORA - timedelta(days=d + 5))
                   for d in range(1, 11)]
        vino = [turno(d, "presente", sid=d) for d in range(1, 11)]
        self.assertLess(puntaje(canceló), puntaje(vino))

    def test_avisar_a_tiempo_es_mejor_que_no_venir(self):
        """Si cancelar castigara igual que faltar, entrenaríamos a la gente a
        no avisar. Tiene que convenir avisar."""
        temprano = [turno(10, estado="cancelada",
                          cancelada_en=AHORA - timedelta(days=15))]
        falto = [turno(10, "ausente")]
        self.assertGreater(puntaje(temprano), puntaje(falto))

    def test_cancelar_sobre_la_hora_pesa_casi_como_faltar(self):
        tarde = [turno(10, estado="cancelada",
                       cancelada_en=AHORA - timedelta(days=10, hours=-2))]
        falto = [turno(10, "ausente")]
        self.assertLess(abs(puntaje(tarde) - puntaje(falto)), 12)

    def test_confirmar_y_no_venir_es_peor_que_faltar_callado(self):
        """La confirmación sacó ese lugar de la lista de espera."""
        callado = [turno(10, "ausente")]
        confirmado = [turno(10, "ausente", confirmacion="si")]
        self.assertLess(puntaje(confirmado), puntaje(callado))

    def test_dos_turnos_del_mismo_servicio_en_la_semana_es_uno_de_mas(self):
        rows = [turno(20, "presente", servicio="cardiologia", sid=1),
                turno(18, None, servicio="cardiologia", sid=2)]
        r = rp.from_requests(rows, AHORA)
        self.assertEqual(r["redundante"], 1)
        self.assertLess(r["puntaje"], puntaje([turno(20, "presente", sid=1)]))

    def test_una_serie_de_kinesiologia_no_es_duplicacion(self):
        """Diez sesiones del mismo servicio en un mes, todas cumplidas: es un
        tratamiento, no un abuso. Penalizarlo sería penalizar estar enfermo."""
        serie = [turno(d, "presente", servicio="kinesiologia", sid=d)
                 for d in range(1, 11)]
        r = rp.from_requests(serie, AHORA)
        self.assertEqual(r["redundante"], 0)
        self.assertGreater(r["puntaje"], 90)

    def test_una_ausencia_no_se_castiga_dos_veces(self):
        """Un turno duplicado Y no asistido es UN hecho: se aplica el peor de
        los dos pesos, nunca la suma."""
        rows = [turno(20, "presente", sid=1), turno(18, "ausente", sid=2)]
        r = rp.from_requests(rows, AHORA)
        self.assertEqual(r["falto"] + r["redundante"], 1)

    def test_los_rechazos_no_cuentan(self):
        """El rechazo lo decide el hospital (orden vencida, ilegible). Contarlo
        castigaría la letra del médico que derivó, no la conducta."""
        rows = [turno(d, estado="rechazada", sid=d) for d in range(1, 11)]
        self.assertTrue(rp.from_requests(rows, AHORA)["sin_historial"])


class ElTiempoPerdona(unittest.TestCase):
    def test_lo_viejo_pesa_menos(self):
        vieja = [turno(1000, "ausente")]
        reciente = [turno(5, "ausente")]
        self.assertGreater(puntaje(vieja), puntaje(reciente))

    def test_quien_se_recupera_sube_solo(self):
        """Faltaba hace dos años, ahora viene siempre: vuelve a estar bien sin
        que nadie tenga que perdonarlo a mano."""
        rows = ([turno(700 + d, "ausente", sid=700 + d) for d in range(5)]
                + [turno(d * 20, "presente", sid=d) for d in range(1, 9)])
        self.assertGreaterEqual(rp.from_requests(rows, AHORA)["puntaje"], 70)

    def test_fuera_de_la_ventana_ni_se_lee(self):
        antiguo = [turno(int(rp.WINDOW_DAYS()) + 200, "ausente")]
        self.assertTrue(rp.from_requests(antiguo, AHORA)["sin_historial"])


class LoQueSeMuestra(unittest.TestCase):
    def test_las_bandas_hablan_como_la_mesa(self):
        bueno = rp.from_requests(
            [turno(d, "presente", sid=d) for d in range(1, 12)], AHORA)
        self.assertEqual(bueno["nivel"], "good")
        self.assertEqual(bueno["etiqueta"], "Viene siempre")

        malo = rp.from_requests(
            [turno(d, "ausente", sid=d) for d in range(1, 12)], AHORA)
        self.assertEqual(malo["nivel"], "bad")
        self.assertEqual(malo["etiqueta"], "Falta seguido")

    def test_tres_bandas_no_cuatro(self):
        """The product owner, 2026-10-27: «three bars, not five. Less precision»
        — tres niveles: «Suele venir» se fundió en «Viene siempre»."""
        self.assertEqual(
            [(corte, slug, texto, nivel) for corte, slug, texto, nivel in rp.BANDS],
            [(70, "viene-siempre", "Viene siempre", "good"),
             (50, "falta-a-veces", "Falta a veces", "warn"),
             (0, "falta-seguido", "Falta seguido", "bad")])

    def test_el_que_antes_solia_venir_ahora_viene_siempre(self):
        """3 de 4 con el promedio del hospital como previa: ~78, la franja
        70–84 que antes decía «Suele venir»."""
        eventos = [{"tipo": "vino", "fecha": AHORA}] * 3 + [{"tipo": "falto", "fecha": AHORA}]
        r = rp.score(eventos, AHORA)
        self.assertTrue(70 <= r["puntaje"] < 85, r["puntaje"])
        self.assertEqual((r["banda"], r["etiqueta"], r["nivel"]),
                         ("viene-siempre", "Viene siempre", "good"))

    def test_el_puntaje_viene_con_el_porque(self):
        """Un número sin explicación no sirve para atender a nadie."""
        rows = ([turno(d, "presente", sid=d) for d in range(1, 6)]
                + [turno(10, "ausente", sid=10), turno(20, "ausente", sid=20)])
        r = rp.from_requests(rows, AHORA)
        self.assertTrue(r["motivos"])
        self.assertTrue(any("ausencia" in m for m in r["motivos"]))
        self.assertLessEqual(len(r["motivos"]), 3)

    def test_los_crudos_son_para_humanos(self):
        """`n` es la evidencia con decaimiento (matemática); vino/falto son
        turnos contados a mano, que es lo que la mesa puede verificar."""
        rows = [turno(5, "presente", servicio="cardiologia", sid=1),
                turno(6, "ausente", servicio="oftalmologia", sid=2),
                turno(7, estado="cancelada", servicio="laboratorio", sid=3,
                      cancelada_en=AHORA - timedelta(days=12))]
        r = rp.from_requests(rows, AHORA)
        self.assertEqual((r["vino"], r["falto"], r["cancelo"]), (1, 1, 1))

    def test_sin_base_de_datos_devuelve_neutro_no_cero(self):
        """Un 0 se leería como 'este falta siempre'. Un dato auxiliar caído
        nunca puede difamar a un paciente."""
        out = rp.by_dnis(["30111222"])
        self.assertEqual(set(out), {"30111222"})
        self.assertGreater(out["30111222"]["puntaje"], 0)


class Configuracion(unittest.TestCase):
    def test_el_prior_se_puede_recalibrar(self):
        _env.set_env(self, TURNOS_RANKING_PRIOR="0.6")
        self.assertEqual(rp.from_requests([], AHORA)["puntaje"], 60)

    def test_un_typo_en_el_entorno_no_rompe_nada(self):
        _env.set_env(self, TURNOS_RANKING_PRIOR="ochenta por ciento")
        self.assertEqual(rp.from_requests([], AHORA)["puntaje"], 80)


if __name__ == "__main__":
    unittest.main()
