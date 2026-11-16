#!/usr/bin/env python3
"""Offline conformance checks for Human Rounds inference profiles.

This intentionally tests transport routing and caller payload compatibility,
not provider behaviour or model quality.  It never contacts a provider: the
shared AI transport is replaced with a deterministic in-process response and
all socket construction is denied for the duration of each profile run.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
import socket
import stat
import sys
import time
from unittest import mock
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CORPUS_RELATIVE = Path("packs/example-town/ordenes-demo")
RESPONSE_RELATIVE = CORPUS_RELATIVE / "offline-profile-responses.json"
PROFILE_NAMES = ("cloud_shared", "hosted_jurisdiction", "self_hosted_vllm")
PROFILE_CONFIGS = {
    "cloud_shared": {
        "AI_INFERENCE_PROFILE": "cloud_shared",
        "AI_INFERENCE_BASE_URL": "",
        "AI_INFERENCE_API_KEY": "offline-placeholder-key",
    },
    "hosted_jurisdiction": {
        "AI_INFERENCE_PROFILE": "hosted_jurisdiction",
        "AI_INFERENCE_BASE_URL": "https://offline.invalid/v1/chat/completions",
        "AI_INFERENCE_API_KEY": "offline-placeholder-key",
    },
    "self_hosted_vllm": {
        "AI_INFERENCE_PROFILE": "self_hosted_vllm",
        "AI_INFERENCE_BASE_URL": "http://127.0.0.1:9/v1/chat/completions",
        "AI_INFERENCE_API_KEY": "offline-placeholder-key",
    },
}
PROFILE_DESTINATIONS = {
    "cloud_shared": "https://openrouter.ai/api/v1/chat/completions",
    "hosted_jurisdiction": "https://offline.invalid/v1/chat/completions",
    "self_hosted_vllm": "http://127.0.0.1:9/v1/chat/completions",
}
PLACEHOLDER_KEY = "offline-placeholder-key"
ENV_KEYS = tuple(sorted({
    "AI_INFERENCE_PROFILE", "AI_INFERENCE_BASE_URL", "AI_INFERENCE_API_KEY",
    "AI_INFERENCE_API_KEY_BACKUP", "OPENROUTER_API_KEY",
    "OPENROUTER_API_KEY_BACKUP", "TURNOS_FAKE_AI", "TURNOS_FAKE_TRANSLATE",
    "TURNOS_FAKE_CHAT_AI", "TURNOS_AI_MODEL", "TURNOS_AI_MODEL_FALLBACK",
    "TURNOS_CONFIG_LOCK", "TURNOS_ENV_FILE",
}))

_REFERRAL_RESPONSE_KEYS = frozenset({
    "paciente", "dni", "especialidad_solicitada", "diagnostico", "practicas",
    "medico_firmante", "matricula", "fecha", "legible", "es_orden_medica",
    "tipo_documento", "jurisdiccion_valida", "jurisdiccion_detectada",
})


class CorpusError(RuntimeError):
    """The public synthetic corpus cannot be safely used for this run."""


class SocketDenied(RuntimeError):
    """A benchmark workload attempted network egress."""


def _json_object(path: Path, label: str) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"{label} is unreadable") from exc
    if not isinstance(payload, dict):
        raise CorpusError(f"{label} must be an object")
    return payload


def _safe_asset_name(value: Any) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise CorpusError("asset path is invalid")
    if not value.endswith(".jpg"):
        raise CorpusError("asset must be a JPEG")
    return value


def load_verified_corpus(corpus_root: Path) -> List[Dict[str, Any]]:
    """Read and validate manifest/golden before opening any image bytes.

    The checks deliberately do not fall back to a directory listing or a seed
    script. A missing or mismatched manifest/golden pair is unsafe input.
    """
    corpus_root = Path(corpus_root)
    manifest = _json_object(corpus_root / "assets.json", "asset manifest")
    golden = _json_object(corpus_root / "golden.json", "golden file")
    assets, cases = manifest.get("assets"), golden.get("cases")
    if manifest.get("manifest_version") != "1.0" or golden.get("schema_version") != "1.0":
        raise CorpusError("corpus schema version is unsupported")
    if not isinstance(assets, list) or not isinstance(cases, list) or len(assets) != 15 or len(cases) != 15:
        raise CorpusError("corpus must declare exactly fifteen assets and cases")

    asset_by_name: Dict[str, Dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise CorpusError("asset entry is invalid")
        name = _safe_asset_name(asset.get("path"))
        if name in asset_by_name:
            raise CorpusError("asset names must be unique")
        digest, byte_count = asset.get("sha256"), asset.get("bytes")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)):
            raise CorpusError("asset digest is invalid")
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count <= 0:
            raise CorpusError("asset byte count is invalid")
        if asset.get("media_type") != "image/jpeg":
            raise CorpusError("asset media type is invalid")
        if asset.get("provenance") != {
            "classification": "synthetic", "source": "from_scratch",
            "from_scratch": True, "no_real": True, "no_derived": True,
        }:
            raise CorpusError("asset provenance is invalid")
        review = asset.get("review")
        if not isinstance(review, dict) or review.get("attested") is not True:
            raise CorpusError("asset review is invalid")
        asset_by_name[name] = asset

    case_by_name: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            raise CorpusError("golden case is invalid")
        name = _safe_asset_name(case.get("path"))
        if name in case_by_name:
            raise CorpusError("golden case names must be unique")
        if case.get("expected_verdict") not in {"parece_correcta", "dudosa", "documento_equivocado"}:
            raise CorpusError("golden verdict is unsupported")
        if case.get("document_kind") not in {"medical_referral", "non_medical"}:
            raise CorpusError("golden document kind is invalid")
        if case.get("jurisdiction") not in {"local", "foreign", "not_applicable"}:
            raise CorpusError("golden jurisdiction is invalid")
        expected_fields = case.get("expected_fields")
        if not isinstance(expected_fields, list) or any(not isinstance(field, str) for field in expected_fields):
            raise CorpusError("golden expected fields are invalid")
        case_by_name[name] = case

    if set(asset_by_name) != set(case_by_name):
        raise CorpusError("manifest and golden file do not close over the same assets")

    verified: List[Dict[str, Any]] = []
    for name in sorted(asset_by_name):
        path = corpus_root / name
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise CorpusError("asset is missing") from exc
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise CorpusError("asset is not a regular file")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise CorpusError("asset cannot be read") from exc
        asset = asset_by_name[name]
        if (not payload.startswith(b"\xff\xd8\xff")
                or len(payload) != asset["bytes"]
                or hashlib.sha256(payload).hexdigest() != asset["sha256"]):
            raise CorpusError("asset bytes do not match manifest")
        verified.append({"path": path, "case": copy.deepcopy(case_by_name[name])})
    return verified


def load_responses(path: Path) -> Dict[str, Any]:
    payload = _json_object(Path(path), "offline response fixture")
    needed = {"referral_templates", "chat_tool_turn", "translation", "dni_ocr"}
    if payload.get("schema_version") != "1.0" or not needed <= set(payload):
        raise CorpusError("offline response fixture is incomplete")
    templates = payload["referral_templates"]
    expected_templates = {"matching", "doubtful", "foreign", "wrong_document"}
    if not isinstance(templates, dict) or set(templates) != expected_templates:
        raise CorpusError("offline referral response templates are incomplete")
    for template in templates.values():
        if not isinstance(template, dict) or set(template) != _REFERRAL_RESPONSE_KEYS:
            raise CorpusError("offline referral response shape is invalid")
        if (not isinstance(template["practicas"], list)
                or any(not isinstance(item, str) for item in template["practicas"])
                or not isinstance(template["legible"], bool)
                or not isinstance(template["es_orden_medica"], bool)
                or not isinstance(template["jurisdiccion_valida"], bool)):
            raise CorpusError("offline referral response values are invalid")
        for field in ("paciente", "dni", "especialidad_solicitada", "diagnostico",
                      "medico_firmante", "matricula", "fecha", "tipo_documento",
                      "jurisdiccion_detectada"):
            if template[field] is not None and not isinstance(template[field], str):
                raise CorpusError("offline referral response value is invalid")

    tool_call = payload["chat_tool_turn"]
    if (not isinstance(tool_call, dict) or set(tool_call) != {"id", "type", "function"}
            or not isinstance(tool_call["id"], str) or not tool_call["id"]
            or tool_call["type"] != "function" or not isinstance(tool_call["function"], dict)
            or set(tool_call["function"]) != {"name", "arguments"}
            or tool_call["function"]["name"] != "search_services"
            or not isinstance(tool_call["function"]["arguments"], str)):
        raise CorpusError("offline chat tool response shape is invalid")
    try:
        arguments = json.loads(tool_call["function"]["arguments"])
    except json.JSONDecodeError as exc:
        raise CorpusError("offline chat tool arguments are invalid") from exc
    if (not isinstance(arguments, dict) or set(arguments) != {"query"}
            or not isinstance(arguments["query"], str) or not arguments["query"].strip()):
        raise CorpusError("offline chat tool arguments are invalid")

    if not isinstance(payload["translation"], str) or not payload["translation"].strip():
        raise CorpusError("offline translation response is invalid")
    dni = payload["dni_ocr"]
    required_dni = {"es_dni", "dni", "apellido", "nombres", "sexo",
                    "fecha_nacimiento", "foto", "legible"}
    if (not isinstance(dni, dict) or set(dni) != required_dni
            or dni["es_dni"] is not True or dni["legible"] is not True
            or not isinstance(dni["dni"], str) or not (7 <= len(dni["dni"]) <= 8)
            or not dni["dni"].isdigit()
            or not isinstance(dni["apellido"], str) or not dni["apellido"].strip()
            or not isinstance(dni["nombres"], str) or not dni["nombres"].strip()
            or dni["sexo"] not in {"M", "F", "X"}
            or not isinstance(dni["fecha_nacimiento"], str)):
        raise CorpusError("offline DNI response shape is invalid")
    bbox = dni["foto"]
    if (not isinstance(bbox, dict) or set(bbox) != {"x", "y", "w", "h"}
            or any(isinstance(bbox[key], bool) or not isinstance(bbox[key], (int, float))
                   for key in bbox)
            or not (0 <= bbox["x"] < 1 and 0 <= bbox["y"] < 1
                    and 0 < bbox["w"] <= 1 and 0 < bbox["h"] <= 1
                    and bbox["x"] + bbox["w"] <= 1
                    and bbox["y"] + bbox["h"] <= 1)):
        raise CorpusError("offline DNI portrait bounds are invalid")
    return payload


def _response_template(case: Mapping[str, Any], responses: Mapping[str, Any]) -> Dict[str, Any]:
    if case["expected_verdict"] == "documento_equivocado":
        key = "wrong_document"
    elif case["jurisdiction"] == "foreign":
        key = "foreign"
    elif case["expected_verdict"] == "dudosa":
        key = "doubtful"
    else:
        key = "matching"
    response = copy.deepcopy(responses["referral_templates"][key])
    expected_fields = set(case.get("expected_fields") or [])
    if {"national_license", "provincial_license"} <= expected_fields:
        response["matricula"] = "MN 12345 / MP 67890"
    return response


class _FakeTransport:
    """Shape-aware deterministic substitute for ``ai_client._post``."""

    def __init__(self, profile_name: str, responses: Mapping[str, Any]):
        if profile_name not in PROFILE_NAMES:
            raise ValueError("unsupported profile")
        self.profile_name = profile_name
        self.responses = responses
        self.current_case: Optional[Mapping[str, Any]] = None
        self.latencies_ms: List[float] = []
        self.payload_shapes: List[str] = []
        self.routes: List[tuple[str, str]] = []

    def _reply(self, content: Any) -> Dict[str, Any]:
        return {"choices": [{"message": {"content": content}}], "usage": {"cost": 0}}

    def post(self, body: Mapping[str, Any], api_key: str, timeout: float,
             headers_extra: Optional[Mapping[str, str]] = None,
             base_url: Optional[str] = None) -> Dict[str, Any]:
        del timeout, headers_extra
        started = time.perf_counter()
        try:
            self.routes.append((str(base_url or ""), str(api_key or "")))
            messages = body.get("messages")
            if body.get("response_format") == {"type": "json_object"}:
                content = messages[0]["content"] if isinstance(messages, list) and messages else None
                prompt = content[0].get("text", "") if isinstance(content, list) and content else ""
                if "lector de documentos de identidad" in prompt:
                    self._validate_image_request(body, content, "dni_ocr")
                    self.payload_shapes.append("dni_ocr")
                    return self._reply(json.dumps(self.responses["dni_ocr"], sort_keys=True))
                if self.current_case is None:
                    raise AssertionError("referral request had no active fixture")
                self._validate_image_request(body, content, "referral")
                self.payload_shapes.append("referral")
                return self._reply(json.dumps(_response_template(self.current_case, self.responses), sort_keys=True))
            if body.get("tools"):
                self._validate_chat_request(body)
                self.payload_shapes.append("chat_tool_turn")
                return {"choices": [{"message": {"content": None, "tool_calls": [copy.deepcopy(self.responses["chat_tool_turn"])]}}]}
            if isinstance(messages, list) and len(messages) == 2 and all(isinstance(item.get("content"), str) for item in messages):
                self._validate_translation_request(body)
                self.payload_shapes.append("translation")
                return self._reply(self.responses["translation"])
            raise AssertionError("unrecognised offline AI payload")
        finally:
            self.latencies_ms.append((time.perf_counter() - started) * 1000)

    @staticmethod
    def _validate_image_request(body: Mapping[str, Any], content: Any, category: str) -> None:
        if (not isinstance(content, list) or len(content) < 2
                or not isinstance(content[0], dict) or content[0].get("type") != "text"
                or not isinstance(content[0].get("text"), str) or not content[0]["text"]
                or not isinstance(content[1], dict) or content[1].get("type") != "image_url"
                or not isinstance(content[1].get("image_url"), dict)
                or not str(content[1]["image_url"].get("url") or "").startswith("data:image/")
                or body.get("response_format") != {"type": "json_object"}
                or "tools" in body):
            raise AssertionError(f"{category} request shape is invalid")

    @staticmethod
    def _validate_chat_request(body: Mapping[str, Any]) -> None:
        tools = body.get("tools")
        choice = body.get("tool_choice")
        if (not isinstance(body.get("messages"), list) or not body["messages"]
                or not isinstance(tools, list) or len(tools) != 1
                or (tools[0].get("function") or {}).get("name") != "search_services"
                or not isinstance(choice, dict)
                or (choice.get("function") or {}).get("name") != "search_services"
                or "response_format" in body):
            raise AssertionError("chat tool-turn request shape is invalid")

    @staticmethod
    def _validate_translation_request(body: Mapping[str, Any]) -> None:
        messages = body.get("messages")
        if (set(body) != {"model", "temperature", "messages"}
                or not isinstance(messages, list) or len(messages) != 2
                or [item.get("role") for item in messages] != ["system", "user"]
                or any(not isinstance(item.get("content"), str) or not item["content"]
                       for item in messages)):
            raise AssertionError("translation request shape is invalid")

    def routing_ok(self) -> bool:
        expected = (PROFILE_DESTINATIONS[self.profile_name], PLACEHOLDER_KEY)
        return bool(self.routes) and all(route == expected for route in self.routes)


@contextlib.contextmanager
def _deny_sockets() -> Iterator[None]:
    """Fail closed even if a future workload bypasses ``ai_client._post``."""
    original_socket = socket.socket
    original_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo

    def denied(*_args: Any, **_kwargs: Any) -> None:
        raise SocketDenied("offline benchmark forbids socket access")

    socket.socket = denied  # type: ignore[assignment]
    socket.create_connection = denied  # type: ignore[assignment]
    socket.getaddrinfo = denied  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[assignment]
        socket.create_connection = original_connection  # type: ignore[assignment]
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]


@contextlib.contextmanager
def _isolated_profile(profile_name: str, transport: _FakeTransport) -> Iterator[None]:
    """Install one profile and restore every mutated process-global on exit."""
    if profile_name not in PROFILE_CONFIGS:
        raise ValueError("unsupported profile")
    from app.ai import ai_client
    from app.core import config, theme

    old_env = {key: os.environ.get(key) for key in ENV_KEYS}
    old_post = ai_client._post
    old_env_path = config.ENV_PATH
    old_active = dict(ai_client._active)
    old_dotenv_cache = copy.deepcopy(config._dotenv_cache)
    old_db_cache = copy.deepcopy(config._db_cache)
    try:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(PROFILE_CONFIGS[profile_name])
        os.environ["TURNOS_AI_MODEL"] = "offline-conformance-model"
        os.environ["TURNOS_AI_MODEL_FALLBACK"] = "off"
        os.environ["TURNOS_CONFIG_LOCK"] = "1"
        os.environ["TURNOS_ENV_FILE"] = "/dev/null"
        config._reset_cache_para_tests()
        ai_client.reset_sticky_for_tests()
        ai_client._post = transport.post
        # The referral request's X-Title names the brand, and theme.current()
        # reads the saved theme from the catalog whenever its ~3s cache is
        # cold. Pin the engine default so the run makes no DB read at all.
        with _deny_sockets(), mock.patch.object(theme, "current", return_value=theme.DEFAULT):
            yield
    finally:
        ai_client._post = old_post
        with ai_client._lock:
            ai_client._active.clear()
            ai_client._active.update(old_active)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config.ENV_PATH = old_env_path
        config._dotenv_cache.clear()
        config._dotenv_cache.update(old_dotenv_cache)
        config._db_cache.clear()
        config._db_cache.update(old_db_cache)


def _percentile(values: Sequence[float], quantile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 3)


def _expected_field_presence(result: Mapping[str, Any], fields: Iterable[str]) -> tuple[int, int]:
    campos = result.get("campos") if isinstance(result.get("campos"), dict) else {}

    def value(name: str) -> Any:
        field = campos.get(name)
        return field.get("valor") if isinstance(field, dict) else None

    matricula = str(value("matricula") or "")
    available = {
        "patient_name": bool(value("paciente")),
        "requested_service": bool(value("especialidad")),
        "requested_study": bool(value("practicas")),
        "requested_studies": bool(value("practicas")),
        "diagnosis": bool(value("diagnostico")),
        "prescriber": bool(value("medico")),
        "license": bool(matricula),
        "national_license": "MN" in matricula.upper(),
        "provincial_license": "MP" in matricula.upper(),
        "date": bool(value("fecha")),
    }
    expected = list(fields)
    if any(field not in available for field in expected):
        raise CorpusError("golden expected field is unsupported")
    return sum(int(available[field]) for field in expected), len(expected)


def _run_chat_workload(chat_agent: Any, chat_tools: Any) -> bool:
    """Run one real chat turn, including execution of ``search_services``."""
    executed: List[Dict[str, Any]] = []
    stored: List[Dict[str, Any]] = []

    def search_services(_user: Any, args: Mapping[str, Any], _shim: Any) -> Dict[str, Any]:
        executed.append(dict(args))
        return {
            "resultados": [{
                "nombre": "Cardiología", "centro": "Centro Modelo",
                "href": "/service/cardiologia-centro-modelo",
                "servicio_id": "cardiologia-centro-modelo",
            }],
            "puerta_de_entrada": {},
        }

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(chat_agent, "_load_history", return_value=[]))
        stack.enter_context(mock.patch.object(chat_agent, "_first_user_message", return_value=""))
        stack.enter_context(mock.patch.object(chat_agent, "_save", side_effect=lambda _c, _k, msg: stored.append(copy.deepcopy(msg))))
        stack.enter_context(mock.patch.object(chat_agent, "_turns_used", return_value=0))
        stack.enter_context(mock.patch.object(chat_agent, "_prompt_with_knowledge", return_value=("Offline chat conformance prompt", [])))
        stack.enter_context(mock.patch.object(chat_agent, "_memory_block", return_value=""))
        stack.enter_context(mock.patch.object(chat_agent, "_specialty_names", return_value=["Cardiología"]))
        stack.enter_context(mock.patch.object(chat_agent.ai_health, "available", return_value=True))
        stack.enter_context(mock.patch.object(chat_agent.ai_health, "report", return_value=False))
        stack.enter_context(mock.patch.dict(chat_tools.TOOL_FNS, {"search_services": search_services}))
        stack.enter_context(mock.patch.object(chat_tools, "enlaces_para", return_value=[]))
        result = chat_agent.chat_full(
            None, "Which doctor should I see for a heart check?",
            "offline-conformance", "web",
        )
    return (executed == [{"query": "Cardiología"}]
            and bool(stored) and isinstance(result.get("texto"), str)
            and "Cardiología" in result["texto"])


def _run_workloads(cases: Iterable[Mapping[str, Any]], responses: Mapping[str, Any],
                   transport: _FakeTransport) -> Dict[str, Any]:
    """Invoke the production callers once each, except one referral per case."""
    from app.ai import ai_referral
    from app.core import translate
    from app.cuentas import ocr_dni
    from app.modules.chat import chat_agent
    from app.modules.chat import chat_tools

    referral_total = 0
    referral_passed = 0
    expected_presence_total = 0
    expected_presence_passed = 0
    with (mock.patch.object(ai_referral, "_prompt", return_value="Offline referral conformance prompt"),
          mock.patch.object(ai_referral, "_report_ai", return_value=False)):
        for item in cases:
            case = item["case"]
            transport.current_case = case
            result = ai_referral.analyze_referral_3pass(
                str(item["path"]), "image/jpeg",
                {"dni": "30123456", "nombre": "Persona de Prueba", "apellido": "Referencia",
                 "especialidad": "Cardiología", "filename": item["path"].name},
            )
            referral_total += 1
            referral_passed += int(result.get("verdict") == case["expected_verdict"])
            present, total = _expected_field_presence(result, case.get("expected_fields") or [])
            expected_presence_passed += present
            expected_presence_total += total
    transport.current_case = None

    chat_ok = _run_chat_workload(chat_agent, chat_tools)

    translated = translate._call_openrouter("Cardiology", "en")
    translation_ok = translated == responses["translation"]

    with mock.patch.object(ai_referral, "_report_ai", return_value=False):
        dni = ocr_dni.leer_frente("data:image/jpeg;base64,/9j/2Q==", "image/jpeg")
    dni_ok = bool(dni.get("ok") and dni.get("patient", {}).get("dni") == "30123456")
    expected_shapes = {"referral", "chat_tool_turn", "translation", "dni_ocr"}
    return {
        "referral_passed": referral_passed,
        "referral_total": referral_total,
        "expected_field_presence_passed": expected_presence_passed,
        "expected_field_presence_total": expected_presence_total,
        "chat_tool_turn": chat_ok,
        "translation": translation_ok,
        "dni_ocr": dni_ok,
        "payload_shapes": sorted(set(transport.payload_shapes)),
        "payload_shapes_ok": expected_shapes <= set(transport.payload_shapes),
    }


def run_benchmark(corpus_root: Path, response_path: Path) -> List[Dict[str, Any]]:
    """Run exactly one offline conformance row for each supported profile."""
    cases = load_verified_corpus(corpus_root)
    responses = load_responses(response_path)
    rows: List[Dict[str, Any]] = []
    for profile_name in PROFILE_NAMES:
        transport = _FakeTransport(profile_name, responses)
        with _isolated_profile(profile_name, transport):
            workloads = _run_workloads(cases, responses, transport)
        call_count = len(transport.routes)
        routing_ok = transport.routing_ok()
        all_ok = (
            workloads["referral_passed"] == workloads["referral_total"]
            and workloads["expected_field_presence_passed"] == workloads["expected_field_presence_total"]
            and workloads["chat_tool_turn"] and workloads["translation"]
            and workloads["dni_ocr"] and workloads["payload_shapes_ok"]
            and call_count == 18 and routing_ok
        )
        rows.append({
            "profile": profile_name,
            "all_ok": all_ok,
            "referral_passed": workloads["referral_passed"],
            "referral_total": workloads["referral_total"],
            "expected_field_presence_passed": workloads["expected_field_presence_passed"],
            "expected_field_presence_total": workloads["expected_field_presence_total"],
            "chat_tool_turn": workloads["chat_tool_turn"],
            "translation": workloads["translation"],
            "dni_ocr": workloads["dni_ocr"],
            "routing_ok": routing_ok,
            "call_count": call_count,
            "local_p50_ms": _percentile(transport.latencies_ms, 0.50),
            "local_p95_ms": _percentile(transport.latencies_ms, 0.95),
            "provider_latency_ms": None,
            "provider_cost_usd": None,
        })
    return rows


def _render_table(rows: Sequence[Mapping[str, Any]]) -> str:
    headers = ("profile", "all_ok", "referrals", "expected_field_presence", "chat",
               "translation", "dni_ocr", "routing_ok", "call_count", "local_p50_ms",
               "local_p95_ms", "provider_latency_ms", "provider_cost_usd")
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(str(value) for value in (
            row["profile"], row["all_ok"], f"{row['referral_passed']}/{row['referral_total']}",
            f"{row['expected_field_presence_passed']}/{row['expected_field_presence_total']}",
            row["chat_tool_turn"], row["translation"], row["dni_ocr"], row["routing_ok"],
            row["call_count"], row["local_p50_ms"],
            row["local_p95_ms"], row["provider_latency_ms"], row["provider_cost_usd"],
        )))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None, stdout: Any = None, stderr: Any = None) -> int:
    parser = argparse.ArgumentParser(description="Offline AI-profile conformance benchmark")
    parser.add_argument("--corpus-root", type=Path, default=ROOT / CORPUS_RELATIVE)
    parser.add_argument("--responses", type=Path, default=ROOT / RESPONSE_RELATIVE)
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args(argv)
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        rows = run_benchmark(args.corpus_root, args.responses)
    except (CorpusError, SocketDenied, AssertionError, ValueError) as exc:
        print(f"offline conformance failed: {exc}", file=stderr)
        return 1
    if args.format == "json":
        json.dump(rows, stdout, sort_keys=True)
        stdout.write("\n")
    else:
        stdout.write(_render_table(rows) + "\n")
    return 0 if all(row["all_ok"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
