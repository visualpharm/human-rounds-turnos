#!/usr/bin/env python3
"""Test hygiene ratchet: the places where a test writes the process environment or sleeps blind.

    python3 scripts/hygiene_ratchet.py check            # exit 1 when a file has more sites than the snapshot allows
    python3 scripts/hygiene_ratchet.py report           # totals per kind, then the worst files
    python3 scripts/hygiene_ratchet.py write            # lower the snapshot to what the tree has now (never raises it)
    python3 scripts/hygiene_ratchet.py write --init     # first snapshot: take the tree as it is

Three kinds of site are counted per file under `tests/` (a site is a place in the source, not an
execution):

* `environ`    - Python: `os.environ[...] = ...`, `del os.environ[...]`, `os.environ.update / setdefault /
                 pop / popitem / clear(...)`, `os.putenv(...)`, `os.unsetenv(...)`. A write that nobody
                 restores is the "neighbour" bug: the next test in the process reads what this one left.
                 Use `tests/_env.py` (`set_env`, `patched_env`), which puts the old values back.
* `patch_dict` - Python: `patch.dict(...)` / `mock.patch.dict(...)`. It restores itself, but it is the
                 same habit as the writes above and the cheapest one to move to `tests/_env.py`.
* `pause`      - a sleep that waits for time instead of for a condition: `time.sleep(...)` in Python,
                 `waitForTimeout(...)` and `setTimeout(resolve, <ms>)` (a promise that resolves after
                 <ms>, other than 0) in the JavaScript tests. Wait for the condition (a retrying `expect`,
                 `holdsFor`, an event) or inject the clock (`core/clock.py`, `tests/_clock.py`).

`scripts/hygiene_floor.json` holds the allowance per file, one line each. The ratchet only turns
one way: a file may keep its count or go down, a file that is not in the snapshot has none, and
`write` lowers the snapshot to what the tree has (a file at zero leaves it) but never raises a number.
A number goes up only by hand, in a diff a reviewer reads, and only for a reason the commit states.
`tests/test_hygiene_ratchet.py` runs `check` in the suite.

Run from the repository root; nothing but the standard library is needed.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOR = os.path.join(ROOT, "scripts", "hygiene_floor.json")
TEST_DIR = "tests"
KINDS = ("environ", "patch_dict", "pause")
SCHEMA_VERSION = 1

_ENVIRON_METHODS = {"update", "setdefault", "pop", "popitem", "clear"}
_OS_FUNCTIONS = {"putenv", "unsetenv"}
_JS_SUFFIXES = (".mjs", ".js", ".cjs")
_SKIP_DIRS = {"node_modules", "__pycache__", "playwright-report", "test-results"}
# The fixture is where the writes belong: it is what the rest of the suite is asked to use.
_EXEMPT = {"tests/_env.py"}

_JS_LINE_COMMENT = re.compile(r"(^|\s)//[^\n]*")
_JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_JS_WAIT_FOR_TIMEOUT = re.compile(r"\bwaitForTimeout\s*\(")
# `setTimeout(resolve, 50)` - the first argument is a bare name (a promise's resolver), the delay is not 0.
# `setTimeout(fn, ms) {` is a method of a fake timer, not a call.
_JS_SLEEP = re.compile(r"\bsetTimeout\s*\(\s*[A-Za-z_$][\w$]*\s*,\s*([^)]*?)\s*\)(?!\s*(?:\{|=>))")


def _is_environ(node):
    """`os.environ` (or `environ` imported from os)."""
    if isinstance(node, ast.Attribute):
        return node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os"
    return isinstance(node, ast.Name) and node.id == "environ"


def _is_environ_target(node):
    return _is_environ(node) or (isinstance(node, ast.Subscript) and _is_environ(node.value))


def _is_patch_dict(func):
    if not (isinstance(func, ast.Attribute) and func.attr == "dict"):
        return False
    base = func.value
    return (isinstance(base, ast.Name) and base.id == "patch") or \
           (isinstance(base, ast.Attribute) and base.attr == "patch")


def count_python(source, path="<string>"):
    """{kind: sites} for one Python source; {} when it does not parse."""
    try:
        tree = ast.parse(source, path)
    except (SyntaxError, ValueError):
        return {}
    sleep_imported = any(isinstance(n, ast.ImportFrom) and n.module == "time"
                         and any(a.name == "sleep" and a.asname is None for a in n.names)
                         for n in ast.walk(tree))
    found = dict.fromkeys(KINDS, 0)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            found["environ"] += sum(1 for t in node.targets if _is_environ_target(t))
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            found["environ"] += 1 if _is_environ_target(node.target) else 0
        elif isinstance(node, ast.Delete):
            found["environ"] += sum(1 for t in node.targets if _is_environ_target(t))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in _ENVIRON_METHODS and _is_environ(func.value):
                    found["environ"] += 1
                elif func.attr in _OS_FUNCTIONS and isinstance(func.value, ast.Name) and func.value.id == "os":
                    found["environ"] += 1
                elif func.attr == "sleep" and isinstance(func.value, ast.Name) and func.value.id == "time":
                    found["pause"] += 1
            elif isinstance(func, ast.Name) and func.id == "sleep" and sleep_imported:
                found["pause"] += 1
            if _is_patch_dict(func):
                found["patch_dict"] += 1
    return {k: n for k, n in found.items() if n}


def count_javascript(source):
    """{kind: sites} for one JavaScript source (comments are ignored)."""
    text = _JS_LINE_COMMENT.sub(r"\1", _JS_BLOCK_COMMENT.sub("", source))
    n = len(_JS_WAIT_FOR_TIMEOUT.findall(text))
    n += sum(1 for m in _JS_SLEEP.finditer(text) if m.group(1).strip() != "0")
    return {"pause": n} if n else {}


def scan(root=ROOT):
    """{relative path: {kind: sites}} for the tests under `<root>/test`."""
    found = {}
    base = os.path.join(root, TEST_DIR)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            python = name.endswith(".py")
            if not (python or name.endswith(_JS_SUFFIXES)):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel in _EXEMPT:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    source = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            counts = count_python(source, path) if python else count_javascript(source)
            if counts:
                found[rel] = counts
    return found


def load_floor(path=None):
    path = path or FLOOR
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("%s: schema_version must be %d" % (path, SCHEMA_VERSION))
    return data["files"]


def dump_floor(files, path=None):
    """One line per file, sorted: two branches that lower different files merge without a conflict."""
    path = path or FLOOR
    lines = []
    for rel in sorted(files):
        entry = ", ".join('"%s": %d' % (k, files[rel][k]) for k in KINDS if files[rel].get(k))
        lines.append('  %s: {%s}' % (json.dumps(rel), entry))
    body = ",\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{\n "schema_version": %d,\n "files": {\n%s\n }\n}\n' % (SCHEMA_VERSION, body))


HINTS = {
    "environ": "write the environment through tests/_env.py (set_env / patched_env), which restores it",
    "patch_dict": "use tests/_env.py (patched_env) for the environment, or set_attr-style patching for other dicts",
    "pause": "wait for the condition (retrying expect, holdsFor, an event) or inject the clock (tests/_clock.py)",
}


def problems(found, floor):
    """The reasons `check` is red: a file with more sites than its allowance, a file with none allowed."""
    out = []
    for rel in sorted(found):
        allowed = floor.get(rel, {})
        for kind in KINDS:
            n, cap = found[rel].get(kind, 0), allowed.get(kind, 0)
            if n > cap:
                scope = "is not in scripts/hygiene_floor.json (a new file has none)" if rel not in floor \
                    else "allows %d" % cap
                out.append("%s: %d `%s` site(s), the snapshot %s; %s" % (rel, n, kind, scope, HINTS[kind]))
    return out


def lowered(found, floor):
    """The snapshot after `write`: every number at most what it was and at most what the tree has."""
    files = {}
    for rel, allowed in floor.items():
        entry = {k: min(allowed.get(k, 0), found.get(rel, {}).get(k, 0)) for k in KINDS}
        entry = {k: n for k, n in entry.items() if n}
        if entry:
            files[rel] = entry
    return files


def totals(files):
    return {k: sum(e.get(k, 0) for e in files.values()) for k in KINDS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("check", "report", "write"))
    parser.add_argument("--init", action="store_true", help="write: take the tree as the first snapshot")
    args = parser.parse_args(argv)
    found = scan()
    if args.command == "write" and args.init:
        dump_floor(found)
        print("snapshot written:", totals(found), "in %d files" % len(found))
        return 0
    floor = load_floor()
    if args.command == "check":
        bad = problems(found, floor)
        for line in bad:
            print(line)
        print("test hygiene: %s in %d files (allowed %s in %d files)" % (
            totals(found), len(found), totals(floor), len(floor)))
        return 1 if bad else 0
    if args.command == "report":
        print("tree:", totals(found), "in %d files" % len(found))
        print("snapshot:", totals(floor), "in %d files" % len(floor))
        worst = sorted(found.items(), key=lambda kv: -sum(kv[1].values()))[:15]
        for rel, counts in worst:
            print("  %5d  %s  %s" % (sum(counts.values()), rel, counts))
        return 0
    files = lowered(found, floor)
    dump_floor(files)
    print("snapshot lowered:", totals(floor), "->", totals(files), "in %d files" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
