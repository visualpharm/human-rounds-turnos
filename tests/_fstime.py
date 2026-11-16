"""Restore a rewritten file's mtime only once its change time has moved.

Tests that rewrite a file in place with the same size and then put back the
old mtime model a mutation that only ``st_ctime_ns`` can reveal. On
filesystems with coarse timestamps (the GitLab runners' overlay among them)
the rewrite can land in the same clock tick as the original write and keep
the very same ctime: the mutated file is then indistinguishable from the
untouched one by ``stat``, so the test fails for a reason no guard could
detect. Re-applying ``utime`` refreshes ctime to "now"; repeating it until
ctime differs makes the test exercise the changed-ctime case it names.
"""

import os
import time


def restore_mtime_after_ctime_moves(path, before, budget=5.0):
    """``os.utime(path, ns=(before.atime, before.mtime))``, repeated until
    ``os.stat(path).st_ctime_ns`` differs from ``before.st_ctime_ns``."""
    deadline = time.monotonic() + budget
    while True:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        if os.stat(path).st_ctime_ns != before.st_ctime_ns:
            return
        if time.monotonic() > deadline:
            raise AssertionError("ctime of %s never moved past %d" % (path, before.st_ctime_ns))
