"""ops/backup_full database reads against a real Postgres.

`test_backup_full` runs the backup with the dump function replaced and `_counts` fed a mock
connection, so the manifest's database evidence — the row counts of the key tables, the files
referenced by requests and by the catalog's doctor / center documents — was never read from a
database. Here `dump_database` and `_counts` run against a real one (only `pg_dump` itself is
replaced by a recorder).
"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psycopg

import _dbtest
from app.core import db
from app.ops import backup_full as b

_database = _dbtest.PerModuleDb("sana_turnos_test_backup_full_db")


def setUpModule():
    _database.set_up()


def tearDownModule():
    _database.tear_down()


class TestDatabaseEvidence(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)
        with db._connect() as conn, conn.cursor() as cur:
            _dbtest.delete_booking_requests(cur)
            cur.execute("DELETE FROM catalog")

    def sql(self, text, params=()):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(text, params)

    def dump(self):
        calls = []
        with patch.object(b, "_tool", return_value="pg_dump"), \
                patch.object(b, "_pg_run", side_effect=lambda argv, url: calls.append(argv)), \
                patch.object(b.subprocess, "run", return_value=SimpleNamespace(stdout="pg_dump (synthetic)\n")):
            manifest = b.dump_database("/tmp/synthetic.dump", _database.test_url)
        return manifest, calls

    def test_counts_are_the_row_counts_of_the_key_tables(self):
        self.sql("INSERT INTO booking_requests (id) VALUES (1), (2), (3)")
        self.sql("INSERT INTO catalog (type, id, doc) VALUES ('medicos', 'm1', '{}'), ('centros', 'c1', '{}')")
        with psycopg.connect(_database.test_url) as conn:
            counts = b._counts(conn)
        self.assertEqual(list(counts), list(b.KEY_TABLES))
        self.assertEqual((counts["booking_requests"], counts["catalog"]), (3, 2))
        with db._connect() as conn, conn.cursor() as cur:
            for table in ("users", "operators", "appointment_events"):
                cur.execute(f"SELECT count(*) AS n FROM {table}")
                self.assertEqual(counts[table], cur.fetchone()["n"])

    def test_a_missing_key_table_fails_the_count(self):
        with psycopg.connect(_database.test_url) as conn:
            with patch.object(b, "KEY_TABLES", ("booking_requests", "no_such_table_synthetic")):
                with self.assertRaisesRegex(b.BackupError, "table"):
                    b._counts(conn)

    def test_the_manifest_lists_request_files_and_local_catalog_pictures(self):
        self.sql("INSERT INTO booking_requests (id, referral_path) VALUES (1, 'ordenes/1.png'), (2, NULL), (3, 'ordenes/3.pdf')")
        self.sql("""INSERT INTO catalog (type, id, doc, deleted) VALUES
            ('medicos', 'm1', '{"foto": "/data/medicos/m1.jpg?v=3", "otra": ["data/x.png", "https://cdn/y.png"], "n": {"deep": "/data/deep.png"}}', false),
            ('centros', 'c1', '{"imagen": "/data/centros/c1.jpg"}', false),
            ('medicos', 'm2', '{"foto": "/data/medicos/deleted.jpg"}', true),
            ('servicios', 's1', '{"foto": "/data/servicios/s1.jpg"}', false)""")
        manifest, calls = self.dump()
        refs = set(manifest["references"])
        self.assertTrue({"ordenes/1.png", "ordenes/3.pdf"} <= refs)
        self.assertTrue({"/data/medicos/m1.jpg", "data/x.png", "/data/deep.png", "/data/centros/c1.jpg"} <= refs)
        self.assertNotIn("/data/medicos/deleted.jpg", refs, "a deleted catalog row is not a reference")
        self.assertNotIn("/data/servicios/s1.jpg", refs, "only doctors and centers are scanned")
        self.assertNotIn("https://cdn/y.png", refs)
        self.assertEqual(len(refs), 6)

    def test_the_manifest_carries_the_alembic_heads_the_counts_and_the_dump_command(self):
        self.sql("INSERT INTO booking_requests (id) VALUES (1)")
        manifest, calls = self.dump()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            head = cur.fetchone()["version_num"]
        self.assertEqual(manifest["alembic_head"], head)
        self.assertEqual(manifest["expected_alembic_head"], head)
        self.assertEqual(manifest["counts"]["booking_requests"], 1)
        self.assertEqual(manifest["tools"]["pg_dump"], "pg_dump (synthetic)")
        (argv,) = calls
        self.assertEqual(argv[:2], ["pg_dump", "--format=custom"])
        self.assertTrue(any(a.startswith("--snapshot=") and len(a) > len("--snapshot=") for a in argv))
        self.assertTrue(argv[-1].endswith("/tmp/synthetic.dump"))

    def test_the_dump_reads_one_consistent_snapshot_in_a_read_only_transaction(self):
        statements = []
        original = psycopg.Cursor.execute

        def spy(self, query, *args, **kwargs):
            statements.append(query if isinstance(query, str) else str(query))
            return original(self, query, *args, **kwargs)

        with patch.object(psycopg.Cursor, "execute", spy):
            self.dump()
        self.assertTrue(statements[0].startswith("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        self.assertIn("pg_export_snapshot", statements[1])


class TestRestoreTargets(unittest.TestCase):
    """The empty-target probe of `restore_database` and the create / verify / drop of
    `scratch_restore`, on a real server (the restore tools themselves are replaced)."""

    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)
        self.created = []

    def tearDown(self):
        for name in self.created:
            with psycopg.connect(_database.maint_url, autocommit=True) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    def make_db(self, ddl=None):
        import uuid
        name = "hr_synth_" + uuid.uuid4().hex[:12]
        self.created.append(name)
        with psycopg.connect(_database.maint_url, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        url = psycopg.conninfo.make_conninfo(_database.maint_url, dbname=name)
        if ddl:
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute(ddl)
        return name, url

    def exists(self, name):
        with psycopg.connect(_database.maint_url) as conn:
            return conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is not None

    def restore(self, url):
        calls = []

        def fake_pg_run(argv, target):
            calls.append(argv)
            for arg in argv:  # a server before 17 restores through SQL text: pg_restore writes the file
                if arg.startswith("--file=") and argv[0] == "pg_restore":
                    Path(arg[len("--file="):]).write_text("SELECT 1;\n")

        with patch.object(b, "_tool", side_effect=lambda name: name), patch.object(b, "_pg_run", side_effect=fake_pg_run):
            b.restore_database("/tmp/synthetic.dump", url, live_url="postgresql://x@127.0.0.1/other")
        return calls

    def test_an_empty_database_is_a_valid_restore_target(self):
        _, url = self.make_db()
        self.assertEqual(self.restore(url)[0][0], "pg_restore")

    def test_any_table_function_or_view_makes_the_target_non_empty(self):
        for ddl in ("CREATE TABLE synthetic_t (id int)",
                    "CREATE FUNCTION synthetic_f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
                    "CREATE VIEW synthetic_v AS SELECT 1 AS one"):
            _, url = self.make_db(ddl)
            with self.assertRaisesRegex(b.BackupError, "empty"):
                self.restore(url)

    def scratch(self, restore_side_effect, manifest):
        with patch.object(b, "restore_database", side_effect=restore_side_effect):
            return b.scratch_restore("/tmp", manifest, _database.maint_url, live_url=_database.test_url)

    def test_a_verified_scratch_restore_drops_its_database(self):
        seen = []

        def fake_restore(dump, target, live_url=None):
            seen.append(target)
            with psycopg.connect(target, autocommit=True) as conn:
                conn.execute("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
                conn.execute("INSERT INTO alembic_version VALUES ('synthetic_head')")
                for table in b.KEY_TABLES:
                    conn.execute(f"CREATE TABLE {table} (id int)")
                conn.execute("INSERT INTO booking_requests VALUES (1), (2)")

        counts = {t: 0 for t in b.KEY_TABLES}
        counts["booking_requests"] = 2
        result = self.scratch(fake_restore, {"alembic_head": "synthetic_head", "row_counts": counts})
        self.assertEqual((result["alembic_head"], result["row_counts"]), ("synthetic_head", counts))
        self.assertTrue(result["database"].startswith("hr_backup_test_"))
        self.assertFalse(self.exists(result["database"]), "a verified scratch database is dropped")

    def test_a_failed_scratch_restore_keeps_its_database_and_names_it(self):
        def failing(dump, target, live_url=None):
            raise RuntimeError("restore broke")

        with self.assertRaisesRegex(b.BackupError, r"retained database (hr_backup_test_\w+)") as caught:
            self.scratch(failing, {"alembic_head": "h", "row_counts": {}})
        name = str(caught.exception).rsplit(" ", 1)[1]
        self.created.append(name)
        self.assertTrue(self.exists(name))


if __name__ == "__main__":
    unittest.main()
