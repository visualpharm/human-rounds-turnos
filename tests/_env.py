"""_env.py - set the process environment for a test, and put it back.

`os.environ` is one dict for the whole interpreter. A test that writes it and does not undo the write
hands its value to the next test of the process (and to the threads a previous module left running).
Every write below is paired with its undo in the same call, so the test body has nothing to forget.

Not named test_*.py on purpose: unittest's discovery (`-p "test_*.py"`) must not collect it. The
ratchet in `scripts/hygiene_ratchet.py` does not count this file: it is where the writes belong.

Usage:

    import _env

    class TestX(unittest.TestCase):
        def setUp(self):
            _env.set_env(self, TURNOS_CONFIG_LOCK="1", TURNOS_ASGI=None)    # None = not set
            ...                                                              # undone after each test

        def test_y(self):
            with _env.patched_env(LANGUAGES="es,en"):                        # undone at the end of the block
                ...

    def setUpModule():
        _env.set_env_for_module(TURNOS_FAKE_AI="1")                         # undone after the module's tests

    _env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")          # at the top, before importing core
    _env.process_set(TURNOS_TURNSTILE_SECRET="", TURNOS_TURNSTILE_SITEKEY=None)   # same, but overwrites

`set_env(case, ...)` takes a TestCase instance (undone after that test) or a TestCase class (undone
after the class, for setUpClass). `clear=True` empties the environment first (only the given keys are
left), which is what a test wants when it must not see the developer's own `TURNOS_*` settings.
A value is a string, or None to remove the key; anything else is refused, because `os.environ`
itself would refuse it later and far from the cause.
"""
import contextlib
import os
import unittest

_MISSING = object()


def _checked(values):
    for key, value in values.items():
        if value is not None and not isinstance(value, str):
            raise TypeError("environment value for %r must be a str or None, got %r" % (key, value))
    return values


def _apply(values, clear, whole=False):
    """Write `values` (None removes), return what puts the environment back exactly as it was.

    `whole` puts back the entire environment (every key, as `mock.patch.dict` does) instead of only the
    given keys: what the code under test wrote to other keys in the meantime is undone as well.
    """
    whole = whole or clear
    saved = dict(os.environ) if whole else {k: os.environ.get(k, _MISSING) for k in values}
    if clear:
        os.environ.clear()
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def restore():
        if whole:
            os.environ.clear()
            os.environ.update(saved)
            return
        for key, old in saved.items():
            if old is _MISSING:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
    return restore


def process_defaults(mapping=None, **values):
    """Import-time defaults for the whole interpreter, set once and never undone.

    For the switches a module needs BEFORE it imports the application (`TURNOS_FAKE_AI`,
    `TURNOS_SKIP_HSI`): a key that is already set wins, like `os.environ.setdefault`. Not for a value
    a single test needs - that is `set_env`.
    """
    for key, value in _merge(mapping, values).items():
        if value is None:
            raise TypeError("process_defaults(%s=None): a default needs a value" % key)
        os.environ.setdefault(key, value)


def process_set(mapping=None, **values):
    """Import-time settings for the whole interpreter, set once and never undone (None removes a key).

    The sibling of `process_defaults` for the module that must NOT inherit the developer's value: it
    overwrites. Only for what the module needs before it imports the application, which reads the
    environment once; everything a test body needs is `set_env` / `patched_env`.
    """
    for key, value in _merge(mapping, values).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _merge(mapping, kwargs):
    values = dict(mapping or {})
    values.update(kwargs)
    return _checked(values)


def set_env(case, mapping=None, *, clear=False, **values):
    """Set variables for the running test (a TestCase instance) or class (a TestCase class)."""
    restore = _apply(_merge(mapping, values), clear)
    if isinstance(case, type):
        case.addClassCleanup(restore)
    else:
        case.addCleanup(restore)
    return restore


def set_env_for_module(mapping=None, *, clear=False, **values):
    """Set variables from `setUpModule`; they are put back when the module's tests are done."""
    restore = _apply(_merge(mapping, values), clear)
    unittest.addModuleCleanup(restore)
    return restore


@contextlib.contextmanager
def patched_env(mapping=None, *, clear=False, **values):
    """Set variables inside a `with` block and put the WHOLE environment back when it ends, however it ends.

    Like `mock.patch.dict(os.environ, ...)`: a key the code under the block wrote is undone too.
    """
    restore = _apply(_merge(mapping, values), clear, whole=True)
    try:
        yield
    finally:
        restore()
