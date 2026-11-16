#!/usr/bin/env python3
"""Dry-run-only inspection for the local credential-boundary migration.

This tool intentionally has no write mode. It reads a user-nominated path only
to produce counts, classifications, and permission modes; that path remains
operator-controlled and is never treated as an approved secret source. It
does not invoke the fixed credential vault provider; that provider's root-controlled,
nonprinting validation is the managed executor's ``--check`` mode. This tool
never prints paths, key names, or values.
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import managed_secrets  # noqa: E402


def _mode(path: Path) -> str | None:
    try:
        return format(stat.S_IMODE(path.stat().st_mode), "04o")
    except OSError:
        return None


def _env_counts(path: Path) -> dict[str, int]:
    counts = {"total": 0, "protected": 0, "public": 0, "malformed": 0}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        counts["malformed"] = 1
        return counts
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            counts["malformed"] += 1
            continue
        key, _value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "A").isalnum() or key != key.upper():
            counts["malformed"] += 1
            continue
        counts["total"] += 1
        counts[managed_secrets.classify_key(key)] += 1
    return counts


def _source_counts(path: Path) -> dict[str, int]:
    counts = {"profiles": 0, "total": 0, "protected": 0, "public": 0, "malformed": 0}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        profiles = doc["profiles"]
        if not isinstance(profiles, dict):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        counts["malformed"] = 1
        return counts
    counts["profiles"] = len(profiles)
    for mapping in profiles.values():
        if not isinstance(mapping, dict):
            counts["malformed"] += 1
            continue
        for key in mapping:
            if not isinstance(key, str) or not managed_secrets.is_runtime_key(key):
                counts["malformed"] += 1
                continue
            counts["total"] += 1
            counts[managed_secrets.classify_key(key)] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dry-run local secret migration inspection")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--self-check", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--source-file", type=Path)
    args = parser.parse_args(argv)
    if args.dry_run and args.env_file is None:
        parser.error("--dry-run requires --env-file")
    if args.self_check and args.source_file is None:
        parser.error("--self-check requires --source-file")
    if args.env_file is not None and not args.dry_run:
        parser.error("--env-file is only valid with --dry-run")
    if args.source_file is not None and not args.self_check:
        parser.error("--source-file is only valid with --self-check")

    path = args.env_file if args.dry_run else args.source_file
    report = {
        "mode": "dry-run" if args.dry_run else "self-check",
        "file_mode": _mode(path),
        "classification": _env_counts(path) if args.dry_run else _source_counts(path),
        "writes": 0,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["classification"]["malformed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
