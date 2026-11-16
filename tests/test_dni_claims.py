"""TASK-68 (security review 2026-11), step a: a sign-up on a document number never locks the real owner out.

A sign-up by password and DNI photo proves nothing about the number. With the desk's check on
(`TURNOS_DNI_CLAIMS`, on by default, off on the demo) the first account on a number is `unverified`, a
second sign-up on it is not refused: it is a `pending` claim living under a minted key, which the desk sees.
Registration, login, sessions and cookies are real; only the email/OTP transports are replaced.
"""
import os
import secrets
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1', TURNOS_AUTH_MODE='password')

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core import config, db, i18n_middleware
from app.core import db_accounts
from app.cuentas import accounts, accounts_router, accounts_store as store
import _dbtest

_db = _dbtest.PerModuleDb('sana_turnos_test_dni_claims', seed=lambda: db_accounts.get_user('__schema__'))
_APP = FastAPI()
_APP.add_middleware(i18n_middleware.I18nMiddleware)
_APP.include_router(accounts_router.router)

DNI = '99000001'


def setUpModule():
    _db.set_up()
    if not _db.have_db:
        raise RuntimeError('Synthetic claims database unavailable: ' + str(_db.skip_reason))


def tearDownModule():
    _db.tear_down()


def client(lang='es'):
    return TestClient(_APP, base_url='http://127.0.0.1:7700',
                      client=('127.0.0.1', 54321), raise_server_exceptions=False,
                      headers={'Accept-Language': lang})


def payload(**changes):
    result = dict(dni=DNI, nombre='Synthetic', apellido='Fixture', nacimiento='1990-05-05',
                  email='fixture+one@example.invalid', password=secrets.token_hex(12), contacto='')
    result.update(changes)
    return result


def claims():
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT dni_claimed, claimant_key, status FROM dni_claims ORDER BY id')
        return [dict(r) for r in cur.fetchall()]


class DniClaimsRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(accounts, '_send_email', return_value=True).start()
        mock.patch.object(accounts, '_send_otp_whatsapp', return_value=True).start()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE dni_claims, sessions, reset_tokens, users RESTART IDENTITY CASCADE')
        config.guardar({'LANGUAGES': 'es,en', 'LANG_DEFAULT': '', 'TURNOS_AUTH_MODE': 'password',
                        'OTP_GATING_ENABLED': '0', 'TURNOS_DNI_CLAIMS': '1'})
        accounts._REGISTERED.clear()
        accounts._LOGIN_ATTEMPTS.clear()
        accounts._DNI_FAILS.clear()
        self.addCleanup(accounts._REGISTERED.clear)
        self.addCleanup(accounts._LOGIN_ATTEMPTS.clear)
        self.addCleanup(accounts._DNI_FAILS.clear)
        self.addCleanup(config.guardar, {'TURNOS_DNI_CLAIMS': ''})

    def register(self, body, lang='es'):
        with client(lang) as c:
            r = c.post('/api/account/register', json=body)
            return r, c.cookies.get(accounts.COOKIE_NAME) if hasattr(accounts, 'COOKIE_NAME') else None

    def login(self, dni, password):
        with client() as c:
            r = c.post('/api/account/login', json={'dni': dni, 'password': password})
            return r, c

    def test_first_account_on_a_number_is_unverified_and_keeps_its_key(self):
        body = payload()
        r, _ = self.register(body)
        self.assertEqual(r.status_code, 200, r.text)
        u = r.json()['usuario']
        self.assertEqual(u['dni'], DNI)
        self.assertEqual(u['dni_verificacion'], 'unverified')
        self.assertEqual(claims(), [dict(dni_claimed=DNI, claimant_key=DNI, status='unverified')])

    def test_second_signup_on_a_taken_number_is_a_pending_claim_not_a_409(self):
        first, second = payload(), payload(email='fixture+two@example.invalid', nombre='Real')
        self.assertEqual(self.register(first)[0].status_code, 200)
        r, _ = self.register(second)
        self.assertEqual(r.status_code, 200, r.text)
        u = r.json()['usuario']
        self.assertNotEqual(u['dni'], DNI, 'the claimant lives under a minted key, never the number')
        self.assertEqual(u['identity_kind'], 'synthetic')
        self.assertEqual(u['dni_verificacion'], 'pending')
        self.assertEqual(u['nombre'] if 'nombre' in u else u.get('first_name'), 'Real')
        rows = claims()
        self.assertEqual([x['status'] for x in rows], ['unverified', 'pending'])
        self.assertEqual(rows[1]['dni_claimed'], DNI)
        self.assertEqual(rows[1]['claimant_key'], u['dni'])
        # the first account is untouched: same key, same password
        ok, _ = self.login(DNI, first['password'])
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['usuario']['dni'], DNI)

    def test_claimant_can_log_in_with_the_number_they_asked_for(self):
        first, second = payload(), payload(email='fixture+two@example.invalid')
        self.register(first)
        key = self.register(second)[0].json()['usuario']['dni']
        r, c = self.login(DNI, second['password'])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['usuario']['dni'], key)
        self.assertEqual(r.json()['usuario']['dni_verificacion'], 'pending')
        me = c.get('/api/account/me')
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()['usuario']['dni'], key)
        self.assertEqual(me.json()['usuario']['dni_verificacion'], 'pending')
        # and the first password does not open the claimant's account, nor a wrong one either account
        self.assertEqual(self.login(DNI, 'not-a-password-at-all')[0].status_code, 401)

    def test_a_number_takes_a_bounded_pile_of_claims(self):
        self.assertEqual(self.register(payload())[0].status_code, 200)
        for i in range(accounts.MAX_PENDING_CLAIMS):
            r, _ = self.register(payload(email=f'fixture+c{i}@example.invalid'))
            self.assertEqual(r.status_code, 200, r.text)
        last, _ = self.register(payload(email='fixture+late@example.invalid'))
        self.assertEqual(last.status_code, 409)
        self.assertEqual(last.json()['error'], 'dni_registrado')
        self.assertEqual(len(claims()), 1 + accounts.MAX_PENDING_CLAIMS)

    def test_claimant_may_use_the_same_email_as_the_holder(self):
        first = payload()
        second = payload(password=secrets.token_hex(12))
        self.register(first)
        r, _ = self.register(second)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(claims()), 2)

    def test_check_off_keeps_the_old_409_and_writes_no_claims(self):
        config.guardar({'TURNOS_DNI_CLAIMS': '0'})
        self.assertEqual(self.register(payload())[0].status_code, 200)
        r, _ = self.register(payload(email='fixture+two@example.invalid'))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['error'], 'dni_registrado')
        self.assertEqual(claims(), [])
        self.assertNotIn('dni_verificacion', self.login(DNI, 'x')[0].text)

    def test_signup_without_a_number_has_nothing_to_check(self):
        r, _ = self.register(payload(dni='', email='fixture+en@example.invalid'), lang='en')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn('dni_verificacion', r.json()['usuario'])
        self.assertEqual(claims(), [])

    def test_losing_the_race_for_a_number_becomes_a_claim(self):
        self.assertEqual(self.register(payload())[0].status_code, 200)
        # the pre-check says «free» although the row is there: the insert loses the conflict
        real = store.get_user
        seen = {'n': 0}

        def blind_once(dni):
            seen['n'] += 1
            return None if seen['n'] == 1 else real(dni)
        with mock.patch.object(store, 'get_user', side_effect=blind_once):
            r, _ = self.register(payload(email='fixture+two@example.invalid'))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['usuario']['dni_verificacion'], 'pending')
        self.assertEqual([x['status'] for x in claims()], ['unverified', 'pending'])

    def test_the_claim_row_and_the_account_commit_together(self):
        with mock.patch.object(db_accounts.sa, 'insert', wraps=db_accounts.sa.insert) as ins:
            self.register(payload())
            self.assertTrue(any(c.args and getattr(c.args[0], 'name', '') == 'dni_claims' for c in ins.call_args_list))
        self.assertEqual(len(claims()), 1)

    def test_one_open_claim_per_account(self):
        self.register(payload())
        with self.assertRaises(Exception):
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("INSERT INTO dni_claims (dni_claimed, claimant_key, status) VALUES (%s, %s, 'pending')",
                            (DNI, DNI))


class UnverifiedAccountSeesNoClinicalDataTest(unittest.TestCase):
    """Step b: an account with an open claim may book but reads nothing clinical keyed by its number."""

    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE dni_claims, booking_requests, sessions, reset_tokens, users RESTART IDENTITY CASCADE')
        config.guardar({'TURNOS_DNI_CLAIMS': '1'})
        self.addCleanup(config.guardar, {'TURNOS_DNI_CLAIMS': ''})
        from app.core import db_booking
        self.sid = db_booking.create_booking_request(
            DNI, 'Real', 'Owner', '1990-05-05', '', 'clinica-medica', 'central')
        db_accounts.create_user(DNI, 'Real', 'Owner', '1990-05-05', None, '', secrets.token_hex(8))
        self.user = {'dni': DNI}

    def open_claim(self, status='unverified'):
        db_accounts.add_claim(DNI, DNI, status)

    def test_confirmed_account_sees_its_history(self):
        from app.modules.turnos import booking_patient
        self.assertEqual(len(booking_patient.patient_rows(DNI)), 1)
        self.assertEqual(booking_patient._patient_detail_core(self.user, self.sid)[0], 200)

    def test_unverified_account_sees_no_requests(self):
        from app.modules.turnos import booking_patient
        self.open_claim()
        self.assertEqual(booking_patient.patient_rows(DNI), [])
        self.assertEqual(booking_patient._patient_list_core(self.user), (200, {'solicitudes': []}))
        self.assertEqual(booking_patient._patient_detail_core(self.user, self.sid)[0], 404)

    def test_pending_claimant_sees_nothing_either(self):
        from app.modules.turnos import booking_patient
        self.open_claim('pending')
        self.assertEqual(booking_patient.patient_rows(DNI), [])

    def test_calendar_feed_is_empty_while_unverified(self):
        from app.modules.turnos import calendar_feed
        self.open_claim()
        with mock.patch.object(calendar_feed.db_accounts, 'user_by_feed_token', return_value=self.user), \
                mock.patch.object(calendar_feed.db_booking, 'list_by_dni') as read:
            calendar_feed.resolve_feed_ics('token')
        read.assert_not_called()

    def test_assistant_tools_read_nothing_while_unverified(self):
        from app.modules.chat import chat_tools
        self.assertEqual(len(chat_tools._tool_mine(self.user, {}, None)['solicitudes']), 1)
        self.open_claim()
        self.assertEqual(chat_tools._tool_mine(self.user, {}, None)['solicitudes'], [])
        with mock.patch('app.ai.lab_results.informes_de') as labs:
            out = chat_tools._tool_lab_results(self.user, {}, None)
        labs.assert_not_called()
        self.assertEqual(out['informes'], [])

    def test_lab_results_route_and_link_are_closed_while_unverified(self):
        from app.ai import lab_results_router
        from app.cuentas import account_deps
        app = FastAPI()
        app.include_router(lab_results_router.router)
        app.dependency_overrides[account_deps.optional_account] = lambda: self.user
        self.open_claim()
        with mock.patch.object(lab_results_router.lab_results, 'informes_de') as labs, \
                TestClient(app, raise_server_exceptions=False) as c:
            r = c.get('/api/account/lab-results')
        labs.assert_not_called()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['informes'], [])
        self.assertEqual(r.json()['analisis_pendiente'], 0)

    def test_flag_off_hides_nothing(self):
        from app.cuentas import claim_gate
        self.open_claim()
        self.assertTrue(claim_gate.hidden(DNI))
        config.guardar({'TURNOS_DNI_CLAIMS': '0'})
        self.assertFalse(claim_gate.hidden(DNI))


if __name__ == '__main__':
    unittest.main()
