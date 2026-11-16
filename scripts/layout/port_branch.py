#!/usr/bin/env python3
"""port_branch.py — carry a branch written on the old layout over to the new one.

    python3 scripts/layout/port_branch.py <branch> [--pre pre-layout] [--post layout]
            [--chain-base pre-layout] [--chain layout] [--name <branch>-layout]

A plain `git rebase` across the layout move conflicts on every rewritten import line. Instead:

1. the branch is rebased onto `--pre` (the `main` the move was applied to) — only the branch's
   own conflicts can appear here, exactly as in any rebase;
2. the layout chain (`--chain-base..--chain`) is replayed on top of that (scripts/layout/apply.py):
   the branch's code is moved and rewritten by the same program that moved `main`;
3. the difference between that tree and the replay of the chain on `--pre` alone is the branch's
   own change, already in the new layout; it is applied to `--post` as one commit on a new
   branch `--name`.

Everything happens in temporary worktrees; the original branch is never modified. The result is
printed, nothing is pushed. Exit 0 with the new branch created, 1 when a step needs a person
(the message says which, and leaves the temporary worktree path).

Defaults: the tags `pre-layout` (main before the move) and `layout` (main after it) that the
layout merge creates; the chain is the merge's second parent.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply as layout_apply  # noqa: E402


class PortError(Exception):
    pass


def _git(root, *args, check=True, input_text=None):
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          input=input_text)
    if check and proc.returncode:
        raise PortError("git %s failed: %s" % (" ".join(args[:3]), proc.stderr.strip()[-400:]))
    return proc


def _worktree(repo, ref, keep):
    path = tempfile.mkdtemp(prefix="port-branch-")
    os.rmdir(path)
    _git(repo, "worktree", "add", "-q", "--detach", path, ref)
    keep.append(path)
    return path


def _drop(repo, paths):
    for path in paths:
        _git(repo, "worktree", "remove", "--force", path, check=False)
        shutil.rmtree(path, ignore_errors=True)


def port(repo, branch, pre, post, chain_base, chain, name, log=print):
    """Create `name` = `post` + the branch's own change. Returns the new commit sha."""
    if _git(repo, "rev-parse", "--verify", "-q", "refs/heads/" + name, check=False).returncode == 0:
        raise PortError("branch %s already exists" % name)
    temps = []
    ok = False
    try:
        # 1. the branch on the old layout's last main
        work = _worktree(repo, branch, temps)
        proc = _git(work, "-c", "user.name=port-branch", "-c", "user.email=port@invalid",
                    "rebase", "-q", pre, check=False)
        if proc.returncode:
            raise PortError("rebasing %s onto %s conflicts (its own change, not the layout) — "
                            "resolve in %s, then rerun" % (branch, pre, work))
        branch_tip = _git(work, "rev-parse", "HEAD").stdout.strip()
        if branch_tip == _git(repo, "rev-parse", pre + "^{commit}").stdout.strip():
            raise PortError("%s has nothing of its own on top of %s" % (branch, pre))
        # 2. the same layout program over the branch, and over `pre` alone
        layout_apply.replay(work, chain_base, chain, log=lambda line: None)
        moved_branch = _git(work, "rev-parse", "HEAD^{tree}").stdout.strip()
        ref_work = _worktree(repo, pre, temps)
        layout_apply.replay(ref_work, chain_base, chain, log=lambda line: None)
        moved_pre = _git(ref_work, "rev-parse", "HEAD^{tree}").stdout.strip()
        # 3. the branch's own change, in the new layout, onto `post`
        diff = _git(repo, "diff", "--binary", "--full-index", moved_pre, moved_branch).stdout
        target = _worktree(repo, post, temps)
        if diff.strip():
            proc = _git(target, "apply", "--3way", "--index", check=False, input_text=diff)
            if proc.returncode:
                raise PortError("the branch's change does not apply cleanly on %s — see %s: %s" % (
                    post, target, proc.stderr.strip()[-300:]))
        subject = _git(repo, "log", "-1", "--format=%s", branch_tip).stdout.strip()
        message = "%s (ported to the new layout)\n\nPorted from %s by scripts/layout/port_branch.py.\n" % (
            subject, branch_tip[:12])
        _git(target, "-c", "user.name=port-branch", "-c", "user.email=port@invalid",
             "commit", "-q", "--allow-empty", "-m", message)
        sha = _git(target, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "branch", name, sha)
        log("created %s at %s (from %s)" % (name, sha[:12], branch))
        ok = True
        return sha
    finally:
        if ok:
            _drop(repo, temps)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Port a branch across the layout move.")
    parser.add_argument("branch")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--pre", default="pre-layout", help="main before the move")
    parser.add_argument("--post", default="layout", help="main after the move")
    parser.add_argument("--chain-base", default=None, help="where the chain starts (default: --pre)")
    parser.add_argument("--chain", default=None, help="the chain's last commit (default: --post^2)")
    parser.add_argument("--name", default=None, help="new branch name (default: <branch>-layout)")
    args = parser.parse_args(argv)
    repo = os.path.abspath(args.repo)
    try:
        port(repo, args.branch, args.pre, args.post, args.chain_base or args.pre,
             args.chain or args.post + "^2", args.name or args.branch + "-layout")
    except (PortError, layout_apply.ReplayError) as e:
        print("port_branch.py: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
