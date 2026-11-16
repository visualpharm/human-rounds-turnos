#!/usr/bin/env python3
"""
Tier 7 public-cut preflight checker.

Deterministically verifies that a candidate tree is safe for public publication.
The check is:

  - deterministic: single pass, sorted output, no dict-order or filesystem-order
                   dependence, no randomness, no wall-clock dependence.
  - value-free:    findings never include a matched secret value, file content,
                   a remote URL, or an arbitrary object id. Forbidden-path
                   findings report a path NAME only; secret findings report the
                   marker class and line number only.
  - fail-closed:   anything the tool cannot verify safe is treated as unsafe.
                   Forbidden private/control-plane paths are rejected by NAME
                   before any file beneath them is opened, and the content scan
                   is never reached while a forbidden path is present.
                   Required-artifact structure (sections, stub markers,
                   approval-record form) is checked only after that gate;
                   institutional approval substance stays external
                   (passed=None).

It performs NO network access, reads NO secrets, and performs NO deletions.

Exit codes: 0 = all local checks pass, 1 = policy violation found,
            2 = usage / environment error (bad args, unreadable policy).
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class PolicyError(Exception):
    """Raised when the policy file is missing, unreadable, or malformed."""


class PlatformError(Exception):
    """Raised when the platform lacks required capabilities (O_NOFOLLOW)."""


# Policy schema v2.3.0-tier7. The version prefix, approval-record prefix, and
# opaque id alphabet are pinned here so a policy file cannot loosen them.
POLICY_VERSION_PREFIX = "2.3.0-tier7"
APPROVAL_RECORD_PREFIX = "approval:"
APPROVAL_ID_RE = re.compile(r"^hr-[0-9a-f]{16}$")
VALID_RECORD_LINE_RE = re.compile(
    r"^[ \t]*record:[ \t]*approval:(hr-[0-9a-f]{16})[ \t]*$")
DATE_LINE_RE = re.compile(
    r"^[ \t]*date:[ \t]*([0-9]{4}-[0-9]{2}-[0-9]{2})[ \t]*$")
H2_RE = re.compile(r"^##\s+(.*?)\s*#*\s*$")

_TOP_LEVEL_ALLOWED = frozenset({
    "policy_version", "policy_name", "_comment",
    "required_public_artifacts", "forbidden_paths",
    "secret_marker_classes", "private_marker_classes", "external_evidence",
    "prune_dirs", "text_scan_extensions", "text_scan_extensionless",
    "max_scan_bytes", "public_binary_asset_sets",
})
_FORBIDDEN_RULE_ALLOWED = frozenset({"id", "match", "pattern", "description", "allow"})
_PRIVATE_CLASS_ALLOWED = frozenset({
    "id", "format", "description", "patterns", "salt", "hashes", "hashes_file",
    "exempt_prefixes", "network_prefixes_v4", "network_prefixes_v6",
})
# Shared address space (RFC 6598) is not "global", yet a Tailscale node's
# address lives there: a public tree has no reason to carry one.
_SHARED_V4 = (0x64400000, 10)  # 100.64/10 as a number: the source carries no address
_PRIVATE_FORMATS = frozenset({"regex", "token-sha256", "public-ip"})
# Private-marker hashing: sha256(salt + normalised token). Tokens are runs of
# [a-z0-9] after NFKD accent stripping and casefolding; adjacent pairs are
# also hashed as "a b", so a two-word name can be listed without its parts.
_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Not after a letter or "/" ("Chrome/131.0.0.0", "v1.2.3.4" are versions).
_IPV4_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Za-z._-])(?<![A-Za-z]/)[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![0-9]|\.[0-9])")
# Not followed by "." (the "::ffff:" head of a v4-mapped address is checked as v4).
_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Za-z:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![0-9A-Za-z:.])")


def _normalise_for_tokens(text: str) -> str:
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def private_token_digest(salt: str, token: str) -> str:
    """Digest a policy lists for one private token (a name, an id, a network).

    Public so a maintainer adds an entry without the value entering the tree:
    ``python3 -c 'import public_cut_preflight as p; print(p.private_token_digest(salt, value))'``
    with the value typed at the prompt, never committed."""
    return hashlib.sha256((salt + token).encode("utf-8")).hexdigest()
_ARTIFACT_ALLOWED = frozenset({
    "path", "min_bytes", "required_sections",
    "placeholder_markers", "approval_record",
})
_APPROVAL_ALLOWED = frozenset({"required", "record_prefix"})
_BINARY_SET_ALLOWED = frozenset({"id", "root", "manifest", "extensions"})
_BINARY_MANIFEST_ALLOWED = frozenset({"manifest_version", "assets"})
_BINARY_ASSET_ALLOWED = frozenset({
    "path", "sha256", "bytes", "media_type", "provenance", "review",
})
_BINARY_PROVENANCE_ALLOWED = frozenset({
    "classification", "source", "from_scratch", "no_real", "no_derived",
})
_BINARY_REVIEW_ALLOWED = frozenset({"attested", "reviewer_id", "date"})
BINARY_MANIFEST_VERSION = "1.0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BINARY_EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,10}$")
_BINARY_MEDIA_TYPE_BY_EXTENSION = {".jpg": "image/jpeg"}
ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_BINARY_FILE_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)


def _word_bounded_marker_re(marker: str) -> "re.Pattern[str]":
    """Case-insensitive, word-bounded regex for one policy marker string."""
    escaped = re.escape(marker)
    first = marker[0]
    last = marker[-1]
    if first.isalnum() or first == "_":
        escaped = r"(?<!\w)" + escaped
    if last.isalnum() or last == "_":
        escaped = escaped + r"(?!\w)"
    return re.compile(escaped, re.IGNORECASE)


def _check_onofollow_support() -> int:
    """Check O_NOFOLLOW support and return the flag value.

    Raises PlatformError if O_NOFOLLOW is missing or zero.
    This check is deferred to runtime, not module import.
    """
    if not hasattr(os, 'O_NOFOLLOW'):
        raise PlatformError("O_NOFOLLOW flag not available on this platform; cannot safely verify symlinks")
    value = os.O_NOFOLLOW
    if not value:
        raise PlatformError("O_NOFOLLOW flag is zero on this platform; cannot safely verify symlinks")
    return value


class PreflightCheck:
    """A single policy finding.

    ``passed`` is True/False for locally verifiable checks and ``None`` for
    external-evidence findings, which are reported as informational and excluded
    from the pass/fail gate (the tool cannot verify them either).
    """

    def __init__(self, check_type: str, id: str, description: str,
                 passed: Optional[bool], details: str = "", path: str = ""):
        self.check_type = check_type
        self.id = id
        self.description = description
        self.passed = passed
        self.details = details
        self.path = path

    def __eq__(self, other):
        if not isinstance(other, PreflightCheck):
            return NotImplemented
        return (self.check_type == other.check_type and
                self.id == other.id and
                self.description == other.description and
                self.passed == other.passed and
                self.details == other.details and
                self.path == other.path)

    def __hash__(self):
        return hash((self.check_type, self.id, self.description,
                     self.passed, self.details, self.path))

    def __repr__(self):
        return f"PreflightCheck({self.check_type}, {self.id}, {self.path})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.check_type,
            "id": self.id,
            "description": self.description,
            "passed": self.passed,
            "details": self.details,
            "path": self.path,
        }



class PublicCutPreflight:
    """Public cut preflight checker."""

    def __init__(self, root: Path, policy_path: Path):
        self.root = root.resolve()
        self.original_root = root  # Keep the original path to detect symlink roots
        self.policy_path = policy_path
        self.policy = self._load_policy()
        self._forbidden = self.policy.get("forbidden_paths", [])
        self._secret_markers: List[Tuple[str, "re.Pattern"]] = self._compile_markers()
        self._private_markers: List[Dict[str, Any]] = self._compile_private_markers()
        self.findings: List[PreflightCheck] = []
        self._ambient_git_overrides = self._detect_ambient_git_overrides()
        self._walk_errors: List[str] = []  # Collect os.walk errors for fail-closed behavior
        self._onofollow = _check_onofollow_support()  # Check platform capability at init time

    # ------------------------------------------------------------------ policy

    def _load_policy(self) -> Dict[str, Any]:
        try:
            with open(self.policy_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise PolicyError(f"cannot load policy {self.policy_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise PolicyError("policy root must be a JSON object")
        for key in ("required_public_artifacts", "forbidden_paths"):
            if not isinstance(data.get(key), list):
                raise PolicyError(f"policy missing list field: {key}")
        self._validate_policy(data)
        return data

    def _validate_policy(self, data: Dict[str, Any]) -> None:
        """Validate policy structure: unique IDs, valid regexes, no path traversal,
        strict keys, typed artifact fields, binary-set declarations, and the
        pinned 2.3.0-tier7 version."""
        version = data.get("policy_version")
        if not isinstance(version, str) or not version.startswith(POLICY_VERSION_PREFIX):
            raise PolicyError(
                "policy_version must be a string starting with 2.3.0-tier7")

        unknown_top = sorted(set(data) - _TOP_LEVEL_ALLOWED)
        if unknown_top:
            raise PolicyError(f"unknown policy key: {unknown_top[0]}")

        seen_ids: set = set()

        # Validate forbidden_paths
        for rule in data.get("forbidden_paths", []):
            rule_id = rule.get("id")
            if not rule_id or not isinstance(rule_id, str):
                raise PolicyError(f"forbidden path rule missing valid id: {rule}")
            if rule_id in seen_ids:
                raise PolicyError(f"duplicate forbidden path id: {rule_id}")
            seen_ids.add(rule_id)

            match = rule.get("match")
            pattern = rule.get("pattern", "")
            if match == "regex":
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise PolicyError(f"invalid forbidden path regex {pattern!r}: {exc}")
            elif match not in ("prefix", "name"):
                raise PolicyError(f"invalid forbidden path match type: {match}")
            unknown_rule = sorted(set(rule) - _FORBIDDEN_RULE_ALLOWED)
            if unknown_rule:
                raise PolicyError(f"unknown forbidden path key: {unknown_rule[0]}")
            if "allow" in rule:
                allow = rule["allow"]
                if (match != "prefix" or not isinstance(allow, list) or
                        any(not isinstance(item, str) or not item for item in allow)):
                    raise PolicyError(
                        "forbidden path allow must be a list of paths on a prefix rule")
                for item in allow:
                    self._validate_relative_path(item, "forbidden path allow entry")
                    if not (item == pattern or item.startswith(pattern + "/")):
                        raise PolicyError("forbidden path allow entry outside its prefix")

        # Validate required_public_artifacts paths and structural fields
        for art in data.get("required_public_artifacts", []):
            if not isinstance(art, dict):
                raise PolicyError("required_public_artifacts entries must be objects")
            unknown_art = sorted(set(art) - _ARTIFACT_ALLOWED)
            if unknown_art:
                raise PolicyError(f"unknown artifact key: {unknown_art[0]}")
            path = art.get("path", "")
            if path:
                self._validate_relative_path(path, "required_public_artifact path")
            if "min_bytes" in art:
                min_bytes = art["min_bytes"]
                if isinstance(min_bytes, bool) or not isinstance(min_bytes, int) or min_bytes < 0:
                    raise PolicyError("min_bytes must be a non-negative integer")
            if "required_sections" in art:
                sections = art["required_sections"]
                if (not isinstance(sections, list) or
                        any(not isinstance(item, str) or not item or "\n" in item
                            for item in sections)):
                    raise PolicyError(
                        "required_sections must be a list of non-empty single-line strings")
            if "placeholder_markers" in art:
                markers = art["placeholder_markers"]
                if (not isinstance(markers, list) or
                        any(not isinstance(item, str) or not item for item in markers)):
                    raise PolicyError(
                        "placeholder_markers must be a list of non-empty strings")
            if "approval_record" in art:
                record = art["approval_record"]
                if not isinstance(record, dict):
                    raise PolicyError("approval_record must be an object")
                unknown_rec = sorted(set(record) - _APPROVAL_ALLOWED)
                if unknown_rec:
                    raise PolicyError(f"unknown approval_record key: {unknown_rec[0]}")
                if "required" not in record or not isinstance(record["required"], bool):
                    raise PolicyError("approval_record.required must be a boolean")
                if "record_prefix" in record and record["record_prefix"] != APPROVAL_RECORD_PREFIX:
                    raise PolicyError("record_prefix must be exactly 'approval:'")

        # Binary asset sets are intentionally opt-in per policy. A declared
        # set names an exact file family and an adjacent manifest.
        binary_sets = data.get("public_binary_asset_sets", [])
        if not isinstance(binary_sets, list):
            raise PolicyError("public_binary_asset_sets must be a list")
        for asset_set in binary_sets:
            if not isinstance(asset_set, dict):
                raise PolicyError("public_binary_asset_sets entries must be objects")
            unknown_set = sorted(set(asset_set) - _BINARY_SET_ALLOWED)
            if unknown_set:
                raise PolicyError(f"unknown binary asset-set key: {unknown_set[0]}")
            set_id = asset_set.get("id")
            if not isinstance(set_id, str) or not set_id:
                raise PolicyError("binary asset-set missing valid id")
            if set_id in seen_ids:
                raise PolicyError(f"duplicate binary asset-set id: {set_id}")
            seen_ids.add(set_id)
            root = asset_set.get("root")
            manifest = asset_set.get("manifest")
            if not isinstance(root, str) or not root:
                raise PolicyError("binary asset-set root must be a non-empty string")
            if not isinstance(manifest, str) or not manifest:
                raise PolicyError("binary asset-set manifest must be a non-empty string")
            self._validate_relative_path(root, "binary asset-set root")
            self._validate_relative_path(manifest, "binary asset-set manifest")
            if Path(manifest).parent != Path(root):
                raise PolicyError("binary asset-set manifest must be directly inside root")
            extensions = asset_set.get("extensions")
            if (not isinstance(extensions, list) or not extensions or
                    any(not isinstance(ext, str) or not BINARY_EXTENSION_RE.fullmatch(ext)
                        for ext in extensions)):
                raise PolicyError(
                    "binary asset-set extensions must be a non-empty list of lowercase extensions")
            if len(set(extensions)) != len(extensions):
                raise PolicyError("binary asset-set extensions must be unique")
            if any(ext not in _BINARY_MEDIA_TYPE_BY_EXTENSION for ext in extensions):
                raise PolicyError("binary asset-set extension has no strict media contract")

        # Validate text_scan_extensionless entries are safe relative paths
        for name in data.get("text_scan_extensionless", []):
            self._validate_relative_path(name, "text_scan_extensionless entry")

        # Validate secret_marker_classes
        for cls in data.get("secret_marker_classes", []):
            cls_id = cls.get("id")
            if not cls_id or not isinstance(cls_id, str):
                raise PolicyError(f"secret marker class missing valid id: {cls}")
            if cls_id in seen_ids:
                raise PolicyError(f"duplicate secret marker class id: {cls_id}")
            seen_ids.add(cls_id)

        # Validate private_marker_classes (people, hosts, infrastructure ids)
        private = data.get("private_marker_classes", [])
        if not isinstance(private, list):
            raise PolicyError("private_marker_classes must be a list")
        for cls in private:
            if not isinstance(cls, dict):
                raise PolicyError("private_marker_classes entries must be objects")
            unknown_cls = sorted(set(cls) - _PRIVATE_CLASS_ALLOWED)
            if unknown_cls:
                raise PolicyError(f"unknown private marker key: {unknown_cls[0]}")
            cls_id = cls.get("id")
            if not cls_id or not isinstance(cls_id, str):
                raise PolicyError("private marker class missing valid id")
            if cls_id in seen_ids:
                raise PolicyError(f"duplicate private marker class id: {cls_id}")
            seen_ids.add(cls_id)
            fmt = cls.get("format")
            if fmt not in _PRIVATE_FORMATS:
                raise PolicyError(f"invalid private marker format: {fmt}")
            for item in cls.get("exempt_prefixes", []):
                if not isinstance(item, str) or not item:
                    raise PolicyError("exempt_prefixes must be non-empty strings")
                self._validate_relative_path(item, "exempt_prefixes entry")
            if fmt == "regex":
                patterns = cls.get("patterns")
                if not isinstance(patterns, list) or not patterns:
                    raise PolicyError(f"private marker class {cls_id} needs patterns")
                for pat in patterns:
                    try:
                        re.compile(pat)
                    except (re.error, TypeError) as exc:
                        raise PolicyError(
                            f"bad private marker regex in class {cls_id}: {exc}") from exc
            hashes = cls.get("hashes", [])
            hashes_file = cls.get("hashes_file")
            if hashes_file is not None:
                if not isinstance(hashes_file, str) or not hashes_file:
                    raise PolicyError("hashes_file must be a non-empty string")
                self._validate_relative_path(hashes_file, "hashes_file")
            if fmt == "token-sha256" and not hashes and hashes_file is None:
                raise PolicyError(f"private marker class {cls_id} needs hashes")
            if (not isinstance(hashes, list) or
                    any(not isinstance(h, str) or not SHA256_RE.fullmatch(h) for h in hashes)):
                raise PolicyError("private marker hashes must be lowercase sha256 hex")
            if ((hashes or hashes_file is not None) and
                    not (isinstance(cls.get("salt"), str) and cls["salt"])):
                raise PolicyError(f"private marker class {cls_id} needs a salt")
            for key, top in (("network_prefixes_v4", 32), ("network_prefixes_v6", 128)):
                prefixes = cls.get(key, [])
                if (not isinstance(prefixes, list) or
                        any(isinstance(p, bool) or not isinstance(p, int) or not 0 < p <= top
                            for p in prefixes)):
                    raise PolicyError(f"{key} must list prefix lengths 1..{top}")

        # Validate external_evidence
        for ev in data.get("external_evidence", []):
            ev_id = ev.get("id")
            if not ev_id or not isinstance(ev_id, str):
                raise PolicyError(f"external evidence missing valid id: {ev}")
            if ev_id in seen_ids:
                raise PolicyError(f"duplicate external evidence id: {ev_id}")
            seen_ids.add(ev_id)

    def _compile_markers(self) -> List[Tuple[str, "re.Pattern"]]:
        compiled: List[Tuple[str, "re.Pattern"]] = []
        for cls in self.policy.get("secret_marker_classes", []):
            cls_id = cls.get("id", "unknown")
            fmt = cls.get("format", "substring")
            for pat in cls.get("patterns", []):
                try:
                    if fmt == "regex":
                        compiled.append((cls_id, re.compile(pat)))
                    else:
                        compiled.append((cls_id, re.compile(re.escape(pat))))
                except re.error as exc:
                    raise PolicyError(
                        f"bad secret marker regex {pat!r} in class {cls_id}: {exc}") from exc
        return compiled

    def _private_digests(self, cls: Dict[str, Any]) -> List[str]:
        """Digests a class keeps in its ``hashes_file`` (a JSON object of
        class id -> digests, beside the policy). The file stays in the private
        working repository: a salted digest of a short name is reversible by a
        dictionary, so it never ships in the cut. A missing file is reported as
        external evidence (``self.private_digests_missing``); a malformed one
        is a policy error."""
        rel = cls.get("hashes_file")
        if rel is None:
            return []
        path = Path(self.policy_path).parent / rel
        if not path.is_file():
            self.private_digests_missing.append(cls["id"])
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PolicyError(f"cannot load private digests: {exc}") from exc
        digests = data.get(cls["id"], []) if isinstance(data, dict) else None
        if (not isinstance(digests, list) or
                any(not isinstance(h, str) or not SHA256_RE.fullmatch(h) for h in digests)):
            raise PolicyError("private digests must map class ids to lowercase sha256 hex")
        return digests

    def _compile_private_markers(self) -> List[Dict[str, Any]]:
        compiled: List[Dict[str, Any]] = []
        self.private_digests_missing: List[str] = []
        for cls in self.policy.get("private_marker_classes", []):
            compiled.append({
                "id": cls["id"],
                "format": cls["format"],
                "patterns": [re.compile(p) for p in cls.get("patterns", [])],
                "salt": cls.get("salt", ""),
                "hashes": frozenset(cls.get("hashes", [])) | frozenset(self._private_digests(cls)),
                "exempt": tuple(p.rstrip("/") for p in cls.get("exempt_prefixes", [])),
                "v4": tuple(cls.get("network_prefixes_v4", [])),
                "v6": tuple(cls.get("network_prefixes_v6", [])),
            })
        return compiled

    def _detect_ambient_git_overrides(self) -> List[str]:
        """Detect ambient GIT_* environment variable overrides."""
        # Standard git vars that are allowed (affect operation but not repository pathing)
        # GIT_CONFIG_COUNT and GIT_CONFIG_KEY_* are NOT allowed (injection vector)
        allowed_git_vars = {
            "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_EDITOR", "GIT_PAGER", "GIT_TERMINAL_PROMPT",
            # GIT_ASKPASS only names the helper that PROMPTS for credentials on a
            # network operation: it cannot change repository pathing, config or
            # content, the preflight performs no network git operation (it proves
            # there are no remotes), and the strictly more powerful
            # GIT_SSH_COMMAND is already allowed. Editors and terminals export it
            # by default, so flagging it false-fails an otherwise clean cut.
            "GIT_ASKPASS",
            "GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSL_NO_VERIFY",
            "GIT_DIFF_OPTS", "GIT_EXTERNAL_DIFF", "GIT_PAGER",
        }
        # Allow patterns (wildcard suffixes)
        overrides = []
        for key in os.environ:
            if key.startswith("GIT_"):
                # Check if this key or its pattern is allowed
                allowed = False
                for allowed_var in allowed_git_vars:
                    if allowed_var.endswith("*"):
                        if key.startswith(allowed_var[:-1]):
                            allowed = True
                            break
                    elif key == allowed_var:
                        allowed = True
                        break
                if not allowed:
                    overrides.append(key)
        return sorted(overrides)

    # ----------------------------------------------------------------- helpers

    def _add(self, check_type: str, id: str, description: str,
             passed: Optional[bool], details: str = "", path: str = ""):
        self.findings.append(PreflightCheck(
            check_type=check_type, id=id, description=description,
            passed=passed, details=details, path=path))

    def _rel_posix(self, abs_path: Path) -> str:
        rel = os.path.relpath(abs_path, self.root)
        return Path(rel).as_posix()

    def _scrubbed_env(self) -> Dict[str, str]:
        """Return environment with all GIT_* variables removed."""
        return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def _validate_relative_path(self, path_str: str, field_name: str) -> None:
        """Validate a policy-controlled path is a safe relative path with no traversal."""
        if not path_str:
            return
        path_obj = Path(path_str)
        if path_obj.is_absolute():
            raise PolicyError(f"{field_name} must be relative, not absolute: {path_str}")
        if ".." in path_obj.parts:
            raise PolicyError(f"{field_name} must not contain '..' traversal: {path_str}")

    def _git(self, args: List[str]) -> Tuple[bool, str]:
        """Run git in self.root with scrubbed environment (no GIT_* vars).
        Returns (ok, stdout-or-error). Fail-closed."""
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(self.root),
                env=self._scrubbed_env(),
                capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return (False, f"git unavailable: {exc}")
        if proc.returncode != 0:
            return (False, (proc.stderr or proc.stdout).strip() or "git failed")
        return (True, proc.stdout.strip())

    def _rule_matches(self, rel_posix: str) -> Optional[str]:
        """Return the id of the first forbidden rule matching a relative path."""
        parts = rel_posix.split("/")
        for rule in self._forbidden:
            mode = rule.get("match")
            pattern = rule.get("pattern", "")
            if mode == "prefix":
                if rel_posix == pattern or rel_posix.startswith(pattern + "/"):
                    # An allow entry admits that file or directory, and the
                    # directories leading to it so the walk can reach it.
                    if any(rel_posix == item or rel_posix.startswith(item + "/")
                           or item.startswith(rel_posix + "/")
                           for item in rule.get("allow", [])):
                        continue
                    return rule.get("id")
            elif mode == "name":
                if pattern in parts:
                    return rule.get("id")
            elif mode == "regex":
                if re.search(pattern, rel_posix):
                    return rule.get("id")
        return None

    def _is_symlink(self, path: Path) -> Optional[bool]:
        """Check if a path is a symlink without dereferencing it.
        Returns True/False if check succeeded, None if inspection failed."""
        try:
            return path.is_symlink()
        except OSError as exc:
            # Inspection error - record as finding, don't silently return False
            rel = self._rel_posix(path)
            self._add(
                "forbidden_path", "inspection-error",
                "symlink inspection error",
                passed=False,
                details=f"cannot inspect path type: {exc}",
                path=rel)
            return None  # Signal that inspection failed

    def _open_nofollow(self, path: Path, mode: str = "r"):
        """Open a file without following symlinks. Requires O_NOFOLLOW and read mode."""
        if not self._onofollow or mode != "r":
            # No fallback - fail closed
            raise OSError(f"Cannot safely open {path} without O_NOFOLLOW")
        fd = os.open(path, os.O_RDONLY | self._onofollow)
        try:
            return open(fd, mode=mode, encoding="utf-8", closefd=True)
        except:
            os.close(fd)
            raise

    def _open_nofollow_bytes(self, path: Path):
        """Open a file as bytes without following symlinks. Requires O_NOFOLLOW."""
        if not self._onofollow:
            raise OSError(f"Cannot safely open {path} without O_NOFOLLOW")
        fd = os.open(path, os.O_RDONLY | self._onofollow)
        try:
            return open(fd, mode="rb", closefd=True)
        except:
            os.close(fd)
            raise

    # --------------------------------------------------------------- phase 0

    def check_ambient_environment(self) -> None:
        """Check for ambient Git environment variable overrides that would affect
        git operations in unpredictable ways."""
        if self._ambient_git_overrides:
            self._add(
                "ambient_environment", "git-overrides",
                "no ambient Git environment variable overrides",
                passed=False,
                details=f"overrides present: {', '.join(self._ambient_git_overrides)}")

    # --------------------------------------------------------------- phase 1

    def _walk_onerror(self, exc: OSError) -> None:
        """Collect os.walk errors for fail-closed behavior."""
        self._walk_errors.append(str(exc))

    def check_forbidden_paths(self) -> None:
        """Reject forbidden private/control-plane paths by NAME, before opening
        anything beneath them. Walks names only; prunes any matched directory so
        its children are never enumerated, and never reads file content.

        This is the ONLY phase that produces output when it fails; all other
        phases are skipped."""
        # Check if root itself is a symlink BEFORE starting the walk
        # (os.walk resolves symlinks, so we need to check beforehand)
        root_is_symlink = self._is_symlink(self.original_root)
        if root_is_symlink is True:
            self._add(
                "forbidden_path", "symlink-root",
                "forbidden path (root is a symlink)",
                passed=False,
                details=f"root path is a symlink (contents not read)",
                path=".")
            return
        # If inspection failed, already recorded - still proceed to scan

        prune = set(self.policy.get("prune_dirs", []))
        self._walk_errors.clear()
        for dirpath, dirnames, filenames in os.walk(self.root, onerror=self._walk_onerror):
            dirnames.sort()

            # Phase 1: Inspect ALL entries (dirs and files) for symlinks/inspection errors
            # BEFORE any prune filtering. This ensures prune-named symlinks are detected.
            all_entries = sorted(list(dirnames) + list(filenames))
            verified_real_dirs = set()  # Only directories verified as real (not symlinks, inspectable)

            for name in all_entries:
                path = Path(dirpath, name)
                rel = self._rel_posix(path)
                is_dir = name in dirnames

                # Reject all symlinks without following them
                is_link = self._is_symlink(path)
                if is_link is True:
                    self._add(
                        "forbidden_path", "symlink",
                        "forbidden path (symlink)",
                        passed=False,
                        details=f"path is a symlink (target not followed): {name}",
                        path=rel)
                    continue
                # If is_link is None, inspection error already recorded; skip further checks
                if is_link is None:
                    continue

                # Track verified real directories for later prune filtering
                if is_dir:
                    verified_real_dirs.add(name)

                # Check against forbidden path rules
                rule_id = self._rule_matches(rel)
                if rule_id is not None:
                    self._add(
                        "forbidden_path", rule_id,
                        "forbidden private/control-plane path",
                        passed=False,
                        details=f"path name matches rule '{rule_id}' (contents not read)",
                        path=rel)

            # Phase 2: Apply prune filtering ONLY to verified real directories
            # Pruned dirs are never descended into (no content read)
            pruned_dirs = verified_real_dirs & prune
            dirnames[:] = [d for d in dirnames if d in verified_real_dirs and d not in pruned_dirs]

            # Phase 3: Also prevent descent into directories matching forbidden rules
            # (this is redundant with Phase 1 for reporting but prevents os.walk from descending)
            matched_dirs = {
                name for name in dirnames
                if self._rule_matches(self._rel_posix(Path(dirpath, name))) is not None}
            dirnames[:] = [d for d in dirnames if d not in matched_dirs]

        # Report any traversal errors that occurred
        if self._walk_errors:
            for err in self._walk_errors:
                self._add(
                    "forbidden_path", "scandir-error",
                    "filesystem traversal error",
                    passed=False,
                    details=f"walk error: {err}",
                    path=".")

    # --------------------------------------------------------------- phase 2

    def check_required_artifacts(self) -> None:
        """Required publication artifacts must exist, be regular files (not
        symlinks), meet the size floor, and satisfy the per-artifact structural
        contract. Opens once with O_NOFOLLOW, then fstats the same descriptor."""
        max_scan = int(self.policy.get("max_scan_bytes", 1 << 20))
        for art in self.policy.get("required_public_artifacts", []):
            rel = art.get("path", "")
            min_bytes = int(art.get("min_bytes", 1))
            path = self.root / rel

            try:
                with self._open_nofollow_bytes(path) as fh:
                    st = os.fstat(fh.fileno())
                    if not stat.S_ISREG(st.st_mode):
                        self._add("required_artifact", rel,
                                  "required publication artifact",
                                  passed=False, details="not a regular file", path=rel)
                        continue
                    size = st.st_size
                    if size == 0:
                        self._add("required_artifact", rel,
                                  "required publication artifact",
                                  passed=False, details="empty", path=rel)
                        continue
                    if size < min_bytes:
                        self._add("required_artifact", rel,
                                  "required publication artifact",
                                  passed=False,
                                  details=f"below floor (<{min_bytes} bytes required)",
                                  path=rel)
                        continue
                    if size > max_scan:
                        self._add("required_artifact", rel,
                                  "required publication artifact",
                                  passed=False, details="oversized", path=rel)
                        continue
                    raw = fh.read(max_scan + 1)
                    if len(raw) > max_scan:
                        self._add("required_artifact", rel,
                                  "required publication artifact",
                                  passed=False, details="oversized", path=rel)
                        continue
            except FileNotFoundError:
                self._add("required_artifact", rel,
                          "required publication artifact",
                          passed=False, details="missing", path=rel)
                continue
            except OSError:
                self._add("required_artifact", rel,
                          "required publication artifact",
                          passed=False, details="unreadable", path=rel)
                continue

            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                self._add("required_artifact", rel,
                          "required publication artifact",
                          passed=False, details="not valid UTF-8", path=rel)
                continue

            failed = self._check_artifact_structure(rel, art, text)
            if not failed:
                self._add("required_artifact", rel,
                          "required publication artifact",
                          passed=True,
                          details="present and structurally complete", path=rel)

    def _check_artifact_structure(self, rel: str, art: Dict[str, Any], text: str) -> bool:
        """Run section, marker, and approval-form checks. Returns True if any failed."""
        lines = text.splitlines()
        # The literal stub historically used to seed every required artifact
        # must never pass merely because its path is a source file whose real
        # contents legitimately discuss placeholder detection. This is a
        # complete structural failure, so short-circuit before later section,
        # marker, or approval-form checks can add duplicate findings.
        historical_stub = re.compile(
            rf"^[ \t]*#[ \t]*{re.escape(rel)}[ \t]+placeholder[ \t]*$",
            re.IGNORECASE)
        for index, line in enumerate(lines):
            if historical_stub.match(line):
                self._add("required_artifact", rel,
                          "required publication artifact",
                          passed=False,
                          details=f"historical placeholder stub at line {index + 1}",
                          path=rel)
                return True

        failed = False
        headings = []
        for line in lines:
            match = H2_RE.match(line)
            if match:
                headings.append(match.group(1))
        heading_set = set(headings)

        missing_sections = [
            section for section in art.get("required_sections") or []
            if section not in heading_set]
        if missing_sections:
            names = ", ".join(missing_sections)
            self._add("required_artifact", rel,
                      "required publication artifact",
                      passed=False,
                      details=f"missing section(s): {names}",
                      path=rel)
            failed = True

        exempt = [bool(VALID_RECORD_LINE_RE.match(line)) for line in lines]
        for marker in art.get("placeholder_markers") or []:
            rx = _word_bounded_marker_re(marker)
            hit_line = None
            for index, line in enumerate(lines):
                if exempt[index]:
                    continue
                if rx.search(line):
                    hit_line = index + 1
                    break
            if hit_line is not None:
                self._add("required_artifact", rel,
                          "required publication artifact",
                          passed=False,
                          details=f"placeholder marker {marker} at line {hit_line}",
                          path=rel)
                failed = True

        approval = art.get("approval_record") or {}
        if approval.get("required") is True:
            field = self._approval_record_issue(text)
            if field is not None:
                self._add("required_artifact", rel,
                          "required publication artifact",
                          passed=False,
                          details=f"malformed approval record field: {field}",
                          path=rel)
                failed = True
        return failed

    def _approval_record_issue(self, text: str) -> Optional[str]:
        """Return the malformed field name, or None if the form is valid.

        Names the field only — never the offending value.
        """
        bodies: List[Tuple[str, List[str]]] = []
        current: Optional[List[str]] = None
        for line in text.splitlines():
            match = H2_RE.match(line)
            if match:
                current = []
                bodies.append((match.group(1), current))
                continue
            if current is not None:
                current.append(line)

        record_body = None
        for heading, body_lines in bodies:
            if heading == "Approval record":
                record_body = body_lines
                break
        if record_body is None:
            return "section"

        record_ok = False
        date_ok = False
        id_malformed = False
        date_malformed = False
        record_line_seen = False
        date_line_seen = False
        for line in record_body:
            rec_match = VALID_RECORD_LINE_RE.match(line)
            if rec_match:
                record_line_seen = True
                if APPROVAL_ID_RE.match(rec_match.group(1)):
                    record_ok = True
                else:
                    id_malformed = True
                continue
            if re.match(r"^[ \t]*record:", line):
                record_line_seen = True
                id_malformed = True
                continue
            date_match = DATE_LINE_RE.match(line)
            if date_match:
                date_line_seen = True
                try:
                    datetime.date.fromisoformat(date_match.group(1))
                except ValueError:
                    date_malformed = True
                else:
                    date_ok = True
                continue
            if re.match(r"^[ \t]*date:", line):
                date_line_seen = True
                date_malformed = True

        if not record_line_seen:
            return "record"
        if id_malformed and not record_ok:
            return "id"
        if not record_ok:
            return "id"
        if not date_line_seen:
            return "date"
        if date_malformed and not date_ok:
            return "date"
        if not date_ok:
            return "date"
        return None

    # ------------------------------------------------------ binary asset sets

    def _binary_failure(self, asset_set: Dict[str, Any], details: str) -> None:
        """Record one value-free binary-set failure."""
        self._add("binary_asset_set", asset_set["id"],
                  "closed-set public binary assets",
                  passed=False, details=details, path=asset_set["root"])

    def _open_binary_root_fd(self, asset_set: Dict[str, Any]) -> Optional[int]:
        """Open the declared asset root through held, no-follow directory FDs."""
        opened: List[int] = []
        try:
            opened.append(os.open(self.root, _DIRECTORY_OPEN_FLAGS))
            for component in Path(asset_set["root"]).parts:
                next_fd = os.open(component, _DIRECTORY_OPEN_FLAGS,
                                  dir_fd=opened[-1])
                try:
                    next_mode = os.fstat(next_fd).st_mode
                except BaseException:
                    os.close(next_fd)
                    raise
                if not stat.S_ISDIR(next_mode):
                    os.close(next_fd)
                    raise OSError("binary root component is not a directory")
                opened.append(next_fd)
        except OSError:
            for fd in reversed(opened):
                os.close(fd)
            details = "asset root unreadable or replaced"
            try:
                if stat.S_ISLNK(os.lstat(self.root / asset_set["root"]).st_mode):
                    details = "asset root is symlink"
            except OSError:
                pass
            self._binary_failure(asset_set, details)
            return None
        root_fd = opened.pop()
        for fd in reversed(opened):
            os.close(fd)
        return root_fd

    def _binary_root_matches_path(self, asset_set: Dict[str, Any], root_fd: int) -> bool:
        """Detect replacement of the visible asset root while its FD is held."""
        try:
            visible = os.lstat(self.root / asset_set["root"])
            held = os.fstat(root_fd)
        except OSError:
            self._binary_failure(asset_set, "asset root changed during verification")
            return False
        if (stat.S_ISLNK(visible.st_mode) or not stat.S_ISDIR(visible.st_mode) or
                (visible.st_dev, visible.st_ino) != (held.st_dev, held.st_ino)):
            self._binary_failure(asset_set, "asset root changed during verification")
            return False
        return True

    def _open_binary_asset_fd(self, root_fd: int, rel: str) -> int:
        """Open one asset relative to the trusted root, no-follow at every hop."""
        parts = Path(rel).parts
        parent_fd = root_fd
        owned_parent = False
        try:
            for component in parts[:-1]:
                next_fd = os.open(component, _DIRECTORY_OPEN_FLAGS,
                                  dir_fd=parent_fd)
                try:
                    next_mode = os.fstat(next_fd).st_mode
                except BaseException:
                    os.close(next_fd)
                    raise
                if not stat.S_ISDIR(next_mode):
                    os.close(next_fd)
                    raise OSError("asset parent is not a directory")
                previous_owned_parent = parent_fd if owned_parent else None
                parent_fd = next_fd
                owned_parent = True
                if previous_owned_parent is not None:
                    os.close(previous_owned_parent)
            asset_fd = os.open(parts[-1], _BINARY_FILE_OPEN_FLAGS,
                               dir_fd=parent_fd)
            try:
                asset_mode = os.fstat(asset_fd).st_mode
            except BaseException:
                os.close(asset_fd)
                raise
            if not stat.S_ISREG(asset_mode):
                os.close(asset_fd)
                raise OSError("asset is not a regular file")
            return asset_fd
        finally:
            if owned_parent:
                os.close(parent_fd)

    def _load_binary_manifest(self, asset_set: Dict[str, Any],
                              root_fd: int) -> Optional[Dict[str, Any]]:
        """Read a manifest relative to the held asset-root descriptor."""
        manifest_name = Path(asset_set["manifest"]).name
        max_scan = int(self.policy.get("max_scan_bytes", 1 << 20))
        try:
            manifest_stat = os.stat(manifest_name, dir_fd=root_fd,
                                    follow_symlinks=False)
            if stat.S_ISLNK(manifest_stat.st_mode):
                self._binary_failure(asset_set, "manifest is symlink")
                return None
            if not stat.S_ISREG(manifest_stat.st_mode):
                self._binary_failure(asset_set, "manifest is not regular")
                return None
            manifest_fd = os.open(manifest_name, _BINARY_FILE_OPEN_FLAGS,
                                  dir_fd=root_fd)
            try:
                manifest_fh = open(manifest_fd, mode="rb", closefd=True)
            except BaseException:
                os.close(manifest_fd)
                raise
            with manifest_fh as fh:
                st = os.fstat(fh.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_size > max_scan:
                    self._binary_failure(asset_set, "manifest unreadable")
                    return None
                raw = fh.read(max_scan + 1)
        except FileNotFoundError:
            self._binary_failure(asset_set, "manifest missing")
            return None
        except OSError:
            self._binary_failure(asset_set, "manifest unreadable")
            return None
        if len(raw) > max_scan:
            self._binary_failure(asset_set, "manifest unreadable")
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._binary_failure(asset_set, "manifest malformed")
            return None
        if not isinstance(data, dict) or set(data) != _BINARY_MANIFEST_ALLOWED:
            self._binary_failure(asset_set, "manifest schema invalid")
            return None
        if data.get("manifest_version") != BINARY_MANIFEST_VERSION:
            self._binary_failure(asset_set, "manifest version invalid")
            return None
        assets = data.get("assets")
        if not isinstance(assets, list):
            self._binary_failure(asset_set, "manifest schema invalid")
            return None
        if not assets:
            self._binary_failure(asset_set, "manifest assets empty")
            return None
        paths = []
        allowed_extensions = set(asset_set["extensions"])
        for asset in assets:
            if not isinstance(asset, dict) or set(asset) != _BINARY_ASSET_ALLOWED:
                self._binary_failure(asset_set, "manifest schema invalid")
                return None
            rel = asset.get("path")
            digest = asset.get("sha256")
            byte_count = asset.get("bytes")
            media_type = asset.get("media_type")
            provenance = asset.get("provenance")
            review = asset.get("review")
            if (not isinstance(rel, str) or not rel or
                    not isinstance(digest, str) or not SHA256_RE.fullmatch(digest) or
                    isinstance(byte_count, bool) or not isinstance(byte_count, int) or
                    byte_count < 0 or not isinstance(media_type, str) or
                    not isinstance(provenance, dict) or not isinstance(review, dict)):
                self._binary_failure(asset_set, "manifest schema invalid")
                return None
            try:
                self._validate_relative_path(rel, "binary manifest asset path")
            except PolicyError:
                self._binary_failure(asset_set, "manifest schema invalid")
                return None
            suffix = Path(rel).suffix
            if suffix not in allowed_extensions:
                self._binary_failure(asset_set, "manifest schema invalid")
                return None
            if media_type != _BINARY_MEDIA_TYPE_BY_EXTENSION[suffix]:
                self._binary_failure(asset_set, "manifest media type invalid")
                return None
            if set(provenance) != _BINARY_PROVENANCE_ALLOWED:
                self._binary_failure(asset_set, "manifest provenance invalid")
                return None
            if (provenance.get("classification") != "synthetic" or
                    provenance.get("source") != "from_scratch" or
                    provenance.get("from_scratch") is not True or
                    provenance.get("no_real") is not True or
                    provenance.get("no_derived") is not True):
                self._binary_failure(asset_set, "manifest provenance invalid")
                return None
            if set(review) != _BINARY_REVIEW_ALLOWED:
                self._binary_failure(asset_set, "manifest review invalid")
                return None
            review_id = review.get("reviewer_id")
            review_date = review.get("date")
            if (review.get("attested") is not True or not isinstance(review_id, str) or
                    not APPROVAL_ID_RE.fullmatch(review_id) or
                    not isinstance(review_date, str) or
                    not ISO_DATE_RE.fullmatch(review_date)):
                self._binary_failure(asset_set, "manifest review invalid")
                return None
            try:
                datetime.date.fromisoformat(review_date)
            except ValueError:
                self._binary_failure(asset_set, "manifest review invalid")
                return None
            paths.append(rel)
        if paths != sorted(paths) or len(set(paths)) != len(paths):
            self._binary_failure(asset_set, "manifest schema invalid")
            return None
        return data

    def _binary_asset_paths(self, asset_set: Dict[str, Any],
                            root_fd: int) -> Optional[List[str]]:
        """Enumerate assets recursively through held no-follow directory FDs."""
        paths: List[str] = []
        extensions = set(asset_set["extensions"])

        def walk(dir_fd: int, prefix: Tuple[str, ...]) -> bool:
            try:
                with os.scandir(dir_fd) as iterator:
                    names = sorted(entry.name for entry in iterator)
            except OSError:
                self._binary_failure(asset_set, "asset set traversal failed")
                return False
            for name in names:
                try:
                    entry_stat = os.stat(name, dir_fd=dir_fd,
                                         follow_symlinks=False)
                except OSError:
                    self._binary_failure(asset_set, "asset set traversal failed")
                    return False
                mode = entry_stat.st_mode
                suffix = Path(name).suffix
                if stat.S_ISLNK(mode):
                    self._binary_failure(asset_set, "asset set contains symlink")
                    return False
                if stat.S_ISDIR(mode):
                    if suffix in extensions:
                        self._binary_failure(
                            asset_set, "asset set contains registered-extension directory")
                        return False
                    try:
                        child_fd = os.open(name, _DIRECTORY_OPEN_FLAGS,
                                           dir_fd=dir_fd)
                    except OSError:
                        self._binary_failure(asset_set,
                                             "asset set traversal changed during verification")
                        return False
                    try:
                        try:
                            child_mode = os.fstat(child_fd).st_mode
                        except OSError:
                            self._binary_failure(
                                asset_set,
                                "asset set traversal changed during verification")
                            return False
                        if not stat.S_ISDIR(child_mode):
                            self._binary_failure(asset_set,
                                                 "asset set traversal changed during verification")
                            return False
                        if not walk(child_fd, prefix + (name,)):
                            return False
                    finally:
                        os.close(child_fd)
                    continue
                if suffix in extensions:
                    if not stat.S_ISREG(mode):
                        self._binary_failure(asset_set,
                                             "asset set contains non-regular file")
                        return False
                    paths.append(Path(*prefix, name).as_posix())
            return True

        if not walk(root_fd, ()):
            return None
        return sorted(paths)

    def check_binary_asset_sets(self) -> None:
        """Verify each declared public binary asset family as an exact set.

        A missing or malformed manifest refuses the candidate before any binary
        asset is opened. Valid manifests require exact filenames, byte counts,
        and SHA-256 values, but findings never expose any of those values.
        """
        for asset_set in self.policy.get("public_binary_asset_sets", []):
            root_fd = self._open_binary_root_fd(asset_set)
            if root_fd is None:
                continue
            try:
                if not self._binary_root_matches_path(asset_set, root_fd):
                    continue
                manifest = self._load_binary_manifest(asset_set, root_fd)
                if manifest is None:
                    continue
                if not self._binary_root_matches_path(asset_set, root_fd):
                    continue
                actual_paths = self._binary_asset_paths(asset_set, root_fd)
                if actual_paths is None:
                    continue
                expected = {asset["path"]: asset for asset in manifest["assets"]}
                if set(actual_paths) != set(expected):
                    self._binary_failure(asset_set, "asset set does not match manifest")
                    continue
                verified = True
                failure_detail = "asset metadata does not match manifest"
                for rel in actual_paths:
                    if not self._binary_root_matches_path(asset_set, root_fd):
                        verified = False
                        failure_detail = "asset root changed during verification"
                        break
                    try:
                        asset_fd = self._open_binary_asset_fd(root_fd, rel)
                        try:
                            asset_fh = open(asset_fd, mode="rb", closefd=True)
                        except BaseException:
                            os.close(asset_fd)
                            raise
                        with asset_fh as fh:
                            st = os.fstat(fh.fileno())
                            if not stat.S_ISREG(st.st_mode):
                                verified = False
                                break
                            digest = hashlib.sha256()
                            header = fh.read(3)
                            if header != b"\xff\xd8\xff":
                                failure_detail = "asset media signature invalid"
                                verified = False
                                break
                            digest.update(header)
                            byte_count = len(header)
                            while True:
                                chunk = fh.read(64 * 1024)
                                if not chunk:
                                    break
                                digest.update(chunk)
                                byte_count += len(chunk)
                    except OSError:
                        failure_detail = "asset traversal changed during verification"
                        verified = False
                        break
                    expected_asset = expected[rel]
                    if (byte_count != expected_asset["bytes"] or
                            digest.hexdigest() != expected_asset["sha256"]):
                        verified = False
                        break
                if not verified:
                    # _binary_root_matches_path already recorded its own
                    # value-free finding when it detected a root replacement.
                    if not (self.findings and
                            self.findings[-1].check_type == "binary_asset_set" and
                            self.findings[-1].id == asset_set["id"] and
                            self.findings[-1].details == failure_detail):
                        self._binary_failure(asset_set, failure_detail)
                    continue
                if not self._binary_root_matches_path(asset_set, root_fd):
                    continue
                self._add("binary_asset_set", asset_set["id"],
                          "closed-set public binary assets",
                          passed=True, details="manifest and asset set verified",
                          path=asset_set["root"])
            finally:
                os.close(root_fd)

    # --------------------------------------------------------------- phase 3

    def check_secret_markers(self) -> None:
        """Scan text files for credential SHAPES. Only reached when no forbidden
        path is present. Reports class + line only; never the matched value.

        Opens files with O_NOFOLLOW and uses fstat to validate regular file
        and size before reading."""
        prune = set(self.policy.get("prune_dirs", []))
        exts = set(self.policy.get("text_scan_extensions", []))
        extensionless = set(self.policy.get("text_scan_extensionless", []))
        max_bytes = int(self.policy.get("max_scan_bytes", 1 << 20))
        if not self._secret_markers and not self._private_markers:
            return
        self._walk_errors.clear()
        for dirpath, dirnames, filenames in os.walk(self.root, onerror=self._walk_onerror):
            dirnames.sort()

            # Inspect ALL directory entries for symlinks BEFORE any prune filtering
            # This ensures prune-named symlinks are detected and rejected
            verified_real_dirs = set()  # Only directories verified as real (not symlinks)
            for name in dirnames:
                path = Path(dirpath, name)
                # Check if this directory is a symlink
                is_link = self._is_symlink(path)
                if is_link is True:
                    # Symlink found - reject it (even if it's in prune_dirs)
                    rel = self._rel_posix(path)
                    self._add(
                        "secret_scan", rel, "scan health",
                        passed=False, details="directory is a symlink (not scanned)", path=rel)
                    continue
                # If is_link is None, inspection error already recorded; skip this dir
                if is_link is None:
                    continue
                # Track verified real directories for prune filtering
                verified_real_dirs.add(name)

            # Apply prune filtering ONLY to verified real directories
            dirnames[:] = [d for d in dirnames if d in verified_real_dirs and d not in prune]

            for name in sorted(filenames):
                path = Path(dirpath, name)
                rel = self._rel_posix(path)

                # Skip symlinks without following
                is_link = self._is_symlink(path)
                if is_link is True:
                    continue
                # If is_link is None, inspection error already recorded
                if is_link is None:
                    continue

                # Check extension-based or extensionless whitelist
                if Path(name).suffix not in exts and name not in extensionless:
                    continue

                try:
                    with self._open_nofollow(path, "r") as fh:
                        # Use fstat on the open descriptor
                        st = os.fstat(fh.fileno())
                        if not stat.S_ISREG(st.st_mode):
                            self._add("secret_scan", rel, "scan health",
                                      passed=False, details="not a regular file",
                                      path=rel)
                            continue
                        if st.st_size > max_bytes:
                            self._add("secret_scan", rel, "scan health",
                                      passed=False,
                                      details=f"file larger than {max_bytes} bytes, not scanned",
                                      path=rel)
                            continue
                        for lineno, line in enumerate(fh, 1):
                            self._scan_line(rel, lineno, line)
                except UnicodeDecodeError:
                    self._add("secret_scan", rel, "scan health",
                              passed=False, details="non-utf8 text file, not verified",
                              path=rel)
                except OSError as exc:
                    self._add("secret_scan", rel, "scan health",
                              passed=False, details=f"unreadable file: {exc}",
                              path=rel)

        # Report any traversal errors
        if self._walk_errors:
            for err in self._walk_errors:
                self._add("secret_scan", ".", "scan health",
                          passed=False, details=f"traversal error: {err}", path=".")

        # Scan tracked files that may have been skipped due to prune rules
        self._scan_tracked_in_pruned_directories()

    def _git_tracked_files_nul(self) -> Optional[List[str]]:
        """Get tracked files using git ls-files -z (NUL-delimited, raw bytes).

        Returns a list of relative paths, or None on ANY error (command failure,
        decode failure, malformed output, or unsafe path). Shell-free, uses
        scrubbed environment, text=False, and validates each path as safe relative.
        """
        try:
            proc = subprocess.run(
                ["git", "ls-files", "-z"],
                cwd=str(self.root),
                env=self._scrubbed_env(),
                capture_output=True,
                text=False,  # Raw bytes for NUL delimiter
                timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None

        if proc.returncode != 0:
            return None

        # Validate output is bytes
        if not isinstance(proc.stdout, bytes):
            return None

        # Parse NUL-delimited output, validate each path
        try:
            data = proc.stdout
            if not data:
                return []  # Empty repo, no tracked files

            # Non-empty output must end with NUL (git ls-files -z guarantees this)
            if data[-1:] != b"\x00":
                return None

            # Split only data[:-1] on NUL (exclude final NUL), fail on empty interior records
            paths = data[:-1].decode("utf-8").split("\x00")
            if any(p == "" for p in paths):
                return None  # Malformed: empty interior record (consecutive NULs)

            # Validate each path is a safe relative path
            safe_paths = []
            for p in paths:
                # Reject paths with newline or other unsafe characters
                if "\n" in p or "\r" in p:
                    return None
                # Reject absolute paths
                if Path(p).is_absolute():
                    return None
                # Reject traversal
                if ".." in Path(p).parts:
                    return None
                safe_paths.append(p)

            return safe_paths
        except (UnicodeDecodeError, ValueError, OSError):
            return None

    def _scan_tracked_in_pruned_directories(self) -> None:
        """Fail-closed check: tracked files in prune_dirs are scanned for secrets.

        This prevents the bypass where a tracked file containing a credential is
        never scanned because its parent directory is in prune_dirs (e.g., a
        tracked node_modules/leak.py with a secret marker).
        """
        prune = set(self.policy.get("prune_dirs", []))
        if not prune or not (self._secret_markers or self._private_markers):
            return

        # Get the list of tracked files from git (NUL-delimited, safe paths)
        tracked_files = self._git_tracked_files_nul()
        if tracked_files is None:
            # Cannot determine tracking status - fail closed
            self._add("secret_scan", ".", "scan health",
                      passed=False,
                      details="cannot verify tracked files in pruned directories",
                      path=".")
            return

        if not tracked_files:
            # No tracked files, nothing to check
            return

        # Check each tracked file
        for rel_path in tracked_files:
            path = self.root / rel_path

            # Check if this file is in a pruned directory
            # A file is in a pruned directory if any path component is in prune_dirs
            parts = Path(rel_path).parts
            in_pruned = any(part in prune for part in parts)

            if not in_pruned:
                # Not in a pruned directory - already handled by the main walk
                continue

            # This tracked file is in a pruned directory - scan it for secrets
            # Use the same logic as the main walk: check extensions, open with O_NOFOLLOW, etc.
            name = path.name
            exts = set(self.policy.get("text_scan_extensions", []))
            extensionless = set(self.policy.get("text_scan_extensionless", []))
            max_bytes = int(self.policy.get("max_scan_bytes", 1 << 20))

            # Skip symlinks without following
            is_link = self._is_symlink(path)
            if is_link is True:
                # Symlink tracked file - reject it
                self._add(
                    "secret_scan", rel_path, "scan health",
                    passed=False, details="tracked file is a symlink (not scanned)", path=rel_path)
                continue
            # If is_link is None, inspection error already recorded
            if is_link is None:
                continue

            # Check extension-based or extensionless whitelist
            if path.suffix not in exts and name not in extensionless:
                # Not a text file - skip it
                continue

            # Scan the file for secrets
            try:
                with self._open_nofollow(path, "r") as fh:
                    st = os.fstat(fh.fileno())
                    if not stat.S_ISREG(st.st_mode):
                        self._add("secret_scan", rel_path, "scan health",
                                  passed=False, details="tracked file is not a regular file",
                                  path=rel_path)
                        continue
                    if st.st_size > max_bytes:
                        self._add("secret_scan", rel_path, "scan health",
                                  passed=False,
                                  details=f"tracked file larger than {max_bytes} bytes, not scanned",
                                  path=rel_path)
                        continue
                    for lineno, line in enumerate(fh, 1):
                        self._scan_line(rel_path, lineno, line)
            except UnicodeDecodeError:
                self._add("secret_scan", rel_path, "scan health",
                          passed=False, details="tracked file is non-utf8 text, not verified",
                          path=rel_path)
            except OSError as exc:
                self._add("secret_scan", rel_path, "scan health",
                          passed=False, details=f"tracked file unreadable: {exc}",
                          path=rel_path)

    def _scan_line(self, rel: str, lineno: int, line: str) -> None:
        for cls_id, rx in self._secret_markers:
            if rx.search(line):
                # class + line number ONLY: never echo the matched value.
                self._add("secret_marker", cls_id,
                          "credential-shaped marker detected",
                          passed=False, details=f"line {lineno}", path=rel)
                return
        for cls in self._private_markers:
            exempt = any(rel == p or rel.startswith(p + "/") for p in cls["exempt"])
            if cls["format"] == "public-ip":
                hit = self._known_network_hit(cls, line) or (
                    not exempt and self._public_ip_hit(cls, line))
            else:
                hit = not exempt and self._private_line_hit(cls, line)
            if hit:
                # class + line number ONLY: the value never reaches the report.
                self._add("private_marker", cls["id"],
                          "installation-private marker detected",
                          passed=False, details=f"line {lineno}", path=rel)

    def _private_line_hit(self, cls: Dict[str, Any], line: str) -> bool:
        fmt = cls["format"]
        if fmt == "regex":
            return any(rx.search(line) for rx in cls["patterns"])
        if fmt == "token-sha256":
            tokens = _TOKEN_RE.findall(_normalise_for_tokens(line))
            salt, hashes = cls["salt"], cls["hashes"]
            for i, token in enumerate(tokens):
                if private_token_digest(salt, token) in hashes:
                    return True
                if i + 1 < len(tokens) and private_token_digest(
                        salt, token + " " + tokens[i + 1]) in hashes:
                    return True
            return False
        return False

    def _public_ip_hit(self, cls: Dict[str, Any], line: str) -> bool:
        """Any globally routable address: documentation (RFC 5737/3849),
        private, loopback and link-local ranges are not global. Shared address
        space (RFC 6598, where tailnet nodes live) counts as a hit too. Exempt
        prefixes (tests) skip this, never the hashed known networks."""
        import ipaddress
        shared = ipaddress.ip_network(_SHARED_V4)
        for rx in (_IPV4_CANDIDATE_RE, _IPV6_CANDIDATE_RE):
            for m in rx.finditer(line):
                try:
                    addr = ipaddress.ip_address(m.group(0))
                except ValueError:
                    continue
                if addr.is_global or (addr.version == 4 and addr in shared):
                    return True
        return False

    def _known_network_hit(self, cls: Dict[str, Any], line: str) -> bool:
        import ipaddress
        if not cls["hashes"]:
            return False
        for rx, prefixes in ((_IPV4_CANDIDATE_RE, cls["v4"]), (_IPV6_CANDIDATE_RE, cls["v6"])):
            for m in rx.finditer(line):
                try:
                    addr = ipaddress.ip_address(m.group(0))
                except ValueError:
                    continue
                for plen in prefixes:
                    net = ipaddress.ip_network(f"{addr}/{plen}", strict=False)
                    if private_token_digest(cls["salt"], str(net)) in cls["hashes"]:
                        return True
        return False

    # --------------------------------------------------------------- phase 4

    def check_git_history(self) -> None:
        """Verify the git history is a fresh, self-contained single commit,
        exactly at the candidate root with no extra refs, no remotes, and clean
        tracked/untracked state.

        Catches every history-tampering / non-fresh vector, including the
        linked-worktree / common-dir case (a worktree shares another repo's
        object store and is not a self-contained publication)."""
        ok, _ = self._git(["rev-parse", "--is-inside-work-tree"])
        if not ok:
            self._add("git_history", "git-available", "git available",
                      passed=False, details="git not available or not a work tree")
            return
        self._git_toplevel_is_root()
        self._git_ordinary_git_dir()
        self._git_clean_state()
        self._git_main_branch()
        self._git_self_contained()
        self._git_not_shallow()
        self._git_no_grafts()
        self._git_no_replace()
        self._git_no_alternates()
        self._git_no_remotes()
        self._git_fresh_refs()
        self._git_single_commit()

    def _git_toplevel_is_root(self) -> None:
        """Verify `git rev-parse --show-toplevel` returns exactly the candidate root."""
        ok, toplevel = self._git(["rev-parse", "--show-toplevel"])
        if not ok:
            self._add("git_history", "git-toplevel-is-root",
                      "candidate root is the git toplevel",
                      passed=False, details="could not determine toplevel")
            return
        toplevel_resolved = Path(toplevel).resolve()
        if toplevel_resolved != self.root:
            self._add("git_history", "git-toplevel-is-root",
                      "candidate root is the git toplevel",
                      passed=False,
                      details="toplevel does not match candidate root")
            return
        self._add("git_history", "git-toplevel-is-root",
                  "candidate root is the git toplevel",
                  passed=True, details="toplevel matches candidate root")

    def _git_ordinary_git_dir(self) -> None:
        """Verify the repository has an ordinary .git directory (not a file or symlink)."""
        ok, gitdir = self._git(["rev-parse", "--git-dir"])
        if not ok:
            self._add("git_history", "git-ordinary-git-dir",
                      "ordinary .git directory (not a file or symlink)",
                      passed=False, details="could not determine git-dir")
            return
        gd_path = Path(gitdir)
        if not gd_path.is_absolute():
            gd_path = self.root / gd_path
        if gd_path.is_symlink():
            self._add("git_history", "git-ordinary-git-dir",
                      "ordinary .git directory (not a file or symlink)",
                      passed=False, details="git-dir is a symlink")
            return
        if not gd_path.is_dir():
            self._add("git_history", "git-ordinary-git-dir",
                      "ordinary .git directory (not a file or symlink)",
                      passed=False, details="git-dir is not a directory")
            return
        self._add("git_history", "git-ordinary-git-dir",
                  "ordinary .git directory (not a file or symlink)",
                  passed=True, details="ordinary .git directory")

    def _git_clean_state(self) -> None:
        """Verify the working tree is clean (no staged or unstaged changes)."""
        ok, out = self._git(["status", "--porcelain"])
        if not ok:
            self._add("git_history", "git-clean-state",
                      "clean working tree (no staged or unstaged changes)",
                      passed=False, details="could not determine working tree state")
            return
        if out.strip():
            self._add("git_history", "git-clean-state",
                      "clean working tree (no staged or unstaged changes)",
                      passed=False, details="working tree is not clean")
            return
        self._add("git_history", "git-clean-state",
                  "clean working tree (no staged or unstaged changes)",
                  passed=True, details="working tree is clean")

    def _git_main_branch(self) -> None:
        """Verify HEAD is on the main branch."""
        ok, default = self._git(["symbolic-ref", "--short", "HEAD"])
        if not ok:
            self._add("git_history", "git-main-branch",
                      "HEAD is on the main branch",
                      passed=False, details="HEAD is detached")
            return
        if default.strip() != "main":
            self._add("git_history", "git-main-branch",
                      "HEAD is on the main branch",
                      passed=False, details="HEAD is not on 'main'")
            return
        self._add("git_history", "git-main-branch",
                  "HEAD is on the main branch",
                  passed=True, details="HEAD is on main")

    def _git_self_contained(self) -> None:
        ok_bare, bare = self._git(["rev-parse", "--is-bare-repository"])
        ok_gd, gitdir = self._git(["rev-parse", "--absolute-git-dir"])
        ok_cd, commondir = self._git(["rev-parse", "--git-common-dir"])
        if not (ok_bare and ok_gd and ok_cd):
            self._add("git_history", "git-self-contained",
                      "self-contained repository (not a linked worktree/submodule)",
                      passed=False, details="git metadata unreadable")
            return
        if bare.strip() == "true":
            self._add("git_history", "git-self-contained",
                      "self-contained repository (not a linked worktree/submodule)",
                      passed=False, details="bare repository (no working tree)")
            return
        # Resolve common-dir relative to the work tree and compare absolutes.
        cd_abs = (self.root / commondir).resolve() if commondir else Path()
        gd_abs = Path(gitdir).resolve() if gitdir else Path()
        if gd_abs != cd_abs:
            self._add("git_history", "git-self-contained",
                      "self-contained repository (not a linked worktree/submodule)",
                      passed=False,
                      details="git-dir differs from common-dir (linked worktree or submodule)")
            return
        self._add("git_history", "git-self-contained",
                  "self-contained repository (not a linked worktree/submodule)",
                  passed=True, details="git-dir equals common-dir")

    def _git_not_shallow(self) -> None:
        ok, val = self._git(["rev-parse", "--is-shallow-repository"])
        if not ok:
            self._add("git_history", "git-not-shallow",
                      "not a shallow clone", passed=False,
                      details="could not determine shallow status")
            return
        shallow = val.strip() == "true"
        self._add("git_history", "git-not-shallow", "not a shallow clone",
                  passed=not shallow,
                  details="shallow" if shallow else "not shallow")

    def _git_path_exists(self, git_path: str) -> Optional[bool]:
        """Check if a file exists relative to the .git directory."""
        ok, out = self._git(["rev-parse", "--git-dir"])
        if not ok:
            return None
        git_dir = Path(out)
        if not git_dir.is_absolute():
            git_dir = self.root / git_dir
        return (git_dir / git_path).exists() if git_path else True

    def _git_no_grafts(self) -> None:
        exists = self._git_path_exists("info/grafts")
        if exists is None:
            self._add("git_history", "git-no-grafts", "no grafts",
                      passed=False, details="could not locate grafts file")
            return
        self._add("git_history", "git-no-grafts", "no grafts",
                  passed=not exists,
                  details="grafts file present" if exists else "no grafts")

    def _git_no_replace(self) -> None:
        ok, out = self._git(["replace", "--list"])
        if not ok:
            self._add("git_history", "git-no-replace", "no replace refs",
                      passed=False, details="could not list replace refs")
            return
        has = bool(out.strip())
        self._add("git_history", "git-no-replace", "no replace refs",
                  passed=not has,
                  details="replace refs present" if has else "no replace refs")

    def _git_no_alternates(self) -> None:
        exists = self._git_path_exists("objects/info/alternates")
        if exists is None:
            self._add("git_history", "git-no-alternates", "no alternates",
                      passed=False, details="could not locate alternates file")
            return
        self._add("git_history", "git-no-alternates", "no alternates",
                  passed=not exists,
                  details="alternates file present" if exists else "no alternates")

    def _git_no_remotes(self) -> None:
        ok, out = self._git(["remote"])
        if not ok:
            self._add("git_history", "git-no-remotes", "no configured remotes",
                      passed=False, details="could not list remotes")
            return
        names = [n for n in out.splitlines() if n.strip()]
        # report count only, never names or URLs
        self._add("git_history", "git-no-remotes", "no configured remotes",
                  passed=not names,
                  details=f"{len(names)} remote(s) configured" if names else "no remotes")

    def _git_fresh_refs(self) -> None:
        ok_head, default = self._git(["symbolic-ref", "--short", "HEAD"])
        ok_refs, refs_out = self._git(["for-each-ref", "--format=%(refname)"])
        if not ok_head:
            self._add("git_history", "git-fresh-refs",
                      "only the default branch exists (no remotes/tags/stash/extra refs)",
                      passed=False, details="HEAD is detached (not on the default branch)")
            return
        if not ok_refs:
            self._add("git_history", "git-fresh-refs",
                      "only the default branch exists (no remotes/tags/stash/extra refs)",
                      passed=False, details="could not enumerate refs")
            return
        refs = {r for r in refs_out.splitlines() if r.strip()}
        expected = {f"refs/heads/{default.strip()}"}
        extra = refs - expected
        self._add("git_history", "git-fresh-refs",
                  "only the default branch exists (no remotes/tags/stash/extra refs)",
                  passed=not extra,
                  details=f"{len(extra)} extra ref(s)" if extra else "no extra refs")

    def _git_single_commit(self) -> None:
        ok, out = self._git(["rev-list", "--count", "--all"])
        if not ok:
            self._add("git_history", "git-single-commit",
                      "exactly one commit reachable across all refs",
                      passed=False, details="could not count commits")
            return
        try:
            count = int(out.strip())
        except ValueError:
            self._add("git_history", "git-single-commit",
                      "exactly one commit reachable across all refs",
                      passed=False, details="unparseable commit count")
            return
        self._add("git_history", "git-single-commit",
                  "exactly one commit reachable across all refs",
                  passed=(count == 1),
                  details=f"{count} commit(s) reachable")

    # --------------------------------------------------------------- external

    def check_external_evidence(self) -> None:
        """Report institutional/GitHub/Meta/maintainer evidence as explicitly
        external. These are informational (passed=None): the tool does not
        pretend to verify them locally, and they do not count as a pass."""
        for ev in self.policy.get("external_evidence", []):
            self._add("external_evidence", ev.get("id", "external"),
                      ev.get("description", "external evidence"),
                      passed=None,
                      details=ev.get("note", "EXTERNAL - not locally verifiable"))
        for cls_id in self.private_digests_missing:
            self._add("external_evidence", "private-digests:" + cls_id,
                      "the private digests of this marker class are not available here",
                      passed=None,
                      details="EXTERNAL - names and hosts are checked in the private working repository")

    # ------------------------------------------------------------------- run

    def run(self) -> bool:
        """Run all preflight checks. Returns True iff every locally verifiable
        check passes. Forbidden paths are evaluated first and fail-closed: when
        any are present, ONLY forbidden path findings are produced (all other
        phases including external evidence are skipped)."""
        self.check_forbidden_paths()
        if any(f.check_type == "forbidden_path" for f in self.findings
               if f.passed is False):
            # Forbidden paths present - fail immediately without checking anything else
            return False
        # No forbidden paths - continue with all checks
        self.check_external_evidence()
        self.check_ambient_environment()
        self.check_required_artifacts()
        self.check_binary_asset_sets()
        self.check_secret_markers()
        self.check_git_history()
        return all(f.passed for f in self.findings if f.passed is not None)

    def print_findings(self) -> None:
        """Print findings in deterministic sorted order."""
        for finding in sorted(
                self.findings,
                key=lambda f: (f.check_type, f.id, f.path, f.details)):
            status = "PASS" if finding.passed is True else (
                "FAIL" if finding.passed is False else "EXT")
            parts = [f"[{status}]", finding.check_type, finding.id]
            if finding.description:
                parts.append(finding.description)
            if finding.path:
                parts.append(finding.path)
            if finding.details:
                parts.append(f"({finding.details})")
            print(" ".join(parts))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tier 7 public-cut preflight checker")
    parser.add_argument("root", type=Path, help="candidate root directory to check")
    parser.add_argument(
        "--policy", type=Path,
        default=Path(__file__).parent / "public_cut_policy.json",
        help="policy file path (default: scripts/public_cut_policy.json)")
    args = parser.parse_args()

    if not args.root.exists():
        print(f"Error: root directory does not exist: {args.root}", file=sys.stderr)
        return 2
    if not args.policy.exists():
        print(f"Error: policy file does not exist: {args.policy}", file=sys.stderr)
        return 2

    try:
        checker = PublicCutPreflight(args.root, args.policy)
    except PolicyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except PlatformError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    all_passed = checker.run()
    checker.print_findings()
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
