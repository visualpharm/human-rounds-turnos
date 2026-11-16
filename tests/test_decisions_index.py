"""The decision log is one file per month plus a generated index (2026-10-26).

`docs/decisions/YYYY-MM.md` holds the entries dated in that month (a closed month
too big for one file is cut into parts `YYYY-MM-w<week><letter>.md`, each under
40 KB, 2026-11-06); the index in
`docs/decisions.md` (entry heading → file) is what
`scripts/decisions_index.py --write` produces from them. The fix for a stale
index is always that command; an entry written into `docs/decisions.md` or into
another month's file is moved, not indexed.
"""
import contextlib
import importlib.util
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    "decisions_index", os.path.join(ROOT, "scripts", "decisions_index.py"))
decisions_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(decisions_index)


OCTOBER_LAST = "2026-10-w5h.md"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestDecisionLogIsCurrent(unittest.TestCase):
    def test_the_index_is_what_the_month_files_produce(self):
        self.assertEqual(
            _read(decisions_index.INDEX), decisions_index.render_index(),
            "stale — run: python3 scripts/decisions_index.py --write")

    def test_every_entry_of_every_month_file_is_indexed_once(self):
        index = _read(decisions_index.INDEX)
        listed = [line for line in index.splitlines() if line.startswith("- ")]
        in_files = [
            f"- {date} · {title}"
            for month in decisions_index.month_files()
            for date, title in decisions_index.entries(month)]
        self.assertEqual(listed, in_files)
        self.assertGreaterEqual(len(in_files), decisions_index.ENTRIES_AT_SPLIT)

    def test_the_concatenated_months_are_the_whole_log(self):
        text = decisions_index.concat()
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(len(headings), sum(
            len(decisions_index.entries(m)) for m in decisions_index.month_files()))
        self.assertTrue(text.startswith("## 2026-07-08 · Fork Sacoturno"))


class TestClosedMonthParts(unittest.TestCase):
    """October 2026 is cut into parts under 40 KB (2026-11-06); nothing else changed."""

    def test_every_part_is_under_the_limit(self):
        parts = [n for n in decisions_index.month_files() if n != decisions_index.month_of(n)]
        self.assertTrue(parts)
        for name in parts:
            size = os.path.getsize(os.path.join(decisions_index.MONTHS_DIR, f"{name}.md"))
            self.assertLess(size, decisions_index.PART_LIMIT, name)

    def test_the_parts_of_october_hold_its_265_entries_in_order(self):
        names = [n for n in decisions_index.month_files() if n.startswith("2026-10")]
        self.assertNotIn("2026-10", names)
        self.assertTrue(all(decisions_index.MONTH_FILE.match(f"{n}.md") for n in names))
        dated = [date for n in names for date, _ in decisions_index.entries(n)]
        self.assertGreaterEqual(len(dated), 265)
        self.assertEqual(dated[0], "2026-10-01")
        self.assertTrue(all(d.startswith("2026-10-") for d in dated))

    def test_no_markdown_link_points_at_the_old_october_file(self):
        stale = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "test-results", ".claude"}]
            for name in files:
                if not name.endswith((".md", ".py", ".mjs", ".json")):
                    continue
                path = os.path.join(base, name)
                if os.path.abspath(path) == os.path.abspath(__file__) \
                        or os.path.dirname(path) == decisions_index.MONTHS_DIR:
                    continue
                try:
                    text = _read(path)
                except (UnicodeDecodeError, OSError):
                    continue
                if "decisions/2026-10.md" in text:
                    stale.append(os.path.relpath(path, ROOT))
        self.assertEqual(stale, [], "docs/decisions/2026-10.md is cut into parts — name the part")


class TestDecisionLogRules(unittest.TestCase):
    """The checker refuses the two ways an entry lands in the wrong place."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        os.mkdir(os.path.join(self.tmp, "decisions"))
        for month in decisions_index.month_files():
            shutil.copy(os.path.join(decisions_index.MONTHS_DIR, f"{month}.md"),
                        os.path.join(self.tmp, "decisions", f"{month}.md"))
        shutil.copy(decisions_index.INDEX, os.path.join(self.tmp, "decisions.md"))
        for name, value in (("INDEX", os.path.join(self.tmp, "decisions.md")),
                            ("MONTHS_DIR", os.path.join(self.tmp, "decisions"))):
            patcher = mock.patch.object(decisions_index, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _append(self, path, text):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def test_a_copy_of_the_log_checks_clean(self):
        self.assertEqual(decisions_index.main(["--check"]), 0)

    def test_an_entry_in_the_index_file_is_refused(self):
        rules = _read(decisions_index.INDEX).split(decisions_index.MARKER)[0]
        with open(decisions_index.INDEX, "w", encoding="utf-8") as fh:
            fh.write(rules + "## 2026-10-26 · Misplaced\nDecided: x\n\n" + decisions_index.MARKER)
        with self.assertRaisesRegex(ValueError, "move it to the end of docs/decisions/"):
            decisions_index.render_index()

    def test_an_entry_in_another_months_file_is_refused(self):
        self._append(os.path.join(decisions_index.MONTHS_DIR, "2026-07.md"),
                     "\n## 2026-10-26 · Late entry\nDecided: x\n")
        with self.assertRaisesRegex(ValueError, "belongs in docs/decisions/2026-10.md or a part of that month"):
            decisions_index.render_index()

    def test_a_new_entry_makes_the_index_stale_until_written(self):
        self._append(os.path.join(decisions_index.MONTHS_DIR, OCTOBER_LAST),
                     "\n## 2026-10-31 · New entry\nDecided: x\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(decisions_index.main(["--check"]), 1)
        self.assertIn("decisions_index.py --write", err.getvalue())
        self.assertEqual(decisions_index.main(["--write"]), 0)
        self.assertEqual(decisions_index.main(["--check"]), 0)
        self.assertIn("- 2026-10-31 · New entry\n", _read(decisions_index.INDEX))

    def test_a_stray_file_in_the_log_directory_is_refused(self):
        open(os.path.join(decisions_index.MONTHS_DIR, "notes.txt"), "w").close()
        with self.assertRaisesRegex(ValueError, r"holds only YYYY-MM\.md files and YYYY-MM-w<week><letter>\.md parts, found: \['notes\.txt'\]"):
            decisions_index.render_index()

    def test_text_between_the_header_and_the_first_entry_is_refused(self):
        path = os.path.join(decisions_index.MONTHS_DIR, "2026-11.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(decisions_index.month_header("2026-11")
                     + "A stray paragraph.\n\n## 2026-11-02 · First of November\nDecided: x\n")
        with self.assertRaisesRegex(ValueError, "the first entry must follow the header"):
            decisions_index.render_index()

    def test_a_heading_without_a_date_is_refused(self):
        self._append(os.path.join(decisions_index.MONTHS_DIR, OCTOBER_LAST),
                     "\n## An entry nobody dated\nDecided: x\n")
        with self.assertRaisesRegex(ValueError, "heading is not `## YYYY-MM-DD"):
            decisions_index.render_index()

    def test_an_index_file_without_its_marker_is_refused(self):
        with open(decisions_index.INDEX, "w", encoding="utf-8") as fh:
            fh.write("# Decisions\n\nRules only.\n")
        with self.assertRaisesRegex(ValueError, "lost its marker line"):
            decisions_index.render_index()

    def test_the_log_may_only_grow(self):
        total = sum(len(decisions_index.entries(m)) for m in decisions_index.month_files())
        with mock.patch.object(decisions_index, "ENTRIES_AT_SPLIT", total + 1):
            with self.assertRaisesRegex(ValueError, "the log is append-only"):
                decisions_index.render_index()

    def test_the_command_reports_a_refusal_on_stderr_and_exits_1(self):
        open(os.path.join(decisions_index.MONTHS_DIR, "notes.txt"), "w").close()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(decisions_index.main(["--write"]), 1)
        self.assertIn("holds only YYYY-MM.md files and YYYY-MM-w<week><letter>.md parts", err.getvalue())

    def test_the_command_prints_every_entry_for_concat(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(decisions_index.main(["--concat"]), 0)
        self.assertEqual(out.getvalue(), decisions_index.concat())

    def test_a_part_over_the_limit_is_refused(self):
        self._append(os.path.join(decisions_index.MONTHS_DIR, OCTOBER_LAST),
                     "\n## 2026-10-31 · Big\nDecided: " + "x" * decisions_index.PART_LIMIT + "\n")
        with self.assertRaisesRegex(ValueError, "a part stays under"):
            decisions_index.render_index()

    def test_a_month_that_is_both_one_file_and_parts_is_refused(self):
        path = os.path.join(decisions_index.MONTHS_DIR, "2026-10.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(decisions_index.month_header("2026-10")
                     + "## 2026-10-31 · Plain\nDecided: x\n")
        with self.assertRaisesRegex(ValueError, "both one file and cut into parts"):
            decisions_index.render_index()

    def test_a_part_needs_its_own_header_and_keeps_its_month(self):
        path = os.path.join(decisions_index.MONTHS_DIR, "2026-11-w1a.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("## 2026-11-02 · No header\nDecided: x\n")
        os.remove(os.path.join(decisions_index.MONTHS_DIR, "2026-11.md"))
        with self.assertRaisesRegex(ValueError, "must start with the month header"):
            decisions_index.render_index()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(decisions_index.month_header("2026-11-w1a")
                     + "## 2026-12-02 · Wrong month\nDecided: x\n")
        with self.assertRaisesRegex(ValueError, "belongs in docs/decisions/2026-12.md"):
            decisions_index.render_index()

    def test_a_new_month_file_needs_the_month_header(self):
        path = os.path.join(decisions_index.MONTHS_DIR, "2026-11.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("## 2026-11-02 · First of November\nDecided: x\n")
        with self.assertRaisesRegex(ValueError, "must start with the month header"):
            decisions_index.render_index()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(decisions_index.month_header("2026-11")
                     + "## 2026-11-02 · First of November\nDecided: x\n")
        self.assertIn("## 2026-11 · [decisions/2026-11.md]", decisions_index.render_index())


if __name__ == "__main__":
    unittest.main()
