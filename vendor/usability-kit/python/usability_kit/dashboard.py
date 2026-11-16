#!/usr/bin/env python3
"""Self-contained HTML dashboard over lite-mode usability runs.

    python3 -m usability_kit.dashboard RUNS_ROOT [--out dashboard.html]

Reads every result.json under RUNS_ROOT (any depth — one per case), deduplicates
findings (same title + category across cases = one problem row) and writes one
dashboard.html: overall status (red if any blocker, amber if any major, else
green), counts per severity, "Worst problems" with expandable evidence, and a
per-case table (role, task, verdict, SEQ, actions, simulated time, probes).
Inline CSS, no JavaScript, no external requests except Google Fonts; light and
dark themes via prefers-color-scheme; readable at 390px. Cases are grouped by
study name when the runs root mixes studies.
"""
import argparse, html, json, os, sys, time
from pathlib import Path

SEVS = ("blocker", "major", "minor", "cosmetic")
SEV_RANK = {"blocker": 0, "major": 1, "minor": 2, "cosmetic": 3}
HEADS = ("Case", "Role", "Task", "Verdict", "SEQ", "Actions", "Time (s)", "Probes")

CSS = """
:root{--bg:#f5f6f8;--card:#ffffff;--ink:#171a1f;--muted:#59626d;--line:#dfe3e8;
 --red:#c62f2f;--amber:#b26a00;--green:#1e7d43;
 --blocker:#c62f2f;--major:#d97f00;--minor:#a08600;--cosmetic:#6d7580;
 --redbg:#fdecec;--amberbg:#fdf3e3;--greenbg:#e8f5ee}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#101317;--card:#191d23;--ink:#e9ecef;--muted:#9ba4af;--line:#2a2f37;
 --red:#ff8080;--amber:#ffb861;--green:#7fd6a0;
 --blocker:#ff8080;--major:#ffb861;--minor:#e3c65b;--cosmetic:#9ba4af;
 --redbg:#3a1d1d;--amberbg:#3a2c14;--greenbg:#15301f;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#101317;--card:#191d23;--ink:#e9ecef;--muted:#9ba4af;--line:#2a2f37;
 --red:#ff8080;--amber:#ffb861;--green:#7fd6a0;
 --blocker:#ff8080;--major:#ffb861;--minor:#e3c65b;--cosmetic:#9ba4af;
 --redbg:#3a1d1d;--amberbg:#3a2c14;--greenbg:#15301f;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;text-wrap:pretty;background:var(--bg);color:var(--ink);font:16px/1.5 "Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:980px;margin:0 auto;padding:16px 16px 56px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:20px;margin:30px 0 10px}
.top{background:var(--card);border:1.5px solid var(--line);border-radius:12px;padding:14px 16px;margin-top:14px}
.top.red{border-color:var(--red);background:var(--redbg)}
.top.amber{border-color:var(--amber);background:var(--amberbg)}
.top.green{border-color:var(--green);background:var(--greenbg)}
.statusword{font-size:19px;font-weight:800;margin:2px 0 10px}
.top.red .statusword{color:var(--red)}.top.amber .statusword{color:var(--amber)}.top.green .statusword{color:var(--green)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 8px}
.chip{border-radius:999px;padding:3px 12px;font-weight:700;font-size:14px;border:1.5px solid var(--line);background:var(--card)}
.chip.blocker{color:var(--blocker);border-color:var(--blocker)}
.chip.major{color:var(--major);border-color:var(--major)}
.chip.minor{color:var(--minor);border-color:var(--minor)}
.chip.cosmetic{color:var(--cosmetic);border-color:var(--cosmetic)}
.sub{color:var(--muted);margin:0;font-size:14px}
.prob{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}
.prob summary{list-style:none;cursor:pointer;padding:12px 14px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.prob summary::-webkit-details-marker{display:none}
.prob summary::before{content:"\\25B8";color:var(--muted)}
.prob[open] summary::before{content:"\\25BE"}
.badge{font-size:14px;font-weight:800;text-transform:uppercase;letter-spacing:.03em;padding:2px 8px;border-radius:6px;border:1.5px solid currentColor}
.badge.blocker{color:var(--blocker)}.badge.major{color:var(--major)}.badge.minor{color:var(--minor)}.badge.cosmetic{color:var(--cosmetic)}
.ptitle{font-weight:700;flex:1;min-width:180px}
.count{color:var(--muted);font-weight:700;font-size:14px}
@media (max-width:560px){.prob summary{display:grid;grid-template-columns:auto 1fr auto;row-gap:6px}.prob summary .ptitle{grid-column:1/-1;grid-row:2;min-width:0}}
.pbody{padding:2px 16px 12px;border-top:1px dashed var(--line)}
.fix{padding-top:10px}
.meta{color:var(--muted);font-size:14px;margin-top:6px}
.evidence{margin:8px 0 0;padding-left:20px}
.evidence li{margin:8px 0}
.evidence .case{font-weight:700}
.evidence q{font-style:italic}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}
th,td{padding:10px 10px;text-align:left;border-bottom:1px solid var(--line);font-size:15px;vertical-align:top}
th{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
tr:last-child td{border-bottom:0}
td.task{color:var(--muted);max-width:260px}
.v-done{color:var(--green);font-weight:800}.v-giveup{color:var(--red);font-weight:800}.v-other{color:var(--muted);font-weight:800}
.pass{color:var(--green);font-weight:700}.fail{color:var(--red);font-weight:700}
@media (max-width:560px){
thead{display:none}
table,tbody,tr,td{display:block;width:100%}
table{border:1px solid var(--line);border-radius:10px;overflow:hidden}
tr{border-bottom:1px solid var(--line);padding:6px 0}
tr:last-child{border-bottom:0}
td{border:0;padding:4px 14px}
td::before{content:attr(data-label) ": ";color:var(--muted);font-weight:700;font-size:14px}
}
"""


def esc(x):
    return html.escape(str(x if x is not None else ""), quote=True)


def load_results(root):
    rows = []
    for p in sorted(root.rglob("result.json")):
        try:
            j = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(j, dict) and j.get("case"):
            rows.append(j)
    return rows


def problems(results):
    """Deduplicate findings: same (title, category) → one row, most severe severity wins."""
    out = {}
    for r in results:
        for f in r.get("findings") or []:
            if not isinstance(f, dict):
                continue
            key = (str(f.get("title") or "").strip().lower(), str(f.get("category") or "").lower())
            row = out.setdefault(key, {"title": str(f.get("title") or "").strip() or "Untitled finding",
                                       "severity": "minor", "category": str(f.get("category") or ""),
                                       "fix": str(f.get("suggested_fix") or ""), "occ": []})
            if SEV_RANK.get(f.get("severity"), 3) < SEV_RANK.get(row["severity"], 3):
                row["severity"] = f.get("severity")
            if not row["fix"] and f.get("suggested_fix"):
                row["fix"] = str(f.get("suggested_fix"))
            row["occ"].append({"case": r.get("case") or "?", "study": r.get("study") or "",
                               "steps": f.get("evidence_steps") or [], "quote": str(f.get("quote") or "")})
    rows = list(out.values())
    rows.sort(key=lambda p: (SEV_RANK.get(p["severity"], 3), -len(p["occ"]), p["title"].lower()))
    return rows


def status(probs):
    sevs = {p["severity"] for p in probs}
    return "red" if "blocker" in sevs else "amber" if "major" in sevs else "green"


def problem_html(p, mixed):
    cases = sorted({(o["study"] + "/" + o["case"]) if mixed else o["case"] for o in p["occ"]})
    ev = "".join(
        f'<li><span class="case">{esc((o["study"] + "/" + o["case"]) if mixed else o["case"])}</span>'
        f' &middot; steps {esc(", ".join(str(s) for s in o["steps"]) or "—")}'
        + (f'<br><q>{esc(o["quote"].strip().strip(chr(34) + "“”«»").strip())}</q>' if o["quote"] else "") + "</li>"
        for o in p["occ"])
    return (f'<details class="prob"{" open" if p["severity"] == "blocker" else ""}>'
            f'<summary><span class="badge {esc(p["severity"])}">{esc(p["severity"])}</span>'
            f'<span class="ptitle">{esc(p["title"])}</span>'
            f'<span class="count">&times; {len(p["occ"])}</span></summary>'
            f'<div class="pbody"><div class="fix"><strong>Suggested fix:</strong> {esc(p["fix"]) or "&mdash;"}</div>'
            f'<div class="meta">category: {esc(p["category"]) or "—"} &middot; {len(cases)} case(s): {esc(", ".join(cases))}</div>'
            f'<ul class="evidence">{ev}</ul></div></details>')


def case_table(rows):
    body = []
    for r in sorted(rows, key=lambda r: str(r.get("case") or "")):
        verdict = str(r.get("verdict") or "—")
        vclass = "v-done" if verdict == "done" else "v-giveup" if verdict in ("giveup", "tool-failure") else "v-other"
        probes = [p for p in (r.get("probes") or []) if isinstance(p, dict)]
        pcell = "<br>".join(f'<span class="{"pass" if p.get("passed") else "fail"}">{"✓" if p.get("passed") else "✗"} {esc(p.get("name"))}</span>'
                            for p in probes) or "—"
        role = f"{r.get('persona') or '?'} ({r.get('device') or '?'})"
        seq = "—" if r.get("seq") is None else r.get("seq")
        cells = (esc(r.get("case")), esc(role), esc(r.get("task")), f'<span class="{vclass}">{esc(verdict)}</span>',
                 esc(seq), esc(r.get("action_count", "—")), esc(r.get("simulated_seconds", "—")), pcell)
        body.append("<tr>" + "".join(f'<td data-label="{h}"{" class=\"task\"" if h == "Task" else ""}>{c}</td>'
                                     for h, c in zip(HEADS, cells)) + "</tr>")
    head = "<thead><tr>" + "".join(f"<th>{h}</th>" for h in HEADS) + "</tr></thead>"
    return f"<table>{head}<tbody>{''.join(body)}</tbody></table>"


def render(results, title="Usability runs"):
    probs = problems(results)
    st = status(probs)
    word = {"red": "Blockers found — fix before shipping", "amber": "Major problems found", "green": "No blockers or major problems"}[st]
    counts = {s: sum(1 for p in probs if p["severity"] == s) for s in SEVS}
    chips = "".join(f'<span class="chip {s}">{counts[s]} {s}{"" if counts[s] == 1 else "s"}</span>' for s in SEVS if counts[s])
    studies = {}
    for r in results:
        studies.setdefault(str(r.get("study") or "unnamed study"), []).append(r)
    mixed = len(studies) > 1
    cases_lbl = f"{len(results)} case{'s' if len(results) != 1 else ''} · {len(studies)} stud{'ies' if mixed else 'y'}"
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>{esc(title)}</title>",
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">",
        "<link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@400;700;800&display=swap\" rel=\"stylesheet\">",
        f"<style>{CSS}</style></head><body><main>",
        f'<header class="top {st}"><h1>{esc(title)}</h1>',
        f'<p class="statusword">{esc(word)}</p>',
        f'<div class="chips">{chips or "<span class=\"chip\">no findings</span>"}</div>',
        f'<p class="sub">{esc(cases_lbl)} · generated {time.strftime("%Y-%m-%d %H:%M")} · simulation evidence, not user findings</p></header>',
        "<h2>Worst problems</h2>",
        "".join(problem_html(p, mixed) for p in probs) or '<p class="sub">No findings were reported.</p>',
    ]
    if mixed:
        for sname, rows in sorted(studies.items()):
            parts.append(f"<h2>{esc(sname)}</h2>{case_table(rows)}")
    else:
        parts.append(f"<h2>Cases</h2>{case_table(results)}")
    parts.append("</main></body></html>\n")
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser(description="render one dashboard.html over result.json files")
    ap.add_argument("runs_root", help="directory to scan recursively for result.json files")
    ap.add_argument("--out", default=None, help="output path (default: RUNS_ROOT/dashboard.html)")
    ap.add_argument("--title", default="Usability runs", help="page title and heading")
    args = ap.parse_args()
    root = Path(os.path.expanduser(args.runs_root))
    results = load_results(root)
    if not results:
        print(f"no result.json found under {root}", file=sys.stderr)
        sys.exit(1)
    out = Path(args.out) if args.out else root / "dashboard.html"
    out.write_text(render(results, args.title), encoding="utf-8")
    probs = problems(results)
    print(f"wrote {out} — {len(results)} cases, {len(probs)} distinct problems, status {status(probs)}")


if __name__ == "__main__":
    main()
