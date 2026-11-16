"""DATA_MAP's persistent-store inventory must produce actual covered entries."""
import json
import tempfile
import unittest
from pathlib import Path
from app.ops import backup_archive as archive
from app.core import db_schema


class InventoryTest(unittest.TestCase):
    def test_documented_stores_and_every_data_top_level_are_accounted_for(self):
        doc = (archive.ROOT / "docs" / "policies" / "DATA_MAP.md").read_text()
        raw = doc.split("<!-- backup-inventory:start -->", 1)[1].split("<!-- backup-inventory:end -->", 1)[0]
        inventory = json.loads(raw)
        known = {"PostgreSQL application database", "data/**", "TURNOS_DATA_DIR/**", "TURNOS_PRIVATE_PACKS_DIR/**",
                 "TURNOS_ENV_FILE", "TURNOS_WEBDATA_DIR/**", "logs/**", "referral-analysis cache", "WebTurno local password file",
                 "browser local/session storage", "application source tree and bundled public packs",
                 "TURNOS_BACKUP_RECOVERY_KEY and TURNOS_BACKUP_OLD_KEYS_SECRET"}
        self.assertEqual({r["location"] for r in inventory}, known,
                         "A changed persistent-state location needs an explicit backup/exclusion contract")
        for row in inventory:
            self.assertIn(row["policy"], {"include", "include-redacted", "exclude", "release-pin"})
            self.assertTrue(row["reason"].strip())
        # Enumerate names only, never read real installation bytes. Reconstruct
        # EVERY top-level entry under a synthetic data root and prove capture.
        names = {p.name for p in (archive.ROOT / "data").iterdir()}
        names |= {"ordenes", "cuenta-fotos", "medico-fotos", "centro-fotos", "marca", "avatar-sprite", "private", "packs"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            for name in names:
                path = data / name / "inventory-fixture"
                path.parent.mkdir(parents=True)
                path.write_text("synthetic")
            packs = root / "private-packs"
            packs.mkdir()
            (packs / "pack.json").write_text("{}")
            env = root / "installation.env"
            env.write_text("FIXTURE=1")
            dump = root / "dump"
            dump.write_bytes(b"PGDMP-synthetic")
            result = archive.build(root / "archive.tar", dump, data_roots={"data": data},
                                   private_packs=packs, env_file=env,
                                   database={"snapshot_at": archive.timestamp(), "alembic_head": "fixture"})
        actual = {r["path"] for r in result["files"]}
        self.assertTrue({f"data/{name}/inventory-fixture" for name in names} <= actual)
        self.assertTrue({"database.dump", "config/installation.env", "private-packs/pack.json"} <= actual)
        self.assertEqual(result["status"], "complete")

    def test_journal_schema_and_forward_revision_are_classified(self):
        from app.core.backup_journal import DDL
        self.assertIn(DDL, db_schema.BASELINE_SQL)
        import importlib.util
        path = archive.ROOT / "alembic/versions/0018_backup_runs.py"
        spec = importlib.util.spec_from_file_location("backup_revision", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.down_revision, "0017_doctor_onboarding")
        self.assertEqual(module.revision, "0018_backup_runs")
