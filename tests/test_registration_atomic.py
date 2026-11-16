"""Native signup/session regressions on a run-scoped synthetic database.

Only email/OTP transports are replaced. Registration, password verification,
sessions, cookies, language resolution and transaction failures are real.
"""
import os
import secrets
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import _env  # noqa: E402
_env.process_set(TURNOS_DNI_CLAIMS='0', TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1', TURNOS_AUTH_MODE='password')

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core import config, db, i18n_middleware
from app.core import db_accounts
from app.cuentas import accounts, accounts_router, accounts_store as store
import _dbtest

_db = _dbtest.PerModuleDb('sana_turnos_test_reg_atomic', seed=lambda: db_accounts.get_user('__schema__'))
_APP = FastAPI()
_APP.add_middleware(i18n_middleware.I18nMiddleware)
_APP.include_router(accounts_router.router)


def setUpModule():
    _db.set_up()
    if not _db.have_db:
        raise RuntimeError('Synthetic registration database unavailable: ' + str(_db.skip_reason))


def tearDownModule():
    _db.tear_down()


def client(lang='es'):
    return TestClient(_APP, base_url='http://127.0.0.1:7700',
                      client=('127.0.0.1', 54321), raise_server_exceptions=False,
                      headers={'Accept-Language': lang})


def synthetic_password():
    """Per-test throwaway input, including characters that must roundtrip."""
    return secrets.token_hex(16) + ' +&%=ñ"\\'


def payload(**changes):
    result = dict(dni='99000001', nombre='Synthetic', apellido='Fixture',
                  nacimiento='1990-05-05', email='fixture+one@example.invalid',
                  password=synthetic_password(), contacto='')
    result.update(changes)
    return result


def count(table, column, value):
    assert (table, column) in {('users', 'dni'), ('sessions', 'dni')}
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT count(*) AS n FROM {table} WHERE {column}=%s', (value,))
        return cur.fetchone()['n']


class RegistrationAtomicTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.send_email = mock.patch.object(accounts, '_send_email', return_value=True).start()
        self.send_otp = mock.patch.object(accounts, '_send_otp_whatsapp', return_value=True).start()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE sessions, reset_tokens, users RESTART IDENTITY CASCADE')
        config.guardar({'LANGUAGES': 'es,en', 'LANG_DEFAULT': '', 'TURNOS_AUTH_MODE': 'password',
                        'OTP_GATING_ENABLED': '0'})
        accounts._REGISTERED.clear()
        accounts._LOGIN_ATTEMPTS.clear()
        self.addCleanup(accounts._REGISTERED.clear)
        self.addCleanup(accounts._LOGIN_ATTEMPTS.clear)

    def register(self, c, body):
        return c.post('/api/account/register', json=body)

    def test_signup_cookie_logout_login_plus_email_and_special_password(self):
        for lang, dni in [('es', '99000001'), ('en', '')]:
            with self.subTest(lang=lang), client(lang) as c:
                body = payload(dni=dni, email=f'fixture+{lang}@example.invalid')
                self.assertTrue(set(' +&%=ñ"\\').issubset(body['password']))
                registered = self.register(c, body)
                self.assertEqual(registered.status_code, 200)
                identity = registered.json()['usuario']['dni']
                cookie = registered.headers['set-cookie']
                self.assertIn('HttpOnly', cookie)
                self.assertIn('SameSite=Lax', cookie)
                self.assertNotIn(body['password'], registered.text)
                self.assertNotIn('pass_hash', registered.text)
                self.assertEqual(c.get('/api/account/me?dni=99000099').json()['usuario']['dni'], identity)
                self.assertEqual(c.post('/api/account/logout').status_code, 200)
                self.assertEqual(c.get('/api/account/me').status_code, 401)
                login = c.post('/api/account/login', json={'email': body['email'].upper(), 'password': body['password']})
                self.assertEqual(login.status_code, 200)
                self.assertEqual(c.get('/api/account/me').json()['usuario']['dni'], identity)
        self.send_email.assert_not_called()
        self.send_otp.assert_not_called()

    def test_duplicate_dni_preserves_old_account_and_issues_no_cookie(self):
        with client() as c:
            original = payload()
            self.assertEqual(self.register(c, original).status_code, 200)
            c.post('/api/account/logout')
            rejected_input = payload(dni='99.000.001', email='changed@example.invalid')
            rejected = self.register(c, rejected_input)
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(rejected.json(), {'error': 'dni_registrado'})
            self.assertNotIn('set-cookie', rejected.headers)
            self.assertEqual(c.get('/api/account/me').status_code, 401)
            self.assertEqual(db_accounts.get_user(original['dni'])['email'], original['email'])
            self.assertIsNotNone(db_accounts.verify_password(original['dni'], original['password']))
            self.assertIsNone(db_accounts.verify_password(original['dni'], rejected_input['password']))
            self.assertEqual(count('sessions', 'dni', original['dni']), 0)

    def test_duplicate_email_without_dni_keeps_existing_account(self):
        with client('en') as c:
            original = payload(dni='')
            registered = self.register(c, original)
            self.assertEqual(registered.status_code, 200)
            identity = registered.json()['usuario']['dni']
            c.post('/api/account/logout')
            rejected_input = payload(dni='', email=original['email'].upper())
            rejected = self.register(c, rejected_input)
            self.assertEqual(rejected.status_code, 409)
            self.assertNotIn('set-cookie', rejected.headers)
            self.assertIsNotNone(db_accounts.verify_password(identity, original['password']))
            self.assertIsNone(db_accounts.verify_password(identity, rejected_input['password']))
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM users WHERE lower(email)=lower(%s)', (original['email'],))
                self.assertEqual(cur.fetchone()['n'], 1)

    def test_family_email_remains_shared_between_dni_accounts(self):
        with client() as c:
            for dni in ['99000001', '99000002']:
                self.assertEqual(self.register(c, payload(dni=dni)).status_code, 200)
                self.assertEqual(c.get('/api/account/me').json()['usuario']['dni'], dni)
            self.assertEqual(db_accounts.get_user('99000001')['email'], db_accounts.get_user('99000002')['email'])

    def race(self, bodies, lookup, lang='es'):
        barrier = threading.Barrier(2)
        original = getattr(store, lookup)
        seen = threading.local()
        def rendezvous(value):
            result = original(value)
            if not getattr(seen, 'waited', False):
                seen.waited = True
                barrier.wait(timeout=5)
            return result
        def signup(body):
            with client(lang) as c:
                return self.register(c, body)
        with mock.patch.object(store, lookup, side_effect=rendezvous), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(signup, body) for body in bodies]
            return [future.result(timeout=10) for future in futures]

    def test_concurrent_dni_has_one_winner_and_conflict_not_server_error(self):
        bodies = [payload(email=f'fixture+{i}@example.invalid') for i in range(2)]
        results = self.race(bodies, 'get_user')
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        winner = next(i for i, r in enumerate(results) if r.status_code == 200)
        self.assertEqual(db_accounts.get_user('99000001')['email'], bodies[winner]['email'])
        self.assertIsNotNone(db_accounts.verify_password('99000001', bodies[winner]['password']))
        self.assertIsNone(db_accounts.verify_password('99000001', bodies[1-winner]['password']))
        self.assertEqual(count('users', 'dni', '99000001'), 1)
        self.assertEqual(count('sessions', 'dni', '99000001'), 1)
        self.assertNotIn('set-cookie', results[1-winner].headers)

    def test_concurrent_no_dni_email_has_one_winner(self):
        bodies = [payload(dni='', email='same+family@example.invalid') for _ in range(2)]
        bodies[1]['email'] = bodies[1]['email'].upper()
        results = self.race(bodies, 'get_user_by_email', 'en')
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) AS n FROM users WHERE lower(email)=lower(%s)', (bodies[0]['email'],))
            self.assertEqual(cur.fetchone()['n'], 1)

    def test_session_insert_failure_rolls_back_account_and_retry_succeeds(self):
        db_accounts.create_user('99000099', 'Synthetic', 'Existing', None, None, None, synthetic_password())
        existing_token = db_accounts.create_session('99000099')
        with client() as c:
            with mock.patch('secrets.token_urlsafe', return_value=existing_token):
                failed = self.register(c, payload())
            self.assertEqual(failed.status_code, 500)
            self.assertNotIn('set-cookie', failed.headers)
            self.assertIsNone(db_accounts.get_user('99000001'))
            self.assertEqual(count('sessions', 'dni', '99000001'), 0)
            self.assertEqual(sum(map(len, accounts._REGISTERED.values())), 0)
            retried = self.register(c, payload())
            self.assertEqual(retried.status_code, 200)
            self.assertEqual(c.get('/api/account/me').json()['usuario']['dni'], '99000001')
            self.assertEqual(db_accounts.get_session(existing_token)['dni'], '99000099')

    def test_query_does_not_override_session_or_registration_body(self):
        db_accounts.create_user('99000099', 'Other', 'Fixture', None, None, None, synthetic_password(), email='other@example.invalid')
        with client() as c:
            registered = c.post('/api/account/register?dni=99000099&email=other@example.invalid', json=payload())
            self.assertEqual(registered.status_code, 200)
            self.assertEqual(registered.json()['usuario']['dni'], '99000001')
            self.assertEqual(c.get('/api/account/me?dni=99000099&email=other@example.invalid').json()['usuario']['dni'], '99000001')
            self.assertEqual(db_accounts.get_user('99000099')['email'], 'other@example.invalid')
            c.post('/api/account/logout?dni=99000099')
            self.assertEqual(c.get('/api/account/me?dni=99000099').status_code, 401)

    def test_synthetic_key_race_redraws_without_changing_existing_account(self):
        db_accounts.create_user('900000001', 'Existing', 'Fixture', None, None, None,
                       synthetic_password(), email='existing@example.invalid')
        real_get = store.get_user
        def miss_once(key):
            if key == '900000001':
                return None
            return real_get(key)
        with client('en') as c, mock.patch.object(accounts, '_mint_synthetic_key', side_effect=['900000001', '900000002']), mock.patch.object(store, 'get_user', side_effect=miss_once):
            result = self.register(c, payload(dni=''))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['usuario']['dni'], '900000002')
        self.assertEqual(db_accounts.get_user('900000001')['email'], 'existing@example.invalid')
        self.assertEqual(count('sessions', 'dni', '900000001'), 0)
        self.assertEqual(count('sessions', 'dni', '900000002'), 1)

    def test_unavailable_transaction_has_no_effect_and_retry_works(self):
        with client() as c:
            with mock.patch.object(store, 'create_user_and_session', side_effect=store.DbUnavailable('synthetic outage')):
                result = self.register(c, payload())
            self.assertEqual(result.status_code, 503)
            self.assertNotIn('set-cookie', result.headers)
            self.assertIsNone(db_accounts.get_user('99000001'))
            self.assertEqual(self.register(c, payload()).status_code, 200)
            self.assertEqual(c.get('/api/account/me').json()['usuario']['dni'], '99000001')

    def test_registration_does_not_need_a_second_user_read_after_commit(self):
        with client() as c, mock.patch.object(store, 'get_user', side_effect=[None, AssertionError('post-commit read')]) as read:
            result = self.register(c, payload())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(result.json()['usuario']['dni'], '99000001')

    def test_full_application_cookie_roundtrip_and_query_scope(self):
        # Actual app middleware/router composition. Do not start worker or
        # integration lifespans merely to exercise these HTTP operations.
        from app.main import app
        c = TestClient(app, base_url='http://127.0.0.1:7700', client=('127.0.0.1', 54321))
        self.addCleanup(c.close)
        body = payload()
        registered = self.register(c, body)
        self.assertEqual(registered.status_code, 200)
        self.assertEqual(c.get('/api/account/me?dni=99000099').json()['usuario']['dni'], '99000001')
        self.assertEqual(c.post('/api/account/logout').status_code, 200)
        self.assertEqual(c.get('/api/account/me?dni=99000001').status_code, 401)
        logged_in = c.post('/api/account/login?dni=99000099', json={'email': body['email'], 'password': body['password']})
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(c.get('/api/account/me').json()['usuario']['dni'], '99000001')

    def test_recovery_stays_generic_and_uses_only_stored_contacts(self):
        original = payload(dni='05200001')
        with client() as c:
            self.assertEqual(self.register(c, original).status_code, 200)
            outcomes = [c.post('/api/account/recover', json=body) for body in [
                {'dni': '05.200.001', 'email': 'attacker@example.invalid', 'contacto': '+10000000000'},
                {'email': original['email'].upper()},
                {'dni': '99000099', 'email': 'unknown@example.invalid'},
            ]]
            accounts.drain_detached()
            self.assertTrue(all(r.status_code == 200 and r.json() == {'ok': True} for r in outcomes))
            self.assertEqual([call.args[0] for call in self.send_email.call_args_list], [original['email'], original['email']])
            self.send_otp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
