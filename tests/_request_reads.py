"""_request_reads.py — what a route's handler takes from the request, read
from its source (AST), for one HTTP method.

`reads(endpoint, method)` walks the handler and every function of this
repository it hands the request (or the query) to — helpers, closures,
`run_in_threadpool(fn, ...)`-style calls, domain functions — and reports:

  - `body`: the body is read (`request.body()` / `.json()` / `.form()` /
    `.stream()`, or `core.api_body`'s readers);
  - `query`: the names of the query parameters read (`request.query_params
    .get("x")`, `parse_qs(request.url.query).get("x")`, `"x" in qs`, a
    constant handed down as the name);
  - `query_unknown`: the query is read in a way whose names the walk cannot
    see (a query dict handed to code outside the repository, returned to a
    caller that does not keep it, or indexed by a computed key).

Branches on `request.method` are followed for the asked method only, so a
`GET`+`DELETE` handler that reads `?probe` only for `GET` does not read it for
`DELETE`. Used by `tests/test_api_docs_schema.py`; not named test_*.py, so
unittest discovery does not collect it.
"""

import ast
import builtins
import importlib
import inspect
import os
import textwrap

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENDOR = (os.sep + "site-packages" + os.sep, os.sep + "node_modules" + os.sep)
_BODY_METHODS = {"body", "json", "form", "stream"}
_BODY_READERS = {"read_body", "read_model", "lazy_model"}
_QUERY_BUILDERS = {"parse_qs", "dict"}
_MAX_DEPTH = 8


class Reads:
    __slots__ = ("body", "query", "query_unknown", "opaque")

    def __init__(self):
        self.body = False
        self.query = set()
        self.query_unknown = False
        self.opaque = set()


class _Factory:
    """A local name bound to what a function of ours returned: one of its closures."""

    __slots__ = ("function",)

    def __init__(self, function):
        self.function = function


def _function_node(fn):
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return None
    node = tree.body[0] if tree.body else None
    return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _ours(target):
    target = inspect.unwrap(target) if callable(target) else target
    if not inspect.isfunction(target):
        return None
    path = inspect.getsourcefile(target) or ""
    if not path.startswith(_ROOT + os.sep) or any(part in path for part in _VENDOR):
        return None
    return target


def _method_test(test, names):
    """(methods, positive) for `request.method == "X"` / `in (...)` / `!=` / `not in`, else None."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    left = test.left
    if not (isinstance(left, ast.Attribute) and left.attr == "method" and isinstance(left.value, ast.Name)
            and names.get(left.value.id) == "request"):
        return None
    right = test.comparators[0]
    values = [right] if isinstance(right, ast.Constant) else getattr(right, "elts", None)
    if not values or not all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in values):
        return None
    methods = {v.value.upper() for v in values}
    if isinstance(test.ops[0], (ast.Eq, ast.In)):
        return methods, True
    if isinstance(test.ops[0], (ast.NotEq, ast.NotIn)):
        return methods, False
    return None


class _Walk:
    def __init__(self, method):
        self.method = method
        self.out = Reads()
        self._seen = set()
        self._locals = {}
        self.typed = {}        # the endpoint's typed query parameters: parameter name -> query name
        self.endpoint = None

    # ---- one function -------------------------------------------------------

    def function(self, fn, names, depth):
        fn = inspect.unwrap(fn)
        key = (fn, tuple(sorted(names.items())))
        if key in self._seen or depth > _MAX_DEPTH:
            return
        self._seen.add(key)
        node = _function_node(fn)
        if node is not None:
            self._block(node.body, dict(names), fn, depth)

    def _block(self, stmts, names, fn, depth):
        """Walk statements in order; True when the block always returns or raises."""
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                admitted = _method_test(stmt.test, names)
                if admitted is not None:
                    methods, positive = admitted
                    branch = stmt.body if (self.method in methods) == positive else stmt.orelse
                    if self._block(branch, names, fn, depth):
                        return True
                    continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A closure runs inside its function; one that takes a request is handed one.
                inner = dict(names)
                inner.update({a.arg: "request" for a in stmt.args.args if a.arg in ("request", "req")})
                self._block(stmt.body, inner, fn, depth)
                continue
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                self._import(stmt, fn)
                continue
            blocks = [getattr(stmt, field) for field in ("body", "orelse", "finalbody")
                      if isinstance(getattr(stmt, field, None), list)]
            blocks += [handler.body for handler in getattr(stmt, "handlers", [])]
            blocks += [case.body for case in getattr(stmt, "cases", [])]
            if blocks:
                # if / for / while / with / try / match: the header's expressions, then each block in order.
                for field in ("test", "iter", "subject"):
                    if getattr(stmt, field, None) is not None:
                        self._expressions(getattr(stmt, field), names, fn, depth)
                for item in getattr(stmt, "items", []):
                    self._expressions(item.context_expr, names, fn, depth)
                ends = [self._block(block, names, fn, depth) for block in blocks]
                if isinstance(stmt, ast.If) and len(ends) == 2 and all(ends):
                    return True
                continue
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                self._assign(stmt.targets[0].id, stmt.value, names, fn)
            self._expressions(stmt, names, fn, depth)
            if isinstance(stmt, ast.Return) and stmt.value is not None \
                    and self._kind(stmt.value, names, fn) in ("qdict", "qstring"):
                self.out.query_unknown = True  # handed back to a caller
            if isinstance(stmt, (ast.Return, ast.Raise)):
                return True
        return False

    def _expressions(self, stmt, names, fn, depth):
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in self.typed \
                    and fn is self.endpoint:
                self.out.query.add(self.typed[node.id])  # a typed parameter, used on this method's branch
            if isinstance(node, ast.Call):
                self._call(node, names, fn, depth)
            elif isinstance(node, ast.Compare) and len(node.ops) == 1 \
                    and isinstance(node.ops[0], (ast.In, ast.NotIn)) \
                    and self._kind(node.comparators[0], names, fn) in ("qdict", "qstring"):
                self._name(self._const(node.left, names), qstring=True)
            elif isinstance(node, ast.Subscript) and self._kind(node.value, names, fn) == "qdict":
                self._name(self._const(node.slice, names))

    def _assign(self, target, value, names, fn):
        kind = self._kind(value, names, fn)
        if kind:
            names[target] = kind
        elif isinstance(value, ast.Call):
            factory = _ours(self._resolve(value.func, fn))
            if factory is not None:
                # `gate = deps.require_admin(...)`, then `await gate(request)`: the closure it returns.
                self._locals.setdefault(fn, {})[target] = _Factory(factory)

    def _name(self, key, qstring=False):
        if key is None:
            self.out.query_unknown = True
        else:
            self.out.query.add(key.split("=", 1)[0] if qstring else key)

    # ---- what an expression holds ---------------------------------------------

    def _const(self, node, names):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and names.get(node.id, "").startswith("const:"):
            return names[node.id][len("const:"):]
        return None

    def _kind(self, node, names, fn):
        """"request", "qdict" (a mapping of the query), "qstring" (the raw query) or None."""
        if isinstance(node, ast.Name):
            kind = names.get(node.id)
            return None if (kind or "").startswith("const:") else kind
        if isinstance(node, ast.Attribute):
            if node.attr == "query_params" and self._kind(node.value, names, fn) == "request":
                return "qdict"
            if node.attr == "query" and isinstance(node.value, ast.Attribute) and node.value.attr == "url" \
                    and self._kind(node.value.value, names, fn) == "request":
                return "qstring"
            return None
        if isinstance(node, ast.BoolOp):
            kinds = {self._kind(v, names, fn) for v in node.values} - {None}
            return kinds.pop() if len(kinds) == 1 else None
        if isinstance(node, ast.Call):
            if self._callee_name(node) in _QUERY_BUILDERS and node.args \
                    and self._kind(node.args[0], names, fn) in ("qstring", "qdict"):
                return "qdict"
            target = _ours(self._resolve(node.func, fn))
            sub = self._arguments(target, node.args, node.keywords, names, fn) if target else None
            if sub and self._returns_query(target, sub):
                return "qdict"
        return None

    def _returns_query(self, target, names):
        node = _function_node(target)
        if node is None:
            return False
        names = dict(names)
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                kind = self._kind(stmt.value, names, target)
                if kind:
                    names[stmt.targets[0].id] = kind
            if isinstance(stmt, ast.Return) and stmt.value is not None:
                return self._kind(stmt.value, names, target) == "qdict"
        return False

    # ---- calls ----------------------------------------------------------------

    @staticmethod
    def _callee_name(node):
        f = node.func
        return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)

    def _import(self, stmt, fn):
        """A function-level import binds names the function's globals do not have."""
        local = self._locals.setdefault(fn, {})
        for alias in stmt.names:
            try:
                if isinstance(stmt, ast.Import):
                    local[alias.asname or alias.name.split(".")[0]] = importlib.import_module(
                        alias.name if alias.asname else alias.name.split(".")[0])
                elif stmt.module and not stmt.level:
                    module = importlib.import_module(stmt.module)
                    value = getattr(module, alias.name, None)
                    if value is None:
                        value = importlib.import_module(stmt.module + "." + alias.name)
                    local[alias.asname or alias.name] = value
            except ImportError:
                continue

    def _resolve(self, node, fn):
        if isinstance(node, ast.Call):
            # `factory(...)(request)`: the function the factory returns is one of its closures,
            # walked with it (see `_block`).
            factory = _ours(self._resolve(node.func, fn))
            return factory
        scope = dict(getattr(fn, "__globals__", {}) or {})
        try:
            scope.update(inspect.getclosurevars(fn).nonlocals)
        except (TypeError, ValueError):
            pass
        scope.update(self._locals.get(fn, {}))
        if isinstance(node, ast.Name):
            return scope.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            owner = scope.get(node.value.id)
            return getattr(owner, node.attr, None) if owner is not None else None
        return None

    def _arguments(self, target, args, keywords, names, fn):
        """The callee's parameter names -> what the call hands them, or None when it hands nothing of the request."""
        node = _function_node(target)
        if node is None:
            return None
        params = [a.arg for a in node.args.posonlyargs + node.args.args]
        if params[:1] in (["self"], ["cls"]):
            params = params[1:]
        sub, consts = {}, {}
        for i, arg in enumerate(args):
            if i >= len(params) or isinstance(arg, ast.Starred):
                break
            kind = self._kind(arg, names, fn)
            if kind:
                sub[params[i]] = kind
            elif self._const(arg, names) is not None:
                consts[params[i]] = "const:" + self._const(arg, names)
        for kw in keywords:
            if kw.arg:
                kind = self._kind(kw.value, names, fn)
                if kind:
                    sub[kw.arg] = kind
                elif self._const(kw.value, names) is not None:
                    consts[kw.arg] = "const:" + self._const(kw.value, names)
        return dict(sub, **consts) if sub else None

    def _call(self, node, names, fn, depth):
        f = node.func
        name = self._callee_name(node)
        if isinstance(f, ast.Attribute):
            base = self._kind(f.value, names, fn)
            if base == "request" and f.attr in _BODY_METHODS:
                self.out.body = True
                return
            if base == "qdict":
                if f.attr in ("get", "getlist") and node.args:
                    self._name(self._const(node.args[0], names))
                elif f.attr not in ("keys", "__contains__"):
                    self.out.query_unknown = True
                return
        if name in _BODY_READERS and any(self._kind(a, names, fn) == "request" for a in node.args):
            self.out.body = True
            return
        if name in _QUERY_BUILDERS:
            return  # builds a query mapping (`_kind`): what is read from it is seen where it is used
        handed = [self._kind(a, names, fn) for a in node.args] + [self._kind(k.value, names, fn) for k in node.keywords]
        if not any(handed):
            return
        resolved = self._resolve(f, fn)
        if isinstance(f, ast.Call) or isinstance(resolved, _Factory):
            factory = resolved.function if isinstance(resolved, _Factory) else resolved
            if factory is not None:
                self.function(factory, {}, depth + 1)
            else:
                self.out.opaque.add(ast.unparse(f))
            return
        followed = False
        if resolved is None and "request" in handed and not (isinstance(f, ast.Name) and hasattr(builtins, f.id)):
            self.out.opaque.add(ast.unparse(f))
        target = _ours(resolved)
        if target is not None:
            sub = self._arguments(target, node.args, node.keywords, names, fn)
            if sub and not self._returns_query(target, sub):
                self.function(target, sub, depth + 1)
                followed = True
            elif sub:
                followed = True  # it builds a query mapping the caller keeps (`_kind`)
        # `run_in_threadpool(fn, *args)`, `api_body.run(body, fn, *args)`: a function of ours
        # handed along with the arguments it will be called with.
        for i, arg in enumerate(node.args):
            inner = _ours(self._resolve(arg, fn))
            if inner is None:
                continue
            sub = self._arguments(inner, node.args[i + 1:], [], names, fn)
            if sub:
                self.function(inner, sub, depth + 1)
                followed = True
        if not followed and any(kind in ("qdict", "qstring") for kind in handed):
            self.out.query_unknown = True  # handed to code outside the repository


def _declared(dependant, method, out):
    """What the signature declares (TASK-76 items E and G): a body dependency of `core.api_body` that reads
    for `method`, anywhere in the dependency tree; returns the endpoint's own typed query parameters,
    {parameter name: query name}."""
    stack = [dependant]
    while stack:
        current = stack.pop()
        methods = getattr(current.call, "body_methods", None)
        if getattr(current.call, "body_kind", None) and (methods is None or method in methods):
            out.body = True
        stack += current.dependencies
        if current is not dependant:
            out.query.update(field.alias for field in current.query_params)
    return {field.name: field.alias for field in dependant.query_params}


def reads(endpoint, method, dependant=None):
    """What `endpoint` reads from the request when it answers `method` (upper case). With the route's
    `dependant`, what its signature declares counts too: a body dependency, and a typed query parameter
    the handler uses on the branch of that method (a route object can serve several methods)."""
    walk = _Walk(method.upper())
    if dependant is not None:
        walk.typed = _declared(dependant, method.upper(), walk.out)
        walk.endpoint = inspect.unwrap(endpoint)
    node = _function_node(inspect.unwrap(endpoint))
    if node is not None:
        params = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        names = {a.arg: "request" for a in params if a.annotation is not None
                 and ast.unparse(a.annotation).endswith("Request")}
        names = names or {a.arg: "request" for a in params if a.arg in ("request", "req")}
        walk.function(endpoint, names, 0)
    return walk.out
