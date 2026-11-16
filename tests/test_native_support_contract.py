"""test_native_support_contract.py - the two `_native_support` modules answer the same (2026-10-28).

`admin_panel/_native_support.py` and `modules/intake/_native_support.py` each carried a copy of the
plumbing the native admin routers share: `ApiError`, `gate()` (the dependency `core.deps.require_admin`
reshaped into a flat `{"error": ...}` body), the terminal 404 and `read_json_body()`. They were written
twice because the second family did not want to reach across families, not because they differ.

What is pinned here, for each module and for what each one calls its terminal 404, is what the pair
answers: the identity `gate()` hands back, the flags it passes to `require_admin`, the status and the
payload of the `ApiError` it raises (a dict detail as it is, any other detail as `{"error": str}`),
what stays outside it (an exception that is not an `HTTPException`), the 404 bytes and headers, and the
`json.loads` contract of `read_json_body()` (empty body, plain body, accents, malformed, not UTF-8).

Nothing here knows where the code lives, so the file stays valid when the copies become one. The
recorded file `tests/golden/native_support.json` is written with the code that existed before they were
joined and is not touched by the change that joins them. Re-record only from code whose answers are
known to be right: `python3 tests/test_native_support_contract.py --write`.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_native_support_contract.py
"""
import asyncio
import importlib
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

GOLDEN = os.path.join(ROOT, "tests", "golden", "native_support.json")

# (module, name of its terminal 404)
TARGETS = [("app.admin_panel._native_support", "not_found_response"),
           ("app.modules.intake._native_support", "terminal_not_found_response")]

BODIES = [b"", b"{}", b'{"a": 1, "b": [1, 2.5, null]}', "{\"nombre\": \"Ñandú Pérez ¿ok?\"}".encode("utf-8"),
          b"null", b"[1, 2]", b"   ", b'{"a": ', b"\xff\xfe{}", b"not json"]


def _run(coro):
    return asyncio.run(coro)


def _describe_response(response):
    headers = sorted([k.decode("latin-1"), v.decode("latin-1")] for k, v in response.raw_headers)
    return {"status": response.status_code, "headers": headers, "body": response.body.decode("utf-8")}


def _gate_cases(mod):
    out = {}
    identity = {"usuario": "ana", "rol": "admin"}
    seen = []

    def dependency_factory(**flags):
        seen.append(flags)

        async def dependency(request):
            return behaviour(request)
        return dependency

    behaviours = {
        "identity": lambda request: identity,
        "dict_detail": lambda request: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail={"error": "solo_admin", "extra": [1]})),
        "str_detail": lambda request: (_ for _ in ()).throw(HTTPException(status_code=401, detail="no autenticado")),
        "none_detail": lambda request: (_ for _ in ()).throw(HTTPException(status_code=500)),
        "list_detail": lambda request: (_ for _ in ()).throw(HTTPException(status_code=418, detail=["a", "b"])),
        "other_error": lambda request: (_ for _ in ()).throw(RuntimeError("boom")),
    }
    for label, fn in behaviours.items():
        behaviour = fn
        seen.clear()
        with mock.patch("app.core.deps.require_admin", dependency_factory):
            try:
                result = _run(mod.gate(SimpleNamespace(), permitir_medico=True, solo_admin=False))
                out[label] = {"returned": result, "flags": seen[:]}
            except mod.ApiError as exc:
                out[label] = {"api_error": [exc.status, exc.payload], "str": str(exc), "args": repr(exc.args),
                              "cause": repr(exc.__cause__), "suppressed": exc.__suppress_context__,
                              "flags": seen[:]}
            except Exception as exc:  # noqa: BLE001 - the type is part of what is pinned
                out[label] = {"raised": type(exc).__name__, "message": str(exc), "flags": seen[:]}
    with mock.patch("app.core.deps.require_admin", dependency_factory):
        seen.clear()
        behaviour = behaviours["identity"]
        _run(mod.gate(SimpleNamespace()))
        out["no_flags"] = {"flags": seen[:]}
    return out


def _body_cases(mod):
    out = {}
    for raw in BODIES:
        request = SimpleNamespace(body=lambda raw=raw: _coro(raw))
        try:
            out[repr(raw)] = {"value": _run(mod.read_json_body(request))}
        except Exception as exc:  # noqa: BLE001
            out[repr(raw)] = {"raised": type(exc).__name__, "message": str(exc)}
    return out


async def _coro(value):
    return value


def _record():
    out = {}
    for module, not_found in TARGETS:
        mod = importlib.import_module(module)
        error = mod.ApiError(409, {"error": "x", "n": [1]})
        out[module] = {
            "gate": _gate_cases(mod),
            "api_error": {"status": error.status, "payload": error.payload, "str": str(error),
                          "args": repr(error.args), "is_exception": isinstance(error, Exception)},
            "not_found": _describe_response(getattr(mod, not_found)()),
            "read_json_body": _body_cases(mod),
        }
    return json.loads(json.dumps(out, sort_keys=True, ensure_ascii=False, default=repr))


def _write():
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, ensure_ascii=False, indent=1)
        fh.write("\n")


class NativeSupportContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as fh:
            cls.want = json.load(fh)
        cls.got = _record()

    def test_each_module_answers_what_it_answered_before_they_were_joined(self):
        self.assertEqual(sorted(self.got), sorted(self.want))
        for module in sorted(self.want):
            for part in sorted(self.want[module]):
                self.assertEqual(self.got[module][part], self.want[module][part], "%s / %s" % (module, part))

    def test_the_pair_answers_alike(self):
        first, second = (self.got[m] for m, _ in TARGETS)
        self.assertEqual(first, second)

    def test_the_terminal_404_is_the_shared_one(self):
        from app.core import terminal_response
        want = _describe_response(terminal_response.json_not_found_response())
        for module, _ in TARGETS:
            self.assertEqual(self.got[module]["not_found"], want, module)


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("wrote", GOLDEN)
    else:
        unittest.main()
