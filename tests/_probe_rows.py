"""The closed request inventory the composed app is probed with, and the probe.

Every row is a (method, path) the repository already promises to answer: the request scenarios in
`scripts/route_inventory_scenarios.py`, the paths both smoke drivers fetch, and the explicit rows
below. `classify()` says which kind of route owns a row, in registration order:

* `native` - a real route of the composition (never the catch-all);
* `terminal-compatibility` - a row declared below that must fall through to the native terminal,
  the catch-all that is physically last (`routers.native_terminal`);
* anything else raises: a row nobody declared fell to the catch-all, or a composition row maps to
  zero or to several routes.

`probe_native_rows()` then drives every non-composition row through the app in-process with the
database and the network unreachable, and fails on an outbound connection attempt (even when the app
swallows the error), on an unexpected server error, and on any change of the known offline 503s.

The route table itself, the counters of this inventory and who owns each table are pinned by the
generated snapshots (`tests/snapshots.py`); this module is where they are computed, so it is a test
helper and not a script. Rows hold a source and an ordinal, never a request body, cookie or token.
"""

from __future__ import annotations

import ast
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import traceback
from urllib.parse import urlsplit

import _env


ROOT = Path(__file__).resolve().parents[1]
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
CATCH_ALL_PATH = "/{full_path:path}"

# Closed inventory. Adding another protocol owner or a new path in one of these owners requires a
# deliberate edit plus the exact-set regression in `test_route_probe.py`.
PROTOCOL_EXCEPTIONS = frozenset({
})

EXPLICIT_NATIVE_ROWS = frozenset({
    ("GET", "/datos"),
    ("GET", "/datos/"),
    ("GET", "/api/demo"),
    ("GET", "/account/demo-access"),
    ("GET", "/admin/demo-doctor-access"),
    ("GET", "/admin/demo-access"),
    ("GET", "/api/whatsapp/webhook"),
    ("POST", "/api/whatsapp/webhook"),
    ("GET", "/api/whatsapp/webhook/"),
    ("POST", "/api/whatsapp/webhook/"),
    ("GET", "/mcp"),
    ("POST", "/mcp"),
    ("DELETE", "/mcp"),
    ("GET", "/api/account/mcp"),
    ("POST", "/api/account/mcp/regenerate"),
    ("POST", "/api/account/mcp/revoke"),
    ("GET", "/mcp/"),
    ("POST", "/mcp/"),
    ("DELETE", "/mcp/"),
    ("GET", "/api/account/mcp/"),
    ("POST", "/api/account/mcp/regenerate/"),
    ("POST", "/api/account/mcp/revoke/"),
    ("POST", "/api/chat/stream"),
    ("POST", "/api/chat/stream/"),
})

# Finite representative terminal rows required by the retirement plan. The dynamic page rows are
# synthetic; asset rows contain no deployment pack data.
TERMINAL_REPRESENTATIVES = frozenset({
    # Double-slash protocol lookalikes are deliberately not aliases. The native terminal owns their
    # stable 404 after the dedicated routers claim only the exact path and one compatibility slash.
    ("GET", "/api/whatsapp/webhook//"),
    ("POST", "/api/whatsapp/webhook//"),
    ("GET", "/mcp//"),
    ("POST", "/mcp//"),
    ("DELETE", "/mcp//"),
    ("GET", "/api/account/mcp//"),
    ("POST", "/api/account/mcp/regenerate//"),
    ("POST", "/api/account/mcp/revoke//"),
    ("POST", "/api/chat/stream//"),
    ("GET", "/fallback-census-specialty"),
    ("GET", "/fallback-census-specialty/fallback-census-doctor"),
    ("GET", "/static/fallback-census.js"),
    ("HEAD", "/static/fallback-census.js"),
    ("GET", "/vendor/fallback-census.js"),
    ("GET", "/webdata/fallback-census.png"),
    ("GET", "/data/fallback-census.png"),
    ("GET", "/fallback-census.css"),
    ("HEAD", "/fallback-census.css"),
    ("GET", "/fallback-census-unknown"),
})

# Source-derived from smoke_prod.py but declared as terminal compatibility: the security smoke
# proves the static policy, not a native application endpoint. Query strings are removed first.
TERMINAL_SMOKE_ROWS = frozenset({
    ("GET", "/asgi_main.py"),
    ("GET", "/.env"),
    ("GET", "/app/core/db.py"),
    ("GET", "/data/ordenes/"),
    ("GET", "/docs/"),
    ("GET", "/static/favicon.svg"),
})

EXPECTED_OFFLINE_503_BODY = b'{"error": "base de datos no disponible"}'
EXPECTED_OFFLINE_503_HTML_SENTINEL = b"No pudimos cargar este turno"
EXPECTED_OFFLINE_503_ROWS = frozenset({
    # Admin password login with no reachable database answers 503 base_datos, not 401 credenciales
    # (97c52240, product owner 2026-09-15): an operator's credentials cannot be judged wrong while the
    # database is down.
    ("POST", "/api/admin/login"),
    ("POST", "/api/admin/passkeys/login-options"),
    ("GET", "/api/intake/t/faketoken"),
    ("POST", "/api/intake/t/faketoken/message"),
    ("POST", "/api/intake/t/faketoken/reopen"),
    ("POST", "/api/account/login"),
    ("GET", "/appointment/offer/token-inexistente"),
    ("POST", "/api/offer/token-inexistente/accept"),
    ("POST", "/api/offer/token-inexistente/reject"),
    ("GET", "/feed/appointments/token-inexistente.ics"),
    ("GET", "/feed/doctor/token-inexistente.ics"),
    ("GET", "/appointment/token-inexistente.ics"),
})
EXPECTED_OFFLINE_503_BODIES = {
    ("POST", "/api/admin/login"):
        b'{"error": "base_datos"}',
    ("POST", "/api/admin/passkeys/login-options"):
        b'{"error": "base_no_disponible"}',
    # The patient account family answers with the TRANSLATED, patient-facing sentence
    # (`account.form.error.database_unavailable`, es by default), not with the internal lowercase
    # marker every other row still carries.
    ("POST", "/api/account/login"):
        '{"error": "Base de datos no disponible."}'.encode("utf-8"),
}
EXPECTED_OFFLINE_503_HTML_ROWS = frozenset({
    ("GET", "/appointment/offer/token-inexistente"),
})

_ENV_KEYS = (
    "DATABASE_URL", "TEST_DATABASE_URL", "TURNOS_ENV_FILE",
    "TURNOS_CONFIG_LOCK", "TURNOS_FAKE_AI", "TURNOS_SKIP_HSI",
    "TURNOS_BASIC_AUTH_USER", "TURNOS_BASIC_AUTH_PASS",
    "TURNOS_ADMIN_SECRET",
    "TURNOS_DEMO_MODE", "ENABLED_MODULES",
    "TURNOS_AUTH_MODE", "TURNOS_RENAPER_PROVIDER",
    "TURNOS_RENAPER_STUB_DEPLOYED", "TURNOS_TEST_IDENTITIES_PATH",
)


class CensusError(RuntimeError):
    """A probe row cannot be proved to belong to the closed inventory."""


class OutboundAttempt(RuntimeError):
    """A probe attempted to open a real network connection."""


@dataclass(frozen=True, order=True)
class Row:
    source: str
    ordinal: int
    method: str
    path: str

    @property
    def row_id(self) -> str:
        return f"{self.source}:{self.ordinal}"


def _read_ast(relative: str) -> ast.Module:
    path = ROOT / relative
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _assignment_map(tree: ast.Module) -> dict[str, ast.AST]:
    result: dict[str, ast.AST] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            result[target.id] = node.value
    return result


def _scenario_tuple(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.Tuple):
        return None
    values = [elt.value if isinstance(elt, ast.Constant) else None for elt in node.elts]
    for index in range(len(values) - 1):
        method, path = values[index:index + 2]
        if method in HTTP_METHODS and isinstance(path, str) and path.startswith("/"):
            return method, path
    return None


def parity_rows() -> list[Row]:
    """Extract every ESCENARIOS* row of `scripts/route_inventory_scenarios.py` without importing it
    (pure literals; a dynamic row fails closed)."""
    tree = _read_ast("scripts/route_inventory_scenarios.py")
    assignments = _assignment_map(tree)
    rows: list[Row] = []
    source_ordinal = 0
    scenario_names = sorted(name for name in assignments if name.startswith("ESCENARIOS"))
    if not scenario_names:
        raise CensusError("route scenarios source has no scenario families")

    for name in scenario_names:
        value = assignments[name]
        parsed: list[tuple[str, str]] = []
        if isinstance(value, ast.List):
            for entry in value.elts:
                row = _scenario_tuple(entry)
                if row is None:
                    raise CensusError(f"route scenario family {name} contains a dynamic row")
                parsed.append(row)
        elif name == "ESCENARIOS_PAGINAS" and isinstance(value, ast.ListComp):
            # The only generated family is GET over one literal tuple of paths, crossed with
            # languages. Route ownership needs each path once.
            if not value.generators:
                raise CensusError("page scenario family has no generator")
            try:
                paths = ast.literal_eval(value.generators[0].iter)
            except (ValueError, TypeError) as exc:
                raise CensusError("page scenario paths are no longer literal") from exc
            if not isinstance(paths, tuple) or not all(
                isinstance(path, str) and path.startswith("/") for path in paths
            ):
                raise CensusError("page scenario paths have an unknown shape")
            parsed.extend(("GET", path) for path in paths)
        else:
            raise CensusError(f"route scenario family {name} has an unknown shape")

        if not parsed:
            raise CensusError(f"route scenario family {name} is empty")
        for method, path in parsed:
            rows.append(Row("route-parity", source_ordinal, method, path))
            source_ordinal += 1
    return rows


def _literal_path_collection(assignments: dict[str, ast.AST], name: str):
    value = assignments.get(name)
    if value is None:
        raise CensusError(f"smoke source is missing {name}")
    try:
        result = ast.literal_eval(value)
    except (ValueError, TypeError) as exc:
        raise CensusError(f"smoke source {name} is no longer literal") from exc
    return result


def smoke_rows() -> list[Row]:
    """Extract both smoke drivers' stable request paths."""
    rows: list[tuple[str, str]] = []

    prod = _read_ast("scripts/smoke_prod.py")
    prod_assignments = _assignment_map(prod)
    security = _literal_path_collection(prod_assignments, "SECURITY")
    pages = _literal_path_collection(prod_assignments, "PAGES_200")
    if not isinstance(security, dict) or not isinstance(pages, list):
        raise CensusError("production smoke path collections have an unknown shape")
    rows.extend(("GET", path) for path in security)
    rows.extend(("GET", path) for path in pages)

    demo = _read_ast("scripts/smoke_demo.py")
    main = next(
        (node for node in demo.body if isinstance(node, ast.FunctionDef) and node.name == "main"),
        None,
    )
    if main is None:
        raise CensusError("demo smoke has no main function")
    for call in (node for node in ast.walk(main) if isinstance(node, ast.Call)):
        if not isinstance(call.func, ast.Name) or call.func.id != "fetch" or len(call.args) < 2:
            continue
        path_node = call.args[1]
        if not isinstance(path_node, ast.Constant) or not isinstance(path_node.value, str):
            raise CensusError("demo smoke contains a non-literal fetch path")
        method = "GET"
        for keyword in call.keywords:
            if keyword.arg == "method":
                if not isinstance(keyword.value, ast.Constant) or keyword.value.value not in HTTP_METHODS:
                    raise CensusError("demo smoke contains a non-literal fetch method")
                method = keyword.value.value
        rows.append((method, path_node.value))

    # Both consistency helpers issue only cache-busted GET / requests. The production PAGES_200
    # already contains /; this set de-duplicates it.
    rows.append(("GET", "/"))
    unique = sorted(set(rows))
    return [Row("smoke", index, method, path) for index, (method, path) in enumerate(unique)]


def composition_rows(payload: dict | None = None) -> list[Row]:
    """Rows of the composed app's route table: the committed inventory unless `payload` (a freshly
    generated one, `tests/snapshots.py`) is given."""
    if payload is None:
        fixture = ROOT / "tests/tier4_composition_inventory.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        raise CensusError("composition inventory has no routes")
    if routes[-1] != {"path": CATCH_ALL_PATH, "methods": sorted(HTTP_METHODS)}:
        raise CensusError("composition catch-all is not the exact final route")
    if any(route.get("path") == CATCH_ALL_PATH for route in routes[:-1]):
        raise CensusError("composition inventory contains an early catch-all")

    rows: list[Row] = []
    ordinal = 0
    for route in routes[:-1]:
        path = route.get("path")
        methods = route.get("methods")
        if not isinstance(path, str) or not isinstance(methods, list) or not methods:
            raise CensusError("composition inventory contains an invalid route")
        for method in methods:
            if method not in HTTP_METHODS:
                raise CensusError("composition inventory contains an unknown method")
            rows.append(Row("composition", ordinal, method, path))
            ordinal += 1
    return rows


def explicit_rows() -> list[Row]:
    pairs = sorted(
        PROTOCOL_EXCEPTIONS | TERMINAL_REPRESENTATIVES | EXPLICIT_NATIVE_ROWS
    )
    return [Row("explicit", index, method, path) for index, (method, path) in enumerate(pairs)]


def all_rows(composition: dict | None = None) -> list[Row]:
    return composition_rows(composition) + parity_rows() + smoke_rows() + explicit_rows()


def flat_routes(routes, acc):
    """Flatten FastAPI's lazy included routers, in registration order."""
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            for candidate in route.effective_candidates():
                resolved = getattr(candidate, "route", None) or candidate
                if hasattr(resolved, "path") and hasattr(resolved, "methods"):
                    acc.append(resolved)
                elif type(resolved).__name__ == "_IncludedRouter":
                    flat_routes([resolved], acc)
                else:
                    raise CensusError(
                        "FastAPI produced an unknown included-route candidate"
                    )
        else:
            acc.append(route)
    return acc


def request_path(raw: str) -> str:
    return urlsplit(raw).path or "/"


def _scope(method: str, path: str) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": request_path(path),
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "headers": [],
        "query_string": b"",
    }


def _first_full_route(app, method: str, path: str):
    from starlette.routing import Match

    for route in app.routes:
        match, _child_scope = route.matches(_scope(method, path))
        if match is Match.FULL:
            return route
    return None


def _is_catch_all(route, app) -> bool:
    return route is app.routes[-1]


def classify(app, row: Row) -> str:
    path = request_path(row.path)
    method_path = (row.method, path)

    if row.source == "composition":
        flat = flat_routes(app.routes, [])
        if not flat or flat[-1].path != CATCH_ALL_PATH:
            raise CensusError("live catch-all is not physically last")
        matches = [
            route for route in flat[:-1]
            if route.path == row.path and row.method in (route.methods or set())
        ]
        if len(matches) != 1:
            raise CensusError(f"{row.row_id} does not map to one native route")
        return "native"

    route = _first_full_route(app, row.method, path)
    if method_path in TERMINAL_REPRESENTATIVES or method_path in TERMINAL_SMOKE_ROWS:
        if route is None or not _is_catch_all(route, app):
            raise CensusError(f"{row.row_id} no longer reaches the native terminal")
        return "terminal-compatibility"
    if route is not None and not _is_catch_all(route, app):
        return "native"
    if method_path in PROTOCOL_EXCEPTIONS:
        return "protocol-exception"
    raise CensusError(f"{row.row_id} reached an unclassified fallback")


def build_report(app, composition: dict | None = None) -> tuple[dict, list[tuple[Row, str]]]:
    """Counters of the inventory (the `census` snapshot section) and every row's class.

    `composition` is a freshly generated route table; without it the committed one is read."""
    rows = all_rows(composition)
    classified = [(row, classify(app, row)) for row in rows]
    by_source = Counter(row.source for row, _category in classified)
    by_category = Counter(category for _row, category in classified)
    unique_by_category = Counter()
    seen = set()
    for row, category in classified:
        key = (row.method, request_path(row.path))
        if key not in seen:
            unique_by_category[category] += 1
            seen.add(key)
    # Keep a literal zero visible once the protocol-exception inventory is closed; omitting the key
    # would make "none remain" less explicit.
    by_category.setdefault("protocol-exception", 0)
    by_category.setdefault("terminal-compatibility", 0)
    unique_by_category.setdefault("protocol-exception", 0)
    unique_by_category.setdefault("terminal-compatibility", 0)
    report = {
        "sources": dict(sorted(by_source.items())),
        "categories": dict(sorted(by_category.items())),
        "unique_categories": dict(sorted(unique_by_category.items())),
    }
    return report, classified


def _reset_loaded_caches():
    config_module = sys.modules.get("app.core.config")
    if config_module is not None:
        config_module._reset_cache_para_tests()
    auth_mode_module = sys.modules.get("app.auth.mode")
    if auth_mode_module is not None:
        auth_mode_module._cache["v"] = None
        auth_mode_module._cache["t"] = 0.0


@contextmanager
def offline_environment():
    """Scrub inherited config before importing the composed application; put it back after."""
    values = dict.fromkeys(_ENV_KEYS)
    values.update({
        "TURNOS_DEMO_MODE": "0",
        "TURNOS_ENV_FILE": "/dev/null",
        "TURNOS_CONFIG_LOCK": "1",
        "TURNOS_FAKE_AI": "1",
        "TURNOS_SKIP_HSI": "1",
        # Passkey option generation signs a short-lived challenge even when no database is
        # available. Keep the probe deterministic and independent of any inherited credential.
        "TURNOS_ADMIN_SECRET": "fallback-census-offline-only",
    })
    try:
        with _env.patched_env(values):
            _reset_loaded_caches()
            yield
    finally:
        _reset_loaded_caches()


def probe_native_rows(app, classified) -> dict:
    """Drive every concrete classified row and fail on hidden server errors.

    Returns the counts plus `requests`, the (method, path) pairs actually sent, in order, so a test
    can compare what was sent with what was meant to be sent."""
    from fastapi.testclient import TestClient
    import socket
    from unittest import mock

    outbound_attempts = []

    def deny_connect(*_args, **_kwargs):
        outbound_attempts.append(True)
        raise OutboundAttempt("network is disabled during the route probe")

    requests = []
    observed_offline_503 = set()
    raised = []

    async def recording_app(scope, receive, send):
        # TestClient(raise_server_exceptions=False) turns an unhandled error into a bare 500; keep
        # what was raised so the failure names it (a plain 500 once appeared on GET /doctors in a
        # loaded run and never reproduced).
        try:
            await app(scope, receive, send)
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised for the server error path
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            raised.append("%s: %s at %s:%s in %s" % (
                type(exc).__name__, str(exc)[:200], os.path.relpath(frame.filename, ROOT),
                frame.lineno, frame.name))
            raise
    candidates = [
        (row, category) for row, category in classified
        if row.source in {"route-parity", "smoke", "explicit"}
    ]
    with (
        offline_environment(),
        mock.patch.object(socket.socket, "connect", new=deny_connect),
        mock.patch.object(socket.socket, "connect_ex", new=deny_connect),
        mock.patch("app.ops.web_runtime.start_background_services"),
        mock.patch("app.ops.web_runtime.stop_app_modules"),
    ):
        with TestClient(
            recording_app,
            client=("127.0.0.1", 12345),
            raise_server_exceptions=False,
        ) as client:
            for row, category in candidates:
                if category not in {"native", "terminal-compatibility"}:
                    raise CensusError(f"{row.row_id} is not probeable")
                outbound_attempts.clear()
                del raised[:]
                kwargs = {"headers": {"Host": "testserver"}, "follow_redirects": False}
                if row.method in {"POST", "PUT", "PATCH"}:
                    kwargs["json"] = {}
                response = client.request(row.method, row.path, **kwargs)
                requests.append((row.method, row.path))
                if outbound_attempts:
                    raise CensusError(f"{row.row_id} attempted outbound network")
                if response.status_code >= 500:
                    key = (row.method, request_path(row.path))
                    if response.status_code != 503 or key not in EXPECTED_OFFLINE_503_ROWS:
                        raise CensusError(
                            f"{row.row_id} returned unexpected server error "
                            f"{response.status_code}"
                            + (f" (raised {raised[-1]})" if raised else " (nothing raised)")
                        )
                    if key in EXPECTED_OFFLINE_503_HTML_ROWS:
                        content_type = response.headers.get("content-type", "")
                        body_matches = (
                            content_type.startswith("text/html")
                            and EXPECTED_OFFLINE_503_HTML_SENTINEL in response.content
                        )
                    else:
                        body_matches = response.content == EXPECTED_OFFLINE_503_BODIES.get(
                            key, EXPECTED_OFFLINE_503_BODY)
                    if not body_matches:
                        raise CensusError(f"{row.row_id} returned unexpected offline 503 body")
                    observed_offline_503.add(key)
    if observed_offline_503 != EXPECTED_OFFLINE_503_ROWS:
        missing = sorted(EXPECTED_OFFLINE_503_ROWS - observed_offline_503)
        raise CensusError(
            f"offline 503 surface changed; missing expected rows: {missing}"
        )
    return {
        "probed": len(requests),
        "expected_offline_503": len(observed_offline_503),
        "requests": requests,
    }
