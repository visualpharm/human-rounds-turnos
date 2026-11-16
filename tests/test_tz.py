"""test_tz.py — tz.py: the installation timezone (phase 2c, 2026-07-30).

Covers the precedence chain (env > packs/ar/country.json > literal fallback),
that a bad/missing tz name degrades to the fallback instead of raising, and
that `tzinfo()` returns a real zoneinfo (DST-safe, not a fixed offset).

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_tz.py"
"""

import os
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import tz  # noqa: E402


class TestTzDefaults(unittest.TestCase):
    """Against the REAL checkout's packs/ar/country.json (Pinamar/Argentina,
    no monkeypatching) — proves the module's own module-level constants
    resolve correctly today, same spirit as test_site_identity.py's
    real-data assertions."""

    def test_tz_name_is_argentina_by_default(self):
        self.assertEqual(tz.tz_name(), "America/Argentina/Buenos_Aires")

    def test_tzinfo_is_a_real_zoneinfo_not_a_fixed_offset(self):
        self.assertIsInstance(tz.tzinfo(), ZoneInfo)

    def test_tzinfo_offset_matches_known_art_offset(self):
        from datetime import datetime
        d = datetime(2026, 7, 30, 12, 0, tzinfo=tz.tzinfo())
        self.assertEqual(d.utcoffset().total_seconds(), -3 * 3600)


class TestLoadTzNamePrecedence(unittest.TestCase):
    """Exercises `_load_tz_name()` directly (the precedence function) rather
    than the module-level constants — those are frozen at import time, so a
    live env/env-var flip can't be observed through them once tz.py has
    already been imported by the suite."""

    def setUp(self):
        _env.set_env(self, TURNOS_TIMEZONE=None)

    def test_env_override_wins(self):
        _env.set_env(self, TURNOS_TIMEZONE="Europe/Madrid")
        self.assertEqual(tz._load_tz_name(), "Europe/Madrid")

    def test_no_env_reads_country_json(self):
        # No override -> resolves through data_packs to the real
        # packs/ar/country.json shipped in this checkout.
        self.assertEqual(tz._load_tz_name(), "America/Argentina/Buenos_Aires")


class TestBadTzNameDegradesToFallback(unittest.TestCase):
    """An invalid IANA id (broken .env, typo'd pack) must never crash import
    — `_resolve()` (the function `TZ_NAME`/`TZINFO` are built from at import
    time) degrades to the literal fallback, same contract as format.py's
    PHONE. `_load_tz_name()` itself does NOT validate — env/pack values pass
    through as-is; `_resolve()` is where the guard lives, so it's what gets
    exercised directly here."""

    def test_unknown_zone_name_falls_back(self):
        name, tzinfo = tz._resolve("Not/A_Real_Zone")
        self.assertEqual(name, tz._FALLBACK_TZ_NAME)
        self.assertIsInstance(tzinfo, ZoneInfo)

    def test_known_zone_name_passes_through(self):
        name, tzinfo = tz._resolve("Europe/Madrid")
        self.assertEqual(name, "Europe/Madrid")
        self.assertIsInstance(tzinfo, ZoneInfo)


if __name__ == "__main__":
    unittest.main()
