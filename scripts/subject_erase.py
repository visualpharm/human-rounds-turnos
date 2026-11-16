#!/usr/bin/env python3
"""subject_erase.py — erase / anonymise one person's data (Ley 25.326 art. 16).

DRY-RUN BY DEFAULT: without --apply it only prints what would happen, counts per
place, and writes nothing.

    python3 scripts/subject_erase.py --dni 20111222 [--phone …] [--email …] [--confirm-shared] \\
        [--mode anonymise|block]                                   # the plan
    python3 scripts/subject_erase.py --dni 20111222 --mode anonymise --apply \\
        --request <ledger id of the ERASURE request> --authorised-by "<who / which counsel answer>" \\
        [--operator <name>]

--apply needs a ledger request (scripts/subject_requests.py add erasure …) of THIS
person whose identity was verified, and a named authorisation. Refused, with
nothing written, while the person has live appointments, an HSI intent still
pending or an unresolved sync conflict. Modes: `anonymise` (recommended) moves the
records that may be the care record to a random key and empties their personal
fields; `block` deactivates the account and keeps the records under the person's
DNI. The clinical access log is never touched. See
docs/architecture/derechos-de-los-titulares.md. Prints counts only, never a name,
a DNI or a phone.
"""
import argparse
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import subject_erase, subject_export  # noqa: E402


def _print_plan(plan):
    print(f"mode: {plan['mode']}")
    for s in plan["steps"]:
        mark = " (depends on counsel)" if s["disputed"] and s["action"] != "keep" else ""
        print(f"  {s['action']:<9} {s['entry']:<34} {s['rows']:>5} rows{mark}")
    print(f"  files: {plan['files']['referrals']} referral file(s), "
          f"{plan['files']['profile_photo']} profile photo(s)")
    for note in plan["notes"]:
        print("note:", note)
    for b in plan["blockers"]:
        print("BLOCKED:", b)
    print("not erased by this tool (ask them, or wait for rotation):")
    for n in plan["not_erased"]:
        print(f"  - {n['what']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dni", required=True)
    ap.add_argument("--phone")
    ap.add_argument("--email")
    ap.add_argument("--confirm-shared", action="store_true")
    ap.add_argument("--mode", choices=subject_erase.MODES, default="anonymise")
    ap.add_argument("--apply", action="store_true", help="write (the default is a dry-run)")
    ap.add_argument("--request", type=int, help="ledger id of the erasure request (needed to apply)")
    ap.add_argument("--authorised-by", default="", help="who allows it (needed to apply)")
    ap.add_argument("--operator", default=getpass.getuser())
    args = ap.parse_args(argv)
    try:
        subject = subject_export.resolve_subject(args.dni, args.phone, args.email,
                                                 confirm_shared=args.confirm_shared)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.apply:
        _print_plan(subject_erase.plan(subject, args.mode))
        print("dry-run: nothing written. Repeat with --apply --request … --authorised-by …")
        return 0
    if not args.request:
        print("refused: --apply needs --request <ledger id of the erasure request>", file=sys.stderr)
        return 2
    try:
        report = subject_erase.apply(subject, args.mode, args.request, args.authorised_by,
                                     args.operator)
    except subject_erase.ErasureRefused as exc:
        for reason in exc.reasons:
            print(f"refused: {reason}", file=sys.stderr)
        return 2
    for s in report["steps"]:
        if s["rows"]:
            print(f"  {s['action']:<9} {s['entry']:<34} {s['rows']:>5} rows")
    print(f"files: {report['files']['removed']} removed, {len(report['files']['failed'])} failed")
    print("done. Answer the request (scripts/subject_requests.py answer …) and tell the person "
          "what is kept and why; copies in encrypted backups expire by rotation.")
    return 1 if report["files"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
