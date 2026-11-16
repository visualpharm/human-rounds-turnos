"""hsi_resolver: el resolutor automático de conflictos de sincronización.

Sin Postgres ni HSI reales: se parchean las funciones de db (mismo seam que
test_hsi_journal.JournalBase) y el cliente HSI entra por ``client_factory``.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _dbowner  # noqa: E402
import _env  # noqa: E402
from app.core import config, db  # noqa: E402
from app.connectors.hsi import hsi_client  # noqa: E402
from app.connectors.hsi import hsi_resolver  # noqa: E402
from app.modules.turnos import hsi_journal  # noqa: E402

_ART = timezone(timedelta(hours=-3))
AHORA = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
FUTURO = datetime(2026, 8, 3, 9, 0, tzinfo=_ART)


def solicitud(**kw):
    base = {"id": 1, "estado": "aprobada", "turno_dt": FUTURO, "hsi_estado": "error",
            "dni": "30111222", "nombre": "Ana", "apellido": "Prueba",
            "nacimiento": "1990-01-01", "sexo": "F", "servicio_id": "cardiologia",
            "medico_asignado": None, "sobreturno": False, "hsi_appointment_id": None}
    base.update(kw)
    return base


def conflicto(**kw):
    base = {"id": 100, "entity_type": "appointment", "entity_id": "1",
            "direction": "outbound", "reason": "no free slot", "created": AHORA,
            "local_payload": None, "remote_payload": None}
    base.update(kw)
    return base


class ResolverBase(unittest.TestCase):
    def setUp(self):
        self.db_orig, self.sync_orig = {}, {}
        self.resueltos = []          # (id, resolucion)
        self.addCleanup(config._reset_cache_para_tests)   # after the environment is put back
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")
        config._reset_cache_para_tests()
        _env.set_env(self, TURNOS_SKIP_HSI="1")   # offline salvo que el test lo pida

        def resolver(id_, resolucion, resuelto_por="resolver"):
            self.resueltos.append((id_, resolucion))
            return {"id": id_}

        self.patch_db("hsi_resolve_conflict", resolver)
        self.patch_db("hsi_outbox_inflight", lambda *a: True)
        self.patch_sync("replay", lambda limit=20: 0)

    def patch_db(self, name, value):
        owner = _dbowner.owner_of(name)
        self.db_orig.setdefault(name, (owner, getattr(owner, name, None)))
        setattr(owner, name, value)

    def patch_sync(self, name, value):
        self.sync_orig.setdefault(name, getattr(hsi_journal, name))
        setattr(hsi_journal, name, value)

    def tearDown(self):
        for name, (owner, value) in self.db_orig.items():
            setattr(owner, name, value)
        for name, value in self.sync_orig.items():
            setattr(hsi_journal, name, value)

    def acciones(self):
        return [r[1].get("accion") for r in self.resueltos]


class TestClasificar(unittest.TestCase):
    def test_cada_reason_cae_en_su_cluster(self):
        casos = {
            "duplicate_local_hsi_appointment_id": "duplicado",
            "no HSI diary matches professional 'Lic. Diego Coronel'": "profesional",
            "no diary matches 'Ecografía'; available specialties: X": "especialidad",
            "no diaries exist in HSI (run tools/seed_hsi_demo.py first)": "especialidad",
            "no exact free slot for 'Diabetología' at 2026-07-24T17:00": "sin_hueco",
            "remote_appointment_cancelled_or_blocked": "remoto_cancelado",
            "concurrent_local_and_hsi_appointment_change": "carrera",
            "ambiguous_local_requests_for_hsi_appointment": "vinculo",
            "hsi_link_while_local_intent_unsettled": "vinculo",
            "hsi_checkpoint_drift": "drift",
            "hsi_appointment_missing": "drift",
            "cualquier otra cosa": "otro",
        }
        for reason, esperado in casos.items():
            self.assertEqual(hsi_resolver.clasificar(reason), esperado, reason)

    def test_detalle_es_nombra_al_profesional_y_a_la_especialidad(self):
        c = {"reason": "no HSI diary matches professional 'Dra. Martin Galvan'"}
        self.assertIn("Martin Galvan", hsi_resolver.detalle_es(c))
        c = {"reason": "no diary matches 'Ecografía'; available specialties: Cardiología"}
        self.assertIn("Ecografía", hsi_resolver.detalle_es(c))
        self.assertNotIn("available", hsi_resolver.detalle_es(c))


class TestObsoletos(ResolverBase):
    def test_conflicto_de_solicitud_ya_sincronizada_se_cierra_solo(self):
        self.patch_db("hsi_open_conflicts",
                      lambda limit=500: [conflicto(reason="no free slot")])
        self.patch_db("get_booking_request", lambda sid: solicitud(hsi_estado="ok"))
        resumen = hsi_resolver.resolver_pass(now=AHORA)
        self.assertEqual(resumen["resueltos"], 1)
        self.assertEqual(resumen["abiertos"], 0)

    def test_turno_pasado_no_queda_gritando_para_siempre(self):
        pasado = datetime(2026, 7, 1, 9, 0, tzinfo=_ART)
        self.patch_db("hsi_open_conflicts",
                      lambda limit=500: [conflicto(reason="no HSI diary matches professional 'X'")])
        self.patch_db("get_booking_request", lambda sid: solicitud(turno_dt=pasado))
        resumen = hsi_resolver.resolver_pass(now=AHORA)
        self.assertEqual(resumen["resueltos"], 1)

    def test_conflictos_repetidos_quedan_en_uno(self):
        abiertos = [conflicto(id=101, reason="no free slot", created=AHORA),
                    conflicto(id=100, reason="no free slot",
                              created=AHORA - timedelta(hours=1))]
        self.patch_db("hsi_open_conflicts", lambda limit=500: abiertos)
        self.patch_db("get_booking_request", lambda sid: solicitud())
        resumen = hsi_resolver.resolver_pass(now=AHORA)
        self.assertEqual(resumen["resueltos"], 1)          # el viejo, como repetido
        self.assertEqual(resumen["abiertos"], 1)           # el nuevo sigue abierto
        self.assertEqual(self.resueltos[0][0], 100)
        self.assertEqual(self.resueltos[0][1]["accion"], "repetido")

    def test_una_solicitud_no_vive_en_dos_clusters_de_ruteo(self):
        # "sin agenda del profesional" y "sin hueco" son etapas del mismo push:
        # sólo el conflicto más nuevo describe el estado actual.
        abiertos = [conflicto(id=301, reason="no free slot", created=AHORA),
                    conflicto(id=300, reason="no HSI diary matches professional 'X'",
                              created=AHORA - timedelta(hours=2))]
        self.patch_db("hsi_open_conflicts", lambda limit=500: abiertos)
        self.patch_db("get_booking_request", lambda sid: solicitud())
        resumen = hsi_resolver.resolver_pass(now=AHORA)
        self.assertEqual(resumen["abiertos"], 1)
        self.assertEqual([r[0] for r in self.resueltos], [300])
        self.assertEqual(self.resueltos[0][1]["accion"], "repetido")

    def test_el_vinculo_de_un_turno_cargado_a_mano_se_cierra_cuando_queda_resuelto(self):
        mano = {"id": "7018"}
        casos = (  # (reason, solicitud, ¿se cierra?)
            ("ambiguous_local_requests_for_hsi_appointment", solicitud(), False),
            ("ambiguous_local_requests_for_hsi_appointment", solicitud(hsi_appointment_id="7018"), True),
            ("ambiguous_local_requests_for_hsi_appointment", solicitud(estado="cancelada"), True),
            ("hsi_link_while_local_intent_unsettled", solicitud(), False),
            ("hsi_link_while_local_intent_unsettled", solicitud(hsi_appointment_id="7018"), True),
            # el envío propio abrió otro turno: HSI lo tiene dos veces, queda para la mesa
            ("hsi_link_while_local_intent_unsettled", solicitud(hsi_appointment_id="8001"), False),
            ("hsi_link_while_local_intent_unsettled", solicitud(estado="cancelada"), True),
        )
        for reason, row, cierra in casos:
            with self.subTest(reason=reason, row=(row["estado"], row["hsi_appointment_id"])):
                self.resueltos.clear()
                c = conflicto(reason=reason, direction="inbound", remote_payload=mano)
                self.assertEqual(hsi_resolver._obsoleto(c, "vinculo", row, AHORA), cierra)
                self.assertEqual(bool(self.resueltos), cierra)

    def test_carrera_sin_intencion_en_vuelo_se_cierra(self):
        self.patch_db("hsi_open_conflicts",
                      lambda limit=500: [conflicto(reason="concurrent_local_and_hsi_appointment_change")])
        self.patch_db("get_booking_request", lambda sid: solicitud(hsi_estado="ok"))
        self.patch_db("hsi_outbox_inflight", lambda *a: False)
        resumen = hsi_resolver.resolver_pass(now=AHORA)
        self.assertEqual(resumen["resueltos"], 1)


class TestDuplicados(ResolverBase):
    def test_conserva_el_dueno_que_coincide_con_hsi_y_reencola_al_resto(self):
        # HSI re-creado desde cero: los ids arrancan en 1 y la solicitud vieja
        # (9) apunta al appointment nuevo de Ana (81).
        vieja = solicitud(id=9, dni="20999888", nombre="Caso", apellido="Viejo",
                          turno_dt=FUTURO + timedelta(days=5),
                          hsi_appointment_id="3", hsi_estado="ok")
        nueva = solicitud(id=81, hsi_appointment_id="3", hsi_estado="ok")
        rows = {9: vieja, 81: nueva}
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(id=200, entity_id="81", reason="duplicate_local_hsi_appointment_id"),
            conflicto(id=201, entity_id="9", reason="duplicate_local_hsi_appointment_id"),
        ])
        self.patch_db("get_booking_request", lambda sid: rows.get(sid))
        self.patch_db("hsi_booking_requests_by_appointment", lambda appt: [vieja, nueva])
        self.patch_db("hsi_checkpoint_get", lambda *a: {
            "date": "2026-08-03", "hour": "09:00:00",
            "patient": {"person": {"identificationNumber": "30111222"}}})
        limpiados = []
        self.patch_db("clear_hsi_link",
                      lambda sid, estado: limpiados.append((sid, estado)) or rows[sid])
        encolados = []
        self.patch_sync("enqueue_appointment", lambda row, turno_dt=None:
                        encolados.append(row["id"]) or {"id": 1})
        resumen = hsi_resolver.resolver_pass(now=AHORA)
        # la vieja pierde el vínculo y, por ser aprobada futura, se re-encola
        self.assertEqual(limpiados, [(9, "pendiente")])
        self.assertEqual(encolados, [9])
        self.assertEqual(resumen["revinculados"], 1)
        self.assertEqual(resumen["resueltos"], 2)          # ambos conflictos cerrados
        self.assertEqual(resumen["abiertos"], 0)

    def test_duplicado_historico_conserva_su_estado_sin_reencolar(self):
        pasada = solicitud(id=10, dni="17000111", turno_dt=FUTURO - timedelta(days=60),
                           hsi_appointment_id="3", hsi_estado="ok")
        nueva = solicitud(id=81, hsi_appointment_id="3", hsi_estado="ok")
        rows = {10: pasada, 81: nueva}
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(id=200, entity_id="81", reason="duplicate_local_hsi_appointment_id")])
        self.patch_db("get_booking_request", lambda sid: rows.get(sid))
        self.patch_db("hsi_booking_requests_by_appointment", lambda appt: [pasada, nueva])
        self.patch_db("hsi_checkpoint_get", lambda *a: {"date": "2026-08-03", "hour": "09:00:00"})
        limpiados = []
        self.patch_db("clear_hsi_link",
                      lambda sid, estado: limpiados.append((sid, estado)) or rows[sid])
        self.patch_sync("enqueue_appointment",
                        lambda *a, **k: self.fail("un turno pasado no se re-encola"))
        hsi_resolver.resolver_pass(now=AHORA)
        self.assertEqual(limpiados, [(10, "ok")])


class FakeClient:
    """HSI de mentira: agendas por profesional y control de qué intento acepta."""

    def __init__(self, diaries, acepta):
        self.diaries = diaries          # [{"professional": ..., "specialty": ...}]
        self.acepta = acepta            # (professional_hint, overturn) -> bool
        self.creados = []

    def ensure_patient(self, *a):
        return 501

    def diaries_for_specialty(self, hint):
        return self.diaries

    def create_appointment(self, patient_id, hint, when_iso, professional_hint=None,
                           overturn=False, exact=False):
        if not self.acepta(professional_hint, overturn):
            raise hsi_client.HsiError("no exact free slot for %r" % hint)
        self.creados.append((professional_hint, overturn))
        return {"appointment_id": 77, "patient_id": patient_id, "diary_id": 5,
                "specialty": "Cardiología", "professional": professional_hint,
                "date": "2026-08-03", "hour": "09:00:00",
                "actual_turno_dt": "2026-08-03T09:00:00-03:00",
                "requested_turno_dt": when_iso}


class RuteoBase(ResolverBase):
    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_SKIP_HSI=None)   # estos tests hablan con el fake
        self.persistidos, self.medicos = [], []
        self.patch_db("reassign_appointment",
                      lambda sid, when, result, actor=None: self.persistidos.append((sid, when)))
        self.patch_db("hsi_checkpoint_set", lambda *a: None)
        self.patch_db("hsi_mirror_appointment", lambda *a: None)
        self.patch_db("hsi_outbox_by_entity", lambda *a, **k: [])
        self.patch_db("hsi_outbox_confirm", lambda *a: None)
        self.patch_db("assign_doctor_hsi",
                      lambda sid, medico, sobreturno=None, medico_id=None:
                      self.medicos.append((sid, medico, sobreturno)))


class TestProfesional(RuteoBase):
    def test_sin_agenda_del_profesional_lo_reasigna_a_otro_a_la_misma_hora(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(reason="no HSI diary matches professional 'Lic. Diego Coronel'")])
        self.patch_db("get_booking_request",
                      lambda sid: solicitud(medico_asignado="Lic. Diego Coronel"))
        client = FakeClient(
            diaries=[{"professional": "MARIA SUAREZ", "specialty": "Cardiología"}],
            # el reintento con Coronel sigue fallando; Suárez tiene el hueco
            acepta=lambda prof, ot: prof == "MARIA SUAREZ")
        resumen = hsi_resolver.resolver_pass(now=AHORA, client_factory=lambda: client)
        self.assertEqual(resumen["reasignados"], 1)
        self.assertEqual(resumen["abiertos"], 0)
        self.assertEqual(self.medicos, [(1, "Maria Suarez", None)])
        self.assertEqual(self.persistidos, [(1, "2026-08-03T09:00:00-03:00")])
        self.assertIn("reasignado", self.acciones())

    def test_si_la_agenda_aparecio_reintenta_sin_cambiar_al_profesional(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(reason="no HSI diary matches professional 'Lic. Diego Coronel'")])
        self.patch_db("get_booking_request",
                      lambda sid: solicitud(medico_asignado="Lic. Diego Coronel"))
        client = FakeClient(diaries=[], acepta=lambda prof, ot: True)
        resumen = hsi_resolver.resolver_pass(now=AHORA, client_factory=lambda: client)
        self.assertEqual(resumen["reintentos_ok"], 1)
        self.assertEqual(self.medicos, [])                # el médico no cambió
        self.assertIn("reintentado", self.acciones())


class TestAgendaLlena(RuteoBase):
    def test_agenda_llena_cae_a_sobreturno_con_el_profesional_original(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(reason="no exact free slot for 'Cardiología' at 2026-08-03T09:00:00-03:00")])
        self.patch_db("get_booking_request",
                      lambda sid: solicitud(medico_asignado="Dra. Martin Galvan"))
        client = FakeClient(
            diaries=[{"professional": "MARTIN GALVAN", "specialty": "Cardiología"}],
            # nadie más tiene hueco; sólo el sobreturno entra
            acepta=lambda prof, ot: ot)
        resumen = hsi_resolver.resolver_pass(now=AHORA, client_factory=lambda: client)
        self.assertEqual(resumen["sobreturnos"], 1)
        self.assertEqual(resumen["abiertos"], 0)
        self.assertEqual(self.medicos, [(1, "Dra. Martin Galvan", True)])
        self.assertIn("sobreturno", self.acciones())

    def test_agenda_llena_sin_medico_asignado_queda_para_una_persona(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(reason="no free slot for 'Medicina general' at 2026-08-06")])
        self.patch_db("get_booking_request", lambda sid: solicitud())
        client = FakeClient(diaries=[], acepta=lambda prof, ot: True)
        resumen = hsi_resolver.resolver_pass(now=AHORA, client_factory=lambda: client)
        self.assertEqual(resumen["abiertos"], 1)
        self.assertEqual(client.creados, [])


class TestEspecialidad(ResolverBase):
    def test_sin_agenda_de_especialidad_no_es_de_it_y_queda_abierto(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(reason="no diary matches 'Ecografía'; available specialties: Cardiología")])
        self.patch_db("get_booking_request", lambda sid: solicitud())
        resumen = hsi_resolver.resolver_pass(now=AHORA)
        self.assertEqual(resumen["abiertos"], 1)
        self.assertEqual(resumen["resueltos"], 0)


class TestEspecialidadConHsi(RuteoBase):
    def test_si_la_agenda_de_la_especialidad_aparecio_reintenta_solo(self):
        # La propuesta le pidió al personal crear la agenda; una vez creada,
        # el conflicto se cierra sin que nadie toque nada.
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(reason="no diary matches 'Ecografía'; available specialties: Cardiología")])
        self.patch_db("get_booking_request", lambda sid: solicitud(servicio_id="ecografia"))
        client = FakeClient(diaries=[], acepta=lambda prof, ot: True)
        resumen = hsi_resolver.resolver_pass(now=AHORA, client_factory=lambda: client)
        self.assertEqual(resumen["reintentos_ok"], 1)
        self.assertEqual(resumen["abiertos"], 0)
        self.assertIn("reintentado", self.acciones())


class TestBotonesDeResolucion(RuteoBase):
    """aplicar_resolucion: lo que disparan los botones de la sección «HSI»."""

    def setUp(self):
        super().setUp()
        self.rechazadas, self.holds = [], []
        self.patch_db("reject", lambda sid, motivo, resuelta_por=None, desde=None:
                      self.rechazadas.append((sid, motivo, resuelta_por, desde)) or solicitud(estado="rechazada"))
        self.patch_db("holds_release_booking_request", lambda sid: self.holds.append(sid))

    def test_mover_agenda_el_primer_hueco_libre_y_cierra_el_conflicto(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(id=400, reason="no free slot")])
        self.patch_db("get_booking_request", lambda sid: solicitud())
        client = FakeClient(diaries=[], acepta=lambda prof, ot: True)
        out = hsi_resolver.aplicar_resolucion("mover", [400], resuelto_por="rosa",
                                              client_factory=lambda: client, now=AHORA)
        self.assertTrue(out["ok"])
        self.assertTrue(out["resultados"][0]["ok"])
        self.assertEqual(self.persistidos, [(1, "2026-08-03T09:00:00-03:00")])
        self.assertIn("movido", self.acciones())

    def test_sobreturno_mantiene_la_hora_prometida(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(id=401, reason="no free slot")])
        self.patch_db("get_booking_request",
                      lambda sid: solicitud(medico_asignado="Dra. Martin Galvan"))
        client = FakeClient(diaries=[], acepta=lambda prof, ot: ot)
        out = hsi_resolver.aplicar_resolucion("sobreturno", [401],
                                              client_factory=lambda: client, now=AHORA)
        self.assertTrue(out["resultados"][0]["ok"])
        self.assertEqual(client.creados, [("Dra. Martin Galvan", True)])
        self.assertEqual(self.medicos, [(1, "Dra. Martin Galvan", True)])

    def test_rechazar_usa_el_motivo_estandar_y_libera_el_slot(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(id=402, reason="no diary matches 'Ecografía'")])
        self.patch_db("get_booking_request", lambda sid: solicitud())
        out = hsi_resolver.aplicar_resolucion("rechazar", [402], resuelto_por="rosa")
        self.assertTrue(out["resultados"][0]["ok"])
        self.assertEqual(self.rechazadas[0][0], 1)
        self.assertEqual(self.rechazadas[0][1], hsi_resolver.MOTIVO_RECHAZO_HSI)
        self.assertEqual(self.rechazadas[0][2], "rosa")
        self.assertEqual(self.holds, [1])
        self.assertIn("rechazado", self.acciones())

    def test_mover_dice_por_que_cuando_hsi_no_tiene_la_agenda(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(id=403, reason="no diary matches 'Ecografía'")])
        self.patch_db("get_booking_request", lambda sid: solicitud())
        client = FakeClient(diaries=[], acepta=lambda prof, ot: False)
        client.create_appointment = lambda *a, **k: (_ for _ in ()).throw(
            hsi_client.HsiError("no diary matches 'Ecografía'; available: X"))
        out = hsi_resolver.aplicar_resolucion("mover", [403],
                                              client_factory=lambda: client, now=AHORA)
        self.assertFalse(out["resultados"][0]["ok"])
        self.assertIn("agenda", out["resultados"][0]["detalle"])

    def test_una_solicitud_que_ya_no_esta_aprobada_no_se_toca(self):
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(id=404, reason="no free slot")])
        self.patch_db("get_booking_request", lambda sid: solicitud(estado="cancelada"))
        out = hsi_resolver.aplicar_resolucion("rechazar", [404])
        self.assertFalse(out["resultados"][0]["ok"])
        self.assertEqual(self.rechazadas, [])

    def test_mover_sin_hsi_activo_responde_hsi_desactivado(self):
        _env.set_env(self, TURNOS_SKIP_HSI="1")
        out = hsi_resolver.aplicar_resolucion("mover", [1])
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "hsi_desactivado")


class TestResolucionesPara(unittest.TestCase):
    def test_la_primera_resolucion_es_la_recomendada_y_mover_encabeza(self):
        rs = hsi_resolver.resoluciones_para("profesional")
        self.assertEqual([r["accion"] for r in rs], ["mover", "sobreturno", "rechazar"])
        self.assertEqual(rs[0]["label"], "Mover al primer horario libre")

    def test_sin_hsi_activo_solo_queda_rechazar(self):
        rs = hsi_resolver.resoluciones_para("sin_hueco", activo=False)
        self.assertEqual([r["accion"] for r in rs], ["rechazar"])

    def test_un_cluster_no_reparable_ofrece_solo_rechazar(self):
        rs = hsi_resolver.resoluciones_para("remoto_cancelado")
        self.assertEqual([r["accion"] for r in rs], ["rechazar"])



class TestResolverDrift(RuteoBase):
    """_resolver_drift: reintenta/revincula una solicitud cuando HSI pierde el turno."""

    def test_resolver_drift_checkpoint_drift_reintenta(self):
        """Turno aprobada futuro con hsi_checkpoint_drift se reintenta."""
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(reason="hsi_checkpoint_drift")])
        self.patch_db("get_booking_request", lambda sid: solicitud())

        # Patch the drift resolver's dependencies
        self.patch_sync("enqueue_reassign", lambda row, when: {"id": 5})
        self.patch_db("hsi_outbox_reset", lambda i: True)
        self.patch_sync("replay_item", lambda i: {"state": "confirmed"})

        resumen = hsi_resolver.resolver_pass(now=AHORA)
        self.assertEqual(resumen["resueltos"], 1)
        self.assertIn("reintentado", self.acciones())

class TestPanel(ResolverBase):
    def test_panel_arma_una_fila_por_conflicto_con_sus_resoluciones(self):
        _env.set_env(self, TURNOS_SKIP_HSI=None)   # HSI activo: mover disponible
        self.patch_db("hsi_metrics", lambda: {
            "futuras": 20, "futuras_ok": 19, "total": 50, "total_ok": 40,
            "outbox": {"confirmed": 40, "conflict": 2}})
        self.patch_db("hsi_open_conflicts", lambda limit=500: [
            conflicto(id=1, entity_id="5", reason="no free slot"),
            conflicto(id=2, entity_id="6",
                      reason="no diary matches 'Rayos'; available specialties: X"),
            conflicto(id=3, entity_id="7", reason="remote_appointment_cancelled_or_blocked"),
        ])
        self.patch_db("get_booking_request",
                      lambda sid: solicitud(id=sid, nombre="Ana", apellido="N%d" % sid,
                                            medico_asignado="Dra. Martin Galvan"))
        self.patch_db("hsi_recently_resolved", lambda limit=40: [
            {"id": 9, "entity_id": "8", "resolved_at": AHORA,
             "resolution": {"accion": "reasignado", "texto": "quedó con Suárez"}},
            {"id": 10, "entity_id": "9", "resolved_at": AHORA,
             "resolution": {"accion": "repetido", "texto": "repetía"}},
        ])
        panel = hsi_resolver.estado_panel()
        self.assertEqual(panel["metricas"]["pct_futuras"], 95)
        self.assertEqual(panel["abiertos"], 3)
        self.assertNotIn("clusters", panel)
        c = panel["conflictos"]
        self.assertEqual(len(c), 3)
        # cada fila trae los campos de la tabla (fecha/paciente/médico/problema)
        self.assertEqual(c[0]["paciente"], "Ana N5")
        self.assertEqual(c[0]["medico"], "Dra. Martin Galvan")
        self.assertTrue(c[0]["fecha"].startswith("2026-08-03"))
        # sin_hueco recomienda mover; remoto_cancelado sólo permite rechazar
        sin_hueco = next(x for x in c if x["conflicto_id"] == 1)
        self.assertEqual(sin_hueco["resoluciones"][0]["accion"], "mover")
        remoto = next(x for x in c if x["conflicto_id"] == 3)
        self.assertEqual([r["accion"] for r in remoto["resoluciones"]], ["rechazar"])
        # la limpieza interna (repetidos/obsoletos) no se le muestra al personal
        self.assertEqual([r["texto"] for r in panel["resueltos"]], ["quedó con Suárez"])

    def test_panel_deduplica_resueltos_con_el_mismo_texto(self):
        self.patch_db("hsi_metrics", lambda: {"futuras": 1, "futuras_ok": 1,
                                               "total": 1, "total_ok": 1, "outbox": {}})
        self.patch_db("hsi_open_conflicts", lambda limit=500: [])
        self.patch_db("hsi_recently_resolved", lambda limit=40: [
            {"id": 1, "entity_id": "5", "resolved_at": AHORA,
             "resolution": {"accion": "revinculado", "texto": "El turno 12 quedó vinculado"}},
            {"id": 2, "entity_id": "6", "resolved_at": AHORA,
             "resolution": {"accion": "revinculado", "texto": "El turno 12 quedó vinculado"}},
        ])
        panel = hsi_resolver.estado_panel()
        self.assertEqual(len(panel["resueltos"]), 1)

    def test_panel_sin_turnos_futuros_no_divide_por_cero(self):
        self.patch_db("hsi_metrics", lambda: {"futuras": 0, "futuras_ok": 0,
                                               "total": 0, "total_ok": 0, "outbox": {}})
        self.patch_db("hsi_open_conflicts", lambda limit=500: [])
        self.patch_db("hsi_recently_resolved", lambda limit=40: [])
        panel = hsi_resolver.estado_panel()
        self.assertIsNone(panel["metricas"]["pct_futuras"])
        self.assertEqual(panel["conflictos"], [])


if __name__ == "__main__":
    unittest.main()
