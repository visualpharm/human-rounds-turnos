"""S3 wire contract: loopback fixture only, never a provider account."""
import hashlib
import datetime as dt
import io
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app.core import backup_s3 as s
import _env  # noqa: E402


class FakeS3(BaseHTTPRequestHandler):
    objects = {}
    calls = []
    fail = 0
    interrupted = False
    def log_message(self, *_):
        pass
    def reply(self, code=200, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.calls.append(("PUT", self.path))
        if self.fail:
            type(self).fail -= 1
            return self.reply(503)
        if "partNumber=" in self.path and self.interrupted:
            return self.reply(400)
        self.objects[self.path] = body
        self.reply(headers={"ETag": '"' + hashlib.md5(body).hexdigest() + '"'})
    def do_GET(self):
        self.calls.append(("GET", self.path))
        if "list-type=2" in self.path:
            if "continuation-token=" not in self.path:
                return self.reply(body=b"<ListBucketResult><IsTruncated>true</IsTruncated><NextContinuationToken>two</NextContinuationToken><Contents><Key>one</Key></Contents></ListBucketResult>")
            return self.reply(body=b"<ListBucketResult><IsTruncated>false</IsTruncated><Contents><Key>two</Key></Contents></ListBucketResult>")
        self.reply(body=self.objects[self.path])
    def do_HEAD(self):
        body = self.objects[self.path]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"' + hashlib.md5(body).hexdigest() + '"')
        self.end_headers()
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.calls.append(("POST", self.path))
        self.reply(body=b"<InitiateMultipartUploadResult><UploadId>fixture</UploadId></InitiateMultipartUploadResult>" if "uploads=" in self.path else b"<CompleteMultipartUploadResult><ETag>fixture</ETag></CompleteMultipartUploadResult>")
    def do_DELETE(self):
        self.calls.append(("DELETE", self.path))
        self.objects.pop(self.path, None)
        self.reply(204)


class SigV4VectorTest(unittest.TestCase):
    # Same IAM/S3 request shapes as the documented SigV4 examples, with
    # synthetic credentials. Expected HMACs were derived independently; see
    # docs/solutions/security/slice7-sigv4-proof.json. No socket required.
    def test_sigv4_iam_synthetic_signing_key_vector(self):
        key = s.signing_key("synthetic-sigv4-secret", "20150830", "us-east-1", "iam")
        self.assertEqual(key.hex(), "4b2c9b47b2d985b926a9bcecff5534886d47495ca93edf07eb3c556cd5c4fb12")

    def test_sigv4_s3_synthetic_get_object_signature_vector(self):
        headers = s.signed_headers(
            "GET", "https://examplebucket.s3.amazonaws.com/test.txt", hashlib.sha256(b"").hexdigest(),
            "synthetic-sigv4-access", "synthetic-sigv4-secret", "us-east-1",
            now=dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc), extra={"Range": "bytes=0-9"})
        self.assertEqual(headers["Authorization"].split(",")[0],
                         "AWS4-HMAC-SHA256 Credential=synthetic-sigv4-access/20130524/us-east-1/s3/aws4_request")
        self.assertTrue(headers["Authorization"].endswith(
            "Signature=51d869dd643ccd0c60504d503e9bb21723a28e2efb6fcc5430f585db8b68d4fc"))


class S3Test(unittest.TestCase):
    def setUp(self):
        FakeS3.objects, FakeS3.calls, FakeS3.fail, FakeS3.interrupted = {}, [], 0, False
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
        except PermissionError:
            self.skipTest("Sandbox forbids loopback bind; in-process protocol tests run separately")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.enterContext(_env.patched_env(TURNOS_TEST_SEAMS="1", TURNOS_TEST_BACKUP_S3_ENDPOINT=f"http://127.0.0.1:{self.server.server_port}", TURNOS_OUTBOUND_KILL="0"))
        self.client = s.S3("https://r2.example", "fixture", "fixture-id", "fixture-secret", sleep=lambda _: None)

    def test_put_head_download_probe_delete_and_retry(self):
        FakeS3.fail = 1
        with tempfile.TemporaryDirectory() as root:
            p = Path(root, "fixture")
            p.write_bytes(b"synthetic")
            self.client.upload("path/a b", p)
            self.assertEqual(self.client.head("path/a b")["size"], 9)
            self.client.download("path/a b", Path(root, "copy"))
            self.assertEqual(Path(root, "copy").read_bytes(), b"synthetic")
        self.client.probe("tests/")
        self.assertTrue(any(method == "DELETE" for method, _ in FakeS3.calls))
        self.assertEqual(len([c for c in FakeS3.calls if c == ("PUT", "/fixture/path/a%20b")]), 2)

    def test_list_carries_pagination_token(self):
        self.assertEqual([r["key"] for r in self.client.list("tests/")], ["one", "two"])

    def test_interrupted_multipart_aborts(self):
        FakeS3.interrupted = True
        with tempfile.TemporaryDirectory() as root, patch.object(s, "SINGLE_LIMIT", 1):
            p = Path(root, "fixture")
            p.write_bytes(b"synthetic")
            with self.assertRaises(s.S3Error):
                self.client.upload("tests/hr-full-fixture.hrb", p)
        self.assertTrue(any(m == "DELETE" and "uploadId=fixture" in p for m, p in FakeS3.calls))

    def test_kill_switch_and_double_gate_fail_closed(self):
        with _env.patched_env({"TURNOS_OUTBOUND_KILL": "1"}):
            with self.assertRaises(s.S3Error):
                self.client.probe("tests/")
        self.assertEqual(FakeS3.calls, [])
        with _env.patched_env({"TURNOS_TEST_SEAMS": "0"}):
            with self.assertRaises(ValueError):
                s.validate_endpoint("http://127.0.0.1:9000")

    def test_https_endpoint_policy(self):
        for url in ("http://r2.example", "https://u:p@r2.example", "https://r2.example/?x=1", "https://r2.example/#x"):
            with self.assertRaises(ValueError):
                s.validate_endpoint(url)


class _Response(io.BytesIO):
    def __init__(self, body=b"", headers=None):
        super().__init__(body)
        self.headers = headers or {}


class _ProtocolFake:
    """Same S3 outcomes over urllib Request objects, without a socket."""
    def open(self, req, timeout=None):
        url = urllib.parse.urlsplit(req.full_url)
        path = url.path + ("?" + url.query if url.query else "")
        method = req.get_method()
        data = req.data.read() if hasattr(req.data, "read") else req.data or b""
        if method in {"PUT", "POST", "GET", "DELETE"}:
            FakeS3.calls.append((method, path))
        if method == "PUT":
            if FakeS3.fail:
                FakeS3.fail -= 1
                raise urllib.error.HTTPError(req.full_url, 503, "fixture", {}, io.BytesIO())
            if "partNumber=" in path and FakeS3.interrupted:
                raise urllib.error.HTTPError(req.full_url, 400, "fixture", {}, io.BytesIO())
            FakeS3.objects[path] = data
            return _Response(headers={"ETag": '"' + hashlib.md5(data).hexdigest() + '"'})
        if method == "POST":
            return _Response(b"<InitiateMultipartUploadResult><UploadId>fixture</UploadId></InitiateMultipartUploadResult>" if "uploads=" in path
                             else b"<CompleteMultipartUploadResult><ETag>fixture</ETag></CompleteMultipartUploadResult>")
        if method == "DELETE":
            FakeS3.objects.pop(path, None)
            return _Response()
        if "list-type=2" in path:
            return _Response(b"<ListBucketResult><IsTruncated>true</IsTruncated><NextContinuationToken>two</NextContinuationToken><Contents><Key>one</Key></Contents></ListBucketResult>" if "continuation-token=" not in path
                             else b"<ListBucketResult><IsTruncated>false</IsTruncated><Contents><Key>two</Key></Contents></ListBucketResult>")
        data = FakeS3.objects[path]
        return _Response(data if method == "GET" else b"", headers={"Content-Length": str(len(data)), "ETag": '"' + hashlib.md5(data).hexdigest() + '"'})


class S3InProcessTest(S3Test):
    def setUp(self):
        FakeS3.objects, FakeS3.calls, FakeS3.fail, FakeS3.interrupted = {}, [], 0, False
        self.enterContext(_env.patched_env(TURNOS_TEST_SEAMS="1", TURNOS_TEST_BACKUP_S3_ENDPOINT="http://127.0.0.1:9999", TURNOS_OUTBOUND_KILL="0"))
        self.client = s.S3("https://r2.example", "fixture", "fixture-id", "fixture-secret", sleep=lambda _: None)
        self.client.opener = _ProtocolFake()

    def test_incomplete_cleanup_requires_exact_journaled_upload_id(self):
        original = self.client.opener
        class Incomplete:
            def open(_self, req, timeout=None):
                if req.get_method() == "GET" and "uploads=" in req.full_url:
                    return _Response(b"""<ListMultipartUploadsResult><IsTruncated>false</IsTruncated>
                        <Upload><Key>tests/hr-full-owned.hrb</Key><UploadId>ours</UploadId><Initiated>2020-01-01T00:00:00Z</Initiated></Upload>
                        <Upload><Key>tests/hr-full-owned.hrb</Key><UploadId>foreign</UploadId><Initiated>2020-01-01T00:00:00Z</Initiated></Upload>
                        <Upload><Key>tests/hr-full-other.hrb</Key><UploadId>other</UploadId><Initiated>2020-01-01T00:00:00Z</Initiated></Upload>
                        </ListMultipartUploadsResult>""")
                return original.open(req, timeout)
        self.client.opener = Incomplete()
        self.client.cleanup_incomplete("tests/", before=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                                       owned_uploads={("tests/hr-full-owned.hrb", "ours")})
        deletes = [path for method, path in FakeS3.calls if method == "DELETE"]
        self.assertEqual(deletes, ["/fixture/tests/hr-full-owned.hrb?uploadId=ours"])

    def test_multipart_completion_keeps_part_order_and_etags(self):
        original, completions = self.client.opener, []
        class Recording:
            def open(_self, req, timeout=None):
                if req.get_method() == "POST" and "uploadId=" in req.full_url:
                    completions.append(req.data)
                return original.open(req, timeout)
        self.client.opener = Recording()
        with tempfile.TemporaryDirectory() as root, patch.object(s, "SINGLE_LIMIT", 1), patch.object(s, "PART_SIZE", 4):
            path = Path(root, "fixture")
            path.write_bytes(b"abcdefghij")
            self.assertEqual(self.client.upload("tests/hr-full-synthetic.hrb", path), "fixture")
        root = s._xml(completions[0])
        self.assertEqual([p.findtext("PartNumber") for p in root.findall("Part")], ["1", "2", "3"])
        self.assertTrue(all(p.findtext("ETag") for p in root.findall("Part")))
