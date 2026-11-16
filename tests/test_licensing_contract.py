"""Public licensing, ownership, governance, and support contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class TestLicensingContract(unittest.TestCase):
    def test_generated_media_is_owner_and_licensor(self):
        licensing = _read("docs/policies/LICENSING.md")
        readme = _read("README.md")
        provenance = _read("docs/legal/provenance-sacoturno.md")

        for text in (licensing, readme, provenance):
            self.assertIn("Generated Media, Inc.", text)
        self.assertIn("owns and stewards the Human Rounds project", licensing)
        self.assertNotRegex(provenance, r"(?i)Ivan .{0,30}(sole author|rightsholder)")

    def test_core_enterprise_and_cloud_boundaries_are_explicit(self):
        licensing = _read("docs/policies/LICENSING.md")
        normalized = " ".join(licensing.split())
        for heading in (
                "## AGPL Core", "## Enterprise services",
                "## Human Rounds Cloud", "## Commercial principle"):
            self.assertIn(heading, licensing)
        self.assertIn("Everything currently published in this repository", licensing)
        self.assertIn("Hosting is not an exception to AGPL-3.0", normalized)
        self.assertIn("Open the medicine. Charge for the institution.", licensing)

    def test_approved_governance_and_support_artifacts_are_structural(self):
        expected = {
            "CODE_OF_CONDUCT.md": (
                "Our standard", "Enforcement", "Contact"),
            ".github/GOVERNANCE.md": (
                "Decision making", "Roles", "Approvals and review",
                "Changes to this document"),
            "docs/policies/MAINTAINERS.md": (
                "Current maintainers", "Reachability", "Scope", "Emeritus"),
            ".github/SUPPORT.md": (
                "Support channels", "Response expectations", "LTS",
                "Commercial support"),
        }
        for path, headings in expected.items():
            text = _read(path)
            for heading in headings:
                self.assertIn(f"## {heading}", text)
            self.assertRegex(text, r"(?m)^record: approval:hr-[0-9a-f]{16}$")
            self.assertRegex(text, r"(?m)^date: 2026-08-23$")

    def test_named_maintainers_and_role_separation(self):
        governance = _read(".github/GOVERNANCE.md")
        maintainers = _read("docs/policies/MAINTAINERS.md")
        for text in (governance, maintainers):
            self.assertIn("Ivan Boyko", text)
            self.assertIn("Pavel Malai", text)
            self.assertIn("Generated Media, Inc.", text)
        self.assertIn("lead maintainer", maintainers.lower())
        self.assertIn("maintainer status does not transfer ownership", maintainers)

    def test_contributor_covenant_is_adopted_with_private_reporting(self):
        conduct = _read("CODE_OF_CONDUCT.md")
        self.assertIn("Contributor Covenant 2.1", conduct)
        self.assertIn("private reporting channel", conduct)
        self.assertNotIn("[INSERT CONTACT METHOD]", conduct)
        self.assertNotRegex(conduct, r"(?i)mailto:|[A-Z0-9._%+-]+@[A-Z0-9.-]+")


if __name__ == "__main__":
    unittest.main()
