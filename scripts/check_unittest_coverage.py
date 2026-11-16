#!/usr/bin/env python3
"""Fail closed when a managed unittest run silently skips required coverage."""

import argparse
import json
import re
from pathlib import Path


ALLOWED_PACK_SKIP = "external private Pinamar pack is not configured"
# Two more honest skips, each one exact reason, counted on their own line of the verdict:
# an optional scanner the runner does not carry, and a test of a file `.gitattributes`
# export-ignore leaves out of an archived copy (tests/_archive.py holds the same text).
GITLEAKS_ABSENT_SKIP = "gitleaks is not installed on this runner"
ARCHIVE_COPY_SKIP = "archived copy: export-ignore leaves out the private file this test needs"
ALLOWED_SKIPS = (ALLOWED_PACK_SKIP, GITLEAKS_ABSENT_SKIP, ARCHIVE_COPY_SKIP)


def floor_minimum(path=None):
    """The minimum test count recorded in scripts/coverage_floor.json: `write` keeps it at
    97 % of the last run that raised it (scripts/coverage_floor.py), so a suite that silently
    loses a few percent of its tests fails, not only one that loses two thirds."""
    path = Path(path) if path else Path(__file__).with_name("coverage_floor.json")
    minimum = json.loads(path.read_text(encoding="utf-8")).get("min_tests")
    if type(minimum) is not int or minimum < 1:
        raise ValueError("invalid min_tests in the coverage floor file")
    return minimum


def inspect_result_report(report, *, minimum):
    """Gate actual unittest result records, not interleaved progress output."""
    counters = ("tests_run", "failures", "errors", "unexpected_successes", "expected_failures")
    if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
            or report["schema_version"] != 1
            or any(type(report.get(key)) is not int or report[key] < 0 for key in counters)
            or not isinstance(report.get("skipped"), list)):
        raise ValueError("invalid unittest result report schema")
    skips = report["skipped"]
    if any(not isinstance(item, dict) or not isinstance(item.get("test_id"), str)
           or not item["test_id"] or not isinstance(item.get("reason"), str)
           for item in skips):
        raise ValueError("invalid unittest skip record")
    by_reason = {reason: sum(item["reason"] == reason for item in skips) for reason in ALLOWED_SKIPS}
    allowed = sum(by_reason.values())
    unexpected = [f"{item['test_id']}: {item['reason']!r}" for item in skips
                  if item["reason"] not in ALLOWED_SKIPS]
    problems = []
    if report["tests_run"] < minimum:
        problems.append(f"only {report['tests_run']} tests ran (expected at least {minimum})")
    if unexpected:
        problems.append(f"{len(unexpected)} unexpected test skip(s); required coverage must execute")
    if report["failures"] or report["errors"] or report["unexpected_successes"]:
        problems.append("unittest recorded failures, errors, or unexpected successes")
    return {"ran": report["tests_run"], "skipped": len(skips), "allowed": allowed,
            "by_reason": by_reason, "unexpected_lines": unexpected, "problems": problems}


def inspect_log(log, *, minimum, allowed_reasons=ALLOWED_SKIPS):
    """Return coverage facts and problems from the final unittest summary.

    A test is allowlisted only when its verbose result line ends with the
    exact quoted reason. Substrings, suffixes, and preliminary summaries do
    not count.
    """
    ran_matches = list(re.finditer(r"(?m)^Ran (\d+) tests?\b", log))
    ran = int(ran_matches[-1].group(1)) if ran_matches else 0
    final_summary = log[ran_matches[-1].end():] if ran_matches else ""
    final_results_start = ran_matches[-2].end() if len(ran_matches) > 1 else 0
    final_results = (log[final_results_start:ran_matches[-1].start()]
                     if ran_matches else "")
    skip_match = re.search(r"\bskipped=(\d+)\b", final_summary)
    skipped = int(skip_match.group(1)) if skip_match else 0
    exact = {
        reason: re.compile(r"(?m)^[^\r\n]* \.\.\. skipped " + re.escape(repr(reason)) + r"\s*$")
        for reason in allowed_reasons
    }
    by_reason = {reason: len(rx.findall(final_results)) for reason, rx in exact.items()}
    allowed = sum(by_reason.values())
    unexpected_lines = [
        line for line in final_results.splitlines()
        if "skipped" in line and not any(rx.fullmatch(line) for rx in exact.values())
    ]
    problems = []
    if ran < minimum:
        problems.append(
            f"only {ran} tests ran (expected at least {minimum}); discovery may be broken"
        )
    if allowed > skipped:
        problems.append(
            f"found {allowed} allowlisted result lines but summary reports {skipped} skips"
        )
    elif skipped != allowed:
        problems.append(
            f"{skipped - allowed} unexpected test skip(s); database and optional-tool coverage must execute"
        )
    return {"ran": ran, "skipped": skipped, "allowed": allowed, "by_reason": by_reason,
            "unexpected_lines": unexpected_lines,
            "problems": problems}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("log")
    parser.add_argument("--minimum", type=int,
                        help="fewer tests than this fail; default: min_tests of "
                             "scripts/coverage_floor.json")
    parser.add_argument("--result-json", help="authoritative standard unittest result records")
    args = parser.parse_args(argv)
    try:
        minimum = args.minimum if args.minimum is not None else floor_minimum()
        if args.result_json:
            with open(args.result_json, encoding="utf-8") as handle:
                facts = inspect_result_report(json.load(handle), minimum=minimum)
        else:
            with open(args.log, encoding="utf-8", errors="replace") as handle:
                facts = inspect_log(handle.read(), minimum=minimum)
    except (OSError, ValueError) as exc:
        print(f"coverage guard: result evidence unavailable or invalid ({type(exc).__name__})")
        return 1
    if facts["problems"]:
        for problem in facts["problems"]:
            print(f"coverage guard: {problem}")
        for line in facts["unexpected_lines"][:20]:
            print(f"coverage evidence: {line[:500]}")
        return 1
    by_reason = facts["by_reason"]
    line = (f"coverage guard OK: {facts['ran']} tests ran; "
            f"{by_reason.get(ALLOWED_PACK_SKIP, 0)} exact external-pack skips")
    # Every other honest skip is named, so a runner without gitleaks or an archived copy
    # says what it did not run instead of passing quietly.
    for reason in ALLOWED_SKIPS[1:]:
        if by_reason.get(reason):
            line += f"; {by_reason[reason]} skipped: {reason}"
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
