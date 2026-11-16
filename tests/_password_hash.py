"""_password_hash.py - hash a given password once per module run, not once per account.

`auth.password_hashing.hash_password` is Argon2id (~25 ms on a quiet machine, several times that on a
shared CI runner). The demo seeder gives ~95 accounts the same public login, and a test that runs
the seeder ten times pays for ~950 hashes - ~90 % of such a file's time - to check which rows get
written, not the KDF. `reuse_hashes()` makes a repeated password return the hash the real function
produced the first time, so every stored value is still a real, verifiable Argon2 hash.

Not named test_*.py on purpose (discovery skips it). Do not use it in a test that is about hashing
itself (`test_db_auth_store`, `test_password_hashing`): there each call must really hash.

Usage:

    import _password_hash

    def setUpModule():
        _password_hash.reuse_hashes()           # undone after the module's tests
"""
import unittest
from unittest import mock

from app.auth import password_hashing


def reuse_hashes():
    real = password_hashing.hash_password
    seen = {}

    def hash_once(password):
        if password not in seen:
            seen[password] = real(password)
        return seen[password]

    patcher = mock.patch.object(password_hashing, "hash_password", hash_once)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
