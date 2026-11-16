"""app_modules.py — module CONTRACT: manifest normalization, discovery,
enable/disable, route ownership, dependencies, lifecycle and hooks
(Phase 6a runtime slice 2026-07-30; Tier 2 module-contract framework
2026-08-05 — docs/roadmap/module-contract-design.md is the BINDING spec
this file implements; folds into docs/modules.md once shipped).

Everything a `modules/<id>/module.json` manifest declares is real, not
documentation: `manage.py mod list`/`GET /api/admin/modules` read it,
routing.py's dispatch() gates every declared route on it, and this file is
also what actually IMPORTS a module's route modules (the loader) instead
of routing.py hand-listing them.

  - `_normalize_manifest()` — the ONE place a v1 manifest (bare-string
    routes/admin_sections, {path,label} nav_entries) becomes the v2 shape
    (routes: [{module, methods}], admin_sections/nav_entries: richer
    dicts, + requires/entrypoint/assets/locales). Every existing field
    keeps its meaning; a v1 manifest stays valid.
  - `list_modules()` — the single discovery function. Sorted by id.
    Every manifest gets an entry, even a broken one (bad JSON, missing
    id, an unknown `requires` target, a dependency cycle, or a route
    module that failed to import) — `status: enabled|disabled|broken` +
    `error` instead of silently vanishing.
  - `route_modules(method)` — the loader half routing.py needs: imports
    (importlib) every REAL route a v2 manifest declares for `method`, in
    deterministic order (sorted module id, manifest order within).
    Modules always REGISTER regardless of enabled/disabled state —
    `route_enabled()` still gates per request, unchanged contract.
  - `enable(id)` / `disable(id)` — same config.guardar()-backed
    read-modify-write as before, now also refusing on an unmet/violated
    `requires` edge (human-readable reason) — `enable()` requires the
    dependency to be both ENABLED and HEALTHY-AND-STARTED, not merely
    named in the config string — and driving live startup()/shutdown()
    for the module's entrypoint (if any).
  - `merge_routes(method, platform_modules)` — the P0 route-order fix
    (Codex monitor review, 2026-08-05): splices every v2 manifest's
    routes into `platform_modules` (routing.py's own platform-core list,
    untouched) at the position each route's `after` anchor names, so the
    built registry reproduces the EXACT pre-Tier-2 literal order
    (git show 347a9f7:routing.py) instead of dumping every app route
    after the platform block. A route with no `after` (any brand-new
    module — zero central wiring needed) appends at the tail, same as
    the original loader contract. See docs/roadmap/module-contract-
    design.md's route-order-preservation section and
    docs/architecture/turnos-manifest.md ("Routes") for the anchor format.
  - `hooks` — a tiny named-hook registry: a module's entrypoint
    registers a callable at startup() (`ctx.register_hook(name, fn)`),
    platform code calls `hooks.call(name, *a, **kw)`, which only invokes
    the callables whose OWNING module is HEALTHY AND ENABLED right now
    (not merely enabled — a module broken mid-startup never fires a
    hook, and startup_one() rolls back any hook a failing startup()
    registered before raising).
  - Lifecycle is transactional (Codex monitor review, 2026-08-05):
    `startup_one()` refuses to start a module already known broken by
    ANY signal (unknown dependency, dependency cycle, or a prior runtime
    failure — not just `_RUNTIME_BROKEN`); `startup_all()`/
    `shutdown_all()` run in topological/reverse-topological order by
    `requires` (not alphabetical) so a dependency is always up before
    its dependent starts, and down only after its dependent stops.
  - `app_router_entries()` / `merge_app_routers()` / `mount_module_routers()`
    (Tier 4 slice 3, docs/roadmap/tier4-replatform-design.md's "Module
    contract adapter") — the SAME contract extended to a second route-
    registration shape: a route entry naming `router_attr` is a FastAPI
    `APIRouter` object on the SAME dotted module, mounted by `app.py`
    (ASGI mode only) instead of ever reaching the reverse-proxy
    catch-all for that path. Per-request enablement is
    `deps.require_module_enabled(module_id)`, not whether the router got
    mounted — mirrors `route_enabled()`'s per-dispatch gate for the legacy
    registry exactly, including "modules always register regardless of
    enabled state."
"""
from __future__ import annotations

import importlib
import json
import os
import re
import stat
import sys
import threading
from app.core import config
from app.core import paths
from app.core import theme
from app.core import i18n
from app.core import log_pii

ROOT = paths.PROJECT_ROOT
# The modules are application code: they live under CODE_ROOT, which is also where a module's
# own `modules/<id>/...` names (assets, locales) resolve.
CODE_ROOT = paths.CODE_ROOT
MODULES_DIR = os.path.join(CODE_ROOT, "modules")


def _code_root():
    """The directory `modules/<id>/<file>` names are relative to."""
    return CODE_ROOT

CONFIG_KEY = "ENABLED_MODULES"

_HTTP_METHODS = ("GET", "POST", "PUT", "DELETE")
_MODULE_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FINGERPRINTABLE_ASSET_RE = re.compile(r"^modules/[A-Za-z_][A-Za-z0-9_]*/[A-Za-z0-9_./-]+\.(?:js|css|mjs)$")


# ============================================================== manifest v1 -> v2

def _normalize_route(entry):
    if isinstance(entry, str):
        return {"module": entry, "methods": list(_HTTP_METHODS), "after": None,
                "router_attr": None}
    if isinstance(entry, dict) and entry.get("module"):
        methods = entry.get("methods") or list(_HTTP_METHODS)
        return {"module": entry["module"], "methods": [str(m).upper() for m in methods],
                "after": entry.get("after"), "router_attr": entry.get("router_attr")}
    raise ValueError(f"ruta inválida en el manifiesto: {entry!r}")


def _resolve_after(after, method):
    """`after` (a route entry's splice anchor) resolved for ONE HTTP
    method: `None` -> tail (today's default, no central wiring needed); a
    bare string -> the same anchor for every method the route registers
    on; a dict -> per-method (several pre-Tier-2 routes sat in DIFFERENT
    relative positions on GET vs POST vs DELETE — chart_summary is the
    clearest example), with an optional "*" key as the dict's own
    default for a method it doesn't name explicitly."""
    if after is None:
        return None
    if isinstance(after, str):
        return after
    if isinstance(after, dict):
        return after.get(method) or after.get("*")
    return None


def _normalize_screen(screen):
    """Tier 5 slice 4 (docs/roadmap/tier5-frontend-design.md, "admin.html
    decomposition into /admin/<screen> — the concrete mechanism"): an
    admin_sections[] entry's optional `screen` object — the ONLY new v2
    field this slice adds, superset-tolerant same as `router_attr` was
    for `routes` entries in Tier 4 slice 3. `None` if the section has no
    server-routed twin yet (today's default, unaffected).

    `module` is an addition to the design doc's own illustrative JSON
    sample (which showed only path/router_attr/template): the sample
    never says HOW the `router_attr` name is resolved to a Python
    object, and every OTHER router_attr consumer in this codebase
    (`routes[].module`, see `_normalize_route()` above) names its module
    explicitly rather than deriving it from a naming convention — kept
    consistent here rather than inventing implicit path->module magic."""
    if screen is None:
        return None
    if not isinstance(screen, dict):
        raise ValueError(f"screen inválido en el manifiesto: {screen!r}")
    for required in ("path", "router_attr", "module"):
        if not screen.get(required):
            raise ValueError(f"screen sin '{required}' en el manifiesto: {screen!r}")
    normalized = {
        "path": screen["path"],
        "router_attr": screen["router_attr"],
        "module": screen["module"],
        "template": screen.get("template"),
        "after": screen.get("after"),
    }
    if screen.get("requires_capability"):
        normalized["requires_capability"] = screen["requires_capability"]
    if screen.get("requires_connector"):
        normalized["requires_connector"] = screen["requires_connector"]
    if screen.get("requires_settings"):
        flags = screen["requires_settings"]
        if not isinstance(flags, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in flags.items()):
            raise ValueError("screen requires_settings must be a string mapping")
        normalized["requires_settings"] = dict(flags)
    return normalized


def _normalize_admin_section(entry):
    if isinstance(entry, str):
        entry = {"id": entry}
    if not isinstance(entry, dict) or not entry.get("id"):
        raise ValueError(f"admin_section inválida en el manifiesto: {entry!r}")
    return {
        "id": entry["id"],
        "label_key": entry.get("label_key"),
        "label": entry.get("label"),
        "roles": entry.get("roles"),
        # Roles that may open the screen but do not get it in the menu
        # («Mis pacientes» for the admin, who has Pacientes: 2026-11-03).
        "nav_hidden_for": entry.get("nav_hidden_for"),
        "js": entry.get("js"),
        "screen": _normalize_screen(entry.get("screen")),
    }


def _normalize_nav_entry(entry):
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        entry = {"path": entry[0], "label": entry[1]}
    if not isinstance(entry, dict) or not entry.get("path"):
        raise ValueError(f"nav_entry inválida en el manifiesto: {entry!r}")
    return {
        "path": entry["path"],
        "label": entry.get("label", ""),
        "label_key": entry.get("label_key"),
        "order": entry.get("order"),
    }


def _normalize_asset(entry):
    """Preserve the v1 string/glob declaration and normalize v2 objects.

    Only an object opting into ``fingerprint`` enters the runtime asset
    registry.  Legacy strings remain declarative ownership metadata so old
    manifests keep their exact meaning.
    """
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict) or set(entry) != {"path", "fingerprint"} or not isinstance(entry.get("path"), str):
        raise ValueError(f"invalid module asset: {entry!r}")
    if entry.get("fingerprint") is not True:
        raise ValueError(f"module asset must opt into fingerprinting: {entry!r}")
    return {"path": entry["path"], "fingerprint": True}


def _normalize_manifest(raw, module_id=None):
    """v1 -> v2, in one place. `raw` is the parsed module.json dict;
    `module_id` is the directory name it was found under (used as a
    fallback / cross-check for `raw["id"]`). Every existing v1 field
    keeps its meaning; new v2 fields default to empty/None so an old
    manifest stays valid untouched. Raises ValueError on a structurally
    broken manifest — callers (list_modules()) catch this and mark the
    module `broken` instead of crashing discovery."""
    if not isinstance(raw, dict):
        raise ValueError("el manifiesto no es un objeto JSON")
    mid = raw.get("id") or module_id
    if not mid:
        raise ValueError("falta 'id' en el manifiesto")
    out = dict(raw)  # preserve *_note / any other documentation-only fields as-is
    out["id"] = mid
    out["name"] = raw.get("name", mid)
    out["description"] = raw.get("description", "")
    out["requires"] = list(raw.get("requires") or [])
    out["entrypoint"] = raw.get("entrypoint") or None
    out["routes"] = [_normalize_route(r) for r in (raw.get("routes") or [])]
    out["admin_sections"] = [_normalize_admin_section(s) for s in (raw.get("admin_sections") or [])]
    out["nav_entries"] = [_normalize_nav_entry(n) for n in (raw.get("nav_entries") or [])]
    raw_assets = raw.get("assets") or []
    if not isinstance(raw_assets, list):
        raise ValueError("module assets must be a JSON list")
    out["assets"] = [_normalize_asset(a) for a in raw_assets]
    out["locales"] = raw.get("locales") or None
    out["db_tables"] = list(raw.get("db_tables") or [])
    out["config_keys_prefixes"] = list(raw.get("config_keys_prefixes") or [])
    return out


def _find_cycle_members(graph):
    """{module_id: [cycle path...]} for every module sitting on a
    `requires` cycle. `graph` is {id: [required_id, ...]}; edges pointing
    outside `graph` are ignored (an unknown `requires` target is a
    SEPARATE error, reported by list_modules() itself, not a cycle)."""
    color = {}  # 0 unvisited (absent), 1 in-progress, 2 done
    result = {}

    def visit(node, stack):
        color[node] = 1
        stack.append(node)
        for dep in graph.get(node, ()) or ():
            if dep not in graph:
                continue
            state = color.get(dep, 0)
            if state == 0:
                visit(dep, stack)
            elif state == 1:
                i = stack.index(dep)
                cycle = stack[i:] + [dep]
                for m in cycle:
                    result.setdefault(m, cycle)
        stack.pop()
        color[node] = 2

    for node in list(graph):
        if color.get(node, 0) == 0:
            visit(node, [])
    return result


# ============================================================== discovery

_RUNTIME_BROKEN = {}  # {module_id: error str} — a route module (or an
# entrypoint) that failed to IMPORT/run, recorded once by route_modules()/
# startup_one() below and merged into list_modules()'s status so it
# survives across calls without re-attempting the (already-failed) import
# every time. Cleared by `enable()` (a fresh enable deserves a fresh try).

_owner_map_cache = None  # memo for _route_owner_map() — same
# check-if-not-None / set-to-None-to-invalidate pattern as config.py's
# _db_cache. route_enabled() is called once per registered route module
# PER REQUEST (routing.dispatch()), and rebuilding it meant a full
# list_modules() (disk listdir + open/json.load per manifest + cycle
# detection) on every single one of those calls (~12ms/request measured).
# The map only reflects what's ON DISK (route ownership never depends on
# enabled/disabled state), so it's invalidated defensively in enable()/
# disable() — not because those change its content today, but so a
# future manifest-mutating path doesn't have to remember to touch a cache
# it doesn't know exists.
#
# 2026-08-05 (Tier 2 monitor review): a prior draft of this memo used
# double-checked locking (read the global unlocked, only take the lock on
# a miss). Investigated as a suspect for a flaky e2e timeout — bisection
# against the CURRENT tree showed the timeout reproduces identically with
# this cache reverted entirely (an unrelated e2e-harness/fixture pack
# mismatch, sibling-owned tests/e2e/** work in flight in the same
# worktree — not this file), so the double-checked pattern was never
# proven wrong. It's simplified anyway: ALWAYS take the lock, one
# `is None` check, no unlocked read. A dict-lookup under an uncontended
# lock is microseconds; correctness-by-inspection beats a clever
# lock-free read this cache never needed the throughput for.
_owner_map_lock = threading.Lock()


def _raw_manifests():
    """[(dirname, normalized_manifest_or_None, error_or_None), ...] for
    every modules/<id>/ directory that has a module.json, sorted by
    directory name — the deterministic discovery order every derived
    ordering downstream builds on."""
    try:
        entries = sorted(os.listdir(MODULES_DIR))
    except OSError:
        return []
    out = []
    for name in entries:
        manifest_path = os.path.join(MODULES_DIR, name, "module.json")
        if not os.path.isfile(manifest_path):
            continue
        if not _MODULE_ID_RE.fullmatch(name):
            out.append((name, None, "module directory id is not a Python-safe identifier"))
            continue
        try:
            with open(manifest_path, encoding="utf-8") as f:
                raw = json.load(f)
            manifest = _normalize_manifest(raw, module_id=name)
            if manifest["id"] != name:
                raise ValueError(
                    f"'id' del manifiesto ({manifest['id']!r}) no coincide "
                    f"con el directorio ({name!r})")
        except Exception as e:  # noqa: BLE001 — a broken manifest never takes discovery down
            out.append((name, None, str(e)))
            continue
        out.append((name, manifest, None))
    return out


def _fingerprinted_asset_error(module_id, asset, seen):
    """Return a fail-closed error for one v2 fingerprinted module asset."""
    path = asset["path"]
    expected_prefix = f"modules/{module_id}/"
    if not _FINGERPRINTABLE_ASSET_RE.fullmatch(path) or not path.startswith(expected_prefix):
        return f"invalid owned module asset path: {path!r}"
    pieces = path.split("/")
    if any(piece in {"", ".", ".."} for piece in pieces):
        return f"unsafe module asset path: {path!r}"
    if path in seen:
        return f"duplicate fingerprinted module asset: {path!r}"
    # Platform assets are registered in the platform fingerprint cohort, never
    # claimed by a module.  Import lazily to keep discovery independent while
    # static_fingerprint imports this registry.
    from app.core import static_fingerprint
    if path in static_fingerprint.MIGRATED:
        return f"module asset overlaps platform fingerprint registry: {path!r}"
    code_root = _code_root()
    disk_path = os.path.join(code_root, path)
    try:
        # Do not let a symlinked parent redirect a declared public asset out
        # of its owning module directory.
        cursor = code_root
        for piece in pieces:
            cursor = os.path.join(cursor, piece)
            parent_info = os.lstat(cursor)
            if stat.S_ISLNK(parent_info.st_mode):
                return f"module asset path contains a symlink: {path!r}"
        info = os.lstat(disk_path)
    except OSError:
        return f"module asset does not exist: {path!r}"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return f"module asset must be a regular non-symlink file: {path!r}"
    seen.add(path)
    return None


def _admin_asset_error(module_id, manifest):
    """Ensure a module's dynamic admin script is declared and owned."""
    fingerprinted = {
        asset["path"] for asset in (manifest.get("assets") or [])
        if isinstance(asset, dict) and asset.get("fingerprint") is True
    }
    prefix = f"modules/{module_id}/"
    for section in manifest.get("admin_sections") or []:
        script = section.get("js")
        if not script:
            continue
        if not isinstance(script, str) or not script.startswith(prefix):
            return f"admin section script is not owned by {module_id!r}: {script!r}"
        if script not in fingerprinted:
            return f"admin section script is not a fingerprinted declared asset: {script!r}"
    return None


def module_asset_paths():
    """Immutable names for all valid fingerprinted module assets.

    This registry intentionally ignores enablement and runtime startup health:
    cached URLs must keep resolving while a module is disabled or temporarily
    runtime-broken.  It derives from structurally valid raw manifests instead
    of ``list_modules()`` so `_RUNTIME_BROKEN` can never make static serving
    depend on whether startup happened before the first fingerprint lookup.
    A malformed or unsafe asset declaration contributes nothing.
    """
    out = set()
    seen = set()
    for _name, manifest, error in _raw_manifests():
        if manifest is None or error is not None:
            continue
        valid = True
        candidate_seen = set(seen)
        for asset in manifest.get("assets") or []:
            if isinstance(asset, dict) and asset.get("fingerprint") is True:
                if _fingerprinted_asset_error(manifest["id"], asset, candidate_seen):
                    valid = False
                    break
        if valid and _admin_asset_error(manifest["id"], manifest):
            valid = False
        if not valid:
            continue
        seen = candidate_seen
        for asset in manifest.get("assets") or []:
            if isinstance(asset, dict) and asset.get("fingerprint") is True:
                out.add(asset["path"])
    return frozenset(out)


def all_ids():
    """Every discovered module id — INCLUDING one that's currently broken
    (it's still a real, known module; broken is a status, not an erasure).
    Only a manifest that failed to even parse an id falls back to its
    directory name (still listed, never silently dropped)."""
    return [name for name, _manifest, _err in _raw_manifests()]


def load_manifest(module_id):
    for name, manifest, _err in _raw_manifests():
        if name == module_id:
            return manifest
    return None


def module_locale_roots():
    """Absolute path of every discovered module's declared `locales` dir
    (docs/modules.md's `locales` field) — BOTH enabled and disabled modules,
    same as routes: a translation catalog is inert data, not a live surface,
    so there's no reason to gate it by enablement the way a route or an
    admin section is gated. A module missing the `locales` field (or one
    that failed to parse at all) contributes nothing. Deterministic order —
    sorted module id, same as every other derived ordering here.

    i18n.py merges `<root>/<lang>/*.json` from each of these into that
    language's catalog, exactly like the central `locales/<lang>/` tree
    (Tier 2 pilot, 2026-08-05 — this was the `locales` field's "declarative
    only, i18n.py doesn't merge module catalog dirs yet" gap the framework
    commit left open on purpose for the pilot to close)."""
    out = []
    for _name, manifest, _err in _raw_manifests():
        if manifest is not None and manifest.get("locales"):
            out.append(os.path.join(_code_root(), manifest["locales"]))
    return out


def list_modules():
    """[manifest, ...] normalized to v2 shape, one entry per
    modules/<id>/ with a module.json — INCLUDING a broken one (bad JSON,
    missing id, an unknown `requires` target, a dependency cycle, or a
    route/entrypoint import failure) so `manage.py mod list` and the
    admin API show it instead of it silently vanishing. Sorted by id.

    `status` precedence: broken beats enabled/disabled — a module listed
    in ENABLED_MODULES that's also broken is still reported broken (its
    routes/nav/sections are absent regardless of what the config key
    says)."""
    raw = _raw_manifests()
    by_id = {name: manifest for name, manifest, err in raw if manifest is not None}
    known_names = {name for name, _m, _e in raw}
    enabled = enabled_ids()

    unknown_dep = {}
    for mid, manifest in by_id.items():
        missing = [r for r in manifest["requires"] if r not in known_names]
        if missing:
            unknown_dep[mid] = missing

    cyclic = _find_cycle_members({mid: m["requires"] for mid, m in by_id.items()})
    asset_errors = {}
    seen_assets = set()
    for mid, manifest in by_id.items():
        for asset in manifest.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            error = _fingerprinted_asset_error(mid, asset, seen_assets)
            if error:
                asset_errors[mid] = error
                break
        if mid not in asset_errors:
            error = _admin_asset_error(mid, manifest)
            if error:
                asset_errors[mid] = error

    out = []
    for name, manifest, err in raw:
        if manifest is None:
            out.append({"id": name, "name": name, "description": "",
                        "requires": [], "entrypoint": None, "routes": [],
                        "admin_sections": [], "nav_entries": [], "assets": [],
                        "locales": None, "db_tables": [], "config_keys_prefixes": [],
                        "status": "broken", "error": err, "enabled": False})
            continue
        mid = manifest["id"]
        entry = dict(manifest)
        if mid in unknown_dep:
            entry["status"] = "broken"
            faltan = sorted(unknown_dep[mid])
            entry["error"] = (("depende de un módulo inexistente: " if len(faltan) == 1
                               else "depende de módulos inexistentes: ") + ", ".join(faltan))
        elif mid in cyclic:
            entry["status"] = "broken"
            entry["error"] = "ciclo de dependencias: " + " -> ".join(cyclic[mid])
        elif mid in asset_errors:
            entry["status"] = "broken"
            entry["error"] = asset_errors[mid]
        elif mid in _RUNTIME_BROKEN:
            entry["status"] = "broken"
            entry["error"] = _RUNTIME_BROKEN[mid]
        else:
            entry["status"] = "enabled" if mid in enabled else "disabled"
            entry["error"] = None
        entry["enabled"] = entry["status"] == "enabled"
        out.append(entry)
    return out


# ============================================================== enabled state

def enabled_ids():
    """The currently enabled module ids (a set), from the raw discovered
    id list (NOT list_modules() — that would recurse back into this
    function to compute each entry's status). UNSET config means every
    discovered module: an installation that never touched
    ENABLED_MODULES behaves exactly like before this key existed. A
    module being in this set does NOT mean it isn't broken — broken-ness
    is a separate, higher-precedence signal (see list_modules()); this
    set is only ever the raw config membership test."""
    raw = config.get(CONFIG_KEY)
    if not raw:
        return set(all_ids())
    return {piece.strip() for piece in raw.split(",") if piece.strip()}


def is_enabled(module_id):
    return module_id in enabled_ids()


def _requires_map():
    return {m["id"]: m.get("requires") or [] for m in list_modules()}


def _broken_ids():
    """{module_id, ...} currently reported `broken` by list_modules() —
    unknown dependency, dependency cycle, OR a prior runtime (import /
    entrypoint) failure. Used by enable() to require a dependency be
    HEALTHY, not merely named in the enabled-config string."""
    return {m["id"] for m in list_modules() if m.get("status") == "broken"}


def enable(module_id):
    """Add `module_id` to the enabled set and persist it. Raises
    ValueError for an unknown id, or when a `requires` dependency isn't
    itself HEALTHY AND STARTED (Codex monitor review, P0, 2026-08-05: a
    dependency merely being NAMED in ENABLED_MODULES used to be enough —
    now it also has to be running for real; no cascading auto-enable —
    explicit only). Each dependency gets one `startup_one()` attempt
    (idempotent — a no-op if it already started) before the health check,
    so a fresh process that never ran startup_all() still gives its
    dependencies a fair chance, same lazy pattern hooks.call()'s
    ensure_started() uses. On success, runs the module's OWN entrypoint
    startup(ctx) live (no restart needed) and clears any stale
    runtime-broken record so a fixed module gets a fresh try."""
    manifest = load_manifest(module_id)
    if not manifest:
        raise ValueError(f"módulo desconocido: {module_id!r}")
    current = enabled_ids()
    broken = _broken_ids()
    unmet = []
    for r in (manifest.get("requires") or []):
        if r not in current:
            unmet.append(r)
            continue
        startup_one(r)  # give it its one chance to actually be running
        if r in broken or r in _RUNTIME_BROKEN or r not in _STARTED:
            unmet.append(r)
    if unmet:
        raise ValueError(
            f"no se puede habilitar {module_id!r}: depende de "
            f"{', '.join(sorted(set(unmet)))}, que no está habilitado y sano")
    current.add(module_id)
    config.guardar({CONFIG_KEY: ",".join(sorted(current))})
    _RUNTIME_BROKEN.pop(module_id, None)
    _invalidate_route_owner_map()
    startup_one(module_id)
    return current


def disable(module_id):
    """Remove `module_id` from the enabled set and persist it. Raises
    ValueError for an unknown id, if `module_id` is the ONLY enabled
    module, or if some OTHER currently-enabled module `requires` it (no
    cascading auto-disable — explicit only, same as enable()). On
    success, runs the module's entrypoint shutdown(ctx) live."""
    manifest = load_manifest(module_id)
    if not manifest:
        raise ValueError(f"módulo desconocido: {module_id!r}")
    current = enabled_ids()
    if module_id not in current:
        return current  # ya estaba deshabilitado — no-op
    dependents = sorted(
        mid for mid, req in _requires_map().items()
        if mid != module_id and mid in current and module_id in req)
    if dependents:
        raise ValueError(
            f"no se puede deshabilitar {module_id!r}: "
            f"{', '.join(dependents)} lo requiere y está habilitado")
    remaining = current - {module_id}
    if not remaining:
        raise ValueError(
            f"no se puede deshabilitar {module_id!r}: quedaría sin ningún "
            "módulo habilitado (el sitio se apagaría por completo)")
    config.guardar({CONFIG_KEY: ",".join(sorted(remaining))})
    _invalidate_route_owner_map()
    shutdown_one(module_id)
    return remaining


# ============================================================== route ownership / loader

def _route_owner_map():
    """{route_module_name: app_module_id} for every route ANY manifest
    declares. A routing.py module name that doesn't appear here is
    platform-core — see route_enabled()."""
    with _owner_map_lock:
        if _owner_map_cache is None:
            owners = {}
            for m in list_modules():
                for route in m.get("routes") or []:
                    owners[route["module"]] = m["id"]
            _set_owner_map_cache(owners)
        return _owner_map_cache


def _set_owner_map_cache(owners):
    global _owner_map_cache
    _owner_map_cache = owners


def _invalidate_route_owner_map():
    """Drop the memoized owner map so the next `_route_owner_map()` call
    rebuilds it from disk. Called from enable()/disable() (see the cache's
    own comment for why, given the map's content doesn't actually depend
    on enabled state)."""
    global _owner_map_cache
    with _owner_map_lock:
        _owner_map_cache = None


def route_enabled(route_name):
    """True if the routing.py module named `route_name` should be
    dispatched right now. A route no manifest claims (platform-core) is
    ALWAYS enabled — module enablement only ever gates a module's OWN
    routes."""
    owner = _route_owner_map().get(route_name)
    if owner is None:
        return True
    return is_enabled(owner)


def _validate_routes(manifest):
    """Imports (importlib) every PLAIN route module a manifest declares —
    ALL of them, regardless of which HTTP method is being asked for, so a
    module broken for ANY one of its declared routes contributes NOTHING
    for ANY method (never a partial registration). Returns
    ({dotted: module_object}, error_str_or_None).

    A route entry that declares `router_attr` (Tier 4 slice 3) is SKIPPED
    here on purpose — this function backs the per-method registry of PLAIN
    route modules (`_base_app_routes()` -> `route_modules()`/
    `merge_routes()`). A `router_attr` entry's dotted module is a
    FastAPI-router-bearing file (see
    modules/intake/interview_patient_router.py's own docstring for why
    it is a SEPARATE file from the domain module); importing it here would
    pull `fastapi` into this loader. Only `app_router_entries()` (below)
    ever imports a `router_attr` module, and only when `app.py` itself is
    loaded."""
    imported = {}
    for route in manifest.get("routes") or []:
        if route.get("router_attr"):
            continue
        dotted = route["module"]
        if dotted in imported:
            continue
        try:
            imported[dotted] = importlib.import_module(dotted)
        except Exception as e:  # noqa: BLE001 — a broken module must never take the platform down
            return imported, f"no se pudo importar {dotted!r}: {e!r}"
    return imported, None


def _base_app_routes(method):
    """[(module_object, after_or_None), ...] for every route ANY v2
    manifest declares for `method`, imported via importlib, in
    deterministic DEFAULT order (sorted module id — list_modules()'s own
    order — then manifest declaration order within). This is the
    tail-append order a route with no `after` anchor gets; merge_routes()
    below is what actually splices an anchored route into position. A
    module whose import fails is marked broken (recorded in
    _RUNTIME_BROKEN, visible via list_modules()) and contributes nothing
    — the platform still boots."""
    out = []
    for manifest in list_modules():
        if manifest.get("status") == "broken":
            continue
        mid = manifest["id"]
        imported, error = _validate_routes(manifest)
        if error:
            _RUNTIME_BROKEN[mid] = error
            # Fail-loud, same doctrine as app_router_entries() below — a
            # module whose legacy route module fails to import used to say
            # nothing on stdout/stderr, only landing in _RUNTIME_BROKEN
            # (visible solely via a manual GET /api/admin/modules).
            print(f"[app_modules] ruta legacy de {mid!r} no se pudo importar: {error}",
                  file=sys.stderr, flush=True)
            continue
        for route in manifest.get("routes") or []:
            if route.get("router_attr"):
                continue  # ASGI-only entry — see _validate_routes()'s docstring
            if method not in route["methods"]:
                continue
            out.append((imported[route["module"]], _resolve_after(route.get("after"), method)))
    return out


def route_modules(method):
    """[module, ...] — every REAL Python module object a v2 manifest
    declares a route for on `method`, in the DEFAULT (tail) order —
    ignores `after` splicing. Modules always REGISTER regardless of
    enabled/disabled state; dispatch()'s app_modules.route_enabled()
    keeps gating per request, unchanged. Use merge_routes() to get the
    actual spliced-into-platform-order registry routing.py serves."""
    return [mod for mod, _after in _base_app_routes(method)]


def merge_routes(method, platform_modules):
    """The FULL effective registry for `method`: `platform_modules`
    (routing.py's own platform-core list for this method, in its
    original relative order, UNTOUCHED) with every app-module route for
    `method` spliced in at the position its manifest declares (route's
    `after` anchor — see docs/roadmap/module-contract-design.md's
    route-order-preservation contract, Codex monitor P0 fix 2026-08-05).

    A route with no `after` (the default — any brand-new module needs
    ZERO central wiring) is appended at the very tail, same behavior the
    loader always had. An `after` anchor that never resolves (typo, or
    the named module isn't in THIS method's platform/app mix at all)
    degrades the same way — tail-appended, never a crash, never silently
    dropped — so a manifest mistake costs an ordering nicety, not an
    outage.

    Algorithm: repeatedly scan the anchored routes, inserting each one
    right after its named anchor as soon as that anchor exists in the
    result so far (a fresh `__name__` scan per item, so an anchor
    resolved EARLIER IN THE SAME PASS is visible to a later item in that
    same pass — this is what lets a whole same-module chain of anchors,
    e.g. admin_patients -> admin_appointments -> reports -> ..., resolve
    in one pass when the manifest declares them in that order). Loops
    until a full pass makes no progress, then tail-appends whatever's
    still unresolved. O(n^2) worst case over ~50 routes — this runs once
    per method at import time, not per request."""
    result = list(platform_modules)
    pending = _base_app_routes(method)
    tail = [mod for mod, after in pending if after is None]
    anchored = [(mod, after) for mod, after in pending if after is not None]

    changed = True
    while anchored and changed:
        changed = False
        still_pending = []
        for mod, after in anchored:
            idx = next((i for i, m in enumerate(result) if m.__name__ == after), None)
            if idx is None:
                still_pending.append((mod, after))
                continue
            result.insert(idx + 1, mod)  # AFTER the anchor, not before it
            changed = True
        anchored = still_pending

    for mod, _after in anchored:  # unresolved anchor -> tail, never dropped
        tail.append(mod)

    result.extend(tail)
    return result


# ============================================================== FastAPI router adapter (Tier 4 slice 3)
#
# docs/roadmap/tier4-replatform-design.md, "Module contract adapter (slice
# 3)": a route entry may ALSO name a `router_attr` — an attribute on the
# SAME dotted `module` (the one `_base_app_routes()`/route_modules()
# import) that is a real FastAPI `APIRouter`. `app.py` mounts that
# file's `router` instead of ever reaching the native terminal for that
# path.
#
# A route entry with no `router_attr` is unaffected — it is served by
# the ASGI native terminal (app.py never sees it as a FastAPI route at
# all) — migrating one route of a module to `router_attr` form is
# additive per-route, never a whole-module cutover.

def _validate_app_routers(manifest):
    """[(dotted, router_attr, router_obj, after), ...] for every route
    entry in `manifest` that declares a `router_attr` — imports the dotted
    module (importlib, same as `_validate_routes()`) and reads the named
    attribute off it. Returns (list, error_or_None); a manifest with no
    router_attr entries at all returns ([], None), same "nothing to do"
    shape `_validate_routes()` has for a routeless manifest."""
    out = []
    for route in manifest.get("routes") or []:
        attr = route.get("router_attr")
        if not attr:
            continue
        dotted = route["module"]
        try:
            mod = importlib.import_module(dotted)
            router = getattr(mod, attr)
        except Exception as e:  # noqa: BLE001 — a broken module must never take the platform down
            return out, f"no se pudo montar router {dotted}.{attr}: {e!r}"
        out.append((dotted, attr, router, route.get("after")))
    return out, None


def _validate_screen_routers(manifest):
    """[(dotted, router_attr, router_obj, after), ...] for every
    `admin_sections[].screen` entry a manifest declares — the SAME shape
    and SAME import-and-getattr mechanism as `_validate_app_routers()`
    above, just reading `admin_sections` instead of `routes` (Tier 5
    slice 4: "admin_sections[].screen.router_attr is mounted by the SAME
    mount_module_routers()/merge_app_routers()", docs/roadmap/
    tier5-frontend-design.md, "Module contract extension"). A manifest
    with no `screen` entries at all returns ([], None), same "nothing to
    do" shape `_validate_app_routers()` has for a screenless manifest."""
    out = []
    for section in manifest.get("admin_sections") or []:
        screen = section.get("screen") if isinstance(section, dict) else None
        if not screen:
            continue
        dotted = screen["module"]
        attr = screen["router_attr"]
        try:
            mod = importlib.import_module(dotted)
            router = getattr(mod, attr)
        except Exception as e:  # noqa: BLE001 — a broken module must never take the platform down
            return out, f"no se pudo montar screen router {dotted}.{attr}: {e!r}"
        out.append((dotted, attr, router, screen.get("after")))
    return out, None


def app_router_entries():
    """[(module_id, router_obj, after), ...] for every `router_attr` route
    ANY v2 manifest declares — ENABLED OR DISABLED (mounted unconditionally
    at FastAPI-app-build time, same "modules always REGISTER" contract
    `route_modules()` already has for the legacy registry; per-request
    gating is `deps.require_module_enabled(module_id)`, not whether the
    router got mounted at all — mirrors how a disabled module's legacy
    route module still gets imported into the registry and is gated at
    DISPATCH time by `route_enabled()`, never at import time).

    Tier 5 slice 4: this now also gathers `admin_sections[].screen`
    routers (`_validate_screen_routers()`) into the SAME list — a screen
    router is a FastAPI router like any other, the only thing new about
    it is that its handler calls `TemplateResponse` instead of returning
    JSON (docs/roadmap/tier5-frontend-design.md, "Module contract
    extension"); it is mounted, ordered and gated exactly the same way,
    with no second registry.

    A module whose router fails to import/resolve is marked broken
    (`_RUNTIME_BROKEN`, visible via `list_modules()`) and contributes
    NOTHING — `app.py`'s `mount_module_routers()` still boots every other
    module's router (broken-module boot survival, same guarantee
    `_base_app_routes()` gives the legacy registry). Deterministic order:
    sorted module id (list_modules()'s own order), manifest declaration
    order within a module (routes before screens)."""
    out = []
    for manifest in list_modules():
        if manifest.get("status") == "broken":
            continue
        mid = manifest["id"]
        routers, error = _validate_app_routers(manifest)
        if error:
            _RUNTIME_BROKEN[mid] = error
            # Fail-loud (repo doctrine, CLAUDE.md "Output discipline"): this
            # used to ONLY land in `_RUNTIME_BROKEN`, visible solely through
            # a manual GET /api/admin/modules — a router_attr string left
            # pointing at a module that moved (e.g. a Tier 2 physical-
            # modularization batch) mounted nothing and said nothing on
            # stdout/stderr, so `app.py`'s ASGI surface for that module
            # silently degraded with no signal in the server log. Print so
            # it shows up the same way every other broken-module signal in
            # this file does (see hooks.call()'s own stderr print above).
            print(f"[app_modules] router de {mid!r} no se pudo montar: {error}",
                  file=sys.stderr, flush=True)
            continue
        screens, error = _validate_screen_routers(manifest)
        if error:
            _RUNTIME_BROKEN[mid] = error
            print(f"[app_modules] screen router de {mid!r} no se pudo montar: {error}",
                  file=sys.stderr, flush=True)
            continue
        for _dotted, _attr, router, after in routers + screens:
            out.append((mid, router, after))
    return out


def merge_app_routers():
    """The mount ORDER `app.py` should `include_router()` every app-module
    router in — same splice ALGORITHM as `merge_routes()` (repeated passes,
    insert right after a resolved anchor, tail-append what never resolves),
    deliberately scoped NARROWER: an `after` anchor here resolves against
    ANOTHER APP-MODULE's id, never against a platform-core route module
    name the way a legacy `after` anchor does.

    Why the narrower scope is still the RIGHT answer, not a shortcut: once
    a route is mounted as a real FastAPI router, it claims its path
    directly, ahead of the native terminal (app.py's module
    docstring) — so its position relative to `routing.py`'s own
    PLATFORM_LIST (what a legacy `after` anchor is a position IN) is moot,
    that list is only ever consulted by the reverse-proxy's legacy target,
    which this path no longer reaches at all. The one ordering question
    that survives the migration for real is app-module router vs.
    app-module router — two different modules' routers both wanting an
    overlapping path — and that's exactly what this function resolves,
    with the identical algorithm `merge_routes()` already uses, not a
    second ordering scheme. A router with no `after` (the default) is
    appended at the tail, same "zero central wiring" contract every other
    default in this file has."""
    entries = app_router_entries()
    result = []
    tail = [(mid, r) for mid, r, after in entries if after is None]
    anchored = [(mid, r, after) for mid, r, after in entries if after is not None]

    changed = True
    while anchored and changed:
        changed = False
        still_pending = []
        for mid, r, after in anchored:
            idx = next((i for i, (m, _r) in enumerate(result) if m == after), None)
            if idx is None:
                still_pending.append((mid, r, after))
                continue
            result.insert(idx + 1, (mid, r))
            changed = True
        anchored = still_pending

    for mid, r, _after in anchored:  # unresolved anchor -> tail, never dropped
        tail.append((mid, r))

    result.extend(tail)
    return result


def mount_module_routers(app):
    """Called ONCE by `app.py`, right before the native terminal is
    registered — mounts every module's `router_attr` router, in
    `merge_app_routers()` order. This is the "zero central wiring" contract
    for the ASGI path: a brand-new module that declares `router_attr`
    routes needs NO `app.py` edit — `app.py`'s own (one-time) call to this
    function already picks it up, same as `routing.py` never needing an
    edit for a new module's plain routes (`route_modules()`/
    `merge_routes()` do the equivalent job for the legacy registry)."""
    for _mid, router in merge_app_routers():
        app.include_router(router)


# ============================================================== hooks + lifecycle

class _ModuleContext:
    """Handed to a module's entrypoint startup(ctx)/shutdown(ctx). `db`
    and `config` are the platform's own modules (a module uses them the
    same way platform code does — this framework slice doesn't hand out
    a separate connection); `register_hook` binds the callable to THIS
    module's id so hooks.call()'s live enabled-check applies to it."""

    def __init__(self, module_id):
        self.module_id = module_id
        self.config = config
        try:
            from app.core import db as _db
            self.db = _db
        except Exception:  # noqa: BLE001
            self.db = None

    def register_hook(self, name, fn):
        hooks.register(self.module_id, name, fn)


class _Hooks:
    """A tiny named-hook registry: modules register callables at
    startup(); platform code calls `call(name, *a, **kw)`, which invokes
    only the callables whose owning module is HEALTHY AND ENABLED right
    now (Codex monitor review, P1, 2026-08-05: `is_enabled()` alone let a
    module broken mid-startup keep firing a hook it registered before
    raising — see `_is_healthy()`) and swallows any exception a callable
    raises (a hook must never break its caller, same "best-effort"
    contract as notifications.py)."""

    def __init__(self):
        self._registry = {}  # {name: [(module_id, fn), ...]}

    def register(self, module_id, name, fn):
        """Idempotent per (module_id, name): a SECOND registration from the
        same module for the same hook name REPLACES the first instead of
        appending. This matters in practice, not just in tests — a module's
        startup(ctx) is expected to register the same thing every time it
        runs, and it legitimately runs more than once per process (an
        enable() -> disable() -> enable() cycle calls startup_one() again
        each time `_STARTED` was cleared by the disable). Without this, that
        cycle would silently accumulate duplicate callables and fire a hook
        (e.g. an email reminder) more than once per event."""
        lst = self._registry.setdefault(name, [])
        lst[:] = [(mid, f) for mid, f in lst if mid != module_id]
        lst.append((module_id, fn))

    def unregister_module(self, module_id):
        """Drop EVERY hook `module_id` ever registered, under every hook
        name — called by startup_one() when that module's startup() fails
        AFTER already registering one (Codex monitor review, P1,
        2026-08-05: "partial registrations roll back"). Also correct
        defense-in-depth beyond call()'s own health check: a broken
        module simply has no live hook entries at all, not just skipped
        ones."""
        for name, lst in list(self._registry.items()):
            lst[:] = [(mid, f) for mid, f in lst if mid != module_id]

    def call(self, name, *args, **kwargs):
        ensure_started()
        out = []
        for module_id, fn in list(self._registry.get(name, ())):
            if not _is_healthy(module_id):
                continue
            try:
                out.append(fn(*args, **kwargs))
            except Exception as e:  # noqa: BLE001
                print(f"[app_modules] hook {name!r} de {module_id!r} falló: {log_pii.exc(e)}",
                      file=sys.stderr, flush=True)
        return out

    def clear(self):
        """Test-only: wipe every registration between cases (mirrors
        config._reset_cache_para_tests()'s role for the config layer)."""
        self._registry.clear()


hooks = _Hooks()

_STARTED = set()  # module ids whose entrypoint startup(ctx) already ran


def _is_broken(module_id):
    """True if list_modules() currently reports `module_id` broken —
    unknown dependency, a dependency cycle, OR a prior runtime (import /
    entrypoint) failure. A single source of truth so startup_one()/
    _is_healthy() never special-case just `_RUNTIME_BROKEN` (Codex
    monitor review, P1, 2026-08-05: "broken modules can still execute
    lifecycle/hooks" — the bug was checking only `_RUNTIME_BROKEN` here,
    missing the unknown-dependency/cycle cases list_modules() already
    tracks)."""
    for m in list_modules():
        if m["id"] == module_id:
            return m.get("status") == "broken"
    return True  # unknown id -> treat as broken, never as healthy


def _is_healthy(module_id):
    """True if `module_id` is enabled, not broken by ANY signal, AND has
    actually completed a successful startup (or has no entrypoint at
    all, which is trivially "started"). This is the "healthy AND
    enabled" bar hooks.call() and enable()'s dependency check both use —
    plain `is_enabled()` was never enough (Codex monitor review, P1)."""
    if not is_enabled(module_id):
        return False
    if module_id in _RUNTIME_BROKEN:
        return False
    if _is_broken(module_id):
        return False
    return module_id in _STARTED


def startup_one(module_id):
    """Runs `entrypoint.startup(ctx)` for one module, if it declares one
    — a no-op (True) for a module without an entrypoint, or one already
    started. Refuses outright if the module is ALREADY known broken by
    any signal (`_is_broken()` — unknown dependency, a cycle, or a prior
    runtime failure), not just a stale `_RUNTIME_BROKEN` entry (Codex
    monitor review, P1, 2026-08-05). If `startup()` registers a hook and
    THEN raises, the registration is rolled back (`hooks.
    unregister_module()`) before the module is marked broken — a failed
    startup never leaves a live, callable hook behind."""
    manifest = load_manifest(module_id)
    if not manifest:
        return False
    if module_id in _RUNTIME_BROKEN or _is_broken(module_id):
        return False
    if module_id in _STARTED:
        return True
    ep = manifest.get("entrypoint")
    if not ep:
        _STARTED.add(module_id)
        return True
    try:
        mod = importlib.import_module(ep)
        fn = getattr(mod, "startup", None)
        if fn:
            fn(_ModuleContext(module_id))
        _STARTED.add(module_id)
        return True
    except Exception as e:  # noqa: BLE001
        hooks.unregister_module(module_id)  # roll back any partial registration
        _RUNTIME_BROKEN[module_id] = f"entrypoint.startup falló: {e!r}"
        return False


def shutdown_one(module_id):
    """Runs `entrypoint.shutdown(ctx)` for one module, if it declares one
    and it was started. Always clears the started flag, even on failure
    — a failed shutdown must not wedge a future re-enable. Also drops any
    hook the module registered — a stopped module has no business
    keeping a live callback around."""
    manifest = load_manifest(module_id)
    if not manifest:
        return False
    ep = manifest.get("entrypoint")
    if not ep or module_id not in _STARTED:
        _STARTED.discard(module_id)
        hooks.unregister_module(module_id)
        return True
    try:
        mod = importlib.import_module(ep)
        fn = getattr(mod, "shutdown", None)
        if fn:
            fn(_ModuleContext(module_id))
    except Exception as e:  # noqa: BLE001
        _RUNTIME_BROKEN[module_id] = f"entrypoint.shutdown falló: {e!r}"
    finally:
        _STARTED.discard(module_id)
        hooks.unregister_module(module_id)
    return True


def _topo_order(ids):
    """`ids` (an iterable of module ids), ordered so every module's
    `requires` (restricted to OTHER ids in this same set — a dependency
    outside it isn't this function's concern) comes before it — DFS
    postorder, deterministic (ids visited in sorted order) so ties don't
    depend on dict/set iteration order. Used by startup_all() (forward)
    and shutdown_all() (reversed) instead of the old alphabetical
    ordering (Codex monitor review, P1, 2026-08-05: "lifecycle startup is
    alphabetical, not topological by requires"). Assumes no cycles among
    `ids` — a real cycle is caught and marked broken upstream
    (list_modules()/_find_cycle_members()), so a module on a cycle never
    reaches here as "enabled and healthy" in the first place."""
    id_set = set(ids)
    reqs = {mid: [r for r in (load_manifest(mid) or {}).get("requires", []) if r in id_set]
            for mid in id_set}
    visited = set()
    order = []

    def visit(mid):
        if mid in visited:
            return
        visited.add(mid)
        for dep in reqs.get(mid, ()):
            visit(dep)
        order.append(mid)

    for mid in sorted(id_set):
        visit(mid)
    return order


def startup_all():
    """Called once at server boot, after platform init (server.py's
    main()) — every currently enabled AND HEALTHY module with an
    entrypoint gets its startup(ctx), in TOPOLOGICAL order by `requires`
    (a dependency always starts before its dependent — Codex monitor
    review, P1, 2026-08-05; was alphabetical). A module already reported
    `broken` (unknown dependency, a cycle, or a prior runtime failure)
    never gets a startup attempt at all. Idempotent (safe to call more
    than once — e.g. a test or hooks.call()'s own lazy ensure_started()
    may already have run it)."""
    healthy_enabled = [m["id"] for m in list_modules() if m.get("status") == "enabled"]
    for mid in _topo_order(healthy_enabled):
        startup_one(mid)


def ensure_started():
    """Lazy version of startup_all() — hooks.call() runs this before
    every dispatch so a hook fires correctly even in a process that never
    called server.py's main() (every test, notably), with no ordering
    dependency on WHEN main() happens to run. Cheap after the first call
    (startup_one() no-ops on an already-started id)."""
    startup_all()


def shutdown_all():
    """Called at server stop — every module that was started gets its
    shutdown(ctx), in REVERSE topological order by `requires` (a
    dependent always stops before its dependency — Codex monitor review,
    P1, 2026-08-05; was alphabetical)."""
    for mid in reversed(_topo_order(list(_STARTED))):
        shutdown_one(mid)


# ============================================================== manifest-derived UI data
#
# What an enabled module contributes to the admin nav / sibling-router hide
# lists, and the patient-site nav. A broken module contributes NOTHING,
# same as a disabled one.

_PLATFORM_ADMIN_SECTIONS = frozenset({
    "usuarios", "cuenta", "settings", "sistema", "configuracion", "plan",
    # Los textos legales del sitio (legal.py) no son de ningún app module.
    "legal",
})

# De las platform-core de arriba, "cuenta" (Mi cuenta) es la única que
# admin.html NO oculta por rol (applyRol() no la toca) -- cualquier sesión
# autenticada edita SU propia cuenta. El resto (usuarios/settings/sistema/
# configuracion/plan/legal) es esAdmin-only ahí, así que sections_payload_
# for_role() usa el mismo criterio.
_PLATFORM_ADMIN_SECTIONS_UNIVERSAL = frozenset({"cuenta"})

# Platform-core direct-screen twins. Module-owned screens declare the same
# relationship in module.json; platform sections have no manifest, so this
# small registry is their one equivalent source of truth. Keep the payload
# shape above unchanged: the legacy shell still owns these static entries.
_PLATFORM_ADMIN_SCREEN_PATHS = {
    "configuracion": "/admin/appearance",
    "cuenta": "/admin/account",
    "legal": "/admin/legal",
    "login": "/admin/login",
    "plan": "/admin/plan",
    "settings": "/admin/settings",
    "sistema": "/admin/system",
    "usuarios": "/admin/users",
}


def platform_admin_screen_path(section_id):
    """Return the direct URL for a platform admin section, if it has one."""
    return _PLATFORM_ADMIN_SCREEN_PATHS.get(section_id)


def enabled_admin_sections():
    """Hash fragments (`#<section>`) that should be reachable right now:
    every ENABLED (not broken) module's admin_sections ids, plus
    platform-core (always reachable). Rol-agnostic — the FULL admin view
    (`manage.py mod list` and similar tooling); GET /api/admin/modules
    itself now serves the role-FILTERED `sections_payload_for_role()`
    below (Codex monitor review, P1, 2026-08-05)."""
    out = set(_PLATFORM_ADMIN_SECTIONS)
    for m in list_modules():
        if m.get("status") == "enabled":
            out.update(s["id"] for s in (m.get("admin_sections") or [])
                       if _section_connector_available(s))
    return sorted(out)


def _section_allowed_for_role(section, rol):
    """A v2 admin_section entry declaring `roles` opts specific non-admin
    roles in (e.g. turnos' "mis-turnos"/"mis-pacientes" for `medico` —
    see docs/architecture/turnos-manifest.md ("Admin sections"), transcribed
    from admin.html's own applyRol() visibility matrix). A section with
    no `roles` at all keeps today's REAL default from that same matrix:
    admin-only — almost every screen (servicios/centros/médicos/reportes/
    settings/...) is `esAdmin` there, not "admin + some generic operator
    role" (this codebase's real roles are admin/medico/admisionista/
    reportes/marca — see admin_users._ROLES — there is no distinct
    "operador" role value, despite some code comments using the word
    loosely for "admin")."""
    roles = section.get("roles")
    if not roles:
        return rol == "admin"
    return rol in roles


def _section_connector_available(section):
    """Whether a connector-owned admin surface exists in this installation.

    The manifest expresses the capability boundary; the connector registry is
    the single runtime authority.  Invalid, off, or differently selected
    connectors fail closed, so vendor UI cannot survive a disabled boundary.
    """
    screen = section.get("screen") or {}
    flags = screen.get("requires_settings") or {}
    if flags:
        from app.core import config
        if any(config.get(key, "0") != expected for key, expected in flags.items()):
            return False
    capability = screen.get("requires_capability")
    if not capability:
        return True
    try:
        from app import connectors
        selected, _source = connectors.resolve(capability)
    except Exception:  # noqa: BLE001 - navigation must fail closed
        return False
    required = screen.get("requires_connector")
    return bool(selected) and (not required or selected == required)


def _project_admin_sections(manifest):
    """Return API-safe section objects with declared module scripts hashed."""
    fingerprinted = {
        a["path"] for a in (manifest.get("assets") or [])
        if isinstance(a, dict) and a.get("fingerprint") is True
    }
    out = []
    for section in manifest.get("admin_sections") or []:
        item = dict(section)
        js = item.get("js")
        if js in fingerprinted:
            from app.core import static_fingerprint
            try:
                item["js"] = static_fingerprint.module_url(js)
            except (OSError, ValueError):
                # Never leak a raw module path if its immutable registration
                # cannot be resolved. Keep the section metadata diagnostic.
                item.pop("js", None)
        out.append(item)
    return out


def sections_payload_for_role(rol, permissions=None):
    """The `sections` field GET /api/admin/modules actually serves
    (Codex monitor review, P0/P1, 2026-08-05): a MIXED list — platform-
    core sections as bare id strings (admin.html already owns their
    nav markup/label statically, nothing to render from data), and
    every ENABLED module's admin_sections as FULL v2 dict entries
    (id/label/label_key/roles/js/screen — the rich shape
    admin-sections.js's normalize() already knows how to read, but the
    backend used to flatten to bare ids before this fix, so the `js`/
    `label` fields never actually reached the client). Filtered to what
    `rol` may see — this is what lets a `medico` session fetch the
    module-derived sections it's allowed to see instead of needing
    `solo_admin=True` (which blocked it outright).

    When `permissions` is None, use the gate-bound effective permissions
    if `admin_auth.active_role() == rol`, else preserve legacy role behavior.
    When `permissions` is provided, a platform section is visible iff
    the permission is held or ANY, and a module section's screen path's
    permission must be held (UNMAPPED -> legacy rule) and the module's
    roles restriction must pass (if it excludes "admin", rol must be in it)."""
    from app.core import permissions as perms_module
    from app.auth import admin_auth

    if permissions is None and admin_auth.active_role() == rol:
        permissions = frozenset(admin_auth.active_permissions())

    def _section_visible(section_path):
        """True if section_path's permission is visible to this role."""
        if permissions is None:
            return True
        perm = perms_module.required_permission("GET", section_path)
        if perm == perms_module.ANY:
            return True
        if perm == perms_module.UNMAPPED:
            # Fall back to legacy rule
            return perms_module.legacy_denial(rol) is None
        return perms_module.holds(perm, permissions)

    out = []
    for name in sorted(_PLATFORM_ADMIN_SECTIONS):
        # The launch roadmap is the Pinamar reference deployment's operating
        # plan, not generic Human Rounds product navigation.  Keep this at
        # the shared payload boundary so both server-rendered and legacy
        # sidebars omit it on every other theme.
        if name == "plan" and theme.current() != "pinamar":
            continue
        is_universal = name in _PLATFORM_ADMIN_SECTIONS_UNIVERSAL
        is_admin_only = name not in _PLATFORM_ADMIN_SECTIONS_UNIVERSAL

        # Check permission visibility for platform sections
        section_path = _PLATFORM_ADMIN_SCREEN_PATHS.get(name)
        if is_universal:
            out.append(name)
        elif permissions is not None:
            # Permission-aware: check if this section's path is visible
            if section_path and _section_visible(section_path):
                out.append(name)
            elif not section_path:
                # Fallback: admin only
                if rol == "admin":
                    out.append(name)
        else:
            # Legacy behavior
            if is_admin_only and rol != "admin":
                continue
            out.append(name)

    for m in list_modules():
        if m.get("status") != "enabled":
            continue
        for s in _project_admin_sections(m):
            if not _section_connector_available(s):
                continue
            if permissions is None:
                if not _section_allowed_for_role(s, rol):
                    continue
            else:
                # Admin-capable screens follow the permission catalog, including
                # custom roles. Doctor-only workspaces still require that role.
                roles = s.get("roles")
                if roles and "admin" not in roles and rol not in roles:
                    continue
                section_path = (s.get("screen") or {}).get("path")
                if not section_path or not _section_visible(section_path):
                    continue
            out.append(s)
    return out


def enabled_nav_entries():
    """{path, label, label_key, order} patient-site nav entries for every
    ENABLED (not broken) module, in manifest order (module order, then
    declaration order within a module — deterministic, not
    set-scrambled)."""
    out = []
    for m in list_modules():
        if m.get("status") == "enabled":
            out.extend(m.get("nav_entries") or [])
    return out


def nav_entries_for_lang(lang=None):
    """`enabled_nav_entries()`, i18n-resolved (label_key -> i18n.t_or(),
    Spanish `label` as the fallback) and ordered: an explicit `order`
    first (ties broken by declaration position — Python's sort is
    stable), entries with no `order` keep their declared relative
    position after the ordered ones. This is what GET /api/nav serves."""
    entries = enabled_nav_entries()
    lang = lang or i18n.active()

    def sort_key(pair):
        idx, entry = pair
        order = entry.get("order")
        return (0, order, idx) if order is not None else (1, 0, idx)

    ordered = sorted(enumerate(entries), key=sort_key)
    out = []
    for _idx, entry in ordered:
        label = entry.get("label") or entry["path"]
        if entry.get("label_key"):
            label = i18n.t_or(entry["label_key"], label, lang)
        out.append({"path": entry["path"], "label": label})
    return out


def modules_status():
    """[{"id", "name", "enabled", "status", "error", "description",
    "requires", "admin_sections"}, ...] — the shape `GET /api/admin/modules`
    and `manage.py mod list` both want. `admin_sections` is passed through
    in its full v2 shape (id + label_key/label/roles/js/screen) so a
    future admin UI can derive its sidebar from this response alone."""
    return [
        {
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "enabled": m.get("status") == "enabled",
            "status": m.get("status", "enabled"),
            "error": m.get("error"),
            "description": m.get("description", ""),
            "requires": m.get("requires", []),
            "admin_sections": _project_admin_sections(m),
        }
        for m in list_modules()
    ]


# ============================================================== HTTP
#
# GET /api/admin/modules — manifest-derived module status + the sections/
# nav the admin panel would need to hide/show if it consumed this
# dynamically. Read-only; platform-core (never itself gated by module
# enablement — this endpoint has to work even with every app module
# disabled, or an operator could never see enough to re-enable one).
#
# Auth (Codex monitor review, P1, 2026-08-05): this used to be
# `solo_admin=True`, which blocks EVERY non-admin role outright — the new
# admin shell needs the section registry (its data, not its enable/
# disable controls, which live only in `modules`) for a `medico` session
# too, since the shell's very first dispatch waits on this fetch. Fix:
# any authenticated panel role may call it (`permitir_medico=True`
# relaxes the one flag that was blocking it); the RESPONSE is what's
# filtered — `sections`/`nav_entries` come back role-appropriate
# (sections_payload_for_role()), and the full `modules` status/error/
# requires list (the actual enable/disable-relevant data) stays admin-
# only. A medico session therefore gets exactly the sections its role is
# allowed to see, and nothing about module health/dependencies.
#
# GET /api/nav — PUBLIC, platform-core: enabled modules' nav_entries,
# i18n-resolved for the requesting visitor's language, merged and
# ordered. nav.js (a sibling concern, not owned by this file) is the
# eventual consumer; this endpoint exists independent of that.
