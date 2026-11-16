#!/usr/bin/env python3
"""Generated test snapshots: one command refreshes them all.

    python3 tests/snapshots.py --write    # rewrite tests/snapshots.json
    python3 tests/snapshots.py --check    # exit 1 when a snapshot is stale

A snapshot is a value the tests compare against something the code produces:
it moves whenever the thing it describes really changes, and the fix is always
the same command. Nothing here is edited by hand, and a failing comparison is
never "fixed" by pasting a new literal: read what moved, then `--write`.

`pages` — one sha256 per server-rendered page. First-party asset URLs carry a
content hash (`core/static_fingerprint.py`), so a hash of the raw bytes moved
on every CSS/JS edit that has nothing to do with the page. The snapshot hashes
the page with each fingerprint replaced by a placeholder (`skeleton()`): markup,
copy, catalog and the SET of linked assets are pinned, the hash values are not.
What the hashes must be is checked separately by `assert_page()`: every URL in
the page has to be exactly what `static_fingerprint` computes today from the
file on disk, and the page's `window.SANA_STATIC` manifest has to be the
current one. A stale or forged hash still fails.

`routes` — `tests/tier4_composition_inventory.json`, the ordered route table of
the composed app (its own file: `tests/_probe_rows.py` reads it).
`census` — the counters of the probe inventory (`tests/_probe_rows.py`):
sources, categories, unique categories. The invariants the counters stood for
(no protocol exception, the terminal rows, composition rows = live routes) are
asserted in `test_route_probe.py`; the numbers move with every route
and are only generated here.
`owners` — who owns each database table: a module (`modules/<id>/module.json`
`db_tables`) or `platform` (every table no module claims). A new table shows up
here as `platform` until a manifest claims it; the data map
(`DATA_MAP.md`) must still name it, a hand edit that
`test_data_map.py` enforces.
`connectors` — the connector directories (`connectors/<id>/connector.json`).
`admin_access` — the permission each `/admin` and `/api/admin` route needs
(`core/permissions.required_permission`; `unmapped` = the legacy role flags
decide), so a new admin route's access is visible in the diff.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TEST_DIR)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

import _probe_rows  # noqa: E402  (tests/_probe_rows.py: the probe inventory, flat_routes, offline_environment)
SNAPSHOT_FILE = os.path.join(TEST_DIR, "snapshots.json")

# `/static/<dir>/<stem>.<hash8>.<suffix>` — the shape of
# `static_fingerprint._fingerprinted_path`; the suffix may be `module.js`.
_ASSET_URL = re.compile(
    r"/static/((?:[A-Za-z0-9_-]+/)*)([A-Za-z0-9_-]+)\.([0-9a-f]{8})\."
    r"((?:module\.)?(?:mjs|js|css))")
_MANIFEST = re.compile(r"<script>window\.SANA_STATIC=(\{[^<]*\});</script>")
PLACEHOLDER = "<fingerprint>"


# --------------------------------------------------------------- pages

def asset_urls(html):
    """[(url, registered name, hash8)] for every fingerprinted URL in `html`."""
    return [
        (m.group(0), m.group(1) + m.group(2) + "." + m.group(4), m.group(3))
        for m in _ASSET_URL.finditer(html)
    ]


def skeleton(body):
    """The page with every fingerprint hash replaced by a placeholder."""
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    return _ASSET_URL.sub(
        lambda m: "/static/%s%s.%s.%s" % (
            m.group(1), m.group(2), PLACEHOLDER, m.group(4)),
        text).encode("utf-8")


def page_entry(body):
    flat = skeleton(body)
    return {"length": len(flat), "sha256": hashlib.sha256(flat).hexdigest()}


def _file_digest(name):
    """sha256[:8] of the asset's bytes read straight from disk — the same rule
    `static_fingerprint` applies, written out here so a producer that returns
    the wrong hash consistently is not agreed with by its own checker."""
    from app.core import paths  # where the layout keeps module code and the root web assets

    if name.startswith(("vendor/", "islands/", "platform/")):
        path = os.path.join(ROOT, "static", name)
    elif name.startswith("modules/"):
        path = paths.code_path(name)
    else:
        path = paths.root_web_asset(name)
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:8]


def stale_assets(body):
    """Fingerprint problems of one rendered page, as readable strings.

    Empty when every asset URL carries the hash of the file on disk (read here
    independently, and equal to what `static_fingerprint` returns) and the
    embedded `window.SANA_STATIC` manifest (if any) is the current one."""
    from app.core import static_fingerprint

    html = body.decode("utf-8") if isinstance(body, bytes) else body
    problems = []
    for url, name, digest in asset_urls(html):
        try:
            expected = (static_fingerprint.module_url(name)
                        if name.startswith("modules/")
                        else static_fingerprint.url(name))
            on_disk = _file_digest(name)
        except (OSError, ValueError) as exc:
            problems.append("%s: %s" % (url, exc))
            continue
        if digest != on_disk:
            problems.append("%s: the file on disk hashes to %s" % (url, on_disk))
        elif expected != url:
            problems.append("%s: static_fingerprint answers %s" % (url, expected))
    manifest = _MANIFEST.search(html)
    if manifest and json.loads(manifest.group(1)) != static_fingerprint.manifest():
        problems.append("window.SANA_STATIC is not static_fingerprint.manifest()")
    return problems


def load():
    with open(SNAPSHOT_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def assert_page(case, key, body):
    """Assert one rendered page against its snapshot and its fingerprints."""
    html = body.decode("utf-8") if isinstance(body, bytes) else body
    # A page with no fingerprinted URL would make both checks below vacuous
    # (a changed URL shape must not silently turn them into raw-byte pins).
    case.assertTrue(asset_urls(html), "%s links no fingerprinted asset" % key)
    case.assertEqual(
        stale_assets(body), [],
        "%s links an asset URL that is not the current fingerprint" % key)
    expected = load()["pages"].get(key)
    case.assertIsNotNone(
        expected, "no snapshot for %s — run: python3 tests/snapshots.py --write" % key)
    got = page_entry(body)
    case.assertEqual(
        got, expected,
        "%s changed (markup, copy or the set of linked assets; fingerprint "
        "values do not count). If that is intended: "
        "python3 tests/snapshots.py --write" % key)


def _emit_pages():
    import test_accounts_jinja
    import test_health_data

    pages = {}
    test_accounts_jinja.setUpModule()
    try:
        for key, body in test_accounts_jinja.render_snapshot_pages().items():
            pages["accounts/" + key] = page_entry(body)
    finally:
        test_accounts_jinja.tearDownModule()
    pages["datos"] = page_entry(test_health_data.render_datos_page())
    return pages


# --------------------------------------------------- routes and the census

def _route_table():
    from app import main as app

    flat = _probe_rows.flat_routes(app.app.routes, [])
    return {
        "routes": [{"path": r.path, "methods": sorted(r.methods or [])} for r in flat],
        "middleware": [str(m) for m in app.app.user_middleware],
    }


def _emit_routes():
    with _probe_rows.offline_environment():
        return _route_table()


def _emit_census():
    with _probe_rows.offline_environment():
        from app import main as app

        # The composition rows come from the app as it is now, not from the
        # committed inventory, so one `--write` is consistent in one pass.
        report, _classified = _probe_rows.build_report(app.app, _route_table())
    return report


def _permission_label(needed):
    from app.core import permissions

    if needed is permissions.UNMAPPED:
        return "unmapped"
    if isinstance(needed, frozenset):
        return "|".join(sorted(needed))
    return "any" if needed == permissions.ANY else needed


def _concrete_paths(template):
    """[(note, concrete path)] a route template is asked about.

    `required_permission` reads a path segment for the catalog editors
    (`/api/admin/content/{tipo}`) and the photo routes (`{entity}`), so those
    templates are asked once per kind, plus one unknown kind."""
    from app.core import permissions

    def fill(text, **named):
        for key, value in named.items():
            text = text.replace("{%s}" % key, value)
        return re.sub(r"\{[^}]+\}", "x", text)

    if template.startswith("/api/admin/content/{tipo}"):
        return [("tipo=" + kind, fill(template, tipo=kind))
                for kind in sorted(permissions._CONTENT_TYPES) + ["unknown"]]
    if template == "/api/admin/{entity}/{eid}/photo":
        return [("entity=" + kind, fill(template, entity=kind))
                for kind in sorted(permissions._PHOTO_ENTITIES) + ["other"]]
    return [("", None)]


def _emit_admin_access():
    from app.core import permissions

    access = {}
    with _probe_rows.offline_environment():
        from app import main as app

        for route in _probe_rows.flat_routes(app.app.routes, []):
            template = route.path
            if template != "/admin" and not template.startswith(("/admin/", "/api/admin/")):
                continue
            for method in sorted(set(route.methods or ()) - {"HEAD", "OPTIONS"}):
                for note, path in _concrete_paths(template):
                    key = "%s %s%s" % (method, template, " [%s]" % note if note else "")
                    needed = permissions.required_permission(method, template, path)
                    access[key] = _permission_label(needed)
                    # The doctor role reaches its own editor and photo.
                    as_doctor = permissions.required_permission(
                        method, template, path, role="medico")
                    if as_doctor != needed:
                        access["[role=medico] " + key] = _permission_label(as_doctor)
    return dict(sorted(access.items()))


# ------------------------------------------- tables and connectors (static)

def db_tables():
    """Every application table of a fresh install at Alembic head, from the
    committed schema picture (`alembic/schema-head.txt`, written by
    `scripts/schema_snapshot.py --write`): the baseline text is frozen, later
    tables come from revisions, so `core/db.py` no longer lists them."""
    with open(os.path.join(ROOT, "alembic", "schema-head.txt"), encoding="utf-8") as fh:
        src = fh.read()
    return set(re.findall(r"^TABLE ([a-zA-Z_][a-zA-Z0-9_]*)$", src, flags=re.MULTILINE))


def module_claims():
    """{module id: set of tables its module.json claims}."""
    claims = {}
    modules = os.path.join(ROOT, "app", "modules")
    for entry in sorted(os.listdir(modules)):
        path = os.path.join(modules, entry, "module.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            claims[doc["id"]] = set(doc.get("db_tables") or ())
    return claims


def table_owners(tables=None, claims=None):
    tables = db_tables() if tables is None else tables
    claims = module_claims() if claims is None else claims
    owner = {}
    for module_id, claimed in claims.items():
        for table in claimed & tables:
            if table in owner:
                raise SystemExit("table %r is claimed by both %r and %r" % (
                    table, owner[table], module_id))
            owner[table] = module_id
    return {table: owner.get(table, "platform") for table in sorted(tables)}


def connector_ids():
    base = os.path.join(ROOT, "app", "connectors")
    return sorted(
        name for name in os.listdir(base)
        if os.path.isfile(os.path.join(base, name, "connector.json")))


def _emit_owners():
    return table_owners()


def _emit_connectors():
    return connector_ids()


# ------------------------------------------------------------ the command

TIER4_FILE = os.path.join(TEST_DIR, "tier4_composition_inventory.json")

# section -> (emitter, where it lives). Each emitter runs in its own process:
# the renderers set up their own environment, and one section's leftovers must
# not leak into another's. `None` = a key of tests/snapshots.json; a path = a
# file of its own.
SECTIONS = {
    "pages": (_emit_pages, None),
    "census": (_emit_census, None),
    "owners": (_emit_owners, None),
    "connectors": (_emit_connectors, None),
    "admin_access": (_emit_admin_access, None),
    "routes": (_emit_routes, TIER4_FILE),
}


def _collect():
    out = {}
    for name in SECTIONS:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--emit", name],
            capture_output=True, text=True, cwd=ROOT,
            env=dict(os.environ, TURNOS_FAKE_AI="1"))
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            raise SystemExit("section %r failed" % name)
        out[name] = json.loads(proc.stdout.splitlines()[-1])
    return out


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _render(fresh):
    """{path: file text} — the files `--write` produces from collected data."""
    files = {SNAPSHOT_FILE: json.dumps(
        {name: fresh[name] for name, (_f, target) in SECTIONS.items() if target is None},
        indent=1, sort_keys=True, ensure_ascii=False) + "\n"}
    for name, (_emit, target) in SECTIONS.items():
        if target is not None:
            files[target] = json.dumps(fresh[name], indent=1, ensure_ascii=False) + "\n"
    return files


def _describe_changes(path, old_text, new_text):
    """Short lines naming what moved (never the content)."""
    try:
        old, new = json.loads(old_text), json.loads(new_text)
    except ValueError:
        return ["%s: rewritten" % os.path.basename(path)]
    lines = []
    if path == SNAPSHOT_FILE:
        for section in sorted(set(old) | set(new)):
            was, now = old.get(section), new.get(section)
            if was == now:
                continue
            if isinstance(was, dict) and isinstance(now, dict):
                for key in sorted(set(was) | set(now)):
                    if was.get(key) != now.get(key):
                        mark = "new" if key not in was else "gone" if key not in now else "changed"
                        lines.append("%s %s/%s" % (mark, section, key))
            else:
                lines.append("changed %s" % section)
    else:
        old_routes = [(r["path"], tuple(r["methods"])) for r in (old.get("routes") or [])]
        new_routes = [(r["path"], tuple(r["methods"])) for r in new.get("routes", [])]
        for route in new_routes:
            if route not in old_routes:
                lines.append("new route %s %s" % (",".join(route[1]), route[0]))
        for route in old_routes:
            if route not in new_routes:
                lines.append("gone route %s %s" % (",".join(route[1]), route[0]))
        if old_routes != new_routes and not lines:
            lines.append("route order changed")
        if old.get("middleware") != new.get("middleware"):
            lines.append("middleware changed")
    return lines


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--emit":
        sys.path.insert(0, ROOT)
        sys.path.insert(0, TEST_DIR)
        print(json.dumps(SECTIONS[args[1]][0]()))
        return 0
    if args not in (["--write"], ["--check"]):
        sys.stderr.write(__doc__)
        return 2
    files = _render(_collect())
    stale = {path: text for path, text in files.items() if _read(path) != text}
    if args == ["--check"]:
        if stale:
            sys.stderr.write("stale: %s — run: python3 tests/snapshots.py --write\n" % ", ".join(
                os.path.relpath(path, ROOT) for path in sorted(stale)))
            return 1
        print("generated snapshots are current")
        return 0
    if not stale:
        print("generated snapshots unchanged")
        return 0
    for path, text in sorted(stale.items()):
        for line in _describe_changes(path, _read(path), text):
            print(line)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
