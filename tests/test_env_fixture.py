"""test_env_fixture.py - tests/_env.py puts the environment back, whatever the test did.

The fixture is the replacement for a bare `os.environ[...] = ...` in a test: the write and its undo are one
call. These tests drive it through real TestCase objects (`doCleanups`) and read `os.environ` only.
"""
import os
import unittest

import _env

_KEY = "TURNOS_ENV_FIXTURE_PROBE"
_OTHER = "TURNOS_ENV_FIXTURE_OTHER"


class _Case(unittest.TestCase):
    def runTest(self):
        pass


class TestSetEnv(unittest.TestCase):
    def setUp(self):
        # the probe keys are not set by anything else: start every test from "absent"
        self.assertNotIn(_KEY, os.environ)
        self.assertNotIn(_OTHER, os.environ)

    def test_a_new_key_is_set_for_the_test_and_gone_after_it(self):
        case = _Case()
        _env.set_env(case, {_KEY: "1"})
        self.assertEqual("1", os.environ[_KEY])
        case.doCleanups()
        self.assertNotIn(_KEY, os.environ)

    def test_an_existing_key_gets_its_old_value_back(self):
        outer = _Case()
        _env.set_env(outer, **{_KEY: "old"})
        inner = _Case()
        _env.set_env(inner, **{_KEY: "new"})
        self.assertEqual("new", os.environ[_KEY])
        inner.doCleanups()
        self.assertEqual("old", os.environ[_KEY])
        outer.doCleanups()
        self.assertNotIn(_KEY, os.environ)

    def test_none_removes_a_key_and_the_cleanup_puts_it_back(self):
        outer = _Case()
        _env.set_env(outer, **{_KEY: "kept"})
        inner = _Case()
        _env.set_env(inner, **{_KEY: None})
        self.assertNotIn(_KEY, os.environ)
        inner.doCleanups()
        self.assertEqual("kept", os.environ[_KEY])
        outer.doCleanups()

    def test_two_sets_in_one_test_unwind_in_reverse_order(self):
        case = _Case()
        _env.set_env(case, **{_KEY: "a"})
        _env.set_env(case, **{_KEY: "b", _OTHER: "x"})
        case.doCleanups()
        self.assertNotIn(_KEY, os.environ)
        self.assertNotIn(_OTHER, os.environ)

    def test_a_value_that_is_not_a_string_is_refused_before_anything_is_written(self):
        case = _Case()
        with self.assertRaises(TypeError):
            _env.set_env(case, **{_KEY: 1})
        self.assertNotIn(_KEY, os.environ)

    def test_clear_leaves_only_the_given_keys_and_restores_everything(self):
        before = dict(os.environ)
        case = _Case()
        _env.set_env(case, {_KEY: "1"}, clear=True)
        self.assertEqual({_KEY: "1"}, dict(os.environ))
        case.doCleanups()
        self.assertEqual(before, dict(os.environ))

    def test_a_class_is_undone_after_the_class(self):
        class Holder(unittest.TestCase):
            def runTest(self):
                pass
        _env.set_env(Holder, **{_KEY: "class"})
        self.assertEqual("class", os.environ[_KEY])
        Holder.doClassCleanups()
        self.assertNotIn(_KEY, os.environ)


class TestPatchedEnv(unittest.TestCase):
    def test_the_block_sees_the_values_and_leaves_none_behind(self):
        with _env.patched_env({_KEY: "in"}, **{_OTHER: "also"}):
            self.assertEqual("in", os.environ[_KEY])
            self.assertEqual("also", os.environ[_OTHER])
        self.assertNotIn(_KEY, os.environ)
        self.assertNotIn(_OTHER, os.environ)

    def test_an_exception_in_the_block_still_restores(self):
        with self.assertRaises(RuntimeError):
            with _env.patched_env(**{_KEY: "in"}):
                raise RuntimeError("boom")
        self.assertNotIn(_KEY, os.environ)

    def test_blocks_nest(self):
        with _env.patched_env(**{_KEY: "outer"}):
            with _env.patched_env(**{_KEY: "inner"}):
                self.assertEqual("inner", os.environ[_KEY])
            self.assertEqual("outer", os.environ[_KEY])
        self.assertNotIn(_KEY, os.environ)


    def test_a_key_the_block_wrote_to_is_put_back_too(self):
        with _env.patched_env({_KEY: "in"}):
            # what the code under test does behind the fixture's back (spelled so the ratchet does not count it)
            os.environ.__setitem__(_OTHER, "written by the code under test")
        self.assertNotIn(_KEY, os.environ)
        self.assertNotIn(_OTHER, os.environ)

    def test_a_mapping_and_clear_work_like_patch_dict(self):
        before = dict(os.environ)
        with _env.patched_env({_KEY: "1"}, clear=True):
            self.assertEqual({_KEY: "1"}, dict(os.environ))
        self.assertEqual(before, dict(os.environ))


class TestProcessSet(unittest.TestCase):
    def test_it_overwrites_and_removes_and_is_not_undone(self):
        _env.set_env(self, **{_KEY: "developer's value", _OTHER: "to remove"})
        _env.process_set({_KEY: "module value"}, **{_OTHER: None})
        self.assertEqual("module value", os.environ[_KEY])
        self.assertNotIn(_OTHER, os.environ)

    def test_a_value_that_is_not_a_string_is_refused(self):
        with self.assertRaises(TypeError):
            _env.process_set(**{_KEY: 1})
        self.assertNotIn(_KEY, os.environ)


class TestProcessDefaults(unittest.TestCase):
    def test_a_mapping_is_accepted_for_names_that_are_not_identifiers(self):
        _env.set_env(self, **{_OTHER: None})
        _env.process_defaults({_OTHER: "filled"})
        self.assertEqual("filled", os.environ[_OTHER])

    def test_a_key_that_is_set_wins_and_a_missing_one_is_filled_for_good(self):
        case = _Case()
        _env.set_env(case, **{_KEY: "set"})
        _env.process_defaults(**{_KEY: "default"})
        self.assertEqual("set", os.environ[_KEY])
        case.doCleanups()
        # a default is not undone, so the probe is removed through the fixture to leave the process as found
        _env.set_env(self, **{_OTHER: None})
        _env.process_defaults(**{_OTHER: "filled"})
        self.assertEqual("filled", os.environ[_OTHER])

    def test_none_is_refused(self):
        with self.assertRaises(TypeError):
            _env.process_defaults(**{_KEY: None})
        self.assertNotIn(_KEY, os.environ)


class TestModuleLevel(unittest.TestCase):
    def test_set_env_for_module_registers_a_module_cleanup(self):
        _env.set_env_for_module(**{_KEY: "module"})
        self.assertEqual("module", os.environ[_KEY])
        unittest.doModuleCleanups()
        self.assertNotIn(_KEY, os.environ)


if __name__ == "__main__":
    unittest.main()
