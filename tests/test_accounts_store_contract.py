"""Contract for the accounts persistence boundary.

The account domain reaches account, session, dependent, reset-token, and OTP
storage through cuentas.accounts_store, never core.db directly. This suite is
hermetic: it opens no database connection.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path
from unittest.mock import patch, sentinel

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = ROOT / "app" / "cuentas" / "accounts.py"

_STORE_FUNCTIONS = [
    "get_session",
    "consume_verified_otp",
    "get_user",
    "user_by_portal_id",
    "user_claim_portal",
    "user_fill_name",
    "get_user_by_email",
    "create_user",
    "create_user_and_session",
    "create_session",
    "verify_password",
    "update_user",
    "delete_session",
    "list_dependents",
    "get_dependent",
    "save_dependent",
    "delete_dependent",
    "change_dni",
    "create_reset_token",
    "otp_recent",
    "create_otp",
    "verify_otp",
    "set_password",
    "consume_reset_token",
    "mcp_token_revoke",
    "open_claim_of",
    "pending_claimants",
]


class TestImportDirectionGuard(unittest.TestCase):
    def test_accounts_never_imports_core_db_or_references_db_attributes(self):
        tree = ast.parse(ACCOUNTS.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "core" and any(
                alias.name == "db" for alias in node.names
            ):
                self.fail(f"accounts.py imports core.db directly: {ast.dump(node)}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"app.core.db", "db"}:
                        self.fail(f"accounts.py imports core.db directly: {ast.dump(node)}")
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "db":
                self.fail(f"accounts.py uses db directly: {ast.dump(node)}")

    def test_accounts_imports_its_store(self):
        self.assertIn(
            "from app.cuentas import accounts_store as store",
            ACCOUNTS.read_text(encoding="utf-8"),
        )


class TestStoreContract(unittest.TestCase):
    def test_db_unavailable_is_the_canonical_exception(self):
        from app.core import db
        from app.core import db_accounts
        from app.cuentas import accounts_store as store

        self.assertIs(store.DbUnavailable, db.DbUnavailable)
        self.assertIs(store.UserKeyTaken, db_accounts.UserKeyTaken)
        self.assertIs(store.UserEmailTaken, db_accounts.UserEmailTaken)

    def test_exact_public_surface(self):
        from app.cuentas import accounts_store as store

        expected = set(_STORE_FUNCTIONS) | {"DbUnavailable", "UserKeyTaken", "UserEmailTaken"}
        actual = {name for name in dir(store) if not name.startswith("_") and not name.startswith("db")}
        self.assertEqual(actual, expected)

    def test_signatures_and_delegations_match_core(self):
        import _dbowner
        from app.cuentas import accounts_store as store

        for name in _STORE_FUNCTIONS:
            with self.subTest(function=name):
                owner = _dbowner.owner_of(name)
                signature = inspect.signature(getattr(owner, name))
                self.assertEqual(inspect.signature(getattr(store, name)), signature)
                args = [object() for parameter in signature.parameters.values()
                        if parameter.kind in (parameter.POSITIONAL_ONLY,
                                              parameter.POSITIONAL_OR_KEYWORD)]
                kwargs = {parameter.name: object() for parameter in signature.parameters.values()
                          if parameter.kind is parameter.KEYWORD_ONLY}
                with patch.object(owner, name, return_value=sentinel.result) as delegated:
                    self.assertIs(getattr(store, name)(*args, **kwargs), sentinel.result)
                self.assertEqual(delegated.call_count, 1)
                received = signature.bind(*delegated.call_args.args, **delegated.call_args.kwargs)
                expected = signature.bind(*args, **kwargs)
                self.assertEqual(received.arguments, expected.arguments)


if __name__ == "__main__":
    unittest.main()
