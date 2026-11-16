"""analytics_api_router.py — read-only export of the anonymous analytics stream, and the admin's keys for it.

  GET    /api/analytics/events     NDJSON, one event per line     (Bearer export key)
  GET    /api/analytics/schedule   NDJSON, one schedule row per line (Bearer export key)
  GET    /api/admin/analytics/keys           the keys (never their value)   (admin)
  POST   /api/admin/analytics/keys           a new key; its value is shown once   (admin)
  DELETE /api/admin/analytics/keys           revoke one: ?id=N   (admin)

Both feeds are keyset-paged: `limit` (default 1000, at most 5000) rows come back and, when there may be
more, the header `X-Next-After` holds the cursor to pass back as `after`. `since` / `until` bound the
event time (events) or the snapshot day (schedule): `YYYY-MM-DD` or an ISO timestamp, `until` exclusive.
The rows carry the allowlisted fields of analytics_events.py / analytics_schedule.py only; the export
key is stored as a SHA-256 hash and a key that is unknown, revoked or over its rate limit gets a bare 401 / 429.
"""

import base64
import datetime
import hashlib
import json
import time

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.auth import turnstile
from app.core import api_body, db_stream, deps, rate_window
from app.core.http_json import gzip_json_response as _json_response
from app.modules.turnos import analytics_export, api_models

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
admin_router = APIRouter(dependencies=[
    Depends(deps.require_module_enabled("turnos")),
    Depends(deps.require_admin(solo_admin=True)),
])

_HITS = {}  # key -> [epoch, ...]; memory only
_RATE_PER_MIN = 60  # per key
_IP_RATE_PER_MIN = 300  # per address, before the key is checked
# Events younger than this are not served. An id is made when the event is cleaned and its row commits a
# moment later, so a newer id can be visible before an older one; a reader that moved its cursor past the
# newer one would never see the older. Holding back the last seconds closes that window.
_LAG_S = 10
MAX_LIMIT = 5000
_EVENT_FIELDS = ("id", "ts", "type", "route", "rid", "ref", "lang", "device", "jid", "pl", "q", "n",
                 "origin", "step", "service")
_SCHEDULE_FIELDS = ("snapshot", "day", "doctor", "center", "service", "hours", "slots_total", "slots_web",
                    "taken", "free")

_QUERY_GET = {
    "since": "Start, inclusive: YYYY-MM-DD or an ISO timestamp",
    "until": "End, exclusive: YYYY-MM-DD or an ISO timestamp",
    "type": "Events only: page_view, search or step",
    "limit": "Rows per page, 1 to 5000 (default 1000)",
    "after": "The cursor from the previous page's X-Next-After header",
}
_QUERY = api_body.query_docs(get=_QUERY_GET)

# The schedule has no event type: its query is the events' without `type`.
_SCHEDULE_QUERY = api_body.query_docs(get={k: v for k, v in _QUERY_GET.items() if k != "type"})


def _plain(row, fields):
    out = {}
    for f in fields:
        v = row.get(f)
        if isinstance(v, datetime.datetime):
            v = v.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif isinstance(v, datetime.date):
            v = v.isoformat()
        elif v is not None and f == "hours":
            v = float(v)
        out[f] = v
    return out


def _when(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        d = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _limit(text):
    try:
        return max(1, min(int(text), MAX_LIMIT))
    except (TypeError, ValueError):
        return 1000


def _bare(status):
    return Response(content=b"", status_code=status, media_type=None, headers={"Content-Length": "0"})


def _authorized(request):
    """None when the Bearer key is good; else the bare answer to give."""
    if not analytics_export.enabled():
        return _bare(404)
    # The address is counted BEFORE the key is looked up: a wrong key costs a database read, and without
    # this a script could guess keys as fast as the server answers. The key's own limit comes after.
    client = request.client
    ip = turnstile.client_ip_from(request.headers, client.host if client else "")
    who = hashlib.sha256(("ip" + str(ip or "?")).encode()).hexdigest()[:20]
    if not rate_window.allow(_HITS, who, _IP_RATE_PER_MIN, 60):
        return _bare(429)
    head = request.headers.get("authorization", "")
    token = head[7:].strip() if head.lower().startswith("bearer ") else ""
    if not db_stream.verify_key(token):
        return _bare(401)
    # keyed on a hash: the plaintext key never sits in process memory as a dict key
    if not rate_window.allow(_HITS, "k" + hashlib.sha256(token.encode()).hexdigest()[:20], _RATE_PER_MIN, 60):
        return _bare(429)
    return None


def _ndjson(rows, fields, next_after):
    body = "".join(json.dumps(_plain(r, fields), ensure_ascii=False, separators=(",", ":")) + "\n"
                   for r in rows).encode("utf-8")
    headers = {"Cache-Control": "no-store", "X-Rows": str(len(rows)), "Content-Length": str(len(body))}
    if next_after:
        headers["X-Next-After"] = next_after
    return Response(content=body, status_code=200, media_type="application/x-ndjson", headers=headers)


def _events(request):
    bad = _authorized(request)
    if bad is not None:
        return bad
    q = request.query_params
    limit = _limit(q.get("limit"))
    kind = q.get("type") or None
    if kind is not None and kind not in ("page_view", "search", "step"):
        return _bare(400)
    after = q.get("after") or None
    if after is not None and not (len(after) == 23 and all(c in "0123456789abcdef" for c in after)):
        return _bare(400)
    since, until = _when(q.get("since")), _when(q.get("until"))
    if False in (since, until):
        return _bare(400)
    settled = "%013x" % max(0, int(time.time() * 1000) - _LAG_S * 1000)  # the ms prefix of an id
    rows = db_stream.events_page(since, until, kind, after, limit, before_id=settled)
    return _ndjson(rows, _EVENT_FIELDS, rows[-1]["id"] if len(rows) == limit else None)


def _cursor(row):
    """The five key columns as a base64url JSON list: a doctor, center or service name may hold any character
    (a «|» included), so the cursor cannot be a joined string."""
    parts = [str(row[k]) for k in ("snapshot", "day", "doctor", "center", "service")]
    return base64.urlsafe_b64encode(json.dumps(parts, ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")


def _after(text):
    try:
        parts = json.loads(base64.urlsafe_b64decode((text or "") + "=" * (-len(text or "") % 4)).decode("utf-8"))
        if not isinstance(parts, list) or len(parts) != 5 or not all(isinstance(p, str) for p in parts):
            return None
        return [datetime.date.fromisoformat(parts[0]), datetime.date.fromisoformat(parts[1]), *parts[2:]]
    except (ValueError, TypeError):  # bad base64, bad UTF-8, bad JSON or a bad date
        return None


def _schedule(request):
    bad = _authorized(request)
    if bad is not None:
        return bad
    q = request.query_params
    limit = _limit(q.get("limit"))
    since, until = _when(q.get("since")), _when(q.get("until"))
    if False in (since, until):
        return _bare(400)
    after = _after(q.get("after"))
    if q.get("after") and after is None:
        return _bare(400)
    rows = db_stream.schedule_page(since.date() if since else None, until.date() if until else None, after, limit)
    return _ndjson(rows, _SCHEDULE_FIELDS, _cursor(rows[-1]) if len(rows) == limit else None)


@router.get("/api/analytics/events", openapi_extra=_QUERY)
async def analytics_events(request: Request):
    return await run_in_threadpool(_events, request)


@router.get("/api/analytics/schedule", openapi_extra=_SCHEDULE_QUERY)
async def analytics_schedule(request: Request):
    return await run_in_threadpool(_schedule, request)


# ---- the admin's keys -------------------------------------------------------------------------------------

def _stamp(v):
    return v.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if v else None


def _list():
    return {"enabled": analytics_export.enabled(),
            "keys": [{"id": k["id"], "label": k["label"], "created_at": _stamp(k["created_at"]),
                      "revoked_at": _stamp(k["revoked_at"]), "last_used_at": _stamp(k["last_used_at"])}
                     for k in db_stream.list_keys()]}


@admin_router.get("/api/admin/analytics/keys",
                  openapi_extra=api_body.docs(get=(None, api_models.AnalyticsKeysOut)))
async def keys_list(request: Request):
    return _json_response(request, await run_in_threadpool(_list))


@admin_router.post("/api/admin/analytics/keys",
                   openapi_extra=api_body.docs(post=(api_models.AnalyticsKeyIn,
                                                     api_models.AnalyticsKeyCreatedOut, 201)))
async def keys_create(request: Request,
        body: api_body.RawRead = Depends(api_body.raw(api_models.WITH_REASON, api_models.AnalyticsKeyIn))):

    def _call():
        try:
            decoded = json.loads(body.raw.decode("utf-8")) if body.raw else {}
        except Exception:  # noqa: BLE001
            decoded = {}
        label = str((decoded or {}).get("label") or "").strip() or "export"
        kid, plain = db_stream.create_key(label)
        return 201, {"id": kid, "label": label[:80], "key": plain}

    status, payload = await api_body.run(body, _call)
    return _json_response(request, payload, status)


@admin_router.delete("/api/admin/analytics/keys",
                     openapi_extra=api_body.docs(delete=(None, api_body.OkOut))
                     | api_body.query_docs(delete={"id": "The key's number"}))
async def keys_revoke(request: Request):
    try:
        kid = int(request.query_params.get("id", ""))
    except ValueError:
        return _json_response(request, {"error": "id"}, 400)
    done = await run_in_threadpool(db_stream.revoke_key, kid)
    return _json_response(request, {"ok": bool(done)}, 200 if done else 404)
