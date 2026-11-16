#!/usr/bin/env python3
"""Refresh scripts/backend_test_timings.json (the seed the shard planner balances by) from CI.

The sharded backend job (scripts/run_backend_shards.py) writes the seconds every test file took
as the CI artifact `backend-timings.json`. The seed is only as good as it is recent: a stale one
puts the slow files on the same lanes and the job runs as long as its worst lane. One command
refreshes it from the real runners:

    python3 scripts/update_backend_timings.py                 # the last 5 green `main` pipelines
    python3 scripts/update_backend_timings.py --pipelines 8   # more of them
    python3 scripts/update_backend_timings.py --since <sha>   # only pipelines of <sha> and what came after
    python3 scripts/update_backend_timings.py --pipeline 183442 --pipeline 183372   # exactly these
    python3 scripts/update_backend_timings.py a.json b.json   # artifacts already downloaded

Reading the pipelines needs the GitLab CLI (`glab`, logged in to the host of the `gitlab` remote).
The seed gets, per file, the MEDIAN of the runs (one run on a busy runner is noise); a file that no
run measured keeps its old value; a file that no longer exists is dropped; a new file that none
measured stays out (the planner weighs it by size). After a change that makes files much faster or
slower, `--since <that commit>` keeps the runs from before it out of the median (otherwise they
outvote the first runs after it). `--assume FILE=SECONDS` sets a value by hand when no run can
have measured it yet: the command that produced a seed is then the whole record of it. The script prints the slowest lane the old and
the new seed would give on each run it read, then writes the file (`--dry-run` only prints).
Commit the result on its own: `git add scripts/backend_test_timings.json`.
"""
import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_backend_shards as shards  # noqa: E402

JOB = "backend"
ARTIFACT = "backend-timings.json"
DEFAULT_PIPELINES = 5


def merge_timings(runs, previous, modules):
    """The new seed: median seconds per file over `runs`; an unmeasured file keeps `previous`;
    only files that exist (`modules`) are kept."""
    wanted = set(modules)
    merged = {}
    for module in sorted(wanted):
        seen = [run[module] for run in runs if module in run]
        if seen:
            merged[module] = round(statistics.median(seen), 2)
        elif module in previous:
            merged[module] = previous[module]
    return merged


def slowest_lane(seed, run, modules, lanes):
    """(slowest lane, even split) in seconds when `seed` plans the lanes and `run` is what the
    files really took."""
    buckets, serial, _ = shards.plan_shards(modules, seed, lanes)
    real = [sum(run.get(m, 0.0) for m in bucket) for bucket in buckets]
    return max(real), sum(run.get(m, 0.0) for m in modules if m not in serial) / len(buckets)


def read_timings(path):
    return shards.load_timings(path)


def _glab(args, host, binary=False):
    done = subprocess.run(["glab", "api", *args, "--hostname", host], capture_output=True, check=True)
    return done.stdout if binary else json.loads(done.stdout)


def parse_remote(url):
    """(host, path without `.git`) of an ssh (`git@host:group/repo.git`) or https remote URL."""
    if "://" in url:
        host, _, path = url.split("://", 1)[1].split("@", 1)[-1].partition("/")
    else:
        host, _, path = url.split("@", 1)[-1].partition(":")
    return host, path.removesuffix(".git")


def remote_project():
    """(host, URL-encoded project path) of the `gitlab` remote, else `origin`."""
    for name in ("gitlab", "origin"):
        done = subprocess.run(["git", "remote", "get-url", name], capture_output=True, text=True,
                              cwd=shards.REPO)
        url = done.stdout.strip()
        if done.returncode == 0 and "gitlab" in url:
            host, path = parse_remote(url)
            return host, quote(path, safe="")
    raise SystemExit("update_backend_timings: no GitLab remote; pass the artifact files instead")


def is_descendant(ref, sha):
    """True when `sha` is `ref` or contains it in its history; None when git does not know one of them."""
    done = subprocess.run(["git", "merge-base", "--is-ancestor", ref, sha], capture_output=True,
                          cwd=shards.REPO)
    return {0: True, 1: False}.get(done.returncode)


def fetch_from_gitlab(count, ref="main", since=None, ids=()):
    """The `backend-timings.json` of the last `count` green pipelines of `ref` (those from `since` on,
    or exactly the pipelines `ids`): [(pipeline, dict)]."""
    host, project = remote_project()
    if ids:
        pipelines = [{"id": i, "sha": ""} for i in ids]
    else:
        pipelines = _glab([f"projects/{project}/pipelines?ref={ref}&status=success&per_page=100"], host)
    found = []
    for pipeline in pipelines:
        if len(found) >= count and not ids:
            break
        if since:
            verdict = is_descendant(since, pipeline["sha"])
            if verdict is None:
                print(f"  pipeline {pipeline['id']}: {pipeline['sha'][:8]} or {since} unknown here "
                      "(git fetch first); skipped", file=sys.stderr)
            if not verdict:
                continue
        jobs = _glab([f"projects/{project}/pipelines/{pipeline['id']}/jobs?per_page=100"], host)
        job = next((j for j in jobs if j["name"] == JOB and j["status"] == "success"), None)
        if job is None:
            continue
        try:
            raw = _glab([f"projects/{project}/jobs/{job['id']}/artifacts/{ARTIFACT}"], host, binary=True)
            modules = json.loads(raw).get("modules")
        except (subprocess.CalledProcessError, ValueError):
            print(f"  pipeline {pipeline['id']}: no {ARTIFACT} (expired?)", file=sys.stderr)
            continue
        if isinstance(modules, dict):
            found.append((str(pipeline["id"]), {k: float(v) for k, v in modules.items()}))
    return found


def parse_assumptions(items, modules):
    """`NAME=SECONDS` pairs; a name that is not a test file is a typo, not a value to keep."""
    out = {}
    for item in items:
        name, _, seconds = item.partition("=")
        name = name.removesuffix(".py")
        if name not in modules or not seconds.replace(".", "", 1).isdigit():
            raise SystemExit(f"update_backend_timings: --assume {item!r}: want an existing FILE=SECONDS")
        out[name] = float(seconds)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", help="backend-timings.json artifacts (default: fetch from GitLab)")
    parser.add_argument("--pipelines", type=int, default=DEFAULT_PIPELINES,
                        help="how many green pipelines of main to read (default %(default)s)")
    parser.add_argument("--pipeline", action="append", default=[], metavar="ID",
                        help="read exactly this pipeline (repeatable) instead of the latest green ones")
    parser.add_argument("--since", metavar="SHA",
                        help="read only pipelines of this commit and the ones after it")
    parser.add_argument("--assume", action="append", default=[], metavar="FILE=SECONDS",
                        help="set a file's seconds by hand (repeatable)")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--shards", type=int, default=shards.DEFAULT_SHARDS)
    parser.add_argument("--dry-run", action="store_true", help="print, do not write the seed")
    args = parser.parse_args(argv)

    if args.files:
        runs = [(Path(f).name, read_timings(f)) for f in args.files]
    else:
        runs = fetch_from_gitlab(args.pipelines, args.ref, args.since, args.pipeline)
    runs = [(name, run) for name, run in runs if run]
    if not runs:
        print("update_backend_timings: no timings to read", file=sys.stderr)
        return 1
    modules = shards.discover_modules()
    assumed = parse_assumptions(args.assume, modules)
    previous = shards.load_timings(shards.SEED_TIMINGS)
    seed = merge_timings([run for _, run in runs], previous, modules)
    seed.update(assumed)

    print(f"{len(runs)} run(s), {len(seed)} of {len(modules)} files in the new seed "
          f"(was {len(previous)}; dropped {len(set(previous) - set(modules))} that no longer exist"
          f"{f'; {len(assumed)} set by --assume' if assumed else ''})")
    print(f"slowest of {args.shards} lanes, in seconds (the even split is the floor)"
          + ("; --assume files are weighed at what the runs measured" if assumed else "") + ":")
    print(f"  {'run':<28}{'old seed':>10}{'new seed':>10}{'even split':>12}")
    for name, run in runs:
        old, ideal = slowest_lane(previous, run, modules, args.shards)
        new, _ = slowest_lane(seed, run, modules, args.shards)
        print(f"  {name:<28}{old:>10.0f}{new:>10.0f}{ideal:>12.0f}")
    if args.dry_run:
        return 0
    shards.SEED_TIMINGS.write_text(json.dumps(
        {"schema_version": shards.SCHEMA_VERSION, "modules": dict(sorted(seed.items()))},
        indent=1) + "\n", encoding="utf-8")
    print(f"wrote {shards.SEED_TIMINGS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
