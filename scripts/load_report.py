#!/usr/bin/env python3
"""load_report.py — turn `scripts/load_hot_paths.py` results into a Markdown table and an SVG chart.

    python3 scripts/load_report.py --svg docs/architecture/capacity-load.svg \\
        --stall-svg docs/architecture/capacity-stall.svg run-a.json run-b.json ...

One line per input file (its `label`), three panels over the concurrency levels: throughput,
95th-percentile latency, and the requests answered 503 («database unavailable»). Standard
library only; the SVG is hand-written so the repo needs no plotting package.
"""
import argparse
import json
import sys
from xml.sax.saxutils import escape

# Okabe-Ito: distinguishable under the common colour-vision deficiencies.
PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000", "#F0E442"]
W, H = 980, 900
LEFT, RIGHT, PANEL_H, GAP = 70, 220, 190, 70


def load(paths):
    runs = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        runs.append({"label": data.get("label") or path,
                     "levels": {lv["concurrency"]: lv for lv in data["levels"]}})
    return runs


def table(runs):
    levels = sorted({c for run in runs for c in run["levels"]})
    out = []
    for run in runs:
        out.append("**%s**\n" % run["label"])
        out.append("| clients | req/s | p50 ms | p95 ms | p99 ms | 503 | other 5xx/errors | DB connections (max) |")
        out.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for c in levels:
            lv = run["levels"].get(c)
            if not lv:
                continue
            a = lv["all"]
            other = a["server_errors"] - a["unavailable_503"]
            out.append("| %d | %.0f | %.0f | %.0f | %.0f | %d | %d | %s |" % (
                c, a["rps"], a["p50_ms"] or 0, a["p95_ms"] or 0, a["p99_ms"] or 0,
                a["unavailable_503"], other, lv.get("postgres", {}).get("connections_max", "-")))
        out.append("")
    return "\n".join(out)


def nice_ceiling(top):
    """Smallest 1/2/4/5/10 x 10^k at or above `top` (so four grid steps are round numbers)."""
    magnitude = 1.0
    while magnitude * 10 <= top:
        magnitude *= 10
    while magnitude > top * 10:
        magnitude /= 10
    for step in (1, 2, 4, 5, 10):
        if magnitude * step >= top:
            return magnitude * step
    return magnitude * 10


def panel(runs, levels, top, title, unit, value):
    values = [value(run["levels"][c]) for run in runs for c in levels if c in run["levels"]]
    ymax = nice_ceiling(max(values + [1.0]))
    x0, x1 = LEFT, W - RIGHT
    y0, y1 = top + PANEL_H, top
    px = lambda i: x0 + (x1 - x0) * (i / max(1, len(levels) - 1))  # noqa: E731
    py = lambda v: y0 - (y0 - y1) * (v / ymax)  # noqa: E731
    parts = ['<text x="%d" y="%d" font-size="15" font-weight="600">%s</text>' % (LEFT, top - 14, escape(title))]
    for k in range(5):
        v = ymax * k / 4
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e5e5e5"/>' % (x0, py(v), x1, py(v)))
        parts.append('<text x="%d" y="%.1f" font-size="12" text-anchor="end" fill="#555">%g</text>' % (
            x0 - 6, py(v) + 4, round(v, 2)))
    for i, c in enumerate(levels):
        parts.append('<text x="%.1f" y="%d" font-size="12" text-anchor="middle" fill="#555">%d</text>' % (px(i), y0 + 16, c))
    parts.append('<text x="%d" y="%d" font-size="12" fill="#555">%s</text>' % (x0, y0 + 32, escape(unit)))
    for n, run in enumerate(runs):
        color = PALETTE[n % len(PALETTE)]
        pts = [(px(i), py(value(run["levels"][c]))) for i, c in enumerate(levels) if c in run["levels"]]
        if len(pts) > 1:
            parts.append('<polyline fill="none" stroke="%s" stroke-width="2.2" points="%s"/>' % (
                color, " ".join("%.1f,%.1f" % p for p in pts)))
        for x, y in pts:
            parts.append('<circle cx="%.1f" cy="%.1f" r="3.2" fill="%s"/>' % (x, y, color))
    return "\n".join(parts)


def svg(runs):
    levels = sorted({c for run in runs for c in run["levels"]})
    body = []
    specs = [
        ("Throughput", "requests per second, by concurrent clients", lambda lv: lv["all"]["rps"]),
        ("95th-percentile latency", "milliseconds, by concurrent clients", lambda lv: lv["all"]["p95_ms"] or 0),
        ("Requests answered 503 (database unavailable)", "count in the 20 s window, by concurrent clients",
         lambda lv: lv["all"]["unavailable_503"]),
    ]
    for n, (title, unit, value) in enumerate(specs):
        body.append(panel(runs, levels, 60 + n * (PANEL_H + GAP), title, unit, value))
    for n, run in enumerate(runs):
        y = 74 + n * 22
        body.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="3"/>' % (
            W - RIGHT + 24, y, W - RIGHT + 48, y, PALETTE[n % len(PALETTE)]))
        body.append('<text x="%d" y="%d" font-size="12">%s</text>' % (W - RIGHT + 54, y + 4, escape(run["label"])))
    height = 60 + 3 * (PANEL_H + GAP)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'font-family="Helvetica, Arial, sans-serif"><rect width="100%%" height="100%%" fill="#fff"/>'
            '<text x="%d" y="26" font-size="18" font-weight="700">Hot paths under growing concurrency '
            '(local demo install, one uvicorn process)</text>\n%s\n</svg>\n') % (W, height, W, height, LEFT, "\n".join(body))


def stall_svg(runs):
    """One small stacked-bar panel per stalled run: answers per second (ok / 503 / other failure), with
    the window in which the lock was held shaded. Returns None when no run carries a timeline."""
    stalled = [(run, next(lv for lv in run["levels"].values() if lv.get("timeline")))
               for run in runs if any(lv.get("timeline") for lv in run["levels"].values())]
    if not stalled:
        return None
    seconds = max(len(lv["timeline"]) and lv["timeline"][-1]["second"] + 1 for _, lv in stalled)
    ymax = nice_ceiling(max(b["ok"] + b["unavailable_503"] + b["failed"]
                            for _, lv in stalled for b in lv["timeline"]))
    x0, x1, row = LEFT, W - 40, 150
    barw = (x1 - x0) / max(1, seconds)
    parts = []
    for n, (run, lv) in enumerate(stalled):
        top = 96 + n * (row + 50)
        y0 = top + row
        stall = lv["stall"]
        parts.append('<text x="%d" y="%d" font-size="14" font-weight="600">%s — %d clients, table lock %gs → %gs</text>' % (
            x0, top - 12, escape(run["label"]), lv["concurrency"], stall["at_s"], stall["at_s"] + stall["for_s"]))
        parts.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="#f3e9d2"/>' % (
            x0 + barw * stall["at_s"], top, barw * stall["for_s"], row))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#999"/>' % (x0, y0, x1, y0))
        parts.append('<text x="%d" y="%d" font-size="11" text-anchor="end" fill="#555">%g</text>' % (x0 - 6, top + 10, ymax))
        for b in lv["timeline"]:
            x = x0 + barw * b["second"]
            y = y0
            for key, color in (("ok", "#009E73"), ("unavailable_503", "#D55E00"), ("failed", "#000000")):
                h = row * b[key] / ymax
                if h > 0:
                    y -= h
                    parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>' % (
                        x + 0.5, y, max(1.0, barw - 1), h, color))
        for s in range(0, seconds, 5):
            parts.append('<text x="%.1f" y="%d" font-size="11" text-anchor="middle" fill="#555">%ds</text>' % (
                x0 + barw * (s + 0.5), y0 + 14, s))
    legend_y = 46
    for k, (label, color) in enumerate((("answered", "#009E73"), ("503 database unavailable", "#D55E00"),
                                        ("other failure / timeout", "#000000"), ("lock held", "#f3e9d2"))):
        parts.append('<rect x="%d" y="%d" width="14" height="14" fill="%s" stroke="#bbb"/>' % (x0 + k * 210, legend_y - 11, color))
        parts.append('<text x="%d" y="%d" font-size="12">%s</text>' % (x0 + k * 210 + 20, legend_y, label))
    height = 96 + len(stalled) * (row + 50)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'font-family="Helvetica, Arial, sans-serif"><rect width="100%%" height="100%%" fill="#fff"/>'
            '<text x="%d" y="26" font-size="18" font-weight="700">Answers per second while the database stalls</text>\n%s\n</svg>\n'
            ) % (W, height, W, height, LEFT, "\n".join(parts))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("results", nargs="+")
    ap.add_argument("--svg", help="write the throughput/latency/503 chart here")
    ap.add_argument("--stall-svg", help="write the per-second chart of the runs made with --stall-table here")
    args = ap.parse_args(argv)
    runs = load(args.results)
    print(table(runs))
    if args.svg:
        with open(args.svg, "w", encoding="utf-8") as fh:
            fh.write(svg(runs))
        print("wrote", args.svg, file=sys.stderr)
    if args.stall_svg:
        chart = stall_svg(runs)
        if chart is None:
            print("no stalled run among the inputs", file=sys.stderr)
        else:
            with open(args.stall_svg, "w", encoding="utf-8") as fh:
                fh.write(chart)
            print("wrote", args.stall_svg, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
