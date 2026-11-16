"""Optional coverage: real native HTTP, sessions and private disposable PG.

No external lookup/message/clinical record is used. Booking creation is fixture
setup; every tested endpoint must preserve it and all non-coverage user fields.
"""
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1', TURNOS_AUTH_MODE='password')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core import config, db, deps, i18n_middleware
from app.core import db_booking
from app.core import db_accounts
from app.cuentas import accounts, accounts_router, accounts_store, health_coverage
from app.modules.turnos import booking_requests_router, postbooking_coverage
import _dbtest

_db = _dbtest.PerModuleDb('sana_turnos_test_postcov', seed=lambda: db_accounts.get_user('__schema__'))
app = FastAPI()
deps.install_gate_handlers(app)  # the app's handlers (a body that does not decode)
app.add_middleware(i18n_middleware.I18nMiddleware)
app.include_router(accounts_router.router)
app.include_router(booking_requests_router.router)
NONE = {'declarada_sin_cobertura': True}
ONE = {'obras_sociales': [{'nombre': 'IOMA', 'afiliado': 'DEMO-001'}]}
TWO = {'prepaga': {'nombre': 'OSDE', 'afiliado': 'DEMO-002', 'plan': '210'}}


def setUpModule():
    _db.set_up()
    if not _db.have_db:
        raise RuntimeError('Private synthetic PG unavailable: ' + str(_db.skip_reason))


def tearDownModule():
    _db.tear_down()


def client(dni=None):
    c = TestClient(app, base_url='http://127.0.0.1:7700', client=('127.0.0.1', 12345),
                   raise_server_exceptions=False, headers={'Accept-Language': 'es'})
    if dni:
        c.cookies.set('st_ses', db_accounts.create_session(dni))
    return c


def booking(dni, requester=None):
    return db_booking.create_booking_request(dni, 'Demo', 'Paciente', '1990-01-01', '',
                                     'demo-service', 'demo-center', solicitante_dni=requester)


def account_payload(user, **changes):
    data = {key: user.get(key) for key in ('nombre', 'apellido', 'sexo', 'contacto', 'email')}
    data['nacimiento'] = str(user['nacimiento'])
    data.update(changes)
    return data


class PostbookingCoverageTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.mail = mock.patch.object(accounts, '_send_email', side_effect=AssertionError('No outbound')).start()
        self.otp = mock.patch.object(accounts, '_send_otp_whatsapp', side_effect=AssertionError('No outbound')).start()
        # The normalizer may read a local cache, never trigger a lookup.
        from app import connectors
        self.insurance = mock.Mock()
        self.insurance.cached.return_value = []
        self.insurance.lookup.side_effect = AssertionError('No insurance request')
        mock.patch.object(connectors, 'adapter_for', return_value=self.insurance).start()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE sessions, reset_tokens, users, booking_requests, appointment_events RESTART IDENTITY CASCADE')
        config.guardar({'TURNOS_AUTH_MODE': 'password', 'LANGUAGES': 'es,en', 'OTP_GATING_ENABLED': '0'})
        for dni in ('99000001', '99000002', '99000003'):
            db_accounts.create_user(dni, 'Demo', 'Paciente', '1990-01-01', 'X', '', 'synthetic-test-only',
                           email=dni + '@example.invalid', domicilio={'texto': 'Demo'}, lang='es')
        self.sid = booking('99000001')
        self.url = f'/api/requests/{self.sid}/coverage'

    def tearDown(self):
        # Fixture setup creates one event per booking; coverage never adds one.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT (SELECT count(*) FROM appointment_events) AS events, (SELECT count(*) FROM booking_requests) AS bookings')
            counts = cur.fetchone()
        self.assertEqual(counts['events'], counts['bookings'])
        self.mail.assert_not_called()
        self.otp.assert_not_called()
        self.insurance.lookup.assert_not_called()

    def body(self, coverage=ONE):
        current = db_accounts.get_user('99000001').get('cobertura')
        return {'choice': 'coverage', 'cobertura': coverage, 'revision': health_coverage.revision(current)}

    def test_self_save_get_and_same_payload_retry_preserve_booking_profile(self):
        before_user = db_accounts.get_user('99000001')
        before_booking = db_booking.get_booking_request(self.sid)
        with client('99000001') as c:
            initial = c.get(self.url)
            self.assertEqual(initial.status_code, 200, initial.text)
            self.assertTrue(initial.json()['supported'])
            payload = self.body()
            saved = c.post(self.url, json=payload)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(c.post(self.url, json=payload).json(), saved.json())
            persisted = c.get(self.url).json()
            self.assertEqual(persisted['cobertura'], saved.json()['cobertura'])
            self.assertNotEqual(initial.json()['revision'], persisted['revision'])
            self.assertEqual(c.get('/api/account/me').json()['usuario']['coverage_revision'], persisted['revision'])
        after = db_accounts.get_user('99000001')
        self.assertEqual({k:v for k,v in after.items() if k != 'cobertura'},
                         {k:v for k,v in before_user.items() if k != 'cobertura'})
        self.assertEqual(db_booking.get_booking_request(self.sid), before_booking)
        self.mail.assert_not_called(); self.otp.assert_not_called(); self.insurance.lookup.assert_not_called()

    def test_auth_foreign_unrelated_and_forged_patient_are_scoped(self):
        with client() as c:
            self.assertEqual(c.get(self.url).status_code, 401)
            self.assertEqual(c.post(self.url, json=self.body()).status_code, 401)
        with client('99000002') as c:
            for method in ('get', 'post'):
                res = getattr(c, method)(self.url, **({'json': self.body()} if method == 'post' else {}))
                self.assertEqual(res.status_code, 404)
                self.assertEqual(res.json(), {'error': 'no_encontrado'})
        with client('99000001') as c:
            self.assertEqual(c.get('/api/requests/999999/coverage').json(), {'error':'no_encontrado'})
            payload = self.body(); payload['dni'] = payload['paciente_dni'] = '99000002'
            self.assertEqual(c.post(self.url, json=payload).status_code, 200)
        self.assertIsNone(db_accounts.get_user('99000002')['cobertura'])

    def test_dependent_guardians_and_child_never_read_or_write_holder_coverage(self):
        child_sid = booking('99000003', '99000001')
        url = f'/api/requests/{child_sid}/coverage'
        db_accounts.user_admin_update('99000001', {'cobertura': ONE})
        db_accounts.user_admin_update('99000003', {'cobertura': TWO})
        for dni in ('99000001', '99000003'):
            with client(dni) as c:
                self.assertEqual(c.get(url).json(), {'supported':False, 'reason':'dependent'})
                self.assertEqual(c.post(url, json=self.body()).status_code, 403)
        with client('99000002') as c:
            self.assertEqual(c.get(url).status_code, 404)
        self.assertEqual(db_accounts.get_user('99000001')['cobertura'], ONE)
        self.assertEqual(db_accounts.get_user('99000003')['cobertura'], TWO)

    def test_none_roundtrips_normalizers_and_unrelated_account_save(self):
        from app.admin_panel import reports
        with client('99000001') as c:
            payload = {'choice':'none', 'revision':health_coverage.revision(None)}
            res = c.post(self.url, json=payload)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertTrue(c.get(self.url).json()['sin_cobertura'])
            self.assertEqual(res.json()['cobertura'], NONE)
            user = c.get('/api/account/me').json()['usuario']
            # Shared picker serializes an empty selection; a declaration must
            # not silently disappear during a profile autosave.
            res = c.post('/api/account/update', json=account_payload(user, cobertura={'obras_sociales':[]}, coverage_revision=user['coverage_revision']))
            self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(db_accounts.get_user('99000001')['cobertura'], NONE)
        self.assertEqual(health_coverage.normalize(NONE), NONE)
        self.assertIsNone(health_coverage.primary(NONE))
        self.assertEqual(reports._insurers_of(NONE), [])
        self.assertFalse(reports._has_private_plan(NONE))

    def test_invalid_no_revision_and_oversize_do_not_modify_booking(self):
        before = db_booking.get_booking_request(self.sid)
        with client('99000001') as c:
            for body in ({}, [], {'choice':'coverage'}, {'choice':'skip'}, {'choice':'coverage','revision':'x','cobertura':{}}, {'choice':'none'}):
                self.assertEqual(c.post(self.url, json=body).status_code, 400, repr(body))
            self.assertEqual(c.post(self.url, content='invalid').status_code, 400)
            self.assertEqual(c.post(self.url, content='x'*16385).status_code, 413)
        self.assertEqual(db_booking.get_booking_request(self.sid), before)
        self.assertIsNone(db_accounts.get_user('99000001')['cobertura'])

    def test_stale_save_conflict_and_parallel_cas_only_one_wins(self):
        old = self.body()
        payloads = [self.body(ONE), self.body(TWO)]
        def save(p):
            with client('99000001') as c:
                return c.post(self.url, json=p).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(save, payloads)), [200,409])
        with client('99000001') as c:
            current = c.get(self.url).json()
            different = self.body(TWO if health_coverage.primary(current['cobertura'])['nombre'] == 'IOMA' else ONE)
            different['revision'] = old['revision']
            self.assertEqual(c.post(self.url, json=different).status_code, 409)
            self.assertEqual(c.get(self.url).json(), current)

    def test_database_outage_retry_does_not_rebook(self):
        before = db_booking.get_booking_request(self.sid)
        payload = self.body()
        with client('99000001') as c:
            with mock.patch.object(db_accounts, 'update_self_booking_coverage', side_effect=db.DbUnavailable('synthetic outage')):
                self.assertEqual(c.post(self.url, json=payload).status_code, 503)
            self.assertIsNone(db_accounts.get_user('99000001')['cobertura'])
            self.assertEqual(c.post(self.url, json=payload).status_code, 200)
        self.assertEqual(db_booking.get_booking_request(self.sid), before)

    def test_old_account_coverage_write_rejected_and_unrelated_edit_merges(self):
        old = db_accounts.get_user('99000001')
        with client('99000001') as c:
            self.assertEqual(c.post(self.url, json=self.body()).status_code, 200)
            stored = db_accounts.get_user('99000001')['cobertura']
            for extra in ({'cobertura':{'obras_sociales':[]}}, {'cobertura':None,'coverage_revision':health_coverage.revision(None)}):
                res = c.post('/api/account/update', json=account_payload(old, **extra))
                self.assertEqual(res.status_code, 409, res.text)
            # Legacy clients that do not write coverage are still supported.
            res = c.post('/api/account/update', json=account_payload(old, nombre='Changed'))
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(db_accounts.get_user('99000001')['cobertura'], stored)
            # A legacy no-op carrying exactly the existing value is harmless.
            res = c.post('/api/account/update', json=account_payload(old, cobertura=stored))
            self.assertEqual(res.status_code, 200, res.text)

    def test_account_cas_interleaving_preserves_new_coverage_and_other_fields(self):
        old = db_accounts.get_user('99000001')
        original = accounts_store.update_user
        def interleave(*args, **kwargs):
            self.assertTrue(db_accounts.update_self_booking_coverage(self.sid, '99000001', None, TWO))
            return original(*args, **kwargs)
        with client('99000001') as c, mock.patch.object(accounts_store, 'update_user', side_effect=interleave):
            res = c.post('/api/account/update', json=account_payload(old, nombre='Should not save', cobertura=ONE, coverage_revision=health_coverage.revision(None)))
            self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(db_accounts.get_user('99000001')['cobertura'], TWO)
        self.assertEqual(db_accounts.get_user('99000001')['nombre'], old['nombre'])

    def test_provider_refresh_and_dni_only_interleaving_do_not_replay_coverage(self):
        old = db_accounts.get_user('99000001')
        original = accounts_store.update_user
        def interleave(*args, **kwargs):
            # Identity changed already for DNI-only path; use the current key.
            current_dni = args[0]
            self.assertTrue(db_accounts.update_self_booking_coverage(self.sid, current_dni, None, TWO))
            return original(*args, **kwargs)
        with mock.patch.object(accounts_store, 'update_user', side_effect=interleave):
            accounts._persist_provider_identity_core(dict(old, domicilio='Demo'), 'synthetic-fixture')
        self.assertEqual(db_accounts.get_user('99000001')['cobertura'], TWO)
        db_accounts.user_admin_update('99000001', {'cobertura':None})
        with client('99000001') as c, mock.patch.object(accounts_store, 'update_user', side_effect=interleave):
            res = c.post('/api/account/update', json=account_payload(old, dni='99000009', cobertura=None, coverage_revision=health_coverage.revision(None)))
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(c.get('/api/account/me').json()['usuario']['dni'], '99000009')
        self.assertEqual(db_accounts.get_user('99000009')['cobertura'], TWO)
        self.assertEqual(db_booking.get_booking_request(self.sid)['dni'], '99000009')

    def test_changed_dni_and_coverage_refuses_before_any_migration(self):
        old = db_accounts.get_user('99000001')
        with client('99000001') as c:
            res = c.post('/api/account/update', json=account_payload(old, dni='99000009', cobertura=ONE, coverage_revision=health_coverage.revision(None)))
            self.assertEqual(res.status_code, 409, res.text)
            self.assertEqual(c.get('/api/account/me').json()['usuario']['dni'], '99000001')
        self.assertIsNone(db_accounts.get_user('99000009'))
        self.assertEqual(db_booking.get_booking_request(self.sid)['dni'], '99000001')

    def test_coverage_save_merges_concurrent_profile_and_preferences_edit(self):
        original = db_accounts.update_self_booking_coverage
        def interleave(*args, **kwargs):
            # Another account request commits after the endpoint read; only
            # the coverage column is allowed to change in the pending save.
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("UPDATE users SET first_name='Updated', contact='demo-contact', notifications=%s::jsonb WHERE dni='99000001'", ('{"whatsapp":false,"email":true}',))
            return original(*args, **kwargs)
        with client('99000001') as c, mock.patch.object(db_accounts, 'update_self_booking_coverage', side_effect=interleave):
            self.assertEqual(c.post(self.url, json=self.body()).status_code, 200)
        current = db_accounts.get_user('99000001')
        self.assertEqual(current['nombre'], 'Updated')
        self.assertEqual(current['contacto'], 'demo-contact')
        self.assertEqual(current['avisos'], {'whatsapp':False,'email':True})
        self.assertEqual(health_coverage.primary(current['cobertura'])['afiliado'], 'DEMO-001')

    def test_no_document_account_is_unsupported_and_deactivated_account_rejected(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET identity_kind='synthetic' WHERE dni='99000001'")
        with client('99000001') as c:
            self.assertEqual(c.get(self.url).json(), {'supported': False, 'reason': 'identity'})
            self.assertEqual(c.post(self.url, json=self.body()).status_code, 403)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET identity_kind='dni', active=false WHERE dni='99000001'")
        status, payload = postbooking_coverage.respond({'dni':'99000001'}, self.sid)
        self.assertEqual((status, payload), (401, {'error':'auth_requerida'}))

    def test_none_replaces_real_coverage_and_can_be_replaced_without_invented_payer(self):
        with client('99000001') as c:
            saved = c.post(self.url, json=self.body()).json()
            none = c.post(self.url, json={'choice':'none', 'revision':saved['revision']})
            self.assertEqual(none.status_code, 200, none.text)
            self.assertEqual(none.json()['cobertura'], NONE)
            replacement = c.post(self.url, json={'choice':'coverage','cobertura':TWO,'revision':none.json()['revision']})
            self.assertEqual(replacement.status_code, 200, replacement.text)
            self.assertNotIn('declarada_sin_cobertura', replacement.json()['cobertura'])
            self.assertEqual(health_coverage.primary(replacement.json()['cobertura'])['nombre'], 'OSDE')

    def test_atomic_recheck_and_legacy_requester(self):
        before = db_accounts.get_user('99000001')['cobertura']
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('UPDATE booking_requests SET requester_dni=%s WHERE id=%s', ('99000002', self.sid))
        self.assertFalse(db_accounts.update_self_booking_coverage(self.sid, '99000001', before, ONE))
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('UPDATE booking_requests SET requester_dni=NULL WHERE id=%s', (self.sid,))
        with client('99000001') as c:
            self.assertEqual(c.post(self.url, json=self.body()).status_code, 200)
