"""Execute the real backup helper without importing the database-seeding script."""
import ast
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import _env  # noqa: E402


class TestUsecaseCredentialBackup(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).parent / "usecases" / "fixtures.py"
        tree = ast.parse(source.read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_creds_to_vault")
        namespace = {"json": json, "os": os}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        self.backup = namespace["_creds_to_vault"]
        self.creds = {"admin_user": "fixture-user", "admin_pass": "fixture-admin-secret",
                      "patient_dni": "fixture-patient", "patient_pass": "fixture-patient-secret"}

    def test_both_credentials_use_stdin_never_process_arguments(self):
        with _env.patched_env({"TURNOS_TEST_CREDENTIAL_VAULT": "fixture-vault"}), \
             patch.object(subprocess, "run") as run:
            self.backup(self.creds)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["op", "item", "create", "-"])
        self.assertEqual(argv[argv.index("--vault") + 1], "fixture-vault")
        for secret in self.creds.values():
            self.assertNotIn(secret, " ".join(argv))
        item = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(item["category"], "LOGIN")
        concealed = {field["value"] for field in item["fields"] if field["type"] == "CONCEALED"}
        self.assertEqual(concealed, {self.creds["admin_pass"], self.creds["patient_pass"]})
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_missing_optional_vault_command_is_nonfatal(self):
        with patch.object(subprocess, "run", side_effect=FileNotFoundError):
            self.backup(self.creds)
