"""Contract for the reports persistence boundary.

``admin_panel.reports`` reaches reporting persistence only through
``admin_panel.reports_store``. This suite is hermetic: it opens no database
connection and uses mocks to prove every delegation.
"""

from __future__ import annotations

import ast
import inspect
import re
import unittest
from pathlib import Path
from unittest.mock import patch, sentinel

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "app" / "admin_panel" / "reports.py"

_STORE_FUNCTIONS = [
    "reports_kpis",
    "reports_volume",
    "reports_booking_requests_by_service",
    "reports_approved_appointment",
    "reports_rejections",
    "top_searches",
    "reports_median_wait_prior",
    "reports_holds_day_hour",
    "reports_time_preference",
    "reports_funnel",
    "reports_appointment_offers",
    "ai_costs_has_rows",
    "costs_summary",
    "costs_by_operation",
    "reports_puco_found",
    "reports_export_rows",
    "reports_patients_ages",
    "reports_patients_coverage",
    "reports_patients_booking_requests_by_patient",
    "reports_patients_kpis",
    "reports_patients_volume",
    "reports_patients_active_months",
    "reports_patients_dependents",
    "stats_panel_login",
    "reports_consultations_completed",
    "reports_statistics_rows",
    "reports_booking_requests_by_week",
    "reports_searches_by_week",
    "list_approved_future",
]


class TestImportDirectionGuard(unittest.TestCase):
    def test_reports_never_imports_core_db(self):
        tree = ast.parse(REPORTS.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "core" and any(
                alias.name == "db" for alias in node.names
            ):
                self.fail(f"reports.py imports core.db directly: {ast.dump(node)}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"app.core.db", "db"}:
                        self.fail(f"reports.py imports core.db directly: {ast.dump(node)}")

    def test_reports_has_no_core_db_attribute_tokens(self):
        source = REPORTS.read_text(encoding="utf-8")
        tokens = re.findall(r"\bdb\.[A-Za-z_][A-Za-z0-9_]*", source)
        self.assertEqual(tokens, [], f"reports.py still references core.db: {tokens}")

    def test_reports_imports_its_store(self):
        self.assertIn(
            "from app.admin_panel import reports_store as store",
            REPORTS.read_text(encoding="utf-8"),
        )

    def test_reports_calls_exactly_the_audited_store_surface(self):
        tree = ast.parse(REPORTS.read_text(encoding="utf-8"))
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "store"
        }
        self.assertEqual(calls, set(_STORE_FUNCTIONS))


def _owner(name):
    """The module a store function delegates to: the domain module that owns `name`."""
    import _dbowner

    return _dbowner.owner_of(name)


class TestStoreContract(unittest.TestCase):
    def test_db_unavailable_is_the_canonical_exception(self):
        from app.admin_panel import reports_store as store
        from app.core import db

        self.assertIs(store.DbUnavailable, db.DbUnavailable)

    def test_storage_outages_propagate_unchanged(self):
        from app.admin_panel import reports_store as store
        from app.core import db, db_reports

        outage = db.DbUnavailable("offline")
        with patch.object(db_reports, "reports_kpis", side_effect=outage):
            with self.assertRaises(store.DbUnavailable) as raised:
                store.reports_kpis()
        self.assertIs(raised.exception, outage)

    def test_exact_public_surface(self):
        from app.admin_panel import reports_store as store

        expected = set(_STORE_FUNCTIONS) | {"DbUnavailable"}
        actual = {name for name in dir(store) if not name.startswith("_") and not name.startswith("db")}
        self.assertEqual(actual, expected)

    def test_signatures_and_delegations_match_core(self):
        from app.admin_panel import reports_store as store

        for name in _STORE_FUNCTIONS:
            with self.subTest(function=name):
                owner = _owner(name)
                signature = inspect.signature(getattr(owner, name))
                self.assertEqual(inspect.signature(getattr(store, name)), signature)
                args = [
                    object()
                    for parameter in signature.parameters.values()
                    if parameter.kind in (
                        parameter.POSITIONAL_ONLY,
                        parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                kwargs = {
                    parameter.name: object()
                    for parameter in signature.parameters.values()
                    if parameter.kind is parameter.KEYWORD_ONLY
                }
                with patch.object(owner, name, return_value=sentinel.result) as delegated:
                    self.assertIs(getattr(store, name)(*args, **kwargs), sentinel.result)
                self.assertEqual(delegated.call_count, 1)
                received = signature.bind(*delegated.call_args.args, **delegated.call_args.kwargs)
                expected = signature.bind(*args, **kwargs)
                self.assertEqual(received.arguments, expected.arguments)


if __name__ == "__main__":
    unittest.main()
