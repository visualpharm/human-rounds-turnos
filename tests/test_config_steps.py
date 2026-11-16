"""test_config_steps.py - pins what config.save_env_keys() does, step by step.

The writer is one public entry point that merges KEY=VALUE pairs into the
effective env file under an advisory lock, stages the result in a unique
same-directory temp and renames it into place. These tests go through that
entry point only and assert what can be observed: the return value, the bytes
and mode of the env file, the staging leftovers, the order of the side effects
and what is released on every exit. They complement the adversarial tests in
test_install_owned_packs.py (symlink swaps, lock policy), which stay where they
are.

Run:  python3 -m unittest discover -s tests -p "test_config_steps.py"
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402


def _write(path, content):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


class _SaveEnvKeysCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="human-rounds-config-steps-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "boot.env")
        # Registered before set_env: cleanups run last-in first-out, so the
        # cache is dropped after the environment is back.
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, TURNOS_ENV_FILE=self.path)
        config._reset_cache_para_tests()

    def _leftovers(self):
        return sorted(n for n in os.listdir(self.tmp)
                      if n.startswith(".env-") and n.endswith(".tmp"))

    def _trace(self):
        """Wrap the steps of the writer so the order of side effects is recorded.

        Every wrapper calls the real function; nothing is faked."""
        events = []
        real_acquire = config._acquire_write_lock
        real_read = config._read_existing_no_follow
        real_gate = config._lock_path_matches_fd
        real_mkstemp = tempfile.mkstemp
        real_fsync = os.fsync
        real_replace = os.replace

        def acquire(lockpath):
            events.append("lock")
            fd = real_acquire(lockpath)
            events.append(("locked", fd))
            return fd

        def read(path):
            events.append("read")
            return real_read(path)

        def mkstemp(*a, **kw):
            events.append(("mkstemp", kw))
            fd, tmp = real_mkstemp(*a, **kw)
            events.append(("staged", tmp))
            return fd, tmp

        def fsync(fd):
            events.append("fsync")
            return real_fsync(fd)

        def gate(lockpath, lockfd):
            events.append("gate")
            return real_gate(lockpath, lockfd)

        def replace(src, dst):
            events.append(("replace", src, dst, _read(src)))
            return real_replace(src, dst)

        patches = [
            patch.object(config, "_acquire_write_lock", side_effect=acquire),
            patch.object(config, "_read_existing_no_follow", side_effect=read),
            patch.object(config, "_lock_path_matches_fd", side_effect=gate),
            patch.object(config.tempfile, "mkstemp", side_effect=mkstemp),
            patch.object(config.os, "fsync", side_effect=fsync),
            patch.object(config.os, "replace", side_effect=replace),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return events


class TestMergeSemantics(_SaveEnvKeysCase):
    def test_existing_key_is_replaced_in_place_and_new_key_is_appended(self):
        _write(self.path, "# header\nA=1\n\nB=2\nTAIL=z\n")
        self.assertTrue(config.save_env_keys({"B": "two", "NEW": "n"}))
        self.assertEqual(_read(self.path), "# header\nA=1\n\nB=two\nTAIL=z\nNEW=n\n")

    def test_new_keys_are_appended_in_the_order_given(self):
        _write(self.path, "A=1\n")
        self.assertTrue(config.save_env_keys([("Z", "1"), ("M", "2"), ("B", "3")]))
        self.assertEqual(_read(self.path), "A=1\nZ=1\nM=2\nB=3\n")

    def test_last_pair_wins_for_a_repeated_key(self):
        self.assertTrue(config.save_env_keys([("K", "first"), ("K", "second")]))
        self.assertEqual(_read(self.path), "K=second\n")

    def test_key_line_is_normalised_when_replaced(self):
        # Indentation and the spaces around "=" are dropped on a replaced line;
        # an unrelated line keeps its bytes exactly.
        _write(self.path, "   SPACED = old value  \n  OTHER = kept  \n")
        self.assertTrue(config.save_env_keys({"SPACED": "new"}))
        self.assertEqual(_read(self.path), "SPACED=new\n  OTHER = kept  \n")

    def test_value_with_equals_sign_is_matched_by_its_first_segment(self):
        _write(self.path, "URL=a=b=c\n")
        self.assertTrue(config.save_env_keys({"URL": "x=y"}))
        self.assertEqual(_read(self.path), "URL=x=y\n")

    def test_every_duplicate_line_of_a_key_is_replaced_and_not_appended_again(self):
        _write(self.path, "K=1\nX=9\nK=2\n")
        self.assertTrue(config.save_env_keys({"K": "v"}))
        self.assertEqual(_read(self.path), "K=v\nX=9\nK=v\n")

    def test_comments_and_lines_without_equals_are_never_matched(self):
        _write(self.path, "#K=old\nK\n   # K=indented comment\n")
        self.assertTrue(config.save_env_keys({"K": "v"}))
        self.assertEqual(_read(self.path), "#K=old\nK\n   # K=indented comment\nK=v\n")

    def test_line_endings_are_normalised_to_newline(self):
        _write(self.path, "A=1\r\nB=2\r\n")
        self.assertTrue(config.save_env_keys({"C": "3"}))
        self.assertEqual(_read(self.path), "A=1\nB=2\nC=3\n")

    def test_trailing_newline_is_added_and_a_trailing_blank_line_is_kept(self):
        _write(self.path, "A=1")
        self.assertTrue(config.save_env_keys({}))
        self.assertEqual(_read(self.path), "A=1\n")
        _write(self.path, "A=1\n\n")
        self.assertTrue(config.save_env_keys({}))
        self.assertEqual(_read(self.path), "A=1\n\n")

    def test_missing_file_is_created_private_with_just_the_pairs(self):
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(config.save_env_keys({"A": "1", "B": "2"}))
        self.assertEqual(_read(self.path), "A=1\nB=2\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_no_pairs_on_a_missing_file_leaves_an_empty_file(self):
        self.assertTrue(config.save_env_keys({}))
        self.assertEqual(_read(self.path), "")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_existing_mode_is_applied_to_the_new_file(self):
        _write(self.path, "A=1\n")
        os.chmod(self.path, 0o640)
        self.assertTrue(config.save_env_keys({"A": "2"}))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        self.assertEqual(self._leftovers(), [])

    def test_lock_file_is_private_and_stays_next_to_the_env(self):
        self.assertTrue(config.save_env_keys({"A": "1"}))
        lockpath = self.path + ".lock"
        self.assertTrue(os.path.isfile(lockpath))
        self.assertEqual(stat.S_IMODE(os.stat(lockpath).st_mode), 0o600)


class TestStagingAndCommit(_SaveEnvKeysCase):
    def test_side_effects_run_in_order_and_replace_gets_the_full_body(self):
        _write(self.path, "A=1\n")
        events = self._trace()
        with patch.object(config, "_SAVE_ENV_KEYS_TEST_HOOK",
                          lambda: events.append("hook")):
            self.assertTrue(config.save_env_keys({"A": "2", "B": "3"}))
        kinds = [e if isinstance(e, str) else e[0] for e in events]
        self.assertEqual(kinds, ["hook", "lock", "gate", "locked", "read", "mkstemp",
                                 "staged", "fsync", "gate", "replace"])
        mkstemp_kwargs = events[5][1]
        self.assertEqual(mkstemp_kwargs, {"dir": self.tmp, "prefix": ".env-",
                                          "suffix": ".tmp"})
        staged = events[6][1]
        replace = events[9]
        self.assertEqual(replace[1], staged)
        self.assertEqual(replace[2], self.path)
        self.assertEqual(replace[3], "A=2\nB=3\n")  # fully written before the rename
        self.assertFalse(os.path.exists(staged))
        self.assertEqual(self._leftovers(), [])

    def test_commit_gate_is_called_with_the_lock_path_and_the_held_fd(self):
        calls = []
        real_gate = config._lock_path_matches_fd

        def gate(lockpath, lockfd):
            calls.append((lockpath, lockfd))
            return real_gate(lockpath, lockfd)

        held = []
        real_acquire = config._acquire_write_lock

        def acquire(lockpath):
            fd = real_acquire(lockpath)
            held.append(fd)
            return fd

        with patch.object(config, "_lock_path_matches_fd", side_effect=gate), \
             patch.object(config, "_acquire_write_lock", side_effect=acquire):
            self.assertTrue(config.save_env_keys({"A": "1"}))
        # Once after the flock (inside the acquire) and once at the commit gate.
        self.assertEqual(calls, [(self.path + ".lock", held[0])] * 2)

    def test_failed_commit_gate_stops_before_the_rename_and_cleans_the_staging(self):
        _write(self.path, "KEEP=1\n")
        events = self._trace()
        real_gate = config._lock_path_matches_fd
        state = {"n": 0}

        def gate(lockpath, lockfd):
            state["n"] += 1
            if state["n"] == 1:  # the acquisition check passes
                return real_gate(lockpath, lockfd)
            return False

        with patch.object(config, "_lock_path_matches_fd", side_effect=gate):
            self.assertFalse(config.save_env_keys({"A": "1"}))
        self.assertNotIn("replace", [e if isinstance(e, str) else e[0] for e in events])
        self.assertEqual(_read(self.path), "KEEP=1\n")
        self.assertEqual(self._leftovers(), [])

    def test_rename_failure_returns_false_and_cleans_the_staging(self):
        _write(self.path, "KEEP=1\n")
        with patch.object(config.os, "replace", side_effect=OSError("rename boom")):
            self.assertFalse(config.save_env_keys({"A": "1"}))
        self.assertEqual(_read(self.path), "KEEP=1\n")
        self.assertEqual(self._leftovers(), [])

    def test_chmod_failure_returns_false_and_cleans_the_staging(self):
        _write(self.path, "KEEP=1\n")
        with patch.object(config.os, "fchmod", side_effect=OSError("chmod boom")):
            self.assertFalse(config.save_env_keys({"A": "1"}))
        self.assertEqual(_read(self.path), "KEEP=1\n")
        self.assertEqual(self._leftovers(), [])

    def test_write_value_error_returns_false_and_cleans_the_staging(self):
        _write(self.path, "KEEP=1\n")
        with patch.object(config.os, "fsync", side_effect=ValueError("closed file")):
            self.assertFalse(config.save_env_keys({"A": "1"}))
        self.assertEqual(_read(self.path), "KEEP=1\n")
        self.assertEqual(self._leftovers(), [])

    def test_fdopen_value_error_closes_the_staging_fd_and_returns_false(self):
        _write(self.path, "KEEP=1\n")
        staged = {}
        real_mkstemp = tempfile.mkstemp
        real_fdopen = os.fdopen

        def mkstemp(*a, **kw):
            fd, tmp = real_mkstemp(*a, **kw)
            staged["fd"], staged["tmp"] = fd, tmp
            return fd, tmp

        def fdopen(fd, *args, **kwargs):
            if args and args[0] == "w":
                raise ValueError("bad mode")
            return real_fdopen(fd, *args, **kwargs)

        with patch.object(config.tempfile, "mkstemp", side_effect=mkstemp), \
             patch.object(config.os, "fdopen", side_effect=fdopen):
            self.assertFalse(config.save_env_keys({"A": "1"}))
        with self.assertRaises(OSError):
            os.fstat(staged["fd"])
        self.assertFalse(os.path.exists(staged["tmp"]))
        self.assertEqual(_read(self.path), "KEEP=1\n")

    def test_failing_staging_cleanup_does_not_escape(self):
        _write(self.path, "KEEP=1\n")
        with patch.object(config.os, "replace", side_effect=OSError("rename boom")), \
             patch.object(config.os, "unlink", side_effect=OSError("unlink boom")):
            self.assertFalse(config.save_env_keys({"A": "1"}))
        self.assertEqual(_read(self.path), "KEEP=1\n")
        for name in self._leftovers():  # the unlink was refused, so the temp is still here
            os.remove(os.path.join(self.tmp, name))


class TestReadFailures(_SaveEnvKeysCase):
    def test_undecodable_existing_bytes_fail_closed_and_stay_untouched(self):
        raw = b"A=1\n\xff\xfe=broken\n"
        with open(self.path, "wb") as f:
            f.write(raw)
        events = self._trace()
        self.assertFalse(config.save_env_keys({"A": "2"}))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), raw)
        self.assertNotIn("mkstemp", [e if isinstance(e, str) else e[0] for e in events])
        self.assertEqual(self._leftovers(), [])

    def test_env_path_that_is_a_directory_is_refused(self):
        os.mkdir(self.path)
        self.assertFalse(config.save_env_keys({"A": "1"}))
        self.assertTrue(os.path.isdir(self.path))
        self.assertEqual(self._leftovers(), [])


class TestHook(_SaveEnvKeysCase):
    def test_hook_runs_once_per_call_before_the_lock_even_when_the_lock_is_refused(self):
        calls = []
        os.mkdir(self.path + ".lock")  # a lock path the writer cannot open for flock
        with patch.object(config, "_SAVE_ENV_KEYS_TEST_HOOK", lambda: calls.append("h")):
            self.assertFalse(config.save_env_keys({"A": "1"}))
        self.assertEqual(calls, ["h"])
        self.assertFalse(os.path.exists(self.path))

    def test_hook_failure_never_stops_a_real_write(self):
        def boom():
            raise RuntimeError("hook boom")

        with patch.object(config, "_SAVE_ENV_KEYS_TEST_HOOK", boom):
            self.assertTrue(config.save_env_keys({"A": "1"}))
        self.assertEqual(_read(self.path), "A=1\n")


class TestLockRelease(_SaveEnvKeysCase):
    def _watch_lock(self):
        """Record the lock fd and every flock call; return (fds, flocks)."""
        fds, flocks = [], []
        real_acquire = config._acquire_write_lock
        real_flock = config._fcntl.flock

        def acquire(lockpath):
            fd = real_acquire(lockpath)
            fds.append(fd)
            return fd

        def flock(fd, op):
            flocks.append((fd, op))
            return real_flock(fd, op)

        for p in (patch.object(config, "_acquire_write_lock", side_effect=acquire),
                  patch.object(config._fcntl, "flock", side_effect=flock)):
            p.start()
            self.addCleanup(p.stop)
        return fds, flocks

    def _assert_released(self, fds, flocks):
        self.assertEqual(len(fds), 1)
        self.assertEqual(flocks[-1], (fds[0], config._fcntl.LOCK_UN))
        with self.assertRaises(OSError):
            os.fstat(fds[0])  # the descriptor is closed

    def test_lock_is_released_after_a_successful_write(self):
        fds, flocks = self._watch_lock()
        self.assertTrue(config.save_env_keys({"A": "1"}))
        self._assert_released(fds, flocks)

    def test_lock_is_released_after_an_unreadable_env(self):
        os.mkdir(self.path)
        fds, flocks = self._watch_lock()
        self.assertFalse(config.save_env_keys({"A": "1"}))
        self._assert_released(fds, flocks)

    def test_lock_is_released_after_a_staging_failure(self):
        fds, flocks = self._watch_lock()
        with patch.object(config.tempfile, "mkstemp", side_effect=OSError("full")):
            self.assertFalse(config.save_env_keys({"A": "1"}))
        self._assert_released(fds, flocks)

    def test_lock_is_released_after_a_rename_failure(self):
        fds, flocks = self._watch_lock()
        with patch.object(config.os, "replace", side_effect=OSError("boom")):
            self.assertFalse(config.save_env_keys({"A": "1"}))
        self._assert_released(fds, flocks)

    def test_a_failing_unlock_is_swallowed_and_the_fd_is_still_closed(self):
        fds, flocks = [], []
        real_acquire = config._acquire_write_lock
        real_flock = config._fcntl.flock

        def acquire(lockpath):
            fd = real_acquire(lockpath)
            fds.append(fd)
            return fd

        def flock(fd, op):
            flocks.append(op)
            if op == config._fcntl.LOCK_UN:
                raise OSError("unlock boom")
            return real_flock(fd, op)

        with patch.object(config, "_acquire_write_lock", side_effect=acquire), \
             patch.object(config._fcntl, "flock", side_effect=flock):
            self.assertTrue(config.save_env_keys({"A": "1"}))
        self.assertEqual(flocks[-1], config._fcntl.LOCK_UN)
        with self.assertRaises(OSError):
            os.fstat(fds[0])
        self.assertEqual(_read(self.path), "A=1\n")


class TestDotenvCache(_SaveEnvKeysCase):
    def _prime(self):
        config._dotenv_cache["vals"] = {"STALE": "1"}
        config._dotenv_cache["mtime"] = 123.0

    def test_success_resets_the_dotenv_cache(self):
        self._prime()
        self.assertTrue(config.save_env_keys({"A": "1"}))
        self.assertIsNone(config._dotenv_cache["vals"])
        self.assertIsNone(config._dotenv_cache["mtime"])

    def test_failure_leaves_the_dotenv_cache_alone(self):
        os.mkdir(self.path)
        self._prime()
        self.assertFalse(config.save_env_keys({"A": "1"}))
        self.assertEqual(config._dotenv_cache["vals"], {"STALE": "1"})
        self.assertEqual(config._dotenv_cache["mtime"], 123.0)

    def test_next_read_sees_the_value_just_written(self):
        _write(self.path, "A=old\n")
        self.assertEqual(config._dotenv().get("A"), "old")  # fills the cache
        self.assertTrue(config.save_env_keys({"A": "new"}))
        self.assertEqual(config._dotenv().get("A"), "new")


if __name__ == "__main__":
    unittest.main()
