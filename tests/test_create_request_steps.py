"""test_create_request_steps.py — behaviour of booking_create.create_request(), the patient's booking operation.

Only the function's boundary is pinned: (user, body) -> (status, body), what it wrote (the request row, the hold,
the referral file, the AI reading, the approval) and what it sent (the desk notice). The collaborators that reach the
database, the catalog, the network or the clock are replaced by recorders, so no database is needed; nothing here
depends on the ORDER of the internal steps or on how the function is cut.

Run:  python3 -m unittest discover -s tests -p test_create_request_steps.py
"""

import contextlib
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.modules.turnos import booking_requests as br  # noqa: E402
from app.modules.turnos import booking_create  # noqa: E402
from app.modules.turnos import booking_decisions  # noqa: E402
from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import booking_requests_store as store  # noqa: E402
from app.modules.turnos import slot_holds  # noqa: E402

USER = {"dni": "27888999", "nombre": "Ana", "apellido": "Lopez", "nacimiento": "1979-06-01", "sexo": "f",
        "contacto": "wa:11-5555"}
DEPENDENT = {"dni": "50111222", "nombre": " Luna ", "apellido": "Lopez ", "nacimiento": "2015-03-01", "sexo": "x"}
EXISTING = {"id": 55, "estado": "aprobada", "ai_json": {"ai": "old"},
            "turno_dt": datetime(2026, 11, 2, 9, 0, tzinfo=br._ART)}
SLOT = {"slot_dia": "2026-11-02", "slot_hora": "09:00"}
ACCT = slot_holds.owner_token(USER)
AUTO = {"service": {"auto_confirmar": True}}
RACE = store.LimitReached(1, 1)
PDF = {"filename": "o.pdf", "mime": "application/pdf", "data_b64": "SG9sYQ=="}

# every collaborator that leaves the function: (module or object, attribute, default answer)
TARGETS = {
    "get_dependent": (store, "get_dependent", None),
    "attendance": (store, "attendance_summary_dni", {"bloqueado_web": False}),
    "idem_lookup": (store, "get_booking_request_by_idem_key", None),
    "create": (store, "create_booking_request_with_limit", {"id": 77, "estado": "pendiente"}),
    "hold_release": (store, "hold_release", None),
    "hold_link": (store, "hold_link_booking_request", None),
    "set_referral": (store, "set_referral", None),
    "save_ai": (store, "save_ai", None),
    "reload": (store, "get_booking_request", {"id": 77, "estado": "pendiente"}),
    "catalog": (booking_create, "_check_catalog_refs", None),
    "slot_doctor": (br, "_resolve_slot_doctor", "doc-slot"),
    "service": (br, "_service_full", {}),
    "load_pending": (booking_referrals, "_load_pending", None),
    "discard_pending": (booking_referrals, "_discard_pending_meta", None),
    "approve": (booking_decisions, "_persist_approval", None),
    "auto_doctor": (booking_create, "_auto_confirm_doctor", ("Dr. Auto", "doc-auto")),
    "interview": (br, "_entrevista_field", None),
    "reconcile": (booking_create.ai_referral, "reconcile_with_request", {"ai": "reconciled"}),
    "analyze": (booking_create.ai_referral, "analyze_referral_3pass", {"ai": "3pass"}),
    "limits": ("app.modules.turnos.booking_limits", "check", (True, {"limite": 2, "abiertas": 0})),
    "horizon": ("app.modules.turnos.booking_horizon", "check_slot", None),
    "hold_take": ("app.modules.turnos.slot_holds", "try_take", True),
    "center": ("app.patient_web.site_centers", "main_center_text", "Hospital de Pinamar"),
    "same_day": ("app.patient_web.site_availability", "_opens_same_day", False),
    "notify": ("app.notifications.send", "notify_received", None),
    "actor": (br, "_patient_actor", "paciente"),
}


class Result:
    def __init__(self):
        self.calls = {}
        self.status = self.body = None
        self.files = {}

    def n(self, name):
        return len(self.calls.get(name, []))

    def arg(self, name, i=0):
        return self.calls[name][0][0][i]

    def kw(self, name):
        return self.calls[name][0][1]


def run(user=USER, body=None, auto=True, pending=False, answers=None, patch_br=None):
    """One create_request call; `answers` overrides a collaborator's answer (an exception is raised, a list answers
    call by call)."""
    res = Result()
    tmp = tempfile.mkdtemp(prefix="crs-")
    answers = dict(answers or {})
    if pending:
        os.makedirs(os.path.join(tmp, "pending-src"))
        src = os.path.join(tmp, "pending-src", "p.pdf")
        with open(src, "wb") as f:
            f.write(b"pendingbytes")
        answers.setdefault("load_pending", {"orden_path": src, "orden_mime": "application/pdf",
                                            "orden_filename": "p.pdf", "ai": None, **(pending if isinstance(pending, dict) else {})})

    def recorder(name, answer):
        it = iter(answer) if isinstance(answer, list) else None

        def fake(*args, **kwargs):
            res.calls.setdefault(name, []).append((args, kwargs))
            out = next(it) if it else answer
            if isinstance(out, BaseException):
                raise out
            return out
        return fake

    with contextlib.ExitStack() as stack:
        for name, (obj, attr, default) in TARGETS.items():
            obj = importlib.import_module(obj) if isinstance(obj, str) else obj
            stack.enter_context(mock.patch.object(obj, attr, side_effect=recorder(name, answers.pop(name, default))))
        assert not answers, f"unknown collaborators: {sorted(answers)}"
        stack.enter_context(mock.patch.object(br, "REFERRALS_DIR", os.path.join(tmp, "refs")))
        stack.enter_context(mock.patch.object(booking_create.secrets, "token_urlsafe", return_value="tok"))
        for attr, value in (patch_br or {}).items():
            stack.enter_context(mock.patch.object(br, attr, value))
        os.makedirs(os.path.join(tmp, "refs"))
        try:
            try:
                res.status, res.body = booking_create.create_request(
                    user, {"servicio_id": "cardiologia", "centro_slug": "hospital", **(body or {})},
                    allow_auto_confirmation=auto)
            except Exception as e:  # noqa: BLE001 — the raise IS the observable
                res.status, res.body = "raises", type(e).__name__
            for base, _dirs, names in os.walk(tmp):
                for n in names:
                    with open(os.path.join(base, n), "rb") as f:
                        res.files[os.path.relpath(os.path.join(base, n), tmp)] = f.read().decode("latin-1")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return res


class TestRefusals(unittest.TestCase):
    """Every refusal answers with its code and body and creates nothing."""

    def check(self, status, error, **kw):
        res = run(**kw)
        self.assertEqual((res.status, (res.body or {}).get("error")), (status, error), res.body)
        self.assertEqual((res.n("create"), res.n("notify")), (0, 0))
        return res

    def test_the_account_and_the_dependent(self):
        self.check(400, "la cuenta no tiene un DNI válido", user={"dni": "12"})
        self.check(403, "paciente_no_vinculado", body={"paciente_dni": "50111222"})
        self.assertEqual(run(body={"paciente_dni": USER["dni"]}).status, 200)  # one's own dni is not a dependent

    def test_a_service_is_required_and_the_catalog_may_refuse(self):
        self.check(400, "falta servicio_id", body={"servicio_id": " "})
        res = run(answers={"catalog": (400, {"codigo": "centro_inexistente"})})
        self.assertEqual((res.status, res.body, res.n("create")), (400, {"codigo": "centro_inexistente"}, 0))

    def test_repeated_no_shows_block_the_web_and_name_the_desk(self):
        blocked = {"attendance": {"bloqueado_web": True, "faltas": 4}}
        res = self.check(403, "bloqueado_por_ausencias", answers=blocked)
        self.assertEqual((res.body["faltas"], res.body["limite"]), (4, br._no_show_limit()))
        self.assertIn("Hospital de Pinamar", res.body["detalle"])
        res = self.check(403, "bloqueado_por_ausencias", answers={**blocked, "center": ""})
        self.assertIn("centro de salud", res.body["detalle"])
        # the block is judged on the patient (a dependent), not on the account holder
        self.assertEqual(run(body={"paciente_dni": "50111222"}, answers={"get_dependent": DEPENDENT}).arg("attendance"),
                         "50111222")

    def test_the_active_limit_refuses_with_a_409_and_the_way_out(self):
        for info, named in (({"limite": 1, "abiertas": 1, "servicio_nombre": "Cardiologia"}, "Cardiologia"),
                            ({"limite": 2, "abiertas": 2}, "este servicio")):
            res = self.check(409, "limite_turnos_activos", answers={"limits": (False, info)})
            self.assertEqual((res.body["limite"], res.body["abiertas"], res.body["servicio_id"]),
                             (info["limite"], info["abiertas"], "cardiologia"))
            self.assertIn(named, res.body["detalle"])
            self.assertEqual(run(body=SLOT, answers={"limits": (False, info)}).n("hold_take"), 0)  # no slot parked
            self.assertEqual(res.body["accion"]["url"], "/account#turnos")

    def test_the_limit_is_resolved_for_the_slots_doctor_never_the_clients(self):
        self.assertEqual(run(body={**SLOT, "slot_medico_id": "forged"}).arg("limits", 2), "doc-slot")
        self.assertEqual(run(body={"slot_medico_id": "doc-9"}).arg("limits", 2), "doc-9")

    def test_the_age_gate_judges_the_patient_not_the_holder(self):
        kids_only = {"service": {"edad": {"max": 17}}}
        self.check(400, "edad_fuera_de_rango", answers=kids_only)
        res = run(body={"paciente_dni": "50111222"}, answers={**kids_only, "get_dependent": DEPENDENT})
        self.assertEqual(res.status, 200)
        self.assertEqual(res.arg("create", 3).isoformat(), "2015-03-01")

    def test_a_slot_must_be_whole_resolvable_inside_the_horizon_and_free(self):
        for body in ({"slot_dia": "2026-11-02"}, {"slot_dia": "02/11/2026", "slot_hora": "09:00"}):
            self.check(400, "slot_dia/slot_hora inválidos", body=body)
        self.check(400, "slot_dia/slot_hora inválidos", body=SLOT, patch_br={"_slot_dt_art": lambda *a: None})
        self.check(409, "fuera_de_horizonte", body=SLOT, answers={"horizon": {"error": "fuera_de_horizonte"}})
        self.check(409, "turno_tomado", body=SLOT, answers={"hold_take": False})
        self.check(409, "turno_tomado", body=SLOT, answers={"hold_take": RuntimeError("db")})  # fails closed

    def test_a_bad_referral_is_refused(self):
        self.check(413, "el archivo es muy pesado", body={"orden": PDF}, patch_br={"MAX_REFERRAL_BYTES": 2})
        with mock.patch.object(booking_referrals, "_decode_referral", side_effect=ValueError("no se pudo")):
            self.check(400, "no se pudo", body={"orden": PDF})


class TestWhatIsWritten(unittest.TestCase):
    def test_the_request_row_carries_the_clamped_fields_of_the_patient(self):
        res = run(user={**USER, "sexo": "q", "nacimiento": "no-fecha"},
                  body={"contacto": " 11-2222 ", "preferencia": "x" * 200, "caso": "y" * 1500, "idem_key": " k5 ",
                        "slot_medico_id": " doc-1 "})
        self.assertEqual((res.status, res.body["id"], res.body["estado"]), (200, 77, "pendiente"))
        dni, nombre, apellido, nacimiento, contacto, servicio, centro, limite = res.calls["create"][0][0]
        self.assertEqual((dni, nombre, apellido, nacimiento, contacto, servicio, centro, limite),
                         ("27888999", "Ana", "Lopez", None, "11-2222", "cardiologia", "hospital", 2))
        kw = res.kw("create")
        self.assertEqual((kw["sexo"], kw["idem_key"], kw["slot_medico_id"], kw["solicitante_dni"], kw["actor"]),
                         (None, "k5", "doc-1", "27888999", "paciente"))
        self.assertEqual((len(kw["preferencia_horario"]), len(kw["nota_paciente"])), (120, booking_create.MAX_PATIENT_NOTE))

    def test_the_contact_falls_back_to_the_account_and_a_dependent_books_under_its_own_data(self):
        self.assertEqual(run(body={"contacto": ""}).arg("create", 4), "wa:11-5555")
        res = run(body={"paciente_dni": "50111222"}, answers={"get_dependent": DEPENDENT})
        self.assertEqual(res.calls["create"][0][0][:3], ("50111222", "Luna", "Lopez"))
        self.assertEqual((res.kw("create")["sexo"], res.kw("create")["solicitante_dni"]), ("X", "27888999"))
        self.assertEqual(res.arg("notify")["dni"], "50111222")
        self.assertEqual(res.arg("notify")["solicitante_dni"], "27888999")
        self.assertEqual(res.calls["actor"][0][0], ("50111222", "27888999"))

    def test_a_slot_request_holds_the_slot_and_does_not_carry_the_note(self):
        res = run(body={**SLOT, "caso": "no puedo esperar"})
        # A logged-in patient holds under the account's token, whatever the browser sent (2026-11-15).
        self.assertEqual((res.arg("hold_take", 3), res.arg("hold_link", 0), res.arg("hold_link", 1)), (ACCT, ACCT, 77))
        self.assertIsNone(res.kw("create")["nota_paciente"])
        self.assertEqual(run(body={"caso": "no puedo esperar"}).kw("create")["nota_paciente"], "no puedo esperar")
        mine = run(body={**SLOT, "hold_token": " mine "})
        self.assertEqual((mine.arg("hold_take", 3), mine.arg("hold_release")), (ACCT, "mine"))
        self.assertEqual(run(body=SLOT, answers={"hold_link": RuntimeError("db")}).status, 200)

    def test_a_pending_referral_moves_to_its_final_name_and_is_linked(self):
        res = run(body={"orden_token": "tok-ok"}, pending=True)
        self.assertEqual((res.status, res.files), (200, {"refs/77.pdf": "pendingbytes"}))
        self.assertEqual(res.arg("discard_pending"), "tok-ok")
        self.assertEqual(res.calls["set_referral"][0][0][::2], (77, "application/pdf"))
        with mock.patch.object(br.os, "replace", side_effect=OSError("cross-device")):
            self.assertEqual(run(body={"orden_token": "t"}, pending=True).files["refs/77.pdf"], "pendingbytes")
        self.assertEqual(run(body={"orden_token": "t"}, pending=True, answers={"set_referral": RuntimeError("db")}).status, 200)
        for gone in ({"orden_path": "/nonexistent/x.pdf", "ai": {"ai": "stored"}}, None):  # expired token / vanished file
            res = run(body={"orden_token": "t"}, answers={"load_pending": gone})
            self.assertEqual((res.status, res.files, res.n("set_referral")), (200, {}, 0))
            self.assertEqual((res.n("reconcile"), res.n("analyze")), (0, 1))  # a lost file's old reading is not trusted

    def test_an_inline_referral_is_decoded_and_written(self):
        self.assertEqual(run(body={"orden": PDF}).files, {"refs/77.pdf": "Hola"})
        data_url = {"filename": "o.png", "data_b64": "data:image/png;base64,SG9sYQ=="}
        self.assertEqual(run(body={"orden": data_url}).files, {"refs/77.png": "Hola"})
        res = run(body={"orden": PDF}, answers={"create": {"id": 77, "estado": "pendiente"}})
        self.assertEqual(res.calls["set_referral"][0][0][::2], (77, "application/pdf"))
        # a file that cannot be written leaves the request without a referral, never without an answer
        res = run(body={"orden": PDF}, answers={"create": {"id": "x/y", "estado": "pendiente"}})
        self.assertEqual((res.status, res.files, res.n("set_referral")), (200, {}, 0))

    def test_the_analysis_is_reconciled_or_run_and_always_saved(self):
        res = run(body={"orden_token": "t"}, pending={"ai": {"ai": "stored"}})
        self.assertEqual((res.n("analyze"), res.body["ai"]), (0, {"ai": "reconciled"}))
        res = run()
        self.assertEqual((res.n("reconcile"), res.body["ai"], res.calls["save_ai"][0][0]), (0, {"ai": "3pass"}, (77, {"ai": "3pass"})))
        self.assertEqual(run(answers={"save_ai": RuntimeError("db")}).status, 200)
        self.assertEqual(run(answers={"save_ai": store.DbUnavailable("down")}).status, "raises")


class TestIdempotencyAndRaces(unittest.TestCase):
    def assert_answered_from(self, res, row_id=55):
        self.assertEqual((res.status, res.body["id"], res.body["estado"]), (200, row_id, "aprobada"))
        self.assertEqual(res.body["turno_dt"], "2026-11-02T09:00:00-03:00")
        self.assertEqual((res.n("notify"), res.n("analyze"), res.n("save_ai"), res.n("set_referral")), (0, 0, 0, 0))

    def test_a_repeated_idem_key_answers_the_original_and_does_nothing_again(self):
        res = run(body={**SLOT, "idem_key": " k1 ", "slot_medico_id": "doc-1"},
                  answers={"idem_lookup": EXISTING, "interview": {"ofrecer": True}})
        self.assert_answered_from(res)
        self.assertEqual((res.body["entrevista"], res.body["ai"], res.arg("idem_lookup")), ({"ofrecer": True}, {"ai": "old"}, "k1"))
        self.assertEqual((res.n("create"), res.n("hold_take"), res.n("approve")), (0, 0, 0))
        pending = {"id": 55, "estado": "pendiente", "ai_json": None}
        self.assertEqual(run(body={"idem_key": "k1"}, answers={"idem_lookup": pending}).body["estado"], "pendiente")

    def test_a_key_created_meanwhile_beats_a_refused_limit_and_a_lost_race(self):
        both = [None, EXISTING]
        refused = run(body={"idem_key": "k2"}, answers={"limits": (False, {"limite": 1}), "idem_lookup": both})
        self.assert_answered_from(refused)
        lost = run(body={**SLOT, "idem_key": "k3"}, answers={"create": RACE, "idem_lookup": both})
        self.assert_answered_from(lost)
        self.assertEqual(lost.n("hold_release"), 1)

    def test_a_row_that_already_existed_is_reported_without_repeating_its_effects(self):
        res = run(body={"idem_key": "k4"}, answers={"create": {**EXISTING, "_ya_existia": True}})
        self.assert_answered_from(res)
        res = run(answers={"create": {"id": 77, "estado": "pendiente", "_ya_existia": True}})  # no key: carries on
        self.assertEqual((res.status, res.body["estado"], res.n("notify")), (200, "pendiente", 1))

    def test_the_race_loser_releases_its_hold_only_when_it_took_one(self):
        res = run(body=SLOT, answers={"create": RACE})
        self.assertEqual((res.status, res.body["error"], res.arg("hold_release")), (409, "limite_turnos_activos", ACCT))
        self.assertEqual(run(body=SLOT, answers={"create": RACE, "hold_release": RuntimeError("db")}).status, 409)
        res = run(body={"hold_token": "stray"}, answers={"create": RACE})
        self.assertEqual((res.status, res.n("hold_release")), (409, 0))


class TestAutoConfirmationAndNotice(unittest.TestCase):
    def test_auto_confirmation_approves_the_chosen_slot_and_sends_no_notice(self):
        res = run(body={**SLOT, "slot_medico_id": "doc-1"}, answers={**AUTO, "interview": {"ofrecer": True}})
        self.assertEqual((res.status, res.body["estado"], res.body["turno_dt"], res.body["ai"]),
                         (200, "aprobada", "2026-11-02T09:00:00-03:00", {"ai": "3pass"}))
        self.assertEqual((res.body["entrevista"], res.n("notify")), ({"ofrecer": True}, 0))
        self.assertEqual(res.calls["auto_doctor"][0][0], ("cardiologia", "doc-slot"))
        # the interview offer follows the doctor put onto the turno, not the id the wizard sent
        self.assertEqual(res.calls["interview"][0][0], (77, "doc-auto"))
        kw = res.kw("approve")
        self.assertEqual((res.arg("approve", 0), kw["medico"], kw["medico_id"], kw["resuelta_por"], kw["slot_capacity"]),
                         (77, "Dr. Auto", "doc-auto", "auto:web", 1))
        self.assertEqual(res.arg("approve", 1).isoformat(), "2026-11-02T09:00:00-03:00")
        self.assertEqual(run(body=SLOT, answers={"same_day": True}).body["estado"], "aprobada")

    def test_without_permission_a_slot_or_after_a_failure_it_stays_pending_and_notifies(self):
        for kwargs in (dict(body=SLOT, auto=False, answers=AUTO), dict(answers=AUTO),
                       dict(body=SLOT, answers={**AUTO, "approve": RuntimeError("boom")})):
            res = run(**kwargs)
            self.assertEqual((res.status, res.body["estado"], res.n("notify")), (200, "pendiente", 1), kwargs)
            if "approve" not in kwargs.get("answers", {}):
                self.assertEqual(res.n("approve"), 0, kwargs)  # nothing is approved without permission or a slot

    def test_a_lost_approval_race_reports_the_real_state_and_sends_nothing(self):
        for error in (store.AlreadyProcessed("x"), store.SlotConflict("x")):
            res = run(body=SLOT, answers={**AUTO, "approve": error, "reload": EXISTING})
            self.assertEqual((res.status, res.body["estado"], res.body["turno_dt"], res.n("notify")),
                             (200, "aprobada", "2026-11-02T09:00:00-03:00", 0))
        res = run(body=SLOT, answers={**AUTO, "approve": store.SlotConflict("x"), "interview": {"ofrecer": True}})
        self.assertEqual((res.body["estado"], res.body["entrevista"], res.n("notify")), ("pendiente", {"ofrecer": True}, 0))
        row = {"id": 77, "estado": "aprobada", "turno_dt": datetime(2026, 11, 2, 9, 0, tzinfo=br._ART)}
        res = run(body=SLOT, answers={**AUTO, "approve": store.SlotConflict("x"), "reload": RuntimeError("db"), "create": row})
        self.assertEqual((res.status, res.body["estado"]), (200, "aprobada"))  # the row it already holds

    def test_the_pending_notice_names_the_request_and_never_breaks_the_booking(self):
        res = run(body={"slot_medico_id": "doc-1"}, answers={"interview": {"ofrecer": True}})
        notice = res.arg("notify")
        self.assertEqual((notice["id"], notice["servicio_id"], notice["medico_asignado_id"], notice["ai_json"]),
                         (77, "cardiologia", "doc-1", {"ai": "3pass"}))
        self.assertEqual(res.body, {"ok": True, "id": 77, "estado": "pendiente", "ai": {"ai": "3pass"},
                                    "entrevista": {"ofrecer": True}})
        self.assertEqual(run(answers={"notify": RuntimeError("smtp")}).body["estado"], "pendiente")


if __name__ == "__main__":
    unittest.main()
