"""Checkout-.env reader contract for the credential-boundary transition.

Only ``core.config`` may name the checkout fallback.  The migration utility is
separately audited because it reads a caller-nominated path in read-only
dry-run mode, never an implicit checkout path.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_CHECKOUT_ENV_OWNER = "app/core/config.py"


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _is_root_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id in {"ROOT", "_ROOT"}:
        return True
    if isinstance(node, ast.Call) and _dotted_name(node.func) in {"Path", "pathlib.Path"}:
        return bool(node.args) and _is_root_expression(node.args[0])
    return False


def _is_checkout_env_expression(node: ast.AST, checkout_names: set[str]) -> bool:
    if isinstance(node, ast.Constant) and node.value == ".env":
        return True
    if isinstance(node, ast.Name):
        return node.id in checkout_names
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_root_expression(node.left) and _is_checkout_env_expression(node.right, checkout_names)
    if not isinstance(node, ast.Call):
        return False
    func = _dotted_name(node.func)
    if func == "os.path.join" and node.args:
        return _is_root_expression(node.args[0]) and any(
            _is_checkout_env_expression(arg, checkout_names) for arg in node.args[1:]
        )
    if func in {"Path", "pathlib.Path"}:
        return bool(node.args) and (
            _is_checkout_env_expression(node.args[0], checkout_names)
            or (
                _is_root_expression(node.args[0])
                and any(_is_checkout_env_expression(arg, checkout_names) for arg in node.args[1:])
            )
        )
    if func in {"os.path.abspath", "os.path.normpath", "os.path.realpath"}:
        return bool(node.args) and _is_checkout_env_expression(node.args[0], checkout_names)
    return False


def _checkout_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None or not _is_checkout_env_expression(value, names):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    changed = True
    return names


def _docstring_constants(tree: ast.AST) -> set[int]:
    constants: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            constants.add(id(value))
    return constants


def _checkout_env_constructions(tree: ast.AST) -> list[ast.AST]:
    """Executable names or constructions of the checkout's exact .env path.

    This deliberately does not enumerate readers.  Any code which creates or
    aliases that path outside ``core.config`` violates ownership before it can
    pick a new filesystem API to read it with.
    """
    names = _checkout_names(tree)
    docstrings = _docstring_constants(tree)
    constructions: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == ".env" and id(node) not in docstrings:
            constructions.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if _is_checkout_env_expression(node.value, names):
                constructions.append(node)
    return constructions


class CheckoutEnvReaderContractTest(unittest.TestCase):
    def test_static_gate_recognizes_literal_and_constructed_checkout_names(self):
        probes = (
            'open(".env")',
            'import builtins\nbuiltins.open(".env")',
            'import os\nos.path.exists(os.path.join(ROOT, ".env"))',
            'import os\nos.stat(ROOT / ".env")',
            'import os\nos.path.getmtime(ROOT / ".env")',
            'from pathlib import Path\nPath(".env").open()',
            'from pathlib import Path\nPath(ROOT, ".env").read_text()',
            'from pathlib import Path\nPath(ROOT, ".env").exists()',
            'from pathlib import Path\ntarget = Path(ROOT) / ".env"\ntarget.open()',
            'name = ".env"\nalias = name',
        )
        for source in probes:
            with self.subTest(source=source):
                self.assertTrue(_checkout_env_constructions(ast.parse(source)), source)

    def test_only_core_config_may_construct_or_name_checkout_env(self):
        offenders: list[str] = []
        for path in ROOT.rglob("*.py"):
            relative = path.relative_to(ROOT)
            # Hook policy may name a protected basename while classifying shell
            # argv; it is not an application checkout-path construction.
            if relative.parts[0] in {".claude", "tests"}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if _checkout_env_constructions(tree) and str(relative) != _CHECKOUT_ENV_OWNER:
                offenders.append(str(relative))
        self.assertEqual(offenders, [])
        config_tree = ast.parse((ROOT / _CHECKOUT_ENV_OWNER).read_text(encoding="utf-8"))
        self.assertTrue(_checkout_env_constructions(config_tree))

    def test_migration_reader_is_explicit_user_controlled_and_read_only(self):
        migration = (ROOT / "scripts/turnos_secret_migration.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--env-file", type=Path)', migration)
        self.assertIn('parser.add_argument("--source-file", type=Path)', migration)
        self.assertIn('"writes": 0', migration)
        self.assertNotIn('ROOT / ".env"', migration)
        self.assertNotIn('Path(ROOT, ".env")', migration)

    def test_voice_webturno_and_asgi_main_never_open_checkout_env_under_devnull(self):
        probe = """
import builtins
import io
import os
import tempfile
from pathlib import Path
from app.core import config

checkout_env = os.path.normcase(os.path.abspath(os.path.join(os.getcwd(), ".env")))
real_open = builtins.open
real_io_open = io.open
real_os_open = os.open
real_exists = os.path.exists
real_stat = os.stat
real_lstat = os.lstat
real_mtime = os.path.getmtime
real_isfile = os.path.isfile
real_isdir = os.path.isdir
real_path_open = Path.open
real_path_read_text = Path.read_text
real_path_read_bytes = Path.read_bytes
real_path_exists = Path.exists
real_path_stat = Path.stat
real_path_lstat = Path.lstat
real_path_is_file = Path.is_file
real_path_is_dir = Path.is_dir

def is_checkout_env(path):
    return isinstance(path, (str, os.PathLike)) and os.path.normcase(os.path.abspath(os.fspath(path))) == checkout_env

def guarded_call(real, operation):
    def guarded(path, *args, **kwargs):
        if is_checkout_env(path):
            raise AssertionError("checkout env " + operation)
        return real(path, *args, **kwargs)
    return guarded

def guarded_open(path, *args, **kwargs):
    if is_checkout_env(path):
        raise AssertionError("checkout env opened")
    return real_open(path, *args, **kwargs)

def guarded_io_open(path, *args, **kwargs):
    if is_checkout_env(path):
        raise AssertionError("checkout env opened")
    return real_io_open(path, *args, **kwargs)

def guarded_path_method(real, operation):
    def guarded(path, *args, **kwargs):
        if is_checkout_env(path):
            raise AssertionError("checkout env " + operation)
        return real(path, *args, **kwargs)
    return guarded

builtins.open = guarded_open
io.open = guarded_io_open
os.open = guarded_call(real_os_open, "opened")
os.path.exists = guarded_call(real_exists, "inspected")
os.stat = guarded_call(real_stat, "inspected")
os.lstat = guarded_call(real_lstat, "inspected")
os.path.getmtime = guarded_call(real_mtime, "inspected")
os.path.isfile = guarded_call(real_isfile, "inspected")
os.path.isdir = guarded_call(real_isdir, "inspected")
Path.open = guarded_path_method(real_path_open, "opened")
Path.read_text = guarded_path_method(real_path_read_text, "read")
Path.read_bytes = guarded_path_method(real_path_read_bytes, "read")
Path.exists = guarded_path_method(real_path_exists, "inspected")
Path.stat = guarded_path_method(real_path_stat, "inspected")
Path.lstat = guarded_path_method(real_path_lstat, "inspected")
Path.is_file = guarded_path_method(real_path_is_file, "inspected")
Path.is_dir = guarded_path_method(real_path_is_dir, "inspected")
config._db_overrides = lambda: {}
config._reset_cache_para_tests()

from app.modules.chat import voice
from app.connectors.webturno import webturno
from app import asgi_main

assert voice._env("TURNOS_VOICE_MODEL") == "fal-ai/wizper"
assert webturno._load_env()["WEBTURNO_DNI"] == "synthetic-dni"
# The WebTurno vault-relay (server.py's _OWNER_ENV/REMOTE_URL) was retired
# 2026-08-16, and server.py itself was retired by the native-migration
# campaign's final wave — asgi_main.py is now the one process entrypoint.
# Importing it under the guards proves it never touches the checkout .env
# on import, and never grew a relay-shaped attribute of its own.
assert not hasattr(asgi_main, "_OWNER_ENV")
assert not hasattr(asgi_main, "REMOTE_URL")

def assert_checkout_denied(operation):
    try:
        operation()
    except AssertionError:
        return
    raise AssertionError("checkout env API was not guarded")

for operation in (
    lambda: builtins.open(checkout_env),
    lambda: io.open(checkout_env),
    lambda: os.open(checkout_env, os.O_RDONLY),
    lambda: os.stat(checkout_env),
    lambda: os.lstat(checkout_env),
    lambda: os.path.exists(checkout_env),
    lambda: os.path.getmtime(checkout_env),
    lambda: os.path.isfile(checkout_env),
    lambda: os.path.isdir(checkout_env),
    lambda: Path(checkout_env).open(),
    lambda: Path(checkout_env).read_text(),
    lambda: Path(checkout_env).read_bytes(),
    lambda: Path(checkout_env).exists(),
    lambda: Path(checkout_env).stat(),
    lambda: Path(checkout_env).lstat(),
    lambda: Path(checkout_env).is_file(),
    lambda: Path(checkout_env).is_dir(),
):
    assert_checkout_denied(operation)

with tempfile.TemporaryDirectory() as temporary:
    safe_path = os.path.join(temporary, "synthetic-public-config")
    with real_open(safe_path, "wb") as safe_file:
        safe_file.write(b"synthetic")
    with io.open(safe_path, "rb") as safe_file:
        assert safe_file.read() == b"synthetic"
    descriptor = os.open(safe_path, os.O_RDONLY)
    os.close(descriptor)
    assert os.stat(safe_path).st_size == len(b"synthetic")
    assert os.lstat(safe_path).st_size == len(b"synthetic")
    assert os.path.exists(safe_path)
    assert os.path.getmtime(safe_path) > 0
    assert os.path.isfile(safe_path)
    assert os.path.isdir(temporary)
    safe = Path(safe_path)
    with safe.open("rb") as safe_file:
        assert safe_file.read() == b"synthetic"
    assert safe.read_text(encoding="utf-8") == "synthetic"
    assert safe.read_bytes() == b"synthetic"
    assert safe.exists()
    assert safe.stat().st_size == len(b"synthetic")
    assert safe.lstat().st_size == len(b"synthetic")
    assert safe.is_file()
    assert Path(temporary).is_dir()
"""
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            env={
                "PATH": os.defpath,
                "TURNOS_ENV_FILE": os.devnull,
                "TURNOS_VOICE_MODEL": "fal-ai/wizper",
                "WEBTURNO_DNI": "synthetic-dni",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_asgi_main_ignores_sacoturno_remote_url_everywhere(self):
        # The vault relay (SACOTURNO_REMOTE_URL → server.REMOTE_URL) was
        # retired 2026-08-16 with the rest of the WebTurno client, and
        # server.py itself was retired by the native-migration campaign's
        # final wave — asgi_main.py is now the one process entrypoint.
        # Whatever a config source says — effective env file, process env —
        # importing it must produce NO remote-vault state at all: the
        # precedence contract this test used to assert has no consumer
        # anymore, anywhere.
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "runtime-config"
            env_file.write_text(
                "SACOTURNO_REMOTE_URL=https://effective.synthetic.invalid/vault/\n",
                encoding="utf-8",
            )
            probe = """
from app import asgi_main
assert not hasattr(asgi_main, "REMOTE_URL")
assert not hasattr(asgi_main, "_OWNER_ENV")
"""
            result = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=ROOT,
                env={
                    "PATH": os.defpath,
                    "TURNOS_ENV_FILE": str(env_file),
                    "SACOTURNO_REMOTE_URL": "https://process.synthetic.invalid/vault/",
                    "TURNOS_FAKE_AI": "1",
                    "TURNOS_SKIP_HSI": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
