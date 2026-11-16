"""Tests for the Tier 7 public-cut preflight checker.

Covers every stated boundary:
  - a clean synthetic one-commit public fixture passes;
  - forbidden private/control-plane paths are rejected by NAME first, without
    reading any content beneath them;
  - the public .env.example template is allowed while .env / .env.<x> are not;
  - required artifacts must be present, regular files (not symlinks), and
    structurally complete against the per-artifact policy contract;
  - secret markers are detected value-free (the value never appears in findings);
  - non-fresh / self-altered git history is rejected, including the
    linked-worktree / common-dir edge case;
  - institutional/GitHub/Meta/maintainer evidence is reported as external, not local;
  - the current public candidate has no forbidden private-pack path and reaches
    the institutional-artifact phase;
  - symlinks of all kinds (root, file, directory, broken) are rejected without
    following them;
  - ambient GIT_* environment overrides are detected and rejected;
  - malformed/unreadable policy files are handled cleanly;
  - dirty/untracked git state is rejected;
  - subdirectory roots are rejected (toplevel must match candidate root);
  - extensionless public config files are scanned for credential shapes;
  - scandir/open instrumentation proves private pack is never descended.

Forbidden-path assertions are intentionally id-agnostic: they assert on the
matched PATH and check_type, not the policy rule id, so they hold regardless of
how the policy names its rules.
"""

import io
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from public_cut_preflight import PolicyError, PublicCutPreflight  # noqa: E402
import public_cut_preflight as preflight_module  # noqa: E402

# The set of artifacts the policy currently requires. Tests that build a
# "clean" fixture must satisfy all of them, so derive the list from the policy
# rather than hand-maintaining a second copy.
import json  # noqa: E402
import _archive  # noqa: E402
import _env  # noqa: E402

with open(SCRIPTS_DIR / "public_cut_policy.json", encoding="utf-8") as _f:
    _POLICY = json.load(_f)
REQUIRED_ARTIFACT_SPECS = _POLICY["required_public_artifacts"]
REQUIRED_ARTIFACTS = [a["path"] for a in REQUIRED_ARTIFACT_SPECS]
ARTIFACT_BY_PATH = {a["path"]: a for a in REQUIRED_ARTIFACT_SPECS}


def export_ignored_paths(rel_paths) -> frozenset:
    """Paths of `rel_paths` the working tree's .gitattributes marks export-ignore."""
    proc = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "export-ignore"], cwd=REPO_ROOT,
        input=b"".join(p.encode("utf-8") + b"\0" for p in rel_paths),
        capture_output=True, check=True)
    fields = proc.stdout.decode("utf-8").split("\0")
    return frozenset(fields[i] for i in range(0, len(fields) - 2, 3)
                     if fields[i + 2] == "set")


def build_public_candidate_snapshot(dest: Path) -> None:
    """Populate `dest` with tracked and nonignored new files (`git ls-files`),
    read from the current working tree, and give it its own fresh
    single-commit history.

    This is what a real public-cut candidate actually looks like: gitignored
    local dev files (.env, CLAUDE.local.md, .claude/inbox, ...) were never
    going to ship, so a test that asserts things about "the current public
    candidate" must not scan the live working checkout (which differs, in
    exactly those gitignored files, on every developer machine) -- it must
    scan a snapshot of what `git` would actually publish. Reading from the
    working tree (not `git show HEAD:...`) means an uncommitted-but-tracked
    edit is reflected too, same as a real `git add && git commit` would see.
    Include nonignored new source/tests: omitting these can hide a credential
    marker until AFTER the supposedly verified slice has been committed.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO_ROOT,
        capture_output=True, check=True)
    rel_paths = sorted({p for p in listing.stdout.decode("utf-8").split("\0") if p})
    # `git archive` (scripts/public_cut_build.py) leaves out every path the
    # tree's .gitattributes marks export-ignore; the snapshot does the same.
    ignored = export_ignored_paths(rel_paths)
    for rel in rel_paths:
        if rel in ignored:
            continue
        src = REPO_ROOT / rel
        if src.is_symlink() or not src.is_file():
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for args in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "T"],
            ["git", "config", "commit.gpgsign", "false"],
            ["git", "branch", "-m", "main"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "public-cut candidate snapshot"]):
        subprocess.run(args, cwd=dest, capture_output=True, text=True, check=True)


# Existing-artifact floors calibrated at the same HEAD: largest power of two
# strictly below half the tracked size. T10 pins these numbers.
EXISTING_ARTIFACT_FLOORS = {
    "README.md": 4096,
    "LICENSE": 16384,
    "CONTRIBUTING.md": 4096,
    "SECURITY.md": 1024,
    "CHANGELOG.md": 1024,
    "docs/INSTALL.md": 2048,
    "docs/UPGRADING.md": 4096,
    ".github/workflows/release.yml": 8192,
    "Dockerfile": 2048,
    "docker-compose.yml": 1024,
    "docker-compose.release.yml": 1024,
    "scripts/public_cut_preflight.py": 16384,
    "scripts/public_cut_policy.json": 2048,
    "tests/test_public_cut_preflight.py": 32768,
    "docs/publication/preflight.md": 2048,
}

# Institutional documents authored in-tree (merged 2026-09-15) whose owner
# approval is still outstanding. The public cut must keep failing on them
# (test_current_repo_has_no_forbidden_private_pack pins that); T10 pins that
# the approval record is the only thing they still lack. Remove a path here in
# the same change that records its real approval id.
G1_AWAITING_OWNER_APPROVAL = frozenset()

_BENIGN_PAD = "\nPadding.\n"
_FIXTURE_APPROVAL_ID = "hr-0f1e2d3c4b5a6978"
_FIXTURE_APPROVAL_DATE = "2026-08-19"


def synthetic_jpeg(payload: bytes) -> bytes:
    """Minimal synthetic JPEG-signature fixture; it is not a real image."""
    return b"\xff\xd8\xff" + payload


def real_shape_document(art, omit_section=None, record_line=None, date_line=None,
                        extra_prose="", include_approval=None):
    """Minimal real-shape document derived from one policy artifact entry."""
    path = art["path"]
    parts = [f"# {path}\n", "Synthetic publication fixture.\n"]
    if extra_prose:
        parts.append(extra_prose if extra_prose.endswith("\n") else extra_prose + "\n")
    for section in art.get("required_sections") or []:
        if omit_section is not None and section == omit_section:
            continue
        parts.append(f"\n## {section}\nCovered.\n")
    approval = art.get("approval_record") or {}
    want_approval = (include_approval if include_approval is not None
                     else bool(approval.get("required")))
    if want_approval:
        parts.append("\n## Approval record\n")
        rec = (f"record: approval:{_FIXTURE_APPROVAL_ID}\n"
               if record_line is None else record_line)
        if rec and not rec.endswith("\n"):
            rec += "\n"
        parts.append(rec)
        dat = (f"date: {_FIXTURE_APPROVAL_DATE}\n"
               if date_line is None else date_line)
        if dat and not dat.endswith("\n"):
            dat += "\n"
        parts.append(dat)
    body = "".join(parts)
    min_bytes = int(art.get("min_bytes") or 1)
    while len(body.encode("utf-8")) < min_bytes:
        body += _BENIGN_PAD
    return body


def historical_placeholder(path):
    return f"# {path} placeholder\n"


def padded_historical_placeholder(art):
    text = historical_placeholder(art["path"])
    min_bytes = int(art.get("min_bytes") or 1)
    while len(text.encode("utf-8")) < min_bytes:
        text += _BENIGN_PAD
    return text


class _Fixture(unittest.TestCase):
    """Base mixin: temp dir + helpers for building candidate trees."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir)
        self.policy_path = SCRIPTS_DIR / "public_cut_policy.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -- file / dir helpers -------------------------------------------------

    def create_file(self, rel_path: str, content: str = "content\n") -> Path:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def create_clean_artifacts(self):
        for art in REQUIRED_ARTIFACT_SPECS:
            self.create_file(art["path"], real_shape_document(art))
        self.create_clean_binary_asset_sets()

    def create_clean_binary_asset_sets(self):
        """Build policy-derived synthetic binary fixtures without static digests."""
        for asset_set in _POLICY.get("public_binary_asset_sets", []):
            root = self.root / asset_set["root"]
            root.mkdir(parents=True, exist_ok=True)
            suffix = asset_set["extensions"][0]
            asset_path = root / ("synthetic" + suffix)
            content = synthetic_jpeg(b"binary fixture\x00\x01")
            asset_path.write_bytes(content)
            manifest = {
                "manifest_version": "1.0",
                "assets": [{
                    "path": asset_path.name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                    "media_type": "image/jpeg",
                    "provenance": {
                        "classification": "synthetic",
                        "source": "from_scratch",
                        "from_scratch": True,
                        "no_real": True,
                        "no_derived": True,
                    },
                    "review": {
                        "attested": True,
                        "reviewer_id": _FIXTURE_APPROVAL_ID,
                        "date": _FIXTURE_APPROVAL_DATE,
                    },
                }],
            }
            (self.root / asset_set["manifest"]).write_text(
                json.dumps(manifest, sort_keys=True), encoding="utf-8")

    def phase4(self):
        checker = PublicCutPreflight(self.root, self.policy_path)
        checker.check_required_artifacts()
        return checker

    def finding_blob(self, checker):
        return " ".join(
            f"{f.check_type}|{f.id}|{f.path}|{f.details}|{f.description}"
            for f in checker.findings)

    # -- git helpers --------------------------------------------------------

    def git(self, *args, cwd=None):
        proc = subprocess.run(
            ["git", *args], cwd=cwd or self.root,
            capture_output=True, text=True, check=True)
        return (True, proc.stdout.strip())

    def init_git(self, cwd=None):
        target = cwd or self.root
        subprocess.run(["git", "init", "-q"], cwd=target,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=target, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=target, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"],
                       cwd=target, capture_output=True, text=True, check=True)
        # Rename to main explicitly (some git versions default to master)
        subprocess.run(["git", "branch", "-m", "main"], cwd=target,
                       capture_output=True, text=True, check=True)

    def commit_all(self, cwd=None, message="init"):
        target = cwd or self.root
        subprocess.run(["git", "add", "-A"], cwd=target,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=target,
                       capture_output=True, text=True, check=True)

    # -- run helpers --------------------------------------------------------

    def run_preflight(self, root=None):
        checker = PublicCutPreflight(root or self.root, self.policy_path)
        result = checker.run()
        return result, checker

    def findings(self, checker, check_type=None):
        return [f for f in checker.findings
                if check_type is None or f.check_type == check_type]

    def failures(self, checker, check_type=None):
        return [f for f in checker.findings
                if f.passed is False
                and (check_type is None or f.check_type == check_type)]

    def forbidden_paths(self, checker):
        return {f.path for f in self.failures(checker, "forbidden_path")}

    def _failmsg(self, checker):
        return "\n".join(
            f"[{'PASS' if f.passed else 'FAIL' if f.passed is False else 'EXT'}] {f.check_type} {f.id} "
            f"{f.path} {f.details}"
            for f in checker.findings)


class TestCleanFixturePasses(_Fixture):
    """A clean synthetic one-commit public fixture must pass."""

    def test_clean_fixture_passes(self):
        self.create_clean_artifacts()
        self.create_file("app/server.py", "print('hi')\n")
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        self.assertTrue(result, msg=self._failmsg(checker))
        self.assertEqual(self.failures(checker), [])
        # external evidence is reported as informational (passed=None), not pass
        ext = self.findings(checker, "external_evidence")
        self.assertGreater(len(ext), 0)
        for f in ext:
            self.assertIsNone(f.passed)

    def test_external_evidence_ids_present(self):
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        _, checker = self.run_preflight()
        ids = {f.id for f in self.findings(checker, "external_evidence")}
        for expected in ("github-publication-readiness", "project-governance-approval",
                         "credential-and-platform-provisioning"):
            self.assertIn(expected, ids)
        # external findings must say they are not locally verifiable (inside the
        # public cut the private digests are absent and say so in their own words)
        for f in self.findings(checker, "external_evidence"):
            if f.id.startswith("private-digests:"):
                self.assertIn("PRIVATE WORKING REPOSITORY", f.details.upper())
            else:
                self.assertIn("NOT EVALUATED", f.details.upper())


class TestForbiddenPaths(_Fixture):
    """Forbidden paths are rejected by name first; content beneath is not read."""

    def test_real_private_pack_fails(self):
        self.create_clean_artifacts()
        self.create_file("packs/ar-b-pinamar/doctor.json",
                         "PRIVA" + "TE-VALUE")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        # the pack DIRECTORY is flagged by name; children pruned, never read
        self.assertIn("packs/ar-b-pinamar", self.forbidden_paths(checker))
        self.assertEqual(self.findings(checker, "secret_marker"), [])
        # the private value placed inside is never surfaced
        for f in checker.findings:
            self.assertNotIn("PRIVATE-VALUE",
                             " ".join(str(v) for v in
                                      (f.id, f.description, f.details, f.path)))

    def test_private_pack_not_descended_into(self):
        """A credential marker placed INSIDE the forbidden pack is never found,
        because the pack directory is pruned before its children are read."""
        self.create_clean_artifacts()
        # a marker that would absolutely be detected if the file were scanned
        self.create_file("packs/ar-b-pinamar/leak.txt",
                         "AKI" + "A" + "IOSFODNN7EXAMPLE")  # aws-ish, but never read
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self.assertEqual(self.findings(checker, "secret_marker"), [])
        self.assertGreater(len(self.failures(checker, "forbidden_path")), 0)

    def test_claude_local_md_forbidden(self):
        self.create_clean_artifacts()
        self.create_file("CLAUDE.local.md", "private notes")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self.assertIn("CLAUDE.local.md", self.forbidden_paths(checker))

    def test_claude_local_settings_forbidden(self):
        self.create_clean_artifacts()
        self.create_file(".claude/settings.local.json", "{}")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        # The whole harness directory is refused (its allow list is empty).
        self.assertIn(".claude", self.forbidden_paths(checker))

    def test_claude_private_state_dirs_forbidden(self):
        self.create_clean_artifacts()
        self.create_file(".claude/inbox/card.md", "x")
        self.create_file(".claude/parking/note.md", "x")
        self.create_file(".claude/memory/fact.md", "x")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        paths = self.forbidden_paths(checker)
        # the matched directory itself is flagged (children pruned)
        self.assertIn(".claude", paths)
        self.assertFalse(any(p.startswith(".claude/") for p in paths))
        self.assertEqual(self.findings(checker, "secret_marker"), [])

    def test_env_exact_forbidden_but_example_allowed(self):
        # .env.example alone must NOT trip the env rule
        self.create_clean_artifacts()
        self.create_file(".env.example", "FOO=bar\n")
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        self.assertTrue(result, msg="public .env.example template must be allowed")
        self.assertEqual(self.failures(checker, "forbidden_path"), [])

    def test_env_local_forbidden(self):
        self.create_clean_artifacts()
        self.create_file(".env.local", "FOO=bar\n")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self.assertIn(".env.local", self.forbidden_paths(checker))

    def test_env_exact_forbidden(self):
        self.create_clean_artifacts()
        self.create_file(".env", "FOO=bar\n")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self.assertIn(".env", self.forbidden_paths(checker))

    def test_ops_repo_prefix_forbidden(self):
        self.create_clean_artifacts()
        self.create_file("humanrounds-pinamar/README.md", "ops")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self.assertIn("humanrounds-pinamar", self.forbidden_paths(checker))

    def test_private_pack_family_regex(self):
        self.create_clean_artifacts()
        self.create_file("packs/anytown-private/data.json", "x")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self.assertIn("packs/anytown-private", self.forbidden_paths(checker))

    def test_fail_first_on_forbidden_paths(self):
        """With a forbidden path AND missing artifacts AND non-fresh git, the
        first FAILURE is a forbidden path, and content/git phases are skipped."""
        self.create_clean_artifacts()
        # non-fresh history
        self.init_git()
        self.commit_all()
        self.create_file("extra.txt", "extra content")
        self.commit_all(message="second")
        # a forbidden path
        self.create_file("packs/ar-b-pinamar/x.json", "x")
        # and a missing artifact
        (self.root / "CODE_OF_CONDUCT.md").unlink()
        result, checker = self.run_preflight()
        self.assertFalse(result)
        failing = [f for f in checker.findings if f.passed is False]
        self.assertGreater(len(failing), 0)
        # the very first failure is a forbidden path
        self.assertEqual(failing[0].check_type, "forbidden_path")
        # content scan and git history never ran (bailed on forbidden)
        self.assertEqual(self.findings(checker, "secret_marker"), [])
        self.assertEqual(self.findings(checker, "git_history"), [])
        self.assertEqual(self.findings(checker, "required_artifact"), [])


class TestSymlinkRejection(_Fixture):
    """Symlinks of all kinds are rejected without following them."""

    def test_file_symlink_rejected(self):
        self.create_clean_artifacts()
        # create a real file and a symlink to it
        real = self.root / "real.txt"
        real.write_text("content\n")
        link = self.root / "link.txt"
        link.symlink_to(real)
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        self.assertFalse(result)
        symlink_fails = [f for f in self.failures(checker, "forbidden_path")
                         if f.id == "symlink"]
        self.assertGreater(len(symlink_fails), 0)
        self.assertIn("link.txt", {f.path for f in symlink_fails})

    def test_directory_symlink_rejected(self):
        self.create_clean_artifacts()
        # create a real directory and a symlink to it
        real_dir = self.root / "real_dir"
        real_dir.mkdir()
        real_dir.joinpath("file.txt").write_text("content\n")
        link_dir = self.root / "link_dir"
        link_dir.symlink_to(real_dir)
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        self.assertFalse(result)
        symlink_fails = [f for f in self.failures(checker, "forbidden_path")
                         if f.id == "symlink"]
        self.assertGreater(len(symlink_fails), 0)
        self.assertIn("link_dir", {f.path for f in symlink_fails})

    def test_broken_symlink_rejected(self):
        self.create_clean_artifacts()
        # create a symlink to a non-existent target
        broken = self.root / "broken.txt"
        broken.symlink_to("/nonexistent/target")
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        self.assertFalse(result)
        symlink_fails = [f for f in self.failures(checker, "forbidden_path")
                         if f.id == "symlink"]
        self.assertGreater(len(symlink_fails), 0)

    def test_root_symlink_rejected(self):
        # create a temp dir with the real content, then symlink the test root to it
        real_dir = Path(self.temp_dir) / "real_content"
        real_dir.mkdir()
        for art in REQUIRED_ARTIFACTS:
            art_path = real_dir / art
            art_path.parent.mkdir(parents=True, exist_ok=True)
            art_path.write_text(f"# {art}\n")
        # initialize git in the real directory
        subprocess.run(["git", "init", "-q"], cwd=real_dir,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=real_dir, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=real_dir, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"],
                       cwd=real_dir, capture_output=True, text=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=real_dir,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=real_dir,
                       capture_output=True, text=True, check=True)
        # make the test root itself a symlink to the real content
        self.root = Path(self.temp_dir) / "symlink_root"
        self.root.symlink_to(real_dir)
        # run preflight on the symlinked root
        result, checker = self.run_preflight(root=self.root)
        self.assertFalse(result)
        symlink_fails = [f for f in self.failures(checker, "forbidden_path")
                         if f.id == "symlink-root"]
        self.assertGreater(len(symlink_fails), 0)


class TestRequiredArtifacts(_Fixture):
    """Required artifacts must exist, be regular files (not symlinks), and non-empty."""

    def test_missing_artifact_fails(self):
        self.create_clean_artifacts()
        (self.root / "CODE_OF_CONDUCT.md").unlink()
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        self.assertFalse(result)
        arts = self.failures(checker, "required_artifact")
        self.assertTrue(any(f.path == "CODE_OF_CONDUCT.md" for f in arts))

    def test_empty_artifact_fails(self):
        self.create_clean_artifacts()
        (self.root / "CODE_OF_CONDUCT.md").write_text("")
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        self.assertFalse(result)
        arts = self.failures(checker, "required_artifact")
        code = [f for f in arts if f.path == "CODE_OF_CONDUCT.md"]
        self.assertEqual(len(code), 1)

    def test_artifact_symlink_fails(self):
        self.create_clean_artifacts()
        # replace a real artifact with a symlink to dummy content
        real = self.root / "real_content.txt"
        real.write_text("dummy\n")
        artifact = self.root / "CODE_OF_CONDUCT.md"
        artifact.unlink()
        artifact.symlink_to(real)
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        self.assertFalse(result)
        # Symlinks are rejected in the forbidden_paths phase (before required_artifacts)
        symlink_fails = [f for f in self.failures(checker, "forbidden_path")
                         if f.id == "symlink" and "CODE_OF_CONDUCT.md" in f.path]
        self.assertGreater(len(symlink_fails), 0)


class TestSecretMarkers(_Fixture):
    """Credential-shaped markers are detected value-free."""

    def _fixture_with_marker(self, rel_path, content):
        self.create_clean_artifacts()
        self.create_file(rel_path, content)
        self.init_git()
        self.commit_all()
        return self.run_preflight()

    def _assert_marker_found(self, checker, cls_id):
        hits = [f for f in self.findings(checker, "secret_marker")
                if f.id == cls_id]
        self.assertGreater(len(hits), 0, f"no {cls_id} marker found")

    def test_anthropic_key(self):
        _, c = self._fixture_with_marker(
            "cfg.py", 'k = "sk-' + 'ant-' + "A" * 24 + '"\n')
        self._assert_marker_found(c, "anthropic-key")

    def test_openrouter_key(self):
        _, c = self._fixture_with_marker(
            "cfg.py", 'k = "sk-' + 'or-' + "B" * 24 + '"\n')
        self._assert_marker_found(c, "openrouter-key")

    def test_google_api_key(self):
        _, c = self._fixture_with_marker(
            "cfg.py", 'k = "AI' + 'za' + "C" * 32 + '"\n')
        self._assert_marker_found(c, "google-api-key")

    def test_aws_access_key(self):
        _, c = self._fixture_with_marker(
            "cfg.py", 'k = "AK' + 'IA' + "D" * 16 + '"\n')
        self._assert_marker_found(c, "aws-access-key")

    def test_github_token(self):
        _, c = self._fixture_with_marker(
            "cfg.py", 'k = "ghp_' + "E" * 36 + '"\n')
        self._assert_marker_found(c, "github-token")

    def test_private_key_block(self):
        _, c = self._fixture_with_marker(
            "cfg.txt", "-----BEGIN RSA PR" + "IVATE KEY-----\n")
        self._assert_marker_found(c, "private-key-block")

    def test_secret_assignment(self):
        _, c = self._fixture_with_marker(
            "cfg.py", 'password = "supersecret' + '-value-1234"\n')
        self._assert_marker_found(c, "secret-assignment")

    def test_secret_assignment_with_braces_is_still_detected(self):
        assignment_name = "pass" + "word"
        content = "".join(
            f'{assignment_name} = "static-value-1234-{suffix}"\n'
            for suffix in ("{suffix", "prefix}", "prefix{middle}suffix")
        )
        _, checker = self._fixture_with_marker("cfg.py", content)
        hits = [f for f in self.findings(checker, "secret_marker")
                if f.id == "secret-assignment"]
        self.assertEqual(len(hits), 3)

    def test_value_is_never_leaked(self):
        """The matched value must never appear anywhere in any finding field."""
        secret = "sk-" + 'ant-' + "Z" * 24
        _, c = self._fixture_with_marker("cfg.py", f'k = "{secret}"\n')
        for f in c.findings:
            blob = " ".join(str(v) for v in
                            (f.check_type, f.id, f.description, f.details, f.path))
            self.assertNotIn(secret, blob)
            self.assertNotIn("Z" * 24, blob)

    def test_no_false_positive_on_bare_prefixes(self):
        """Bare prefixes that used to cause false positives must NOT match."""
        self.create_clean_artifacts()
        # prose mentioning these terms, but no real credential shape
        self.create_file("DOCS.md",
                         "# Project\nWe use OpenRouter and HSI_URL config.\n"
                         "Set AIza as needed. See credential vault docs.\n"
                         "password = '***'\n")  # too short to match {16,}
        self.init_git()
        self.commit_all()
        result, c = self.run_preflight()
        self.assertEqual(self.findings(c, "secret_marker"), [],
                         msg="bare prefixes must not create false positives")
        self.assertTrue(result, msg=self._failmsg(c))

    def test_extensionless_config_scanned(self):
        """Extensionless public config files (Dockerfile, Makefile, etc.) are scanned."""
        self.create_clean_artifacts()
        # Dockerfile with a secret
        self.create_file("Dockerfile",
                         'FROM python\nENV API_KEY="sk-' + 'ant-' + "A" * 24 + '"\n')
        self.init_git()
        self.commit_all()
        _, c = self.run_preflight()
        self._assert_marker_found(c, "anthropic-key")

    def test_env_example_scanned(self):
        """.env.example is scanned for credential shapes."""
        self.create_clean_artifacts()
        self.create_file(".env.example",
                         'DATABASE_URL="postgres://..."\nAPI_KEY="sk-' + 'or-' + "B" * 24 + '"\n')
        self.init_git()
        self.commit_all()
        _, c = self.run_preflight()
        self._assert_marker_found(c, "openrouter-key")


class TestPrivateMarkers(_Fixture):
    """Installation-private markers (private hosts, infrastructure ids, public
    addresses, people's names as salted digests) are found value-free.

    Every sample is assembled from parts so this file never carries a whole
    marker itself; the token classes run on a throwaway salt and digest."""

    _SALT = "test-only-salt:"

    def setUp(self):
        super().setUp()
        self._policy_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self._policy_dir, True)

    def _policy_with(self, private_classes=None, forbidden=None):
        data = json.loads(json.dumps(_POLICY))
        if private_classes is not None:
            data["private_marker_classes"] = private_classes
        if forbidden is not None:
            data["forbidden_paths"] = forbidden
        path = self._policy_dir / ("policy-%d.json" % len(list(self._policy_dir.iterdir())))
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _scan(self, files, policy=None):
        for rel, content in files.items():
            self.create_file(rel, content)
        checker = PublicCutPreflight(self.root, policy or self.policy_path)
        checker.check_secret_markers()
        return checker

    def _hits(self, checker, cls_id):
        return sorted(f.path for f in checker.findings
                      if f.check_type == "private_marker" and f.id == cls_id
                      and f.passed is False)

    def _token_class(self, *values, **extra):
        cls = {"id": "known-private-token", "format": "token-sha256",
               "salt": self._SALT,
               "hashes": [preflight_module.private_token_digest(self._SALT, v)
                          for v in values]}
        cls.update(extra)
        return cls

    def test_tailnet_host_is_found(self):
        host = "box" + ".tail" + "1234" + ".ts" + ".net"
        c = self._scan({"docs/ops.md": "ssh " + host + "\n", "docs/ok.md": "the .net runtime\n"})
        self.assertEqual(self._hits(c, "tailnet-host"), ["docs/ops.md"])

    def test_private_host_matches_as_a_word_pair_digest(self):
        # A private host is listed as the digest of its first two labels, so
        # the policy that ships never names it.
        a, b = "git" + "lab", "exam" + "ple9"
        policy = self._policy_with([self._token_class(a + " " + b)])
        c = self._scan({"README.md": "git clone https://" + a + "." + b + ".com/x/y.git\n",
                        "docs/ok.md": "the public " + a + " docs\n"}, policy)
        self.assertEqual(self._hits(c, "known-private-token"), ["README.md"])

    def _digests_file(self, payload, name="digests.json"):
        path = self._policy_dir / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return name

    def test_digests_load_from_the_private_file_beside_the_policy(self):
        word = "zorz" + "al"
        rel = self._digests_file(
            {"known-private-token": [preflight_module.private_token_digest(self._SALT, word)]})
        cls = {"id": "known-private-token", "format": "token-sha256", "salt": self._SALT,
               "hashes_file": rel}
        c = self._scan({"app/x.py": "# " + word + "\n"}, self._policy_with([cls]))
        self.assertEqual(self._hits(c, "known-private-token"), ["app/x.py"])
        self.assertEqual(c.private_digests_missing, [])

    def test_missing_digests_file_is_external_evidence_not_a_pass_claim(self):
        cls = {"id": "known-private-token", "format": "token-sha256", "salt": self._SALT,
               "hashes_file": "absent.json"}
        checker = PublicCutPreflight(self.root, self._policy_with([cls]))
        self.assertEqual(checker.private_digests_missing, ["known-private-token"])
        checker.check_external_evidence()
        ids = [f.id for f in checker.findings if f.check_type == "external_evidence"]
        self.assertIn("private-digests:known-private-token", ids)
        self.assertTrue(all(f.passed is None for f in checker.findings
                            if f.id == "private-digests:known-private-token"))

    def test_bad_digests_file_is_a_policy_error(self):
        for payload in ({"known-private-token": ["ABC"]}, ["0" * 64],
                        {"known-private-token": "0" * 64}):
            with self.subTest(payload=payload):
                rel = self._digests_file(payload, "bad.json")
                cls = {"id": "known-private-token", "format": "token-sha256",
                       "salt": self._SALT, "hashes_file": rel}
                with self.assertRaises(PolicyError):
                    PublicCutPreflight(self.root, self._policy_with([cls]))

    def test_shipped_policy_keeps_digests_out_of_the_cut(self):
        # The digests of names, hosts and networks stay in the export-ignored
        # file: a salted digest of a short name is reversible by a dictionary.
        for cls in _POLICY["private_marker_classes"]:
            with self.subTest(cls=cls["id"]):
                self.assertEqual(cls.get("hashes", []), [])
        names = {cls.get("hashes_file") for cls in _POLICY["private_marker_classes"]} - {None}
        self.assertEqual(names, {"public_cut_private_digests.json"})
        ignored = export_ignored_paths(["scripts/public_cut_private_digests.json"])
        self.assertEqual(sorted(ignored), ["scripts/public_cut_private_digests.json"])

    def test_shared_address_space_is_found_outside_tests(self):
        addr = ".".join(["100", "101", "7", "9"])
        c = self._scan({"docs/ops.md": "ssh " + addr + "\n",
                        "tests/test_net.py": "IP = '" + addr + "'\n",
                        "docs/ok.md": "IP " + ".".join(["10", "101", "7", "9"]) + "\n"})
        self.assertEqual(self._hits(c, "public-ip-address"), ["docs/ops.md"])

    def test_coolify_resource_id_next_to_the_word_coolify(self):
        rid = "a1b2c3d4e5f6" + "g7h8i9j0k1l2"
        c = self._scan({
            "docs/deploy.md": "Coolify app " + rid + "\n",
            # The same shape without the word is an ordinary hash or id.
            "docs/other.md": "build " + rid + "\n",
            # Coolify prose with a long English word is not an id.
            "docs/prose.md": "Coolify keeps the " + "environmentvariablesfiles" + "\n",
        })
        self.assertEqual(self._hits(c, "coolify-resource-id"), ["docs/deploy.md"])

    def test_global_address_found_outside_tests_only(self):
        addr4 = ".".join(["8", "8", "4", "4"])
        addr6 = ":".join(["2001", "4860", "4860", "", "8844"])
        c = self._scan({
            "app/net.py": "HOST = '" + addr4 + "'\n",
            "app/net6.py": "HOST = '" + addr6 + "'\n",
            "tests/test_net.py": "HOST = '" + addr4 + "'\n",
            # Documentation, private, loopback ranges and version strings pass.
            "app/doc.py": "A = '" + ".".join(["203", "0", "113", "7"]) + "'\n"
                          "B = '" + ".".join(["10", "1", "2", "3"]) + "'\n"
                          "C = '" + ":".join(["2001", "db8", "", "1"]) + "'\n"
                          "UA = 'Chrome/" + ".".join(["131", "0", "0", "0"]) + "'\n",
        })
        self.assertEqual(self._hits(c, "public-ip-address"), ["app/net.py", "app/net6.py"])

    def test_listed_network_is_found_in_tests_too(self):
        net = ".".join(["198", "51", "100", "0"]) + "/24"
        policy = self._policy_with([{
            "id": "public-ip-address", "format": "public-ip", "exempt_prefixes": ["tests"],
            "salt": self._SALT, "network_prefixes_v4": [24],
            "hashes": [preflight_module.private_token_digest(self._SALT, net)]}])
        inside = ".".join(["198", "51", "100", "9"])
        c = self._scan({"tests/test_x.py": "IP = '" + inside + "'\n",
                        "tests/test_y.py": "IP = '" + ".".join(["198", "51", "101", "9"]) + "'\n"},
                       policy)
        self.assertEqual(self._hits(c, "public-ip-address"), ["tests/test_x.py"])

    def test_names_match_as_digests_across_case_and_accents(self):
        first, last = "Ana" + "lia", "Pe" + "rez"
        policy = self._policy_with([self._token_class((first + " " + last).lower())])
        c = self._scan({
            "docs/a.md": "Asked by " + first.upper() + " " + last[:1] + "é" + last[2:] + ".\n",
            "docs/b.md": "Asked by " + first + " alone.\n",
            "docs/c.md": "Asked by " + last + " alone.\n",
        }, policy)
        self.assertEqual(self._hits(c, "known-private-token"), ["docs/a.md"])

    def test_single_token_digest_and_exempt_prefix(self):
        word = "zorz" + "al"
        policy = self._policy_with([self._token_class(word, exempt_prefixes=["tests"])])
        c = self._scan({"app/x.py": "# " + word.capitalize() + "\n",
                        "tests/test_x.py": "# " + word + "\n"}, policy)
        self.assertEqual(self._hits(c, "known-private-token"), ["app/x.py"])

    def test_value_never_reaches_a_finding(self):
        word = "zorz" + "al"
        host = "box" + ".tail" + "9" + ".ts" + ".net"
        policy = self._policy_with([self._token_class(word)] + [
            cls for cls in _POLICY["private_marker_classes"] if cls["id"] == "tailnet-host"])
        c = self._scan({"app/x.py": word + " " + host + "\n"}, policy)
        self.assertEqual(len([f for f in c.findings if f.check_type == "private_marker"]), 2)
        for f in c.findings:
            blob = " ".join(str(v) for v in (f.check_type, f.id, f.description, f.details, f.path))
            self.assertNotIn(word, blob.lower())
            self.assertNotIn(host, blob)

    def test_private_digests_carry_no_readable_value(self):
        _archive.skip_in_archive_copy(self)
        source = REPO_ROOT / "scripts" / "public_cut_private_digests.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        self.assertTrue(data)
        for cls_id, digests in data.items():
            for digest in digests:
                with self.subTest(cls=cls_id):
                    self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_bad_private_classes_are_policy_errors(self):
        bad = [
            [{"id": "x", "format": "nope"}],
            [{"id": "x", "format": "token-sha256", "salt": "s"}],
            [{"id": "x", "format": "token-sha256", "salt": "s", "hashes": ["ABC"]}],
            [{"id": "x", "format": "token-sha256", "hashes": ["0" * 64]}],
            [{"id": "x", "format": "regex", "patterns": []}],
            [{"id": "x", "format": "regex", "patterns": ["("]}],
            [{"id": "x", "format": "public-ip", "network_prefixes_v4": [33]}],
            [{"id": "x", "format": "regex", "patterns": ["a"], "colour": "red"}],
            [{"id": "anthropic-key", "format": "regex", "patterns": ["a"]}],
            [{"id": "x", "format": "token-sha256", "salt": "s", "hashes_file": "../d.json"}],
            [{"id": "x", "format": "token-sha256", "hashes_file": "d.json"}],
            [{"id": "x", "format": "token-sha256", "salt": "s", "hashes_file": ""}],
        ]
        for classes in bad:
            with self.subTest(classes=classes):
                with self.assertRaises(PolicyError):
                    PublicCutPreflight(self.root, self._policy_with(classes))

    def _forbidden_hits(self, policy):
        checker = PublicCutPreflight(self.root, policy)
        checker.check_forbidden_paths()
        return {f.path for f in self.failures(checker, "forbidden_path")}

    def test_harness_dir_is_forbidden_except_its_allow_list(self):
        self.create_file(".claude/hooks/guard.py", "x = 1\n")
        self.create_file(".claude/public/readme.md", "public\n")
        self.create_file("app/x.py", "x = 1\n")
        rule = {"id": "claude-harness-dir", "match": "prefix", "pattern": ".claude"}
        blocked = self._forbidden_hits(self._policy_with(forbidden=[dict(rule, allow=[])]))
        self.assertTrue(blocked and all(p.startswith(".claude") for p in blocked), blocked)
        allowed = self._forbidden_hits(self._policy_with(
            forbidden=[dict(rule, allow=[".claude/public/readme.md"])]))
        self.assertEqual(allowed, {".claude/hooks"})

    def test_allow_list_shape_is_validated(self):
        bad = [
            [{"id": "a", "match": "name", "pattern": ".claude", "allow": [".claude/x"]}],
            [{"id": "a", "match": "prefix", "pattern": ".claude", "allow": ["docs/x.md"]}],
            [{"id": "a", "match": "prefix", "pattern": ".claude", "allow": ".claude/x"}],
            [{"id": "a", "match": "prefix", "pattern": ".claude", "allow": ["../x"]}],
            [{"id": "a", "match": "prefix", "pattern": ".claude", "except": []}],
        ]
        for forbidden in bad:
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(PolicyError):
                    PublicCutPreflight(self.root, self._policy_with(forbidden=forbidden))

    def test_export_ignored_tracked_files_are_forbidden_paths(self):
        # What .gitattributes keeps out of `git archive` is also what the
        # preflight refuses, so a cut built another way fails instead of leaking.
        _archive.skip_in_archive_copy(self)  # a copy carries none of the export-ignored files
        listing = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT,
                                 capture_output=True, check=True)
        tracked = [p for p in listing.stdout.decode("utf-8").split("\0") if p]
        ignored = sorted(export_ignored_paths(tracked))
        self.assertTrue(ignored)
        checker = PublicCutPreflight(self.root, self.policy_path)
        unmatched = [p for p in ignored if checker._rule_matches(p) is None]
        self.assertEqual(unmatched, [])


class TestAmbientGitOverrides(_Fixture):
    """Ambient GIT_* environment variable overrides are detected."""

    def test_git_dir_override_detected(self):
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        # set a GIT_DIR override
        with _env.patched_env({"GIT_DIR": "/some/other/git"}):
            result, checker = self.run_preflight()
            self.assertFalse(result)
            ambient_fails = [f for f in self.failures(checker, "ambient_environment")
                            if f.id == "git-overrides"]
            self.assertGreater(len(ambient_fails), 0)
            self.assertIn("GIT_DIR", ambient_fails[0].details)

    def test_git_work_tree_override_detected(self):
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        with _env.patched_env({"GIT_WORK_TREE": "/some/tree"}):
            result, checker = self.run_preflight()
            self.assertFalse(result)
            ambient_fails = [f for f in self.failures(checker, "ambient_environment")
                            if f.id == "git-overrides"]
            self.assertGreater(len(ambient_fails), 0)

    def test_allowed_git_vars_not_flagged(self):
        """Standard GIT_AUTHOR_* and GIT_COMMITTER_* vars are allowed."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        with _env.patched_env({"GIT_AUTHOR_NAME": "Test",
                                       "GIT_AUTHOR_EMAIL": "test@example.com",
                                       "GIT_COMMITTER_NAME": "Test",
                                       "GIT_COMMITTER_EMAIL": "test@example.com"}):
            result, checker = self.run_preflight()
            # should pass despite these vars
            ambient_fails = self.failures(checker, "ambient_environment")
            self.assertEqual(len(ambient_fails), 0)


class TestGitHistoryFreshness(_Fixture):
    """Non-fresh / self-altered git history is rejected, incl worktree edge case."""

    def _clean(self):
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()

    def _git_fail(self, checker, finding_id):
        hits = [f for f in self.failures(checker, "git_history")
                if f.id == finding_id]
        self.assertGreater(len(hits), 0, f"git finding {finding_id} did not fail")

    def _git_pass(self, checker, finding_id):
        hits = [f for f in checker.findings if f.check_type == "git_history"
                and f.id == finding_id and f.passed is True]
        self.assertGreater(len(hits), 0, f"git finding {finding_id} did not pass")

    def test_toplevel_is_root_pass(self):
        """On a clean repo, toplevel equals candidate root."""
        self._clean()
        _, checker = self.run_preflight()
        self._git_pass(checker, "git-toplevel-is-root")

    def test_toplevel_is_not_root_fails(self):
        """If run from a subdirectory, toplevel != candidate root."""
        self._clean()
        subdir = self.root / "subdir"
        subdir.mkdir()
        # run preflight from subdir (not the git root)
        checker = PublicCutPreflight(subdir, self.policy_path)
        checker.run()
        self._git_fail(checker, "git-toplevel-is-root")

    def test_ordinary_git_dir_pass(self):
        """A normal .git directory is accepted."""
        self._clean()
        _, checker = self.run_preflight()
        self._git_pass(checker, "git-ordinary-git-dir")

    def test_git_dir_is_file_fails(self):
        """If .git is a file (gitworktree file submodule marker), reject."""
        # This test is skipped because once .git is replaced with a file,
        # git commands no longer work properly. The check for ordinary .git
        # directory is tested indirectly through the linked_worktree test which
        # creates a realistic scenario where .git differs from commondir.
        pass

    def test_git_dir_is_symlink_fails(self):
        """If .git is a symlink, reject."""
        self._clean()
        # remove .git and replace with a symlink
        real_git = self.root / "real_git"
        shutil.move(self.root / ".git", real_git)
        (self.root / ".git").symlink_to(real_git)
        result, checker = self.run_preflight()
        self.assertFalse(result)
        # The symlink is now detected in phase 1 (check_forbidden_paths), which is
        # more secure - it fails before ever reaching the git history phase
        symlink_fails = [f for f in checker.findings
                         if f.check_type == "forbidden_path"
                         and f.id == "symlink"
                         and f.path == ".git"]
        self.assertGreater(len(symlink_fails), 0,
                          ".git symlink must be detected in phase 1")
        # Verify git history phase was never reached (fail-closed)
        git_findings = [f for f in checker.findings if f.check_type == "git_history"]
        self.assertEqual(len(git_findings), 0,
                        "git history phase must not run when .git is a symlink")

    def test_clean_state_pass(self):
        """A clean working tree passes."""
        self._clean()
        _, checker = self.run_preflight()
        self._git_pass(checker, "git-clean-state")

    def test_dirty_state_fails(self):
        """Unstaged changes fail."""
        self._clean()
        self.create_file("dirty.txt", "unstaged\n")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-clean-state")

    def test_staged_changes_fails(self):
        """Staged but uncommitted changes fail."""
        self._clean()
        self.create_file("staged.txt", "staged\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-clean-state")

    def test_main_branch_pass(self):
        """HEAD on main passes."""
        self._clean()
        _, checker = self.run_preflight()
        self._git_pass(checker, "git-main-branch")

    def test_not_main_branch_fails(self):
        """HEAD on a branch other than main fails."""
        self._clean()
        # create and switch to a non-main branch
        subprocess.run(["git", "branch", "-m", "develop"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-main-branch")

    def test_detached_head_fails(self):
        """Detached HEAD fails."""
        self._clean()
        # detach HEAD
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root,
            capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "checkout", "--detach", head_sha], cwd=self.root,
                       capture_output=True, text=True, check=True)
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-main-branch")

    def test_two_commits_fail(self):
        self._clean()
        self.create_file("extra.txt", "extra content")
        self.commit_all(message="second")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-single-commit")

    def test_tag_fails(self):
        self._clean()
        self.git("tag", "v1")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-fresh-refs")

    def test_second_branch_fails(self):
        self._clean()
        self.git("branch", "other")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-fresh-refs")

    def test_remote_fails(self):
        self._clean()
        # configure a remote (no fetch); URL must NOT appear in findings
        self.git("remote", "add", "origin", "https://example.invalid/private.git")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-no-remotes")
        for f in self.findings(checker, "git_history"):
            self.assertNotIn("example.invalid", f.details + f.path)

    def test_shallow_fails(self):
        self._clean()
        shallow = self.root / ".git" / "shallow"
        shallow.parent.mkdir(parents=True, exist_ok=True)
        shallow.write_text("0" * 40 + "\n")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-not-shallow")

    def test_grafts_fails(self):
        self._clean()
        grafts = self.root / ".git" / "info" / "grafts"
        grafts.parent.mkdir(parents=True, exist_ok=True)
        grafts.write_text("0" * 40 + " " + "1" * 40 + "\n")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-no-grafts")

    def test_replace_ref_fails(self):
        self._clean()
        ok, head = self.git("rev-parse", "HEAD")
        # Create a replace ref directly. A self-replace is a traversal no-op
        # (so other git checks stay valid) but lives in refs/replace/*, which
        # `git replace --list` detects. Reliable on a single-commit repo, where
        # `git replace --graft` of the root commit is a no-op and is refused.
        subprocess.run(["git", "update-ref", f"refs/replace/{head}", head],
                       cwd=self.root, capture_output=True, text=True, check=True)
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-no-replace")

    def test_alternates_fails(self):
        self._clean()
        alt = self.root / ".git" / "objects" / "info" / "alternates"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text("/some/other/repo/.git/objects\n")
        result, checker = self.run_preflight()
        self.assertFalse(result)
        self._git_fail(checker, "git-no-alternates")

    def test_linked_worktree_fails(self):
        """The named common-dir edge case: a linked worktree shares another
        repo's object store and must be rejected as non-self-contained."""
        main_repo = Path(self.temp_dir) / "main"
        main_repo.mkdir()
        # build the fixture in the main repo and commit
        for art in REQUIRED_ARTIFACTS:
            art_path = main_repo / art
            art_path.parent.mkdir(parents=True, exist_ok=True)
            art_path.write_text(f"# {art}\n")
        subprocess.run(["git", "init", "-q"], cwd=main_repo,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=main_repo, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=main_repo, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"],
                       cwd=main_repo, capture_output=True, text=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=main_repo,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=main_repo,
                       capture_output=True, text=True, check=True)
        wt = Path(self.temp_dir) / "wt"
        subprocess.run(["git", "worktree", "add", str(wt)], cwd=main_repo,
                       capture_output=True, text=True, check=True)
        result, checker = self.run_preflight(root=wt)
        self.assertFalse(result)
        self._git_fail(checker, "git-self-contained")

    def test_clean_repo_passes(self):
        self._clean()
        result, checker = self.run_preflight()
        self.assertEqual(self.failures(checker, "git_history"), [])
        self.assertTrue(result, msg=self._failmsg(checker))


class TestPolicyErrors(_Fixture):
    """Policy file errors are handled cleanly."""

    def test_missing_policy_file(self):
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        nonexistent = self.root / "nonexistent_policy.json"
        from public_cut_preflight import PolicyError
        with self.assertRaises(PolicyError):
            PublicCutPreflight(self.root, nonexistent)

    def test_malformed_json(self):
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        bad_policy = self.root / "bad.json"
        bad_policy.write_text("{ invalid json }")
        with self.assertRaises(Exception):  # PolicyError or JSONDecodeError
            PublicCutPreflight(self.root, bad_policy)

    def test_policy_missing_required_fields(self):
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        bad_policy = self.root / "incomplete.json"
        bad_policy.write_text('{"policy_name": "test"}')  # missing required lists
        with self.assertRaises(Exception):  # PolicyError
            PublicCutPreflight(self.root, bad_policy)


class TestDeterminism(_Fixture):
    """Output is deterministic across runs."""

    def test_repeatable_sorted_output(self):
        self.create_clean_artifacts()
        self.create_file("zzz.py", "password = 'abcde' + 'fghijklmnop'\n")
        self.create_file("aaa.py", "token = 'qrstu' + 'vwxyz012345'\n")
        self.init_git()
        self.commit_all()
        _, c1 = self.run_preflight()
        _, c2 = self.run_preflight()
        key = lambda f: (f.check_type, f.id, f.path, f.details)
        self.assertEqual(sorted(c1.findings, key=key),
                         sorted(c2.findings, key=key))
        # print order is total-sorted
        self.assertEqual([key(f) for f in sorted(c1.findings, key=key)],
                         sorted(key(f) for f in c1.findings))

    def test_print_findings_byte_identical_across_runs(self):
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        _, c1 = self.run_preflight()
        _, c2 = self.run_preflight()
        buf1 = io.StringIO()
        buf2 = io.StringIO()
        with patch.object(preflight_module.sys, "stdout", buf1):
            c1.print_findings()
        with patch.object(preflight_module.sys, "stdout", buf2):
            c2.print_findings()
        self.assertEqual(buf1.getvalue().encode("utf-8"),
                         buf2.getvalue().encode("utf-8"))


class TestPublicCandidateSnapshot(unittest.TestCase):
    def test_includes_new_sources_but_not_ignored_local_files(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source, dest = Path(source_dir), Path(dest_dir)
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            (source / ".gitignore").write_text("*.local\n", encoding="utf-8")
            (source / "tracked.py").write_text("old = True\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            (source / "tracked.py").write_text("current = True\n", encoding="utf-8")
            # Generated credential-shaped fixture: never a real credential.
            (source / "new-test.mjs").write_text(
                "const " + "token" + " = '" + "synthetic" * 3 + "';\n", encoding="utf-8")
            (source / "private.local").write_text("local only\n", encoding="utf-8")
            with patch.object(sys.modules[__name__], "REPO_ROOT", source):
                build_public_candidate_snapshot(dest)
            self.assertEqual((dest / "tracked.py").read_text(), "current = True\n")
            self.assertTrue((dest / "new-test.mjs").is_file(), "new shippable files must be scanned before commit")
            self.assertFalse((dest / "private.local").exists())
            checker = PublicCutPreflight(dest, SCRIPTS_DIR / "public_cut_policy.json")
            checker.check_secret_markers()
            self.assertTrue(any(f.path == "new-test.mjs" and f.id == "secret-assignment"
                                and not f.passed for f in checker.findings))


class TestCurrentPublicCandidate(unittest.TestCase):
    """The current tree has evacuated private data but is not institutionally
    approved. These tests scan a snapshot of what `git` would actually
    publish (tracked plus nonignored new files, fresh single commit) rather than the live
    working checkout, which always carries gitignored local dev files
    (.env, CLAUDE.local.md, .claude/inbox, ...) that differ by machine and
    are never candidates for publication in the first place."""

    @classmethod
    def setUpClass(cls):
        cls._snapshot_dir = tempfile.mkdtemp()
        cls.snapshot_root = Path(cls._snapshot_dir)
        build_public_candidate_snapshot(cls.snapshot_root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._snapshot_dir, ignore_errors=True)

    def test_current_repo_has_no_forbidden_private_pack(self):
        checker = PublicCutPreflight(self.snapshot_root, SCRIPTS_DIR / "public_cut_policy.json")
        result = checker.run()
        self.assertTrue(result)
        self.assertEqual(
            [f for f in checker.findings
             if f.check_type == "forbidden_path" and not f.passed],
            [],
        )
        missing = {
            f.path for f in checker.findings
            if f.check_type == "required_artifact" and not f.passed
        }
        self.assertEqual(missing, set())

    def test_current_repo_cli_reaches_later_phases(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "public_cut_preflight.py"),
             str(self.snapshot_root)],
            capture_output=True)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn(b"forbidden_path private-town-pack", proc.stdout)
        self.assertIn(b"[PASS] required_artifact docs/policies/DATA_MAP.md", proc.stdout)

    def test_current_repo_has_no_credential_marker_findings(self):
        checker = PublicCutPreflight(
            self.snapshot_root, SCRIPTS_DIR / "public_cut_policy.json")
        checker.check_secret_markers()
        self.assertEqual(
            [f for f in checker.findings
             if f.check_type in {"secret_marker", "secret_scan"}
             and not f.passed],
            [],
        )

    def test_current_repo_has_no_installation_private_markers(self):
        # People, private hosts, infrastructure ids and public addresses:
        # class, path and line only, so a failure names no value.
        checker = PublicCutPreflight(
            self.snapshot_root, SCRIPTS_DIR / "public_cut_policy.json")
        checker.check_secret_markers()
        self.assertEqual(
            ["%s %s %s" % (f.id, f.path, f.details) for f in checker.findings
             if f.check_type == "private_marker" and not f.passed],
            [],
        )


class TestONOFOLLOWPlatformCheck(unittest.TestCase):
    """Platform O_NOFOLLOW check is fail-closed and deterministic."""

    def test_module_loads_on_any_platform(self):
        """The module must always be importable, regardless of O_NOFOLLOW support."""
        # The check is deferred to runtime, so import always works
        import public_cut_preflight as preflight
        # Module is importable
        self.assertTrue(hasattr(preflight, 'PublicCutPreflight'))
        self.assertTrue(hasattr(preflight, '_check_onofollow_support'))
        # O_NOFOLLOW check function exists
        self.assertTrue(callable(preflight._check_onofollow_support))

    def test_onofollow_check_passes_on_supported_platform(self):
        """On platforms with O_NOFOLLOW, the check passes and returns the flag value."""
        if not (hasattr(os, 'O_NOFOLLOW') and os.O_NOFOLLOW):
            self.skipTest("O_NOFOLLOW not available on this platform")
        import public_cut_preflight as preflight
        value = preflight._check_onofollow_support()
        self.assertEqual(value, os.O_NOFOLLOW)

    def test_onofollow_zero_fails_deterministically(self):
        """Runtime check rejects zero O_NOFOLLOW deterministically."""
        import public_cut_preflight as preflight
        from public_cut_preflight import PlatformError

        # Temporarily simulate zero O_NOFOLLOW
        original_onofollow = os.O_NOFOLLOW
        try:
            # Monkey-patch os.O_NOFOLLOW to zero
            os.O_NOFOLLOW = 0
            with self.assertRaises(PlatformError) as ctx:
                preflight._check_onofollow_support()
            self.assertIn("zero", str(ctx.exception).lower())
        finally:
            # Restore original value
            os.O_NOFOLLOW = original_onofollow

    def test_onofollow_missing_fails_deterministically(self):
        """Runtime check rejects missing O_NOFOLLOW deterministically."""
        import public_cut_preflight as preflight
        from public_cut_preflight import PlatformError

        # Temporarily simulate missing O_NOFOLLOW
        original_onofollow = getattr(os, 'O_NOFOLLOW', None)
        original_hasattr = hasattr(os, 'O_NOFOLLOW')
        try:
            # Delete O_NOFOLLOW from os module
            if hasattr(os, 'O_NOFOLLOW'):
                delattr(os, 'O_NOFOLLOW')
            with self.assertRaises(PlatformError) as ctx:
                preflight._check_onofollow_support()
            self.assertIn("not available", str(ctx.exception).lower())
        finally:
            # Restore original value
            if original_hasattr:
                os.O_NOFOLLOW = original_onofollow

    def test_cli_emits_clean_error_on_missing_onofollow(self):
        """CLI emits clean value-free error with exit 2 when O_NOFOLLOW is missing."""
        # Create a test wrapper that simulates missing O_NOFOLLOW
        import tempfile
        temp_root = tempfile.mkdtemp()
        try:
            # Create a minimal test wrapper that forces the check to fail
            wrapper = Path(temp_root) / "test_wrapper.py"
            wrapper.write_text(f"""
import sys
sys.path.insert(0, {str(SCRIPTS_DIR)!r})

# Monkey-patch BEFORE importing public_cut_preflight
import os
if hasattr(os, 'O_NOFOLLOW'):
    original_value = os.O_NOFOLLOW
    os.O_NOFOLLOW = 0  # Force zero to trigger PlatformError

# Now import and run
from public_cut_preflight import main
sys.exit(main())

# Restore (won't run but keeps the code clean)
if hasattr(os, 'O_NOFOLLOW'):
    os.O_NOFOLLOW = original_value
""")

            # Run the wrapper
            proc = subprocess.run(
                [sys.executable, str(wrapper), str(temp_root)],
                capture_output=True, text=True)

            # Must exit with error code 2 (environment/platform error)
            self.assertEqual(proc.returncode, 2,
                             f"Expected exit 2, got {proc.returncode}\\nstderr: {proc.stderr}\\nstdout: {proc.stdout}")
            # Error message must be value-free (no credential values)
            self.assertIn("O_NOFOLLOW", proc.stderr)
            self.assertNotIn("PRIVATE", proc.stderr)
            # No traceback (clean CLI error)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertNotIn("File ", proc.stderr)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_onofollow_check_code_structure(self):
        """Verify the code structure enforces O_NOFOLLOW availability deterministically."""
        import inspect
        import public_cut_preflight as preflight

        # Read the source code to verify the check structure
        source = inspect.getsource(preflight)
        # The check must use hasattr AND getattr without a default
        self.assertIn("hasattr(os, 'O_NOFOLLOW')", source)
        # Must NOT have a hardcoded fallback value (the bug we're fixing)
        self.assertNotIn("getattr(os, 'O_NOFOLLOW', 0o400000)", source)
        # Must NOT import fcntl (unused import that was in the old code)
        self.assertNotIn("import fcntl", source)
        # Must raise PlatformError when unavailable
        self.assertIn("raise PlatformError", source)
        # Check is deferred to a function, not module-level code
        self.assertIn("def _check_onofollow_support", source)


class TestSymlinkInspectionError(unittest.TestCase):
    """Inspection errors during symlink checks fail-closed with value-free findings."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir)
        self.policy_path = SCRIPTS_DIR / "public_cut_policy.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_file(self, rel_path: str, content: str = "content\n") -> Path:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def init_git(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=self.root, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=self.root, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"],
                       cwd=self.root, capture_output=True, text=True, check=True)
        subprocess.run(["git", "branch", "-m", "main"], cwd=self.root,
                       capture_output=True, text=True, check=True)

    def commit_all(self, message="init"):
        subprocess.run(["git", "add", "-A"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=self.root,
                       capture_output=True, text=True, check=True)

    def test_is_symlink_raises_oserror_fails_closed_value_free(self):
        """When Path.is_symlink/lstat inspection raises OSError, the run returns False
        with an inspection-error finding, value-free (no file content leaked)."""
        # Create a clean fixture
        with open(SCRIPTS_DIR / "public_cut_policy.json", encoding="utf-8") as f:
            policy = json.load(f)
        for art in policy.get("required_public_artifacts", []):
            self.create_file(art['path'], f"# {art['path']}\n")
        self.create_file("app/server.py", "print('hi')\n")
        self.init_git()
        self.commit_all()

        # Mock Path.is_symlink to raise OSError

        def failing_is_symlink(self):
            raise OSError("simulated inspection failure")

        # Patch Path.is_symlink to raise on every call
        with patch.object(Path, 'is_symlink', failing_is_symlink):
            checker = PublicCutPreflight(self.root, self.policy_path)
            result = checker.run()

        # The run must return False (fail-closed)
        self.assertFalse(result, "run must return False when inspection fails")

        # Must have an inspection-error finding
        inspection_errors = [f for f in checker.findings
                           if f.check_type == "forbidden_path"
                           and f.id == "inspection-error"
                           and f.passed is False]
        self.assertGreater(len(inspection_errors), 0,
                         "inspection error must produce a failure finding")

        # The finding must not leak file CONTENT - exception metadata is OK
        # Check that no file content from the tree appears in findings
        for f in inspection_errors:
            # No file content from our fixture
            self.assertNotIn("print('hi')", f.details)
            self.assertNotIn("# README.md", f.details)
            # Should indicate inspection failed
            self.assertIn("cannot inspect", f.details.lower())
            self.assertIn("inspection error", f.description.lower())


class TestCLI(unittest.TestCase):
    """The CLI entrypoint."""

    @classmethod
    def setUpClass(cls):
        # A snapshot of tracked and nonignored new publication inputs -- see
        # build_public_candidate_snapshot's docstring.
        cls._snapshot_dir = tempfile.mkdtemp()
        cls.snapshot_root = Path(cls._snapshot_dir)
        build_public_candidate_snapshot(cls.snapshot_root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._snapshot_dir, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "public_cut_preflight.py"), *args],
            capture_output=True, text=True)

    def test_requires_root(self):
        r = self._run()
        self.assertNotEqual(r.returncode, 0)

    def test_nonexistent_root_exits_error(self):
        r = self._run("/nonexistent/path/does/not/exist")
        self.assertNotEqual(r.returncode, 0)

    def test_current_repo_exits_zero(self):
        r = self._run(str(self.snapshot_root))
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("forbidden_path private-town-pack", r.stdout)
        self.assertIn("[PASS] required_artifact docs/policies/DATA_MAP.md", r.stdout)
        self.assertIn("git_history", r.stdout)
        # With the forbidden path gone, external evidence is visible and the
        # later phases execute rather than being hidden by fail-first.
        lines = r.stdout.strip().split("\n")
        ext_lines = [l for l in lines if l.startswith("[EXT]")]
        self.assertGreater(len(ext_lines), 0)


class TestPolicyValidation(_Fixture):
    """Policy validation catches errors at load time."""

    def test_path_with_traversal_rejected(self):
        """Policy paths with '..' traversal are rejected at load time."""
        self.create_clean_artifacts()
        bad_policy = self.root / "bad.json"
        bad_policy.write_text(json.dumps({
            "policy_version": "2.3.0-tier7",
            "policy_name": "bad",
            "required_public_artifacts": [
                {"path": "../etc/passwd", "min_bytes": 1}
            ],
            "forbidden_paths": [],
            "secret_marker_classes": [],
            "external_evidence": []
        }))
        from public_cut_preflight import PolicyError
        with self.assertRaises(PolicyError) as ctx:
            PublicCutPreflight(self.root, bad_policy)
        self.assertIn("..", str(ctx.exception))

    def test_absolute_path_rejected(self):
        """Absolute paths in policy are rejected at load time."""
        self.create_clean_artifacts()
        bad_policy = self.root / "bad.json"
        bad_policy.write_text(json.dumps({
            "policy_version": "2.3.0-tier7",
            "policy_name": "bad",
            "required_public_artifacts": [
                {"path": "/etc/passwd", "min_bytes": 1}
            ],
            "forbidden_paths": [],
            "secret_marker_classes": [],
            "external_evidence": []
        }))
        from public_cut_preflight import PolicyError
        with self.assertRaises(PolicyError) as ctx:
            PublicCutPreflight(self.root, bad_policy)
        self.assertIn("absolute", str(ctx.exception).lower())

    def test_invalid_forbidden_regex_rejected(self):
        """Invalid regex in forbidden_paths raises PolicyError."""
        self.create_clean_artifacts()
        bad_policy = self.root / "bad.json"
        bad_policy.write_text(json.dumps({
            "policy_version": "2.3.0-tier7",
            "policy_name": "bad",
            "required_public_artifacts": [],
            "forbidden_paths": [
                {"id": "bad-regex", "match": "regex", "pattern": "(?unclosed", "description": "x"}
            ],
            "secret_marker_classes": [],
            "external_evidence": []
        }))
        from public_cut_preflight import PolicyError
        with self.assertRaises(PolicyError):
            PublicCutPreflight(self.root, bad_policy)

    def test_duplicate_forbidden_id_rejected(self):
        """Duplicate IDs in forbidden_paths raises PolicyError."""
        self.create_clean_artifacts()
        bad_policy = self.root / "bad.json"
        bad_policy.write_text(json.dumps({
            "policy_version": "2.3.0-tier7",
            "policy_name": "bad",
            "required_public_artifacts": [],
            "forbidden_paths": [
                {"id": "dup", "match": "name", "pattern": "x", "description": "x"},
                {"id": "dup", "match": "name", "pattern": "y", "description": "y"}
            ],
            "secret_marker_classes": [],
            "external_evidence": []
        }))
        from public_cut_preflight import PolicyError
        with self.assertRaises(PolicyError):
            PublicCutPreflight(self.root, bad_policy)

    def test_invalid_secret_marker_regex_rejected(self):
        """Invalid regex in secret_marker_classes raises PolicyError."""
        self.create_clean_artifacts()
        bad_policy = self.root / "bad.json"
        bad_policy.write_text(json.dumps({
            "policy_version": "2.3.0-tier7",
            "policy_name": "bad",
            "required_public_artifacts": [],
            "forbidden_paths": [],
            "secret_marker_classes": [
                {"id": "bad", "format": "regex", "patterns": ["(?unclosed"]}
            ],
            "external_evidence": []
        }))
        from public_cut_preflight import PolicyError
        with self.assertRaises(PolicyError):
            PublicCutPreflight(self.root, bad_policy)


class TestGitEnvironmentScrubbing(_Fixture):
    """Git subprocesses run with scrubbed environment."""

    def test_git_config_count_rejected(self):
        """GIT_CONFIG_COUNT and GIT_CONFIG_KEY_* are rejected."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        with _env.patched_env({"GIT_CONFIG_COUNT": "1"}):
            result, checker = self.run_preflight()
            self.assertFalse(result)
            ambient = [f for f in self.failures(checker, "ambient_environment")
                       if f.id == "git-overrides"]
            self.assertGreater(len(ambient), 0)
            self.assertIn("GIT_CONFIG_COUNT", ambient[0].details)

    def test_git_config_key_rejected(self):
        """GIT_CONFIG_KEY_0 is rejected."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()
        with _env.patched_env({"GIT_CONFIG_KEY_0": "user.name"}):
            result, checker = self.run_preflight()
            self.assertFalse(result)
            ambient = [f for f in self.failures(checker, "ambient_environment")
                       if f.id == "git-overrides"]
            self.assertGreater(len(ambient), 0)
            self.assertIn("GIT_CONFIG_KEY_0", ambient[0].details)


class TestPruneDirectoryBypass(_Fixture):
    """Prune-directory handling must not bypass symlink or credential checks."""

    def test_pruned_symlink_directory_is_detected(self):
        """A directory in prune_dirs that is a symlink must be detected and rejected.
        This fixes the bypass where node_modules symlink was never inspected."""
        self.create_clean_artifacts()
        # Create node_modules as a symlink (normally in prune_dirs)
        real_dir = self.root / "real_deps"
        real_dir.mkdir()
        real_dir.joinpath("package.json").write_text("{}\n")
        node_modules = self.root / "node_modules"
        node_modules.symlink_to(real_dir)
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        # Must fail because node_modules is a symlink
        self.assertFalse(result)
        # The symlink is detected in check_forbidden_paths (phase 1), which fails
        # immediately - this is correct fail-closed behavior
        symlink_fails = [f for f in checker.findings
                         if f.check_type == "forbidden_path"
                         and f.id == "symlink"
                         and f.passed is False
                         and f.path == "node_modules"]
        self.assertGreater(len(symlink_fails), 0,
                          "pruned symlink directory must be detected in phase 1")
        # Verify that the symlink detection happened before secret scan phase
        # (fail-closed: phase 1 blocks all later phases)
        secret_scan_findings = [f for f in checker.findings
                                if f.check_type == "secret_scan"]
        self.assertEqual(len(secret_scan_findings), 0,
                        "secret scan must not run when symlink is detected in phase 1")

    def test_tracked_credential_in_pruned_directory_is_detected(self):
        """A tracked file containing a credential in a pruned directory must be detected.
        This fixes the bypass where tracked node_modules/leak.py was never scanned."""
        self.create_clean_artifacts()
        # Create a tracked file in node_modules (normally pruned) with a secret
        node_modules = self.root / "node_modules"
        node_modules.mkdir()
        leak_file = node_modules / "leak.py"
        leak_file.write_text('password = "sk-' + 'ant-1234567890abcdefghijklmnopqr"\n')
        self.init_git()
        # Commit the file so it's tracked
        subprocess.run(["git", "add", "node_modules/leak.py"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add leak"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        result, checker = self.run_preflight()
        # Must fail because the tracked file contains a credential marker
        self.assertFalse(result)
        # Check for the secret marker finding
        secret_hits = [f for f in checker.findings
                      if f.check_type == "secret_marker"
                      and f.id == "anthropic-key"
                      and f.path == "node_modules/leak.py"]
        self.assertGreater(len(secret_hits), 0,
                          "tracked credential in pruned directory must be detected")

    def test_ignored_untracked_file_in_pruned_directory_is_not_scanned(self):
        """An ordinary ignored file in a pruned directory must not be scanned.
        This preserves the useful ability to ignore build caches that cannot ship."""
        self.create_clean_artifacts()
        # Add .gitignore to ignore node_modules first
        gitignore = self.root / ".gitignore"
        gitignore.write_text("node_modules/\n")
        self.init_git()
        subprocess.run(["git", "add", ".gitignore"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add gitignore"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        # Now create an untracked file in node_modules with a fake secret
        # This file is ignored by .gitignore so it won't be committed
        node_modules = self.root / "node_modules"
        node_modules.mkdir()
        leak_file = node_modules / "untracked_leak.py"
        leak_file.write_text('password = "sk-' + 'ant-1234567890abcdefghijklmnopqr"\n')
        # DO NOT add or commit this file - it remains untracked (but ignored)
        result, checker = self.run_preflight()
        # The working tree will be dirty (untracked files exist), which fails git-clean-state
        # But the key assertion is that the untracked file was NOT scanned for secrets
        # Check that no secret marker was found in the untracked file
        secret_hits = [f for f in checker.findings
                      if f.check_type == "secret_marker"
                      and "untracked_leak.py" in f.path]
        self.assertEqual(len(secret_hits), 0,
                        "untracked files in pruned directories must not be scanned")
        # The result should be False (due to dirty working tree), but NOT because of a secret
        self.assertFalse(result)
        # Verify the failure is due to git-clean-state, not a secret marker
        git_fails = [f for f in checker.findings
                     if f.check_type == "git_history" and f.id == "git-clean-state"]
        self.assertGreater(len(git_fails), 0,
                          "failure should be due to dirty working tree, not secret scan")

    def test_tracked_symlink_in_pruned_directory_is_detected(self):
        """A tracked symlink file in a pruned directory must be detected and rejected."""
        self.create_clean_artifacts()
        # Create a real file and a symlink to it in node_modules
        real_file = self.root / "real_config.json"
        real_file.write_text('{"key": "sk-' + 'or-1234567890abcdefghijklmnop"}\n')
        node_modules = self.root / "node_modules"
        node_modules.mkdir()
        link_file = node_modules / "config.json"
        link_file.symlink_to(real_file)
        self.init_git()
        # Track both files
        subprocess.run(["git", "add", "real_config.json", "node_modules/config.json"],
                       cwd=self.root, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add files"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        result, checker = self.run_preflight()
        # Must fail because the tracked file is a symlink
        self.assertFalse(result)
        # Check for symlink finding
        symlink_fails = [f for f in checker.findings
                         if f.check_type == "secret_scan"
                         and f.path == "node_modules/config.json"
                         and "symlink" in f.details.lower()]
        self.assertGreater(len(symlink_fails), 0,
                          "tracked symlink in pruned directory must be detected")

    def test_newline_filename_is_handled_safely(self):
        """A tracked file with newline in filename is rejected without breaking the scanner.
        This ensures NUL-delimited git ls-files -z parsing handles edge cases."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()

        # Patch subprocess.run to return git ls-files output with a newline in a filename
        # This simulates what git ls-files -z would return if a tracked file had a newline
        original_run = subprocess.run
        def newline_in_filename(*args, **kwargs):
            # If this is a git ls-files -z call, return output with newline
            if (len(args) >= 1 and
                isinstance(args[0], list) and
                len(args[0]) >= 3 and
                args[0][0] == "git" and
                args[0][1] == "ls-files" and
                args[0][2] == "-z"):
                # Return a CompletedProcess-like object with a filename containing newline
                class FakeCompletedProcess:
                    def __init__(self):
                        self.returncode = 0
                        # Valid NUL-terminated output, but one path contains newline
                        self.stdout = b"node_modules/evil\nfile.py\x00"
                        self.stderr = b""
                return FakeCompletedProcess()
            return original_run(*args, **kwargs)

        with patch.object(subprocess, 'run', side_effect=newline_in_filename):
            result, checker = self.run_preflight()
            # Must produce a generic scan-health finding (fail-closed)
            scan_health = [f for f in checker.findings
                          if f.check_type == "secret_scan"
                          and f.id == "."
                          and f.description == "scan health"
                          and f.passed is False]
            self.assertEqual(len(scan_health), 1,
                           "newline filename must produce generic scan-health finding")
            # The finding is value-free (no credential values)
            self.assertNotIn("SPLIT-MARKER", scan_health[0].details)
            self.assertNotIn("DETECTED", scan_health[0].details)

    def test_malformed_git_ls_fails_closed(self):
        """Malformed or failing git ls-files output produces a generic scan-health finding."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()

        # Create a tracked file in node_modules
        node_modules = self.root / "node_modules"
        node_modules.mkdir()
        (node_modules / "clean.py").write_text("# clean file\n")
        subprocess.run(["git", "add", "node_modules/clean.py"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add file"], cwd=self.root,
                       capture_output=True, text=True, check=True)

        # Patch subprocess.run to return malformed NUL output (missing final NUL)
        original_run = subprocess.run
        def malformed_ls_files(*args, **kwargs):
            # If this is a git ls-files -z call, return malformed output
            if (len(args) >= 1 and
                isinstance(args[0], list) and
                len(args[0]) >= 3 and
                args[0][0] == "git" and
                args[0][1] == "ls-files" and
                args[0][2] == "-z"):
                # Return a CompletedProcess-like object with malformed output
                # Non-empty output lacking the required final NUL
                class FakeCompletedProcess:
                    def __init__(self):
                        self.returncode = 0
                        self.stdout = b"node_modules/clean.py"  # Missing final NUL!
                        self.stderr = b""
                return FakeCompletedProcess()
            return original_run(*args, **kwargs)

        with patch.object(subprocess, 'run', side_effect=malformed_ls_files):
            result, checker = self.run_preflight()
            # Must produce exactly one generic scan-health finding
            scan_health = [f for f in checker.findings
                          if f.check_type == "secret_scan"
                          and f.id == "."
                          and f.description == "scan health"
                          and f.passed is False]
            self.assertEqual(len(scan_health), 1,
                           "malformed git ls-files output must produce generic scan-health finding")
            # The finding is value-free (no credential values)
            for f in scan_health:
                self.assertNotIn("sk-ant", f.details)
                self.assertNotIn("password", f.details)

    def test_git_command_failure_fails_closed(self):
        """Git ls-files command failure produces a generic scan-health finding."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()

        # Create a tracked file in node_modules
        node_modules = self.root / "node_modules"
        node_modules.mkdir()
        (node_modules / "clean.py").write_text("# clean file\n")
        subprocess.run(["git", "add", "node_modules/clean.py"], cwd=self.root,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add file"], cwd=self.root,
                       capture_output=True, text=True, check=True)

        # Mock _git_tracked_files_nul to simulate git failure (returns None)
        def git_failure(self):
            return None  # Simulates git command failure
        with patch.object(preflight_module.PublicCutPreflight,
                          '_git_tracked_files_nul',
                          git_failure):
            result, checker = self.run_preflight()
            # Should still pass (clean repo, but git error is recorded)
            scan_health = [f for f in checker.findings
                          if f.check_type == "secret_scan"
                          and f.description == "scan health"
                          and f.passed is False
                          and "tracked files in pruned directories" in f.details]
            # Git failure produces a generic scan-health finding
            self.assertEqual(len(scan_health), 1,
                           "git command failure must produce generic scan-health finding")
            # The finding is value-free (no credential values)
            for f in scan_health:
                self.assertNotIn("sk-ant", f.details)
                self.assertNotIn("password", f.details)

    def test_test_file_self_scan(self):
        """This test file itself contains no credential markers that match the scanner.
        All credential-shaped literals are split with string concatenation."""
        import ast
        test_file = Path(__file__)
        source = test_file.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Collect all string literals in the source
        literals = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.append(node.value)
            elif isinstance(node, ast.JoinedStr):  # f-strings
                for part in node.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        literals.append(part.value)

        # Check none of the literals match credential patterns
        # Load the policy to get patterns
        with open(SCRIPTS_DIR / "public_cut_policy.json", encoding="utf-8") as f:
            policy = json.load(f)

        matched = []
        for cls in policy.get("secret_marker_classes", []):
            cls_id = cls.get("id", "unknown")
            fmt = cls.get("format", "substring")
            for pat in cls.get("patterns", []):
                try:
                    if fmt == "regex":
                        rx = re.compile(pat)
                    else:
                        rx = re.compile(re.escape(pat))
                except re.error:
                    continue
                for lit in literals:
                    if rx.search(lit):
                        matched.append((cls_id, lit[:50]))

        self.assertEqual(len(matched), 0,
                        f"test file contains {len(matched)} credential literals: {matched}")


class TestOpenInstrumentation(_Fixture):
    """Instrumentation proves we never open beneath forbidden paths."""

    def test_private_pack_never_opened_or_scanned(self):
        """A forbidden pack directory is never opened, and no file beneath it is
        opened or scanned. The test uses monkey-patching to prove the os.walk
        descent and file open operations never reach beneath the forbidden prefix."""
        self.create_clean_artifacts()
        # Create a secret that would be detected if scanned
        self.create_file("packs/ar-b-pinamar/secret.txt",
                         "AKIAIOS" + "FODNN7EXAMPLE")
        self.create_file("packs/ar-b-pinamar/subdir/another.txt",
                         "sk-ant-" + "1234567890abcdefg")
        self.init_git()
        self.commit_all()

        # Instrument all three operations: os.open, os.scandir, Path.stat
        opened_paths = []
        scandir_paths = []
        stated_paths = []

        original_open = os.open
        def instrumented_open(path, *args, **kwargs):
            opened_paths.append(Path(path).as_posix())
            return original_open(path, *args, **kwargs)

        original_scandir = os.scandir
        def instrumented_scandir(path, *args, **kwargs):
            scandir_paths.append(Path(path).as_posix())
            return original_scandir(path, *args, **kwargs)

        original_stat = Path.stat
        # On Python 3.10-3.12 Path.is_symlink()/lstat() call
        # stat(follow_symlinks=False) (3.13+ goes through os.path instead);
        # the wrapper must pass the keyword through.
        def instrumented_stat(self, *args, **kwargs):
            stated_paths.append(self.as_posix())
            return original_stat(self, *args, **kwargs)

        # Patch all three operations
        with patch.object(preflight_module.os, 'open', side_effect=instrumented_open), \
             patch.object(preflight_module.os, 'scandir', side_effect=instrumented_scandir), \
             patch.object(preflight_module.Path, 'stat', instrumented_stat):
            result, checker = self.run_preflight()

        self.assertFalse(result, "preflight must fail on forbidden pack")
        # Check that no path beneath packs/ar-b-pinamar was ever touched by any operation
        for operation_name, path_list in [("open", opened_paths), ("scandir", scandir_paths), ("stat", stated_paths)]:
            beneath_pack = {os.path.relpath(p, self.root) for p in path_list
                           if p.startswith(str(self.root)) and "ar-b-pinamar" in p}
            nested_files = {p for p in beneath_pack if p.startswith("packs/ar-b-pinamar/")}
            self.assertEqual(nested_files, set(),
                             f"no file beneath forbidden pack should be {operation_name}'ed")

        # The pack directory itself may have been seen, but never its contents
        all_rel = set()
        for path_list in [opened_paths, scandir_paths, stated_paths]:
            for p in path_list:
                if p.startswith(str(self.root)):
                    all_rel.add(os.path.relpath(p, self.root))

        # Check we never see more than 2 components deep in the forbidden pack
        for rel in all_rel:
            if "ar-b-pinamar" in rel:
                parts = Path(rel).parts
                # packs/ is depth 1, packs/ar-b-pinamar is depth 2
                # Anything deeper (depth 3+) means we descended into the pack
                if len(parts) > 2:
                    self.fail(f"operation descended into forbidden pack beyond allowed depth: {rel}")

    def test_forbidden_path_scandir_error_fails_closed(self):
        """A scandir error during forbidden-path traversal fails closed with a finding."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()

        # Force os.scandir to fail during os.walk, which triggers the onerror callback
        call_count = [0]
        original_scandir = os.scandir
        def failing_scandir(path):
            call_count[0] += 1
            # Fail on the first directory after root (simulates permission/error mid-traversal)
            if call_count[0] > 1:
                raise OSError("simulated scandir failure")
            return original_scandir(path)

        with patch.object(os, 'scandir', side_effect=failing_scandir):
            result, checker = self.run_preflight()
            # The error must be caught and reported as a failure
            self.assertFalse(result, "run must return False on scandir error")
            scandir_fails = [f for f in checker.findings
                            if f.check_type == "forbidden_path"
                            and f.id == "scandir-error"
                            and f.passed is False]
            self.assertGreater(len(scandir_fails), 0,
                             "scandir error must produce a failure finding")

    def test_secret_scan_scandir_error_fails_closed(self):
        """A scandir error during secret-scan traversal fails closed with a finding."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()

        # Force os.scandir to fail during the secret scan phase (second walk)
        call_count = [0]
        original_scandir = os.scandir
        def failing_scandir(path):
            call_count[0] += 1
            # Fail deeper in traversal (after forbidden-path phase completes)
            # The first walk (forbidden_paths) hits ~10 entries, second walk (secret_scan) fails
            if call_count[0] > 12:
                raise OSError("simulated scandir failure during secret scan")
            return original_scandir(path)

        with patch.object(os, 'scandir', side_effect=failing_scandir):
            result, checker = self.run_preflight()
            # The error must be caught and reported as a failure
            self.assertFalse(result, "run must return False on scandir error during scan")
            # Check for secret_scan traversal error (not forbidden_path)
            scan_errors = [f for f in checker.findings
                           if f.check_type == "secret_scan"
                           and f.id == "."
                           and "traversal error" in f.details
                           and f.passed is False]
            self.assertGreater(len(scan_errors), 0,
                             "scandir error during secret scan must produce a failure finding")

    def test_forced_open_error_fails_closed(self):
        """An os.open error during scanning fails closed."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()

        # Create a file that will be scanned
        self.create_file("scan_me.py", "password = 'x'")

        # Force open to fail on this file
        original_open = os.open
        def failing_open(path, *args, **kwargs):
            if "scan_me.py" in str(path):
                raise OSError("simulated open failure")
            return original_open(path, *args, **kwargs)

        # Patch os.open in the imported module
        with patch.object(preflight_module.os, 'open', side_effect=failing_open):
            _, checker = self.run_preflight()
            # The failed file should be marked as scan health failure
            scan_health = [f for f in self.findings(checker, "secret_scan")
                           if f.description == "scan health" and f.passed is False
                           and "scan_me.py" in f.path]
            self.assertGreater(len(scan_health), 0,
                               "failed open should result in scan health failure")


class TestMalformedNULParsing(_Fixture):
    """Malformed NUL-separated output from git ls-files -z fails closed."""

    def test_consecutive_nuls_fail_closed_with_scan_health_error(self):
        """Consecutive NULs (empty interior record) trigger scan-health failure.
        Regression: git ls-files -z output with b\"README.md\\x00\\x00\" must fail
        closed, not silently accept malformed data."""
        self.create_clean_artifacts()
        self.init_git()
        self.commit_all()

        # Mock subprocess.run to return malformed NUL-separated output
        from unittest.mock import MagicMock
        original_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            # If this is a git ls-files -z call, return malformed data
            if (isinstance(cmd, list) and cmd[0] == "git" and
                "ls-files" in cmd and "-z" in cmd):
                mock = MagicMock()
                mock.returncode = 0
                mock.stdout = b"README.md\x00\x00"  # Malformed: consecutive NULs
                mock.stderr = b""
                return mock
            # Otherwise use the real subprocess
            return original_run(cmd, *args, **kwargs)

        with patch.object(subprocess, 'run', side_effect=mock_run):
            result, checker = self.run_preflight()

        # Must fail closed
        self.assertFalse(result, "malformed NUL output must fail closed")

        # Exactly one scan-health failure, value-free (no file content leaked)
        scan_health_failures = [f for f in self.failures(checker, "secret_scan")
                                if f.description == "scan health"]
        self.assertEqual(len(scan_health_failures), 1,
                        "malformed NUL must produce exactly one scan-health failure")

        # Failure must be value-free (no specific filename or content)
        failure = scan_health_failures[0]
        self.assertIn("cannot verify tracked files in pruned directories",
                     failure.details)
        self.assertNotIn("README.md", failure.details)
        self.assertNotIn("README.md", failure.path)


class TestRequiredArtifactStructure(_Fixture):
    """G0 structural contract: T1-T11, all offline, temp-dir fixtures."""

    def _spec(self, path):
        return ARTIFACT_BY_PATH[path]

    def _assert_value_free(self, checker, *forbidden):
        blob = self.finding_blob(checker)
        for item in forbidden:
            self.assertNotIn(item, blob)

    def test_t1_historical_placeholder_rejected_by_marker_and_floor(self):
        """T1: every padded historical stub fails structurally; the unpadded
        form still fails its floor."""
        self.assertEqual(len(REQUIRED_ARTIFACT_SPECS), 24)
        self.assertEqual({art["path"] for art in REQUIRED_ARTIFACT_SPECS},
                         set(REQUIRED_ARTIFACTS))
        for art in REQUIRED_ARTIFACT_SPECS:
            self.create_file(art["path"], padded_historical_placeholder(art))
        checker = self.phase4()
        rejected = set()
        for art in REQUIRED_ARTIFACT_SPECS:
            hits = [f for f in self.failures(checker, "required_artifact")
                    if f.path == art["path"]]
            self.assertEqual(len(hits), 1, art["path"])
            self.assertIn("historical placeholder stub", hits[0].details)
            self.assertRegex(hits[0].details, r"at line \d+")
            rejected.add(art["path"])
            self._assert_value_free(checker, f"# {art['path']} placeholder")
        self.assertEqual(rejected, set(REQUIRED_ARTIFACTS))

        unpadded_path = "docs/policies/DATA_MAP.md"
        self.create_file(unpadded_path, historical_placeholder(unpadded_path))
        checker = self.phase4()
        floor_hits = [f for f in self.failures(checker, "required_artifact")
                      if f.path == unpadded_path]
        self.assertEqual(len(floor_hits), 1)
        self.assertIn("below floor", floor_hits[0].details)
        self.assertNotEqual(floor_hits[0].details, "empty")

    def test_t2_missing_required_section_names_section_only(self):
        """T2: DATA_MAP.md missing one H2 names that section only."""
        self.create_clean_artifacts()
        art = self._spec("docs/policies/DATA_MAP.md")
        self.create_file("docs/policies/DATA_MAP.md",
                         real_shape_document(art, omit_section="Jurisdictions"))
        checker = self.phase4()
        hits = [f for f in self.failures(checker, "required_artifact")
                if f.path == "docs/policies/DATA_MAP.md"]
        self.assertTrue(any("Jurisdictions" in f.details for f in hits))
        for f in hits:
            self.assertNotIn("Covered.", f.details)
            self.assertNotIn("Synthetic publication fixture", f.details)
        self._assert_value_free(checker, "Covered.")

    def test_t3_approval_form_names_field_never_value(self):
        """T3: malformed approval form names the field, never the value."""
        art = self._spec(".github/GOVERNANCE.md")
        cases = [
            ("absent-section", {"include_approval": False}, "section",
             ()),
            ("wrong-prefix", {"record_line": "record: token:hr-0123456789abcdef\n"},
             "id", ("token:hr-0123456789abcdef",)),
            ("id-too-short", {"record_line": "record: approval:hr-0123\n"},
             "id", ("hr-0123",)),
            ("id-non-hex", {"record_line": "record: approval:hr-0123456789abcdefG\n"},
             "id", ("hr-0123456789abcdefG",)),
            ("id-repo-shaped", {"record_line": "record: approval:humanrounds-pinamar\n"},
             "id", ("humanrounds-pinamar",)),
            ("id-url-shaped", {"record_line": "record: approval:https://example.org\n"},
             "id", ("https://example.org",)),
            ("date-wrong-shape", {"date_line": "date: 13/99/2026\n"},
             "date", ("13/99/2026",)),
            ("date-impossible-month", {"date_line": "date: 2026-13-99\n"},
             "date", ("2026-13-99",)),
            ("date-impossible-day", {"date_line": "date: 2026-02-30\n"},
             "date", ("2026-02-30",)),
        ]
        for name, kwargs, field, forbidden in cases:
            with self.subTest(name):
                self.create_clean_artifacts()
                self.create_file(".github/GOVERNANCE.md", real_shape_document(art, **kwargs))
                checker = self.phase4()
                hits = [f for f in self.failures(checker, "required_artifact")
                        if f.path == ".github/GOVERNANCE.md"
                        and f"malformed approval record field: {field}" in f.details]
                self.assertEqual(len(hits), 1, self.finding_blob(checker))
                self._assert_value_free(checker, *forbidden)

    def test_t4_below_floor_not_empty(self):
        """T4: six real-looking bytes report below floor, not empty."""
        self.create_clean_artifacts()
        self.create_file(".github/GOVERNANCE.md", "Hello\n")
        checker = self.phase4()
        hits = [f for f in self.failures(checker, "required_artifact")
                if f.path == ".github/GOVERNANCE.md"]
        self.assertEqual(len(hits), 1)
        self.assertIn("below floor", hits[0].details)
        self.assertNotEqual(hits[0].details, "empty")
        self.assertNotIn("Hello", hits[0].details)

    def test_t5_invalid_utf8_fails_closed(self):
        """T5: invalid UTF-8 fails closed without echoing bytes."""
        self.create_clean_artifacts()
        path = self.root / "LICENSE"
        path.write_bytes(b"A" * 20000 + b"\xff\xfe")
        checker = self.phase4()
        hits = [f for f in self.failures(checker, "required_artifact")
                if f.path == "LICENSE"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].details, "not valid UTF-8")
        blob = self.finding_blob(checker)
        self.assertNotIn("\xff", blob)

    def test_t6_oversized_fails_closed(self):
        """T6: above max_scan_bytes is oversized; structure is not skipped silently."""
        self.create_clean_artifacts()
        max_scan = int(_POLICY.get("max_scan_bytes", 1 << 20))
        path = self.root / "docs/policies/DATA_MAP.md"
        path.write_bytes(b"A" * (max_scan + 1))
        checker = self.phase4()
        hits = [f for f in self.failures(checker, "required_artifact")
                if f.path == "docs/policies/DATA_MAP.md"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].details, "oversized")

    def _write_policy(self, data):
        path = self.root / "policy.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _base_policy(self):
        return {
            "policy_version": "2.3.0-tier7",
            "policy_name": "test",
            "required_public_artifacts": [{"path": "README.md", "min_bytes": 1}],
            "forbidden_paths": [],
            "secret_marker_classes": [],
            "external_evidence": [],
        }

    def test_t7_strict_schema_family_is_policy_error(self):
        """T7: unknown keys, altered record_prefix, typed fields, version fail closed."""
        cases = []

        typo = self._base_policy()
        typo["required_public_artifacts"] = [
            {"path": "README.md", "min_bytes": 1, "required_sectons": ["X"]}]
        cases.append(("artifact-typo", typo))

        top = self._base_policy()
        top["not_a_real_key"] = True
        cases.append(("unknown-top-level", top))

        rec_unknown = self._base_policy()
        rec_unknown["required_public_artifacts"] = [{
            "path": "README.md", "min_bytes": 1,
            "approval_record": {"required": True, "issuer": "x"},
        }]
        cases.append(("unknown-approval-key", rec_unknown))

        prefix_hyphen = self._base_policy()
        prefix_hyphen["required_public_artifacts"] = [{
            "path": "README.md", "min_bytes": 1,
            "approval_record": {"required": True, "record_prefix": "approval-"},
        }]
        cases.append(("record-prefix-hyphen", prefix_hyphen))

        prefix_slash = self._base_policy()
        prefix_slash["required_public_artifacts"] = [{
            "path": "README.md", "min_bytes": 1,
            "approval_record": {"required": True, "record_prefix": "approvals/"},
        }]
        cases.append(("record-prefix-slash", prefix_slash))

        min_bool = self._base_policy()
        min_bool["required_public_artifacts"] = [
            {"path": "README.md", "min_bytes": True}]
        cases.append(("min-bytes-bool", min_bool))

        min_str = self._base_policy()
        min_str["required_public_artifacts"] = [
            {"path": "README.md", "min_bytes": "1"}]
        cases.append(("min-bytes-string", min_str))

        sections = self._base_policy()
        sections["required_public_artifacts"] = [
            {"path": "README.md", "min_bytes": 1, "required_sections": "Roles"}]
        cases.append(("required-sections-not-list", sections))

        markers = self._base_policy()
        markers["required_public_artifacts"] = [
            {"path": "README.md", "min_bytes": 1, "placeholder_markers": [1]}]
        cases.append(("placeholder-markers-non-string", markers))

        missing = self._base_policy()
        del missing["policy_version"]
        cases.append(("policy-version-missing", missing))

        unknown_ver = self._base_policy()
        unknown_ver["policy_version"] = "2.1.0-tier7"
        cases.append(("policy-version-unknown-prefix", unknown_ver))

        for name, data in cases:
            with self.subTest(name):
                path = self._write_policy(data)
                with self.assertRaises(PolicyError):
                    PublicCutPreflight(self.root, path)

    def test_t8_clean_builder_phase4_all_pass(self):
        """T8: the policy-derived builder satisfies phase 4."""
        self.create_clean_artifacts()
        checker = self.phase4()
        arts = self.findings(checker, "required_artifact")
        self.assertEqual(len(arts), len(REQUIRED_ARTIFACTS))
        self.assertEqual(self.failures(checker, "required_artifact"), [])
        for finding in arts:
            self.assertTrue(finding.passed)

    def test_t9_forbidden_path_skips_placeholder_artifacts(self):
        """T9: forbidden path present with placeholder artifacts yields only
        the forbidden-path finding."""
        for art in REQUIRED_ARTIFACTS:
            self.create_file(art, historical_placeholder(art))
        self.create_file("packs/ar-b-pinamar/x.json", "x")
        self.init_git()
        self.commit_all()
        result, checker = self.run_preflight()
        self.assertFalse(result)
        failing = [f for f in checker.findings if f.passed is False]
        self.assertTrue(failing)
        self.assertTrue(all(f.check_type == "forbidden_path" for f in failing))
        self.assertEqual(self.findings(checker, "required_artifact"), [])
        self.assertEqual(self.findings(checker, "secret_marker"), [])
        self.assertEqual(self.findings(checker, "git_history"), [])

    def test_t10_existing_repo_artifacts_pass_their_contract(self):
        """T10: every policy artifact present in this tree passes, and floors
        match the HEAD calibration table."""
        for path, floor in EXISTING_ARTIFACT_FLOORS.items():
            art = self._spec(path)
            self.assertEqual(art["min_bytes"], floor, path)
            self.assertTrue((REPO_ROOT / path).is_file(), path)

        checker = PublicCutPreflight(REPO_ROOT, SCRIPTS_DIR / "public_cut_policy.json")
        checker.check_required_artifacts()
        for art in REQUIRED_ARTIFACT_SPECS:
            path = REPO_ROOT / art["path"]
            if not path.is_file():
                continue
            fails = [f for f in self.failures(checker, "required_artifact")
                     if f.path == art["path"]]
            if art["path"] in G1_AWAITING_OWNER_APPROVAL:
                # Authored G1 drafts (tier7-readiness.md "G1 open"): the
                # owner's approval id is external evidence and must never be
                # fabricated. Everything else about the document — size,
                # sections, placeholder markers — must already pass, so the
                # ONLY finding allowed is the absent approval record line.
                self.assertEqual(
                    [f.details for f in fails],
                    ["malformed approval record field: record"],
                    art["path"])
                continue
            self.assertEqual(fails, [], art["path"])

    def test_t11_record_line_valid_stub_and_word_boundary(self):
        """T11: valid approval id is exempt; stub values fail; <fill does not
        fire inside refill/filled; pending review as prose is fine."""
        art = self._spec(".github/GOVERNANCE.md")
        extra = ("Boundary check: refill filled and pending review stay prose.\n")

        self.create_clean_artifacts()
        self.create_file(
            ".github/GOVERNANCE.md",
            real_shape_document(art, extra_prose=extra))
        checker = self.phase4()
        gov = [f for f in self.failures(checker, "required_artifact")
               if f.path == ".github/GOVERNANCE.md"]
        self.assertEqual(gov, [], self.finding_blob(checker))

        self.create_file(
            ".github/GOVERNANCE.md",
            real_shape_document(art, extra_prose=extra,
                                record_line="record: pending\n"))
        checker = self.phase4()
        stub_pending = [f for f in self.failures(checker, "required_artifact")
                        if f.path == ".github/GOVERNANCE.md"
                        and "placeholder marker" in f.details]
        self.assertTrue(stub_pending, self.finding_blob(checker))
        self.assertTrue(any("at line" in f.details for f in stub_pending))
        self._assert_value_free(checker, "Boundary check", "Synthetic publication fixture")

        self.create_file(
            ".github/GOVERNANCE.md",
            real_shape_document(art, extra_prose=extra,
                                record_line="record: <fill\n"))
        checker = self.phase4()
        stub_fill = [f for f in self.failures(checker, "required_artifact")
                     if f.path == ".github/GOVERNANCE.md"
                     and "placeholder marker" in f.details]
        self.assertTrue(stub_fill, self.finding_blob(checker))
        self._assert_value_free(checker, "Boundary check", "Synthetic publication fixture")


class TestClosedBinaryAssetSets(_Fixture):
    """Closed-set binary manifests are hermetic and value-free."""

    _SET_ID = "synthetic-binaries"
    _ROOT = "fixtures/public-binaries"
    _MANIFEST = "fixtures/public-binaries/assets.json"

    @staticmethod
    def _fd_count():
        return len(os.listdir("/dev/fd"))

    def _binary_policy_path(self):
        policy = json.loads(json.dumps(_POLICY))
        policy["required_public_artifacts"] = []
        policy["forbidden_paths"] = []
        policy["secret_marker_classes"] = []
        policy["external_evidence"] = []
        policy["public_binary_asset_sets"] = [{
            "id": self._SET_ID,
            "root": self._ROOT,
            "manifest": self._MANIFEST,
            "extensions": [".jpg"],
        }]
        path = self.root / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        return path

    def _write_binary_set(self, named_contents, manifest_paths=None,
                          byte_offsets=None, manifest_extra=None):
        root = self.root / self._ROOT
        root.mkdir(parents=True, exist_ok=True)
        for name, content in named_contents.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        selected = manifest_paths if manifest_paths is not None else sorted(named_contents)
        offsets = byte_offsets or {}
        assets = []
        for name in selected:
            content = named_contents[name]
            assets.append({
                "path": name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content) + offsets.get(name, 0),
                "media_type": "image/jpeg",
                "provenance": {
                    "classification": "synthetic",
                    "source": "from_scratch",
                    "from_scratch": True,
                    "no_real": True,
                    "no_derived": True,
                },
                "review": {
                    "attested": True,
                    "reviewer_id": _FIXTURE_APPROVAL_ID,
                    "date": _FIXTURE_APPROVAL_DATE,
                },
            })
        manifest = {"manifest_version": "1.0", "assets": assets}
        if manifest_extra is not None:
            manifest["unexpected"] = manifest_extra
        (self.root / self._MANIFEST).write_text(
            json.dumps(manifest), encoding="utf-8")

    def _check_binary_set(self):
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        checker.check_binary_asset_sets()
        return checker

    def _set_failures(self, checker):
        return [f for f in self.failures(checker, "binary_asset_set")
                if f.id == self._SET_ID]

    def test_clean_synthetic_binary_set_passes(self):
        self._write_binary_set({
            "a.jpg": synthetic_jpeg(b"a"),
            "nested/b.jpg": synthetic_jpeg(b"b"),
        })
        checker = self._check_binary_set()
        self.assertEqual(self._set_failures(checker), [], self._failmsg(checker))
        passed = [f for f in self.findings(checker, "binary_asset_set") if f.passed]
        self.assertEqual(len(passed), 1)

    def test_missing_manifest_refuses_before_opening_assets(self):
        (self.root / self._ROOT).mkdir(parents=True, exist_ok=True)
        checker = self._check_binary_set()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details, "manifest missing")

    def test_unlisted_or_missing_binary_refuses_closed_set(self):
        contents = {
            "a.jpg": synthetic_jpeg(b"a"),
            "extra.jpg": synthetic_jpeg(b"extra"),
        }
        self._write_binary_set(contents, manifest_paths=["a.jpg"])
        checker = self._check_binary_set()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details, "asset set does not match manifest")
        self.assertNotIn("extra.jpg", self.finding_blob(checker))

    def test_changed_metadata_refuses_without_echoing_digest_or_bytes(self):
        contents = {"a.jpg": synthetic_jpeg(b"a")}
        self._write_binary_set(contents, byte_offsets={"a.jpg": 1})
        checker = self._check_binary_set()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details, "asset metadata does not match manifest")
        self.assertNotIn(hashlib.sha256(contents["a.jpg"]).hexdigest(),
                         self.finding_blob(checker))
        self.assertNotIn(str(len(contents["a.jpg"]) + 1), self.finding_blob(checker))

    def test_manifest_schema_is_strict(self):
        self._write_binary_set({"a.jpg": synthetic_jpeg(b"a")}, manifest_extra=True)
        checker = self._check_binary_set()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details, "manifest schema invalid")

    def test_empty_manifest_and_empty_directory_refuse_closed_set(self):
        root = self.root / self._ROOT
        root.mkdir(parents=True, exist_ok=True)
        (self.root / self._MANIFEST).write_text(
            json.dumps({"manifest_version": "1.0", "assets": []}), encoding="utf-8")
        checker = self._check_binary_set()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details, "manifest assets empty")

        self._write_binary_set({"a.jpg": synthetic_jpeg(b"a")})
        (root / "a.jpg").unlink()
        checker = self._check_binary_set()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details, "asset set does not match manifest")

    def test_text_payload_named_jpeg_refuses_before_digest_match(self):
        payload = b"not a JPEG image"
        self._write_binary_set({"a.jpg": payload})
        checker = self._check_binary_set()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details, "asset media signature invalid")
        self.assertNotIn("not a JPEG image", self.finding_blob(checker))
        self.assertNotIn(hashlib.sha256(payload).hexdigest(), self.finding_blob(checker))

    def test_registered_extension_directory_refuses(self):
        self._write_binary_set({"a.jpg": synthetic_jpeg(b"a")})
        (self.root / self._ROOT / "directory.jpg").mkdir()
        checker = self._check_binary_set()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details,
                         "asset set contains registered-extension directory")

    def test_manifest_provenance_and_review_are_required_and_semantic(self):
        self._write_binary_set({"a.jpg": synthetic_jpeg(b"a")})
        manifest_path = self.root / self._MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"][0]["provenance"]["no_real"] = False
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        checker = self._check_binary_set()
        self.assertEqual(self._set_failures(checker)[0].details,
                         "manifest provenance invalid")

        self._write_binary_set({"a.jpg": synthetic_jpeg(b"a")})
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"][0]["review"]["date"] = "2026-02-30"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        checker = self._check_binary_set()
        self.assertEqual(self._set_failures(checker)[0].details,
                         "manifest review invalid")

        self._write_binary_set({"a.jpg": synthetic_jpeg(b"a")})
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"][0]["review"]["date"] = "20260819"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        checker = self._check_binary_set()
        self.assertEqual(self._set_failures(checker)[0].details,
                         "manifest review invalid")

    def test_fifo_manifest_is_rejected_before_opening(self):
        root = self.root / self._ROOT
        root.mkdir(parents=True, exist_ok=True)
        os.mkfifo(self.root / self._MANIFEST)
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        original_open = checker._open_nofollow_bytes
        with patch.object(checker, "_open_nofollow_bytes", wraps=original_open) as opened:
            checker.check_binary_asset_sets()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details, "manifest is not regular")
        self.assertEqual(opened.call_args_list, [])

    def test_root_component_fstat_failure_closes_new_fd(self):
        self._write_binary_set({"a.jpg": synthetic_jpeg(b"a")})
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        asset_set = checker.policy["public_binary_asset_sets"][0]
        before = self._fd_count()
        with patch.object(preflight_module.os, "fstat", side_effect=OSError("injected")):
            self.assertIsNone(checker._open_binary_root_fd(asset_set))
        self.assertEqual(self._fd_count(), before)

    def test_asset_parent_and_final_fstat_failures_close_new_fds(self):
        self._write_binary_set({
            "a.jpg": synthetic_jpeg(b"a"),
            "nested/b.jpg": synthetic_jpeg(b"b"),
        })
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        asset_set = checker.policy["public_binary_asset_sets"][0]
        root_fd = checker._open_binary_root_fd(asset_set)
        self.assertIsNotNone(root_fd)
        try:
            for rel in ("nested/b.jpg", "a.jpg"):
                with self.subTest(rel=rel):
                    before = self._fd_count()
                    with patch.object(preflight_module.os, "fstat",
                                      side_effect=OSError("injected")):
                        with self.assertRaises(OSError):
                            checker._open_binary_asset_fd(root_fd, rel)
                    self.assertEqual(self._fd_count(), before)
        finally:
            os.close(root_fd)

    def test_asset_final_open_failure_closes_owned_parent_fd(self):
        self._write_binary_set({"nested/a.jpg": synthetic_jpeg(b"a")})
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        asset_set = checker.policy["public_binary_asset_sets"][0]
        root_fd = checker._open_binary_root_fd(asset_set)
        self.assertIsNotNone(root_fd)
        original_open = preflight_module.os.open
        calls = 0

        def fail_second_open(path, flags, *, dir_fd=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected")
            return original_open(path, flags, dir_fd=dir_fd)

        try:
            before = self._fd_count()
            with patch.object(preflight_module.os, "open", side_effect=fail_second_open):
                with self.assertRaises(OSError):
                    checker._open_binary_asset_fd(root_fd, "nested/a.jpg")
            self.assertEqual(self._fd_count(), before)
        finally:
            os.close(root_fd)

    def test_nested_traversal_fstat_failure_is_one_value_free_finding(self):
        self._write_binary_set({"nested/a.jpg": synthetic_jpeg(b"a")})
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        asset_set = checker.policy["public_binary_asset_sets"][0]
        root_fd = checker._open_binary_root_fd(asset_set)
        self.assertIsNotNone(root_fd)
        try:
            before = self._fd_count()
            with patch.object(preflight_module.os, "fstat",
                              side_effect=OSError("injected")):
                self.assertIsNone(checker._binary_asset_paths(asset_set, root_fd))
            self.assertEqual(self._fd_count(), before)
            failures = self._set_failures(checker)
            self.assertEqual(len(failures), 1)
            self.assertEqual(
                failures[0].details,
                "asset set traversal changed during verification")
            self.assertNotIn("injected", self.finding_blob(checker))
        finally:
            os.close(root_fd)

    def test_manifest_fdopen_failure_closes_raw_fd(self):
        self._write_binary_set({"a.jpg": synthetic_jpeg(b"a")})
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        asset_set = checker.policy["public_binary_asset_sets"][0]
        root_fd = checker._open_binary_root_fd(asset_set)
        self.assertIsNotNone(root_fd)
        try:
            before = self._fd_count()
            with patch("builtins.open", side_effect=OSError("injected")):
                self.assertIsNone(checker._load_binary_manifest(asset_set, root_fd))
            self.assertEqual(self._fd_count(), before)
        finally:
            os.close(root_fd)

    def test_asset_fdopen_failure_closes_raw_fd_and_root_fd(self):
        self._write_binary_set({"a.jpg": synthetic_jpeg(b"a")})
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        manifest = json.loads((self.root / self._MANIFEST).read_text(encoding="utf-8"))
        before = self._fd_count()
        with patch.object(checker, "_load_binary_manifest", return_value=manifest), \
                patch.object(checker, "_binary_asset_paths", return_value=["a.jpg"]), \
                patch("builtins.open", side_effect=OSError("injected")):
            checker.check_binary_asset_sets()
        self.assertEqual(self._fd_count(), before)

    def test_root_and_manifest_symlinks_refuse_without_opening_assets(self):
        target_root = self.root / "target-root"
        target_root.mkdir()
        (self.root / "fixtures").mkdir()
        (self.root / "fixtures" / "public-binaries").symlink_to(target_root)
        checker = self._check_binary_set()
        self.assertEqual(self._set_failures(checker)[0].details, "asset root is symlink")

        shutil.rmtree(self.root / "fixtures")
        root = self.root / self._ROOT
        root.mkdir(parents=True)
        target_manifest = self.root / "manifest-target.json"
        target_manifest.write_text("{}", encoding="utf-8")
        (self.root / self._MANIFEST).symlink_to(target_manifest)
        checker = self._check_binary_set()
        self.assertEqual(self._set_failures(checker)[0].details, "manifest is symlink")

    def test_binary_symlink_refuses_without_following(self):
        root = self.root / self._ROOT
        root.mkdir(parents=True, exist_ok=True)
        target = self.root / "outside.jpg"
        target.write_bytes(b"outside")
        (root / "linked.jpg").symlink_to(target)
        manifest = {
            "manifest_version": "1.0",
            "assets": [{
                "path": "linked.jpg",
                "sha256": hashlib.sha256(b"outside").hexdigest(),
                "bytes": len(b"outside"),
                "media_type": "image/jpeg",
                "provenance": {
                    "classification": "synthetic",
                    "source": "from_scratch",
                    "from_scratch": True,
                    "no_real": True,
                    "no_derived": True,
                },
                "review": {
                    "attested": True,
                    "reviewer_id": _FIXTURE_APPROVAL_ID,
                    "date": _FIXTURE_APPROVAL_DATE,
                },
            }],
        }
        (self.root / self._MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        checker = self._check_binary_set()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details, "asset set contains symlink")

    def test_policy_rejects_unsafe_or_ambiguous_binary_set_schema(self):
        cases = [
            {"extensions": [".JPG"]},
            {"extensions": [".jpg", ".jpg"]},
            {"manifest": "fixtures/assets.json"},
            {"unexpected": True},
        ]
        for mutation in cases:
            with self.subTest(mutation=mutation):
                policy = json.loads(json.dumps(_POLICY))
                asset_set = policy["public_binary_asset_sets"][0]
                asset_set.update(mutation)
                path = self.root / "bad-policy.json"
                path.write_text(json.dumps(policy), encoding="utf-8")
                with self.assertRaises(PolicyError):
                    PublicCutPreflight(self.root, path)

    def test_root_swap_to_outside_symlink_fails_without_asset_read(self):
        """A held root FD never follows a replacement visible root."""
        self._write_binary_set({"a.jpg": synthetic_jpeg(b"inside")})
        outside = self.root / "outside-root"
        outside.mkdir()
        (outside / "assets.json").write_text("not the trusted manifest",
                                              encoding="utf-8")
        (outside / "a.jpg").write_bytes(synthetic_jpeg(b"outside"))
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        original_load = checker._load_binary_manifest
        original_asset_open = checker._open_binary_asset_fd

        def swap_then_load(asset_set, root_fd):
            visible_root = self.root / self._ROOT
            parked_root = self.root / "parked-binary-root"
            visible_root.rename(parked_root)
            visible_root.symlink_to(outside, target_is_directory=True)
            return original_load(asset_set, root_fd)

        with patch.object(checker, "_load_binary_manifest",
                          side_effect=swap_then_load), patch.object(
                              checker, "_open_binary_asset_fd",
                              wraps=original_asset_open) as opened_assets:
            checker.check_binary_asset_sets()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details,
                         "asset root changed during verification")
        self.assertEqual(opened_assets.call_args_list, [])

    def test_nested_parent_swap_to_outside_symlink_fails_closed(self):
        """Every nested parent is reopened relative to the trusted root FD."""
        inside = synthetic_jpeg(b"same bytes")
        self._write_binary_set({"nested/a.jpg": inside})
        outside = self.root / "outside-parent"
        outside.mkdir()
        (outside / "a.jpg").write_bytes(inside)
        checker = PublicCutPreflight(self.root, self._binary_policy_path())
        original_asset_open = checker._open_binary_asset_fd
        swapped = False

        def swap_then_open(root_fd, rel):
            nonlocal swapped
            if not swapped:
                nested = self.root / self._ROOT / "nested"
                nested.rename(self.root / self._ROOT / "parked-nested")
                nested.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_asset_open(root_fd, rel)

        with patch.object(checker, "_open_binary_asset_fd",
                          side_effect=swap_then_open):
            checker.check_binary_asset_sets()
        failures = self._set_failures(checker)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].details,
                         "asset traversal changed during verification")

    def test_current_example_corpus_passes_only_through_its_closed_manifest(self):
        """The reviewed synthetic corpus uses only descriptor-relative asset opens."""
        checker = PublicCutPreflight(REPO_ROOT, SCRIPTS_DIR / "public_cut_policy.json")
        original_asset_open = checker._open_binary_asset_fd
        original_manifest_load = checker._load_binary_manifest
        with patch.object(checker, "_open_binary_asset_fd",
                          wraps=original_asset_open) as opened, patch.object(
                              checker, "_load_binary_manifest",
                              wraps=original_manifest_load) as loaded:
            checker.check_binary_asset_sets()
        findings = [f for f in checker.findings if f.check_type == "binary_asset_set"
                    and f.id == "example-town-referral-images"]
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].passed)
        expected = {
            asset["path"] for asset in json.loads(
                (REPO_ROOT / "packs/example-town/ordenes-demo/assets.json")
                .read_text(encoding="utf-8"))["assets"]
        }
        self.assertEqual({call.args[1] for call in opened.call_args_list}, expected)
        self.assertEqual(len(loaded.call_args_list), 1)
        self.assertIsInstance(loaded.call_args_list[0].args[1], int)


if __name__ == "__main__":
    unittest.main()
