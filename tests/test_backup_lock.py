"""The third guard of the municipal installation (salud.pinamar.gob.ar), held by tests:
the application never deletes or overwrites a backup there, by any path.

The municipality, through the product owner (Teams 2026-10-26 19:48): «La app puede escribir
respaldos; lo que no puede es borrarlos ni sobrescribirlos por ningún camino…
No te pedimos que saques la funcionalidad: te pedimos que no tenga el permiso.»
The lock is `core.backup_lock`, in the two remote clients (the B2 native API
of the data/ backups, the S3 transport of the encrypted full backups); the
upper paths check it first so nothing is left half done.

1. The inventory (AST over the code, not the tests): every B2 native call
   name, every network send of the backup clients and every call of a client
   method that deletes or writes is listed here. A new one fails the test
   until it is looked at — through the lock? a test on `*.gob.ar`? — and
   added to the list.
2. Each path on a `*.gob.ar` origin (refused, nothing sent) and on a test
   stand (as before).

Run:  python3 -m unittest discover -s tests -p test_backup_lock.py
"""

import ast
import collections
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import worker  # noqa: E402
from app.admin_panel import settings_admin, system_status  # noqa: E402
from app.core import backup_lock, backup_s3, backup_settings, backup_setup, config, i18n  # noqa: E402
from app.ops import backup_full, data_backups, data_backups_restore as restore, purge_guard  # noqa: E402
import _env  # noqa: E402
from app.core import db_backup  # noqa: E402

_GOV = "https://salud.pinamar.gob.ar"
_STAND = "https://pinamar.humanrounds.org"
_LANGS = ("es", "en", "pt", "fr", "it", "de", "ru", "uk", "ja", "zh")


class _Env(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN=_STAND, TURNOS_BACKUP_RETENTION_APPLY="",
                     TURNOS_BACKUP_PURGE_REMOTE="", TURNOS_OUTBOUND_KILL="0")
        # Nothing from a checkout's .env or a database override reaches a guard under test.
        self.enterContext(patch.object(config, "dotenv_values", return_value={}))
        self.enterContext(patch.object(config, "_db_overrides", return_value={}))
        self.lock_log = self.enterContext(patch.object(backup_lock, "_log"))

    def origin(self, value):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN=value)

    def errors(self):
        return [c.args[0] for c in self.lock_log.call_args_list if c.args[0].startswith("ERROR")]


# ------------------------------------------------------------------ 1. inventory

_SKIP_DIRS = {"tests", "node_modules", "venv", ".venv"}


def _sources():
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(x for x in dirs if x not in _SKIP_DIRS and not x.startswith("."))
        for f in sorted(files):
            if f.endswith(".py"):
                path = os.path.join(d, f)
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                # A file the scan cannot read is a hole in the inventory: fail, never skip.
                yield rel, text, ast.parse(text, filename=rel)


def _tracked_files():
    """The repository's files (git ls-files; a plain walk where there is no git)."""
    listed = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True)
    if listed.returncode == 0 and listed.stdout.strip():
        return listed.stdout.splitlines()
    out = []
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS and not x.startswith(".")]
        out += [os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/") for f in files]
    return out


def _walk(tree):
    """(qualified function name, node) for every node, nested defs included."""
    out = []

    def go(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                go(child, stack + [child.name])
                continue
            out.append((".".join(stack), child))
            go(child, stack)

    go(tree, [])
    return out


def _callee(call):
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr, ast.unparse(fn.value)
    if isinstance(fn, ast.Name):
        return fn.id, ""
    return None, ""


# Every B2 native call named in the code: (file, function, call). Only
# `B2_ALLOWED` calls may leave on government; the one other is refused before
# any request (TestOperatorTools).
_B2_CALLS = {
    ("app/ops/data_backups.py", "_b2_authorize_url", "b2_authorize_account"),
    ("app/ops/data_backups.py", "_upload_b2", "b2_get_upload_url"),
    ("app/ops/data_backups_restore.py", "_b2_list_page", "b2_list_file_versions"),
    ("app/ops/data_backups_restore.py", "delete_remote_version", "b2_delete_file_version"),
    ("app/ops/data_backups_restore.py", "download_remote_version", "b2_download_file_by_id"),
}
_B2_REFUSED_EARLY = {("app/ops/data_backups_restore.py", "delete_remote_version")}

# Every network send of a module that speaks to B2 or S3. The first two pass
# the lock before sending; the download only reads.
_SENDERS = {
    ("app/core/backup_s3.py", "S3.request", "self.opener.open"),
    ("app/ops/data_backups.py", "_req_json", "urllib.request.urlopen"),
    ("app/ops/data_backups_restore.py", "download_remote_version", "urllib.request.urlopen"),
    # Modules that only name the backup credentials (panels, setup) and send elsewhere:
    ("app/admin_panel/system_status.py", "_probe_json", "urllib.request.urlopen"),  # OpenRouter key/credits
    ("app/admin_panel/system_status.py", "_reachable", "urllib.request.urlopen"),  # HSI reachability
    ("app/ops/setup.py", "_geoip_lookup", "urllib.request.urlopen"),  # ip-api.com country
}
# What a module that speaks the B2 native API or S3 cannot do without (a product
# name in a panel text is not one): the protocol, the signer, the credentials.
_REMOTE_MARKERS = ("b2api", "AWS4-HMAC", "x-amz-", "signed_headers", "TURNOS_BACKUP_S3_KEY_ID",
                   "TURNOS_B2_APP_KEY", "_authorize(")
# Other ways to reach a bucket, none of which may appear: SDKs, and cloud
# storage CLIs called from code or from the shell/deploy files.
_FORBIDDEN_IMPORTS = {"boto3", "botocore", "aiobotocore", "b2sdk", "minio", "s3fs", "s3transfer"}
_CLI = re.compile(r"(?<![\w-])(?:b2 (?:rm|delete-file-version|hide|sync|upload-file|file|cancel-.*)|rclone|aws s3|s3cmd)(?![\w-])")
_CLI_ARGV0 = {"b2", "rclone", "aws", "s3cmd", "mc"}

# Every call (or reference, for the B2 primitives handed around as values) of
# a method that deletes or writes remotely, with what holds it on government.
_MUTATING_NAMES = {"delete", "upload", "probe", "cleanup_incomplete", "delete_remote_version",
                   "apply_retention", "_upload_b2", "_purge_remote", "hide_file", "copy_file"}
_REFERENCED = {"delete_remote_version", "_upload_b2", "_purge_remote"}
# Receivers that are not backup clients: SQLAlchemy, FastAPI, mail/AI connectors.
_NOT_BACKUP = {"sa", "router", "_impl", "adapter", "ai_health", "dialogue"}
# How many times each appears: a second call in an already listed place counts too.
_MUTATING = collections.Counter({
    # S3 client internals: every one passes S3.request -> backup_lock.check_s3_request.
    ("app/core/backup_s3.py", "S3.upload", "self.request", "PUT"): 2,  # object (HEAD first on government), part
    ("app/core/backup_s3.py", "S3.upload", "self.request", "POST"): 2,  # multipart start (HEAD first), completion
    ("app/core/backup_s3.py", "S3.upload", "self.request", "DELETE"): 1,  # abort: skipped on government
    ("app/core/backup_s3.py", "S3.delete", "self.request", "DELETE"): 1,  # refused on government
    ("app/core/backup_s3.py", "S3.cleanup_incomplete", "self.request", "DELETE"): 1,  # returns first on government
    ("app/core/backup_s3.py", "S3.probe", "self.request", "PUT"): 1,  # new uuid name, HEAD first on government
    ("app/core/backup_s3.py", "S3.probe", "self.delete", ""): 1,  # skipped on government (object kept)
    # Upper paths.
    ("app/admin_panel/settings_admin.py", "_probar_backup", "backup_settings.client().probe", ""): 1,
    ("app/core/backup_setup.py", "update", "backup_settings.client(cfg).probe", ""): 1,
    ("app/ops/backup_full.py", "apply_retention", "cloud.delete", ""): 1,  # returns first on government
    ("app/ops/backup_full.py", "run", "cloud.upload", ""): 1,  # hr-full-<uuid>.hrb, HEAD first on government
    ("app/ops/backup_full.py", "run", "apply_retention", ""): 1,
    ("app/ops/backup_full.py", "run", "cloud.cleanup_incomplete", ""): 1,
    ("app/ops/data_backups.py", "_tick_unlocked", "_purge_remote", ""): 1,  # returns first on government
    ("app/ops/data_backups.py", "tick", "_upload_b2", ""): 1,  # content-addressed names on government
    ("app/ops/data_backups.py", "_purge_ops_default", "restore.delete_remote_version", ""): 1,  # refused on government
    # Not backup clients (local files, a seed script's HTTP helper).
    ("app/ai/ai_health.py", "_loop", "probe", ""): 1,
    ("app/modules/turnos/study_store.py", "purge", "delete", ""): 4,
    # An HTTP route decorator, not a backup client: revokes an analytics export key in Postgres.
    # Listed by function on purpose: any OTHER `admin_router.delete` route must be read and listed too.
    ("app/modules/turnos/analytics_api_router.py", "keys_revoke", "admin_router.delete", ""): 1,
    ("scripts/seed_patient_account.py", "Http.post", "self.request", "POST"): 1,
    ("scripts/seed_patient_account.py", "Http.put", "self.request", "PUT"): 1,
})


class TestInventory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = list(_sources())

    def test_every_b2_native_call_is_listed(self):
        import re
        pattern = re.compile(r"/b2api/v\d+/(b2_[a-z_]+)")
        found = set()
        for rel, _text, tree in self.sources:
            for func, node in _walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for m in pattern.finditer(node.value):
                        found.add((rel, func, m.group(1)))
        self.assertEqual(found, _B2_CALLS, "a B2 native call appeared or went away: is it a delete, hide, copy "
                                           "or overwrite? Then backup_lock must refuse it on *.gob.ar (with a test)")
        for rel, func, name in found:
            if name not in backup_lock.B2_ALLOWED:
                self.assertIn((rel, func), _B2_REFUSED_EARLY, name)

    def test_every_network_send_of_the_backup_clients_is_listed(self):
        found = set()
        for rel, text, tree in self.sources:
            if not any(marker in text for marker in _REMOTE_MARKERS):
                continue
            for func, node in _walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name, recv = _callee(node)
                method = next((kw.value.value for kw in node.keywords if kw.arg == "method"
                               and isinstance(kw.value, ast.Constant)), None)
                if (name in ("urlopen", "HTTPConnection", "HTTPSConnection", "urlretrieve", "Session",
                             "PoolManager", "AsyncClient", "Client")
                        or (name == "open" and "opener" in recv)
                        or (name == "Request" and method not in (None, "GET", "HEAD"))
                        or recv in ("requests", "httpx", "http.client", "urllib3", "session", "s")):
                    found.add((rel, func, ast.unparse(node.func)))
        self.assertEqual(found, _SENDERS, "a new way to reach B2/S3 must go through backup_lock")

    def test_no_storage_sdk_and_no_storage_cli(self):
        for rel, _text, tree in self.sources:
            for _func, node in _walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                             else [node.module or ""])
                    for name in names:
                        self.assertNotIn(name.split(".")[0], _FORBIDDEN_IMPORTS, (rel, name))
                elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
                    first = node.args[0].elts[:1]
                    if first and isinstance(first[0], ast.Constant) and isinstance(first[0].value, str):
                        self.assertNotIn(os.path.basename(first[0].value), _CLI_ARGV0, (rel, ast.unparse(node)[:80]))
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertIsNone(_CLI.search(node.value), (rel, node.value[:80]))
        tracked = _tracked_files()
        shellish = [f for f in tracked if not f.startswith(("tests/", "docs/"))
                    and (f.endswith((".sh", ".yml", ".yaml", ".toml", ".cron")) or os.path.basename(f).startswith(
                        ("Dockerfile", "Procfile", "docker-compose", "nixpacks", "railway")))]
        self.assertTrue(any(f == "Dockerfile" for f in shellish))
        for f in shellish:
            with open(os.path.join(ROOT, f), encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    self.assertIsNone(_CLI.search(line), (f, n, line.strip()[:80]))

    def _first_line(self, rel, func, callee):
        tree = next(t for r, _x, t in self.sources if r == rel)
        lines = [node.lineno for f, node in _walk(tree)
                 if f == func and isinstance(node, ast.Call) and ast.unparse(node.func) == callee]
        self.assertTrue(lines, (rel, func, callee))
        return min(lines)

    def test_the_lock_runs_before_the_send_in_both_clients(self):
        self.assertLess(self._first_line("app/ops/data_backups.py", "_req_json", "backup_lock.check_b2_call"),
                        self._first_line("app/ops/data_backups.py", "_req_json", "urllib.request.urlopen"))
        self.assertLess(self._first_line("app/core/backup_s3.py", "S3.request", "backup_lock.check_s3_request"),
                        self._first_line("app/core/backup_s3.py", "S3.request", "self.opener.open"))

    @staticmethod
    def _scan_mutating(rel, tree, found):
        called = set()
        for func, node in _walk(tree):
            if isinstance(node, ast.Call):
                called.add(id(node.func))
                name, recv = _callee(node)
                arg0 = (node.args[0].value if node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str) else "")
                if name in _MUTATING_NAMES and recv not in _NOT_BACKUP:
                    found[(rel, func, ast.unparse(node.func), "")] += 1
                elif name == "request" and arg0 in ("DELETE", "PUT", "POST"):
                    found[(rel, func, ast.unparse(node.func), arg0)] += 1
            elif (isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load)
                  and id(node) not in called
                  and (node.id if isinstance(node, ast.Name) else node.attr) in _REFERENCED):
                # Handed around as a value.
                found[(rel, func, ast.unparse(node), "")] += 1

    def test_a_new_admin_router_delete_route_is_caught(self):
        found = collections.Counter()
        src = ('@admin_router.delete("/api/admin/other")\n'
               'async def other_route(request):\n'
               '    return None\n')
        self._scan_mutating("app/modules/turnos/analytics_api_router.py", ast.parse(src), found)
        self.assertEqual(sum(found.values()), 1)
        self.assertFalse(set(found) <= set(_MUTATING), "a second admin_router.delete route must not be pre-listed")

    def test_every_call_that_deletes_or_writes_remotely_is_listed(self):
        found = collections.Counter()
        for rel, _text, tree in self.sources:
            self._scan_mutating(rel, tree, found)
        missing = _MUTATING - found
        extra = found - _MUTATING
        self.assertEqual((sorted(extra.items()), sorted(missing.items())), ([], []),
                         "a path that deletes or writes remotely appeared or went away: make it pass "
                         "core.backup_lock (refused on *.gob.ar, with a test here) and list it")


# ------------------------------------------------------------ 2. the S3 client


class _Resp:
    def __init__(self, body=b"", headers=None):
        self.body, self.headers = io.BytesIO(body), dict(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n=-1):
        return self.body.read(n)


class _Opener:
    """A fake S3 endpoint: answers every request in-process, records it."""

    def __init__(self, existing=(), fail_parts=False, head_status=None, lose_put_answer=False):
        self.calls, self.objects = [], {k: b"old" for k in existing}
        self.fail_parts, self.head_status = fail_parts, head_status
        self.lose_put_answer = lose_put_answer  # the first PUT is stored, its answer is a 503

    def open(self, req, timeout=None):
        url = urllib.parse.urlsplit(req.full_url)
        key = urllib.parse.unquote(url.path.split("/", 2)[2]) if url.path.count("/") >= 2 else ""
        query = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
        method = req.get_method()
        self.calls.append((method, key, tuple(sorted(query))))
        if method == "HEAD":
            if self.head_status:
                raise urllib.error.HTTPError(req.full_url, self.head_status, "x", {}, io.BytesIO())
            if key not in self.objects:
                raise urllib.error.HTTPError(req.full_url, 404, "x", {}, io.BytesIO())
            return _Resp(headers={"Content-Length": str(len(self.objects[key])), "ETag": '"e"'})
        if method == "PUT":
            if "partNumber" in query and self.fail_parts:
                raise urllib.error.HTTPError(req.full_url, 400, "x", {}, io.BytesIO())
            data = req.data.read() if hasattr(req.data, "read") else (req.data or b"")
            if "partNumber" not in query:
                self.objects[key] = data
            if self.lose_put_answer:
                self.lose_put_answer = False
                raise urllib.error.HTTPError(req.full_url, 503, "x", {}, io.BytesIO())
            return _Resp(headers={"ETag": '"e"'})
        if method == "GET":
            return _Resp(body=self.objects.get(key, b""))
        if method == "POST":
            if "uploads" in query:
                return _Resp(body=b"<InitiateMultipartUploadResult><UploadId>u1</UploadId></InitiateMultipartUploadResult>")
            return _Resp(body=b"<CompleteMultipartUploadResult><ETag>e</ETag></CompleteMultipartUploadResult>")
        if method == "DELETE":
            self.objects.pop(key, None)
            return _Resp()
        raise AssertionError(method)


class TestS3Client(_Env):
    def client(self, **opener):
        c = backup_s3.S3("https://r2.example", "fixture", "fixture-id", "fixture-secret", sleep=lambda _: None)
        c.opener = _Opener(**opener)
        return c

    def methods(self, c):
        return [m for m, _k, _q in c.opener.calls]

    def file(self, content=b"synthetic"):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name, "f")
        p.write_bytes(content)
        return p

    # government

    def test_government_delete_is_refused_before_anything_is_sent(self):
        self.origin(_GOV)
        c = self.client(existing=["k"])
        with self.assertRaises(backup_lock.BackupWriteForbidden) as ctx:
            c.delete("k")
        self.assertIn("sin permiso en esta instalación", str(ctx.exception))
        self.assertEqual(c.opener.calls, [])
        self.assertEqual(len(self.errors()), 1)

    def test_government_refuses_multi_delete_and_copy(self):
        self.origin(_GOV)
        c = self.client()
        with self.assertRaises(backup_lock.BackupWriteForbidden):
            c.request("POST", "", query={"delete": ""}, data=b"<Delete/>")
        with self.assertRaises(backup_lock.BackupWriteForbidden):
            c.request("PUT", "k", extra={"x-amz-copy-source": "/fixture/other"})
        self.assertEqual(c.opener.calls, [])

    def test_government_uploads_a_new_name_after_a_head(self):
        self.origin(_GOV)
        c = self.client()
        c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertEqual(self.methods(c), ["HEAD", "PUT"])
        self.assertEqual(self.errors(), [])

    def test_government_refuses_an_upload_onto_an_existing_name(self):
        self.origin(_GOV)
        c = self.client(existing=["human-rounds/hr-full-a.hrb"])
        with self.assertRaises(backup_lock.BackupWriteForbidden):
            c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertEqual(self.methods(c), ["HEAD"])
        self.assertEqual(c.opener.objects["human-rounds/hr-full-a.hrb"], b"old")

    def test_government_an_unanswered_head_never_counts_as_free(self):
        self.origin(_GOV)
        c = self.client(head_status=403)
        with self.assertRaisesRegex(backup_s3.S3Error, "read and list objects"):
            c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertEqual(self.methods(c), ["HEAD"])
        c = self.client(head_status=500)
        with self.assertRaises(backup_s3.S3Error):
            c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertNotIn("PUT", self.methods(c))

    def test_government_a_retried_put_looks_again_and_never_rewrites(self):
        self.origin(_GOV)
        c = self.client(lose_put_answer=True)
        with self.assertRaises(backup_lock.BackupWriteForbidden):
            c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertEqual(self.methods(c), ["HEAD", "PUT", "HEAD"])
        c = self.client(lose_put_answer=True)  # the same on a test stand: retried as before
        self.origin(_STAND)
        c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertEqual(self.methods(c), ["PUT", "PUT"])

    def test_government_multipart_on_an_existing_name_never_starts(self):
        self.origin(_GOV)
        c = self.client(existing=["human-rounds/hr-full-a.hrb"])
        with patch.object(backup_s3, "SINGLE_LIMIT", 1), self.assertRaises(backup_lock.BackupWriteForbidden):
            c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertEqual(self.methods(c), ["HEAD"])

    def test_government_a_failed_multipart_upload_is_not_aborted(self):
        self.origin(_GOV)
        c = self.client(fail_parts=True)
        with patch.object(backup_s3, "SINGLE_LIMIT", 1), self.assertRaises(backup_s3.S3Error):
            c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertEqual(self.methods(c), ["HEAD", "POST", "PUT"])

    def test_government_cleanup_of_incomplete_uploads_sends_nothing(self):
        import datetime as dt
        self.origin(_GOV)
        c = self.client()
        c.cleanup_incomplete("human-rounds/", before=dt.datetime.now(dt.timezone.utc),
                             owned_uploads={("human-rounds/hr-full-a.hrb", "u1")})
        self.assertEqual(c.opener.calls, [])

    def test_government_probe_writes_reads_and_keeps_its_object(self):
        self.origin(_GOV)
        c = self.client()
        self.assertTrue(c.probe("human-rounds/"))
        self.assertNotIn("DELETE", self.methods(c))
        self.assertEqual(len(c.opener.objects), 1)
        self.assertEqual(self.errors(), [])

    # test stand: as before

    def test_stand_deletes_aborts_cleans_and_probes_as_before(self):
        import datetime as dt
        c = self.client(existing=["k"])
        c.delete("k")
        self.assertEqual(self.methods(c), ["DELETE"])
        c = self.client()
        c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertEqual(self.methods(c), ["PUT"])  # no HEAD off government
        c = self.client()
        self.assertTrue(c.probe("human-rounds/"))
        self.assertEqual(self.methods(c)[-1], "DELETE")
        self.assertEqual(c.opener.objects, {})
        c = self.client(fail_parts=True)
        with patch.object(backup_s3, "SINGLE_LIMIT", 1), self.assertRaises(backup_s3.S3Error):
            c.upload("human-rounds/hr-full-a.hrb", self.file())
        self.assertEqual(self.methods(c)[-1], "DELETE")
        self.assertEqual(self.errors(), [])
        with patch.object(c, "request", return_value=(b"<ListMultipartUploadsResult><IsTruncated>false"
                                                      b"</IsTruncated></ListMultipartUploadsResult>", {})) as req:
            c.cleanup_incomplete("human-rounds/", before=dt.datetime.now(dt.timezone.utc),
                                 owned_uploads={("human-rounds/hr-full-a.hrb", "u1")})
        req.assert_called_once()


# ------------------------------------------------------------- 3. B2 native API


class TestB2Native(_Env):
    def setUp(self):
        super().setUp()
        self.urlopen = self.enterContext(patch("urllib.request.urlopen", side_effect=self._answer))
        self.answers = []

    def _answer(self, req, timeout=None):
        body = self.answers.pop(0) if self.answers else {}
        return _Resp(body=json.dumps(body).encode())

    def call(self, name, **kw):
        return data_backups._req_json("https://api.example/b2api/v2/" + name, {"Authorization": "t"}, **kw)

    def upload(self, file_name, payload):
        return data_backups._req_json(
            "https://pod.example/b2api/v2/b2_upload_file/bucket/tok",
            {"Authorization": "t", "X-Bz-File-Name": urllib.parse.quote(file_name)}, raw=payload)

    def test_government_refuses_every_call_that_deletes_hides_copies_or_is_unknown(self):
        self.origin(_GOV)
        for name in ("b2_delete_file_version", "b2_hide_file", "b2_copy_file", "b2_cancel_large_file",
                     "b2_delete_bucket", "b2_update_bucket", "b2_start_large_file", "b2_new_thing"):
            with self.subTest(name), self.assertRaises(backup_lock.BackupWriteForbidden):
                self.call(name, body={"fileName": "data/x", "fileId": "id"})
        with self.assertRaises(backup_lock.BackupWriteForbidden):
            data_backups._req_json("https://elsewhere.example/upload", {}, raw=b"x")
        self.urlopen.assert_not_called()

    def test_government_lets_reads_lists_and_the_upload_url_through(self):
        self.origin(_GOV)
        for name in ("b2_authorize_account", "b2_get_upload_url", "b2_list_file_versions",
                     "b2_list_file_names", "b2_get_file_info", "b2_download_file_by_id"):
            self.call(name, body={})
        self.assertEqual(self.urlopen.call_count, 6)
        self.assertEqual(self.errors(), [])

    def test_government_uploads_only_under_the_sha256_of_its_bytes(self):
        self.origin(_GOV)
        payload = b"photo-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        self.upload("data/cuenta-fotos/1.jpg~" + digest, payload)
        self.assertEqual(self.urlopen.call_count, 1)
        for name in ("data/cuenta-fotos/1.jpg",  # the plain name: would overwrite
                     "data/cuenta-fotos/1.jpg~" + hashlib.sha256(b"other").hexdigest()):
            with self.subTest(name), self.assertRaises(backup_lock.BackupWriteForbidden):
                self.upload(name, payload)
        with self.assertRaises(backup_lock.BackupWriteForbidden):
            data_backups._req_json("https://pod.example/b2api/v2/b2_upload_file/b/t",
                                   {"X-Bz-File-Name": "data/a~" + digest}, body={"x": 1})
        self.assertEqual(self.urlopen.call_count, 1)

    def test_government_the_real_transport_refuses_a_plain_name_before_the_upload(self):
        self.origin(_GOV)
        payload = b"photo-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        self.answers = [{"apiUrl": "https://api.example", "authorizationToken": "t"},
                        {"uploadUrl": "https://pod.example/b2api/v2/b2_upload_file/b/t", "authorizationToken": "u"}]
        with _env.patched_env({"TURNOS_B2_KEY_ID": "k", "TURNOS_B2_APP_KEY": "a", "TURNOS_B2_BUCKET_ID": "b"}), \
                self.assertRaises(backup_lock.BackupWriteForbidden):
            data_backups._upload_b2("data/cuenta-fotos/1.jpg", payload, digest, auth_cache={})
        self.assertEqual(self.urlopen.call_count, 2)  # authorize, upload URL; no upload
        name = backup_lock.addressed_name("data/cuenta-fotos/1.jpg", digest)
        self.answers = [{"apiUrl": "https://api.example", "authorizationToken": "t"},
                        {"uploadUrl": "https://pod.example/b2api/v2/b2_upload_file/b/t", "authorizationToken": "u"},
                        {"fileId": "id-1", "fileName": name, "contentLength": len(payload),
                         "contentSha1": hashlib.sha1(payload).hexdigest(), "action": "upload"}]
        with _env.patched_env({"TURNOS_B2_KEY_ID": "k", "TURNOS_B2_APP_KEY": "a", "TURNOS_B2_BUCKET_ID": "b"}):
            receipt = data_backups._upload_b2(name, payload, digest, auth_cache={})
        self.assertEqual((receipt["file_id"], receipt["remote_name"]), ("id-1", name))

    def test_stand_sends_everything_as_before(self):
        self.call("b2_delete_file_version", body={"fileName": "data/x", "fileId": "id"})
        self.call("b2_hide_file", body={})
        self.upload("data/cuenta-fotos/1.jpg", b"photo-bytes")
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual(self.errors(), [])


# ------------------------------------------------------- 4. the data/ sweep names


class TestDataSweep(_Env):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.enterContext(patch.object(data_backups, "DATA_DIR", self.dir))
        self.journal, self.uploaded, self.ledger = {}, [], {}
        self.enterContext(patch.object(db_backup, "backup_journal_map",
                                       side_effect=lambda: {k: dict(v) for k, v in self.journal.items()}))
        self.enterContext(patch.object(db_backup, "backup_journal_commit", side_effect=self._commit))
        self.enterContext(patch.object(db_backup, "backup_journal_refresh_stat",
                                       side_effect=self._refresh))
        self.rows = self.enterContext(patch.object(db_backup, "backup_version_rows",
                                                   side_effect=lambda: {k: dict(v) for k, v in self.ledger.items()}))

    def _commit(self, path, sha256, size, mtime_ns, remote_name, origin="upload", receipt=None):
        self.journal[path] = {"sha256": sha256, "size": size, "mtime_ns": mtime_ns, "remote_name": remote_name,
                              "remote_file_id": receipt and receipt["file_id"]}
        if receipt:
            self.ledger[receipt["file_id"]] = {
                "file_id": receipt["file_id"], "remote_name": receipt["remote_name"], "sha256": sha256,
                "sha1": receipt["sha1"], "size": receipt["size"], "journal_path": path}

    def tick_with_receipts(self):
        def transport(name, payload, sha):
            self.uploaded.append((name, sha))
            return {"fileId": f"id-{len(self.uploaded)}", "fileName": name, "contentLength": len(payload),
                    "contentSha1": hashlib.sha1(payload).hexdigest(), "action": "upload"}
        return data_backups.tick(subir_fn=transport)

    def test_government_content_that_comes_back_is_not_sent_again(self):
        self.origin(_GOV)
        path = os.path.join(self.dir, "cuenta-fotos/1.jpg")
        self.write("cuenta-fotos/1.jpg", b"photo A")
        self.assertEqual(self.tick_with_receipts(), 1)
        self.write("cuenta-fotos/1.jpg", b"photo B, longer")
        self.assertEqual(self.tick_with_receipts(), 1)
        self.write("cuenta-fotos/1.jpg", b"photo A")  # back to A: its name already holds these bytes
        os.utime(path, ns=(10**18, 10**18))
        self.assertEqual(self.tick_with_receipts(), 0)
        self.assertEqual([n for n, _ in self.uploaded],
                         ["data/cuenta-fotos/1.jpg~" + hashlib.sha256(b"photo A").hexdigest(),
                          "data/cuenta-fotos/1.jpg~" + hashlib.sha256(b"photo B, longer").hexdigest()])
        row = self.journal["cuenta-fotos/1.jpg"]
        self.assertEqual((row["remote_name"], row["remote_file_id"]), (self.uploaded[0][0], "id-1"))
        self.assertEqual(self.tick_with_receipts(), 0)  # and it stays quiet

    def test_government_without_the_ledger_nothing_is_sent(self):
        self.origin(_GOV)
        self.rows.side_effect = OSError("db down")
        self.write("cuenta-fotos/1.jpg", b"photo A")
        with patch.object(data_backups, "_log") as log:
            self.assertEqual(self.tick(), 0)
        self.assertEqual(self.uploaded, [])
        self.assertTrue(any("ledger unavailable" in c.args[0] for c in log.call_args_list))

    def test_stand_does_not_read_the_ledger(self):
        self.write("cuenta-fotos/1.jpg", b"photo A")
        self.tick()
        self.rows.assert_not_called()

    def _refresh(self, path, size, mtime_ns):
        self.journal[path].update(size=size, mtime_ns=mtime_ns)

    def write(self, rel, content):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    def tick(self):
        return data_backups.tick(subir_fn=lambda name, payload, sha: self.uploaded.append((name, sha)))

    def test_government_names_carry_the_sha256_and_a_change_goes_to_a_new_name(self):
        self.origin(_GOV)
        self.write("cuenta-fotos/1.jpg", b"first photo")
        self.assertEqual(self.tick(), 1)
        first = hashlib.sha256(b"first photo").hexdigest()
        self.assertEqual(self.uploaded, [("data/cuenta-fotos/1.jpg~" + first, first)])
        self.assertEqual(self.tick(), 0)  # unchanged: nothing sent
        self.write("cuenta-fotos/1.jpg", b"second, longer photo")
        self.assertEqual(self.tick(), 1)
        second = hashlib.sha256(b"second, longer photo").hexdigest()
        self.assertEqual(self.uploaded[-1], ("data/cuenta-fotos/1.jpg~" + second, second))
        self.assertEqual(len({name for name, _ in self.uploaded}), 2)  # never the same name twice

    def test_government_keeps_the_plain_names_already_uploaded_and_never_reuses_them(self):
        self.write("cuenta-fotos/1.jpg", b"first photo")
        self.assertEqual(self.tick(), 1)  # before the lock: the plain name
        self.origin(_GOV)
        self.assertEqual(self.tick(), 0)  # nothing re-sent for an unchanged file
        os.utime(os.path.join(self.dir, "cuenta-fotos/1.jpg"), ns=(1, 1))
        self.assertEqual(self.tick(), 0)  # same bytes, new mtime: the row only learns the stat
        self.write("cuenta-fotos/1.jpg", b"second, longer photo")
        self.assertEqual(self.tick(), 1)
        self.assertEqual([n for n, _ in self.uploaded],
                         ["data/cuenta-fotos/1.jpg",
                          "data/cuenta-fotos/1.jpg~" + hashlib.sha256(b"second, longer photo").hexdigest()])

    def test_stand_keeps_the_plain_name_as_before(self):
        self.write("cuenta-fotos/1.jpg", b"first photo")
        self.write("cuenta-fotos/1.jpg", b"first photo")
        self.tick()
        self.write("cuenta-fotos/1.jpg", b"second, longer photo")
        self.tick()
        self.assertEqual([n for n, _ in self.uploaded], ["data/cuenta-fotos/1.jpg"] * 2)


class TestPurge(_Env):
    def test_government_an_injected_purge_deletes_nothing(self):
        self.origin(_GOV)
        list_fn, delete_fn = MagicMock(), MagicMock()
        with patch.object(db_backup, "purged_files_pending") as pending:
            self.assertEqual(data_backups._purge_remote(None, "data/", (list_fn, delete_fn)), 0)
        pending.assert_not_called()
        list_fn.assert_not_called()
        delete_fn.assert_not_called()
        (line,) = self.errors()
        self.assertIn("remote purge", line)

    def test_stand_purges_as_before(self):
        list_fn = MagicMock(return_value=[{"fileName": "data/ordenes/x.pdf", "fileId": "id-1"}])
        delete_fn = MagicMock()
        with patch.object(db_backup, "purged_files_pending", return_value=["ordenes/x.pdf"]), \
                patch.object(db_backup, "backup_version_tombstone"), \
                patch.object(db_backup, "purged_file_remote_done"), \
                patch.object(db_backup, "backup_purge_state_save"), \
                patch.object(data_backups, "_data_dir", return_value=tempfile.gettempdir() + "/no-such-dir"):
            self.assertEqual(data_backups._purge_remote(None, "data/", (list_fn, delete_fn)), 1)
        delete_fn.assert_called_once_with("data/ordenes/x.pdf", "id-1")

    def test_stand_purges_the_content_addressed_copies_too(self):
        # Copies made while the installation was municipal carry `~<sha256>`: a
        # purge where it is allowed must not call the path done and leave them.
        addressed = backup_lock.addressed_name("data/ordenes/x.pdf", hashlib.sha256(b"x").hexdigest())
        list_fn = MagicMock(return_value=[{"fileName": "data/ordenes/x.pdf", "fileId": "id-1"},
                                          {"fileName": addressed, "fileId": "id-2"},
                                          {"fileName": "data/ordenes/x.pdf.bak", "fileId": "id-3"}])
        delete_fn = MagicMock()
        with patch.object(db_backup, "purged_files_pending", return_value=["ordenes/x.pdf"]), \
                patch.object(db_backup, "backup_version_tombstone") as tomb, \
                patch.object(db_backup, "purged_file_remote_done") as done, \
                patch.object(db_backup, "backup_purge_state_save"), \
                patch.object(data_backups, "_data_dir", return_value=tempfile.gettempdir() + "/no-such-dir"):
            self.assertEqual(data_backups._purge_remote(None, "data/", (list_fn, delete_fn)), 1)
        self.assertEqual([c.args for c in delete_fn.call_args_list],
                         [("data/ordenes/x.pdf", "id-1"), (addressed, "id-2")])
        self.assertEqual([c.args[:2] for c in tomb.call_args_list],
                         [("id-1", "data/ordenes/x.pdf"), ("id-2", addressed)])
        done.assert_called_once()


# ------------------------------------------------------- 5. the operator tools


class TestOperatorTools(_Env):
    def test_government_the_exact_delete_is_refused_before_authorizing(self):
        self.origin(_GOV)
        with patch.object(data_backups, "_authorize") as auth, \
                self.assertRaises(backup_lock.BackupWriteForbidden):
            restore.delete_remote_version("data/x.pdf", "id-1")
        auth.assert_not_called()

    def test_stand_the_exact_delete_passes_the_lock(self):
        with patch.object(data_backups, "configured", return_value=False), \
                self.assertRaisesRegex(restore.BackupVersionError, "not configured"):
            restore.delete_remote_version("data/x.pdf", "id-1")

    def plan(self):
        return {"candidates": [{"file_id": "id-1", "remote_name": "data/x.pdf"},
                               {"file_id": "id-2", "remote_name": "data/y.pdf"}], "blockers": {}}

    def test_government_an_applied_retention_is_refused_before_the_first_candidate(self):
        self.origin(_GOV)
        delete_fn, tomb = MagicMock(), MagicMock()
        with self.assertRaises(backup_lock.BackupWriteForbidden):
            restore.apply_retention(self.plan(), delete_fn, tomb, dry_run=False)
        delete_fn.assert_not_called()
        tomb.assert_not_called()
        out = restore.apply_retention(self.plan(), delete_fn, tomb)  # the dry run stays
        self.assertEqual([o["status"] for o in out], ["would-delete"] * 2)

    def test_stand_an_applied_retention_deletes_as_before(self):
        delete_fn, tomb = MagicMock(), MagicMock()
        restore.apply_retention(self.plan(), delete_fn, tomb, dry_run=False)
        self.assertEqual(delete_fn.call_count, 2)

    def test_a_restore_puts_a_content_addressed_name_back_on_its_plain_path(self):
        payload = b"photo-bytes"
        sha256, sha1 = hashlib.sha256(payload).hexdigest(), hashlib.sha1(payload).hexdigest()
        name = backup_lock.addressed_name("data/cuenta-fotos/1.jpg", sha256)
        version = {"file_id": "id-1", "remote_name": name, "sha256": sha256, "sha1": sha1, "size": len(payload)}
        meta = {"fileId": "id-1", "fileName": name, "contentSha1": sha1, "contentLength": len(payload)}
        for origin in (_GOV, _STAND):
            with self.subTest(origin), tempfile.TemporaryDirectory() as dest, \
                    tempfile.TemporaryDirectory() as scratch:
                self.origin(origin)
                target = restore.restore_version(version, dest, scratch, lambda _id: (meta, payload),
                                                 prefix="data/", purged_paths=set())
                self.assertEqual(target, os.path.join(dest, "cuenta-fotos", "1.jpg"))
                self.assertEqual(Path(target).read_bytes(), payload)

    def test_plain_name_only_strips_its_own_digest(self):
        sha = hashlib.sha256(b"a").hexdigest()
        self.assertEqual(backup_lock.plain_name("data/a.jpg~" + sha, sha), "data/a.jpg")
        self.assertEqual(backup_lock.plain_name("data/a.jpg~" + sha, "0" * 64), "data/a.jpg~" + sha)
        self.assertEqual(backup_lock.plain_name("data/a~b.jpg"), "data/a~b.jpg")


# --------------------------------------------- 6. the rotation of the full backups


class TestFullRotation(_Env):
    def cfg(self, apply):
        return {"TURNOS_BACKUP_RETENTION_APPLY": apply, "TURNOS_BACKUP_S3_PREFIX": "human-rounds/"}

    def test_government_the_rotation_deletes_nothing_and_says_so(self):
        self.origin(_GOV)
        cloud = MagicMock()
        plan = [{"id": "a", "archive_key": "human-rounds/hr-full-a.hrb"}]
        self.assertEqual(backup_full.apply_retention(plan, self.cfg("1"), cloud, keys={}, store=None), [])
        cloud.delete.assert_not_called()
        cloud.download.assert_not_called()
        (line,) = self.errors()
        self.assertIn("TURNOS_BACKUP_RETENTION_APPLY=1", line)
        self.assertEqual(backup_full.apply_retention(plan, self.cfg("0"), cloud, keys={}, store=None), [])
        self.assertEqual(len(self.errors()), 1)  # off: quiet

    def _five_tested_backups_then_one_more(self, root):
        """Real `backup_full.run` through the real S3 client (in-process
        endpoint): five backups marked restore-tested, then a sixth whose
        rotation has candidates. Returns (results, the client)."""
        import datetime as dt
        from app.core import backup_crypto as crypto
        from test_backup_full import MemoryStore  # noqa: PLC0415 - a sibling test's fixture
        data = Path(root, "data")
        data.mkdir()
        (data / "synthetic.txt").write_text("fixture")
        env_file = Path(root, "turnos.env")
        env_file.write_text("FIXTURE=1\n")
        key = b"k" * 32
        cfg = dict(backup_settings.DEFAULTS, TURNOS_BACKUP_ENABLED="1",
                   TURNOS_BACKUP_S3_ENDPOINT="https://r2.example", TURNOS_BACKUP_S3_BUCKET="fixture",
                   TURNOS_BACKUP_S3_KEY_ID="synthetic", TURNOS_BACKUP_S3_SECRET="synthetic",
                   TURNOS_BACKUP_RETENTION_APPLY="1", TURNOS_BACKUP_RETAIN_DAILY="1",
                   TURNOS_BACKUP_RETAIN_WEEKLY="1", TURNOS_BACKUP_RETAIN_MONTHLY="1")
        cfg.pop(backup_settings.KEY)
        cfg.pop(backup_settings.OLD_KEYS)
        cloud = backup_s3.S3("https://r2.example", "fixture", "synthetic", "synthetic", sleep=lambda _: None)
        cloud.opener = _Opener()
        store = MemoryStore()

        def dump(path, _url):
            Path(path).write_bytes(b"PGDMP-synthetic")
            return {"snapshot_at": backup_full.timestamp(), "references": [], "counts": {},
                    "alembic_head": "fixture", "tools": {"pg_dump": "fixture"}}

        now = dt.datetime.now(dt.timezone.utc)
        results = []
        with patch.object(backup_settings, "keys", return_value={crypto.key_id(key): key}), \
                patch.object(backup_settings, "key_status", return_value={"configured": True}), \
                _env.patched_env({"SOURCE_COMMIT": "0123456789abcdef0123456789abcdef01234567"}), \
                patch.object(backup_full.db, "_db_url", return_value=""):
            for i in range(6):
                result = backup_full.run(cfg=cfg, cloud=cloud, store=store, dump_fn=dump, roots={"data": data},
                                         private_packs=None, env_file=env_file)
                results.append(result)
                row = next(r for r in store.rows if r["id"] == result["id"])
                row.update(started_at=(now - dt.timedelta(days=6 - i)).isoformat(), restore_tested=True,
                           restore_tested_at=now.isoformat())
        return results, cloud

    def test_government_a_real_full_backup_runs_and_rotates_nothing(self):
        self.origin(_GOV)
        with tempfile.TemporaryDirectory() as root:
            results, cloud = self._five_tested_backups_then_one_more(root)
        self.assertTrue(all(r["uploaded"] and r["integrity_verified"] for r in results))
        self.assertTrue(results[-1]["retention_plan"])  # there was something to rotate
        methods = [m for m, _k, _q in cloud.opener.calls]
        self.assertNotIn("DELETE", methods)
        puts = [i for i, m in enumerate(methods) if m == "PUT"]
        self.assertEqual(len(puts), 6)
        self.assertTrue(all(methods[i - 1] == "HEAD" for i in puts))  # every new object looked first
        self.assertEqual(len(cloud.opener.objects), 6)
        self.assertTrue(any("TURNOS_BACKUP_RETENTION_APPLY=1" in line for line in self.errors()))

    def test_stand_the_same_backups_rotate_as_before(self):
        with tempfile.TemporaryDirectory() as root:
            results, cloud = self._five_tested_backups_then_one_more(root)
        self.assertIn("DELETE", [m for m, _k, _q in cloud.opener.calls])
        self.assertLess(len(cloud.opener.objects), 6)
        self.assertEqual(self.errors(), [])

    def test_stand_the_rotation_passes_the_lock(self):
        with self.assertRaisesRegex(backup_full.BackupError, "advisory lock"):
            backup_full.apply_retention([], self.cfg("1"), MagicMock(), keys={}, store=None)

    def test_the_signal_at_start_and_in_the_worker(self):
        self.origin(_GOV)
        with patch.object(purge_guard, "_log") as log:
            self.assertFalse(purge_guard.warn_rotation_ignored("start"))
            with _env.patched_env({"TURNOS_BACKUP_RETENTION_APPLY": "1"}):
                self.assertTrue(purge_guard.warn_rotation_ignored("start"))
                with patch("app.ops.backup_full_queue.register") as register:
                    worker._register_backup_full("app")
                register.assert_called_once()
        lines = [c.args[0] for c in log.call_args_list]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith("ERROR") for line in lines))
        self.assertIn("(worker start)", lines[1])
        self.origin(_STAND)
        with _env.patched_env({"TURNOS_BACKUP_RETENTION_APPLY": "1"}), patch.object(purge_guard, "_log") as log:
            self.assertFalse(purge_guard.warn_rotation_ignored("start"))
        log.assert_not_called()


# -------------------------------------- 7. the manual tools: settings, setup, «Sistema»


class TestManualTools(_Env):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(config, "locked", return_value=False))
        self.save = self.enterContext(patch.object(config, "guardar"))
        self.enterContext(patch.object(settings_admin, "_api_keys_payload", return_value={"ok": True}))

    def test_government_the_panel_refuses_to_switch_the_rotation_on_in_every_language(self):
        self.origin(_GOV)
        seen = set()
        for lang in _LANGS:
            with patch.object(i18n, "active", return_value=lang):
                with self.assertRaises(settings_admin.SettingsError) as ctx:
                    settings_admin.resolve_save("api_keys", {"TURNOS_BACKUP_RETENTION_APPLY": "1"}, [])
            seen.add(str(ctx.exception))
        self.assertEqual(len(seen), 10)  # none fell back to the Spanish
        self.assertIn("Sin permiso en esta instalación", i18n.t("backup.forbidden", "es"))
        self.save.assert_not_called()
        settings_admin.resolve_save("api_keys", {"TURNOS_BACKUP_RETENTION_APPLY": "0"}, [])  # off is fine
        self.save.assert_called_once()

    def test_stand_the_panel_saves_the_switch_as_before(self):
        settings_admin.resolve_save("api_keys", {"TURNOS_BACKUP_RETENTION_APPLY": "1"}, [])
        self.assertEqual(self.save.call_args.args[0].get("TURNOS_BACKUP_RETENTION_APPLY"), "1")

    def test_the_setup_refuses_it_on_government_only(self):
        self.origin(_GOV)
        with self.assertRaises(backup_lock.BackupWriteForbidden):
            backup_setup.update(None, {"TURNOS_BACKUP_RETENTION_APPLY": "1", "action": "skip"})
        self.origin(_STAND)
        token, result = backup_setup.update(None, {"TURNOS_BACKUP_RETENTION_APPLY": "1", "action": "skip"})
        self.assertTrue(result["continue"])
        backup_setup.discard(token)

    def test_the_probe_says_its_object_stays_on_government(self):
        client = MagicMock()
        client.probe.return_value = True
        with patch.object(backup_settings, "client", return_value=client):
            stand = settings_admin.resolve_probe("backup")
            self.origin(_GOV)
            gov = {lang: None for lang in _LANGS}
            for lang in _LANGS:
                with patch.object(i18n, "active", return_value=lang):
                    gov[lang] = settings_admin.resolve_probe("backup")
        self.assertEqual(stand, {"ok": True, "detalle": "Conexión comprobada"})
        self.assertTrue(all(r["ok"] for r in gov.values()))
        self.assertEqual(len({r["detalle"] for r in gov.values()}), 10)
        self.assertIn("queda en el bucket", gov["es"]["detalle"])

    def _backup_block(self):
        with patch("app.core.backup_journal.Store") as store, \
                patch.object(backup_settings, "key_status", return_value={"configured": False}):
            store.return_value.list.return_value = []
            return system_status._cluster_backup()

    def test_sistema_warns_when_the_rotation_is_switched_on_on_government(self):
        self.origin(_GOV)
        self.assertEqual(len(self._backup_block()["items"]), 1)
        seen = set()
        with _env.patched_env({"TURNOS_BACKUP_RETENTION_APPLY": "1"}):
            for lang in _LANGS:
                with patch.object(i18n, "active", return_value=lang):
                    block = self._backup_block()
                item = block["items"][-1]
                self.assertEqual((item["estado"], item["campo"]), ("warn", "TURNOS_BACKUP_RETENTION_APPLY"))
                self.assertIn("TURNOS_BACKUP_RETENTION_APPLY=1", item["detalle"], lang)
                self.assertEqual(block["estado"], "warn")
                seen.add(item["valor"])
        self.assertEqual(len(seen), 10)
        self.origin(_STAND)
        with _env.patched_env({"TURNOS_BACKUP_RETENTION_APPLY": "1"}):
            self.assertEqual(len(self._backup_block()["items"]), 1)


class TestLocales(unittest.TestCase):
    KEYS = ("backup.forbidden", "backup.probe_ok_kept", "backup.rotation_ignored.label",
            "backup.rotation_ignored.value", "backup.rotation_ignored.detail")

    def load(self, lang, name):
        with open(os.path.join(ROOT, "locales", lang, name), encoding="utf-8") as fh:
            return json.load(fh)

    def test_ten_languages_and_the_source_hashes(self):
        es = self.load("es", "backup.json")
        self.assertEqual(es["backup.forbidden"], backup_lock.FORBIDDEN_ES)
        self.assertEqual(es["backup.probe_ok_kept"], backup_lock.PROBE_KEPT_ES)
        for lang in _LANGS:
            data = self.load(lang, "backup.json")
            for key in self.KEYS:
                self.assertTrue(data.get(key), (lang, key))
            if lang in ("es", "en"):
                continue
            hashes = self.load(lang, "_source_hash.json")
            for key in self.KEYS:
                self.assertEqual(hashes.get(key), hashlib.sha256(es[key].encode()).hexdigest()[:16], (lang, key))


if __name__ == "__main__":
    unittest.main()
