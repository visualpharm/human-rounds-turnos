#!/usr/bin/env python3
"""Run unittest discovery and write a machine-readable TestResult summary."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest


SCHEMA_VERSION = 1


def _report(result):
    """Keep only stable TestResult counters and skip identities/reasons."""
    return {
        "schema_version": SCHEMA_VERSION,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "expected_failures": len(result.expectedFailures),
        "skipped": [
            {"test_id": test.id(), "reason": str(reason)}
            for test, reason in result.skipped
        ],
    }


class _LeftThreads(unittest.TestCase):
    """Stands in for the module that left app background threads behind."""

    def __init__(self, module):
        super().__init__("runTest")
        self._module = module

    def runTest(self):  # never run: only reported through addError
        pass

    def id(self):
        return "%s (daemon threads left running)" % self._module

    def shortDescription(self):
        return self.id()


class _LeakCheckResult(unittest.TextTestResult):
    """Fails the run, naming the module, when a test module leaves the app's
    lifespan daemons (tests/_lifespan.py) running: in the one-process run they
    reach every later module, in a shard they outlive nothing but still show
    which file started them. `_lifespan.no_background_services()` is the fix."""

    leaked_threads = staticmethod(lambda: [])  # set by main() once tests/ is importable

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._module = None
        self._reported = set()

    def startTest(self, test):
        module = type(test).__module__
        if self._module is not None and module != self._module:
            self._check(self._module)
        self._module = module
        super().startTest(test)

    def stopTestRun(self):
        if self._module is not None:
            self._check(self._module)
            self._module = None
        super().stopTestRun()

    def _check(self, module):
        left = [name for name in self.leaked_threads() if name not in self._reported]
        if not left:
            return
        self._reported.update(left)
        error = RuntimeError("module %s left app background threads running: %s"
                             % (module, ", ".join(left)))
        self.addError(_LeftThreads(module), (RuntimeError, error, None))


def _write_report(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp",
                                     dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--start-directory", default="tests")
    parser.add_argument("--pattern", default="test_*.py")
    args = parser.parse_args(argv)

    # Match `python -m unittest` import context; direct scripts otherwise put
    # scripts/ rather than the invocation directory at the front of sys.path.
    sys.path.insert(0, os.getcwd())
    suite = unittest.defaultTestLoader.discover(
        start_dir=args.start_directory, pattern=args.pattern)
    try:  # discover() has put the start directory on sys.path
        import _lifespan
        _LeakCheckResult.leaked_threads = staticmethod(_lifespan.leaked_threads)
    except ImportError:
        pass
    result = unittest.TextTestRunner(verbosity=2, resultclass=_LeakCheckResult).run(suite)
    _write_report(args.report, _report(result))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
