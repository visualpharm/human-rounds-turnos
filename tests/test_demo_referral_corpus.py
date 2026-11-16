"""Contracts for the public synthetic referral-image corpus."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "packs" / "example-town" / "ordenes-demo"
MANIFEST = CORPUS / "assets.json"
GOLDEN = CORPUS / "golden.json"
SEEDER = ROOT / "scripts" / "seed_demo_all.py"


def _literal(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_literal(item) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_literal(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {_literal(key): _literal(value)
                for key, value in zip(node.keys, node.values)}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "dict" and not node.args:
            return {kw.arg: _literal(kw.value) for kw in node.keywords}
    raise AssertionError(f"unsupported corpus literal: {ast.dump(node)}")


def _assignment(name):
    tree = ast.parse(SEEDER.read_text(encoding="utf-8"), filename=str(SEEDER))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name
                   for target in node.targets):
                return _literal(node.value)
    raise AssertionError(f"missing assignment: {name}")


class TestSyntheticReferralCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.profiles = _assignment("_ORDENES")

    def test_manifest_is_the_exact_nonempty_jpeg_set(self):
        actual = sorted(path.name for path in CORPUS.glob("*.jpg") if path.is_file())
        declared = [item["path"] for item in self.manifest["assets"]]
        self.assertEqual(len(actual), 15)
        self.assertEqual(declared, sorted(declared))
        self.assertEqual(declared, actual)

    def test_manifest_bytes_digests_media_and_attestation_are_exact(self):
        for item in self.manifest["assets"]:
            payload = (CORPUS / item["path"]).read_bytes()
            self.assertTrue(payload.startswith(b"\xff\xd8\xff"), item["path"])
            self.assertEqual(item["bytes"], len(payload), item["path"])
            self.assertEqual(item["sha256"], hashlib.sha256(payload).hexdigest(),
                             item["path"])
            self.assertEqual(item["media_type"], "image/jpeg")
            self.assertEqual(
                item["provenance"],
                {
                    "classification": "synthetic",
                    "source": "from_scratch",
                    "from_scratch": True,
                    "no_real": True,
                    "no_derived": True,
                },
            )
            self.assertTrue(item["review"]["attested"])
            self.assertRegex(item["review"]["reviewer_id"], r"^hr-[0-9a-f]{16}$")
            self.assertRegex(item["review"]["date"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

    def test_golden_cases_close_over_manifest_and_seed_profiles(self):
        cases = self.golden["cases"]
        self.assertEqual(self.golden["schema_version"], "1.0")
        self.assertEqual([case["path"] for case in cases],
                         [item["path"] for item in self.manifest["assets"]])
        profiles = {profile["fname"]: profile for profile in self.profiles}
        self.assertEqual(set(profiles), {case["path"] for case in cases})
        for case in cases:
            self.assertEqual(profiles[case["path"]]["verdict"],
                             case["expected_verdict"], case["path"])
            self.assertIn(case["document_kind"],
                          {"medical_referral", "non_medical"})
            self.assertIn(case["jurisdiction"],
                          {"local", "foreign", "not_applicable"})
            self.assertEqual(case["expected_fields"],
                             list(dict.fromkeys(case["expected_fields"])))

    def test_demo_day_mapping_uses_only_approvable_profiles(self):
        profiles = {profile["fname"]: profile for profile in self.profiles}
        mapping = _assignment("_ORDEN_HOY_POR_SERVICIO")
        self.assertTrue(mapping)
        for filename in mapping.values():
            self.assertIn(filename, profiles)
            self.assertIn("aprobada", profiles[filename]["estados"])

    def test_readme_lists_every_case_and_makes_the_safety_boundary_explicit(self):
        readme = (CORPUS / "README.md").read_text(encoding="utf-8")
        documented = sorted(set(re.findall(r"`([^\`]+\.jpg)`", readme)))
        expected = [item["path"] for item in self.manifest["assets"]]
        self.assertEqual(documented, expected)
        self.assertIn("Every JPEG is synthetic, created from scratch", readme)
        self.assertIn("No file contains, reproduces, or derives from a real", readme)
        self.assertNotIn("real, anonymized", readme)


if __name__ == "__main__":
    unittest.main()
