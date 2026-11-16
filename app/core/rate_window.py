"""rate_window.py — the per-IP sliding window the guards share (2026-10-25).

Seven modules kept their own `_allowed` / `_permitido` (puco, auth_renaper, ocr_dni, chat_api, voice,
interview_patient, slot_holds) and three their own `_sweep` (abuse_guard, panel_login, searches) over a
module-level `{ip: [epoch, …]}`; the rule is the same, only the dict, the window and the limit differ.
Each module keeps its own dict (and its own tests clear it), and passes it in.
`tests/test_shared_helpers_contract.py` pins what each copy answered before.

Not here: the queue-task `_sweep`s, `ai_cooldown._sweep` (another structure and a strict `>` at the edge)
and `mcp_api._permitido` (delegates to its own guard).

`Hits` is the same window for the three guards that prune by the count of calls instead of by the size of
the dict (`abuse_guard`, `searches`, `panel_login`): every 500th call it drops the IPs with no hit left
in the window. `tests/test_ip_limiters_contract.py` pins what each of the three answered before.
"""

import time

_PRUNE_ABOVE = 5000


def allow(hits, key, limit, window, prune_above=_PRUNE_ABOVE):
    """Count a hit of `key` in `hits` unless it already has `limit` inside the last `window` seconds
    (then the hit is refused and not recorded). A blank key is the bucket «?». Past `prune_above`
    keys, the ones with no hit left inside the window are dropped (`None`: never prune)."""
    key = key or "?"
    now = time.time()
    xs = [t for t in hits.get(key, []) if now - t < window]
    if len(xs) >= limit:
        hits[key] = xs
        return False
    xs.append(now)
    hits[key] = xs
    if prune_above is not None and len(hits) > prune_above:
        for k in [k for k, v in hits.items() if not v or now - v[-1] > window]:
            del hits[k]
    return True


def over_limit(hits, key, limit, window):
    """Has `key` already `limit` hits inside the last `window` seconds? Only asks (nothing is recorded); the
    key keeps just its hits inside the window. The caller records a hit itself, when the event it counts
    (an account created, a failed login) really happened. `key` is used as it is (no «?» bucket)."""
    now = time.time()
    xs = [t for t in hits.get(key, []) if now - t < window]
    hits[key] = xs
    return len(xs) >= limit


def sweep(hits, now, window, stamp=lambda entry: entry):
    """Opportunistic pruning: drop the keys with no entry newer than `window` seconds — without it an
    IP that exhausts its quota and never comes back (or a scraper rotating fake IPs) stays in the dict
    for the life of the process. `stamp` reads the epoch out of an entry (the default: the entry is it)."""
    dead = [key for key, entries in hits.items() if not any(now - stamp(e) < window for e in entries)]
    for key in dead:
        del hits[key]


class Hits(dict):
    """`{key: [epoch, ...]}` of one per-IP limit, with the pruning on board: every `sweep_every`-th call to
    `allow` drops the keys with no hit left inside `window` seconds (cheap, but not free with many IPs, so
    not on every call). It is a plain dict for whoever reads or clears it (the tests do); `calls` counts
    the calls since the last prune."""

    def __init__(self, window, sweep_every=500):
        super().__init__()
        self.window = window
        self.sweep_every = sweep_every
        self.calls = 0

    def sweep(self, now):
        sweep(self, now, self.window)

    def allow(self, key, limit, also=None):
        """Count a hit of `key` unless it already has `limit` inside the window; `also` is a `(seconds,
        limit)` pair for a shorter window inside it. A refused hit is not recorded. A blank key is «?»."""
        key = key or "?"
        now = time.time()
        self.calls += 1
        if self.calls >= self.sweep_every:
            self.calls = 0
            self.sweep(now)
        xs = [t for t in self.get(key, []) if now - t < self.window]
        refused = len(xs) >= limit or (
            also is not None and sum(1 for t in xs if now - t < also[0]) >= also[1])
        if not refused:
            xs.append(now)
        self[key] = xs
        return not refused
