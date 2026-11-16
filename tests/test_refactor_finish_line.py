"""The approved refactor completion definition stays bounded and executable."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "roadmap" / "refactor-finish-line.md"


class TestRefactorFinishLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.normalized = " ".join(cls.text.split())

    def test_exact_completion_evidence_is_required(self):
        for phrase in (
                "Every retained post-`664cf0b6` candidate",
                "`local-ci` and `local-ci-asgi`",
                "exact Git SHA is deployed",
                "Production and demo pass their repository-owned smoke suites"):
            self.assertIn(phrase, self.normalized)
        self.assertIn(
            "tested, deployed and smoked bytes must be the same exact candidate",
            self.normalized,
        )

    def test_implementation_provenance_is_the_binding_retirement_gate(self):
        for phrase in (
                # the approved document keeps the paths of its day (docs/roadmap/ is a record)
                "`test/test_transport_shapes.py` and `test/test_route_probe.py` pass",
                "`handle(handler, method)` transport must not exist under any name",
                "never count as retirement"):
            self.assertIn(phrase, self.normalized)
        self.assertIn(
            "do not claim to detect arbitrary dead source",
            self.normalized,
        )
        for name in ("test_transport_shapes.py", "test_route_probe.py"):
            self.assertTrue((ROOT / "tests" / name).is_file(), name)
        self.assertIn(
            "A FastAPI-owned URL is not sufficient evidence",
            self.normalized,
        )

    def test_publication_and_optional_daemons_are_separate(self):
        self.assertIn("## Separate follow-up tracks", self.text)
        self.assertIn("Optional daemon migrations", self.text)
        self.assertIn("Public-repository publication", self.text)
        self.assertIn("does not keep this refactor open", self.normalized)

    def test_completion_claim_names_its_evidence(self):
        """The doc may only claim the finish line was REACHED while it still
        names the exact evidence criteria 1-5 demand: one release-candidate
        SHA, both managed topologies, and a deployed + smoked installation.
        (Before 2026-08-27 this asserted the "approved but not yet reached"
        wording; the candidate has since been deployed and smoked, so the
        guard now pins the evidence rather than the not-yet state.)"""
        self.assertIn("Reached on 2026-08-27", self.text)
        self.assertIn("release candidate `d6af4ad1`", self.text)
        self.assertIn(
            "both managed topologies green, `--require-complete` passing, "
            "deployed and smoked", self.normalized)

    def test_post_finish_line_closure_is_recorded(self):
        """The `handle()` follow-up track was executed on 2026-08-30/31 and
        the hash shell retired on 2026-09-01; the document must describe
        that reached state (never "still test-reachable"), and hand
        programme-level closure to the audit acceptance checklist so the
        refactor cannot be reopened by re-deriving "not complete"."""
        self.assertIn("Post-finish-line closure", self.text)
        self.assertIn("every transport is deleted", self.normalized)
        self.assertIn("audit-acceptance-checklist.md", self.text)
        self.assertNotIn("test-reachable, not unreachable", self.normalized)
        self.assertNotIn(
            "there is no call site, no test caller", self.normalized)


if __name__ == "__main__":
    unittest.main()
