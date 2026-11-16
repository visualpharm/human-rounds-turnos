#!/usr/bin/env python3
"""import_all.py — compile every tracked Python file and import every application module.

    python3 scripts/layout/import_all.py [--root DIR] [--json OUT]

A layout move is only mechanical if every module that imported before still imports after: this
probe lists each tracked `.py` that does not compile, and each application module (every module
under the application packages, wherever the layout keeps them) that raises on import, with the
exception — and each module name a module/connector manifest loads only at run time (`module`,
`entrypoint`, `after`), which no import statement reaches. Run it on the tree before and after the move and compare the two lists — only a
module that broke in the move is a finding (one that needs a database or a key fails on both).

Imports run in one fresh interpreter per batch with the repository root first on sys.path and
`TURNOS_FAKE_AI=1`, `TURNOS_SKIP_HSI=1`; nothing is served, no database is opened by the import
itself. Exit 0 when every file compiles and every module imports, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan  # noqa: E402

_CHILD = r"""
import importlib, json, sys, traceback
sys.path.insert(0, sys.argv[1])
out = {}
for name in sys.argv[2:]:
    try:
        importlib.import_module(name)
    except BaseException as e:  # noqa: BLE001 - every failure is the finding
        out[name] = "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:200] if str(e) else "")
print(json.dumps(out))
"""


MANIFEST_KEYS = ("module", "entrypoint", "after")


def tracked(root):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True,
                         text=True).stdout
    return sorted(p for p in out.split("\0") if p and not plan.is_skipped(p))


def tracked_python(root):
    return [p for p in tracked(root) if p.endswith(".py")]


def manifest_names(root, files):
    """The module names the manifests (`module.json`, `connector.json`) import at run time."""
    names = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in MANIFEST_KEYS and isinstance(value, str):
                    names.add(value)
                elif key in MANIFEST_KEYS and isinstance(value, dict):
                    names.update(v for v in value.values() if isinstance(v, str))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for f in files:
        if os.path.basename(f) in ("module.json", "connector.json"):
            with open(os.path.join(root, f), encoding="utf-8") as fh:
                walk(json.load(fh))
    return sorted(names)


def application_modules(files):
    """Dotted names of the modules of the application packages, old layout or new."""
    roots = set(plan.PACKAGES) | {"app"}
    # after the move the root keeps only run-only shims, which refuse to be imported on purpose
    moved = any(f.startswith("app/") for f in files)
    mods = []
    for f in files:
        if "/" in f:
            if f.split("/", 1)[0] not in roots:
                continue
        elif moved or f[:-3] not in plan.ROOT_MODULES:
            continue
        mod = f[:-3]
        if mod.endswith("/__main__"):
            continue  # `python -m <package>` runs the service: importing it is starting it
        if mod.endswith("/__init__"):
            mod = mod[: -len("/__init__")]
        mods.append(mod.replace("/", "."))
    return sorted(set(mods))


def compile_errors(root, files):
    errors = {}
    for f in files:
        try:
            with open(os.path.join(root, f), "rb") as fh:
                compile(fh.read(), f, "exec", dont_inherit=True)
        except SyntaxError as e:
            errors[f] = "SyntaxError: %s (line %s)" % (e.msg, e.lineno)
    return errors


def import_errors(root, modules, batch=40, python=None):
    env = dict(os.environ, TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", PYTHONDONTWRITEBYTECODE="1")
    errors = {}
    for i in range(0, len(modules), batch):
        chunk = modules[i:i + batch]
        proc = subprocess.run([python or sys.executable, "-c", _CHILD, root, *chunk], cwd=root,
                              env=env, capture_output=True, text=True, timeout=600)
        try:
            errors.update(json.loads(proc.stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            for name in chunk:
                errors[name] = "batch crashed: %s" % (proc.stderr.strip().splitlines() or ["?"])[-1][:200]
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compile and import every application module.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", help="write {compile: {...}, import: {...}} here")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    files = tracked_python(root)
    result = {
        "compile": compile_errors(root, files),
        "import": import_errors(root, application_modules(files)),
        "manifest": import_errors(root, manifest_names(root, tracked(root))),
        "modules": len(application_modules(files)),
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, sort_keys=True)
    print("modules %d, compile errors %d, import errors %d, manifest errors %d" % (
        result["modules"], len(result["compile"]), len(result["import"]), len(result["manifest"])))
    for kind in ("compile", "import", "manifest"):
        for name, err in sorted(result[kind].items()):
            print("  %s %s %s" % (kind, name, err))
    return 0 if not any(result[k] for k in ("compile", "import", "manifest")) else 1


if __name__ == "__main__":
    sys.exit(main())
