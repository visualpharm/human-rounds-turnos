"""Contract test for the booking-requests persistence-boundary slice.

Pins the boundary drawn by db-coupling slice 2
(docs/roadmap/db-coupling-slice2-booking-store.md): after this slice,
modules/turnos/booking_requests.py may reach persistence ONLY through
modules/turnos/booking_requests_store.py — never through core.db directly. Since the cut by
action (TASK-60, 2026-11-06) the same holds for every module split out of it (BOOKING_FILES).
Hermetic: no DB, no fixtures, no network.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from app.core import db_booking

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "app" / "modules" / "turnos" / "booking_requests.py"
# booking_requests.py and the modules cut out of it by action (TASK-60, 2026-11-06).
BOOKING_FILES = [ORCHESTRATOR] + [ROOT / "app" / "modules" / "turnos" / (name + ".py") for name in (
    "booking_create", "booking_referrals", "booking_queue", "booking_decisions", "booking_patient",
    "booking_capacity", "booking_emr", "panel_settings")]

# The original 36-function inventory plus the reviewed overbooking count
# extension documented in the roadmap's September 4 addendum. Kept as
# a literal list so a `dir()`-diff below catches accidental extras.
_STORE_FUNCTIONS = [
    "create_booking_request_with_limit",
    "get_booking_request",
    # 2026-10-09: the desk completes a booking's patient data (names, birth
    # date, sex) and the account gains what it was missing.
    "update_booking_patient_data",
    "user_admin_update",
    "get_booking_request_by_idem_key",
    "set_referral",
    "save_ai",
    "clear_referral_path",
    "referrals_to_purge",
    "purged_file_record",
    "purged_file_paths",
    "assign_pending_booking_request",
    "approve",
    "reject",
    "cancel",
    "cancel_actor",
    # 2026-11-05: the desk restores a cancelled turno from the patient's ficha
    # (the product owner): which state it was cancelled from, and whether its time is taken.
    "cancelled_from",
    "slot_taken",
    "mark_attendance",
    "update_hsi",
    "reassign_appointment",
    "reschedule_preference",
    "list_admin",
    "count_admin",
    "list_by_dni",
    "approved_in_range",
    "list_approved_future",
    "overbooking_count",
    "attendance_summary_dni",
    "attendance_summary_by_dnis",
    "hold_link_booking_request",
    "holds_release_booking_request",
    "hold_of_booking_request",
    "hold_release",
    "waitlist_active",
    "get_user",
    "operator_doctor_id",
    "get_dependent",
    "interview_for_request",
    "hsi_appointments_by_dni",
    "hsi_sync_status",
    "hsi_outbox_reset",
    "refeps_get",
    "refeps_set",
]

_STORE_EXCEPTIONS = ["DbUnavailable", "LimitReached", "AlreadyProcessed", "SlotConflict", "OverbookingCapacityReached"]


class TestImportDirectionGuard(unittest.TestCase):
    """booking_requests.py and its split modules must not import app.core.db, or reference `db.` at all."""

    def test_no_core_db_import(self):
        for path in BOOKING_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "core" and any(
                    alias.name == "db" for alias in node.names
                ):
                    self.fail(f"{path.name} imports core.db directly: {ast.dump(node)}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"app.core.db", "db"}:
                            self.fail(f"{path.name} imports core.db directly: {ast.dump(node)}")

    def test_no_db_dot_token(self):
        # Simplest robust form (matches the repo's other source-scanning
        # tests): scan the raw source for the literal "db.<name>" token used
        # as an attribute access, in code AND in prose/docstring citations —
        # the slice re-worded prose citations to plain function names.
        import re

        for path in BOOKING_FILES:
            src = path.read_text(encoding="utf-8")
            tokens = re.findall(r"\bdb\.[A-Za-z_][A-Za-z0-9_]*", src)
            self.assertEqual(tokens, [], f"{path.name} still references core.db: {tokens}")

    def test_imports_store_instead(self):
        for path in BOOKING_FILES:
            src = path.read_text(encoding="utf-8")
            self.assertIn(
                "from app.modules.turnos import booking_requests_store as store",
                src,
                f"{path.name} must import booking_requests_store as its persistence boundary",
            )


class TestStoreIdentityGuard(unittest.TestCase):
    """The five re-exported exceptions must be the exact core.db_booking objects."""

    def test_exceptions_are_reexports_not_copies(self):
        from app.core import db, db_booking
        from app.modules.turnos import booking_requests_store as store

        for name in _STORE_EXCEPTIONS:
            owner = db if name == "DbUnavailable" else db_booking
            with self.subTest(exception=name):
                self.assertIs(
                    getattr(store, name),
                    getattr(owner, name),
                    f"store.{name} must be `is` {owner.__name__}.{name} (re-export, not a subclass/copy)",
                )


class TestStoreSurfaceGuard(unittest.TestCase):
    """The store exposes exactly the audited inventory (37 functions + the two `purged_file_*`
    journal calls of the referral retention, schema review №11)."""

    def test_overbooking_count_preserves_all_scope_arguments(self):
        from unittest.mock import patch
        from app.modules.turnos import booking_requests_store as store

        with patch.object(db_booking, "overbooking_count", return_value=2) as count:
            self.assertEqual(store.overbooking_count(
                "2099-01-01", "2099-01-02", "Test Doctor", medico_id=7,
                center_slug="test-center", exclude_sid=11,
            ), 2)
        count.assert_called_once_with(
            desde="2099-01-01", hasta="2099-01-02", medico="Test Doctor",
            medico_id=7, center_slug="test-center", exclude_sid=11,
        )

    def test_every_inventoried_function_present_and_callable(self):
        from app.modules.turnos import booking_requests_store as store

        for name in _STORE_FUNCTIONS:
            with self.subTest(function=name):
                self.assertTrue(hasattr(store, name), f"store is missing {name}")
                self.assertTrue(callable(getattr(store, name)), f"store.{name} is not callable")

    def test_no_undocumented_extras(self):
        from app.modules.turnos import booking_requests_store as store

        expected = set(_STORE_FUNCTIONS) | set(_STORE_EXCEPTIONS)
        actual_public = {
            name
            for name in dir(store)
            if not name.startswith("_") and not name.startswith("db")
        }
        extras = actual_public - expected
        self.assertEqual(
            extras,
            set(),
            f"booking_requests_store exposes undocumented extras beyond the audited inventory: {extras}",
        )


if __name__ == "__main__":
    unittest.main()
