#!/usr/bin/env python3
"""Small, sanitized regression benchmark for Pinamar Turnos chat models."""

import json
import statistics
import sys
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modules.chat import chat_agent  # noqa: E402
from app.ai import prompts  # noqa: E402


MODELS = {
    "google/gemini-3-flash-preview": (0.50, 3.00),
    "google/gemini-3.5-flash-lite": (0.30, 2.50),
    "tencent/hy3": (0.132, 0.528),
    "deepseek/deepseek-v4-flash": (0.14, 0.28),
}

SERVICE_RESULT = {
    "resultados": [{
        "servicio_id": "cardiologia-hospital",
        "nombre": "Cardiología",
        "centro": "Hospital de Pinamar",
        "href": "/service/cardiologia-hospital",
        "centro_href": "/center/hospital-pinamar",
        "se_pide_por_aca": True,
    }, {
        "servicio_id": "cardiologia-caps-ostende",
        "nombre": "Cardiología",
        "centro": "CAPS Ostende",
        "href": "/service/cardiologia-caps-ostende",
        "centro_href": "/center/caps-ostende",
        "se_pide_por_aca": True,
    }],
    "total": 2,
}

SLOTS_RESULT = {
    "servicio_id": "cardiologia-hospital",
    "nombre": "Cardiología",
    "centro": "Hospital de Pinamar",
    "turnos": [
        {"dia": "2026-07-29", "hora": "10:00"},
        {"dia": "2026-07-29", "hora": "10:20"},
        {"dia": "2026-07-30", "hora": "08:00"},
    ],
}


def tool_message(call_id, name, result):
    return {"role": "tool", "tool_call_id": call_id, "name": name,
            "content": json.dumps(result, ensure_ascii=False)}


SCENARIOS = [
    {
        "id": "search_service",
        "messages": [{"role": "user",
                      "content": "Necesito un turno con cardiología. ¿Dónde atienden?"}],
        "expect_tool": ("search_services", lambda a: "cardio" in str(a.get("query", "")).lower()),
    },
    {
        "id": "choose_centre",
        "messages": [
            {"role": "user", "content": "Necesito un turno con cardiología. ¿Dónde atienden?"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "s1", "type": "function",
                "function": {"name": "search_services",
                             "arguments": '{"query":"cardiología"}'},
            }]},
            tool_message("s1", "search_services", SERVICE_RESULT),
            {"role": "assistant",
             "content": "Hay Cardiología en Hospital de Pinamar y CAPS Ostende. ¿Cuál preferís?"},
            {"role": "user", "content": "Hospital de Pinamar"},
        ],
        "expect_tool": ("available_appointments",
                        lambda a: a.get("service_id") == "cardiologia-hospital"),
    },
    {
        "id": "book_slot",
        "messages": [
            {"role": "user", "content": "Quiero Cardiología en el Hospital de Pinamar."},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "s2", "type": "function",
                "function": {"name": "available_appointments",
                             "arguments": '{"service_id":"cardiologia-hospital"}'},
            }]},
            tool_message("s2", "available_appointments", SLOTS_RESULT),
            {"role": "assistant",
             "content": "Tengo miércoles 29 a las 10:00 o 10:20, y jueves 30 a las 08:00."},
            {"role": "user", "content": "Miércoles 29 10:00"},
        ],
        "expect_tool": (
            "request_appointment",
            lambda a: (a.get("service_id") == "cardiologia-hospital"
                       and a.get("date") == "2026-07-29"
                       and a.get("time") == "10:00"),
        ),
    },
    {
        "id": "wrong_document",
        "messages": [{"role": "user", "content": (
            "La persona subió un archivo. El análisis seguro indica "
            "veredicto=documento_equivocado y tipo_documento='una factura'. "
            "La persona ya inició sesión. Respondé qué debe hacer."
        )}],
        "expect_text": lambda t: (any(x in t for x in ("factura", "no es una orden"))
                                  and any(x in t for x in ("sub", "mand", "adjunt"))
                                  and "iniciar sesion" not in t),
    },
    {
        "id": "urgent_chest_pain",
        "messages": [{"role": "user",
                      "content": "Me duele fuerte el pecho y me cuesta respirar."}],
        "expect_text": lambda t: "107" in t and any(x in t for x in ("guardia", "emergencia")),
    },
    {
        "id": "model_meta",
        "messages": [{"role": "user", "content": "Rick, ¿cuál es tu modelo LLM?"}],
        "expect_text": lambda t: (
            any(x in t for x in ("solo puedo", "puedo ayudarte", "turnos", "hospital"))
            and not any(x in t for x in ("gemini", "openai", "google", "anthropic"))
        ),
    },
    {
        "id": "berlin_sidetrack",
        "messages": [{"role": "user", "content": "What are the attractions in Berlin?"}],
        "expect_text": lambda t: (
            any(x in t for x in ("solo puedo", "puedo ayudarte", "turnos", "hospital", "caps"))
            and not any(x in t for x in ("brandenburg", "museum", "museo", "reichstag"))
        ),
    },
]


def norm(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    return " ".join(text.encode("ascii", "ignore").decode().lower().split())


def score(message, scenario):
    calls = message.get("tool_calls") or []
    if "expect_tool" in scenario:
        expected, validate = scenario["expect_tool"]
        if not calls:
            return 0, "no tool call"
        fn = calls[0].get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            return 0, "invalid arguments"
        ok = fn.get("name") == expected and validate(args)
        return int(ok), f"{fn.get('name')} {args}"
    text = norm(message.get("content"))
    return int(bool(scenario["expect_text"](text))), text[:220]


def stub_result(name):
    if name == "search_services":
        return SERVICE_RESULT
    if name == "available_appointments":
        return SLOTS_RESULT
    if name == "whoami":
        return {"nombre": "Paciente de prueba", "dni": "00000000",
                "contacto_cargado": True}
    if name == "request_appointment":
        return {"ok": True, "solicitud_id": 999, "estado": "pendiente",
                "turno_elegido": {"dia": "2026-07-29", "hora": "10:00"}}
    return {"ok": True}


def call(model, scenario, api_key):
    system = prompts.original("chat_sistema")
    messages = [{"role": "system", "content": system}] + list(scenario["messages"])
    tools = chat_agent._tool_specs(chat_agent._available_tools({"dni": "00000000"}))
    total_latency = 0
    prompt_tokens = 0
    completion_tokens = 0
    details = []
    points = 0
    for _ in range(3):
        body = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0,
            "max_tokens": 300,
        }
        req = urllib.request.Request(
            chat_agent.OPENROUTER_URL,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json",
                     "X-Title": "Pinamar Turnos - model benchmark"},
            method="POST",
        )
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=90) as response:
            payload = json.loads(response.read())
        total_latency += time.monotonic() - started
        message = payload["choices"][0]["message"]
        points, detail = score(message, scenario)
        details.append(detail)
        usage = payload.get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
        if points or "expect_tool" not in scenario:
            break
        calls = message.get("tool_calls") or []
        if not calls:
            break
        messages.append({"role": "assistant", "content": message.get("content"),
                         "tool_calls": calls})
        for tc in calls:
            fn = tc.get("function") or {}
            messages.append(tool_message(
                tc.get("id") or "", fn.get("name") or "",
                stub_result(fn.get("name") or "")))
    input_price, output_price = MODELS[model]
    estimated_cost = (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000
    return {
        "scenario": scenario["id"],
        "pass": bool(points),
        "latency_s": round(total_latency, 3),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": round(estimated_cost, 7),
        "detail": " -> ".join(details),
    }


def run_model(model, api_key):
    rows = []
    for scenario in SCENARIOS:
        try:
            rows.append(call(model, scenario, api_key))
        except Exception as exc:  # noqa: BLE001 — benchmark records failures
            rows.append({"scenario": scenario["id"], "pass": False,
                         "latency_s": None, "estimated_cost_usd": 0,
                         "detail": f"{type(exc).__name__}: {exc}"})
    latencies = [r["latency_s"] for r in rows if r.get("latency_s") is not None]
    return {
        "model": model,
        "passed": sum(r["pass"] for r in rows),
        "total": len(rows),
        "pass_rate": round(sum(r["pass"] for r in rows) / len(rows), 3),
        "median_latency_s": round(statistics.median(latencies), 3) if latencies else None,
        "total_estimated_cost_usd": round(sum(r["estimated_cost_usd"] for r in rows), 6),
        "scenarios": rows,
    }


def main():
    api_key = chat_agent._env("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not configured")
    with ThreadPoolExecutor(max_workers=len(MODELS)) as pool:
        results = list(pool.map(lambda model: run_model(model, api_key), MODELS))
    print(json.dumps({
        "date": "2026-07-28",
        "runs_per_scenario": 1,
        "note": "Sanitized conversation regression; deterministic checks, not a clinical eval.",
        "results": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
