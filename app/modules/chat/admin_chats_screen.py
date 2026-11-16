"""`/admin/chats` — the chat module's read-only conversation list (the product owner,
2026-09-05: "create the chat as a separate group in the menu; put there the
wiki, put there the list of chats").

Admin-only, gated by the chat module at ROUTER level (module off -> 404
before the role gate). The rows come straight from `db_chat.chat_conversations()`
— one line per (channel, key): when it started, when it was last active,
how many turns, and the patient's first message. The transcript itself is
not exposed here: the list answers "who asked what, and when", which is
what a coordinator needs to decide which wiki article to write next.
Dates leave here in the installation timezone (`tz.iso`) and are written
client-side by the shared `dates.mjs` in ONE form, the panel lists' short
one ("Hoy, 10:15" / "Vie 6 nov, 17:21"), never raw ISO (the product owner's hub,
2026-11-07: the column mixed "Viernes 6 de noviembre, 21:58" and
"Vie 6 nov, 17:21").
"""

import hashlib
import json

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.core import access_log
from app.core import db_clinical
from app.core import db_chat
from app.core import deps
from app.core import screen_page
from app.core import tz
from app.auth import admin_auth

_MODULE_ID = "chat"
_SECTION_ID = "chats"
_TEMPLATE = "modules/chat/admin_chats.html"

router = APIRouter(dependencies=[Depends(deps.require_module_enabled(_MODULE_ID))])
screen_router = router  # name the manifest expects (screen.router_attr)


def _log_interviews(identity, rows):
    """The list shows the start of each pre-consult interview — the patient's
    own words about their health — so every interview it shows leaves its row
    in the clinical access log (K10, 2026-10-23), once a day like every list."""
    keys = [r["key"] for r in rows if r.get("channel") == "interview"]
    items = []
    if keys:
        owners = db_clinical.interviews_by_session_keys(keys)
        items = [(owners.get(k, {}).get("dni"), owners.get(k, {}).get("id") or k) for k in keys]
    # The web and WhatsApp rows show their first 240 characters too — a patient's words about their health,
    # under no DNI. They leave a row as well (a digest of the conversation key as the target, never the phone
    # number itself), so a read of the list is never unrecorded (security review 2026-11).
    items += [(None, "%s:%s" % (r["channel"], hashlib.sha256(str(r["key"]).encode()).hexdigest()[:16]))
              for r in rows if r.get("channel") != "interview"]
    if items:
        access_log.record_many(identity, "conversaciones", items)


# The channels the pre-consult interview engine writes: the interview itself
# and «Contanos tu caso» (the same engine, before a request is sent).
CLINICAL_CHANNELS = ("interview", "caso")


def _sees_interviews(identity):
    """The pre-consult interview is clinical: its rows reach this list only for
    a session that reads every patient's clinical data (the administrator, or
    an account holding `todos_los_pacientes`). The `asistente` permission alone
    — editing what the assistant knows and reading its conversations — never
    opens a patient's interview (security review 2026-11, M-4); a doctor reads
    their own patients' interviews on their own screens. Read from the
    permissions the screen gate verified for this request."""
    if (identity or {}).get("rol") == "admin":
        return True
    return access_log.ALL_PATIENTS in admin_auth.active_permissions()


def _rows(identity=None, sees_interviews=True):
    try:
        rows = db_chat.chat_conversations(
            exclude_channels=() if sees_interviews else CLINICAL_CHANNELS)
        # A list whose interview rows can't be logged is not shown at all:
        # same rule as every clinical read (no unrecorded read).
        _log_interviews(identity, rows)
    except Exception:  # noqa: BLE001 — a DB hiccup shows an empty list, never a 500 shell
        return None
    out = []
    for r in rows:
        out.append({
            "channel": r["channel"], "key": r["key"],
            # Installation wall clock: the list reads day and hour off the text.
            "started": tz.iso(r.get("started")),
            "last_at": tz.iso(r.get("last_at")),
            "user_messages": r["user_messages"], "messages": r["messages"],
            "first_message": (r.get("first_message") or "")[:240],
        })
    return out


@router.get("/admin/chats")  # == module.json's admin_sections[].screen.path
async def chats_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    rows = await run_in_threadpool(_rows, identity, _sees_interviews(identity))
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        rows_json=json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"),
        rows_failed=rows is None,
    )
