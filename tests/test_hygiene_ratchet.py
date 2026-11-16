"""test_hygiene_ratchet.py - tests do not write the process environment or sleep blind any more than they do.

`scripts/hygiene_ratchet.py` counts, per file under tests/, the sites that write `os.environ`
(`os.environ[...] = ...`, `.update`, `.setdefault`, `.pop`, `os.putenv`), the `patch.dict(...)` calls and the
blind pauses (`time.sleep`, `waitForTimeout`, `setTimeout(resolve, <ms>)`), and `scripts/hygiene_floor.json`
holds how many each file may have. This test is the gate: a file with more sites than its allowance, or a
NEW file with any, is red. The number only goes down; a file that improved gets its line lowered with
`python3 scripts/hygiene_ratchet.py write`.

Fix a red result with the fixtures (`tests/_env.py` for the environment, `tests/_clock.py` for the time), not by
raising a number. How to write such a test: docs/solutions/dev/test-environment-and-time.md.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import hygiene_ratchet  # noqa: E402


class TestTheTree(unittest.TestCase):
    def test_no_file_has_more_sites_than_the_snapshot_allows(self):
        bad = hygiene_ratchet.problems(hygiene_ratchet.scan(ROOT), hygiene_ratchet.load_floor())
        self.assertEqual([], bad, "\n" + "\n".join(bad))

    def test_the_snapshot_is_well_formed(self):
        with open(hygiene_ratchet.FLOOR, encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(raw)
        self.assertEqual(hygiene_ratchet.SCHEMA_VERSION, data["schema_version"])
        files = data["files"]
        self.assertEqual(sorted(files), list(files), "one line per file, sorted: branches merge without conflicts")
        for rel, entry in files.items():
            self.assertTrue(rel.startswith("tests/"), rel)
            self.assertTrue(entry, "%s has no site left: delete its line (write does)" % rel)
            for kind, n in entry.items():
                self.assertIn(kind, hygiene_ratchet.KINDS, rel)
                self.assertGreater(n, 0, "%s: %s" % (rel, kind))
        # `write` leaves the file exactly as it is when nothing improved: the committed text is its output
        out = os.path.join(tempfile.mkdtemp(), "floor.json")
        self.addCleanup(os.remove, out)
        hygiene_ratchet.dump_floor(files, out)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(raw, f.read())

    def test_the_fixtures_are_not_counted_against_the_tests(self):
        self.assertNotIn("tests/_env.py", hygiene_ratchet.scan(ROOT))


class TestPythonCounts(unittest.TestCase):
    def count(self, source):
        return hygiene_ratchet.count_python(source)

    def test_every_way_of_writing_the_environment_counts(self):
        src = (
            "import os\n"
            "os.environ['A'] = '1'\n"
            "os.environ.update({'B': '2'})\n"
            "os.environ.setdefault('C', '3')\n"
            "os.environ.pop('D', None)\n"
            "os.environ.popitem()\n"
            "os.environ.clear()\n"
            "os.putenv('E', '5')\n"
            "os.unsetenv('E')\n"
            "del os.environ['A']\n"
            "os.environ['F'] += 'x'\n"
            "os.environ |= {'G': '1'}\n"
        )
        self.assertEqual({"environ": 11}, self.count(src))

    def test_a_bare_environ_imported_from_os_counts(self):
        self.assertEqual({"environ": 2}, self.count("from os import environ\nenviron['A'] = '1'\nenviron.pop('A')\n"))

    def test_reading_and_copying_the_environment_does_not_count(self):
        src = ("import os\n"
               "x = os.environ.get('A')\n"
               "y = os.environ['A']\n"
               "env = dict(os.environ)\n"
               "env['A'] = '1'\n"
               "z = os.environ.copy()\n"
               "text = \"os.environ['A'] = '1' and time.sleep(1)\"  # in a string\n")
        self.assertEqual({}, self.count(src))

    def test_patch_dict_counts_in_all_its_spellings_and_as_a_decorator(self):
        src = ("from unittest import mock\n"
               "from unittest.mock import patch\n"
               "import unittest.mock\n"
               "@patch.dict('os.environ', {'A': '1'})\n"
               "def f():\n"
               "    with mock.patch.dict(os.environ, {'B': '2'}):\n"
               "        pass\n"
               "    with unittest.mock.patch.dict(d, {}):\n"
               "        pass\n"
               "    patch.object(x, 'dict')\n")
        self.assertEqual({"patch_dict": 3}, self.count(src))

    def test_time_sleep_counts_and_a_stubbed_sleep_does_not(self):
        src = ("import time\n"
               "time.sleep(0.1)\n"
               "mock.patch.object(time, 'sleep')\n"
               "S3(sleep=lambda _: None)\n"
               "asyncio.sleep(0)\n")
        self.assertEqual({"pause": 1}, self.count(src))
        self.assertEqual({"pause": 1}, self.count("from time import sleep\nsleep(1)\n"))
        self.assertEqual({}, self.count("def sleep(x):\n    pass\nsleep(1)\n"))

    def test_a_file_that_does_not_parse_counts_nothing(self):
        self.assertEqual({}, self.count("def broken(:\n"))


class TestJavascriptCounts(unittest.TestCase):
    def count(self, source):
        return hygiene_ratchet.count_javascript(source)

    def test_a_fixed_sleep_counts(self):
        src = ("await page.waitForTimeout(100);\n"
               "await new Promise((resolve) => setTimeout(resolve, 50));\n"
               "await new Promise(r => setTimeout(r, ms));\n")
        self.assertEqual({"pause": 3}, self.count(src))

    def test_a_flush_a_deadline_and_a_fake_timer_do_not_count(self):
        src = ("await new Promise((resolve) => setTimeout(resolve, 0));\n"
               "new Promise((_, reject) => setTimeout(() => reject(new Error('x')), 8000));\n"
               "test.setTimeout(30_000);\n"
               "const timers = { setTimeout(fn, ms) { return 1; } };\n"
               "globalThis.setTimeout = (fn, ms) => 1;\n"
               "const t = (fn, ms) => 1; setTimeout(fn, ms) => 2;\n")
        self.assertEqual({}, self.count(src))

    def test_comments_do_not_count(self):
        src = ("// await page.waitForTimeout(100);\n"
               "/* await new Promise(r => setTimeout(r, 50)); */\n"
               "const a = 1; // setTimeout(r, 5)\n")
        self.assertEqual({}, self.count(src))


class TestTheRatchet(unittest.TestCase):
    def test_a_new_file_with_any_site_is_red(self):
        found = {"tests/test_nuevo.py": {"environ": 1}}
        out = hygiene_ratchet.problems(found, {})
        self.assertEqual(1, len(out))
        self.assertIn("tests/test_nuevo.py", out[0])
        self.assertIn("a new file has none", out[0])
        self.assertIn("tests/_env.py", out[0])

    def test_growing_inside_a_listed_file_is_red_for_the_kind_that_grew(self):
        floor = {"tests/test_x.py": {"environ": 2, "pause": 1}}
        self.assertEqual([], hygiene_ratchet.problems({"tests/test_x.py": {"environ": 2, "pause": 1}}, floor))
        out = hygiene_ratchet.problems({"tests/test_x.py": {"environ": 2, "pause": 2}}, floor)
        self.assertEqual(1, len(out))
        self.assertIn("`pause`", out[0])
        self.assertIn("allows 1", out[0])

    def test_a_site_traded_for_another_kind_is_red(self):
        floor = {"tests/test_x.py": {"environ": 2}}
        out = hygiene_ratchet.problems({"tests/test_x.py": {"pause": 1}}, floor)
        self.assertEqual(1, len(out))

    def test_shrinking_is_green_and_write_lowers_but_never_raises(self):
        floor = {"tests/a.py": {"environ": 3, "pause": 1}, "tests/b.py": {"environ": 1}, "tests/gone.py": {"pause": 2}}
        found = {"tests/a.py": {"environ": 1, "pause": 4}, "tests/new.py": {"environ": 9}}
        self.assertEqual([], hygiene_ratchet.problems({"tests/a.py": {"environ": 1, "pause": 1}}, floor))
        self.assertEqual({"tests/a.py": {"environ": 1, "pause": 1}}, hygiene_ratchet.lowered(found, floor))

    def test_the_totals_add_up_per_kind(self):
        files = {"a": {"environ": 2}, "b": {"environ": 1, "pause": 4}}
        self.assertEqual({"environ": 3, "patch_dict": 0, "pause": 4}, hygiene_ratchet.totals(files))


class TestScanAndWrite(unittest.TestCase):
    def tree(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for rel, body in files.items():
            path = os.path.join(tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        return tmp.name

    def test_only_the_test_directory_is_scanned_and_python_and_javascript_both(self):
        root = self.tree({
            "tests/test_a.py": "import os\nos.environ['A'] = '1'\n",
            "tests/e2e/x.spec.mjs": "await page.waitForTimeout(5);\n",
            "tests/node_modules/dep/index.js": "await page.waitForTimeout(5);\n",
            "tests/_env.py": "import os\nos.environ['A'] = '1'\n",
            "tests/notes.md": "os.environ['A'] = '1'\n",
            "core/x.py": "import os\nos.environ['A'] = '1'\n",
        })
        self.assertEqual({"tests/test_a.py": {"environ": 1}, "tests/e2e/x.spec.mjs": {"pause": 1}},
                         hygiene_ratchet.scan(root))

    def test_dump_writes_one_line_per_file_and_load_reads_it_back(self):
        out = os.path.join(self.tree({"x/y": ""}), "floor.json")
        files = {"tests/b.py": {"pause": 2, "environ": 1}, "tests/a.py": {"patch_dict": 3}}
        hygiene_ratchet.dump_floor(files, out)
        with open(out, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual('  "tests/a.py": {"patch_dict": 3},', lines[3])
        self.assertEqual('  "tests/b.py": {"environ": 1, "pause": 2}', lines[4])
        self.assertEqual(files, hygiene_ratchet.load_floor(out))


class TestCommandLine(unittest.TestCase):
    """`check`, `report` and `write` on a small fake tree and a snapshot in a temp directory."""

    def run_main(self, floor, found, *argv):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "floor.json")
        if floor is not None:
            hygiene_ratchet.dump_floor(floor, path)
        out = io.StringIO()
        with mock.patch.object(hygiene_ratchet, "FLOOR", path), \
                mock.patch.object(hygiene_ratchet, "scan", return_value=found), \
                contextlib.redirect_stdout(out):
            code = hygiene_ratchet.main(list(argv))
        return code, out.getvalue(), path

    def test_check_is_green_at_the_allowance_and_red_above_it(self):
        code, text, _ = self.run_main({"tests/a.py": {"environ": 2}}, {"tests/a.py": {"environ": 2}}, "check")
        self.assertEqual(0, code)
        self.assertIn("test hygiene:", text)
        code, text, _ = self.run_main({"tests/a.py": {"environ": 2}}, {"tests/a.py": {"environ": 3}}, "check")
        self.assertEqual(1, code)
        self.assertIn("tests/a.py: 3 `environ` site(s)", text)

    def test_report_lists_the_worst_files(self):
        code, text, _ = self.run_main({"tests/a.py": {"pause": 1}},
                                      {"tests/a.py": {"pause": 1}, "tests/b.py": {"environ": 5}}, "report")
        self.assertEqual(0, code)
        self.assertLess(text.index("tests/b.py"), text.index("tests/a.py"))

    def test_write_lowers_and_init_takes_the_tree(self):
        code, _, path = self.run_main({"tests/a.py": {"environ": 3}}, {"tests/a.py": {"environ": 1}}, "write")
        self.assertEqual(0, code)
        self.assertEqual({"tests/a.py": {"environ": 1}}, hygiene_ratchet.load_floor(path))
        code, _, path = self.run_main(None, {"tests/n.py": {"pause": 4}}, "write", "--init")
        self.assertEqual(0, code)
        self.assertEqual({"tests/n.py": {"pause": 4}}, hygiene_ratchet.load_floor(path))

    def test_a_snapshot_of_another_schema_is_refused(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "floor.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 99, "files": {}}, f)
        with self.assertRaises(ValueError):
            hygiene_ratchet.load_floor(path)


if __name__ == "__main__":
    unittest.main()
