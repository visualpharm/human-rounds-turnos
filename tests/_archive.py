"""An archived copy of the repository (`git archive`, the public cut) has none of the
files `.gitattributes` marks export-ignore: the agents' hooks and settings, CLAUDE.md,
installation-only scripts and data. A test that needs one of them skips there with ONE
exact reason, which `scripts/check_unittest_coverage.py` accepts as an honest skip; in
the working repository nothing skips, so a lost file stays red.

The copy is recognised by one export-ignored file nobody adds to a copy of their own (a
user's own CLAUDE.md or `.claude/` folder does not turn the tests back on).
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Keep in step with ARCHIVE_COPY_SKIP in scripts/check_unittest_coverage.py
# (tests/test_unittest_coverage_guard.py pins the two are equal).
REASON = "archived copy: export-ignore leaves out the private file this test needs"

# Export-ignored (under `.claude/`) and always tracked in the working repository;
# tests/test_unittest_coverage_guard.py pins both.
MARKER = ".claude/hooks/managed-credential-boundary.py"


def is_archive_copy():
    """True in a `git archive` copy of the repository."""
    return not os.path.isfile(os.path.join(ROOT, MARKER))


def skip_module_in_archive_copy():
    """Call at the top of a test module that loads an export-ignored file on import
    (it guards a copy archived with an older `.gitattributes` that still carried it)."""
    if is_archive_copy():
        raise unittest.SkipTest(REASON)


def skip_in_archive_copy(case):
    """Call inside a test method (or setUp) that needs an export-ignored file."""
    if is_archive_copy():
        case.skipTest(REASON)
