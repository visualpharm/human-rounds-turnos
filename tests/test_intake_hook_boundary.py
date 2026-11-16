"""test_intake_hook_boundary.py — the 2026-08-16 hook-boundary slice's own
gate: the four callers that used to couple to intake inline behind their
own `app_modules.is_enabled("intake")` gate — turnos'
booking_requests/doctor_appointments via direct `modules.intake` imports,
admin_panel's doctor_patients and ai's chart_summary via raw `core.db`
interview reads (never an intake import) — now talk to it ONLY through
named hooks (modules/intake/hooks.py's REGISTRY, registered by
modules/intake/app.py's startup). Two halves:

  1. Dependency direction (static): none of those files imports
     modules.intake or hand-rolls its own `app_modules.is_enabled("intake")`
     gate anymore — the boundary is named hooks, nothing else.
  2. Absence/presence (runtime): with intake disabled every wrapper keeps
     returning its old best-effort fallback (and never touches the DB);
     with intake enabled the real registered bodies answer, including the
     internal failure granularity the direct imports had (an error inside
     a body degrades exactly one piece of data, never the caller).

No network, no PHI, no DATABASE_URL: config is the in-memory store from
test_module_hooks.py's pattern, and every db touch is a mock.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_intake_hook_boundary.py"
"""

import ast
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
from app.core import app_modules  # noqa: E402

from app.auth import turnstile  # noqa: E402
turnstile.ENV_PATH = ""   # do not read the real .env (same trick as the other tests)
config.ENV_PATH = ""      # same: do not read the real .env of the dev checkout


# The files this slice decoupled from modules/intake.
# (booking_requests.py was cut by action on 2026-11-06, TASK-60: its split modules hold the same code.)
_DECOUNPLED = (
    "app/modules/turnos/booking_requests.py",
    "app/modules/turnos/booking_create.py",
    "app/modules/turnos/booking_referrals.py",
    "app/modules/turnos/booking_queue.py",
    "app/modules/turnos/booking_decisions.py",
    "app/modules/turnos/booking_patient.py",
    "app/modules/turnos/booking_capacity.py",
    "app/modules/turnos/booking_emr.py",
    "app/modules/turnos/panel_settings.py",
    "app/modules/turnos/doctor_appointments.py",
    "app/admin_panel/doctor_patients.py",
    "app/ai/chart_summary.py",
)


class TestIntakeHookBoundaryDirection(unittest.TestCase):
    """Static half: the four callers must stay on the hook side of the
    boundary — no `modules.intake` import of any shape, and no private
    enablement gate either (both were the leak this slice removed)."""

    def test_no_caller_imports_modules_intake(self):
        offenders = []
        for rel in _DECOUNPLED:
            path = os.path.join(ROOT, rel)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "app.modules.intake" or alias.name.startswith("app.modules.intake."):
                            offenders.append("%s imports %r" % (rel, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.module and (node.module == "app.modules.intake"
                                        or node.module.startswith("app.modules.intake.")):
                        offenders.append("%s imports from %r" % (rel, node.module))
        self.assertEqual(
            offenders, [],
            "a decoupled caller imports modules.intake directly — use "
            "app_modules.hooks.call(<name>, ...) (modules/intake/hooks.py):\n  "
            + "\n  ".join(offenders))

    def test_no_caller_gates_on_intake_enablement_itself(self):
        offenders = []
        for rel in _DECOUNPLED:
            path = os.path.join(ROOT, rel)
            with open(path, encoding="utf-8") as f:
                source = f.read()
            if 'is_enabled("intake")' in source or "is_enabled('intake')" in source:
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            "a decoupled caller hand-rolls its own intake enablement check — "
            "hooks.call() already filters by the module being enabled right "
            "now, so a private gate is dead weight (and a second place to "
            "forget): " + ", ".join(offenders))


class _FakeConfigStore(unittest.TestCase):
    """Same in-memory config + lifecycle-state save/restore pattern as
    test_module_hooks.py, so this file runs with no reachable DB."""

    def setUp(self):
        config._reset_cache_para_tests()
        self._store = {}

        def fake_overrides():
            return dict(self._store)

        def fake_guardar(overrides):
            for k, v in overrides.items():
                if v in (None, ""):
                    self._store.pop(k, None)
                else:
                    self._store[k] = v
            config._db_cache["v"] = None
            config._db_cache["t"] = 0.0
            return dict(self._store)

        self._p1 = patcher = mock.patch.object(config, "_db_overrides", side_effect=fake_overrides)
        self._p2 = mock.patch.object(config, "guardar", side_effect=fake_guardar)
        patcher.start()
        self._p2.start()
        self._orig_started = set(app_modules._STARTED)
        self._orig_broken = dict(app_modules._RUNTIME_BROKEN)
        # `_STARTED` and the hook registry are one lifecycle state: restoring
        # only the former makes an already-started module permanently look as
        # though it had no hooks for the rest of full-suite discovery.
        self._orig_hooks = {
            name: list(entries)
            for name, entries in app_modules.hooks._registry.items()
        }
        app_modules.hooks.clear()
        app_modules._STARTED.discard("intake")
        app_modules._RUNTIME_BROKEN.pop("intake", None)

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        config._reset_cache_para_tests()
        app_modules.hooks.clear()
        app_modules.hooks._registry.update({
            name: list(entries) for name, entries in self._orig_hooks.items()
        })
        app_modules._STARTED.clear()
        app_modules._STARTED.update(self._orig_started)
        app_modules._RUNTIME_BROKEN.clear()
        app_modules._RUNTIME_BROKEN.update(self._orig_broken)

    def _set_enabled(self, raw):
        self._store["ENABLED_MODULES"] = raw
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0


class TestIntakeHookRegistration(_FakeConfigStore):
    """intake's startup() registers the whole integration table — the proof
    the named surface the callers invoke actually exists."""

    def test_startup_registers_every_registry_hook_plus_the_reminder(self):
        self._set_enabled("turnos,intake")
        app_modules.ensure_started()
        from app.modules.intake import hooks as intake_hooks
        for name in [n for n, _fn in intake_hooks.REGISTRY] + ["interview_reminder"]:
            self.assertIn(name, app_modules.hooks._registry,
                          "startup() did not register %r" % name)
            owners = [mid for mid, _fn in app_modules.hooks._registry[name]]
            self.assertIn("intake", owners, "%r not owned by intake" % name)


class TestAbsenceIntakeDisabled(_FakeConfigStore):
    """Module off → every wrapper returns its old fallback AND the DB layer
    is never reached (the hook body is the only thing that reads it)."""

    def setUp(self):
        super().setUp()
        self._set_enabled("turnos")  # intake NOT enabled
        from app.modules.turnos import booking_requests, doctor_appointments
        from app.admin_panel import doctor_patients
        from app.ai import chart_summary
        self._booking = booking_requests
        self._appointments = doctor_appointments
        self._patients = doctor_patients
        self._chart = chart_summary

    def test_doctor_appointments_fallbacks(self):
        medico = {"id": "m1", "entrevista_habilitada": True}
        self.assertFalse(self._appointments._intake_on_for(medico))
        self.assertEqual(
            self._appointments._entrevistas_sin_turno("m1", medico, "2026-08-16"), [])
        self.assertIsNone(self._appointments._entrevista_config("m1", medico))
        turnos = [{"id": 10}]
        self._appointments._add_triage_by_request(turnos, medico)
        self.assertNotIn("entrevista", turnos[0])
        grupos = [{"medico_id": "m1", "turnos": [{"id": 10}]}]
        self._appointments._add_triage(grupos, "2026-08-16")
        self.assertNotIn("entrevista", grupos[0]["turnos"][0])

    def test_booking_requests_fallbacks(self):
        self.assertIsNone(self._booking._entrevista_field(7, "m1"))
        self.assertIsNone(self._booking._entrevista_field(7, "m1", incluir_estado=True))
        self.assertIsNone(self._booking._entrevista_patient_token(7))

    def test_doctor_patients_and_chart_summary_fallbacks(self):
        self.assertEqual(self._patients._entrevistas_de_doctor("m1"), [])
        self.assertEqual(self._chart._entrevistas("30111111"), [])

    def test_disabled_means_no_db_read_at_all(self):
        with mock.patch("app.core.db_slots.interviews_by_dni") as by_dni, \
                mock.patch("app.core.db_clinical.interviews_of_doctor") as of_doctor, \
                mock.patch("app.core.db_clinical.interviews_for_day") as for_day:
            self._chart._entrevistas("30111111")
            self._patients._entrevistas_de_doctor("m1")
            self._appointments._add_triage(
                [{"medico_id": "m1", "turnos": [{"id": 1}]}], "2026-08-16")
            self._booking._entrevista_patient_token(7)
            by_dni.assert_not_called()
            of_doctor.assert_not_called()
            for_day.assert_not_called()


class TestPresenceIntakeEnabled(_FakeConfigStore):
    """Module on → the real registered bodies answer through the wrappers,
    with the same values (and the same internal failure granularity) the
    direct imports produced. Every db read is a mock; no network, no PHI."""

    def setUp(self):
        super().setUp()
        self._set_enabled("turnos,intake")

    def test_enabled_for_follows_the_doctor_override(self):
        from app.modules.turnos import doctor_appointments
        self.assertTrue(doctor_appointments._intake_on_for({"entrevista_habilitada": True}))
        self.assertFalse(doctor_appointments._intake_on_for({"entrevista_habilitada": False}))
        # No override -> the system default (INTAKE_ENABLED, OFF by default
        # since 2026-10-21): a fresh installation offers nothing until
        # someone turns it on explicitly.
        self.assertFalse(doctor_appointments._intake_on_for({"id": "m1"}))
        self._store["INTAKE_ENABLED"] = "1"
        config._db_cache["v"] = None
        self.assertTrue(doctor_appointments._intake_on_for({"id": "m1"}))
        self._store["INTAKE_ENABLED"] = "0"
        config._db_cache["v"] = None
        self.assertFalse(doctor_appointments._intake_on_for({"id": "m1"}))
        # A doctor's own override still wins over the system default.
        self.assertTrue(doctor_appointments._intake_on_for({"entrevista_habilitada": True}))

    def test_entrevista_config_shape(self):
        from app.modules.turnos import doctor_appointments
        cfg = doctor_appointments._entrevista_config("m1", {"entrevista_habilitada": False})
        self.assertEqual(cfg, {
            "efectivo": False,
            "override": False,
            "cartel_url": "/api/admin/intake/door/m1",
        })
        # Toggle off is still computed (that's what lets the doctor turn it
        # on from #cola) — only a module-level off yields None (absence
        # tests above), never a toggle off.
        cfg = doctor_appointments._entrevista_config("m1", {})
        self.assertFalse(cfg["efectivo"])  # system default OFF (2026-10-21)
        self.assertIsNone(cfg["override"])
        self._store["INTAKE_ENABLED"] = "1"
        config._db_cache["v"] = None
        cfg = doctor_appointments._entrevista_config("m1", {})
        self.assertTrue(cfg["efectivo"])  # system explicitly ON
        self.assertIsNone(cfg["override"])

    def test_add_triage_attaches_state_for_enabled_doctors_only(self):
        from app.modules.turnos import doctor_appointments
        iv = {"id": 55, "request_id": 10, "triage": "ROJO",
              "summary": "Dolor de pecho", "status": "done", "reopened_at": None}
        grupos = [
            {"medico_id": "m1", "turnos": [{"id": 10}, {"id": 11}]},
            {"medico_id": "m2", "turnos": [{"id": 10}]},
        ]
        with mock.patch("app.core.db_clinical.interviews_for_day", return_value=[iv]) as for_day, \
                mock.patch.object(doctor_appointments, "doctor_of",
                                  side_effect=lambda mid: {"entrevista_habilitada": mid == "m1"}):
            doctor_appointments._add_triage(grupos, "2026-08-16")
        for_day.assert_called_once()
        self.assertEqual(grupos[0]["turnos"][0]["entrevista"], {
            "id": 55, "triage": "ROJO", "resumen": "Dolor de pecho",
            "estado": "done", "actualizada": False, "consulta_confirmada": False,
        })
        self.assertNotIn("entrevista", grupos[0]["turnos"][1])  # no match
        self.assertNotIn("entrevista", grupos[1]["turnos"][0])  # doctor off

    def test_add_triage_by_request_attaches_state(self):
        from app.modules.turnos import doctor_appointments
        turnos = [{"id": 10}, {"id": 11}]
        with mock.patch("app.core.db_clinical.interviews_by_request_ids",
                        return_value={10: {"id": 55, "request_id": 10, "triage": "VERDE",
                                           "summary": "Control", "status": "done",
                                           "reopened_at": "2026-08-15T10:00:00+00:00"}}):
            doctor_appointments._add_triage_by_request(
                turnos, {"entrevista_habilitada": True})
        self.assertEqual(turnos[0]["entrevista"]["triage"], "VERDE")
        self.assertTrue(turnos[0]["entrevista"]["actualizada"])
        self.assertNotIn("entrevista", turnos[1])

    def test_entrevistas_sin_turno_shapes_qr_rows(self):
        from app.modules.turnos import doctor_appointments
        creado = datetime(2026, 8, 16, 14, 30, tzinfo=timezone.utc)
        with mock.patch("app.core.db_clinical.interviews_unmatched_for_day",
                        return_value=[{"id": 9, "dni": "30444444",
                                       "entered_name": None, "entered_surname": None,
                                       "created": creado, "triage": "AMARILLO",
                                       "summary": "Tos", "status": "done",
                                       "reopened_at": None}]):
            out = doctor_appointments._entrevistas_sin_turno(
                "m1", {"entrevista_habilitada": True}, "2026-08-16")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["nombre"], "DNI 30444444")  # anonymous QR visitor
        self.assertEqual(out[0]["triage"], "AMARILLO")
        self.assertTrue(out[0]["hora"])  # formatted in the install timezone
        # Doctor with the toggle off gets nothing, even with rows present.
        with mock.patch("app.core.db_clinical.interviews_unmatched_for_day", return_value=[{"id": 9}]):
            self.assertEqual(doctor_appointments._entrevistas_sin_turno(
                "m1", {"entrevista_habilitada": False}, "2026-08-16"), [])

    def test_entrevista_field_offer_and_completada(self):
        from app.modules.turnos import booking_requests
        from app.patient_web import catalog
        doc = {"id": "m1", "entrevista_habilitada": True}
        with mock.patch.object(catalog, "doctor_doc", return_value=doc):
            out = booking_requests._entrevista_field(7, "m1")
        self.assertEqual(out, {"ofrecer": True, "url": "/entrevista/7"})
        # incluir_estado adds "completada"; a read that fails must NOT kill
        # the offer — same internal granularity the direct import had.
        with mock.patch.object(catalog, "doctor_doc", return_value=doc), \
                mock.patch("app.core.db_clinical.interview_for_request",
                           return_value={"id": 3, "status": "done"}):
            out = booking_requests._entrevista_field(7, "m1", incluir_estado=True)
        self.assertEqual(out["completada"], True)
        def _boom(sid):
            raise RuntimeError("db down")
        with mock.patch.object(catalog, "doctor_doc", return_value=doc), \
                mock.patch("app.core.db_clinical.interview_for_request", side_effect=_boom):
            out = booking_requests._entrevista_field(7, "m1", incluir_estado=True)
        self.assertEqual(out, {"ofrecer": True, "url": "/entrevista/7", "completada": False})
        # Doctor override off -> no CTA at all.
        with mock.patch.object(catalog, "doctor_doc",
                               return_value={"entrevista_habilitada": False}):
            self.assertIsNone(booking_requests._entrevista_field(7, "m1"))

    def test_entrevista_patient_token_mints_via_interview_patient(self):
        from app.modules.turnos import booking_requests
        from app.modules.intake import interview_patient
        with mock.patch("app.core.db_booking.get_booking_request", return_value={"id": 7}), \
                mock.patch.object(interview_patient, "_find_or_create_interview",
                                  return_value={"id": 3, "patient_token": "tok-9"}) as find:
            self.assertEqual(booking_requests._entrevista_patient_token(7), "tok-9")
        find.assert_called_once_with({"id": 7}, 7)
        with mock.patch("app.core.db_booking.get_booking_request", return_value=None):
            self.assertIsNone(booking_requests._entrevista_patient_token(7))

    def test_entrevistas_de_doctor_passes_rows_through(self):
        from app.admin_panel import doctor_patients
        filas = [{"id": 5, "dni": "30111111", "triage": "ROJO"}]
        with mock.patch("app.core.db_clinical.interviews_of_doctor", return_value=filas) as of_doctor:
            self.assertEqual(doctor_patients._entrevistas_de_doctor("m1"), filas)
        of_doctor.assert_called_once_with("m1")

    def test_chart_summary_compacts_interviews(self):
        from app.ai import chart_summary
        creado = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
        with mock.patch("app.core.db_slots.interviews_by_dni", return_value=[{
                "id": 5, "created": creado, "triage": "ROJO", "summary": "Dolor",
                "brief": "breve", "red_flags": ["fiebre"], "status": "done",
                "doctor_id": "m1"}]):
            out = chart_summary._entrevistas("30111111")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["triage"], "ROJO")
        self.assertEqual(out[0]["banderas_rojas"], ["fiebre"])
        self.assertEqual(out[0]["medico_id"], "m1")
        self.assertEqual(out[0]["fecha"], creado.astimezone(
            chart_summary._ART).date().isoformat())


class TestHookRegistryOrderIsolation(unittest.TestCase):
    """Full-suite leak this file used to leave behind: a prior test starts
    intake and registers hooks, then this fixture snapshots `_STARTED`,
    clears the registry, and on teardown restores `_STARTED` without the
    registrations. Later `hooks.call()` sees intake already started,
    `startup_one()` no-ops, and every wrapper returns its absence fallback.

    This case plants that precondition itself (no inherited process state)
    and drives the same `_FakeConfigStore` setUp/tearDown the other classes
    use, so the proof does not depend on unittest discovery order."""

    _SENTINEL = "_intake_hook_boundary_isolation_sentinel"

    def test_teardown_restores_started_intake_and_exact_registry(self):
        def sentinel():
            return "preexisting"

        orig_started = set(app_modules._STARTED)
        orig_broken = dict(app_modules._RUNTIME_BROKEN)
        orig_hooks = {
            name: list(pairs) for name, pairs in app_modules.hooks._registry.items()
        }
        fixture = _FakeConfigStore(methodName="run")
        finished = False
        try:
            app_modules.hooks.register("intake", self._SENTINEL, sentinel)
            app_modules._STARTED.add("intake")
            app_modules._RUNTIME_BROKEN.pop("intake", None)
            app_modules._RUNTIME_BROKEN["__isolation_canary__"] = "preexisting-broken"
            expected_hooks = {
                name: list(pairs)
                for name, pairs in app_modules.hooks._registry.items()
            }

            fixture.setUp()
            self.assertNotIn("intake", app_modules._STARTED)
            self.assertNotIn(self._SENTINEL, app_modules.hooks._registry)

            fixture.tearDown()
            finished = True

            self.assertIn("intake", app_modules._STARTED)
            self.assertNotIn("intake", app_modules._RUNTIME_BROKEN)
            self.assertEqual(
                app_modules._RUNTIME_BROKEN.get("__isolation_canary__"),
                "preexisting-broken")
            self.assertEqual(app_modules.hooks._registry, expected_hooks)
            restored = [
                fn for mid, fn in app_modules.hooks._registry.get(self._SENTINEL, ())
                if mid == "intake"
            ]
            self.assertEqual(len(restored), 1)
            self.assertIs(restored[0], sentinel)
            # Production path that made the leak look like "intake is off":
            # already-started intake never re-registers, so the restored
            # bodies must still be the ones that were there before setUp.
            self.assertTrue(app_modules.startup_one("intake"))
            self.assertIs(
                [fn for mid, fn in app_modules.hooks._registry[self._SENTINEL]
                 if mid == "intake"][0],
                sentinel)
        finally:
            if not finished:
                try:
                    fixture.tearDown()
                except Exception:
                    pass
            app_modules.hooks._registry.clear()
            app_modules.hooks._registry.update(
                {name: list(pairs) for name, pairs in orig_hooks.items()})
            app_modules._STARTED.clear()
            app_modules._STARTED.update(orig_started)
            app_modules._RUNTIME_BROKEN.clear()
            app_modules._RUNTIME_BROKEN.update(orig_broken)


if __name__ == "__main__":
    unittest.main()
