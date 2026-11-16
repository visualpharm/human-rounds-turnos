"""Registry rewire (2026-08-20): the five capability consumers resolve their
connector through `connectors.adapter_for()` — proven hermetically (no DB,
no network, no ambient config): `adapter_for` is monkeypatched to a fake ABC
instance and the REAL consumer function runs, asserting (a) the fake gets the
consumer's exact arguments and (b) the documented degrade when nothing is
routed (`adapter_for -> None`) or the connector raises ProviderUnavailable —
never an exception reaching the caller.

  B1 identity_verification — cuentas.accounts._login_test_identity_core
  B2 insurance_lookup      — cuentas.accounts._norm_coverage
  B3 license_lookup        — modules.turnos.booking_queue._admin_refeps_core
  B4 slot_source           — patient_web.patient_site._slot_source_info /
                              _next_appointment_best
  B5 clinical_history_lookup — ai.chart_summary._historia_clinica /
                              _fuentes_linea (a fan-out over every discovered
                              archive, through connectors._instantiate_adapter)
"""
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import connectors  # noqa: E402
from app.connectors.msd import msd_data  # noqa: E402
from app.patient_web import site_availability, site_base, site_catalog, site_doctors  # noqa: E402
from test_connectors import TempAdapterConnectorsBase, _write_adapter, _write_manifest  # noqa: E402


class _FakeAdapter:
    """Records every call; answers from `answers` (a value, or an exception
    instance to raise). Deliberately NOT a subclass of any ABC: the consumers
    must only rely on the method shape, never on isinstance."""

    def __init__(self, **answers):
        self.calls = []
        self.answers = answers

    def _answer(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        value = self.answers.get(name)
        if isinstance(value, BaseException):
            raise value
        return value

    def authenticate(self, dni, **kwargs):
        return self._answer("authenticate", dni, **kwargs)

    def cached(self, national_id):
        return self._answer("cached", national_id)

    def search_by_license(self, license_number):
        return self._answer("search_by_license", license_number)

    def slots_for_service(self, servicio):
        return self._answer("slots_for_service", servicio)


def _route(adapter):
    """Patch connectors.adapter_for -> adapter for the calling test."""
    return mock.patch.object(connectors, "adapter_for", lambda capability: adapter)


# ---- B1 identity_verification ---------------------------------------------------

class TestIdentityVerificationLogin(unittest.TestCase):
    ACCOUNT = {"dni": "30111222", "nombre": "Real", "apellido": "Account",
               "nacimiento": "1980-01-01", "sexo": "F", "contacto": "wa:1",
               "email": "r@example.invalid", "domicilio": {"texto": "Calle 1", "localidad": "Pinamar"}}

    def setUp(self):
        from app.cuentas import accounts
        self.accounts = accounts
        for target, value in (
            ("_test_auth_enabled", lambda headers, client_host: True),
            ("_persist_provider_identity_core", self._persist),
        ):
            patcher = mock.patch.object(accounts, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.persisted = []

    def _persist(self, persona, provider, ip=""):
        from app.cuentas.native_result import Result
        self.persisted.append((persona, provider))
        return Result.json({"ok": True}, status=200)

    def _login(self, adapter, account=ACCOUNT, body=None):
        with mock.patch.object(self.accounts.store, "get_user", lambda dni: account), _route(adapter):
            return self.accounts._login_test_identity_core(
                {}, "127.0.0.1",
                body or {"provider": "renaper", "dni": "30111222"},
                ip="127.0.0.1",
            )

    def test_existing_account_civil_data_reaches_the_adapter(self):
        persona = {"dni": "30111222", "nombre": "Real", "apellido": "Account", "sexo": "F"}
        adapter = _FakeAdapter(authenticate=persona)
        result = self._login(adapter)
        self.assertEqual(result.status, 200)
        self.assertEqual(self.persisted, [(persona, "renaper")])
        (name, args, kwargs), = adapter.calls
        self.assertEqual(name, "authenticate")
        self.assertEqual(args, ("30111222",))
        self.assertEqual(kwargs["provider"], "stub")
        # existing_user is the account's OWN civil data — the anti-clobber path.
        self.assertEqual(kwargs["existing_user"], {
            "nombre": "Real", "apellido": "Account", "nacimiento": "1980-01-01",
            "sexo": "F", "contacto": "wa:1", "email": "r@example.invalid",
            "domicilio": "Calle 1", "localidad": "Pinamar",
        })

    def test_no_account_passes_existing_user_none(self):
        adapter = _FakeAdapter(authenticate={"dni": "30111222", "sexo": "X"})
        self._login(adapter, account=None)
        self.assertIsNone(adapter.calls[0][2]["existing_user"])

    def test_no_connector_routed_is_404_not_an_exception(self):
        result = self._login(None)
        self.assertEqual(result.status, 404)
        self.assertEqual(result.json_body, {"error": "identity_verification_no_disponible"})
        self.assertEqual(self.persisted, [])

    def test_invalid_identity_is_still_400(self):
        adapter = _FakeAdapter(authenticate=connectors.InvalidIdentity("dni_invalido"))
        result = self._login(adapter)
        self.assertEqual(result.status, 400)
        self.assertEqual(result.json_body, {"error": "dni_invalido"})

    def test_identity_not_found_is_the_same_client_safe_400(self):
        adapter = _FakeAdapter(authenticate=connectors.IdentityNotFound("missing"))
        result = self._login(adapter)
        self.assertEqual(result.status, 400)
        self.assertEqual(result.json_body, {"error": "dni_invalido"})
        self.assertEqual(self.persisted, [])

    def test_provider_unavailable_is_503(self):
        adapter = _FakeAdapter(authenticate=connectors.ProviderUnavailable("down"))
        result = self._login(adapter)
        self.assertEqual(result.status, 503)
        self.assertEqual(result.json_body, {"error": "identity_verification_no_disponible"})
        self.assertEqual(self.persisted, [])

    def test_gate_still_wins_before_the_registry_is_consulted(self):
        adapter = _FakeAdapter(authenticate={"dni": "30111222"})
        with mock.patch.object(
            self.accounts, "_test_auth_enabled",
            lambda headers, client_host: False,
        ):
            result = self._login(adapter)
        self.assertEqual(result.status, 404)
        self.assertEqual(result.json_body, {"error": "test_auth_no_disponible"})
        self.assertEqual(adapter.calls, [])


# ---- B2 insurance_lookup -----------------------------------------------------------

class TestInsuranceLookupCoverageAttach(unittest.TestCase):
    BODY = {"obras_sociales": [{"nombre": "IOMA", "afiliado": "77"}, {"nombre": "OSDE"}]}

    def setUp(self):
        from app.cuentas import accounts
        self.accounts = accounts

    def test_cached_coverages_attach_as_puco_with_rnos(self):
        adapter = _FakeAdapter(cached=[{"nombre": "IOMA", "rnos": "1-2000-1"}])
        with _route(adapter):
            out = self.accounts._norm_coverage(self.BODY, dni="12345670")
        self.assertEqual(adapter.calls, [("cached", ("12345670",), {})])
        self.assertEqual(out["obras_sociales"], [
            {"nombre": "IOMA", "origen": "puco", "rnos": "1-2000-1", "afiliado": "77"},
            {"nombre": "OSDE", "origen": "declarada"},
        ])

    def test_no_connector_routed_means_everything_is_declarada(self):
        with _route(None):
            out = self.accounts._norm_coverage(self.BODY, dni="12345670")
        self.assertEqual([o["origen"] for o in out["obras_sociales"]], ["declarada", "declarada"])

    def test_a_failing_connector_degrades_to_declarada(self):
        adapter = _FakeAdapter(cached=RuntimeError("boom"))
        with _route(adapter):
            out = self.accounts._norm_coverage(self.BODY, dni="12345670")
        self.assertEqual([o["origen"] for o in out["obras_sociales"]], ["declarada", "declarada"])

    def test_without_a_dni_the_registry_is_not_consulted(self):
        calls = []
        with mock.patch.object(connectors, "adapter_for", lambda cap: calls.append(cap)):
            self.accounts._norm_coverage(self.BODY, dni=None)
        self.assertEqual(calls, [])


# ---- B3 license_lookup -------------------------------------------------------------

class TestLicenseLookupAdminCheck(unittest.TestCase):
    RECORD = {"apellido": "Gomez", "nombre": "Ana", "profesiones": [
        {"profesionReferencia": "Medico", "refepsEspecialidad": "Cardiologia",
         "matriculas": [{"matricula": "12345", "origenEmite": "MN", "provinciaMatricula": "CABA",
                         "situacionMatricula": "Habilitado", "tipoSancion": None}]}]}

    def setUp(self):
        from app.modules.turnos import booking_queue
        self.queue = booking_queue
        self.saved = []

        def refeps_set(matricula, items, ok=True):
            self.saved.append((matricula, items, ok))
            return {"checked": datetime(2026, 8, 20, 12, 0, 0)}

        self.stale = None
        for target, value in (("refeps_get", lambda matricula: self.stale), ("refeps_set", refeps_set)):
            p = mock.patch.object(self.queue.store, target, value)
            p.start()
            self.addCleanup(p.stop)

    def _check(self, adapter, matricula="M.N. 12.345", medico="Dra. Gómez", force=False):
        qs = {"matricula": [matricula], "medico": [medico]}
        if force:
            qs["force"] = ["1"]  # the admin's refresh button: skip the fresh cache row
        with _route(adapter):
            return self.queue._admin_refeps_core(qs)

    def test_live_lookup_goes_through_the_adapter_and_is_cached(self):
        adapter = _FakeAdapter(search_by_license=[self.RECORD])
        status, payload = self._check(adapter)
        self.assertEqual(adapter.calls, [("search_by_license", ("12345",), {})])
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"], "live")
        self.assertTrue(payload["found"])
        self.assertEqual(payload["match"]["apellido"], "Gomez")
        self.assertEqual(self.saved, [("12345", [self.RECORD], True)])

    def test_no_connector_routed_is_the_stale_cache_or_502_branch(self):
        status, payload = self._check(None)
        self.assertEqual(status, 502)
        self.assertIn("license_lookup", payload["error"])
        self.assertEqual(self.saved, [])
        # With a cached row on file, a forced refresh that finds no connector
        # prefers stale data over nothing at all.
        self.stale = {"items": [self.RECORD], "checked": datetime(2026, 1, 1)}
        status, payload = self._check(None, force=True)
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"], "cache-stale")
        self.assertTrue(payload["found"])
        self.assertIn("license_lookup", payload["error"])

    def test_provider_unavailable_is_the_same_branch(self):
        adapter = _FakeAdapter(search_by_license=connectors.ProviderUnavailable("refeps down"))
        status, payload = self._check(adapter)
        self.assertEqual(status, 502)
        self.assertEqual(payload, {"error": "refeps down"})
        self.stale = {"items": [], "checked": datetime(2026, 1, 1)}
        _status, payload = self._check(adapter, force=True)
        self.assertEqual(payload["source"], "cache-stale")
        self.assertEqual(payload["error"], "refeps down")


# ---- B4 slot_source ----------------------------------------------------------------

class TestSlotSourceWizard(unittest.TestCase):
    SERVICE = {"id": "svc-x", "modo": "turno", "cluster": "no-such-cluster",
               "centro": "no-such-centro"}

    def setUp(self):
        patcher = mock.patch.object(site_catalog, "_doctors", lambda *a, **kw: [])
        patcher.start()
        self.addCleanup(patcher.stop)
        # These 2099 connector fixtures are inside the public booking window,
        # not arbitrary far-future offers exempt from the real horizon policy.
        clock = mock.patch.object(site_base, "_now_art", return_value=datetime(2099, 1, 1, 0))
        clock.start(); self.addCleanup(clock.stop)

    def test_slots_come_from_the_adapter_with_the_service_dict(self):
        info = {"medicos": [{"medico": "Dr X", "turnos": [{"dia": "2099-01-02", "hora": "10:00"},
                                                          {"dia": "2099-01-01", "hora": "09:30"}]}]}
        adapter = _FakeAdapter(slots_for_service=info)
        with _route(adapter):
            clean = site_availability._slot_source_info(self.SERVICE)
            self.assertEqual(clean["medicos"][0]["turnos"], info["medicos"][0]["turnos"])
            self.assertEqual(clean["medicos"][0]["proximo"],
                             {"dia": "2099-01-01", "hora": "09:30"})
            best = site_availability._next_appointment_best(self.SERVICE, ocupados=set())
        self.assertEqual(adapter.calls[0], ("slots_for_service", (self.SERVICE,), {}))
        self.assertEqual(best, ("2099-01-01", "09:30"))

    def test_no_connector_routed_is_empty_and_the_cell_falls_back(self):
        with _route(None):
            self.assertEqual(site_availability._slot_source_info(self.SERVICE), {})
            self.assertIsNone(site_availability._next_appointment_best(self.SERVICE, ocupados=set()))

    def test_provider_unavailable_is_empty(self):
        adapter = _FakeAdapter(slots_for_service=connectors.ProviderUnavailable("webturno down"))
        with _route(adapter):
            self.assertEqual(site_availability._slot_source_info(self.SERVICE), {})
            self.assertIsNone(site_availability._next_appointment_best(self.SERVICE, ocupados=set()))

    def test_explicit_webturno_selection_reaches_the_patient_consumer(self):
        from app.connectors.webturno import webturno

        class FakeWebturno:
            def slots(self, clinic, idme):
                self.seen = (clinic, idme)
                return [{"fecha": "2099-01-03T08:45:00", "idtur": "slot-1"}]

        fake = FakeWebturno()
        service = dict(self.SERVICE, clinic_token="clinic", idme="doctor",
                       medico="Doctor Example")

        def fake_get(key, default=None, legacy=None):
            if key == "CONNECTOR_SLOT_SOURCE":
                return "webturno"
            return default

        with mock.patch.object(connectors.config, "get", fake_get), \
                mock.patch.object(webturno, "Webturno", lambda: fake):
            best = site_availability._next_appointment_best(service, ocupados=set())
        self.assertEqual(fake.seen, ("clinic", "doctor"))
        self.assertEqual(best, ("2099-01-03", "08:45"))

    def test_a_none_answer_is_treated_as_empty(self):
        adapter = _FakeAdapter(slots_for_service=None)
        with _route(adapter):
            self.assertEqual(site_availability._slot_source_info(self.SERVICE), {})

    def test_mixed_connector_slots_keep_only_future_values(self):
        now = datetime(2026, 9, 5, 9, 0)
        info = {"medicos": [{"medico": "Dr X", "turnos": [
            {"dia": "2026-07-17", "hora": "09:40"},
            {"dia": "2026-09-05", "hora": "08:40"},
            {"dia": "2026-09-05", "hora": "09:00"},
            {"dia": "not-a-date", "hora": "10:00"},
            {"dia": "2026-09-05", "hora": "10:00"},
            {"dia": "2026-09-07", "hora": "09:30"},
        ], "proximo": {"dia": "2026-07-17", "hora": "09:40"}}]}
        adapter = _FakeAdapter(slots_for_service=info)
        with mock.patch.object(site_base, "_now_art", return_value=now), _route(adapter):
            clean = site_availability._slot_source_info(self.SERVICE)
            self.assertEqual(
                [(t["dia"], t["hora"]) for t in clean["medicos"][0]["turnos"]],
                [("2026-09-05", "10:00"), ("2026-09-07", "09:30")],
            )
            self.assertEqual(clean["medicos"][0]["proximo"],
                             {"dia": "2026-09-05", "hora": "10:00"})
            self.assertEqual(site_availability._next_appointment_best(self.SERVICE, ocupados=set()),
                             ("2026-09-05", "10:00"))
        self.assertEqual(info["medicos"][0]["proximo"],
                         {"dia": "2026-07-17", "hora": "09:40"})
        self.assertEqual(info["medicos"][0]["turnos"][2],
                         {"dia": "2026-09-05", "hora": "09:00"})

    def test_proximo_only_connector_slot_is_normalized_without_mutating_source(self):
        now = datetime(2026, 9, 5, 9, 0)
        info = {"medicos": [{"medico": "Dr X", "proximo": {
            "dia": "2026-09-05", "hora": "10:00"}}]}
        with mock.patch.object(site_base, "_now_art", return_value=now), _route(
                _FakeAdapter(slots_for_service=info)):
            clean = site_availability._slot_source_info(self.SERVICE)
        self.assertEqual(clean["medicos"][0]["turnos"], [{"dia": "2026-09-05", "hora": "10:00"}])
        self.assertEqual(clean["medicos"][0]["proximo"], {"dia": "2026-09-05", "hora": "10:00"})
        self.assertNotIn("turnos", info["medicos"][0])

    def test_all_past_connector_slots_fall_back_to_future_local_agenda(self):
        now = datetime(2026, 9, 5, 9, 0)
        service = {
            "id": "svc-x", "modo": "turno", "cluster": "no-such-cluster",
            "centro": "no-such-centro",
            "agenda": {"dias": [1], "desde": "08:00", "hasta": "09:00", "cada_min": 20},
        }
        adapter = _FakeAdapter(slots_for_service={"medicos": [{"turnos": [
            {"dia": "2026-07-17", "hora": "09:40"},
            {"dia": "2026-09-05", "hora": "08:40"},
        ]}]})
        with mock.patch.object(site_base, "_now_art", return_value=now), _route(adapter):
            self.assertEqual(site_availability._slot_source_info(service), {})
            self.assertEqual(site_availability._next_appointment_best(service, ocupados=set()),
                             ("2026-09-07", "08:00"))
            offered = site_availability._appointments_for_service(service)
        self.assertEqual(offered["fuente"], "agenda")
        slots = [(t["dia"], t["hora"]) for t in offered["medicos"][0]["turnos"]]
        self.assertEqual(slots[:3], [
            ("2026-09-07", "08:00"), ("2026-09-07", "08:20"), ("2026-09-07", "08:40"),
        ])
        self.assertTrue(all(day > now.date().isoformat() for day, _hour in slots))

    def test_doctor_listing_ignores_past_msd_matches_before_local_fallback(self):
        now = datetime(2026, 9, 5, 9, 0)
        service = {
            "id": "svc-x", "modo": "turno", "cluster": "no-such-cluster",
            "centro": "no-such-centro",
            "agenda": {"dias": [1], "desde": "08:00", "hasta": "09:00", "cada_min": 20},
        }
        doctor = {"nombre": "Dra. X", "centro": "no-such-centro", "cluster": "no-such-cluster"}
        def stale_only_when_accepted(*_args, **kwargs):
            self.assertFalse(kwargs["accept"]("2026-07-17", "09:40"))
            return None

        with (
            mock.patch.object(site_base, "_now_art", return_value=now),
            mock.patch.object(site_doctors, "_doctor_service", return_value=service),
            mock.patch.object(site_catalog, "_services", return_value=[service]),
            mock.patch.object(msd_data, "proximo_own_anywhere",
                              side_effect=stale_only_when_accepted),
            mock.patch.object(msd_data, "proximo_for_medico",
                              side_effect=stale_only_when_accepted),
            _route(None),
        ):
            next_slot = site_availability._doctor_next(doctor)
        self.assertEqual((next_slot["dia"], next_slot["hora"]), ("2026-09-07", "08:00"))


# ---- B5 clinical_history_lookup ------------------------------------------------------

_HISTORY_ADAPTER = '''
from app.connectors import ClinicalHistoryLookupConnector, ProviderUnavailable


class Archive(ClinicalHistoryLookupConnector):
    def lookup_by_dni(self, dni):
        if MODE == "down":
            raise ProviderUnavailable("not configured")
        if MODE == "boom":
            raise KeyError("unexpected")
        return [{"sistema": SYSTEM, "fecha": FECHA, "tipo": "consulta", "texto": "x " + dni}]
'''


class TestClinicalHistoryFanOut(TempAdapterConnectorsBase):
    def setUp(self):
        super().setUp()
        from app.ai import chart_summary
        self.cs = chart_summary
        self.logged = []
        patcher = mock.patch.object(chart_summary, "_log", lambda msg: self.logged.append(msg))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _archive(self, connector_id, mode="ok", fecha="2021-01-01"):
        src = (f'MODE = {mode!r}\nSYSTEM = {connector_id!r}\nFECHA = {fecha!r}\n'
               + _HISTORY_ADAPTER)
        _write_adapter(self._tmp, connector_id, connectors.CLINICAL_HISTORY_LOOKUP, src)

    def test_in_tree_order_is_local_history_then_sorted(self):
        # The real registry: exactly the historical hard-coded order.
        connectors.ROOT = self._orig_root
        self.assertEqual(self.cs._history_connector_ids(),
                         ["local_history", "alepo", "marqui", "patient_uploads"])

    def test_discovery_order_pins_local_history_first(self):
        _write_manifest(self._tmp, "tmpconn_b", connectors.CLINICAL_HISTORY_LOOKUP)
        _write_manifest(self._tmp, "local_history", connectors.CLINICAL_HISTORY_LOOKUP)
        _write_manifest(self._tmp, "tmpconn_a", connectors.CLINICAL_HISTORY_LOOKUP)
        _write_manifest(self._tmp, "tmpconn_other", connectors.SLOT_SOURCE)
        self.assertEqual(self.cs._history_connector_ids(),
                         ["local_history", "tmpconn_a", "tmpconn_b"])

    def test_every_discovered_archive_answers_and_unavailable_stays_silent(self):
        self._archive("tmpconn_b", fecha="2020-05-05")
        self._archive("tmpconn_a", fecha="2022-05-05")
        self._archive("tmpconn_down", mode="down")
        entradas, estados = self.cs._historia_clinica("12345678")
        self.assertEqual(list(estados.items()), [
            ("tmpconn_a", "ok"), ("tmpconn_b", "ok"), ("tmpconn_down", "no_disponible")])
        # Entries merged and sorted by fecha, whichever archive they came from.
        self.assertEqual([e["sistema"] for e in entradas], ["tmpconn_b", "tmpconn_a"])
        self.assertEqual(self.logged, [])  # ProviderUnavailable is silent by contract
        # Generic labels for ids this file never heard of — no KeyError.
        partes = self.cs._fuentes_linea([], [], entradas, estados)
        self.assertIn("Tmpconn A: 1 registros 2022–2022", partes)
        self.assertIn("Tmpconn B: 1 registros 2020–2020", partes)
        self.assertIn("Tmpconn Down: no disponible", partes)

    def test_an_unexpected_failure_or_missing_adapter_is_logged_not_raised(self):
        self._archive("tmpconn_boom", mode="boom")
        _write_manifest(self._tmp, "tmpconn_noadapter", connectors.CLINICAL_HISTORY_LOOKUP)
        entradas, estados = self.cs._historia_clinica("12345678")
        self.assertEqual(entradas, [])
        self.assertEqual(estados, {"tmpconn_boom": "no_disponible",
                                   "tmpconn_noadapter": "no_disponible"})
        self.assertEqual(len(self.logged), 2)

    def test_curated_labels_and_entry_labels_still_win(self):
        estados = {"local_history": "no_disponible", "alepo": "ok", "marqui": "no_disponible"}
        historia = [{"sistema": "alepo", "fecha": "2021-02-02"},
                    {"sistema": "custom_archive", "sistema_label": "Custom", "fecha": "2019-01-01"}]
        partes = self.cs._fuentes_linea([], [], historia, estados)
        self.assertEqual(partes[:4], ["Alepo: 1 registros 2021–2021",
                                      "Custom: 1 registros 2019–2019",
                                      "Archivo clínico: no disponible",
                                      "Marqui: no disponible"])


if __name__ == "__main__":
    unittest.main()
