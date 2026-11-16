"""The `TURNOS_*` registry (docs/env-registry.md) is generated and complete (2026-10-30, KISS-2).

A variable the code reads is a row of the registry; a name the architecture docs mention is a variable some code
reads (or a documented exception below). The fix for a red test is `python3 scripts/env_registry.py --write` — or,
for a stale doc, correcting the doc.
"""
import contextlib
import importlib.util
import io
import os
import re
import tempfile
import subprocess
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location("env_registry", os.path.join(ROOT, "scripts", "env_registry.py"))
env_registry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(env_registry)

# Names the architecture docs may mention without a reader in product code, with the reason.
DOC_EXCEPTIONS = {
    "TURNOS_X": "placeholder in an example",
    "TURNOS_OPCIONALES": "a JS constant of the HSI extension (lib/shared.js), not an environment variable",
}
# The docs the check reads: the architecture notes and the top-level docs (roadmaps, decisions, solutions, the rename
# map and QA notes are history and keep the names they had).
SKIPPED_DOCS = {"env-registry.md", "rename-map.md"}  # the registry itself; the rename map records the old names
DOC_DIRS = ("docs", "docs/architecture", "docs/deploy", "docs/operations")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _doc_files():
    for rel in DOC_DIRS:
        base = os.path.join(ROOT, rel)
        for name in sorted(os.listdir(base)):
            if name.endswith(".md") and name not in SKIPPED_DOCS:
                yield os.path.join(rel, name)


class TestRegistryIsCurrent(unittest.TestCase):
    def test_the_registry_is_what_the_code_produces(self):
        self.assertEqual(
            _read(env_registry.REGISTRY), env_registry.render(),
            "stale — run: python3 scripts/env_registry.py --write")

    def test_check_and_write_agree(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(env_registry.main(["--check"]), 0)
        self.assertIn("current", out.getvalue())

    def test_write_rewrites_the_registry_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "env-registry.md")
            with mock.patch.object(env_registry, "REGISTRY", target), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(env_registry.main(["--write"]), 0)
                self.assertEqual(_read(target), env_registry.render())

    def test_a_stale_registry_fails_the_check(self):
        real_read = env_registry._read
        stale = lambda rel: "stale\n" if rel == "docs/env-registry.md" else real_read(rel)  # noqa: E731
        with mock.patch.object(env_registry, "_read", stale), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(env_registry.main(["--check"]), 1)

    def test_no_flag_prints_help(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(env_registry.main([]), 2)


class TestEveryReadNameIsListed(unittest.TestCase):
    def test_every_turnos_constant_in_product_code_is_a_row(self):
        listed = env_registry.registry_names()
        found, _setters, _tests = env_registry.collect()
        self.assertEqual(sorted(set(found) - listed), [])
        self.assertEqual(sorted(listed - set(found)), [], "a row for a name no code holds any more")

    def test_the_names_the_known_readers_use_are_listed(self):
        listed = env_registry.registry_names()
        for name in ("TURNOS_QUEUE_WORKER", "TURNOS_PUBLIC_ORIGIN", "TURNOS_PUCO_GWT_PERM_", "TURNOS_AI_HEALTH_OFF",
                     "TURNOS_IA_SALUD_OFF"):
            self.assertIn(name, listed)

    def test_every_legacy_alias_is_read_and_its_new_name_too(self):
        listed = env_registry.registry_names()
        for new, old in env_registry.ALIAS_PAIRS:
            self.assertIn(new, listed, new)
            self.assertIn(old, listed, old)

    def test_tests_and_docs_are_not_product_code(self):
        found, _setters, _tests = env_registry.collect()
        for name in ("TURNOS_TEST_SLOW_ENDPOINT", "TURNOS_SHUTDOWN_DRAIN_SECONDS", "TURNOS_WHATSAPP_URL"):
            self.assertNotIn(name, found)


class TestScanners(unittest.TestCase):
    def test_python_constants_defaults_and_aliases(self):
        found = {}
        env_registry.scan_python("m.py", (
            '"""TURNOS_IN_DOCSTRING is not read."""\n'
            "import os\n"
            "# the first knob\n"
            'A = os.environ.get("TURNOS_A", "3")\n'
            'B = _env2("TURNOS_B", "TURNOS_OLD_B")\n'
            'C = config.get("TURNOS_C", default=7)\n'
            'D = "TURNOS_FAMILY_"\n'
            'E = "not TURNOS_EMBEDDED in a sentence"\n'
        ), found)
        self.assertEqual(sorted(found), ["TURNOS_A", "TURNOS_B", "TURNOS_C", "TURNOS_FAMILY_", "TURNOS_OLD_B"])
        self.assertEqual(found["TURNOS_A"]["reads"], {("os.environ.get", "'3'")})
        self.assertEqual(found["TURNOS_B"]["reads"], {("_env2", None)})
        self.assertEqual(found["TURNOS_C"]["reads"], {("config.get", "7")})
        self.assertEqual(found["TURNOS_A"]["comment"], "the first knob")

    def test_settings_fields_are_read_by_their_class_with_their_default(self):
        found = {}
        env_registry.scan_python("app/core/settings.py", (
            "PORT_DEFAULT = 7700\n"
            "class ServerSettings(Base):\n"
            "    # The port.\n"
            '    port: int = Field(PORT_DEFAULT, validation_alias="PORT")\n'
            '    host: str = Field(default="0.0.0.0", validation_alias="HOST")\n'
            '    lock: bool = Field(False, validation_alias="TURNOS_LOCK_X")\n'
            '    other: str = Field("", alias="NOT_READ")\n'
            '    lower: str = Field("", validation_alias="lowercase")\n'
            "    plain: int = 3\n"
        ), found)
        self.assertEqual(sorted(found), ["HOST", "PORT", "TURNOS_LOCK_X"])
        self.assertEqual(found["PORT"]["reads"], {("ServerSettings.current", "7700")})
        self.assertEqual(found["HOST"]["reads"], {("ServerSettings.current", "'0.0.0.0'")})
        self.assertEqual(found["PORT"]["comment"], "The port.")
        self.assertIn("PORT", env_registry.registry_names("| `PORT` | x |\n| `TURNOS_A` | y |\n"))

    def test_a_python_file_that_does_not_parse_is_skipped(self):
        found = {}
        env_registry.scan_python("bad.py", "def (:\n", found)
        self.assertEqual(found, {})

    def test_shell_tokens_and_javascript_process_env(self):
        found = {}
        env_registry.scan_text("x.sh", "#!/bin/bash\n# gate\nX=${TURNOS_SH_ONE:-1}\n", found)
        env_registry.scan_text("x.mjs", 'const a = process.env.TURNOS_JS_ONE; const B = "TURNOS_JS_CONST";\n', found)
        self.assertEqual(sorted(found), ["TURNOS_JS_ONE", "TURNOS_SH_ONE"])
        self.assertEqual(found["TURNOS_SH_ONE"]["comment"], "gate")

    def test_env_example_notes_take_the_block_above_a_commented_example(self):
        notes = env_registry.env_example_notes("# What it does.\n# More.\n# TURNOS_ONE=1\n\nTURNOS_TWO=2\n")
        self.assertEqual(notes["TURNOS_ONE"], "What it does. More.")
        self.assertEqual(notes["TURNOS_TWO"], "")

    def test_one_line_squeezes_cuts_and_defaults(self):
        self.assertEqual(env_registry._one_line(""), "—")
        self.assertEqual(env_registry._one_line("a | b\n c"), "a / b c")
        long = env_registry._one_line("word " * 60)
        self.assertLessEqual(len(long), 130)
        self.assertTrue(long.endswith("..."))

    def test_read_how_and_where_and_flags(self):
        info = {"reads": {("config.get", "'1'"), ("os.environ.get", None)}, "files": {"b.py", "a.py", "c.py", "d.py"},
                "note": "—"}
        how, default = env_registry.read_how(info)
        self.assertEqual((how, default), ("`config.get`, `os.environ.get`", "`'1'`"))
        self.assertEqual(env_registry.read_how({"reads": set()}), ("referenced", "—"))
        many = {"reads": {("f", str(i)) for i in range(4)} | {(f"g{i}", None) for i in range(4)}}
        how, default = env_registry.read_how(many)
        self.assertIn("+", how)
        self.assertIn("+2", default)
        self.assertEqual(env_registry._where(info["files"]), "`a.py`, `b.py`, `c.py` +1")
        self.assertEqual(env_registry.flags("TURNOS_FAM_", info, {}), "family `TURNOS_FAM_*`")
        self.assertEqual(env_registry.flags("TURNOS_IA_FORZAR", info, {}), "legacy alias of `TURNOS_AI_FORCE`")
        self.assertEqual(env_registry.flags("TURNOS_TEST_SEAMS", info, {}), "test seam")

    def test_is_code_excludes_tests_vendor_docs_and_tooling(self):
        for rel in ("app/core/config.py", "scripts/ci_local.sh", "app/worker.py"):
            self.assertTrue(env_registry.is_code(rel), rel)
        for rel in ("tests/test_x.py", "vendor/k/x.py", ".claude/hooks/h.py", "docs/x.py", "static/a.css"):
            self.assertFalse(env_registry.is_code(rel), rel)

    def test_the_tree_walk_fallback_lists_the_same_files(self):
        with mock.patch.object(subprocess, "run", side_effect=OSError("no git")):
            walked = set(env_registry._files())
        tracked = set(env_registry._files())
        self.assertIn("scripts/env_registry.py", walked)
        self.assertTrue({"app/core/config.py", "app/worker.py"} <= walked)
        self.assertTrue({"app/core/config.py", "app/worker.py"} <= tracked)


class TestDocsNameOnlyRealVariables(unittest.TestCase):
    def test_every_turnos_name_in_the_docs_is_read_by_code(self):
        listed = env_registry.registry_names()
        families = [n for n in listed if n.endswith("_")]
        stale = []
        for rel in _doc_files():
            text = _read(os.path.join(ROOT, rel))
            for m in re.finditer(r"TURNOS_[A-Z0-9_]+", text):
                name = m.group(0)
                if name in DOC_EXCEPTIONS or name in listed:
                    continue
                wildcard = name.endswith("_") or text[m.end():m.end() + 1] == "*"
                if wildcard and any(n.startswith(name) for n in listed):
                    continue
                if any(name.startswith(f) for f in families):
                    continue
                stale.append(f"{rel}: {name}")
        self.assertEqual(sorted(set(stale)), [],
                         "names no code reads — fix the doc, or add the name to DOC_EXCEPTIONS with its reason")


if __name__ == "__main__":
    unittest.main()
