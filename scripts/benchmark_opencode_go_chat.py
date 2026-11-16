#!/usr/bin/env python3
"""Benchmark OpenCode Go models against Pinamar Turnos' real anonymous chat loop.

The harness keeps Sana's production prompt, RAG retrieval, deterministic
catalogue tools, marker parsing, and link generation. It changes only the
provider transport. No synthetic conversation is persisted.
"""

import argparse
import concurrent.futures
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai import ai_client  # noqa: E402
from app.modules.chat import chat_agent  # noqa: E402


QUESTIONS_PATH = (
    ROOT / "docs" / "studies" / "opencode-go-chat-2026-07-30" / "questions.json"
)
CHAT_COMPLETIONS_URL = "https://opencode.ai/zen/go/v1/chat/completions"
MESSAGES_URL = "https://opencode.ai/zen/go/v1/messages"

# Requests per five hours shown by OpenCode Go on 2026-07-30. This is a
# subscription-capacity proxy, not per-token pricing.
MODELS = {
    "grok-4.5": {
        "endpoint": "chat", "requests_5h": 120,
        "input_price": 2.0, "output_price": 6.0, "cached_read_price": 0.3,
    },
    "kimi-k3": {
        "endpoint": "chat", "requests_5h": 110,
        "input_price": 3.0, "output_price": 15.0, "cached_read_price": 0.3,
    },
    "glm-5.2": {
        "endpoint": "chat", "requests_5h": 880,
        "input_price": 1.4, "output_price": 4.4, "cached_read_price": 0.26,
    },
    "qwen3.7-max": {
        "endpoint": "messages", "requests_5h": 950,
        "input_price": 2.5, "output_price": 7.5, "cached_read_price": 0.5,
        "cached_write_price": 3.125,
    },
    "kimi-k2.7-code": {
        "endpoint": "chat", "requests_5h": 1350,
        "input_price": 0.95, "output_price": 4.0, "cached_read_price": 0.19,
    },
    "minimax-m3": {
        "endpoint": "messages", "requests_5h": 3200,
        "input_price": 0.3, "output_price": 1.2, "cached_read_price": 0.06,
    },
    "mimo-v2.5-pro": {
        "endpoint": "chat", "requests_5h": 3250,
        "input_price": 0.435, "output_price": 0.87,
        "cached_read_price": 0.003625,
    },
    "deepseek-v4-pro": {
        "endpoint": "chat", "requests_5h": 3450,
        "input_price": 0.435, "output_price": 0.87,
        "cached_read_price": 0.003625,
    },
    "qwen3.7-plus": {
        "endpoint": "messages", "requests_5h": 4300,
        "input_price": 0.4, "output_price": 1.6, "cached_read_price": 0.04,
        "cached_write_price": 0.5,
    },
    "hy3": {
        "endpoint": "chat", "requests_5h": 4300,
        "input_price": 0.14, "output_price": 0.58, "cached_read_price": 0.035,
    },
    "mimo-v2.5": {
        "endpoint": "chat", "requests_5h": 30100,
        "input_price": 0.14, "output_price": 0.28,
        "cached_read_price": 0.0028,
    },
    "deepseek-v4-flash": {
        "endpoint": "chat", "requests_5h": 31650,
        "input_price": 0.14, "output_price": 0.28,
        "cached_read_price": 0.0028,
    },
}

GEMINI_3_FLASH_PRICING = {
    "input_price": 0.5,
    "output_price": 3.0,
    "cached_read_price": 0.05,
}


def _read_key(path):
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit("OpenCode Go API key file is empty")
    if value.startswith("[") or " " in value:
        raise SystemExit("OpenCode Go API key file contains a concealed placeholder")
    return value


def _post(url, body, key, headers=None):
    request_headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "Sana-Turnos-OpenCode-Go-Benchmark/1.0",
    }
    request_headers.update(headers or {})
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(
            url,
            data=encoded,
            headers=request_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            transient = (
                exc.code == 400
                and "Upstream request failed" in detail
                and attempt < 2
            )
            if not transient:
                raise
            time.sleep(1.5 * (2 ** attempt))
    raise AssertionError("unreachable")


def _anthropic_messages(messages):
    system = []
    converted = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system.append(str(message.get("content") or ""))
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id") or "",
                "content": str(message.get("content") or ""),
            }
            if converted and converted[-1]["role"] == "user" and isinstance(
                    converted[-1]["content"], list):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        if role == "assistant" and message.get("tool_calls"):
            content = []
            if message.get("content"):
                content.append({"type": "text", "text": str(message["content"])})
            for call in message["tool_calls"]:
                function = call.get("function") or {}
                try:
                    inputs = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    inputs = {}
                content.append({
                    "type": "tool_use",
                    "id": call.get("id") or "",
                    "name": function.get("name") or "",
                    "input": inputs,
                })
            converted.append({"role": "assistant", "content": content})
            continue
        converted.append({
            "role": "assistant" if role == "assistant" else "user",
            "content": str(message.get("content") or ""),
        })
    return "\n\n".join(system), converted


def _from_anthropic(payload):
    texts = []
    calls = []
    for block in payload.get("content") or []:
        if block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            calls.append({
                "id": block.get("id") or "",
                "type": "function",
                "function": {
                    "name": block.get("name") or "",
                    "arguments": json.dumps(
                        block.get("input") or {}, ensure_ascii=False),
                },
            })
    usage = payload.get("usage") or {}
    cached_read = usage.get("cache_read_input_tokens") or 0
    cached_write = usage.get("cache_creation_input_tokens") or 0
    uncached_input = usage.get("input_tokens") or 0
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "\n".join(texts).strip() or None,
                "tool_calls": calls or None,
            }
        }],
        "usage": {
            "prompt_tokens": uncached_input + cached_read + cached_write,
            "completion_tokens": usage.get("output_tokens") or 0,
            "prompt_tokens_details": {"cached_tokens": cached_read},
            "cache_write_tokens": cached_write,
        },
    }


def _call_provider(model, messages, tools, key, temperature=0.0, max_tokens=450):
    metadata = MODELS[model]
    if metadata["endpoint"] == "chat":
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return _post(CHAT_COMPLETIONS_URL, body, key)

    system, converted = _anthropic_messages(messages)
    body = {
        "model": model,
        "system": system,
        "messages": converted,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = [{
            "name": item["function"]["name"],
            "description": item["function"].get("description") or "",
            "input_schema": item["function"]["parameters"],
        } for item in tools]
        body["tool_choice"] = {"type": "auto"}
    payload = _post(
        MESSAGES_URL,
        body,
        key,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    return _from_anthropic(payload)


def _contains_any(text, values):
    folded = str(text or "").casefold()
    return any(str(value).casefold() in folded for value in values)


def _checks(case, answer):
    links = [item.get("href") for item in answer.get("enlaces") or []]
    sources = [item.get("url") for item in answer.get("fuentes") or []]
    text = answer.get("texto") or ""
    checks = {}
    if case.get("must_links"):
        checks["must_links"] = all(link in links for link in case["must_links"])
    if case.get("must_link_prefixes"):
        checks["must_link_prefixes"] = all(
            any(str(link or "").startswith(prefix) for link in links)
            for prefix in case["must_link_prefixes"]
        )
    if case.get("source_prefixes"):
        checks["source_prefixes"] = all(
            any(str(url or "").startswith(prefix) for url in sources)
            for prefix in case["source_prefixes"]
        )
    if case.get("required_text_groups"):
        checks["required_text_groups"] = all(
            _contains_any(text, group) for group in case["required_text_groups"]
        )
    if case.get("forbid_text"):
        checks["forbid_text"] = not _contains_any(text, case["forbid_text"])
    if "expect_login" in case:
        checks["expect_login"] = answer.get("login") is bool(case["expect_login"])
    return checks


def _usage_event(payload):
    usage = payload.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return {
        "prompt_tokens": int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        "completion_tokens": int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        "cached_read_tokens": int(
            details.get("cached_tokens")
            or usage.get("cache_read_input_tokens")
            or 0),
        "cached_write_tokens": int(
            usage.get("cache_write_tokens")
            or usage.get("cache_creation_input_tokens")
            or 0),
    }


def _summarize_usage(events, pricing):
    prompt_tokens = sum(item["prompt_tokens"] for item in events)
    completion_tokens = sum(item["completion_tokens"] for item in events)
    cached_read_tokens = min(
        prompt_tokens, sum(item["cached_read_tokens"] for item in events))
    cached_write_tokens = sum(
        item["cached_write_tokens"] for item in events)
    uncached_input_tokens = max(
        0, prompt_tokens - cached_read_tokens - cached_write_tokens)
    cost_usd = (
        uncached_input_tokens * pricing["input_price"]
        + cached_read_tokens * pricing.get(
            "cached_read_price", pricing["input_price"])
        + cached_write_tokens * pricing.get(
            "cached_write_price", pricing["input_price"])
        + completion_tokens * pricing["output_price"]
    ) / 1_000_000
    gemini_cost_usd = (
        prompt_tokens * GEMINI_3_FLASH_PRICING["input_price"]
        + completion_tokens * GEMINI_3_FLASH_PRICING["output_price"]
    ) / 1_000_000
    return {
        "provider_calls": len(events),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_read_tokens": cached_read_tokens,
        "cached_write_tokens": cached_write_tokens,
        "cost_cents": round(cost_usd * 100, 6),
        "gemini_3_flash_equivalent_cents": round(
            gemini_cost_usd * 100, 6),
    }


class MemoryHistory:
    def __init__(self):
        self.rows = {}

    def save(self, channel, key, message):
        self.rows.setdefault((channel, key), []).append(message)

    def load(self, channel, key, limit=20):
        return list(self.rows.get((channel, key), []))[-limit:]

    def export(self, channel, key):
        return list(self.rows.get((channel, key), []))

    def import_rows(self, channel, key, rows):
        self.rows[(channel, key)] = list(rows)


def _run_case(
        model, case, key, history, conversation_key, usage_events, pricing):
    started = time.monotonic()
    usage_start = len(usage_events)
    answer = chat_agent.chat_full(
        None, case["question"], clave=conversation_key, canal="opencode-eval")
    elapsed = time.monotonic() - started
    checks = _checks(case, answer)
    usage = _summarize_usage(usage_events[usage_start:], pricing)
    return {
        "id": case["id"],
        "question": case["question"],
        "answer": answer,
        "checks": checks,
        "auto_pass": all(checks.values()),
        "elapsed_seconds": round(elapsed, 3),
        "usage": usage,
        "history": history.export("opencode-eval", conversation_key),
    }


def _run_model(model, cases, key, imported_screen_history=None, max_tokens=450):
    history = MemoryHistory()
    usage_events = []
    chat_agent._save = history.save
    chat_agent._load_history = history.load
    chat_agent._turns_used = lambda *args, **kwargs: 0
    def benchmark_call(
            messages, tools, _model, _api_key, timeout=45, temperature=0.3):
        payload = _call_provider(
            model, messages, tools, key,
            temperature=0.0, max_tokens=max_tokens)
        usage_events.append(_usage_event(payload))
        return payload

    chat_agent._call_chat = benchmark_call
    ai_client.primary_key = lambda: "configured-for-benchmark"
    ai_client.backup_key = lambda: ""

    results = []
    for case in cases:
        conversation_key = f"{model}:{case['id']}"
        if case["phase"] == "followup":
            conversation_key = f"{model}:screen-flow"
            if imported_screen_history is not None and not history.export(
                    "opencode-eval", conversation_key):
                history.import_rows(
                    "opencode-eval", conversation_key, imported_screen_history)
        try:
            results.append(_run_case(
                model, case, key, history, conversation_key, usage_events,
                MODELS[model]))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            detail = getattr(exc, "code", None) or str(exc)
            results.append({
                "id": case["id"],
                "question": case["question"],
                "answer": {},
                "checks": {"request": False},
                "auto_pass": False,
                "elapsed_seconds": None,
                "error": f"{type(exc).__name__}: {detail}",
                "history": history.export("opencode-eval", conversation_key),
            })

    latencies = [
        item["elapsed_seconds"] for item in results
        if item.get("elapsed_seconds") is not None
    ]
    return {
        "model": model,
        "endpoint": MODELS[model]["endpoint"],
        "requests_5h": MODELS[model]["requests_5h"],
        "pricing_per_million": {
            key: value for key, value in MODELS[model].items()
            if key.endswith("_price")
        },
        "max_tokens": max_tokens,
        "output_cap_cents": round(
            max_tokens * MODELS[model]["output_price"] / 10_000, 6),
        "passed": sum(item["auto_pass"] for item in results),
        "total": len(results),
        "total_cost_cents": round(sum(
            item.get("usage", {}).get("cost_cents") or 0
            for item in results), 6),
        "gemini_3_flash_equivalent_cents": round(sum(
            item.get("usage", {}).get("gemini_3_flash_equivalent_cents") or 0
            for item in results), 6),
        "median_latency_seconds": (
            round(statistics.median(latencies), 3) if latencies else None
        ),
        "results": results,
    }


def _run_job(job):
    return _run_model(
        job["model"],
        job["cases"],
        job["key"],
        imported_screen_history=job["imported_screen_history"],
        max_tokens=job["max_tokens"],
    )


def _rank_screen(rows):
    eligible_latencies = [
        row["median_latency_seconds"] for row in rows
        if (
            row["median_latency_seconds"] is not None
            and row["passed"] == row["total"]
        )
    ]
    fastest = min(eligible_latencies) if eligible_latencies else 1
    maximum_capacity = max(row["requests_5h"] for row in rows)
    for row in rows:
        quality = row["passed"] / max(1, row["total"])
        latency = row["median_latency_seconds"]
        speed = fastest / latency if latency else 0
        capacity = math.log1p(row["requests_5h"]) / math.log1p(maximum_capacity)
        compatibility = 1 if row["endpoint"] == "chat" else 0.9
        # Quality is the gate and largest component. Among safe models, speed
        # and subscription capacity determine practical value.
        row["selection_score"] = round(
            0.5 * quality + 0.25 * speed + 0.2 * capacity
            + 0.05 * compatibility,
            4,
        )
    return sorted(
        rows,
        key=lambda item: (
            item["selection_score"],
            item["passed"],
            -(item["median_latency_seconds"] or 9999),
        ),
        reverse=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--phase", choices=("screen", "final"), required=True)
    parser.add_argument("--models", nargs="*", choices=tuple(MODELS))
    parser.add_argument("--screen-results")
    parser.add_argument("--max-tokens", type=int, default=450)
    parser.add_argument("--output-budget-cents", type=float)
    parser.add_argument("--technical-max-tokens", type=int, default=32768)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    key = _read_key(args.api_key_file)
    if args.output_budget_cents is not None and args.output_budget_cents <= 0:
        raise SystemExit("--output-budget-cents must be positive")
    if args.technical_max_tokens <= 0:
        raise SystemExit("--technical-max-tokens must be positive")
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    models = args.models or list(MODELS)

    if args.phase == "screen":
        cases = [case for case in questions if case["phase"] == "screen"]
        screen_payload = None
    else:
        cases = [case for case in questions if case["phase"] in {"final", "followup"}]
        if not args.screen_results:
            raise SystemExit("--screen-results is required for the final phase")
        screen_payload = json.loads(
            Path(args.screen_results).read_text(encoding="utf-8"))

    jobs = []
    for model in models:
        imported = None
        if screen_payload:
            screen_row = next(
                (item for item in screen_payload["results"]
                 if item["model"] == model),
                None,
            )
            if not screen_row:
                raise SystemExit(f"missing screen results for {model}")
            q1 = next(item for item in screen_row["results"] if item["id"] == "GO-01")
            imported = q1.get("history") or []
        max_tokens = args.max_tokens
        if args.output_budget_cents is not None:
            output_price = MODELS[model]["output_price"]
            budget_limit = math.floor(
                (args.output_budget_cents / 100) * 1_000_000 / output_price)
            max_tokens = min(args.technical_max_tokens, budget_limit)
        jobs.append({
            "model": model,
            "cases": cases,
            "key": key,
            "imported_screen_history": imported,
            "max_tokens": max_tokens,
        })

    rows = []
    if args.parallel and len(jobs) > 1:
        print(
            f"starting {len(jobs)} models in parallel for {args.phase}",
            flush=True,
        )
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=len(jobs)) as executor:
            future_models = {
                executor.submit(_run_job, job): job["model"] for job in jobs
            }
            for future in concurrent.futures.as_completed(future_models):
                model = future_models[future]
                row = future.result()
                print(
                    f"{model}: {row['passed']}/{row['total']} "
                    f"median={row['median_latency_seconds']}s",
                    flush=True,
                )
                rows.append(row)
    else:
        for job in jobs:
            model = job["model"]
            print(f"{model}: starting {args.phase}", flush=True)
            row = _run_job(job)
            print(
                f"{model}: {row['passed']}/{row['total']} "
                f"median={row['median_latency_seconds']}s",
                flush=True,
            )
            rows.append(row)

    if args.phase == "screen":
        rows = _rank_screen(rows)
    payload = {
        "date": "2026-07-30",
        "provider": "OpenCode Go",
        "phase": args.phase,
        "max_tokens": (
            args.max_tokens if args.output_budget_cents is None else None),
        "output_budget_cents": args.output_budget_cents,
        "technical_max_tokens": args.technical_max_tokens,
        "gemini_3_flash_pricing_per_million": GEMINI_3_FLASH_PRICING,
        "pricing_source": "https://opencode.ai/docs/go/",
        "sanitized": True,
        "results": rows,
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
