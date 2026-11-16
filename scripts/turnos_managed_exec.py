#!/usr/bin/env python3
"""Run one fixed Human Rounds local profile with a scrubbed environment.

This is deliberately an argv-only launcher: profiles build their own command
arrays and execution uses ``os.execve``.  It never accepts an arbitrary shell
command, never prints injected values, and replaces (rather than extends) the
caller's environment.  Production Coolify containers continue to inject their
own environment and run their configured command directly.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import managed_secrets  # noqa: E402


_FIXED_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_DB_KEYS = frozenset({"DATABASE_URL", "TEST_DATABASE_URL"})
_CONTROL_KEYS = frozenset({
    "PATH", "HOME", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    "TURNOS_ENV_FILE", "TURNOS_SECRET_SOURCE", "TURNOS_MANAGED_PROFILE",
})
_FOCUSED_TEST = re.compile(r"^tests/(?:test_[A-Za-z0-9_]+|managed_exec_child)\.py$")
# One browser spec file: a plain name, no path segments, no dots but the suffix.
_E2E_FOCUSED_TEST = re.compile(r"^tests/e2e/[a-z0-9][A-Za-z0-9_-]*\.spec\.mjs$")
_LOCAL_CI_COMMAND = ["/bin/bash", "scripts/ci_local.sh"]

# The local agents-hub vault broker (``run`` mode) is the only way the live
# credential document reaches this process.  Its application entry
# (``managed_secrets.BROKER_APPLICATION`` in the broker config) runs this
# interpreter with this script's own path, binding the vault field to
# ``managed_secrets.BROKER_ENV_NAME``.  These are fixed locations, never
# caller-selected.
_BROKER_HOME = Path.home()
_BROKER_PYTHON = _BROKER_HOME / ".local/share/agents-hub-vault/venv/bin/python"
_BROKER_CORE = _BROKER_HOME / "projects/agents-hub-core"
_BROKER_CONFIG = _BROKER_HOME / ".config/agents-hub-vault/config.json"
# Set on the broker hop so a broker that did not inject the document fails
# closed instead of re-launching itself.
_BROKER_HOP = "TURNOS_MANAGED_BROKER_HOP"


def _db_key(key: str) -> bool:
    return key in _DB_KEYS


def _runtime_key(key: str) -> bool:
    return key not in _CONTROL_KEYS and managed_secrets.is_runtime_key(key)


@dataclass(frozen=True)
class Profile:
    name: str
    command: Callable[[str | None], list[str]]
    allowed_key: Callable[[str], bool]
    focused: bool = False
    # A sibling profile can deliberately reuse an existing source mapping
    # without requiring duplicate credentials in the provider item.
    source_profile: str | None = None
    # Values controlled by this profile alone, applied after its source mapping
    # has been validated.  They are never caller input or source material.
    fixed_environment: tuple[tuple[str, str], ...] = ()


def _no_arg(command: list[str]) -> Callable[[str | None], list[str]]:
    def build(value: str | None) -> list[str]:
        if value is not None:
            raise ValueError("this profile does not accept a test selector")
        return list(command)
    return build


def _focused(value: str | None) -> list[str]:
    if not value or not _FOCUSED_TEST.fullmatch(value):
        raise ValueError("focused tests must be one tests/test_*.py file")
    # One file, on a database of its own on the test server, dropped afterwards — the
    # shared development database's revision is never the file's (2026-11-06).
    return [
        sys.executable, "-W", "error::ResourceWarning", "scripts/run_backend_focused.py",
        Path(value).name,
    ]


def _e2e_focused(value: str | None) -> list[str]:
    if not value or not _E2E_FOCUSED_TEST.fullmatch(value):
        raise ValueError("focused e2e must be one tests/e2e/<name>.spec.mjs file")
    return ["/opt/homebrew/bin/npx", "playwright", "test", "--workers=1", value]


PROFILES = {
    "backend-focused": Profile("backend-focused", _focused, _db_key, focused=True),
    "backend-full": Profile(
        "backend-full",
        # The same one-process discover, on a database of its own (see _focused).
        _no_arg([sys.executable, "-W", "error::ResourceWarning",
                 "scripts/run_backend_focused.py", "test_*.py"]),
        _db_key,
    ),
    "routes": Profile("routes", _no_arg(["/bin/bash", "scripts/run_routes_tests.sh"]), _db_key),
    "local-ci": Profile("local-ci", _no_arg(_LOCAL_CI_COMMAND), _db_key),
    # This is deliberately a sibling of (not a change to) ``local-ci``.  It
    # runs the same fixed five-surface command while making ASGI mode an
    # executor-owned test condition, after the DB-only source values pass
    # validation.  No source or ambient value can select the mode.
    "local-ci-asgi": Profile(
        "local-ci-asgi", _no_arg(_LOCAL_CI_COMMAND), _db_key,
        source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"),),
    ),
    # A standalone browser gate can reproduce a UI failure without repeating
    # the entire backend ladder. It is not a substitute for local-ci-asgi.
    "e2e-asgi": Profile(
        "e2e-asgi", _no_arg(["/opt/homebrew/bin/npm", "run", "e2e"]), _db_key,
        source_profile="local-ci",
        # The same four workers as scripts/ci_local.sh's e2e surface.
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1"),
                           ("PW_WORKERS", "4")),
    ),
    # Any ONE browser spec file, selected with ``--test tests/e2e/<name>.spec.mjs``
    # (the name is matched against a closed pattern, never passed to a shell).
    # Same credential boundary and fixed serving mode as the narrow profiles
    # below, which remain for the specs that have one.
    "e2e-focused": Profile(
        "e2e-focused", _e2e_focused, _db_key, focused=True,
        source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    # Narrow diagnostic profiles retain the full gate's credential boundary.
    # Their test files are constants: no caller command, path, or environment
    # selector can turn a failed browser check into arbitrary execution.
    "e2e-account-asgi": Profile(
        "e2e-account-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/account-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-account-delayed-asgi": Profile(
        "e2e-account-delayed-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/account-delayed.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-admin-screen-asgi": Profile(
        "e2e-admin-screen-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-screen.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-account-reset-asgi": Profile(
        "e2e-account-reset-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/account-reset-keeps-booking.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-home-search-asgi": Profile(
        "e2e-home-search-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/search-delayed.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-home-listing-asgi": Profile(
        "e2e-home-listing-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/listing-delayed.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-public-chrome-asgi": Profile(
        "e2e-public-chrome-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/public-chrome.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-ai-off-asgi": Profile(
        "e2e-ai-off-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/ai-off.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        # The spec blanks the fake itself for the app it starts (appOptions.env).
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-journey-j10-asgi": Profile(
        "e2e-journey-j10-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/journey-j10-walkin-handoff.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-journey-j11-asgi": Profile(
        "e2e-journey-j11-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/journey-j11-booking-to-prescription.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-journey-j12-asgi": Profile(
        "e2e-journey-j12-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/journey-j12-reopen-confirmed-visit.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-journey-j13-asgi": Profile(
        "e2e-journey-j13-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/journey-j13-schedule-manager-slots.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-journey-j14-asgi": Profile(
        "e2e-journey-j14-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/journey-j14-disable-doctor-archive-patient.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-journey-j15-asgi": Profile(
        "e2e-journey-j15-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/journey-j15-doctor-sees-own-patients.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-journey-j16-asgi": Profile(
        "e2e-journey-j16-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/journey-j16-referral-approval-reminder.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-solicitudes-detail-asgi": Profile(
        "e2e-solicitudes-detail-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-solicitudes-detail-in-view.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-aprobar-race-asgi": Profile(
        "e2e-aprobar-race-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-solicitudes-agenda-race.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-doctor-queue-header-asgi": Profile(
        "e2e-doctor-queue-header-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/doctor-queue-header.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-progressive-disclosure-asgi": Profile(
        "e2e-progressive-disclosure-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-progressive-disclosure.spec.mjs",
            "tests/e2e/control-fit-canonical.spec.mjs",
            "tests/e2e/admin-screen.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-doctor-schedules-asgi": Profile(
        "e2e-doctor-schedules-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/doctor-schedules.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-system-asgi": Profile(
        "e2e-system-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-system-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-ai-register-asgi": Profile(
        "e2e-ai-register-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-ai-register.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-settings-asgi": Profile(
        "e2e-settings-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-settings-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-plan-asgi": Profile(
        "e2e-plan-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-plan-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-alerts-asgi": Profile(
        "e2e-alerts-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-shell.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-reports-asgi": Profile(
        "e2e-reports-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-reports-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-appearance-asgi": Profile(
        "e2e-appearance-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-appearance-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-hsi-asgi": Profile(
        "e2e-hsi-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-hsi-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-hsi-sync-asgi": Profile(
        "e2e-hsi-sync-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/hsi-sync-entries.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-patients-native-asgi": Profile(
        "e2e-patients-native-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-patients-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-doctor-native-asgi": Profile(
        "e2e-doctor-native-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-doctor-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-preparations-native-asgi": Profile(
        "e2e-preparations-native-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-preparations-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-admin-auth-asgi": Profile(
        "e2e-admin-auth-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-patient-portal-asgi": Profile(
        "e2e-patient-portal-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/patient-portal.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-staff-account-asgi": Profile(
        "e2e-staff-account-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-staff-account-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-consultation-asgi": Profile(
        "e2e-consultation-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-consultation-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-day-queue-asgi": Profile(
        "e2e-day-queue-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-day-queue-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-create-appointment-context-asgi": Profile(
        "e2e-create-appointment-context-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/create-appointment-context.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-catalog-native-asgi": Profile(
        "e2e-catalog-native-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/catalog-screens-lifecycle.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-doctor-center-window-asgi": Profile(
        "e2e-doctor-center-window-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/doctor-center-window.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-doctor-second-specialty-asgi": Profile(
        "e2e-doctor-second-specialty-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/doctor-second-specialty.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-roles-asgi": Profile(
        "e2e-roles-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-roles-save.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    # TASK-3 form audit: the forms the 2026-10-25 inventory found untested, and
    # the older round-trip file whose partial checks it completed.
    "e2e-forms-audit-asgi": Profile(
        "e2e-forms-audit-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-forms-audit.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-forms-audit-desk-asgi": Profile(
        "e2e-forms-audit-desk-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-forms-audit-desk.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-forms-audit-catalog-asgi": Profile(
        "e2e-forms-audit-catalog-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-forms-audit-catalog.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-passkeys-asgi": Profile(
        "e2e-passkeys-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/passkeys.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-forms-roundtrip-asgi": Profile(
        "e2e-forms-roundtrip-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-forms-roundtrip.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-role-screens-asgi": Profile(
        "e2e-role-screens-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-role-screens-no-403.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-queue-sse-asgi": Profile(
        "e2e-queue-sse-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/queue-sse.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-footer-small-asgi": Profile(
        "e2e-footer-small-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/footer-small.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-english-labels-asgi": Profile(
        "e2e-english-labels-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/english-labels-icons.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-english-panel-asgi": Profile(
        "e2e-english-panel-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/english-panel-sweep.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-listing-fit-asgi": Profile(
        "e2e-listing-fit-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/listing-long-name-fit.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-booking-upload-chooser-asgi": Profile(
        "e2e-booking-upload-chooser-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/booking-upload-chooser.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-booking-first-paint-asgi": Profile(
        "e2e-booking-first-paint-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/booking-first-paint.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-footer-late-asgi": Profile(
        "e2e-footer-late-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/footer-late-layout-shift.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-confirm-asgi": Profile(
        "e2e-confirm-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/confirma-telefono.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-tiles-asgi": Profile(
        "e2e-tiles-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/tiles-canonical.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-phone-gutter-asgi": Profile(
        "e2e-phone-gutter-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-phone-gutter.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    "e2e-admin-left-edge-asgi": Profile(
        "e2e-admin-left-edge-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=1",
            "tests/e2e/admin-left-edge.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    # The specs that serve their own static map (lib/static-deps.mjs) and the two
    # that pin the panel's disclosure/left-edge markup, each repeated 12 times with
    # no retry on CI's four workers: a pass here is not a lucky retry.
    "e2e-synthetic-static-repeat-asgi": Profile(
        "e2e-synthetic-static-repeat-asgi", _no_arg([
            "/opt/homebrew/bin/npx", "playwright", "test", "--workers=4",
            "--repeat-each=12", "--retries=0",
            "tests/e2e/admin-roles-save.spec.mjs",
            "tests/e2e/admin-simplification.spec.mjs",
            "tests/e2e/catalog-screens-lifecycle.spec.mjs",
            "tests/e2e/intake-controls.spec.mjs",
        ]), _db_key, source_profile="local-ci",
        fixed_environment=(("TURNOS_ASGI", "1"), ("TURNOS_FAKE_AI", "1")),
    ),
    # The historical "app" profile ran the legacy `python3 server.py`
    # topology; that topology is retired (native-migration campaign,
    # docs/roadmap/native-migration-campaign-20260826.md) — "asgi" is the
    # one way to run the live app.  asgi_main.py refuses to boot without
    # TURNOS_ASGI=1, so the profile sets it itself.
    "asgi": Profile(
        "asgi", _no_arg([sys.executable, "asgi_main.py"]), _runtime_key,
        fixed_environment=(("TURNOS_ASGI", "1"),),
    ),
    "worker": Profile("worker", _no_arg([sys.executable, "worker.py"]), _runtime_key),
    "worker-jobs": Profile("worker-jobs", _no_arg([sys.executable, "worker.py", "--jobs"]), _runtime_key),
}


def build_environment(
    profile: str,
    values: dict[str, str],
    fixed_environment: tuple[tuple[str, str], ...] = (),
) -> dict[str, str]:
    """Build a new child environment; no ambient application value survives."""
    env = {
        "PATH": _FIXED_PATH,
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        # The profile source is the only local secret/config source.  The
        # transitional checkout .env is explicitly not overlaid into it.
        "TURNOS_ENV_FILE": os.devnull,
        "TURNOS_MANAGED_PROFILE": profile,
    }
    env.update(values)
    # These are reviewed executor constants, not caller/source configuration.
    # Apply them last so a future profile cannot accidentally let a validated
    # secret mapping override a serving-mode control.
    env.update(fixed_environment)
    return env


def resolve(profile_name: str, test: str | None, source_spec: str | None = None):
    """Return ``(argv, env)`` without executing it; useful for synthetic tests."""
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise ValueError("unknown managed profile")
    argv = profile.command(test)
    # ``source_spec`` is an explicit direct-injection seam for synthetic unit
    # tests. The real managed CLI calls source_from_environment(), which
    # permits the fixed credential vault provider only.
    source = (managed_secrets.source_from_spec(source_spec)
              if source_spec is not None else managed_secrets.source_from_environment())
    values = source.values_for(profile.source_profile or profile.name, profile.allowed_key)
    return argv, build_environment(profile.name, values, profile.fixed_environment)


def broker_command(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Return ``(argv, env)`` that re-runs this script under the vault broker.

    The broker hop gets a fixed environment only; the broker application
    entry clears ``PYTHONPATH`` before it starts this script again.
    """
    argv = [
        str(_BROKER_PYTHON), "-P", "-m", "modules.secrets.onepassword",
        "--config", str(_BROKER_CONFIG),
        "run", managed_secrets.BROKER_APPLICATION, "--",
        str(Path(__file__).resolve()), *args,
    ]
    env = {
        "PATH": _FIXED_PATH,
        "HOME": str(_BROKER_HOME),
        "PYTHONPATH": str(_BROKER_CORE),
        "PYTHONDONTWRITEBYTECODE": "1",
        _BROKER_HOP: "1",
    }
    return argv, env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="execute one fixed Human Rounds profile")
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--test", help="one approved focused test file; backend-focused and e2e-focused only")
    parser.add_argument("--check", action="store_true", help="validate silently; do not execute")
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_args)
    try:
        # Validate the selector before any broker hop: a rejected command
        # never reaches the vault.
        PROFILES[args.profile].command(args.test)
        source = managed_secrets.source_from_environment()
        if not source.available():
            if os.environ.get(_BROKER_HOP):
                raise managed_secrets.SecretSourceError(
                    "credential vault document was not injected by the broker")
            broker_argv, broker_env = broker_command(raw_args)
            try:
                os.execve(broker_argv[0], broker_argv, broker_env)
            except OSError:
                raise managed_secrets.SecretSourceError(
                    "credential vault broker is unavailable") from None
        command, env = resolve(args.profile, args.test)
    except (ValueError, managed_secrets.SecretSourceError) as exc:
        print(f"managed execution refused: {exc}", file=sys.stderr)
        return 2
    if args.check:
        return 0
    os.chdir(ROOT)
    os.execve(command[0], command, env)
    return 127  # pragma: no cover - execve does not return


if __name__ == "__main__":
    raise SystemExit(main())
