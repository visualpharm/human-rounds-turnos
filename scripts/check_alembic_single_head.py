#!/usr/bin/env python3
"""check_alembic_single_head.py — Guard against multiple Alembic heads shipping.

This script uses Alembic's ScriptDirectory to verify the migration script
directory has exactly ONE head revision. Multiple heads occur when a merge
renumbered a migration but left the old file in place, causing `upgrade(head)`
to fail at boot and breaking DB access.

Exit code: 0 if exactly one head, 1 otherwise (pre-commit will block the commit).
"""

import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def check_single_head():
    """Verify exactly one head revision exists in the Alembic versions directory."""
    repo_root = Path(__file__).parent.parent

    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(repo_root, "alembic"))
    script_dir = ScriptDirectory.from_config(cfg)

    heads = list(script_dir.get_heads())

    if len(heads) == 1:
        print(f"✓ Alembic: exactly one head — {heads[0]}")
        return 0
    else:
        print(f"✗ Alembic: multiple head revisions found: {heads}")
        print()
        print(
            "This likely means a merge renumbered a migration file but left the old one in place."
        )
        print("Fix: renumber or delete the stale revision file(s) in alembic/versions/")
        print()
        print("To check which revision a running database is at, inspect the alembic_version table:")
        print("  SELECT version_num FROM alembic_version;")
        return 1


if __name__ == "__main__":
    sys.exit(check_single_head())
