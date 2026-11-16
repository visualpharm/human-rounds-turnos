"""Pins the Core/HSI boundary after the durable journal extraction."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
OLD_MODULES = {
    "connectors.hsi.hsi_resync",
    "connectors.hsi.hsi_sync",
    "connectors.hsi.hsi_sync_queue",
}
OLD_NAMES = {"hsi_resync", "hsi_sync", "hsi_sync_queue"}


class TestHSICoreBoundary(unittest.TestCase):
    def test_old_vendor_path_modules_are_deleted(self):
        for module_name in OLD_MODULES:
            path = ROOT.joinpath(*module_name.split(".")).with_suffix(".py")
            self.assertFalse(path.exists(), f"retired wrapper still exists: {path}")

    def test_no_production_imports_old_vendor_paths(self):
        violations = []
        for path in ROOT.rglob("*.py"):
            relative = path.relative_to(ROOT)
            if relative.parts[0] in {"tests", ".git", ".claude"}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in OLD_MODULES:
                            violations.append(f"{relative}:{node.lineno}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module in OLD_MODULES:
                        violations.append(
                            f"{relative}:{node.lineno}: from {node.module} import ..."
                        )
                    if node.module == "app.connectors.hsi":
                        for alias in node.names:
                            if alias.name in OLD_NAMES:
                                violations.append(
                                    f"{relative}:{node.lineno}: "
                                    f"from app.connectors.hsi import {alias.name}"
                                )
        self.assertEqual([], violations)

    def test_neutral_modules_expose_the_journal_contract(self):
        from app.modules.turnos import hsi_fallback
        from app.modules.turnos import hsi_journal
        from app.modules.turnos import hsi_journal_queue

        for name in (
            "tick",
            "replay",
            "reconcile",
            "enqueue_appointment",
            "enqueue_cancel",
            "enqueue_reassign",
            "enqueue_attendance",
        ):
            self.assertTrue(hasattr(hsi_journal, name), name)
        self.assertTrue(hasattr(hsi_journal_queue, "register"))
        self.assertTrue(hasattr(hsi_journal_queue, "rekick"))
        self.assertTrue(hasattr(hsi_fallback, "tick"))
        self.assertTrue(hasattr(hsi_fallback, "start"))

    def test_persisted_queue_task_name_is_unchanged(self):
        from app.modules.turnos import hsi_journal_queue

        self.assertEqual(
            "connectors.hsi.hsi_sync.tick",
            hsi_journal_queue._TASK_NAME,
        )


if __name__ == "__main__":
    unittest.main()
