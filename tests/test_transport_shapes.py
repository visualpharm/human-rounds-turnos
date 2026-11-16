"""No route may be served by the retired `handle(handler, method)` transport, under any name.

The pre-refactor topology (`server.py`, `legacy_shim.py`, the dispatcher, 54 `handle()` entrypoints)
is deleted, and `test_asgi_main_dark_mode_retirement.test_legacy_topology_files_stay_deleted` pins
that the files stay deleted. This file pins the rest, three ways, so that bringing a handler back
fails here whether it returns under its old name, a new name, or an indirection:

1. the production source has no retired-transport shape (`tests/_transport_shapes.py`: a `handle`
   entrypoint, a retired module imported by any means, the old response writer or a fabricated
   request rebuilt under another name);
2. the detector itself is tested on samples that must be found (renamed, local, dynamic) and on
   real HTTP/Starlette code that must not;
3. the composed app and its live routes show no trace: no retired module loaded, every route has
   methods and a callable endpoint, no endpoint or dependency (or anything it names directly)
   comes from a retired module, no module that owns an endpoint exposes a `handle`, and a walk of
   the live objects from every endpoint and dependency (globals, closures, nested functions,
   classes, `module.attr` hops; `_reachable_functions`) reaches no `handle`, no retired module and
   no name bound to one. That walk is the reachability the old census had, and it needs no
   particular source shape.

Limit: the walk follows names, not values, so a callable fetched by a computed string or held in
a data structure is not followed; arbitrary dead source is not looked at.
"""

import functools
import inspect
import json
import pathlib
import sys
import unittest

import _probe_rows as probe
import _transport_shapes as shapes


def _codes(source, name="sample.py"):
    return [item["code"] for item in shapes.findings_from_source(source, name)]


class ProductionSourceTest(unittest.TestCase):
    def test_production_source_has_no_retired_transport_shape(self):
        files = list(shapes.production_python_files())
        self.assertGreater(len(files), 200)  # the walk really sees the code base
        self.assertEqual(shapes.production_findings(files), [])

    def test_unparsable_production_source_fails_loud(self):
        with self.assertRaises(shapes.ShapeError):
            shapes.findings_from_source("def broken(:\n", "broken.py")


class DetectorTest(unittest.TestCase):
    def test_renamed_and_local_writer_shapes_are_rejected(self):
        self.assertIn("legacy-response-writer", _codes("""
def factory():
    class InnocentTransport:
        def send_response(self, status): pass
        def send_header(self, name, value): pass
        def end_headers(self): pass
    return InnocentTransport()
"""))

    def test_fabricated_request_clone_is_rejected_by_shape(self):
        self.assertIn("fabricated-request-clone", _codes("""
class Envelope:
    def __init__(self):
        self.command = "POST"
        self.path = "/mcp"
        self.headers = {}
        self.client_address = ("local", 0)
        self._body = b"{}"
    def _read_json(self): return {}
"""))

    def test_two_field_request_clone_is_rejected_without_reader_methods(self):
        self.assertIn("fabricated-request-clone", _codes("""
class Peer:
    def __init__(self, headers, client_address):
        self.headers = headers
        self.client_address = client_address
"""))

    def test_dynamic_writer_constructions_and_mutations_are_rejected(self):
        codes = _codes("""
import types
methods = {
    "send_response": lambda self, status: None,
    "send_header": lambda self, name, value: None,
    "end_headers": lambda self: None,
}
Dynamic = type("Dynamic", (), methods)
holder = types.SimpleNamespace(_send_json=lambda value: None, status=0)
probe = object()
setattr(probe, "send_response", lambda status: None)
probe.send_header = lambda name, value: None
probe.end_headers = lambda: None
sink = object()
sink.wfile.write(b"legacy")
""")
        self.assertGreaterEqual(codes.count("legacy-response-writer"), 2)
        self.assertIn("legacy-json-writer", codes)
        self.assertIn("legacy-wfile-writer", codes)

    def test_send_json_call_is_a_writer_without_extra_recorder_state(self):
        self.assertEqual(_codes("""
def answer(transport):
    transport._send_json({"ok": True})
"""), ["legacy-json-writer"])

    def test_direct_dynamic_and_mapped_handle_paths_are_rejected(self):
        # `handle` reached by a constant that only folds to the name, a mapping, or a closure
        # over the owner module (the old `route_via_handle(module, "handle")`).
        self.assertTrue({
            "legacy-dynamic-import",
            "legacy-handle-definition",
            "legacy-handle-reference",
            "legacy-handle-dynamic-reference",
            "legacy-handle-dynamic-surface",
        } <= set(_codes("""
import importlib
owner_name = "legacy_shim"
entry_name = "handle"
owner = importlib.import_module(owner_name)
def handle(request): return None
routes = {"POST": owner.handle}
getattr(owner, entry_name)(None)
setattr(owner, "han" + "dle", lambda request: None)
DynamicOwner = type("DynamicOwner", (), {"handle": lambda self: None})
""")))
        self.assertIn("legacy-handle-dynamic-reference", _codes("""
attr_name = "handle"
def route_via(owner):
    async def endpoint():
        return getattr(owner, attr_name)()
    return endpoint
"""))

    def test_retired_contract_import_is_rejected_whatever_it_is_called(self):
        for source in (
            "from app.services.legacy_http import Adapter as TotallyNewName\n",
            "from app.core import routing as fresh_router\n",
            "import server as old\n",
            "import app.routers.legacy_http_transport\n",
            "from app.ops.legacy_web_server import main\n",
            "from legacy_shim import LegacyHandlerShim as RenamedTransport\n",
            # function-local, including the intermediate-helper shape
            "async def endpoint():\n    from legacy_shim import LegacyHandlerShim\n"
            "    return LegacyHandlerShim\n",
            "def late():\n    import app.core.module_router as router\n    return router\n",
        ):
            with self.subTest(source=source):
                self.assertEqual(_codes(source), ["legacy-contract-import"])

    def test_dynamic_import_of_a_retired_module_is_rejected(self):
        for source in (
            "import importlib\nimportlib.import_module('app.ops.' + 'legacy_web_server')\n",
            "name = 'services.legacy_' + 'static'\n__import__(name)\n",
        ):
            with self.subTest(source=source):
                self.assertIn("legacy-dynamic-import", _codes(source))

    def test_real_http_and_typed_request_response_shapes_are_allowed(self):
        self.assertEqual(_codes("""
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from urllib.request import HTTPRedirectHandler
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

@dataclass(frozen=True)
class Command:
    path: str
    headers: dict[str, str]
    body: bytes

@dataclass(frozen=True)
class Result:
    status: int
    headers: dict[str, str]
    body: bytes

async def endpoint(request: Request) -> Response:
    payload = await request.json()
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})
"""), [])


def _dependency_calls(route):
    calls = []

    def visit(node):
        for child in getattr(node, "dependencies", ()):
            call = getattr(child, "call", None)
            if call is not None:
                calls.append(call)
            visit(child)

    visit(getattr(route, "dependant", None))
    return calls


def _unwrapped(value):
    if isinstance(value, functools.partial):
        value = value.func
    return getattr(value, "__func__", value)


def _retired_name(value):
    """The retired module a value belongs to or is, else None."""
    if inspect.ismodule(value):
        name = value.__name__
    else:
        name = getattr(value, "__module__", None) or ""
    return name if name in shapes.RETIRED_MODULES else None


def _in_project(function):
    """A function defined in production source of this checkout (not stdlib, a
    dependency, or a test)."""
    try:
        path = pathlib.Path(inspect.getsourcefile(function) or "").resolve()
    except (TypeError, OSError):
        return False
    if not path.is_relative_to(shapes.ROOT):
        return False
    return not any(part in shapes._IGNORED_SOURCE_DIRS | {"site-packages", ".venv", "venv"}
                   for part in path.relative_to(shapes.ROOT).parts)


def _reachable_functions(entry, include=_in_project, limit=4000):
    """Every project function reachable from `entry` through the names its code
    uses: globals and closure cells that are functions or classes' methods, and
    `module.attr` references into a project module. A static walk of the live
    objects, so it needs no particular source shape (the gap the AST detector
    leaves: a handler written in a wholly different shape)."""
    seen = {}
    queue = [_unwrapped(entry)]
    while queue and len(seen) < limit:
        item = queue.pop()
        if inspect.isclass(item):
            queue.extend(_unwrapped(v) for v in vars(item).values()
                         if inspect.isfunction(_unwrapped(v)))
            continue
        if not inspect.isfunction(item) or id(item) in seen or not include(item):
            continue
        seen[id(item)] = item
        code_names = _code_names(item.__code__)
        values = list(item.__globals__.get(n) for n in code_names)
        values += [cell.cell_contents for cell in (item.__closure__ or ())
                   if _cell_filled(cell)]
        for value in values:
            if inspect.ismodule(value):
                queue.extend(_unwrapped(getattr(value, n, None)) for n in code_names
                             if inspect.isfunction(_unwrapped(getattr(value, n, None))))
            elif inspect.isfunction(_unwrapped(value)) or inspect.isclass(value):
                queue.append(_unwrapped(value))
    return list(seen.values())


def _code_names(code):
    """The global and attribute names a function uses, including those of the
    functions, lambdas and comprehensions defined inside it."""
    names = set(code.co_names)
    for constant in code.co_consts:
        if inspect.iscode(constant):
            names |= _code_names(constant)
    return names


def _cell_filled(cell):
    try:
        cell.cell_contents
    except ValueError:
        return False
    return True


def _sample_helper():
    return 1


def _sample_cycle_a():
    return _sample_cycle_b()


def _sample_cycle_b():
    return _sample_cycle_a()


class _SampleOwner:
    def method(self):
        return _sample_helper()


def _sample_entry():
    def inner():
        return _sample_cycle_a()
    return inner, _SampleOwner, shapes.findings_from_source


def _sample_unreached():
    return 1


class ReachabilityWalkTest(unittest.TestCase):
    """The walker on synthetic code: it follows globals, closures, classes and
    `module.attr` hops, honours its boundary, and terminates on cycles."""

    @staticmethod
    def _here(function):
        return function.__module__ == __name__

    def test_walk_follows_calls_closures_classes_and_cycles(self):
        names = sorted(f.__name__ for f in _reachable_functions(_sample_entry, self._here))
        self.assertEqual(names, [
            "_sample_cycle_a", "_sample_cycle_b", "_sample_entry", "_sample_helper", "method"])

    def test_walk_stays_inside_the_project(self):
        self.assertEqual(_reachable_functions(json.dumps), [])
        # the boundary excludes tests/, so a test helper is never walked into
        self.assertEqual(_reachable_functions(_sample_entry), [])


class LiveCompositionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with probe.offline_environment():
            from app import main as app
            cls.routes = probe.flat_routes(app.app.routes, [])

    def test_no_retired_module_is_loaded(self):
        for name in sorted(shapes.RETIRED_MODULES):
            with self.subTest(module=name):
                self.assertNotIn(name, sys.modules)

    def test_every_route_has_methods_and_a_callable_endpoint(self):
        self.assertGreater(len(self.routes), 200)
        for route in self.routes:
            with self.subTest(path=route.path):
                self.assertTrue(route.methods, "method-less route shape: %s" % type(route).__name__)
                self.assertTrue(callable(getattr(route, "endpoint", None)))

    def test_no_endpoint_or_dependency_comes_from_a_retired_module(self):
        for route in self.routes:
            for call in [route.endpoint] + _dependency_calls(route):
                call = _unwrapped(call)
                with self.subTest(path=route.path, call=getattr(call, "__qualname__", repr(call))):
                    self.assertIsNone(_retired_name(call))
                    if not inspect.isfunction(call):
                        continue
                    try:
                        closure = inspect.getclosurevars(call)
                    except (TypeError, ValueError):
                        continue
                    named = list(closure.globals.values()) + list(closure.nonlocals.values())
                    self.assertEqual([v for v in named if _retired_name(v)], [])

    def test_nothing_an_endpoint_can_reach_is_a_retired_handler(self):
        """The walk the old census did, over the live objects instead of bytecode
        files: from every endpoint and dependency, follow the names its code uses
        through project functions, classes and `module.attr` hops. Nothing reached
        is a `handle`, lives in a retired module, or names one."""
        reached = {}
        for route in self.routes:
            for call in [route.endpoint] + _dependency_calls(route):
                for function in _reachable_functions(call):
                    reached[id(function)] = (route.path, function)
        self.assertGreater(len(reached), 500)
        for path, function in reached.values():
            with self.subTest(route=path, function=function.__qualname__):
                self.assertNotEqual(function.__name__, "handle")
                self.assertIsNone(_retired_name(function))
                names = _code_names(function.__code__)
                self.assertEqual(
                    [n for n in names
                     if _retired_name(function.__globals__.get(n))], [])

    def test_no_module_that_owns_an_endpoint_exposes_handle(self):
        owners = {}
        for route in self.routes:
            for call in [route.endpoint] + _dependency_calls(route):
                module = sys.modules.get(getattr(_unwrapped(call), "__module__", None) or "")
                if module is not None:
                    owners[module.__name__] = module
        self.assertGreater(len(owners), 20)
        for name, module in sorted(owners.items()):
            with self.subTest(module=name):
                self.assertFalse(hasattr(module, "handle"))


if __name__ == "__main__":
    unittest.main()
