#!/usr/bin/env python3
"""load_hot_paths.py — closed-loop load test of the hot paths of ONE demo installation.

Point it at a LOCAL demo install only (own container + own Postgres, see
docs/architecture/capacity.md for the exact recipe). It refuses any host that
is not loopback or a private docker name unless `--i-know-this-is-not-production`
is passed, and it never reads or writes anything but the demo's fictional data.

What one virtual user does, in a loop with no think time, for `--duration`
seconds, at each concurrency level in `--levels`:

  login          POST /api/admin/login             (staff sign-in: password hashing, session cookie)
  queue          GET /api/admin/day-queue          (today's queue, the desk's main view)
  slots_patient  GET /api/service-slots/<id>       (what the patient wizard shows)
  slots_admin    GET /api/admin/appointment-slots  (the desk's free slots)
  panel          GET /api/admin/patients, /api/admin/requests and the /admin/cola screen
  booking        POST /api/admin/appointments      (a real write: books a free slot)

Output: one JSON document (`--out`) with, per level and per path, the request
count, throughput, latency percentiles and the status codes seen; plus the
Postgres connections observed in use when `--pg-container` names the database
container. `scripts/load_report.py` turns that JSON into the table and chart.

`--stall-table T` adds the failure the pool actually has to survive: while a level runs, the
database container holds an ACCESS EXCLUSIVE lock on T for `--stall-for` seconds (as a stuck
migration or a long write would), and the level also records a per-second timeline of answers,
503s and the slowest answer, so the start of the stall and the recovery are visible.

A 409 or 404 on `booking` is a business answer (the slot was just taken), not a
failure. The failures that matter are 5xx — above all 503, the app's «database
unavailable» answer when no pooled connection frees up within the wait.
"""
import argparse
import asyncio
import json
import random
import socket
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime
from ipaddress import ip_address
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
DEFAULT_MIX = {"login": 10, "queue": 25, "slots_patient": 20,
               "slots_admin": 10, "panel": 25, "booking": 10}
DEFAULT_LEVELS = (1, 5, 10, 20, 30, 40, 60, 80, 120)


def percentile(sorted_values, q):
    """q in [0, 100]; nearest-rank on an already sorted list."""
    if not sorted_values:
        return None
    rank = max(1, int(round(q / 100.0 * len(sorted_values))))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def summarise(samples, seconds):
    """samples: [(status, seconds)] -> counts, throughput, percentiles in ms."""
    latencies = sorted(s for _, s in samples)
    statuses = {}
    for status, _ in samples:
        statuses[str(status)] = statuses.get(str(status), 0) + 1
    ms = lambda v: None if v is None else round(v * 1000.0, 1)  # noqa: E731
    return {
        "requests": len(samples),
        "rps": round(len(samples) / seconds, 1) if seconds else 0.0,
        "p50_ms": ms(percentile(latencies, 50)),
        "p95_ms": ms(percentile(latencies, 95)),
        "p99_ms": ms(percentile(latencies, 99)),
        "max_ms": ms(latencies[-1] if latencies else None),
        "mean_ms": ms(statistics.fmean(latencies) if latencies else None),
        "statuses": statuses,
        "server_errors": sum(n for code, n in statuses.items() if code.startswith("5") or code == "0"),
        "unavailable_503": statuses.get("503", 0),
    }


def check_target(base, allow_other):
    host = urlsplit(base).hostname or ""
    try:
        address = ip_address(host)
    except ValueError:
        try:  # a name (localhost, a docker service name): judge what it resolves to
            address = ip_address(socket.gethostbyname(host))
        except (OSError, ValueError):
            address = None
    local = address is not None and (address.is_loopback or address.is_private)
    if not local and not allow_other:
        raise SystemExit(
            "refusing %r: this tool is for a LOCAL demo install only "
            "(--i-know-this-is-not-production overrides)" % host)


class PgSampler(threading.Thread):
    """Polls the database container for the connections in use."""

    def __init__(self, container, db, user):
        super().__init__(daemon=True)
        self.container, self.db, self.user = container, db, user
        self.stop = threading.Event()
        self.total, self.active = [], []

    def run(self):
        query = ("select count(*), count(*) filter (where state <> 'idle') "
                 "from pg_stat_activity where datname = '%s' and pid <> pg_backend_pid()" % self.db)
        while not self.stop.wait(1.0):
            try:
                out = subprocess.run(
                    ["docker", "exec", self.container, "psql", "-U", self.user, "-d", self.db,
                     "-tA", "-F", ",", "-c", query],
                    capture_output=True, text=True, timeout=5).stdout.strip()
                total, active = (int(x) for x in out.split(","))
            except Exception:  # noqa: BLE001 — a missed sample is not a failure
                continue
            self.total.append(total)
            self.active.append(active)

    def summary(self):
        return {"connections_max": max(self.total, default=None),
                "connections_active_max": max(self.active, default=None),
                "samples": len(self.total)}


class Scenario:
    def __init__(self, base, mix, seed, login_body):
        self.base = base.rstrip("/")
        self.login_body = login_body
        self.mix = mix
        self.rng = random.Random(seed)
        self.today = datetime.now(TZ).strftime("%Y-%m-%d")
        self.cookies = {}
        self.service_ids = []
        self.dnis = []
        self.free = []  # (servicio_id, dia, hora, medico) not yet booked in this run

    async def discover(self, client):
        # The demo's own «Gestión» entry is a read-only sandbox session (every write answers
        # 403 demo_solo_lectura), so the run signs in as the owner account the container was
        # started with (TURNOS_ADMIN_USER / TURNOS_ADMIN_PASS), exactly like the desk does.
        r = await client.post(self.base + "/api/admin/login", json=self.login_body)
        if r.status_code != 200 or "st_admin" not in client.cookies:
            raise SystemExit("admin login failed (%s %s) — start the container with "
                             "TURNOS_ADMIN_USER/TURNOS_ADMIN_PASS" % (r.status_code, r.text[:120]))
        self.cookies = dict(client.cookies)
        services = (await client.get(self.base + "/api/services")).json()
        rows = services.get("servicios") or []
        patients = (await client.get(self.base + "/api/admin/patients")).json()
        self.dnis = [str(p["dni"]) for p in patients.get("items") or [] if p.get("dni")]
        # Only services that actually have a bookable agenda take part in the run.
        for service in rows:
            sid = service.get("id")
            r = await client.get(self.base + "/api/admin/appointment-slots", params={"servicio_id": sid})
            if r.status_code != 200 or not r.json().get("libres"):
                continue
            self.service_ids.append(sid)
            for slot in r.json()["libres"]:
                self.free.append((sid, slot.get("dia"), slot.get("hora"), slot.get("medico")))
        self.rng.shuffle(self.free)
        if not self.service_ids or not self.free or not self.dnis:
            raise SystemExit("discovery found services=%d free_slots=%d patients=%d — the demo is not seeded yet"
                             % (len(self.service_ids), len(self.free), len(self.dnis)))

    def pick(self):
        names = list(self.mix)
        return self.rng.choices(names, weights=[self.mix[n] for n in names])[0]

    async def run_one(self, client, name):
        """Perform one request of kind `name`; yield (status, seconds) for each HTTP call."""
        base = self.base
        calls = []
        if name == "login":
            calls.append(("POST", "/api/admin/login", {}, self.login_body))
        elif name == "queue":
            calls.append(("GET", "/api/admin/day-queue", {"dia": self.today}, None))
        elif name == "slots_patient":
            calls.append(("GET", "/api/service-slots/%s" % self.rng.choice(self.service_ids), {}, None))
        elif name == "slots_admin":
            calls.append(("GET", "/api/admin/appointment-slots",
                          {"servicio_id": self.rng.choice(self.service_ids)}, None))
        elif name == "panel":
            calls.append(("GET", "/api/admin/patients", {}, None))
            calls.append(("GET", "/api/admin/requests", {}, None))
            calls.append(("GET", "/admin/cola", {}, None))
        elif name == "booking":
            if self.free:
                sid, dia, hora, medico = self.free.pop()
            else:  # exhausted: keep the write path loaded, the answer will be a 409
                sid, dia, hora, medico = (self.rng.choice(self.service_ids), self.today, "09:00", None)
            body = {"dni": self.rng.choice(self.dnis), "servicio_id": sid, "dia": dia, "hora": hora}
            if medico:
                body["medico"] = medico
            calls.append(("POST", "/api/admin/appointments", {}, body))
        out = []
        for method, path, params, body in calls:
            started = time.perf_counter()
            try:
                r = await client.request(method, base + path, params=params or None, json=body,
                                         follow_redirects=False)
                status = r.status_code
            except Exception:  # noqa: BLE001 — timeouts and resets count as failures (status 0)
                status = 0
            finished = time.perf_counter()
            out.append((status, finished - started, name if name != "panel" else "panel:" + path, finished))
        return out


async def run_level(scenario, concurrency, duration, timeout):
    limits = httpx.Limits(max_connections=concurrency + 5, max_keepalive_connections=concurrency + 5)
    deadline = time.perf_counter() + duration
    results = []

    async def user(index):
        async with httpx.AsyncClient(cookies=scenario.cookies, timeout=timeout, limits=limits) as client:
            while time.perf_counter() < deadline:
                results.extend(await scenario.run_one(client, scenario.pick()))

    started = time.perf_counter()
    await asyncio.gather(*(user(i) for i in range(concurrency)))
    return results, time.perf_counter() - started, started


def timeline(results, started):
    """Per second of the level (by completion time): requests answered, 503s, other failures,
    and the slowest answer — how a stall starts and how fast the app recovers from it."""
    buckets = {}
    for status, secs, _name, finished in results:
        b = buckets.setdefault(int(finished - started), {"ok": 0, "unavailable_503": 0, "failed": 0, "max_ms": 0.0})
        if status == 503:
            b["unavailable_503"] += 1
        elif status == 0 or status >= 500:
            b["failed"] += 1
        else:
            b["ok"] += 1
        b["max_ms"] = max(b["max_ms"], round(secs * 1000.0, 1))
    return [dict(second=s, **buckets[s]) for s in sorted(buckets)]


def start_stall(args):
    """Hold an ACCESS EXCLUSIVE lock on one table for `--stall-for` seconds, `--stall-at` seconds from now:
    every query that touches it (here: the staff permission lookup behind every panel request) waits,
    so connections stay checked out — the way a stuck migration or a long write would stall the app."""
    def lock():
        time.sleep(args.stall_at)
        sql = "BEGIN; LOCK TABLE %s IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(%s); COMMIT;" % (
            args.stall_table, args.stall_for)
        subprocess.run(["docker", "exec", args.pg_container, "psql", "-U", args.pg_user, "-d", args.pg_db,
                        "-q", "-c", sql], capture_output=True, text=True, timeout=args.stall_for + 30)
    thread = threading.Thread(target=lock, daemon=True)
    thread.start()
    return thread


async def amain(args):
    check_target(args.base, args.i_know_this_is_not_production)
    mix = dict(DEFAULT_MIX)
    if args.mix:
        mix = {k: int(v) for k, v in (pair.split("=") for pair in args.mix.split(","))}
    scenario = Scenario(args.base, mix, args.seed,
                        {"usuario": args.admin_user, "password": args.admin_pass})
    async with httpx.AsyncClient(timeout=30) as client:
        await scenario.discover(client)
    report = {"base": args.base, "label": args.label, "mix": mix, "duration_s": args.duration,
              "free_slots_at_start": len(scenario.free), "levels": []}
    for concurrency in args.levels:
        sampler = None
        if args.pg_container:
            sampler = PgSampler(args.pg_container, args.pg_db, args.pg_user)
            sampler.start()
        stall = start_stall(args) if args.stall_table else None
        results, seconds, started = await run_level(scenario, concurrency, args.duration, args.timeout)
        if stall:
            stall.join(timeout=args.stall_for + 30)
        if sampler:
            sampler.stop.set()
            sampler.join(timeout=3)
        by_name = {}
        for status, secs, name, _finished in results:
            by_name.setdefault(name, []).append((status, secs))
        level = {"concurrency": concurrency,
                 "all": summarise([(s, t) for s, t, _, _ in results], seconds),
                 "paths": {name: summarise(samples, seconds) for name, samples in sorted(by_name.items())}}
        if args.stall_table:
            level["stall"] = {"table": args.stall_table, "at_s": args.stall_at, "for_s": args.stall_for}
            level["timeline"] = timeline(results, started)
        if sampler:
            level["postgres"] = sampler.summary()
        report["levels"].append(level)
        total = level["all"]
        print("c=%-4d %6.1f req/s  p50 %6s  p95 %6s  503=%-4d 5xx/err=%-4d  pg_conns_max=%s" % (
            concurrency, total["rps"], total["p50_ms"], total["p95_ms"], total["unavailable_503"],
            total["server_errors"], level.get("postgres", {}).get("connections_max")), flush=True)
        await asyncio.sleep(args.cooldown)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print("wrote", args.out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:17700")
    ap.add_argument("--levels", type=lambda s: tuple(int(x) for x in s.split(",")), default=DEFAULT_LEVELS)
    ap.add_argument("--duration", type=float, default=25.0, help="seconds per level")
    ap.add_argument("--cooldown", type=float, default=5.0)
    ap.add_argument("--timeout", type=float, default=30.0, help="client timeout per request, seconds")
    ap.add_argument("--mix", help="e.g. queue=50,booking=10 (names as in the module docstring)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="load-result.json")
    ap.add_argument("--admin-user", default="loadadmin",
                    help="owner account of the throw-away container (TURNOS_ADMIN_USER)")
    ap.add_argument("--admin-pass", default="loadtest-pass-local-1",
                    help="its password (TURNOS_ADMIN_PASS); a local throw-away value, not a secret")
    ap.add_argument("--pg-container", help="docker container running the demo's Postgres (to sample connections)")
    ap.add_argument("--pg-db", default="turnos")
    ap.add_argument("--pg-user", default="postgres")
    ap.add_argument("--stall-table", help="lock this table during the run (needs --pg-container): simulates a stuck DB")
    ap.add_argument("--stall-at", type=float, default=8.0, help="seconds into each level when the lock is taken")
    ap.add_argument("--stall-for", type=float, default=10.0, help="how long the lock is held, seconds")
    ap.add_argument("--i-know-this-is-not-production", action="store_true")
    asyncio.run(amain(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
