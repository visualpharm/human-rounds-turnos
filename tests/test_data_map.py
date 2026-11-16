"""Static contracts for the Tier 7 data map (root `DATA_MAP.md`).

The map is deliberately a repository artifact, not a probe of a configured
database or provider. These tests keep its factual inventories aligned with
the tracked schema and connector directories without requiring credentials,
a network connection, or a running service.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import snapshots


ROOT = Path(__file__).resolve().parents[1]
DATA_MAP = ROOT / "docs" / "policies" / "DATA_MAP.md"


# `schema_migrations` is the old numbered runner's bookkeeping, left as a fossil
# in installations from before Alembic; Alembic creates its version table at
# runtime. They are migration metadata, not a normal application-table union.
EXPECTED_METADATA_TABLES = frozenset({"schema_migrations"})


class TestDataMap(unittest.TestCase):
    def setUp(self):
        self.text = DATA_MAP.read_text(encoding="utf-8")

    def test_review_draft_has_required_content_and_size(self):
        self.assertGreaterEqual(DATA_MAP.stat().st_size, 2048)
        headings = set(re.findall(r"^## (.+)$", self.text, flags=re.MULTILINE))
        self.assertTrue({
            "Data categories", "Storage", "Processors and flows", "Retention",
            "Jurisdictions", "Approval record",
        } <= headings)

    def test_domain_table_inventory_matches_current_schema(self):
        # The table list is generated (tests/snapshots.json, section `owners`:
        # python3 tests/snapshots.py --write) from the schema of a fresh install
        # at Alembic head (`alembic/schema-head.txt`); the judgement that stays
        # by hand is that the data map describes every one of them.
        tables = snapshots.db_tables()
        self.assertEqual(
            sorted(tables), sorted(snapshots.load()["owners"]),
            "the schema's tables changed; run: python3 tests/snapshots.py --write")
        documented = set(re.findall(r"`([a-z_][a-z0-9_]*)`", self.text))
        self.assertEqual(
            sorted(tables - documented), [],
            "tables the data map never mentions — describe them in DATA_MAP.md")

    def test_current_and_legacy_migration_metadata_are_explicit(self):
        documented = set(re.findall(r"`([a-z_][a-z0-9_]*)`", self.text))
        self.assertTrue(EXPECTED_METADATA_TABLES <= documented)
        self.assertIn("`alembic_version`", self.text)

    def test_connector_directory_inventory_matches_current_tree(self):
        actual = frozenset(
            path.name for path in (ROOT / "app" / "connectors").iterdir()
            if path.is_dir() and (path / "connector.json").is_file()
        )
        self.assertEqual(
            sorted(actual), snapshots.load()["connectors"],
            "the connector directories changed; run: python3 tests/snapshots.py --write")
        documented = set(re.findall(r"`([a-z_][a-z0-9_]*)`", self.text))
        self.assertEqual(
            sorted(actual - documented), [],
            "connectors the data map never mentions — describe them in DATA_MAP.md")

    def test_key_storage_flow_browser_and_retention_contracts_are_documented(self):
        for phrase in (
            "`data/`", "`ordenes/`", "`cuenta-fotos/`", "`logs/`",
            "hard-coded `<repo>/data`", "200 MiB", "legacy HTTP",
            "PostgreSQL", "Mailgun", "Cloudflare Turnstile",
            "Backblaze B2", "fal.ai", "local or session storage", "180 days",
            "48-hour", "24-hour", "512 entries", "10 MB", "five retained files",
            "Its only\n  automatic remote deletion", "Sentry-compatible",
            "front-and-back national-identity OCR\n  photographs",
            "WAHA is loopback development-only", "Meta", "synthetic sample histories",
            "Google Fonts", "through IndexedDB, service workers, or\nCacheStorage",
            "`st_ses`", "`st_admin`", "`st_oidc`", "`st_setup`", "`lang`",
            "`st_nav`", "`st_order_pending`", "30 days for idle `sessions`",
            "90 days for\ncaptured `chat_messages`", "full request line",
            "persists across logout", "including a\ndiagnosis",
            "rendered chat HTML and a WhatsApp URL", "client lifetime of 10 years",
            "sliding 30-day lifetime", "10 minutes", "one year", "one hour",
            "password-hash, and TOTP state", "Pinamar theme self-hosts its fonts",
            "Human Rounds/generic theme loads Google\nFonts",
            "Administrative surfaces load Material Symbols",
            "Only the legacy\n`admin.html` shell loads the Cloudflare Turnstile script unconditionally",
            "`templates/admin/base.html` shell does not",
            "Patient surfaces can\nload Turnstile", "`img.logo.dev`",
            "Intake analysis, health-profile analysis, and consultation\n  composition call AI directly",
            "`hosted_jurisdiction`", "`self_hosted_vllm`", "`*.oaiusercontent.com`",
            "one-hour object lifecycle, but provider deletion is\n  not guaranteed",
            "inbound HTTP fetch of\n  a configured owner iCalendar capability URL",
            "rejection\n  notices, closer-slot offers, account recovery, administrator invitations and\n  resets, and disk alerts",
            "configured inbound webhook accepts message data",
            "processes image, document, audio, and video media",
            "operator-only restore path authorises and\n  downloads one exact raw object into a fresh local destination",
            "Mailgun uses a\nUS default endpoint", "national Argentine systems",
            "Buenos Aires Province source", "no pinned\njurisdiction",
            "OpenAI/ChatGPT `*.oaiusercontent.com` file hosting",
            "Logo.dev\n`img.logo.dev`",
            "`/app/data/turnos.env`", "configuration and credentials",
            "`data/calendar.json`", "dormant compatibility store",
            "`data/test-identities.json`", "legacy/test-only identity store",
            "Pending requests and future approved appointments are never eligible",
            "database row and its history remain after file deletion",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_root_map_invents_no_institutional_approval(self):
        # The map lives at the root for the public cut, but nobody has
        # approved it: the record says so and carries no id or date.
        self.assertFalse(
            (ROOT / "docs" / "publication" / "drafts" / "DATA_MAP.md").exists())
        approval = self.text.split("## Approval record", 1)[1].split("\n## ", 1)[0]
        for field in ("record", "date", "approval"):
            with self.subTest(field=field):
                self.assertNotRegex(approval, r"(?m)^\s*`?%s:" % field)
        self.assertNotRegex(approval, r"\bhr-[0-9a-f]{16}\b")
        self.assertIn(
            "Institutional approval for this inventory has not been recorded yet",
            " ".join(approval.split()))


if __name__ == "__main__":
    unittest.main()
