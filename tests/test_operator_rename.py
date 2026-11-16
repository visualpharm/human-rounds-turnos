"""Postgres transaction/concurrency proofs; synthetic fixtures only.
Run with scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_operator_rename.py.
"""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import _dbtest
from app.core import db
from app.core import db_operators

_lane = _dbtest.PerModuleDb("sana_turnos_test_operator_rename", seed=db_operators.operators_list)

def setUpModule():
    _lane.set_up()

def tearDownModule():
    _lane.tear_down()

class OperatorRenameTests(unittest.TestCase):
    def setUp(self):
        if not _lane.have_db:
            self.skipTest(_lane.skip_reason)
        self.owner = patch('app.auth.admin_auth.owner_usuario', return_value='owner')
        self.owner.start()
        self.addCleanup(self.owner.stop)
        with db._connect() as conn, conn.cursor() as cur:
            for table in ('operators','operator_username_history','operator_access','admin_preferences','admin_account_security','admin_reset_tokens','passkeys','passkey_challenges','appointment_events'):
                cur.execute(f"TRUNCATE {table} CASCADE")
        db_operators.operator_create('old.staff','synth-pw-1',rol='medico')

    def test_rename_moves_security_preferences_tokens_keys_and_audit(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access(username,granted,denied) VALUES ('old.staff',ARRAY['custom.allow'],ARRAY['custom.deny'])")
            cur.execute("INSERT INTO admin_preferences(username,key,value) VALUES ('old.staff','landing','\"/admin/cola\"')")
            cur.execute("INSERT INTO admin_account_security(username,totp_deadline) VALUES ('old.staff',now()-interval '1 day')")
            cur.execute("INSERT INTO admin_reset_tokens(token_hash,username,expires) VALUES ('synthetic-token','old.staff',now()+interval '1 hour')")
            cur.execute("INSERT INTO booking_requests(id,dni,first_name,status) VALUES (123,'30111999','Ana','pendiente')")
            cur.execute("INSERT INTO appointment_events(request_id,kind,actor) VALUES (123,'creada','old.staff')")
        db_operators.operator_rename('old.staff','new.staff',current_password='synth-pw-1')
        self.assertIsNone(db_operators.operator_get('old.staff'))
        self.assertTrue(db_operators.operator_verify_password('new.staff','synth-pw-1'))
        with db._connect() as conn, conn.cursor() as cur:
            for table in ('operator_access','admin_preferences','admin_account_security','admin_reset_tokens'):
                cur.execute(f"SELECT username FROM {table}")
                self.assertEqual(cur.fetchone()['username'],'new.staff')
            cur.execute("SELECT actor FROM appointment_events")
            self.assertEqual(cur.fetchone()['actor'],'new.staff')
            cur.execute("SELECT old_username,new_username FROM operator_username_history")
            self.assertEqual(cur.fetchone(),{'old_username':'old.staff','new_username':'new.staff'})
        with self.assertRaises(db_operators.OperatorConflict):
            db_operators.operator_create('OLD.STAFF','another-pw-ok')

    def test_any_dependency_failure_rolls_back_rename_and_password(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO admin_preferences(username,key,value) VALUES ('old.staff','landing','null')")
            cur.execute("CREATE FUNCTION slice2_reject_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic rollback'; END $$")
            cur.execute("CREATE TRIGGER slice2_reject_update BEFORE UPDATE ON admin_preferences FOR EACH ROW EXECUTE FUNCTION slice2_reject_update()")
        try:
            with self.assertRaises(Exception):
                db_operators.operator_rename('old.staff','new.staff',password='new-synth-pw',current_password='synth-pw-1')
            self.assertTrue(db_operators.operator_verify_password('old.staff','synth-pw-1'))
            self.assertIsNone(db_operators.operator_get('new.staff'))
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM operator_username_history")
                self.assertEqual(cur.fetchone()['n'],0)
        finally:
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("DROP TRIGGER slice2_reject_update ON admin_preferences")
                cur.execute("DROP FUNCTION slice2_reject_update()")

    def test_concurrent_case_insensitive_collision_has_one_winner(self):
        db_operators.operator_create('other.staff','synth-pw-1',rol='medico')
        def rename(pair):
            try:
                db_operators.operator_rename(*pair,current_password='synth-pw-1')
                return True
            except db_operators.OperatorConflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(rename,[('old.staff','new.staff'),('other.staff','NEW.STAFF')]))
        self.assertEqual(sorted(results),[False,True])

    def test_wrong_current_password_leaves_everything_unchanged(self):
        with self.assertRaises(db_operators.OperatorConflict):
            db_operators.operator_rename('old.staff','new.staff',current_password='wrong')
        self.assertIsNotNone(db_operators.operator_get('old.staff'))
        self.assertIsNone(db_operators.operator_get('new.staff'))
