"""analytics_report.py — the «Visitas» report of Reportes, computed from the anonymous stream.

Reads only `analytics_events` / `analytics_schedule` (core/db_stream.py). A visit is one `jid` (a browser
session id that rotates after 30 idle minutes and is never stored on the device); nothing here can name a
person. The funnel counts visits that reached each step, in order of the booking path:

  visit -> searched -> opened a doctor or service page -> saw slots -> sent the request -> confirmed
"""

import datetime
from collections import Counter

from app.core import clock, db_stream, search_collapse, tz

FUNNEL = ("visit", "search", "profile", "slots_shown", "submit", "done")
_TOP = 12


def _local_day(dt):
    return dt.astimezone(tz.tzinfo()).date()


class _Searches:
    """One row per typing session, fed in time order: successive prefixes of the same visitor (same `pl`)
    within 30 s collapse into the longest text, which keeps its final result count. A session that has been
    quiet for longer than the window is counted and forgotten, so the state stays small on a long report."""

    def __init__(self):
        self.current = {}
        self.veces, self.con = Counter(), Counter()
        self._fed = 0

    def _close(self, e):
        self.veces[e["q"]] += 1
        if (e.get("n") or 0) > 0:
            self.con[e["q"]] += 1

    def feed(self, e):
        if e["type"] != "search" or not e.get("q"):
            return
        key = e.get("pl") or e["id"]
        last = self.current.get(key)
        if last and (e["ts"] - last["ts"]).total_seconds() <= search_collapse.GLUE_SECONDS \
                and search_collapse.related(last["q"], e["q"]):
            self.current[key] = e if len(e["q"]) >= len(last["q"]) else dict(last, ts=e["ts"])
        else:
            if last:
                self._close(last)
            self.current[key] = e
        self._fed += 1
        if self._fed % 2000 == 0:
            for k in [k for k, v in self.current.items()
                      if (e["ts"] - v["ts"]).total_seconds() > search_collapse.GLUE_SECONDS]:
                self._close(self.current.pop(k))

    def top(self):
        for e in self.current.values():
            self._close(e)
        self.current = {}
        return [{"q": q, "veces": n, "con_resultados": self.con[q]} for q, n in self.veces.most_common(_TOP)]


_PROFILE_ROUTES = ("/doctor/:id", "/service/:id", "/d/:id", "/center/:slug")
# The booking path, from the first page to the confirmation. «search» is not on it: a visit may skip it.
_PATH = ("visit", "profile", "slots_shown", "submit", "done")


def _funnel(reached):
    """The counts of the funnel, monotone along the path: a visit that reached a step counts at every step
    before it too. The steps are sets of visit ids that were collected independently, and a visit id rotates
    after 30 idle minutes (or a beacon is lost), so «confirmed» can hold an id that never showed a page or
    the slots; read as they came, the later step would be larger than the earlier one."""
    reached = {k: set(v) for k, v in reached.items()}
    for later, earlier in zip(reversed(_PATH), reversed(_PATH[:-1])):
        reached[earlier] |= reached[later]
    reached["search"] &= reached["visit"]
    return [{"paso": k, "n": len(reached[k])} for k in FUNNEL]


def _top(counter, n=_TOP):
    return [{"clave": k, "n": v} for k, v in counter.most_common(n)]


def _schedule(first, last):
    snap = db_stream.latest_snapshot()
    if snap is None:
        return {"snapshot": None, "dias": []}
    rows = db_stream.schedule_days(snap, first, last)
    per_day = {}
    for r in rows:
        d = per_day.setdefault(r["day"], {"day": r["day"].isoformat(), "web": 0, "taken": 0, "free": 0})
        d["web"] += int(r["slots_web"] or 0)
        d["taken"] += int(r["taken"] or 0)
        d["free"] += int(r["free"] or 0)
    return {"snapshot": snap.isoformat(), "dias": [per_day[k] for k in sorted(per_day)]}


def payload(dias):
    now = clock.now()
    since = clock.utcnow() - datetime.timedelta(days=int(dias))
    # One pass over the window, read in pages (db_stream.events_iter): only the counters and the sets of
    # visit ids below stay in memory, however long the period.
    searches = _Searches()
    reached = {k: set() for k in FUNNEL}
    visits, per_day, seen_day = set(), Counter(), set()
    routes, pages, refs, devices, langs = Counter(), Counter(), Counter(), Counter(), Counter()
    events = views = 0
    for e in db_stream.events_iter(since):
        events += 1
        searches.feed(e)
        jid = e.get("jid")
        if e["type"] == "page_view":
            views += 1
            if jid:
                visits.add(jid)
                reached["visit"].add(jid)
                if e.get("route") in _PROFILE_ROUTES:
                    reached["profile"].add(jid)
                key = (_local_day(e["ts"]), jid)
                if key not in seen_day:
                    seen_day.add(key)
                    per_day[key[0]] += 1
            routes[e["route"]] += 1
            if e.get("rid"):
                pages[f'{e["route"]}|{e["rid"]}'] += 1
            for counter, field in ((refs, "ref"), (devices, "device"), (langs, "lang")):
                if e.get(field):
                    counter[e[field]] += 1
        elif jid and e["type"] == "search":
            reached["search"].add(jid)
        elif jid and e["type"] == "step" and e.get("step") in ("slots_shown", "submit", "done"):
            reached[e["step"]].add(jid)
    top = searches.top()
    days = []
    today = now.date()
    for i in range(int(dias) - 1, -1, -1):
        d = today - datetime.timedelta(days=i)
        days.append({"day": d.isoformat(), "visitas": per_day.get(d, 0)})
    funnel = _funnel(reached)
    return {
        "dias": int(dias),
        "eventos": events,
        "visitas": funnel[0]["n"],  # the headline and the funnel's first step are the same number
        "vistas": views,
        "serie": days,
        "embudo": funnel,
        "busquedas": top,
        "sin_resultados": [r for r in top if r["con_resultados"] == 0],
        "rutas": _top(routes),
        "paginas": _top(pages),
        "origenes": _top(refs),
        "dispositivos": _top(devices),
        "idiomas": _top(langs),
        "agenda": _schedule(today, today + datetime.timedelta(days=59)),
    }
