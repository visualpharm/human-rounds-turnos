#!/usr/bin/env python3
"""Build the public source cut and check it, without publishing anything.

    python3 scripts/public_cut_build.py [--ref HEAD] [--dest DIR] [--summary-dir DIR]

1. `git archive <ref>` of this repository is unpacked into a NEW EMPTY directory
   outside the repository. Paths the archived tree's `.gitattributes` marks
   `export-ignore` stay out (`--worktree-attributes` applies the working tree's
   file instead, to check an older ref against today's exclusions).
2. The directory becomes a fresh repository: branch `main`, one commit, no
   remotes, a neutral author.
3. `scripts/public_cut_preflight.py` (the policy of the CHECKED-OUT repository,
   not of the ref) runs on it, then gitleaks (`gitleaks dir`, redacted; installed
   with Homebrew when missing).
4. A value-free summary is written: findings by class, rule and path; never a
   matched value, a secret, or a line's content.

Nothing is pushed and no remote is created: publication is a separate step.
Exit 0 when the preflight passes and gitleaks finds nothing, 1 otherwise, 2 on
a usage or environment error.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import public_cut_preflight as preflight  # noqa: E402

AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "Human Rounds",
    "GIT_AUTHOR_EMAIL": "noreply@humanrounds.invalid",
    "GIT_COMMITTER_NAME": "Human Rounds",
    "GIT_COMMITTER_EMAIL": "noreply@humanrounds.invalid",
}


class BuildError(Exception):
    pass


def _clean_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(args: List[str], cwd: Path, *, env: Optional[Dict[str, str]] = None,
         binary: bool = False) -> Any:
    proc = subprocess.run(["git", *args], cwd=str(cwd), env=env or _clean_env(),
                          capture_output=True, text=not binary, timeout=600)
    if proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        raise BuildError(f"git {args[0]} failed: {err.strip()[:300]}")
    return proc.stdout


def prepare_dest(dest: Optional[Path], repo: Path) -> Path:
    """A new empty directory outside the repository."""
    if dest is None:
        return Path(tempfile.mkdtemp(prefix="hr-public-cut-"))
    dest = dest.resolve()
    repo_r = repo.resolve()
    if dest == repo_r or repo_r in dest.parents:
        raise BuildError("destination must be outside the repository")
    if dest.exists():
        if not dest.is_dir() or dest.is_symlink() or any(dest.iterdir()):
            raise BuildError("destination exists and is not an empty directory")
    else:
        dest.mkdir(parents=True)
    return dest


def archive_into(repo: Path, ref: str, dest: Path, worktree_attributes: bool) -> int:
    args = ["archive", "--format=tar"]
    if worktree_attributes:
        args.append("--worktree-attributes")
    args.append(ref)
    data = _git(args, repo, binary=True)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        members = []
        for m in tar.getmembers():
            if m.name == "pax_global_header":
                continue
            members.append(m)
            if m.isfile():
                count += 1
        tar.extractall(dest, members=members, filter="data")
    return count


def commit_fresh(dest: Path) -> None:
    env = _clean_env()
    env.update(AUTHOR_ENV)
    _git(["init", "-q", "-b", "main", "."], dest, env=env)
    _git(["config", "commit.gpgsign", "false"], dest, env=env)
    # -f: a tracked file that also matches .gitignore is part of the archive
    # and must be part of the cut's commit.
    _git(["add", "-A", "-f"], dest, env=env)
    _git(["commit", "-q", "--no-verify", "-m", "Human Rounds public source"], dest, env=env)


def excluded_paths(repo: Path, ref: str, dest: Path) -> List[str]:
    """Paths in `ref` that the cut left out (export-ignore)."""
    listed = _git(["ls-tree", "-r", "-z", "--name-only", ref], repo)
    in_ref = {p for p in listed.split("\0") if p}
    in_cut = {p for p in _git(["ls-files", "-z"], dest).split("\0") if p}
    return sorted(in_ref - in_cut)


def dangling_references(dest: Path, excluded: List[str]) -> Dict[str, List[str]]:
    """Files of the cut that NAME a path the cut left out (a test that reads an
    excluded file, a link to an excluded note). Names only; content never."""
    kept = [rel for rel in _git(["ls-files", "-z"], dest).split("\0") if rel]
    keys = set()
    for p in excluded:
        parts = p.split("/")
        keys.add(p)
        # A directory counts only when the cut keeps nothing under it
        # (scripts/fixes stays, one file in it does not).
        top = "/".join(parts[:2])
        if len(parts) > 2 and not any(k.startswith(top + "/") for k in kept):
            keys.add(top)
    keys = {k for k in keys if len(k) > 8}
    exts = {".py", ".mjs", ".js", ".cjs", ".sh", ".json", ".yml", ".yaml", ".toml", ".md"}
    out: Dict[str, List[str]] = {}
    for rel in kept:
        if not rel or Path(rel).suffix not in exts:
            continue
        try:
            text = (dest / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = sorted(k for k in keys if k in text)
        if hits:
            out[rel] = hits
    return out


def run_preflight(dest: Path, policy: Path) -> Dict[str, Any]:
    checker = preflight.PublicCutPreflight(dest, policy)
    passed = checker.run()
    failures = [f for f in checker.findings if f.passed is False]
    by_class = collections.Counter(f"{f.check_type}:{f.id}" for f in failures)
    return {
        "passed": passed,
        "failures": len(failures),
        "external": sum(1 for f in checker.findings if f.passed is None),
        "by_class": dict(sorted(by_class.items())),
        "items": sorted(
            ({"check": f.check_type, "id": f.id, "path": f.path, "details": f.details}
             for f in failures),
            key=lambda d: (d["check"], d["id"], d["path"] or "", d["details"] or "")),
    }


def ensure_gitleaks() -> str:
    found = shutil.which("gitleaks")
    if found:
        return found
    brew = shutil.which("brew")
    if not brew:
        raise BuildError("gitleaks is missing and Homebrew is not available")
    subprocess.run([brew, "install", "gitleaks"], check=False, capture_output=True, timeout=1800)
    found = shutil.which("gitleaks")
    if not found:
        raise BuildError("gitleaks could not be installed")
    return found


def run_gitleaks(dest: Path, workdir: Path) -> Dict[str, Any]:
    exe = ensure_gitleaks()
    report = workdir / "gitleaks.json"
    argv = [exe, "dir", "--no-banner", "--redact=100", "--exit-code", "0", "--log-level", "error",
            "--report-format", "json", "--report-path", str(report)]
    # The cut's own reviewed config, named explicitly; an ambient GITLEAKS_*
    # (GITLEAKS_CONFIG outranks the file) must not swap in other rules.
    if (dest / ".gitleaks.toml").is_file():
        argv += ["--config", ".gitleaks.toml"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITLEAKS_")}
    proc = subprocess.run(argv + ["."], cwd=str(dest), env=env,
                          capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not report.exists():
        raise BuildError("gitleaks did not produce a report")
    raw = json.loads(report.read_text(encoding="utf-8") or "[]")
    report.unlink()
    # Scanned as "." from inside the cut, so paths are cut-relative and the
    # cut's own .gitleaks.toml / .gitleaksignore (reviewed fixtures) apply.
    prefix = str(dest).rstrip("/") + "/"
    items = []
    for leak in raw:
        path = str(leak.get("File", ""))
        if path.startswith(prefix):
            path = path[len(prefix):]
        if path.startswith("./"):
            path = path[2:]
        # Rule, file and line: never the Secret/Match fields.
        items.append({"rule": leak.get("RuleID"), "path": path, "line": leak.get("StartLine")})
    items.sort(key=lambda d: (d["path"], d["line"] or 0, d["rule"] or ""))
    version = subprocess.run([exe, "version"], capture_output=True, text=True).stdout.strip()
    return {"version": version, "findings": len(items),
            "by_rule": dict(collections.Counter(i["rule"] for i in items).most_common()),
            "items": items}


def write_summary(summary: Dict[str, Any], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / "public-cut-summary.json"
    js.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = out_dir / "public-cut-summary.md"
    lines = [
        "# Public source cut",
        "",
        f"- Ref: `{summary['ref']}` ({summary['commit'][:12]})",
        f"- Built: {summary['built_at']}",
        f"- Files in the cut: {summary['files']}; left out by export-ignore: {len(summary['excluded'])}",
        f"- Preflight: {'PASS' if summary['preflight']['passed'] else 'FAIL'} "
        f"({summary['preflight']['failures']} failures, {summary['preflight']['external']} external)",
        f"- gitleaks {summary['gitleaks'].get('version', '')}: {summary['gitleaks'].get('findings')} findings",
        "",
        "## Preflight failures by class",
        "",
    ]
    for cls, n in summary["preflight"]["by_class"].items():
        lines.append(f"- {cls}: {n}")
    lines += ["", "## Preflight failures", ""]
    for it in summary["preflight"]["items"]:
        lines.append(f"- {it['check']} `{it['id']}` {it['path'] or ''} {it['details'] or ''}".rstrip())
    lines += ["", "## gitleaks by rule", ""]
    for rule, n in summary["gitleaks"].get("by_rule", {}).items():
        lines.append(f"- {rule}: {n}")
    lines += ["", "## gitleaks findings (rule, file, line)", ""]
    for it in summary["gitleaks"].get("items", []):
        lines.append(f"- {it['rule']} {it['path']}:{it['line']}")
    lines += ["", "## Files of the cut that name an excluded path", ""]
    for rel, hits in summary["dangling_references"].items():
        lines.append(f"- {rel}: {', '.join(hits[:4])}" + (f" +{len(hits) - 4}" if len(hits) > 4 else ""))
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [js, md]


def build(repo: Path, ref: str, dest: Optional[Path], policy: Path,
          worktree_attributes: bool, gitleaks: bool) -> Dict[str, Any]:
    commit = _git(["rev-parse", "--verify", ref + "^{commit}"], repo).strip()
    dest = prepare_dest(dest, repo)
    files = archive_into(repo, commit, dest, worktree_attributes)
    commit_fresh(dest)
    excluded = excluded_paths(repo, commit, dest)
    summary: Dict[str, Any] = {
        "ref": ref,
        "commit": commit,
        "built_at": datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "dest": str(dest),
        "worktree_attributes": worktree_attributes,
        "files": files,
        "excluded": excluded,
        "preflight": run_preflight(dest, policy),
        "dangling_references": dangling_references(dest, excluded),
    }
    if gitleaks:
        with tempfile.TemporaryDirectory(prefix="hr-gitleaks-") as tmp:
            summary["gitleaks"] = run_gitleaks(dest, Path(tmp))
    else:
        summary["gitleaks"] = {"findings": None, "skipped": True}
    leaks = summary["gitleaks"].get("findings")
    summary["passed"] = bool(summary["preflight"]["passed"]) and leaks == 0
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--dest", type=Path, help="new empty directory outside the repo (default: a fresh temp dir)")
    ap.add_argument("--summary-dir", type=Path, help="where public-cut-summary.{json,md} go")
    ap.add_argument("--policy", type=Path, default=SCRIPTS / "public_cut_policy.json")
    ap.add_argument("--worktree-attributes", action="store_true",
                    help="apply the working tree's .gitattributes (check an older ref against today's exclusions)")
    ap.add_argument("--no-gitleaks", action="store_true")
    args = ap.parse_args(argv)
    try:
        summary = build(REPO, args.ref, args.dest, args.policy,
                        args.worktree_attributes, not args.no_gitleaks)
    except (BuildError, preflight.PolicyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.summary_dir:
        for p in write_summary(summary, args.summary_dir):
            print(f"summary: {p}")
    pf = summary["preflight"]
    print(f"cut: {summary['dest']} ({summary['files']} files, {len(summary['excluded'])} left out)")
    print(f"preflight: {'PASS' if pf['passed'] else 'FAIL'} {pf['by_class']}")
    gl = summary["gitleaks"]
    print(f"gitleaks: {gl.get('findings')} findings {gl.get('by_rule', '')}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
