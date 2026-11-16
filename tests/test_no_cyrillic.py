"""test_no_cyrillic.py — no Cyrillic text anywhere in the repository except the
Russian and Ukrainian locale catalogs (owner, 2026-10-28: "there must be no
Russian in the repository at all").

Comments, docstrings, docs, test names and quotes are English (or Spanish where
the file is Spanish). A language that must be matched or spelled in Cyrillic
(the chat's language detection, the language names in the switcher, search
tests on Cyrillic names, prompt examples) writes it as `\\uXXXX` escapes: the
runtime string is identical and the source carries no Cyrillic literal.

The generated catalogs in `locales/ru/` and `locales/uk/` are the only allowed
place (`scripts/translate_locales.py` writes them).
"""
import os
import re
import subprocess
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOWED_PREFIXES = ("locales/ru/", "locales/uk/")

# Every Cyrillic block: basic + supplement, Ext-C, phonetic letters, Ext-A, Ext-B,
# combining half marks, Ext-D. Built from code points so this file stays free of
# Cyrillic; test_the_detector_covers_every_cyrillic_character_of_this_unicode pins it
# against the Unicode names.
_RANGES = ((0x0400, 0x052F), (0x1C80, 0x1C8F), (0x1D2B, 0x1D2B), (0x1D78, 0x1D78),
           (0x2DE0, 0x2DFF), (0xA640, 0xA69F), (0xFE2E, 0xFE2F), (0x1E030, 0x1E08F))
CYRILLIC = re.compile("[" + "".join("%s-%s" % (chr(a), chr(b)) for a, b in _RANGES) + "]")

_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "ci-artifacts"}


def _tracked_files():
    """The repository's files (git ls-files; a plain walk where there is no git)."""
    try:
        listed = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], capture_output=True)
    except OSError:  # no git executable
        listed = None
    if listed is not None and listed.returncode == 0 and listed.stdout:
        return [p.decode("utf-8", "surrogateescape") for p in listed.stdout.split(b"\0") if p]
    out = []
    for d, dirs, files in os.walk(ROOT):
        rel_dir = os.path.relpath(d, ROOT).replace(os.sep, "/")
        dirs[:] = [x for x in dirs
                   if x not in _SKIP_DIRS and not (rel_dir == ".claude" and x == "worktrees")]
        out += [os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/") for f in files]
    return out


def scan(paths, root=ROOT):
    """[(path, line number or 0 for the file name)] for every Cyrillic hit
    outside the allowed catalogs. Binary files (a NUL byte) are skipped."""
    hits = []
    for rel in paths:
        if rel.startswith(ALLOWED_PREFIXES):
            continue
        if CYRILLIC.search(rel):
            hits.append((rel, 0))
        full = os.path.join(root, rel)
        if not os.path.isfile(full) or os.path.islink(full):
            continue
        with open(full, "rb") as fh:
            data = fh.read()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", "replace")
        for number, line in enumerate(text.splitlines(), 1):
            if CYRILLIC.search(line):
                hits.append((rel, number))
    return hits


class TestNoCyrillic(unittest.TestCase):
    def test_repository_has_no_cyrillic_outside_the_ru_and_uk_catalogs(self):
        hits = scan(_tracked_files())
        shown = ["%s:%s" % (p, n or "(file name)") for p, n in hits[:40]]
        self.assertEqual(
            hits, [],
            "Cyrillic outside locales/ru and locales/uk (%d lines; translate to "
            "English, or write language data as \\uXXXX escapes):\n  %s"
            % (len(hits), "\n  ".join(shown)))

    def test_the_detector_sees_every_cyrillic_block_and_nothing_else(self):
        for sample in ("\u041f\u0440\u0438\u0432\u0435\u0442", "\u0456", "\u0490",
                       "\u0500", "\u1c80", "\u2de0", "\ua640"):
            self.assertTrue(CYRILLIC.search(sample), repr(sample))
        for sample in ("Hola, ñandú é ß", "日本語", "中文",
                       "Hello \\u0431", "αβγ"):
            self.assertFalse(CYRILLIC.search(sample), repr(sample))

    def test_the_detector_covers_every_cyrillic_character_of_this_unicode(self):
        import unicodedata
        missed = [hex(cp) for cp in range(0x20000)
                  if unicodedata.name(chr(cp), "").startswith("CYRILLIC")
                  and not CYRILLIC.search(chr(cp))]
        self.assertEqual(missed, [])
        self.assertTrue(CYRILLIC.search(chr(0x1E030)))

    def test_without_git_the_walk_finds_the_files(self):
        with unittest.mock.patch("subprocess.run", side_effect=FileNotFoundError("git")):
            files = _tracked_files()
        self.assertIn("tests/test_no_cyrillic.py", files)
        self.assertFalse([f for f in files if f.startswith(".git/")])

    def test_the_catalogs_are_the_only_allowed_place(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for rel, text in (("locales/ru/a.json", "\u0434\u0430"), ("locales/uk/a.json", "\u0442\u0430\u043a"),
                              ("locales/de/a.json", "\u0434\u0430"), ("docs/a.md", "\u0434\u0430"),
                              ("docs/b.md", "plain english")):
                os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
                with open(os.path.join(tmp, rel), "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
            hits = scan(["locales/ru/a.json", "locales/uk/a.json", "locales/de/a.json",
                         "docs/a.md", "docs/b.md"], root=tmp)
        self.assertEqual(hits, [("locales/de/a.json", 1), ("docs/a.md", 1)])


if __name__ == "__main__":
    unittest.main()
