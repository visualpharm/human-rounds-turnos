#!/usr/bin/env python3
"""Run a bounded real-model quality batch without persisting test conversations."""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.modules.chat import chat_agent


DEFAULT_QUESTIONS = os.path.join(
    ROOT, "docs", "studies", "chat-quality-2026-07-29", "questions.json")


def _contains_any(text, values):
    folded = str(text or "").casefold()
    return any(str(value).casefold() in folded for value in values)


def _auto_checks(case, answer):
    links = [item.get("href") for item in answer.get("enlaces") or []]
    sources = [item.get("url") for item in answer.get("fuentes") or []]
    text = answer.get("texto") or ""
    checks = {}
    if case.get("must_links"):
        checks["must_links"] = all(link in links for link in case["must_links"])
    if case.get("any_link_prefixes"):
        checks["any_link_prefixes"] = any(
            str(link or "").startswith(prefix)
            for prefix in case["any_link_prefixes"] for link in links)
    if case.get("must_link_prefixes"):
        checks["must_link_prefixes"] = all(
            any(str(link or "").startswith(prefix) for link in links)
            for prefix in case["must_link_prefixes"])
    if case.get("source_prefixes"):
        checks["source_prefixes"] = all(
            any(str(url or "").startswith(prefix) for url in sources)
            for prefix in case["source_prefixes"])
    if case.get("any_text"):
        checks["any_text"] = _contains_any(text, case["any_text"])
    if case.get("forbid_text"):
        checks["forbid_text"] = not _contains_any(text, case["forbid_text"])
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--questions", default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.questions, encoding="utf-8") as fh:
        cases = [case for case in json.load(fh)
                 if int(case.get("batch") or 0) == args.batch]
    if len(cases) != 10:
        raise SystemExit(f"batch {args.batch} must contain 10 questions, got {len(cases)}")

    # Quality evaluation must not consume the visitor's conversation quota or
    # leave synthetic medical-looking messages in the transcript table.
    chat_agent._save = lambda *args, **kwargs: None
    chat_agent._load_history = lambda *args, **kwargs: []
    chat_agent._turns_used = lambda *args, **kwargs: 0

    results = []
    for case in cases:
        started = time.monotonic()
        answer = chat_agent.chat_full(
            None, case["question"], clave=f"eval:{case['id']}", canal="eval")
        checks = _auto_checks(case, answer)
        results.append({
            "id": case["id"],
            "question": case["question"],
            "answer": answer,
            "checks": checks,
            "auto_pass": all(checks.values()),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        })
        print(f"{case['id']} {'PASS' if all(checks.values()) else 'REVIEW'} "
              f"{results[-1]['elapsed_seconds']}s")

    payload = {
        "batch": args.batch,
        "model": chat_agent._model(),
        "results": results,
        "auto_pass": sum(item["auto_pass"] for item in results),
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"batch {args.batch}: {payload['auto_pass']}/10 automatic checks passed")


if __name__ == "__main__":
    main()
