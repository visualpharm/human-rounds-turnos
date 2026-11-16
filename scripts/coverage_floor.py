#!/usr/bin/env python3
"""Coverage floors: what the backend suite really exercises, with a ratchet under it.

    python3 scripts/coverage_floor.py check [--kind js] --summary <summary.json> [--report <backend-results.json>]
    python3 scripts/coverage_floor.py write [--kind js] --summary <summary.json> [--report <backend-results.json> | --log <frontend.log>] [--lower]
    python3 scripts/coverage_floor.py count --log <frontend.log>

The sharded runner (`scripts/run_backend_shards.py --coverage-dir D`) runs every test file
under coverage.py (branch mode), combines the data files and leaves, in D:

* `coverage-summary.json` — lines and branches covered/total per package (the first path
  component; `(root)` = the files at the repository root) plus `total`;
* `coverage-summary.txt`  — the same as a table, next to the floors; CI keeps it as an artifact;
* `coverage-files.json`   — coverage.py's own per-file JSON report.

`scripts/coverage_floor.json` holds the floors:

* `min_tests` — fewer tests than this means the suite silently lost some
  (`scripts/check_unittest_coverage.py` reads it);
* `python.<package>.lines|branches` — a package whose line or branch coverage drops below its
  floor turns the surface red. A package with no floor is red too: run `write`;
* `min_js_tests` — the same for the frontend suite: the `# tests N` lines of `node --test`
  summed over the log (`count --log`; checked even where c8 is not installed);
* `js.<package>.lines|branches` — the same for the frontend suite (`--kind js`): `c8` wraps
  `node --test` (`scripts/c8rc.json`, `npm ci` installs it) and its `coverage-summary.json` is
  summed per package here (`static/<dir>` for the files under static/, else the first path
  component, `(root)` for files at the root). It counts what the node processes of the tests
  execute: code a test evaluates with `vm.runInContext` from extracted text, or loads in a
  Chromium page, is not attributed to its file, so a file such as `admin-app.js` shows 0 %
  — the number is a floor to keep from falling, not the share of the frontend that is tested.

Floors only go up. `write` sets each floor to the measured value minus `margin_points`
(0.5 percentage points, rounded down to 0.1) and `min_tests` to 97 % of the tests that ran, and
never lowers either — like `tests/snapshots.py --write`, the fix for a stale file is the command,
not a hand-pasted number. A deliberate drop (dead code deleted, a package split) is
`write --lower`: the diff of the JSON then says what was given up.

Run from the repository root; `check` and `write` need nothing but the standard library.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FLOOR_FILE = REPO / "scripts" / "coverage_floor.json"
RCFILE = REPO / "scripts" / "coveragerc"
SCHEMA_VERSION = 1
ROOT_PACKAGE = "(root)"
MARGIN_POINTS = 0.5
TEST_COUNT_SHARE = 0.97
SUMMARY_JSON = "coverage-summary.json"
SUMMARY_TEXT = "coverage-summary.txt"
FILES_JSON = "coverage-files.json"
# `node --test` prints "# tests N" as TAP and "ℹ tests N" with the spec reporter; the frontend
# runner prints one summary per lane, so the log's count is the sum.
JS_TESTS_LINE = re.compile(r"(?m)^(?:# |ℹ )tests (\d+)\s*$")
KINDS = ("lines", "branches")
SECTIONS = ("python", "js")
JS_NESTED = ("static", "app")
# The application package keeps a floor per subpackage (`app/core`, `app/modules`, ...); its
# own top-level modules (`app/main.py`, `app/worker.py`) count as `app`.
PY_NESTED = ("app",)


# ---------------------------------------------------------------- measuring

def percent(covered, total):
    """Coverage in percent; nothing to cover is 100 %."""
    return 100.0 if not total else 100.0 * covered / total


def package_of(path, repo=REPO, nested=()):
    """First path component of a source file relative to the repository (`static/<dir>` for
    the first components listed in `nested`)."""
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.relative_to(repo)
        except ValueError:
            return None
    parts = p.parts
    if not parts or parts[0] == "..":
        return None
    if len(parts) == 1:
        return ROOT_PACKAGE
    if parts[0] in nested and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _empty_row():
    return {"lines": {"covered": 0, "total": 0}, "branches": {"covered": 0, "total": 0}, "files": 0}


def summarize_files(files, repo=REPO):
    """Per-package sums from coverage.py's JSON `files` mapping."""
    packages = {}
    for name, info in files.items():
        package = package_of(name, repo, nested=PY_NESTED)
        if package is None:
            continue
        s = info["summary"]
        row = packages.setdefault(package, _empty_row())
        row["lines"]["covered"] += s["covered_lines"]
        row["lines"]["total"] += s["num_statements"]
        row["branches"]["covered"] += s.get("covered_branches", 0)
        row["branches"]["total"] += s.get("num_branches", 0)
        row["files"] += 1
    return _with_total(packages)


def summarize_c8(files, repo=REPO):
    """Per-package sums from c8's `coverage-summary.json` (a mapping from absolute path to
    `{lines, branches, ...}`, plus a `total` entry that is skipped)."""
    packages = {}
    for name, info in files.items():
        if name == "total":
            continue
        package = package_of(name, repo, nested=JS_NESTED)
        if package is None:
            continue
        row = packages.setdefault(package, _empty_row())
        for kind in KINDS:
            row[kind]["covered"] += info[kind]["covered"]
            row[kind]["total"] += info[kind]["total"]
        row["files"] += 1
    return _with_total(packages)


def _with_total(packages):
    total = _empty_row()
    for row in packages.values():
        for kind in KINDS:
            total[kind]["covered"] += row[kind]["covered"]
            total[kind]["total"] += row[kind]["total"]
        total["files"] += row["files"]
    return {"schema_version": SCHEMA_VERSION, "packages": dict(sorted(packages.items())),
            "total": total}


def combine_and_summarize(coverage_dir, rcfile=RCFILE, repo=REPO):
    """Combine the parallel data files in `coverage_dir`, write the three artifacts there,
    return the summary. Raises when coverage.py is missing or there is no data."""
    import coverage  # not a runtime dependency: scripts/requirements-ci.txt
    directory = Path(coverage_dir)
    previous = os.getcwd()
    os.chdir(repo)  # the rcfile's `source = .` and the report paths are relative to it
    try:
        cov = coverage.Coverage(data_file=str(directory / ".coverage"), config_file=str(rcfile))
        cov.combine(data_paths=[str(directory)], strict=True)
        cov.save()
        files_json = directory / FILES_JSON
        cov.json_report(outfile=str(files_json))
    finally:
        os.chdir(previous)
    files = json.loads(files_json.read_text(encoding="utf-8"))["files"]
    summary = summarize_files(files, repo)
    _write_json(directory / SUMMARY_JSON, summary)
    (directory / SUMMARY_TEXT).write_text(
        render_table(summary, load_floor(missing_ok=True)), encoding="utf-8")
    return summary


# ---------------------------------------------------------------- floors

def _write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=1, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, target)
        os.chmod(target, 0o644)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_floor(path=FLOOR_FILE, missing_ok=False):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing_ok:
            return {"min_tests": None, "min_js_tests": 0, "python": {}, "js": {}}
        raise
    if (not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION
            or type(data.get("min_tests")) is not int or data["min_tests"] < 0
            or type(data.get("min_js_tests")) is not int or data["min_js_tests"] < 0):
        raise ValueError("invalid coverage floor file")
    for section in SECTIONS:
        if not isinstance(data.get(section), dict):
            raise ValueError(f"invalid coverage floor file: no {section!r} section")
        for package, floors in data[section].items():
            if (not isinstance(floors, dict)
                    or any(not isinstance(floors.get(kind), (int, float)) for kind in KINDS)):
                raise ValueError(f"invalid coverage floor for {package}")
    return data


def floor_for(measured, margin=MARGIN_POINTS):
    """The floor `write` sets for a measured percentage: margin below, rounded down to 0.1."""
    return max(0.0, math.floor(round((measured - margin) * 10, 6)) / 10)


def inspect(summary, floor, section="python"):
    """Problems and one row per package: (package, kind, measured %, floor %)."""
    problems = []
    rows = []
    packages = summary["packages"]
    for package, floors in floor[section].items():
        if package not in packages:
            problems.append(f"{package}: in the floor file but not in the run "
                            "(package removed or never imported); run `write --lower`")
            continue
        for kind in KINDS:
            got = percent(packages[package][kind]["covered"], packages[package][kind]["total"])
            rows.append((package, kind, got, floors[kind]))
            if round(got, 2) < floors[kind]:
                problems.append(f"{package}: {kind} coverage {got:.2f} % is below the floor "
                                f"{floors[kind]:.1f} %")
    for package in packages:
        if package not in floor[section]:
            problems.append(f"{package}: measured but has no floor; run "
                            "`python3 scripts/coverage_floor.py write`"
                            + (" --kind js" if section == "js" else ""))
    return problems, rows


def render_table(summary, floor, section="python"):
    floors = floor.get(section, {})
    lines = [f"{'package':<16} {'files':>5} {'lines':>17} {'floor':>6} {'branches':>19} {'floor':>6}"]

    def cell(row, kind):
        c, t = row[kind]["covered"], row[kind]["total"]
        return f"{c:>6}/{t:<6} {percent(c, t):6.2f}%"

    for package, row in list(summary["packages"].items()) + [("total", summary["total"])]:
        f = floors.get(package, {})
        lf = f"{f['lines']:.1f}" if "lines" in f else "-"
        bf = f"{f['branches']:.1f}" if "branches" in f else "-"
        lines.append(f"{package:<16} {row['files']:>5} {cell(row, 'lines')} {lf:>6} "
                     f"{cell(row, 'branches')} {bf:>6}")
    return "\n".join(lines) + "\n"


def js_tests_in_log(text):
    """The frontend tests a `node --test` log reports (0 when it has no summary line)."""
    return sum(int(n) for n in JS_TESTS_LINE.findall(text))


def updated_floor(floor, summary, tests_run=None, lower=False, margin=MARGIN_POINTS,
                  section="python", js_tests_run=None):
    """The floor file after `write`: the section's floors move up to the measured value minus
    the margin, never down (unless `lower`); packages that left the run stay unless `lower`;
    the other section is untouched."""
    new = {"schema_version": SCHEMA_VERSION, "margin_points": margin,
           "min_tests": floor["min_tests"], "min_js_tests": floor["min_js_tests"]}
    for other in SECTIONS:
        new[other] = dict(floor[other])
    mine = {}
    for package, row in summary["packages"].items():
        old = floor[section].get(package, {})
        entry = {}
        for kind in KINDS:
            wanted = floor_for(percent(row[kind]["covered"], row[kind]["total"]), margin)
            entry[kind] = wanted if lower else max(old.get(kind, 0.0), wanted)
        mine[package] = entry
    if not lower:
        for package, old in floor[section].items():
            mine.setdefault(package, old)
    new[section] = dict(sorted(mine.items()))
    if tests_run is not None:
        wanted = math.floor(tests_run * TEST_COUNT_SHARE)
        new["min_tests"] = wanted if lower else max(floor["min_tests"], wanted)
    if js_tests_run is not None:
        wanted = math.floor(js_tests_run * TEST_COUNT_SHARE)
        new["min_js_tests"] = wanted if lower else max(floor["min_js_tests"], wanted)
    return new


# ---------------------------------------------------------------- command line

def _tests_run(path):
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))["tests_run"]


def _load_summary(path, kind):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if kind == "js":  # c8's own coverage-summary.json
        if not isinstance(data, dict) or "total" not in data:
            raise ValueError("invalid c8 coverage summary")
        try:
            return summarize_c8(data)
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid c8 coverage summary") from exc
    if data.get("schema_version") != SCHEMA_VERSION or "packages" not in data:
        raise ValueError("invalid coverage summary")
    return data


def _count(args):
    try:
        floor = load_floor(args.floor)
        ran = js_tests_in_log(Path(args.log).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        print(f"frontend tests: evidence unavailable or invalid ({type(exc).__name__}: {exc})")
        return 1
    if ran < floor["min_js_tests"]:
        print(f"frontend tests: only {ran} tests ran (expected at least {floor['min_js_tests']}; "
              "the log has no summary line, or the suite lost tests)")
        return 1
    print(f"frontend tests OK: {ran} tests ran (floor {floor['min_js_tests']})")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    count = sub.add_parser("count", help="fail when a frontend log reports fewer tests than min_js_tests")
    count.add_argument("--log", required=True, help="the output of the `node --test` run")
    count.add_argument("--floor", default=str(FLOOR_FILE))
    for name in ("check", "write"):
        p = sub.add_parser(name)
        p.add_argument("--kind", choices=("python", "js"), default="python",
                       help="python: coverage-summary.json of the sharded backend run; "
                            "js: c8's coverage-summary.json of the frontend suite")
        p.add_argument("--summary", required=True, help="the run's coverage summary")
        p.add_argument("--report", help="backend-results.json of the run (tests_run)")
        p.add_argument("--floor", default=str(FLOOR_FILE))
    sub.choices["write"].add_argument("--lower", action="store_true",
                                      help="also lower floors to the measured value")
    sub.choices["write"].add_argument("--log", help="frontend log: raises min_js_tests (--kind js)")
    args = parser.parse_args(argv)
    if args.command == "count":
        return _count(args)
    section = args.kind
    try:
        summary = _load_summary(args.summary, section)
        tests_run = _tests_run(args.report)
        js_tests_run = (js_tests_in_log(Path(args.log).read_text(encoding="utf-8", errors="replace"))
                        if getattr(args, "log", None) else None)
        if args.command == "write":
            floor = load_floor(args.floor, missing_ok=True)
            if floor["min_tests"] is None:
                floor["min_tests"] = 0
            new = updated_floor(floor, summary, tests_run, lower=args.lower, section=section,
                                js_tests_run=js_tests_run)
            _write_json(args.floor, new)
            print(f"coverage floor written: {len(new[section])} {section} packages, "
                  f"min_tests {new['min_tests']}, min_js_tests {new['min_js_tests']}")
            return 0
        floor = load_floor(args.floor)
    except (OSError, ValueError, KeyError) as exc:
        print(f"coverage floor: evidence unavailable or invalid ({type(exc).__name__}: {exc})")
        return 1
    problems, _ = inspect(summary, floor, section)
    if tests_run is not None and tests_run < floor["min_tests"]:
        problems.append(f"only {tests_run} tests ran (floor {floor['min_tests']})")
    print(render_table(summary, floor, section), end="")
    if problems:
        for problem in problems:
            print(f"coverage floor: {problem}")
        return 1
    print(f"coverage floor OK: {len(floor[section])} {section} packages at or above their floors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
