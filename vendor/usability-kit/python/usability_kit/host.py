"""Python host helpers — the exact mirror of the Node findings store
(src/store/index.js). Same state machine, same ``check|page|signature`` dedup
key, same stopwords (read from the shared src/store/stopwords.json so the two
implementations cannot drift), same idempotent ingest, same atomic writes.

test/fixtures/store-cases.json replays the same op sequences through both
implementations and both must reach the expected statuses.

Functions:
  load_store(dir) -> dict            the parsed store (empty shape when absent)
  store_json(dir) -> str             the store file's JSON text
  save_store(dir, store)             atomic temp+rename write
  evidence_path(dir, fnd_id, file)   absolute path or None (traversal-safe)
  enqueue_run(dir, requested_by, kind='preflight', now=None) -> run_id
  claim_run(dir, run_id) -> run or None
  finish_run(dir, run_id, summary, error, now=None) -> run or None
  mark_fixed(dir, id_or_key, note, now=None) -> finding or None
  set_rating(dir, id_or_key, rating) -> finding or None
  ingest(dir, payload, now=None)     {report | study, run_id, evidence: {path: base64}}
  ingest_preflight(store, report, opts) / ingest_study(store, results, opts)
  worst(store, n=5)                  ranked open/failing findings
  signature(text) / key_of(item)     the shared keyword signature
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "usability-kit/findings-store"
SEVERITIES = ("critical", "serious", "moderate", "minor")
SEV_ORDER = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
RATINGS = (0, 1, 2, 3)
FND_RE = re.compile(r"^fnd_[a-f0-9]{12}$")
RUN_RE = re.compile(r"^run_[a-f0-9]{12}$")
EVIDENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
PAGE_RE = re.compile(r"^/(?![/\\])")
LOCAL_PATH_RE = re.compile(r"^/(?![/\\])\S*$")

# Mirrors BUG_WORDS in src/store/index.js character for character.
BUG_WORDS = re.compile(
    r"\b(error|errors|broken|breaks|crash|crashes|fails|failed|fails to load|"
    r"did not load|does not load|does not work|doesn'?t work|not clickable|"
    r"overflow|cut off|clipped|blank|404|500|no carga|no funciona|no anda|"
    r"roto|se rompio)\b",
    re.IGNORECASE,
)

SEV_FROM_STUDY = {
    "blocker": "critical", "critical": "critical",
    "major": "serious", "serious": "serious",
    "minor": "moderate", "moderate": "moderate",
    "cosmetic": "minor",
}
SEV_FROM_PREFLIGHT = {
    "critical": "critical", "serious": "serious", "moderate": "moderate",
    "minor": "minor", "major": "serious", "blocker": "critical",
    "cosmetic": "minor",
}
OUTCOME = {
    "done": "success", "success": "success", "ok": "success",
    "giveup": "fail", "fail": "fail", "failed": "fail",
    "abandon": "abandon", "tool-failure": "abandon", "error": "abandon",
}

_STOPWORDS_PATH = Path(__file__).resolve().parents[2] / "src" / "store" / "stopwords.json"
with open(_STOPWORDS_PATH, encoding="utf-8") as _fh:
    _SW = json.load(_fh)
STOPWORDS = frozenset(_SW["en"] + _SW["es"])


# ---- small shared helpers -------------------------------------------------


def _now_iso(now=None) -> str:
    if isinstance(now, datetime):
        return _iso(now)
    if isinstance(now, str) and now:
        return _iso(_parse(now))
    return _iso(datetime.now(timezone.utc))


def _parse(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _clip(value, n):
    text = str("" if value is None else value).strip()[:n]
    return text or None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def deaccent(text) -> str:
    folded = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(c for c in folded if not unicodedata.combining(c))


def tokens(text):
    out = []
    for word in re.split(r"[^a-z0-9]+", deaccent(text)):
        if len(word) >= 2 and word not in STOPWORDS and word not in out:
            out.append(word)
    return out


def signature(text) -> str:
    return "-".join(sorted(tokens(text)))


def norm_page(p):
    s = str("" if p is None else p).strip().split("?")[0].split("#")[0]
    if not s or not PAGE_RE.match(s):
        return None
    return s


def key_of(item):
    page = item.get("page")
    if page is None:
        pages = item.get("pages") or []
        page = pages[0] if pages else None
    p = norm_page(page) or ""
    return f"{item.get('check') or ''}|{p}|{signature(item.get('title'))}"


# ---- store shape -----------------------------------------------------------


def empty_store(project=None) -> dict:
    return {
        "schema": SCHEMA, "version": 1, "project": project or None,
        "updated_at": None, "findings": [], "runs": [], "sessions": [],
    }


def _hydrate(s) -> dict:
    store = empty_store((s or {}).get("project"))
    if not isinstance(s, dict):
        return store
    store.update(s)
    store["schema"] = SCHEMA
    store["version"] = 1
    store["findings"] = s.get("findings") if isinstance(s.get("findings"), list) else []
    store["runs"] = s.get("runs") if isinstance(s.get("runs"), list) else []
    store["sessions"] = s.get("sessions") if isinstance(s.get("sessions"), list) else []
    return store


def load_store(dir) -> dict:
    path = os.path.join(str(dir), "store.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return _hydrate(json.load(fh))
    except FileNotFoundError:
        return empty_store()


def store_json(dir) -> str:
    try:
        with open(os.path.join(str(dir), "store.json"), encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return json.dumps(empty_store(), indent=2) + "\n"


def save_store(dir, store) -> dict:
    store["updated_at"] = _iso(datetime.now(timezone.utc))
    os.makedirs(str(dir), exist_ok=True)
    final = os.path.join(str(dir), "store.json")
    tmp = os.path.join(str(dir), f".store-{os.getpid()}-{secrets.token_hex(4)}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, final)
    return store


# ---- evidence --------------------------------------------------------------


def _evidence_name(slot: str, index: int, src_key) -> str:
    m = re.search(r"\.[a-z0-9]{2,5}$", str(src_key or ""), re.IGNORECASE)
    ext = ("." + m.group(0)[1:].lower()) if m else ".png"
    return f"{slot}{ext}" if index <= 1 else f"{slot}-{index}{ext}"


def write_evidence(dir, fnd_id: str, name: str, data: bytes) -> str:
    edir = os.path.join(str(dir), "evidence", fnd_id)
    os.makedirs(edir, exist_ok=True)
    final = os.path.join(edir, name)
    tmp = os.path.join(edir, f".{name}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, final)
    return f"evidence/{fnd_id}/{name}"


def evidence_path(dir, fnd_id, file):
    """Absolute path of an evidence file, or None when the request is unsafe."""
    fnd_id = str(fnd_id or "")
    file = str(file or "")
    if not FND_RE.match(fnd_id) or not EVIDENCE_RE.match(file):
        return None
    if ".." in file:
        return None
    root = os.path.realpath(os.path.join(str(dir), "evidence"))
    full = os.path.realpath(os.path.join(root, fnd_id, file))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full


def _occurrence_evidence(f, item, index, opts):
    out = {"before": None, "after": None}
    bytes_map = opts.get("evidence") or {}
    evidence = item.get("evidence") or {}
    if isinstance(evidence, str):
        evidence = {"before": evidence, "after": None}
    for slot in ("before", "after"):
        src = evidence.get(slot)
        if not src or src not in bytes_map:
            continue
        name = _evidence_name(slot, index, src)
        if opts.get("dir"):
            out[slot] = write_evidence(opts["dir"], f["id"], name, bytes_map[src])
        else:
            out[slot] = f"evidence/{f['id']}/{name}"
    return out


# ---- ingest ----------------------------------------------------------------


def _seen_transition(status: str) -> str:
    if status in ("fixed", "confirmed"):
        return "failing"
    if status == "failing":
        return "open"
    return "open"


def _item_pages(item):
    pages = []
    for p in item.get("pages") or []:
        n = norm_page(p)
        if n and n not in pages:
            pages.append(n)
    return pages


def _count_summary(items) -> dict:
    summary = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
    for i in items:
        summary[i["severity"]] += 1
    return summary


def _distinct_participants(store) -> int:
    return len({s["id"] for s in store["sessions"]})


def _refresh_participants(f):
    hit = {o["participant"] for o in f.get("occurrences") or [] if o.get("participant")}
    f["participants_hit"] = len(hit)


def _run_covered(run, f) -> bool:
    pages = f.get("pages") or []
    if not pages:
        return run["source"] == f.get("source")
    if run["kind"] == "preflight" and (f.get("viewports") or []):
        return any(f"{p}@{v}" in run["pairs"] for p in pages for v in f["viewports"])
    return any(p in run["pages"] for p in pages)


def _apply_run(store, run, opts):
    touched = []
    seen = set()

    existing = next((r for r in store["runs"] if r["id"] == run["id"]), None)
    if existing and existing.get("ingested_at"):
        return {"touched": [], "run": existing}

    for item in run["items"]:
        key = key_of(item)
        at = item.get("at") or run["at"]
        seen.add(key)
        f = next((x for x in store["findings"] if x["key"] == key), None)
        if f is None:
            f = {
                "id": _new_id("fnd"), "key": key,
                "kind": item["kind"], "scope": item["scope"], "source": run["source"],
                "check": item.get("check"), "severity": item["severity"],
                "title": item["title"], "detail": item.get("detail"),
                "pages": _item_pages(item), "viewports": list(item["viewports"]),
                "rating": None, "status": "open", "fixed_at": None, "fix_note": None,
                "participants_hit": 0, "participants_ran": 0,
                "suggested_fix": item.get("suggested_fix"),
                "first_seen": at, "last_seen": at, "occurrences": [],
            }
            store["findings"].append(f)
        else:
            f["status"] = _seen_transition(f["status"])
            if SEV_ORDER[item["severity"]] < SEV_ORDER[f["severity"]]:
                f["severity"] = item["severity"]
            if not f.get("detail") and item.get("detail"):
                f["detail"] = item["detail"]
            if not f.get("suggested_fix") and item.get("suggested_fix"):
                f["suggested_fix"] = item["suggested_fix"]
            for p in _item_pages(item):
                if p not in f["pages"]:
                    f["pages"].append(p)
            for v in item["viewports"]:
                if v not in f["viewports"]:
                    f["viewports"].append(v)
            if not f.get("last_seen") or at > f["last_seen"]:
                f["last_seen"] = at
        index = len(f["occurrences"]) + 1
        f["occurrences"].append({
            "run_id": run["id"], "at": at,
            "page": norm_page((item.get("pages") or [None])[0]),
            "viewport": item["viewports"][0] if item["viewports"] else None,
            "participant": item.get("participant"),
            "quote": item.get("quote"),
            "evidence": _occurrence_evidence(f, item, index, opts),
        })
        _refresh_participants(f)
        touched.append(f["id"])

    for f in store["findings"]:
        if f["key"] in seen:
            continue
        if not _run_covered(run, f):
            continue
        since = f.get("fixed_at") or f.get("last_seen") or f.get("first_seen")
        if since and run["at"] < since:
            continue
        if f["status"] != "confirmed":
            f["status"] = "confirmed"
        if not f.get("fixed_at"):
            f["fixed_at"] = run["at"]

    for ses in run["sessions"]:
        store["sessions"].append(ses)
    if run["sessions"]:
        ran = _distinct_participants(store)
        for f in store["findings"]:
            if any(o.get("participant") for o in f.get("occurrences") or []):
                f["participants_ran"] = ran

    if existing is None:
        record = {
            "id": run["id"], "kind": run["kind"], "status": "queued",
            "requested_by": run.get("requestedBy") or "ingest",
            "requested_at": run["at"], "started_at": run["at"], "finished_at": None,
            "base_url": run.get("baseUrl") or None, "summary": None, "error": None,
        }
        store["runs"].append(record)
    else:
        record = existing
    record["kind"] = record.get("kind") or run["kind"]
    record["base_url"] = record.get("base_url") or run.get("baseUrl") or None
    record["ingested_at"] = _now_iso(opts.get("now"))
    if record["status"] in ("queued", "running"):
        record["status"] = "done"
        record["finished_at"] = record.get("finished_at") or run["at"]
    if not record.get("summary"):
        record["summary"] = _count_summary(run["items"])

    store["updated_at"] = _now_iso(opts.get("now"))
    return {"touched": touched, "run": record}


def _normalize_viewports(v):
    raw = v if isinstance(v, list) else ([] if v is None else [v])
    out = []
    for n in raw:
        try:
            num = float(n)
        except (TypeError, ValueError):
            continue
        if num > 0 and num == int(num) and int(num) not in out:
            out.append(int(num))
    return out


def _coverage_from_report(report, items):
    pages = set()
    pairs = set()

    def add(p, v):
        n = norm_page(p)
        if not n:
            return
        pages.add(n)
        if v:
            pairs.add(f"{n}@{v}")

    viewports = _normalize_viewports(report.get("viewports"))
    raw = report.get("coverage") or report.get("pages") or []
    for entry in raw:
        if isinstance(entry, str):
            if viewports:
                for v in viewports:
                    add(entry, v)
            else:
                add(entry, None)
        elif isinstance(entry, dict):
            p = entry.get("page") or entry.get("path") or entry.get("url")
            vs = _normalize_viewports(entry.get("viewports") or entry.get("viewport"))
            if vs:
                for v in vs:
                    add(p, v)
            elif viewports:
                for v in viewports:
                    add(p, v)
            else:
                add(p, None)
    if not pages:
        for item in items:
            for p in _item_pages(item):
                for v in (item["viewports"] or [None]):
                    add(p, v)
    return pages, pairs


def ingest_preflight(store, report, opts=None):
    opts = opts or {}
    report = report or {}
    items = []
    for p in report.get("findings") or report.get("problems") or []:
        if not p or not p.get("title"):
            continue
        evidence = p.get("evidence")
        if isinstance(evidence, str):
            evidence = {"before": evidence, "after": None}
        pages = [p.get("page") or p.get("path") or (p.get("pages") or [None])[0]]
        items.append({
            "check": p.get("check"),
            "severity": SEV_FROM_PREFLIGHT.get(str(p.get("severity") or "").lower(), "minor"),
            "title": _clip(p["title"], 200),
            "detail": _clip(p.get("detail") or p.get("body"), 1000),
            "pages": [x for x in pages if x],
            "viewports": _normalize_viewports(p.get("viewport") or p.get("viewports")),
            "kind": "design" if p.get("kind") == "design" else "bug",
            "scope": "lab" if p.get("scope") == "lab" else "product",
            "suggested_fix": _clip(p.get("suggested_fix") or p.get("fix"), 500),
            "participant": None, "quote": None,
            "evidence": evidence or {},
        })
    at = _now_iso(report.get("at") or report.get("generated_at") or opts.get("now"))
    pages, pairs = _coverage_from_report(report, items)
    return _apply_run(store, {
        "id": opts.get("runId") or report.get("run_id") or _new_id("run"),
        "kind": "preflight", "source": "preflight", "at": at,
        "baseUrl": report.get("base_url"), "requestedBy": opts.get("requestedBy"),
        "items": items, "sessions": [], "pages": pages, "pairs": pairs,
    }, opts)


def ingest_study(store, study_results, opts=None):
    opts = opts or {}
    results = study_results if isinstance(study_results, list) else [study_results]
    items = []
    sessions = []
    pages = set()
    at0 = _now_iso(opts.get("now"))

    for result in results:
        if not isinstance(result, dict):
            continue
        ses_id = result.get("session_id") or _new_id("ses")
        at = _now_iso(result.get("at") or result.get("finished_at") or opts.get("now"))
        sessions.append({
            "id": ses_id, "run_id": opts.get("runId") or result.get("run_id"),
            "persona": _clip(result.get("persona") or result.get("participant"), 80) or "participant",
            "task": _clip(result.get("task") or result.get("case"), 200),
            "outcome": OUTCOME.get(str(result.get("outcome") or result.get("verdict") or "").lower(), "abandon"),
            "steps": _to_int(result.get("steps")),
            "at": at,
            "summary": _clip(result.get("answer") or result.get("summary") or result.get("note"), 300),
        })
        for p in (result.get("pages") or [result.get("path")]):
            n = norm_page(p)
            if n:
                pages.add(n)
        for f in result.get("findings") or []:
            if not f or not f.get("title"):
                continue
            if f.get("kind") in ("bug", "design"):
                kind = f["kind"]
            elif f.get("category") == "bug":
                kind = "bug"
            elif BUG_WORDS.search(str(f["title"])):
                kind = "bug"
            else:
                kind = "design"
            evidence = f.get("evidence")
            if isinstance(evidence, str):
                evidence = {"before": evidence, "after": None}
            items.append({
                "check": None,
                "severity": SEV_FROM_STUDY.get(str(f.get("severity") or "").lower(), "minor"),
                "title": _clip(f["title"], 200),
                "detail": _clip(f.get("detail"), 1000),
                "pages": [x for x in [f.get("page")] if x],
                "viewports": _normalize_viewports(f.get("viewport")),
                "kind": kind, "scope": "lab" if f.get("scope") == "lab" else "product",
                "suggested_fix": _clip(f.get("suggested_fix"), 500),
                "participant": ses_id,
                "quote": _clip(f.get("quote"), 300),
                "evidence": evidence or {},
                "at": at,
            })

    if not pages:
        for item in items:
            n = norm_page((item.get("pages") or [None])[0])
            if n:
                pages.add(n)
    return _apply_run(store, {
        "id": opts.get("runId") or _new_id("run"),
        "kind": "study", "source": "simulated", "at": at0,
        "baseUrl": opts.get("baseUrl"), "requestedBy": opts.get("requestedBy"),
        "items": items, "sessions": sessions, "pages": pages, "pairs": set(),
    }, opts)


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


# ---- disk-backed owner actions ---------------------------------------------


def _finding(store, id_or_key):
    for f in store["findings"]:
        if f["id"] == id_or_key or f["key"] == id_or_key:
            return f
    return None


def mark_fixed(dir, id_or_key, note, now=None):
    store = load_store(dir)
    f = _finding(store, id_or_key)
    if not f:
        return None
    f["status"] = "fixed"
    f["fix_note"] = _clip(note, 500)
    f["fixed_at"] = _now_iso(now)
    store["updated_at"] = f["fixed_at"]
    save_store(dir, store)
    return f


def set_rating(dir, id_or_key, rating):
    store = load_store(dir)
    f = _finding(store, id_or_key)
    if not f:
        return None
    if rating is None or rating == "":
        value = None
    else:
        value = int(rating)
        if value not in RATINGS:
            raise ValueError("bad_rating")
    f["rating"] = value
    store["updated_at"] = _now_iso()
    save_store(dir, store)
    return f


def enqueue_run(dir, requested_by, kind="preflight", now=None, base_url=None) -> str:
    store = load_store(dir)
    run = {
        "id": _new_id("run"), "kind": "study" if kind == "study" else "preflight",
        "status": "queued",
        "requested_by": _clip(requested_by, 120) or "panel",
        "requested_at": _now_iso(now), "started_at": None, "finished_at": None,
        "base_url": base_url, "summary": None, "error": None,
    }
    store["runs"].append(run)
    store["updated_at"] = run["requested_at"]
    save_store(dir, store)
    return run["id"]


def claim_run(dir, run_id, now=None):
    store = load_store(dir)
    run = next((r for r in store["runs"] if r["id"] == run_id), None)
    if not run:
        return None
    run["status"] = "running"
    run["started_at"] = run.get("started_at") or _now_iso(now)
    store["updated_at"] = run["started_at"]
    save_store(dir, store)
    return run


def finish_run(dir, run_id, summary, error, now=None):
    store = load_store(dir)
    run = next((r for r in store["runs"] if r["id"] == run_id), None)
    if not run:
        return None
    run["status"] = "failed" if error else "done"
    run["finished_at"] = _now_iso(now)
    run["error"] = _clip(error, 500) if error else None
    if summary:
        run["summary"] = summary
    store["updated_at"] = run["finished_at"]
    save_store(dir, store)
    return run


def ingest(dir, payload, now=None):
    """Mirror of the Node POST /api/ingest body: {report | study, run_id,
    evidence: {path: base64}} applied to the store on disk."""
    evidence = {}
    for k, v in (payload.get("evidence") or {}).items():
        if isinstance(v, str) and v:
            try:
                evidence[k] = base64.b64decode(v)
            except Exception:  # noqa: BLE001 - skip undecodable, like Node
                pass
    opts = {
        "runId": payload.get("run_id") or None,
        "dir": str(dir), "now": now, "evidence": evidence,
    }
    store = load_store(dir)
    if payload.get("study"):
        out = ingest_study(store, payload["study"], opts)
    elif payload.get("report"):
        out = ingest_preflight(store, payload["report"], opts)
    else:
        raise ValueError("report_or_study_required")
    save_store(dir, store)
    return out


# ---- ranking (mirror of src/store/rank.js) ----------------------------------


def _effective_rating(f) -> int:
    if f.get("rating") is not None:
        return f["rating"]
    return 3 - SEV_ORDER.get(f.get("severity"), 3)


def worst(store, n=5):
    count = 5 if n is None else n
    listed = [
        f for f in store.get("findings") or []
        if f.get("status") in ("open", "failing") and f.get("rating") != 0
    ]
    listed.sort(
        key=lambda f: (
            -_effective_rating(f),
            SEV_ORDER.get(f.get("severity"), 3),
            -(f.get("participants_hit") or 0),
            str(f.get("key")),
        )
    )
    return listed if count < 0 else listed[:count]
