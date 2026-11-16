"""TASK-68 (security review 2026-11), step c: the desk resolves a DNI claim in ONE transaction.

Approving a `pending` claim hands the number's account to the claimant and moves the holder's account to a
minted `ANON-` key; rejecting archives the claimant. Everything runs on the synthetic test database.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1')

from app.core import db, db_accounts, personal_data  # noqa: E402
from app.core import schema_model as sm  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb('sana_turnos_test_dni_claims_resolve', seed=lambda: db_accounts.get_user('__schema__'))

NUMBER = '99100001'
CLAIMANT = 'CLAIM-0000000000000001'
CLAIMANT_B = 'CLAIM-0000000000000002'


def setUpModule():
    _db.set_up()
    if not _db.have_db:
        raise RuntimeError('Synthetic claims database unavailable: ' + str(_db.skip_reason))


def tearDownModule():
    _db.tear_down()


def q(sql, args=()):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()] if cur.description else []


def user(dni, name='Ana'):
    db_accounts.create_user(dni, name, 'Fixture', '1990-01-01', 'F', '1155550000', 'Clave-123456',
                            email=f'{dni.lower()}@example.invalid')
    db_accounts.create_session(dni)


def exists(dni):
    return q('SELECT active FROM users WHERE dni=%s', (dni,))


class ClassificationTest(unittest.TestCase):
    def test_every_moved_column_is_decided(self):
        moved = set(db_accounts.DNI_MOVED_COLUMNS)
        cascades = set(db_accounts.CLAIM_HOLDER_CASCADES)
        moves = set(db_accounts.CLAIM_HOLDER_MOVES)
        stays = set(db_accounts.CLAIM_HOLDER_STAYS)
        self.assertEqual(moved, cascades | moves | stays)
        self.assertFalse(cascades & moves or cascades & stays or moves & stays)

    def test_cascades_are_the_children_of_users_dni(self):
        children = {(fk[1], fk[2]) for fk in db_accounts.FOREIGN_KEYS
                    if fk[3] == 'users' and fk[4] == 'dni'}
        self.assertEqual(children | {('users', 'dni')}, set(db_accounts.CLAIM_HOLDER_CASCADES))

    def test_registry_tables_with_a_dni_are_all_classified(self):
        known = set(db_accounts.DNI_MOVED_COLUMNS) | set(db_accounts.DNI_KEPT_COLUMNS)
        for e in personal_data.REGISTRY:
            for sel in e.find:
                if sel[0] == 'dni':
                    self.assertIn((e.table, sel[1]), known, e.label)


class ResolveClaimTest(unittest.TestCase):
    def setUp(self):
        q('TRUNCATE dni_claims, sessions, reset_tokens, chat_messages, ai_costs, patient_health_profile, users RESTART IDENTITY CASCADE')
        user(NUMBER, 'Holder')
        user(CLAIMANT, 'Owner')
        user(CLAIMANT_B, 'Other')
        db_accounts.add_claim(NUMBER, CLAIMANT, 'pending')
        db_accounts.add_claim(NUMBER, CLAIMANT_B, 'pending')
        self.claim = q('SELECT id FROM dni_claims WHERE claimant_key=%s', (CLAIMANT,))[0]['id']

    def test_open_claims_lists_holder_and_claimant(self):
        rows = db_accounts.open_claims()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['holder_dni'], NUMBER)
        self.assertEqual(rows[0]['nombre'], 'Owner')

    def test_the_archived_holder_keeps_no_recovery_link(self):
        q("INSERT INTO reset_tokens(token, dni, expires) VALUES ('tok-holder', %s, now() + interval '1 hour')", (NUMBER,))
        db_accounts.resolve_claim(self.claim, True, 'desk')
        self.assertEqual(q('SELECT 1 FROM reset_tokens WHERE token=%s', ('tok-holder',)), [])

    def test_open_claim_number_reads_one_open_claim(self):
        self.assertEqual(db_accounts.open_claim_number(self.claim), NUMBER)
        db_accounts.resolve_claim(self.claim, False, 'desk')
        self.assertIsNone(db_accounts.open_claim_number(self.claim))

    def test_approve_pending_moves_account_and_everything_under_the_key(self):
        q("INSERT INTO chat_messages(channel,key,role,content) VALUES ('web',%s,'user','\"a\"'),('web',%s,'user','\"b\"')",
          ('dni:' + NUMBER, 'dni:' + CLAIMANT))
        q("INSERT INTO ai_costs(operation,user_dni) VALUES ('x',%s),('y',%s)", (NUMBER, CLAIMANT))
        out = db_accounts.resolve_claim(self.claim, True, 'desk')
        self.assertEqual(out, {'status': 'approved', 'dni': NUMBER})
        holder = [r for r in q('SELECT dni, active FROM users') if r['dni'].startswith('ANON-')]
        self.assertEqual(len(holder), 1)
        self.assertFalse(holder[0]['active'])
        self.assertTrue(exists(NUMBER)[0]['active'])
        self.assertEqual(exists(CLAIMANT), [])
        anon = holder[0]['dni']
        self.assertEqual(q('SELECT 1 FROM sessions WHERE dni=%s', (anon,)), [])
        self.assertEqual(len(q('SELECT 1 FROM sessions WHERE dni=%s', (NUMBER,))), 1)
        keys = sorted(r['key'] for r in q('SELECT key FROM chat_messages'))
        self.assertEqual(keys, sorted(['dni:' + anon, 'dni:' + NUMBER]))
        users = sorted(r['user_dni'] for r in q('SELECT user_dni FROM ai_costs'))
        self.assertEqual(users, sorted([anon, NUMBER]))
        # nothing is left under the claimant's key
        for table, column in db_accounts.DNI_MOVED_COLUMNS:
            if (table, column) in db_accounts.CLAIM_HOLDER_STAYS:
                continue
            self.assertEqual(q(f'SELECT 1 FROM {table} WHERE {column}=%s', (CLAIMANT,)), [], table)
        st = {r['claimant_key']: r['status'] for r in q('SELECT claimant_key, status FROM dni_claims')}
        self.assertEqual(st[NUMBER], 'approved')
        self.assertEqual(st[CLAIMANT_B], 'pending')

    def test_conflict_rolls_back_everything(self):
        q("INSERT INTO patient_health_profile(dni, datos) VALUES (%s,'{}'),(%s,'{}')", (NUMBER, CLAIMANT))
        with self.assertRaises(db_accounts.ClaimError) as cm:
            db_accounts.resolve_claim(self.claim, True, 'desk')
        self.assertEqual(cm.exception.code, 'conflicto')
        self.assertTrue(exists(NUMBER)[0]['active'])
        self.assertTrue(exists(CLAIMANT)[0]['active'])
        self.assertEqual(q("SELECT 1 FROM users WHERE dni LIKE 'ANON-%%'"), [])
        self.assertEqual(q("SELECT status FROM dni_claims WHERE id=%s", (self.claim,))[0]['status'], 'pending')

    def test_reject_archives_claimant_and_closes(self):
        out = db_accounts.resolve_claim(self.claim, False, 'desk', 'no es el titular')
        self.assertEqual(out['status'], 'rejected')
        self.assertFalse(exists(CLAIMANT)[0]['active'])
        self.assertEqual(q('SELECT 1 FROM sessions WHERE dni=%s', (CLAIMANT,)), [])
        self.assertTrue(exists(NUMBER)[0]['active'])
        row = q('SELECT status, resolved_by, note FROM dni_claims WHERE id=%s', (self.claim,))[0]
        self.assertEqual((row['status'], row['resolved_by']), ('rejected', 'desk'))

    def test_approve_unverified_confirms_account(self):
        q('TRUNCATE dni_claims RESTART IDENTITY CASCADE')
        db_accounts.add_claim(NUMBER, NUMBER, 'unverified')
        cid = q('SELECT id FROM dni_claims')[0]['id']
        out = db_accounts.resolve_claim(cid, True, 'desk')
        self.assertEqual(out, {'status': 'approved', 'dni': NUMBER})
        self.assertTrue(exists(NUMBER)[0]['active'])
        self.assertEqual(db_accounts.open_claims(), [])

    def test_already_resolved_and_missing(self):
        db_accounts.resolve_claim(self.claim, False, 'desk')
        with self.assertRaises(db_accounts.ClaimError) as cm:
            db_accounts.resolve_claim(self.claim, True, 'desk')
        self.assertEqual(cm.exception.code, 'ya_resuelta')
        with self.assertRaises(db_accounts.ClaimError) as cm:
            db_accounts.resolve_claim(99999, True, 'desk')
        self.assertEqual(cm.exception.code, 'no_encontrada')


if __name__ == '__main__':
    unittest.main()
