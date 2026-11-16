#!/usr/bin/env python3
"""apply.py — replay the whole layout chain onto another commit.

    python3 scripts/layout/apply.py --base <ref> --chain <ref> [--root DIR] [--verify]

The layout change is a chain of commits on top of `--base`: ordinary commits (hand edits, each
with its tests) and mechanical ones, marked with a `Layout-Stage: <stage>` trailer, whose content
is whatever `move.py --stage <stage>` produces. Replaying the chain onto the commit checked out in
`--root` (a clean checkout, usually a fresh `main`) re-runs every mechanical stage there — so code
that landed after the chain was written is moved too — and cherry-picks every other commit.

`--verify`: the checkout must be at `--base`; the replay must then reproduce the chain's own
tree exactly (same `git write-tree`). That equality is what lets port_branch.py carry an open
branch across the move without layout conflicts.

Exit 0 on success, 1 on a conflict or a verification mismatch (the checkout is left mid-replay
for a person: `git cherry-pick --abort` / `git reset --hard <start>`), 2 on a usage error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan  # noqa: E402

TRAILER = "Layout-Stage"


class ReplayError(Exception):
    pass


def _git(root, *args, env=None, check=True):
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          env=dict(os.environ, **(env or {})))
    if check and proc.returncode:
        raise ReplayError("git %s failed: %s" % (" ".join(args), proc.stderr.strip()[-400:]))
    return proc.stdout


def chain_commits(root, base, chain):
    """[(sha, stage or None)] of base..chain, oldest first. A merge commit is refused."""
    out = []
    for sha in _git(root, "rev-list", "--reverse", "--topo-order", "%s..%s" % (base, chain)).split():
        parents = _git(root, "rev-list", "--parents", "-n", "1", sha).split()[1:]
        if len(parents) != 1:
            raise ReplayError("%s is a merge commit; the layout chain must be linear" % sha[:12])
        stage = None
        for line in _git(root, "log", "-1", "--format=%B", sha).splitlines():
            if line.startswith(TRAILER + ":"):
                stage = line.split(":", 1)[1].strip()
        if stage is not None and stage not in plan.STAGES:
            raise ReplayError("%s names an unknown stage %r" % (sha[:12], stage))
        out.append((sha, stage))
    return out


def _identity(root, sha):
    fmt = "%an%x00%ae%x00%ad%x00%cn%x00%ce%x00%cd"
    an, ae, ad, cn, ce, cd = _git(root, "log", "-1", "--format=" + fmt, "--date=raw", sha).split("\0")
    return {"GIT_AUTHOR_NAME": an, "GIT_AUTHOR_EMAIL": ae, "GIT_AUTHOR_DATE": ad,
            "GIT_COMMITTER_NAME": cn, "GIT_COMMITTER_EMAIL": ce, "GIT_COMMITTER_DATE": cd.strip()}


def replay(root, base, chain, log=print):
    """Replay base..chain onto HEAD of `root`. Returns the list of produced commit shas."""
    if _git(root, "status", "--porcelain", "--untracked-files=no").strip():
        raise ReplayError("the checkout has uncommitted changes")
    produced = []
    for sha, stage in chain_commits(root, base, chain):
        env = _identity(root, sha)
        subject = _git(root, "log", "-1", "--format=%s", sha).strip()
        if stage is None:
            proc = subprocess.run(["git", "cherry-pick", "--allow-empty", "--keep-redundant-commits", sha],
                                  cwd=root, capture_output=True, text=True,
                                  env=dict(os.environ, **{k: v for k, v in env.items()
                                                          if k.startswith("GIT_COMMITTER")}))
            if proc.returncode:
                conflicts = _git(root, "diff", "--name-only", "--diff-filter=U", check=False).split()
                raise ReplayError("cherry-pick of %s (%s) stopped: %s" % (
                    sha[:12], subject, ", ".join(conflicts) or proc.stderr.strip()[-300:]))
            log("picked  %s %s" % (sha[:12], subject))
        else:
            # the chain's own move.py at this point of the chain, not the caller's copy
            tool = os.path.join(root, "scripts", "layout", "move.py")
            if not os.path.exists(tool):
                raise ReplayError("%s needs scripts/layout/move.py earlier in the chain" % sha[:12])
            proc = subprocess.run([sys.executable, tool, "--stage", stage, "--root", root],
                                  cwd=root, capture_output=True, text=True)
            if proc.returncode:
                raise ReplayError("move.py --stage %s before %s: %s" % (
                    stage, sha[:12], (proc.stderr or proc.stdout).strip()[-400:]))
            message = _git(root, "log", "-1", "--format=%B", sha)
            _git(root, "commit", "-q", "--allow-empty", "-m", message, env=env)
            log("moved   %s %s — %s" % (sha[:12], subject, proc.stdout.splitlines()[0]
                                        if proc.stdout else ""))
        produced.append(_git(root, "rev-parse", "HEAD").strip())
    return produced


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay the layout chain onto the checkout.")
    parser.add_argument("--base", required=True, help="the commit the chain was written on")
    parser.add_argument("--chain", required=True, help="the last commit of the chain")
    parser.add_argument("--root", default=".")
    parser.add_argument("--verify", action="store_true",
                        help="the checkout is at --base: the replay must reproduce --chain's tree")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    try:
        if args.verify and _git(root, "rev-parse", "HEAD").strip() != \
                _git(root, "rev-parse", args.base + "^{commit}").strip():
            print("apply.py: --verify needs the checkout at --base", file=sys.stderr)
            return 2
        replay(root, args.base, args.chain)
        tree = _git(root, "rev-parse", "HEAD^{tree}").strip()
        print("tree %s" % tree)
        if args.verify:
            want = _git(root, "rev-parse", args.chain + "^{tree}").strip()
            if tree != want:
                print("apply.py: replay tree %s differs from the chain's %s" % (tree, want),
                      file=sys.stderr)
                return 1
            print("verified: the replay reproduces the chain exactly")
    except ReplayError as e:
        print("apply.py: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
