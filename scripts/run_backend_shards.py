#!/usr/bin/env python3
"""Run the backend unittest suite as parallel shards, each on its own database.

The serial run (`scripts/run_backend_tests.py`, one process, ~6900 tests) is the
slowest surface of the CI ladder. This runner splits the `tests/test_*.py` files
into N shards that run side by side and merges the results into the SAME report
schema, so `scripts/check_unittest_coverage.py --result-json` gates a sharded
run exactly as it gates a serial one (same `--minimum`).

* **One fresh interpreter per test file.** A shard runs its files one after the
  other, each as `run_backend_tests.py --pattern <file>` — exactly the
  documented way to run a single file (`unittest discover -p test_x.py`). The
  serial run shares one process across the whole suite, and a good number of
  files only pass there because an earlier file left `os.environ`, a cache or a
  dead `DATABASE_URL` behind; split across shards that dependency would
  surface (or not) depending on which files happen to be neighbours. A fresh
  interpreter per file makes a file's result independent of the partition. The
  price is ~0.3 s of interpreter start-up per file.
* **Own database per shard.** Test files create run-scoped databases of their
  own (`tests/_dbtest.py`), but a file without one — and every fall-through
  after a teardown — talks to `DATABASE_URL`, the shared maintenance database,
  whose state (language settings, caches) two parallel runs corrupt. Each shard
  gets a database and a `TMPDIR` of its own, `DATABASE_URL` / `TEST_DATABASE_URL`
  pointed at it, and both are emptied again between two files; the databases
  are named so that `_dbtest.reclaim_stale_databases` collects the ones a
  killed run leaves.
* **Balanced by recorded time.** Files are placed longest-first onto the
  least-loaded shard, using per-file seconds from
  `scripts/backend_test_timings.json` (override with `--timings`). A fixed seed
  keeps the partition identical on every machine. Each run writes its own
  measurements with `--timings-out` (CI keeps them as an artifact); fold them
  into the seed with `--update-seed` after the suite changes shape, or refresh
  it from the CI runners' own artifacts with `scripts/update_backend_timings.py`
  (what a lane takes on a shared runner is not what it takes on a laptop). A
  file missing from the seed is weighed by its size, so a new test file needs
  no seed edit. CI hands the refreshed seed over by itself (`--seed-out`, the artifact
  `backend-timings-seed.json`: copy it over the seed file), and
  `test_run_backend_shards` fails when more than `MAX_UNSEEDED_SHARE` of the files have no
  seconds in the seed. A run whose slowest lane is far above an even split says so.
* **Coverage (`--coverage-dir D`).** Each file's interpreter runs under `coverage run --branch`
  (`scripts/coveragerc`), its data file lands in D, and once every lane is done the runner
  combines them and leaves `coverage-summary.json` / `.txt` / `coverage-files.json` there
  (`scripts/coverage_floor.py` explains the numbers and gates them). Measuring is in-process
  only: what a test starts as a subprocess is not counted.
* **Serial-only group last.** Files in `SERIAL_ONLY` cannot share the machine
  with other suite processes (reason beside each name); they run alone, after
  the shards, on their own database.

`TURNOS_BACKEND_SHARDS` / `--shards` sets N; `TURNOS_BACKEND_SERIAL=1` is handled
by the callers (ci_local.sh, .gitlab-ci.yml) and keeps the one-process run.
"""
import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO = Path(__file__).resolve().parents[1]
SEED_TIMINGS = REPO / "scripts" / "backend_test_timings.json"
WORKER = REPO / "scripts" / "run_backend_tests.py"
COVERAGE_RCFILE = REPO / "scripts" / "coveragerc"
SCHEMA_VERSION = 1
DEFAULT_SHARDS = 5
MAX_SHARDS = 12
# A file that runs longer than this is killed and counted as a failure; the
# whole serial suite is ~13 minutes on a quiet machine, the slowest file ~4.
MODULE_TIMEOUT_SECONDS = 1500
# The whole run stops with its reason after this (TURNOS_BACKEND_DEADLINE / --deadline, 0 = none):
# under the 2-hour limit a background local-ci gets, so the runner names what was still running
# instead of being killed silently by whoever holds the outer timeout (05.11).
DEFAULT_DEADLINE_SECONDS = 5400
# A line saying where every lane is, this often (TURNOS_BACKEND_HEARTBEAT / --heartbeat, 0 = none).
DEFAULT_HEARTBEAT_SECONDS = 120
POLL_SECONDS = 0.5        # how soon a lane sees a stop, the main thread a deadline
KILL_GRACE_SECONDS = 60   # how long a SIGKILLed file may take to be reaped before the lane gives up
STOP_GRACE_SECONDS = 90   # how long the run waits for its lanes once it is stopping
TAIL_LINES = 20           # how much of a dead file's output its death notice repeats
# DROP / CREATE DATABASE never hang a lane on a lock for longer than this.
MAINT_STATEMENT_TIMEOUT_MS = 300000

# file stem -> why it must not share the machine with other suite processes.
SERIAL_ONLY = {}

# The slowest lane may exceed an even split of the work by this factor before the run says the seed is stale.
SKEW_WARN = 1.25
# More files than this without seconds in the seed and the balancing is mostly a size guess.
MAX_UNSEEDED_SHARE = 0.05


# ---------------------------------------------------------------- planning

def discover_modules(start_directory="tests", pattern="test_*.py"):
    """Top-level test files, exactly what `unittest discover` would import
    (`tests/` is not a package, so subdirectories are never descended)."""
    root = REPO / start_directory
    return sorted(p.stem for p in root.glob(pattern) if p.is_file())


def load_timings(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    modules = data.get("modules") if isinstance(data, dict) else None
    if not isinstance(modules, dict):
        return {}
    return {str(k): float(v) for k, v in modules.items()
            if isinstance(v, (int, float)) and v >= 0}


def _weights(modules, timings, start_directory="tests"):
    """Seconds per module: the recorded time, else an estimate from file size
    scaled by the recorded seconds-per-byte (a new file lands somewhere
    plausible instead of weighing nothing)."""
    root = REPO / start_directory
    sizes = {}
    for m in modules:
        try:
            sizes[m] = (root / f"{m}.py").stat().st_size
        except OSError:
            sizes[m] = 0
    known = [m for m in modules if m in timings]
    total_bytes = sum(sizes[m] for m in known)
    per_byte = (sum(timings[m] for m in known) / total_bytes) if total_bytes else 0.0
    default_seconds = 1.0
    return {m: timings[m] if m in timings
            else max(sizes[m] * per_byte, default_seconds) for m in modules}


def plan_shards(modules, timings, shards, serial_only=None, start_directory="tests"):
    """Return (parallel shards, serial group, shard loads in seconds).

    Longest-processing-time-first onto the least-loaded shard; ties go to the
    lowest index and files inside a shard run in name order, so the plan is a
    pure function of its inputs."""
    serial_only = SERIAL_ONLY if serial_only is None else serial_only
    serial = [m for m in modules if m in serial_only]
    parallel = [m for m in modules if m not in serial_only]
    shards = max(1, min(int(shards), len(parallel) or 1))
    weight = _weights(parallel, timings, start_directory)
    loads = [0.0] * shards
    buckets = [[] for _ in range(shards)]
    for m in sorted(parallel, key=lambda name: (-weight[name], name)):
        i = min(range(shards), key=lambda k: (loads[k], k))
        buckets[i].append(m)
        loads[i] += weight[m]
    return [sorted(b) for b in buckets], serial, loads


def unseeded_share(modules, timings):
    """Share of `modules` that the seed has no seconds for (0.0 on an empty suite)."""
    return sum(1 for m in modules if m not in timings) / len(modules) if modules else 0.0


def next_seed(timings, measured, modules=None):
    """The seed after a run: this run's seconds win, a file it did not measure keeps its old value;
    with `modules` (the files that exist) a file that no longer exists is dropped."""
    seed = {**timings, **measured}
    if modules is not None:
        seed = {m: s for m, s in seed.items() if m in set(modules)}
    return dict(sorted(seed.items()))


def balance_note(lane_seconds, longest_file):
    """One line on how evenly the lanes finished, with the way out when they did not. A lane that
    is slow only because it holds the single longest file is not the seed's fault: no hint then."""
    if len(lane_seconds) < 2 or not any(lane_seconds):
        return ""
    slowest = max(lane_seconds)
    even = sum(lane_seconds) / len(lane_seconds)
    note = f"lane balance: slowest {slowest:.0f}s, even split {even:.0f}s (x{slowest / even:.2f})"
    if slowest > even * SKEW_WARN and slowest > longest_file * 1.05:
        note += "; the seed is stale, refresh it: python3 scripts/update_backend_timings.py"
    return note


# ---------------------------------------------------------------- databases

def _run_token():
    """Same stamp shape as `_dbtest._RUN_TOKEN`, so an orphan is reclaimable
    from the name alone (`_dbtest.reclaim_stale_databases`)."""
    return "t%xp%dr%d" % (int(time.time()), os.getpid(), secrets.randbelow(1000))


def _urls(base_url, database):
    p = urlsplit(base_url)
    maint = urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))
    own = urlunsplit((p.scheme, p.netloc, "/" + database, p.query, p.fragment))
    return maint, own


def _maint_exec(maint_url, statements):
    """Runs CREATE / DROP DATABASE statements on the maintenance database. They are utility SQL with
    no Core construct, so `text()` through `core/sqlcore.py`, the one executor of the code base
    (`:` is escaped so `text()` does not read it as a bind parameter)."""
    import psycopg
    import sqlalchemy as sa
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from app.core import sqlcore
    with psycopg.connect(maint_url, autocommit=True, connect_timeout=10,
                         options=f"-c statement_timeout={MAINT_STATEMENT_TIMEOUT_MS}") as conn:
        with conn.cursor() as cur:
            for statement in statements:
                sqlcore.run(cur, sa.text(statement.replace(":", "\\:")))


# ---------------------------------------------------------------- execution

def _describe_exit(code):
    """Why an interpreter ended without a result report, in words a log reader needs."""
    if code is None:
        return f"did not exit {KILL_GRACE_SECONDS}s after SIGKILL"
    if code < 0:
        try:
            return f"killed by {signal.Signals(-code).name}"
        except ValueError:
            return f"killed by signal {-code}"
    if code:
        return f"exit code {code}"
    return "exited 0 without writing its result report"


def _clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def _tail(path, offset, lines=TAIL_LINES):
    """The last lines a file wrote to the lane log, from its `#####` banner on."""
    try:
        with open(path, "rb") as stream:
            stream.seek(offset)
            text = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.rstrip("\n").splitlines()[-lines:]


class _Shard:
    """One lane: runs its files one at a time, each in a fresh interpreter,
    appending their output to one log. A file that ends without its result report
    (a signal, an exit code, the per-file timeout) stops the whole run at once:
    `halt` raises the shared stop flag and the reason goes out flushed."""

    def __init__(self, label, modules, env, log, workdir, start_directory, stop, fresh,
                 coverage_rcfile=None, say=None, halt=None):
        self.label, self.modules, self.env = label, modules, env
        self.coverage_rcfile = coverage_rcfile  # run each file under `coverage run` with it
        self.log, self.workdir = log, workdir
        self.start_directory, self.stop = start_directory, stop
        self.fresh = fresh     # called between two files: empty database, empty TMPDIR
        self.say = say or (lambda line: print(line, flush=True))
        self.halt = halt or (lambda reason: stop.set())
        self.reports = {}      # module -> report dict, or None if it wrote none
        self.exits = {}        # module -> exit code (negative = signal, None = would not die)
        self.causes = {}       # module -> why it ended without a report (it stopped the run)
        self.interrupted = []  # modules killed because the run was stopping
        self.seconds = {}      # module -> wall seconds, interpreter start included
        self.elapsed = 0.0
        self.done = False
        self.module, self.position, self.started = None, 0, 0.0
        self.current = None    # live Popen, for the abort handler

    def run(self):
        began = time.monotonic()
        try:
            with open(self.log, "wb") as stream:
                for position, module in enumerate(self.modules):
                    if self.stop.is_set():
                        break
                    if position:
                        self.module = None
                        self.fresh()
                        if self.stop.is_set():
                            break
                    self._run_file(stream, position, module)
        except Exception as exc:  # noqa: BLE001 - a lane that cannot go on stops the run, named
            self.halt(f"{self.label} stopped: {exc!r}")
        finally:
            self.module = None
            self.done = True
            self.elapsed = time.monotonic() - began

    def _run_file(self, stream, position, module):
        report = self.workdir / f"{self.label}-{module}.json"
        command = [sys.executable, "-W", "error::ResourceWarning"]
        if self.coverage_rcfile:
            command += ["-m", "coverage", "run", "--rcfile", str(self.coverage_rcfile)]
        command += [str(WORKER),
                    "--report", str(report),
                    "--start-directory", str(REPO / self.start_directory),
                    "--pattern", f"{module}.py"]
        offset = stream.tell()
        stream.write(f"##### {module}\n".encode())
        stream.flush()
        self.started = time.monotonic()
        self.position, self.module = position, module
        self.current = subprocess.Popen(
            command, cwd=REPO, env=self.env, stdout=stream,
            stderr=subprocess.STDOUT, start_new_session=True)
        code, timed_out = self._wait()
        seconds = time.monotonic() - self.started
        if timed_out:
            stream.write(f"\n##### {module}: killed after {MODULE_TIMEOUT_SECONDS}s\n".encode())
            stream.flush()
        self.seconds[module] = seconds
        self.exits[module] = code
        self.reports[module] = _read_json(report)
        _kill_group(self.current)  # strays of the file (servers, workers)
        self.current = None
        self.module = None
        if self.reports[module] is not None:
            return
        if self.stop.is_set() and not timed_out:
            self.interrupted.append(module)
            return
        why = (f"killed after the {MODULE_TIMEOUT_SECONDS}s per-file timeout" if timed_out
               else _describe_exit(code))
        self.causes[module] = why
        lines = [f"{self.label}: {module} died: {why} after {_clock(seconds)} "
                 f"(file {position + 1}/{len(self.modules)}); its last output:"]
        lines += [f"  | {line}" for line in _tail(self.log, offset)]
        self.say("\n".join(lines))
        self.halt(f"{self.label}: {module} died: {why}")

    def _wait(self):
        """(exit code, timed out). Polls so a stop or the per-file timeout is seen within a second,
        and never waits on a killed group longer than KILL_GRACE_SECONDS."""
        while True:
            try:
                return self.current.wait(timeout=POLL_SECONDS), False
            except subprocess.TimeoutExpired:
                pass
            if self.stop.is_set():
                return _reap(self.current), False
            if time.monotonic() - self.started > MODULE_TIMEOUT_SECONDS:
                return _reap(self.current), True

    def finished(self):
        """Files that ran to their result report."""
        return sum(report is not None for report in self.reports.values())

    def status(self):
        """This lane in one heartbeat cell: where it is and for how long."""
        n = len(self.modules)
        if self.done:
            return f"{self.label} done {self.finished()}/{n}"
        if self.module is None:
            return f"{self.label} {self.finished()}/{n} resetting"
        return (f"{self.label} {self.position + 1}/{n} {self.module} "
                f"{_clock(time.monotonic() - self.started)}")


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _reap(process):
    """Kills the file's process group and waits for it a bounded time; None when it would not die."""
    _kill_group(process)
    try:
        return process.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return None


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def merge_reports(reports):
    """Sum per-file reports into one report in the serial schema. A missing
    report (the interpreter died before writing one) is None and fails the
    merge."""
    merged = {"schema_version": SCHEMA_VERSION, "tests_run": 0, "failures": 0,
              "errors": 0, "unexpected_successes": 0, "expected_failures": 0,
              "skipped": []}
    for report in reports:
        if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
            return None
        for key in ("tests_run", "failures", "errors", "unexpected_successes",
                    "expected_failures"):
            merged[key] += report[key]
        merged["skipped"].extend(report["skipped"])
    return merged


_FAILURE_HEADER = re.compile(r"(?m)^={70}\n(?:FAIL|ERROR): ")
_FILE_END = re.compile(r"(?m)^-{70}\nRan \d+ tests?")


def failure_sections(log_text):
    """Every traceback block in a shard log (one `Ran N tests` summary per
    file), cut from its first FAIL/ERROR banner to that file's summary."""
    out = []
    position = 0
    while True:
        first = _FAILURE_HEADER.search(log_text, position)
        if not first:
            break
        end = _FILE_END.search(log_text, first.start())
        stop = end.start() if end else len(log_text)
        out.append(log_text[first.start():stop].rstrip() + "\n")
        position = stop
    return "\n".join(out)


def _print_plan(shards, serial, loads):
    for i, (bucket, load) in enumerate(zip(shards, loads), 1):
        print(f"shard {i}: {len(bucket)} modules, ~{load:.0f}s")
        for m in bucket:
            print(f"  {m}")
    print(f"serial group: {len(serial)} modules")
    for m in serial:
        print(f"  {m}")


def _prepare_coverage_dir(coverage_arg):
    """The empty directory the lanes leave their coverage data in, or None when
    not measuring. Raises ImportError when coverage.py is missing."""
    if not coverage_arg:
        return None
    import coverage  # noqa: F401
    coverage_dir = Path(coverage_arg).resolve()
    shutil.rmtree(coverage_dir, ignore_errors=True)
    coverage_dir.mkdir(parents=True)
    return coverage_dir


class _Lanes:
    """The lanes of one run, with what they share: the databases they created,
    the working directory, the stop flag the abort handler raises."""

    def __init__(self, args, coverage_dir, progress=None):
        self.start_directory = args.start_directory
        self.coverage_dir = coverage_dir
        self.base_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
        self.log_dir = Path(args.log_dir) if args.log_dir else Path(tempfile.mkdtemp(prefix="shard-logs-"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Short path on purpose: each lane's TMPDIR lives under it and unix socket
        # paths are capped at ~104 bytes (macOS's default temp dir is already ~50).
        self.workdir = Path(tempfile.mkdtemp(prefix="bs", dir="/tmp" if os.path.isdir("/tmp") else None))
        self.token = _run_token()
        self.created = []
        self.stop = threading.Event()
        self.reason = None     # why the run stopped early (a death, the deadline, a signal)
        self.lanes = []
        self.progress = progress
        self.deadline = getattr(args, "deadline", 0) or 0
        self.heartbeat = getattr(args, "heartbeat", 0) or 0
        self.began = time.monotonic()
        self._said = threading.RLock()  # re-entrant: the signal handler may land inside say()

    def say(self, line):
        """One announcement, flushed at once to the log and to the progress stream: a run that is
        killed a second later has still said why it was stopping."""
        with self._said:
            for stream in (sys.stdout, self.progress):
                if stream is None:
                    continue
                try:
                    print(line, file=stream, flush=True)
                except (OSError, ValueError, RuntimeError):
                    pass  # a closed progress stream or a re-entrant write never stops the run

    def halt(self, reason):
        """Stops every lane (the first reason wins) and says which files were running."""
        with self._said:
            if self.stop.is_set():
                return
            self.reason = reason
            self.stop.set()
        running = [f"{lane.label} {lane.module}" for lane in self.lanes
                   if not lane.done and lane.module]
        never = sum(len(lane.modules) - len(lane.reports) - (1 if lane.module else 0)
                    for lane in self.lanes if not lane.done)
        self.say(f"run_backend_shards: stopping every lane after "
                 f"{_clock(time.monotonic() - self.began)}: {reason}"
                 f"; running: {', '.join(running) or 'nothing'}; {never} more file(s) will not run")
        for lane in self.lanes:
            if lane.current is not None:
                _kill_group(lane.current)

    def new_lane(self, label, bucket):
        env = dict(os.environ)
        tmp = self.workdir / label
        tmp.mkdir()
        env["TMPDIR"] = str(tmp)
        if self.coverage_dir:
            env["COVERAGE_FILE"] = str(self.coverage_dir / ".coverage")
        statements = []
        if self.base_url:
            database = f"sana_turnos_test_{label}_{self.token}"
            maint, own = _urls(self.base_url, database)
            statements = [f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)',
                          f'CREATE DATABASE "{database}"']
            _maint_exec(maint, statements)
            self.created.append((maint, database))
            env["DATABASE_URL"] = own
            env["TEST_DATABASE_URL"] = own

        def fresh():
            # What one file leaves in the lane's database or temp directory is
            # not there for the next one.
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            if statements:
                _maint_exec(maint, statements)

        lane = _Shard(label, bucket, env, self.log_dir / f"{label}.log", self.workdir,
                      self.start_directory, self.stop, fresh,
                      coverage_rcfile=COVERAGE_RCFILE if self.coverage_dir else None,
                      say=self.say, halt=self.halt)
        self.lanes.append(lane)
        return lane

    def abort(self, signum, _frame):
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = f"signal {signum}"
        self.halt(f"interrupted by {name}")

    def _tick(self, next_beat):
        """The main thread's turn between two joins: the run deadline and the heartbeat."""
        now = time.monotonic()
        if self.deadline and now - self.began >= self.deadline and not self.stop.is_set():
            self.halt(f"deadline of {self.deadline}s reached")
        if self.heartbeat and now >= next_beat:
            try:
                load = f" load {os.getloadavg()[0]:.0f}"
            except OSError:
                load = ""
            self.say(f"heartbeat {_clock(now - self.began)}{load} | "
                     + " | ".join(lane.status() for lane in self.lanes))
            next_beat = now + self.heartbeat
        return next_beat

    def run_group(self, group):
        threads = [threading.Thread(target=lane.run, name=lane.label, daemon=True) for lane in group]
        for thread in threads:
            thread.start()
        next_beat = time.monotonic() + self.heartbeat
        stopping_since = None
        while any(thread.is_alive() for thread in threads):
            alive = next(thread for thread in threads if thread.is_alive())
            alive.join(POLL_SECONDS)  # a join with a timeout stays interruptible
            next_beat = self._tick(next_beat)
            if self.stop.is_set():
                stopping_since = stopping_since or time.monotonic()
                if time.monotonic() - stopping_since > STOP_GRACE_SECONDS:
                    stuck = ", ".join(t.name for t in threads if t.is_alive())
                    self.say(f"run_backend_shards: {stuck} did not stop within "
                             f"{STOP_GRACE_SECONDS}s; reporting without them")
                    break
        for lane in group:
            self.say(f"  {lane.label}: {lane.finished()}/{len(lane.modules)} modules "
                     f"{lane.elapsed:.0f}s")

    def run_shards(self, shards, serial):
        """Runs the shard lanes side by side, then the serial group alone; always
        kills the strays and restores the signal handlers. The databases are dropped
        by `drop_databases`, after the report: a slow drop never delays the reason."""
        previous = {s: signal.signal(s, self.abort) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            self.run_group([self.new_lane(f"shard{i}", bucket) for i, bucket in enumerate(shards, 1)])
            if serial and not self.stop.is_set():
                self.run_group([self.new_lane("serial", serial)])
        finally:
            for lane in self.lanes:
                if lane.current is not None:
                    _kill_group(lane.current)
            for s, handler in previous.items():
                signal.signal(s, handler)

    def drop_databases(self):
        for maint, database in self.created:
            try:
                _maint_exec(maint, [f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'])
            except Exception as exc:  # noqa: BLE001 - reclaimed later by _dbtest
                print(f"run_backend_shards: could not drop {database}: {exc!r}",
                      file=sys.stderr)


def _print_lane_logs(lanes):
    """One log, same shape the serial run leaves: per-shard output."""
    for lane in lanes:
        n = sum(r["tests_run"] for r in lane.reports.values() if r)
        print("\n" + "=" * 70)
        print(f"{lane.label}: {len(lane.modules)} modules, {n} tests in {lane.elapsed:.0f}s")
        print("=" * 70)
        print(lane.log.read_text(encoding="utf-8", errors="replace"))


def _print_failures(lanes, died, interrupted, missing, reason):
    """The failures of every shard together, then what never produced a result: the files that
    died (with the signal or exit code), the ones the stop cut short, the ones never started."""
    sections = [(lane.label, failure_sections(
        lane.log.read_text(encoding="utf-8", errors="replace"))) for lane in lanes]
    sections = [(label, text) for label, text in sections if text]
    if sections or died or interrupted or missing or reason:
        print("\n" + "#" * 70)
        print("FAILURES OF ALL SHARDS")
        print("#" * 70)
        for label, text in sections:
            print(f"--- {label} ---")
            print(text)
        for m, why in sorted(died.items()):
            print(f"--- {m}: no result report ({why}) ---")
        for m in interrupted:
            print(f"--- {m}: stopped while running ---")
        for m in missing:
            print(f"--- {m}: never ran ---")
        if reason:
            print(f"--- run stopped: {reason} ---")
        sys.stdout.flush()


def _print_coverage(coverage_dir):
    """Combines the lanes' coverage data and prints the table; False when it could not."""
    sys.path.insert(0, str(REPO / "scripts"))
    import coverage_floor
    try:
        began = time.monotonic()
        summary = coverage_floor.combine_and_summarize(coverage_dir)
        print("\n" + "=" * 70)
        print(f"coverage (combined in {time.monotonic() - began:.0f}s; "
              f"{coverage_dir / coverage_floor.SUMMARY_JSON})")
        print("=" * 70)
        print(coverage_floor.render_table(summary, coverage_floor.load_floor(missing_ok=True)),
              end="")
    except Exception as exc:  # noqa: BLE001 - reported, and fails the run
        print(f"\ncoverage: could not combine the data files ({type(exc).__name__}: {exc})")
        return False
    return True


def _write_timings(args, lanes, nshards, timings, clean, stopped=False):
    """Prints the lane-balance note and writes the measured seconds (--timings-out, --update-seed)."""
    measured = {m: round(s, 2) for lane in lanes for m, s in lane.seconds.items()}
    note = balance_note([lane.elapsed for lane in lanes[:nshards]], max(measured.values(), default=0.0))
    if note and not stopped:  # a stopped run says nothing about the seed
        print("\n" + note)
    share = unseeded_share(list(measured), timings)
    if args.seed_out and share > MAX_UNSEEDED_SHARE:
        print(f"seed: {share:.0%} of the files have no seconds in it; take the artifact "
              "backend-timings-seed.json over scripts/backend_test_timings.json")
    if args.timings_out:
        out = {"schema_version": SCHEMA_VERSION, "modules": dict(sorted(measured.items()))}
        Path(args.timings_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.timings_out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    if (args.update_seed or args.seed_out) and measured and clean:
        def dump(seed):
            return json.dumps({"schema_version": SCHEMA_VERSION, "modules": seed}, indent=1) + "\n"
        if args.update_seed:
            SEED_TIMINGS.write_text(dump(next_seed(timings, measured)), encoding="utf-8")
        if args.seed_out:
            refreshed = next_seed(timings, measured, discover_modules(args.start_directory, args.pattern))
            Path(args.seed_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.seed_out).write_text(dump(refreshed), encoding="utf-8")


def _verdict_flags(merged):
    flags = []
    if merged["failures"]:
        flags.append(f"failures={merged['failures']}")
    if merged["errors"]:
        flags.append(f"errors={merged['errors']}")
    if merged["skipped"]:
        flags.append(f"skipped={len(merged['skipped'])}")
    if merged["expected_failures"]:
        flags.append(f"expected failures={merged['expected_failures']}")
    if merged["unexpected_successes"]:
        flags.append(f"unexpected successes={merged['unexpected_successes']}")
    return flags


def _write_report_and_verdict(args, merged, lanes, nshards, nserial, wall, ok, reason=None):
    """The merged report (with its `sharding` block) and the closing verdict lines; returns the
    verdict in one line for the progress stream."""
    if merged is None:
        verdict = f"FAILED (run stopped: {reason})" if reason else \
            "FAILED (a test file produced no valid result report)"
        print("\n" + verdict)
        return verdict
    merged["sharding"] = {
        "shards": nshards, "serial_modules": nserial,
        "wall_seconds": round(wall, 1),
        "shard_seconds": {lane.label: round(lane.elapsed, 1) for lane in lanes},
    }
    sys.path.insert(0, str(REPO / "scripts"))
    import run_backend_tests
    run_backend_tests._write_report(args.report, merged)
    flags = _verdict_flags(merged)
    n = merged["tests_run"]
    print("\n" + "-" * 70)
    print(f"Ran {n} test{'s' if n != 1 else ''} in {wall:.3f}s ({nshards} shards)\n")
    if reason:
        flags.append(f"run stopped: {reason}")
    verdict = ("OK" if ok else "FAILED") + (f" ({', '.join(flags)})" if flags else "")
    print(verdict)
    return verdict


def _open_progress(fd):
    """The stream the heartbeats and the verdict also go to (ci_local.sh hands its own stdout in as
    fd 3, so a backgrounded local-ci is not silent while the runner's log goes to a file)."""
    if fd is None:
        return None
    try:
        return os.fdopen(fd, "w", buffering=1, encoding="utf-8", errors="replace", closefd=False)
    except OSError as exc:
        print(f"run_backend_shards: --progress-fd {fd} unusable ({exc}); no progress stream",
              file=sys.stderr)
        return None


def run(args):
    modules = discover_modules(args.start_directory, args.pattern)
    if not modules:
        print("run_backend_shards: no test modules found", file=sys.stderr)
        return 2
    timings = load_timings(args.timings)
    shards, serial, loads = plan_shards(modules, timings, args.shards,
                                        start_directory=args.start_directory)
    if args.plan:
        _print_plan(shards, serial, loads)
        return 0
    try:
        coverage_dir = _prepare_coverage_dir(args.coverage_dir)
    except ImportError:
        print("run_backend_shards: --coverage-dir needs coverage.py "
              "(python3 -m pip install -r scripts/requirements-ci.txt)", file=sys.stderr)
        return 2
    progress = _open_progress(args.progress_fd)
    runner = _Lanes(args, coverage_dir, progress=progress)
    try:
        return _run_and_report(args, runner, modules, shards, serial, timings, coverage_dir)
    finally:
        runner.drop_databases()
        shutil.rmtree(runner.workdir, ignore_errors=True)


def _run_and_report(args, runner, modules, shards, serial, timings, coverage_dir):
    lanes = runner.lanes
    t0 = time.monotonic()
    limits = []
    if runner.deadline:
        limits.append(f"deadline {runner.deadline}s")
    if runner.heartbeat:
        limits.append(f"heartbeat every {runner.heartbeat}s")
    runner.say(f"run_backend_shards: {len(modules)} modules, {len(shards)} shards"
               f" (+{len(serial)} serial-only), logs in {runner.log_dir}"
               + (f" ({', '.join(limits)})" if limits else ""))
    runner.run_shards(shards, serial)

    wall = time.monotonic() - t0
    reason = runner.reason
    ran = {m for lane in lanes for m in lane.reports}
    missing = sorted(set(modules) - ran)
    reports = [lane.reports[m] for lane in lanes for m in lane.modules if m in lane.reports]
    merged = merge_reports(reports)
    died = {m: why for lane in lanes for m, why in lane.causes.items()}
    interrupted = sorted(m for lane in lanes for m in lane.interrupted)
    crashed = sorted(m for lane in lanes for m, r in lane.reports.items() if r is None)

    if reason:
        # A stopped run: the reason and the per-file verdicts first, the full lane logs stay on disk.
        print(f"\nrun_backend_shards: run stopped ({reason}); per-lane logs in {runner.log_dir}")
    else:
        _print_lane_logs(lanes)
    _print_failures(lanes, died, interrupted, missing, reason)
    coverage_ok = True
    if coverage_dir and not reason:
        coverage_ok = _print_coverage(coverage_dir)
    _write_timings(args, lanes, len(shards), timings, not (crashed or missing), stopped=bool(reason))

    ok = (merged is not None and coverage_ok and not crashed and not missing
          and not reason
          and merged["failures"] == 0 and merged["errors"] == 0
          and merged["unexpected_successes"] == 0
          and all(c == 0 for lane in lanes for c in lane.exits.values()))
    verdict = _write_report_and_verdict(args, merged, lanes, len(shards), len(serial), wall, ok,
                                        reason)
    sys.stdout.flush()
    if runner.progress is not None:
        try:
            print(f"run_backend_shards: {verdict} in {_clock(wall)}; per-lane logs in "
                  f"{runner.log_dir}", file=runner.progress, flush=True)
        except (OSError, ValueError):
            pass
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", help="merged result report (serial schema)")
    parser.add_argument("--shards", type=int,
                        default=int(os.environ.get("TURNOS_BACKEND_SHARDS", DEFAULT_SHARDS)))
    parser.add_argument("--timings", default=os.environ.get("TURNOS_BACKEND_TIMINGS",
                                                            str(SEED_TIMINGS)),
                        help="per-file seconds used for balancing")
    parser.add_argument("--timings-out", help="write this run's per-file seconds here")
    parser.add_argument("--update-seed", action="store_true",
                        help="fold this run's timings into scripts/backend_test_timings.json")
    parser.add_argument("--seed-out",
                        help="write the seed this run would give (the old one with this run's "
                             "seconds folded in) here; CI keeps it as the artifact "
                             "backend-timings-seed.json, copy it over scripts/backend_test_timings.json")
    parser.add_argument("--log-dir", help="keep per-shard logs here")
    parser.add_argument("--coverage-dir",
                        help="run every file under coverage.py (branch mode) and leave the "
                             "combined data and the per-package summary here")
    parser.add_argument("--start-directory", default="tests")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--plan", action="store_true",
                        help="print the shard plan and exit")
    parser.add_argument("--deadline", type=int,
                        default=int(os.environ.get("TURNOS_BACKEND_DEADLINE",
                                                   DEFAULT_DEADLINE_SECONDS)),
                        help="stop every lane after this many seconds and say what was running "
                             "(0 = no deadline)")
    parser.add_argument("--heartbeat", type=int,
                        default=int(os.environ.get("TURNOS_BACKEND_HEARTBEAT",
                                                   DEFAULT_HEARTBEAT_SECONDS)),
                        help="print where every lane is this often, in seconds (0 = never)")
    parser.add_argument("--progress-fd", type=int,
                        help="also write the announcements, heartbeats and verdict to this "
                             "open file descriptor")
    args = parser.parse_args(argv)
    args.deadline = max(0, args.deadline)
    args.heartbeat = max(0, args.heartbeat)
    if not args.plan and not args.report:
        parser.error("--report is required")
    args.shards = max(1, min(args.shards, MAX_SHARDS))
    return run(args)


if __name__ == "__main__":
    # Line-buffered even into a file (ci_local.sh: `>backend.log`): what was printed before a
    # SIGKILL is in the log, not in a lost block buffer.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(line_buffering=True)
    raise SystemExit(main())
