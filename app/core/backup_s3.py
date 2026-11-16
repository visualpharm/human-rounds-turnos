"""Small path-style HTTPS S3/R2 transport. No redirects or ambient proxies.

Retries are bounded and replay only idempotent operations. Multipart initiation
is not retried: an ambiguous initiation is recoverable through the prefix-scoped
incomplete-upload listing. Only hr-full-* uploads are eligible for cleanup.

On the municipal installation every request first passes `core.backup_lock`:
no DELETE (an abandoned multipart upload and the probe's test object stay),
and a new object only under a name that is not taken yet.
"""
import datetime as dt
import hashlib
import hmac
import ipaddress
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from app.core import backup_lock, outbound_guard, queue_infra
from app.core.status_error import RemoteError

SINGLE_LIMIT = 100 * 1024 * 1024
PART_SIZE = 16 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024


class S3Error(RemoteError, RuntimeError):
    """Transport error without URLs, credentials or response bodies.
    `status` is the HTTP status when the server answered one."""


def validate_endpoint(endpoint, *, test=False):
    p = urllib.parse.urlsplit(endpoint)
    local = (test and queue_infra.test_seams_enabled() and p.scheme == "http"
             and p.hostname in {"127.0.0.1", "::1", "localhost"})
    if (not p.hostname or (p.scheme != "https" and not local) or p.username or p.password
            or p.query or p.fragment or p.path not in ("", "/")):
        raise ValueError("Use an HTTPS S3 endpoint without credentials, path, query or fragment")
    try:
        p.port
    except ValueError:
        raise ValueError("Invalid S3 endpoint port") from None
    return endpoint.rstrip("/")


def signing_key(secret, date, region, service="s3"):
    key = ("AWS4" + secret).encode()
    for value in (date, region, service, "aws4_request"):
        key = hmac.digest(key, value.encode(), "sha256")
    return key


def signed_headers(method, url, body_hash, access, secret, region, *, now=None, extra=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    p = urllib.parse.urlsplit(url)
    headers = {"host": p.netloc, "x-amz-date": stamp, "x-amz-content-sha256": body_hash}
    headers.update({k.lower(): " ".join(str(v).split()) for k, v in (extra or {}).items()})
    names = ";".join(sorted(headers))
    canonical_headers = "".join(k + ":" + headers[k] + "\n" for k in sorted(headers))
    query = "&".join(k + "=" + v for k, v in sorted(
        (urllib.parse.quote(k, safe="-_.~"), urllib.parse.quote(v, safe="-_.~"))
        for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)))
    request = "\n".join((method, p.path or "/", query, canonical_headers, names, body_hash))
    scope = date + "/" + region + "/s3/aws4_request"
    string = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(request.encode()).hexdigest()))
    signature = hmac.digest(signing_key(secret, date, region), string.encode(), "sha256").hex()
    headers["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={names}, Signature={signature}"
    return headers


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kw):
        return None


def _xml(raw):
    if len(raw) > MAX_XML or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise S3Error("S3 XML exceeds protocol bounds")
    try:
        root = ET.fromstring(raw)
        for el in root.iter():
            el.tag = el.tag.split("}")[-1]
        return root
    except ET.ParseError:
        raise S3Error("Invalid S3 XML response") from None


class S3:
    def __init__(self, endpoint, bucket, access, secret, region="auto", *, sleep=time.sleep):
        validate_endpoint(endpoint)
        seam = os.environ.get("TURNOS_TEST_BACKUP_S3_ENDPOINT") if queue_infra.test_seams_enabled() else ""
        self.endpoint = validate_endpoint(seam, test=True) if seam else endpoint.rstrip("/")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}", bucket or ""):
            raise ValueError("Invalid S3 bucket name")
        if not re.fullmatch(r"[a-z0-9-]{1,40}", region):
            raise ValueError("Invalid S3 signing region")
        self.bucket, self.access, self.secret, self.region = bucket, access, secret, region
        self.sleep = sleep
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    @staticmethod
    def preflight():
        if outbound_guard.requested():
            raise S3Error("TURNOS_OUTBOUND_KILL=1 forbids backup transport")

    def _url(self, key="", query=None):
        if key.startswith("/") or any(x in (".", "..") for x in key.split("/")) or "\\" in key or "\x00" in key:
            raise S3Error("Unsafe S3 object name")
        url = self.endpoint + "/" + self.bucket + "/" + urllib.parse.quote(key, safe="/-_.~")
        if query:
            url += "?" + urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote)
        return url

    def request(self, method, key="", *, query=None, data=b"", output=None, extra=None, retry=True):
        self.preflight()
        path = data if isinstance(data, Path) else None
        if path:
            with path.open("rb") as inp:
                digest = hashlib.file_digest(inp, "sha256").hexdigest()
            size = path.stat().st_size
        else:
            digest, size = hashlib.sha256(data).hexdigest(), len(data)
        url = self._url(key, query)
        for attempt in range(3 if retry else 1):
            self.preflight()
            # Before every attempt and outside the try below (a refusal is an
            # OSError and must not turn into a retry): a retried PUT whose
            # first answer got lost finds its name taken and stops.
            backup_lock.check_s3_request(method, query, extra, exists=lambda: self._taken(key))
            handle = path.open("rb") if path else None
            try:
                headers = signed_headers(method, url, digest, self.access, self.secret, self.region, extra=extra)
                headers["Content-Length"] = str(size)
                req = urllib.request.Request(url, data=(handle if path else data) if method in ("PUT", "POST") else None,
                                             headers=headers, method=method)
                with self.opener.open(req, timeout=120) as resp:
                    response_headers = dict((k.lower(), v) for k, v in resp.headers.items())
                    if output is not None:
                        # Replace each failed attempt's partial download, never append.
                        with Path(output).open("wb") as out:
                            while block := resp.read(1024 * 1024):
                                out.write(block)
                        return b"", response_headers
                    body = resp.read(MAX_XML + 1) if method != "HEAD" else b""
                    if len(body) > MAX_XML:
                        raise S3Error("S3 response exceeds protocol bounds")
                    return body, response_headers
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                if code not in (408, 429, 500, 502, 503, 504) or attempt == 2 or not retry:
                    raise S3Error(f"S3 {method} failed (HTTP {code})", code) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 2 or not retry:
                    raise S3Error(f"S3 {method} transport unavailable") from None
            finally:
                if handle:
                    handle.close()
            self.sleep(0.5 * 2**attempt)
        raise S3Error("S3 request failed")

    def upload(self, key, path):
        path = Path(path)
        if path.stat().st_size <= SINGLE_LIMIT:
            return self.request("PUT", key, data=path)[1].get("etag")
        upload_id = None
        try:
            raw, _ = self.request("POST", key, query={"uploads": ""}, retry=False)
            upload_id = _xml(raw).findtext("UploadId")
            if not upload_id:
                raise S3Error("Multipart initiation lacks upload id")
            callback = getattr(self, "on_multipart_started", None)
            if callback:
                callback(key, upload_id)
            parts = []
            with path.open("rb") as inp:
                while block := inp.read(PART_SIZE):
                    if len(parts) >= 10000:
                        raise S3Error("Archive exceeds S3 multipart bound")
                    number = len(parts) + 1
                    _, headers = self.request("PUT", key, query={"uploadId": upload_id, "partNumber": str(number)}, data=block)
                    etag = headers.get("etag")
                    if not etag:
                        raise S3Error("Multipart part lacks ETag")
                    parts.append((number, etag))
            root = ET.Element("CompleteMultipartUpload")
            for number, etag in parts:
                part = ET.SubElement(root, "Part")
                ET.SubElement(part, "PartNumber").text = str(number)
                ET.SubElement(part, "ETag").text = etag
            raw, _ = self.request("POST", key, query={"uploadId": upload_id}, data=ET.tostring(root))
            result = _xml(raw)
            if result.tag == "Error":
                raise S3Error("Multipart completion refused")
            return result.findtext("ETag")
        except BaseException:
            # The municipal installation never deletes: the abandoned upload
            # stays until the bucket's own lifecycle cancels it.
            if upload_id and not backup_lock.locked():
                try:
                    self.request("DELETE", key, query={"uploadId": upload_id})
                except S3Error:
                    pass  # prefix-scoped stale-upload cleanup on the next job
            raise

    def _taken(self, key):
        """Whether an object named `key` exists (HEAD 404 = free); any other
        failure is raised, so an unanswered check never counts as free."""
        try:
            self.request("HEAD", key)
        except S3Error as exc:
            if exc.status == 404:
                return False
            if exc.status in (401, 403):
                # AWS answers 403 for a missing object to a key without ListBucket.
                raise S3Error(f"S3 HEAD refused (HTTP {exc.status}): this installation checks that a backup "
                              "name is free before writing, so the key must be allowed to read and list "
                              "objects", exc.status) from None
            raise
        return True

    def head(self, key):
        _, headers = self.request("HEAD", key)
        try:
            return {"size": int(headers["content-length"]), "etag": headers.get("etag")}
        except (KeyError, ValueError):
            raise S3Error("S3 HEAD lacks object length") from None

    def download(self, key, target):
        return self.request("GET", key, output=target)

    def delete(self, key):
        self.request("DELETE", key)

    def list(self, prefix):
        token, seen = None, set()
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if token:
                query["continuation-token"] = token
            root = _xml(self.request("GET", query=query)[0])
            for obj in root.findall("Contents"):
                yield {"key": obj.findtext("Key"), "size": int(obj.findtext("Size") or 0)}
            if root.findtext("IsTruncated") != "true":
                return
            token = root.findtext("NextContinuationToken")
            if not token or token in seen:
                raise S3Error("S3 LIST pagination stalled")
            seen.add(token)

    def cleanup_incomplete(self, prefix, *, before, owned_uploads=()):
        """Only our named objects, old enough to exclude a concurrent live upload.
        Never on the municipal installation (an abort is a DELETE there)."""
        owned_uploads = set(owned_uploads)
        if not owned_uploads or backup_lock.locked():
            return
        query, seen = {"uploads": "", "prefix": prefix + "hr-full-"}, set()
        while True:
            root = _xml(self.request("GET", query=query)[0])
            for row in root.findall("Upload"):
                key, ident, started = row.findtext("Key"), row.findtext("UploadId"), row.findtext("Initiated")
                if (key and (key, ident) in owned_uploads and key.startswith(prefix + "hr-full-") and key.endswith(".hrb") and ident and started
                        and dt.datetime.fromisoformat(started.replace("Z", "+00:00")) < before):
                    self.request("DELETE", key, query={"uploadId": ident})
            if root.findtext("IsTruncated") != "true":
                break
            marker = (root.findtext("NextKeyMarker"), root.findtext("NextUploadIdMarker"))
            if not all(marker) or marker in seen:
                raise S3Error("S3 multipart pagination stalled")
            seen.add(marker)
            query.update({"key-marker": marker[0], "upload-id-marker": marker[1]})

    def probe(self, prefix):
        """Write, read back and remove a small test object. On the municipal
        installation the test object is kept (no delete there)."""
        self.preflight()
        name, payload = prefix + "connection-test-" + uuid.uuid4().hex, os.urandom(32)
        try:
            self.request("PUT", name, data=payload)
            raw, _ = self.request("GET", name)
            if raw != payload or self.head(name)["size"] != len(payload):
                raise S3Error("S3 probe read-back mismatch")
        finally:
            if not backup_lock.locked():
                self.delete(name)
        return True
