"""test_alembic_single_head.py — Guard test: Alembic script directory has exactly ONE head.

This test verifies that a merge has not left duplicate migration files behind.
Multiple heads cause `upgrade(head)` to fail at boot, breaking DB access on both
prod and demo.

See docs/solutions/alembic-multi-head-prevention.md for the full incident analysis.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


class TestAlembicSingleHead(unittest.TestCase):
    """Verify Alembic migrations have exactly one head (no duplicates from merges)."""

    def test_current_repo_has_single_head(self):
        """The running repo's alembic/versions/ must have exactly one head."""
        repo_root = Path(__file__).parent.parent
        cfg = Config()
        cfg.set_main_option("script_location", os.path.join(repo_root, "alembic"))
        script_dir = ScriptDirectory.from_config(cfg)
        heads = list(script_dir.get_heads())

        self.assertEqual(
            len(heads),
            1,
            f"Expected exactly one head, but found {len(heads)}: {heads}. "
            "This usually means a merge renumbered a migration but left the old file. "
            "Delete or renumber the stale revision in alembic/versions/",
        )

    def test_detect_duplicate_head(self):
        """Verify the guard detects when a second migration file is added."""
        # Create a temporary directory with a mock Alembic structure
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            versions_dir = tmp_path / "versions"
            versions_dir.mkdir()

            # Create two migration files to simulate a duplicate-head scenario
            (versions_dir / "0001_initial.py").write_text(
                "# migration 0001\nrevision = '0001'\ndown_revision = None\n"
            )
            (versions_dir / "0002_rename.py").write_text(
                "# migration 0002\nrevision = '0002'\ndown_revision = '0001'\n"
            )
            # This simulates the bug: a stale file left behind after a renumber
            (versions_dir / "0001_rename.py").write_text(
                "# stale duplicate (should have been deleted)\nrevision = '0001_rename'\ndown_revision = None\n"
            )

            cfg = Config()
            cfg.set_main_option("script_location", str(tmp_path))
            script_dir = ScriptDirectory.from_config(cfg)
            heads = list(script_dir.get_heads())

            # Should find at least 2 heads due to the orphaned file
            self.assertGreater(
                len(heads),
                1,
                "Expected multiple heads when duplicate revisions exist",
            )


if __name__ == "__main__":
    unittest.main()
