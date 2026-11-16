"""Anonymous study policy and deterministic analysis. No AI or outbound I/O."""
import copy
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit

from app.core import config, i18n

ROLES = ("patient", "doctor", "admin")
OUTCOMES = ("ok", "doubt", "stuck", "skipped")
STATUSES = ("found", "in_progress", "fixed", "not_done", "dismissed")
KINDS = ("click", "nav", "dead_click", "scroll_stop", "visibility")
_DEFINITION = json.loads(Path(__file__).with_name("study.json").read_text(encoding="utf-8"))
# Paths are a closed vocabulary. Unknown/dynamic segments cannot identify people.
STATIC_PATHS = frozenset(("/", "/doctors", "/centers", "/account", "/upload-order",
    "/my-requests", "/d/diagnostico", "/d/grupos", "/admin", "/admin/solicitudes",
    "/admin/cola", "/admin/my-patients", "/admin/mis-turnos", "/admin/my-schedules",
    "/admin/onboarding", "/admin/account", "/admin/pacientes", "/admin/services",
    "/admin/centers", "/admin/doctors", "/admin/users", "/admin/demo-access",
    "/admin/demo-doctor-access", "/account/demo-access", "/admin/login"))
DYNAMIC_PREFIXES = ("/admin/patients/", "/doctor/", "/center/", "/service/", "/preparation/")


def enabled():
    # Fail closed on the real installation even if the switch is set by mistake.
    return (config.get("TURNOS_STUDY", "0") == "1"
            and config.get("TURNOS_DEMO_MODE", "0") == "1")


def retention_days():
    try:
        return max(1, int(config.get("TURNOS_STUDY_RETENTION_DAYS", "90")))
    except (ValueError, TypeError):
        return 90


def definition(locale=None):
    lang = locale or i18n.active()
    data = copy.deepcopy(_DEFINITION)
    for role, group in data["roles"].items():
        group["label"] = i18n.t_or("study.role." + role, group["label"], lang)
        for task in group["tasks"]:
            for field in ("title", "instruction", "success_hint"):
                task[field] = i18n.t_or(f"study.task.{task['id']}.{field}", task[field], lang)
    for question in data["final_questions"]:
        question["title"] = i18n.t_or("study.final." + question["id"], question["title"], lang)
    return data


def task_ids(role, final=False):
    if role not in ROLES:
        raise ValueError("role")
    ids = {t["id"] for t in _DEFINITION["roles"][role]["tasks"]}
    return ids | {"F1", "F2", "F3"} if final else ids


def safe_path(value):
    try:
        path = urlsplit(str(value or "/")).path
    except ValueError:
        return "/other"
    if path in STATIC_PATHS:
        return path
    for prefix in DYNAMIC_PREFIXES:
        if path.startswith(prefix) and path[len(prefix):] and "/" not in path[len(prefix):]:
            return prefix + ":item"
    return "/other"


def integer(value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("number")
    return value


def safe_comment(value):
    if not isinstance(value, str):
        raise ValueError("text")
    text = value[:2000]
    # No reversible map is retained. Conservative filters also catch dotted DNI.
    text = re.sub(r"[\w.+-]+@[\w.-]+", "[dato omitido]", text)
    text = re.sub(r"\+?\d[\d\s()./-]{5,}\d", "[dato omitido]", text)
    text = re.sub(r"\b(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+){1,3}[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b",
                  "[dato omitido]", text)
    text = re.sub(r"(?i)\b(?:me llamo|mi nombre es|soy el paciente|soy la paciente)\s+[^,.!\n]+",
                  "[dato omitido]", text)
    return text


def sanitize_event(payload, role):
    if not isinstance(payload, dict) or payload.get("task_id") not in task_ids(role):
        raise ValueError("task")
    if payload.get("kind") not in KINDS:
        raise ValueError("kind")
    target = payload.get("target") or {}
    if not isinstance(target, dict):
        raise ValueError("target")
    tag = str(target.get("tag", "")).lower()
    if tag not in ("a", "button", "input", "select", "textarea", "label", "div",
                   "span", "p", "li", "td", "th", "img", "summary", "h1", "h2", "body"):
        tag = "other"
    return {"task_id": payload["task_id"], "kind": payload["kind"],
            "t": integer(payload.get("t"), 0, 86400000),
            "path": safe_path(payload.get("path")),
            "target": {"tag": tag, "interactive": target.get("interactive") is True,
                       "index": integer(target.get("index", 0), 0, 100000)}}


def sanitize_result(payload, role):
    if not isinstance(payload, dict) or payload.get("task_id") not in task_ids(role):
        raise ValueError("task")
    if payload.get("outcome") not in OUTCOMES:
        raise ValueError("outcome")
    ease = payload.get("ease")
    if ease is not None:
        ease = integer(ease, 1, 5)
    return {"task_id": payload["task_id"], "outcome": payload["outcome"], "ease": ease,
            "comment": safe_comment(payload.get("comment", "")),
            "duration_ms": integer(payload.get("duration_ms", 0), 0, 86400000)}


def user_agent_class(value):
    value = str(value or "").lower()
    if "ipad" in value or "tablet" in value:
        return "tablet"
    return "mobile" if "mobile" in value or "android" in value else "desktop"


def analyze(results, events, sessions=None, as_of=None):
    """Candidate evidence, never a measured product outcome or automatic decision.

    Group by task/kind. Count participants rather than event volume. Durations
    exclude skipped tasks. An unfinished task needs an event proving it began.
    """
    by_task = defaultdict(list)
    streams = defaultdict(list)
    for row in results:
        if row.get("task_id", "").startswith(("P", "D", "A")):
            by_task[row["task_id"]].append(row)
    for event in events:
        streams[(event["task_id"], event["session_id"])].append(event)
    completed = {(r["task_id"], r["session_id"]) for r in results if r["outcome"] != "skipped"}
    session_map = {s["id"]: s for s in sessions or []}
    candidates = []

    def add(task, kind, count, total, detail, severity="medium", **metrics):
        role = {"P": "patient", "D": "doctor", "A": "admin"}[task[0]]
        candidates.append({"task_id": task, "role": role, "kind": kind, "severity": severity,
            "title": f"{task}: {kind.replace('_', ' ')}", "detail": detail,
            "evidence_summary": {"participants": count, "total": total, "detail": detail, **metrics}})

    for task in sorted(set(by_task) | {k[0] for k in streams}):
        rows = by_task[task]
        answered = [r for r in rows if r["outcome"] != "skipped"]
        difficult = sum(r["outcome"] in ("stuck", "doubt") for r in answered)
        if difficult:
            add(task, "difficulty", difficult, len(answered), f"{difficult}/{len(answered)} stuck or doubt", "high")
        low = sum(r.get("ease") is not None and r["ease"] <= 2 for r in answered)
        if low:
            add(task, "low_ease", low, len(answered), f"{low}/{len(answered)} ease at most 2/5")
        durations = [r["duration_ms"] for r in answered]
        threshold = next(t["threshold_ms"] for g in _DEFINITION["roles"].values()
                         for t in g["tasks"] if t["id"] == task)
        if durations and median(durations) > threshold:
            add(task, "duration", len(durations), len(durations),
                f"Median {int(median(durations))} ms exceeds {threshold} ms",
                median_ms=int(median(durations)), threshold_ms=threshold)
        dead, back, abandoned = set(), set(), set()
        task_streams = [(sid, stream) for (tid, sid), stream in streams.items() if tid == task]
        for sid, stream in task_streams:
            ordered = sorted(stream, key=lambda e: e["t"])
            session = session_map.get(sid)
            last_seen = max((e["recorded_at"] for e in ordered if e.get("recorded_at")), default=None)
            inactive = (as_of is not None and last_seen is not None
                        and (as_of - last_seen).total_seconds() >= 1800)
            # Active participants are not abandoned. Offline callers may
            # supply an already-closed snapshot without session metadata.
            abandoned_session = sessions is None or bool(session and session.get("finished_at")) or inactive
            if (task, sid) not in completed and abandoned_session:
                abandoned.add(sid)
            for event in ordered:
                if event["kind"] == "dead_click":
                    dead.add(sid)
                elif (event["kind"] == "click" and not (event.get("target") or {}).get("interactive")
                      and ordered[-1]["t"] >= event["t"] + 1500
                      and not any(e["kind"] == "nav" and event["t"] < e["t"] <= event["t"] + 1500 for e in ordered)):
                    dead.add(sid)
            paths = [e["path"] for e in ordered if e["kind"] == "nav"]
            paths = [p for i, p in enumerate(paths) if i == 0 or p != paths[i - 1]]
            reversals = sum(paths[i] == paths[i - 2] for i in range(2, len(paths)))
            if reversals >= 2:
                back.add(sid)
        for kind, people in (("dead_click", dead), ("backtrack", back), ("abandoned", abandoned)):
            if people:
                add(task, kind, len(people), len(task_streams), f"{len(people)}/{len(task_streams)} participants")
    return candidates
