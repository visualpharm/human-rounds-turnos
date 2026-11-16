"""HRBACK01 streaming format. See docs/operations/backup-format.md.

Only the content header is immutable. The authenticated key envelope is
replaceable so rotation never requires rewriting ciphertext or reusing nonces.
No function logs key material; callers publish plaintext only after validation.
"""
import base64
import hashlib
import hmac
import json
import os
import shutil
import struct
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"HRBACK01"
MAX_HEADER = 65536
DEFAULT_CHUNK = 1024 * 1024


class FormatError(ValueError):
    """Invalid or unauthenticated recovery material (value-free message)."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def key_id(key):
    if not isinstance(key, bytes) or len(key) != 32:
        raise FormatError("Recovery key must contain 32 bytes")
    return hashlib.sha256(key).hexdigest()[:8]


def encode_key(key):
    key_id(key)
    raw = base64.b32encode(key + hashlib.sha256(b"HRBACK-key-v1" + key).digest()[:4]).decode().rstrip("=")
    return "-".join(raw[i:i+5] for i in range(0, len(raw), 5))


def decode_key(text):
    try:
        raw = "".join(str(text).upper().split()).replace("-", "")
        decoded = base64.b32decode(raw + "=" * (-len(raw) % 8))
    except (ValueError, TypeError):
        raise FormatError("Invalid recovery key encoding") from None
    if len(decoded) != 36 or not hmac.compare_digest(
            decoded[32:], hashlib.sha256(b"HRBACK-key-v1" + decoded[:32]).digest()[:4]):
        raise FormatError("Recovery key checksum does not match")
    return decoded[:32]


def chunk_nonce(prefix, counter):
    if len(prefix) != 4 or not 0 <= counter < 2**64:
        raise FormatError("Chunk counter outside format bound")
    return prefix + struct.pack(">Q", counter)


def _exact(src, length):
    data = src.read(length)
    if len(data) != length:
        raise FormatError("Truncated backup")
    return data


def _field(src):
    length = struct.unpack(">I", _exact(src, 4))[0]
    if not 1 <= length <= MAX_HEADER:
        raise FormatError("Invalid header length")
    return _exact(src, length)


def _prefix(header, envelopes):
    a, b = canonical(header), canonical(envelopes)
    if max(len(a), len(b)) > MAX_HEADER:
        raise FormatError("Header exceeds format bound")
    return MAGIC + struct.pack(">I", len(a)) + a + struct.pack(">I", len(b)) + b


def _wrap(data_key, header, keys):
    copies = []
    for ident, key in sorted(keys.items()):
        if ident != key_id(key):
            raise FormatError("Recovery key id does not match")
        nonce = os.urandom(12)
        wrapped = AESGCM(key).encrypt(nonce, data_key, b"HRBACK-wrap-v1:" + header["archive_id"].encode())
        copies.append({"key_id": ident, "nonce": nonce.hex(), "wrapped": wrapped.hex()})
    if not copies:
        raise FormatError("No recovery keys supplied")
    return copies


def read_header(src, keys):
    try:
        if _exact(src, 8) != MAGIC:
            raise FormatError("Unknown backup format")
        raw_header, raw_copies = _field(src), _field(src)
        header, copies = json.loads(raw_header), json.loads(raw_copies)
        if (canonical(header) != raw_header or canonical(copies) != raw_copies
                or header["version"] != 1 or header["cipher"] != "AES-256-GCM"
                or header["kdf"] != "none-random-256" or header["wrap"] != "AES-256-GCM"
                or not 64 <= header["chunk_size"] <= 16 * 1024 * 1024
                or len(bytes.fromhex(header["nonce_prefix"])) != 4
                or str(uuid.UUID(header["archive_id"])) != header["archive_id"]
                or not isinstance(copies, list) or not 1 <= len(copies) <= 64):
            raise FormatError("Unsupported backup header")
        data_key = None
        for copy in copies:
            ident = copy["key_id"]
            if ident not in keys:
                continue
            key = keys[ident]
            if ident != key_id(key):
                raise FormatError("Recovery key id does not match")
            try:
                data_key = AESGCM(key).decrypt(bytes.fromhex(copy["nonce"]), bytes.fromhex(copy["wrapped"]),
                                               b"HRBACK-wrap-v1:" + header["archive_id"].encode())
                break
            except InvalidTag:
                continue
        if data_key is None or len(data_key) != 32:
            raise FormatError("No matching recovery key")
        expected = hmac.digest(data_key, _prefix(header, copies), "sha256")
        if not hmac.compare_digest(_exact(src, 32), expected):
            raise FormatError("Backup header authentication failed")
        return header, copies, data_key
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, FormatError):
            raise
        raise FormatError("Invalid backup header") from None


def _write_header(dst, header, copies, data_key):
    prefix = _prefix(header, copies)
    dst.write(prefix)
    dst.write(hmac.digest(data_key, prefix, "sha256"))


def encrypt(src, dst, keys, *, chunk_size=DEFAULT_CHUNK, archive_id=None):
    if not 64 <= chunk_size <= 16 * 1024 * 1024:
        raise FormatError("Invalid chunk size")
    header = {"version": 1, "archive_id": archive_id or str(uuid.uuid4()),
              "cipher": "AES-256-GCM", "kdf": "none-random-256", "wrap": "AES-256-GCM",
              "chunk_size": chunk_size, "nonce_prefix": os.urandom(4).hex()}
    data_key = os.urandom(32)
    copies = _wrap(data_key, header, keys)
    _write_header(dst, header, copies, data_key)
    digest = hashlib.sha256(canonical(header)).digest()
    aes = AESGCM(data_key)
    prefix = bytes.fromhex(header["nonce_prefix"])
    current = src.read(chunk_size)
    counter = 0
    while True:
        following = src.read(chunk_size)
        final = 0 if following else 1
        ciphertext = aes.encrypt(chunk_nonce(prefix, counter), current,
                                 digest + struct.pack(">Q", counter) + bytes([final]))
        dst.write(struct.pack(">IB", len(ciphertext), final))
        dst.write(ciphertext)
        if final:
            break
        current = following
        counter += 1
    return header


def decrypt(src, dst, keys):
    header, _copies, data_key = read_header(src, keys)
    digest = hashlib.sha256(canonical(header)).digest()
    prefix = bytes.fromhex(header["nonce_prefix"])
    aes = AESGCM(data_key)
    counter = 0
    while True:
        length, final = struct.unpack(">IB", _exact(src, 5))
        if (final not in (0, 1) or not 16 <= length <= header["chunk_size"] + 16
                or (not final and length != header["chunk_size"] + 16)):
            raise FormatError("Invalid backup chunk")
        try:
            plaintext = aes.decrypt(chunk_nonce(prefix, counter), _exact(src, length),
                                    digest + struct.pack(">Q", counter) + bytes([final]))
        except InvalidTag:
            raise FormatError("Backup chunk authentication failed") from None
        dst.write(plaintext)
        if final:
            if src.read(1):
                raise FormatError("Trailing bytes after final backup chunk")
            return header
        counter += 1


def rewrap(src, dst, keys, new_keys, *, retain=True):
    """Seekable input; validate every old chunk before changing its envelope."""
    start = src.tell()
    with open(os.devnull, "wb") as sink:
        decrypt(src, sink, keys)
    src.seek(start)
    header, copies, data_key = read_header(src, keys)
    new = _wrap(data_key, header, new_keys)
    if retain:
        new += [c for c in copies if c["key_id"] not in new_keys]
    _write_header(dst, header, new, data_key)
    shutil.copyfileobj(src, dst, 1024 * 1024)
