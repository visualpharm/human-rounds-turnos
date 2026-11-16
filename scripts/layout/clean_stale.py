#!/usr/bin/env python3
"""clean_stale.py — what an old checkout keeps in the directories the layout move emptied.

    python3 scripts/layout/clean_stale.py [--root DIR] [--apply]

After checking out the new layout, git removes the tracked files of `core/`, `test/`, ... but an
untracked `__pycache__/` (or a local file such as `connectors/webturno/passwords.json`) keeps the
old directory alive. A directory `core/` holding only `__pycache__/` is importable as an empty
namespace package, so a stale `import core` fails late and obscurely instead of at once.

Without `--apply` nothing is touched: every leftover is listed. With `--apply`, `__pycache__`
directories and then empty directories under the old locations are removed; any other file is
never deleted — it is listed with the place it belongs now. Never `git clean -X`: it would also
delete the ignored `.env`. Exit 0 when nothing but caches was found, 1 when files need a person.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan  # noqa: E402


def _tracked(root):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True,
                         text=True).stdout
    return {p for p in out.split("\0") if p}


def old_locations(tracked):
    """Old directories of every stage that no longer hold a tracked file."""
    out = []
    for _stage, old, _new in plan.all_moves():
        if "." in old.rsplit("/", 1)[-1]:
            continue  # a file, not a directory
        if any(p == old or p.startswith(old + "/") for p in tracked):
            continue  # not moved in this checkout yet
        out.append(old)
    return out


def scan(root):
    """(caches, leftovers): `__pycache__` dirs and other untracked files under old locations."""
    tracked = _tracked(root)
    caches, leftovers = [], []
    for old in old_locations(tracked):
        base = os.path.join(root, old)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            if "__pycache__" in dirnames:
                caches.append(os.path.join(dirpath, "__pycache__"))
                dirnames.remove("__pycache__")
            for f in filenames:
                rel = os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/")
                leftovers.append((rel, plan.map_path(_stage_of(old), rel)))
    return sorted(caches), sorted(leftovers)


def _stage_of(old):
    for stage, src, _dst in plan.all_moves():
        if src == old:
            return stage
    raise KeyError(old)


def prune_empty(root):
    removed = []
    for old in old_locations(_tracked(root)):
        base = os.path.join(root, old)
        for dirpath, _dirnames, _filenames in os.walk(base, topdown=False):
            try:
                os.rmdir(dirpath)
                removed.append(dirpath)
            except OSError:
                pass
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description="List or remove leftovers of the layout move.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--apply", action="store_true", help="remove caches and empty directories")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    caches, leftovers = scan(root)
    for c in caches:
        print("cache    %s" % os.path.relpath(c, root))
        if args.apply:
            shutil.rmtree(c, ignore_errors=True)
    if args.apply:
        for d in prune_empty(root):
            print("removed  %s/" % os.path.relpath(d, root))
    for rel, new in leftovers:
        print("leftover %s -> belongs in %s (move it yourself if you still need it)" % (rel, new))
    return 1 if leftovers else 0


if __name__ == "__main__":
    sys.exit(main())
