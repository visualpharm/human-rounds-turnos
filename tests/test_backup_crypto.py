"""The on-disk recovery contract, using generated non-secret fixture bytes."""
import io
import json
import struct
import unittest

from app.core import backup_crypto as c


class CryptoTest(unittest.TestCase):
    def setUp(self):
        self.key = bytes(range(32))

    def encrypted(self, data=b"abcdefgh" * 100, **kw):
        out = io.BytesIO()
        c.encrypt(io.BytesIO(data), out, {c.key_id(self.key): self.key}, chunk_size=64, **kw)
        return out.getvalue()

    def decrypt(self, data, keys=None):
        out = io.BytesIO()
        c.decrypt(io.BytesIO(data), out, keys or {c.key_id(self.key): self.key})
        return out.getvalue()

    def test_roundtrip_empty_and_cross_chunk_large_file(self):
        for data in (b"", b"x" * 64, bytes(range(256)) * 10000):
            self.assertEqual(self.decrypt(self.encrypted(data)), data)

    def test_wrong_key_and_wrong_key_id(self):
        for keys in ({c.key_id(self.key): b"z" * 32}, {"unknown": self.key}):
            with self.assertRaises(c.FormatError):
                self.decrypt(self.encrypted(), keys)

    def test_truncation_missing_terminal_and_trailing_bytes(self):
        blob = self.encrypted()
        for bad in (blob[:-1], blob[:-30], blob + b"extra"):
            with self.assertRaises(c.FormatError):
                self.decrypt(bad)

    def test_header_tampering_reordering_duplication_and_substitution(self):
        blob = self.encrypted()
        stream = io.BytesIO(blob)
        c.read_header(stream, {c.key_id(self.key): self.key})
        start = stream.tell()
        frame = 4 + 1 + 64 + 16
        for bad in (blob[:start] + blob[start+frame:start+2*frame] + blob[start:start+frame] + blob[start+2*frame:],
                    blob[:start+frame] + blob[start:start+frame] + blob[start+frame:],
                    blob[:start] + self.encrypted()[start:start+frame] + blob[start+frame:],
                    blob.replace(b'"chunk_size":64', b'"chunk_size":65', 1)):
            with self.assertRaises(c.FormatError):
                self.decrypt(bad)

    def test_nonce_counter_is_unique_and_archive_prefix_is_random(self):
        nonces = {c.chunk_nonce(b"abcd", i) for i in range(10000)}
        self.assertEqual(len(nonces), 10000)
        self.assertNotEqual(self.encrypted(), self.encrypted())

    def test_operator_encoding_checksum_and_fingerprint(self):
        text = c.encode_key(self.key)
        self.assertEqual(c.decode_key(text), self.key)
        with self.assertRaises(c.FormatError):
            c.decode_key(text[:-1] + ("A" if text[-1] != "A" else "B"))
        self.assertEqual(len(c.key_id(self.key)), 8)

    def test_rewrap_does_not_reencrypt_chunks_and_retains_old_key(self):
        blob = self.encrypted()
        new = b"n" * 32
        src, dst = io.BytesIO(blob), io.BytesIO()
        c.rewrap(src, dst, {c.key_id(self.key): self.key}, {c.key_id(new): new}, retain=True)
        revised = dst.getvalue()
        self.assertEqual(self.decrypt(revised), self.decrypt(blob))
        self.assertEqual(self.decrypt(revised, {c.key_id(new): new}), self.decrypt(blob))
        a, b = io.BytesIO(blob), io.BytesIO(revised)
        c.read_header(a, {c.key_id(self.key): self.key})
        c.read_header(b, {c.key_id(new): new})
        self.assertEqual(a.read(), b.read())
