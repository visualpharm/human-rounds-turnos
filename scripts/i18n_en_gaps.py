#!/usr/bin/env python3
"""i18n_en_gaps.py — qué textos de la interfaz quedan en castellano en inglés.

`locales/es/` es la fuente y `locales/en/` se mantiene a mano: cada clave que
se agrega al castellano y no al inglés se ve en castellano en la versión en
inglés, y quien la agregó no lo ve nunca porque navega en castellano. Este
script lista las dos formas de ese hueco:

    Faltan      una clave del catálogo castellano (central o de un módulo)
                que el inglés no tiene.
    Sin clave   una clave que el código pide (`t_or("…")`, `T("…")`,
                `i18n.t("…")`, `{{t.…}}`) y que no está en ningún catálogo:
                se ve siempre el castellano del fallback.

Uso:  python3 scripts/i18n_en_gaps.py [--json]
Salida 0 = sin huecos; 1 = hay huecos (la lista va a stdout).
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directorios que no son código servido.
_SKIP_DIRS = {
    ".git", "node_modules", ".claude", "locales", "test", "tests", "stories",
    "storybook-static", "docs", "__pycache__", ".venv", "venv", "data",
    "packs", "extensions", "intake", "alembic", "migrations",
}
_CODE_EXT = (".py", ".js", ".mjs", ".html", ".jinja", ".j2")

_KEY = r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+"
_PATTERNS = [
    re.compile(r"\bt_or\(\s*[\"'](" + _KEY + r")[\"']"),
    re.compile(r"(?<![\w.])T\(\s*[\"'](" + _KEY + r")[\"']"),
    re.compile(r"\b(?:i18n|_i18n|I18N)\.t\(\s*[\"'](" + _KEY + r")[\"']"),
    re.compile(r"(?<![\w.])t\(\s*[\"'](" + _KEY + r")[\"']"),
    re.compile(r"\{\{\s*t\.(" + _KEY + r")\s*\}\}"),
]


def locale_roots():
    roots = [os.path.join(ROOT, "locales")]
    mods = os.path.join(ROOT, "app", "modules")
    for mid in sorted(os.listdir(mods)) if os.path.isdir(mods) else []:
        manifest = os.path.join(mods, mid, "module.json")
        if not os.path.isfile(manifest):
            continue
        try:
            with open(manifest, encoding="utf-8") as fh:
                loc = json.load(fh).get("locales")
        except (OSError, ValueError):
            continue
        if loc:
            roots.append(os.path.join(ROOT, "app", loc))  # a manifest names it inside the code package
    return roots


def catalog(lang):
    """{key: (value, file)} across every root for one language."""
    out = {}
    for root in locale_roots():
        d = os.path.join(root, lang)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".json") or fname.startswith("_"):
                continue
            path = os.path.join(d, fname)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            for k, v in data.items():
                if k.startswith("_"):
                    continue
                out[k] = (v, os.path.relpath(path, ROOT))
    return out


def code_refs():
    """{key: first 'path:line'} for every literal key the served code asks for."""
    refs = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        parts = [] if rel == "." else rel.split(os.sep)
        if any(p in _SKIP_DIRS for p in parts):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if not fname.endswith(_CODE_EXT):
                continue
            path = os.path.join(dirpath, fname)
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(lines, 1):
                for pat in _PATTERNS:
                    for m in pat.finditer(line):
                        refs.setdefault(m.group(1), "%s:%d" % (os.path.relpath(path, ROOT), i))
    return refs


def gaps():
    es, en = catalog("es"), catalog("en")
    missing = {k: es[k][1] for k in es if k not in en}
    refs = code_refs()
    unkeyed = {k: where for k, where in refs.items() if k not in en and k not in es}
    return missing, unkeyed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    missing, unkeyed = gaps()
    if args.json:
        print(json.dumps({"missing": missing, "unkeyed": unkeyed}, ensure_ascii=False, indent=1, sort_keys=True))
    else:
        print("Faltan en en (%d):" % len(missing))
        for k in sorted(missing):
            print("  %s  (%s)" % (k, missing[k]))
        print("Sin clave en ningún catálogo (%d):" % len(unkeyed))
        for k in sorted(unkeyed):
            print("  %s  (%s)" % (k, unkeyed[k]))
    return 1 if (missing or unkeyed) else 0


if __name__ == "__main__":
    sys.exit(main())
