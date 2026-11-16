#!/usr/bin/env python3
"""subject_requests.py — the ledger of data-subject requests (Ley 25.326 arts. 14-16).

    python3 scripts/subject_requests.py add access --dni 20111222 --by <name> \\
        [--received 2026-10-23] [--channel presencial|email|nota]
    python3 scripts/subject_requests.py identity <id> --how "DNI shown in person" --by <name>
    python3 scripts/subject_requests.py review <id> --by <name>            # rectification: flag «under review»
    python3 scripts/subject_requests.py answer <id> --outcome "copy handed over" --by <name>
    python3 scripts/subject_requests.py refuse <id> --basis "Ley 25.326 art. 16 inc. 5: ..." --by <name>
    python3 scripts/subject_requests.py note <id> --text "..." --by <name>
    python3 scripts/subject_requests.py show <id>
    python3 scripts/subject_requests.py list [--overdue]

Deadlines: access = received + 10 calendar days; rectification / erasure =
received + 5 working days (weekends and the installation's holidays skipped).
The person is stored as an HMAC of the DNI; output shows the first 8 characters
of it, never a DNI. Exit code 1 when `list --overdue` finds something.
"""
import argparse
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import subject_requests  # noqa: E402


def _line(r, today):
    flag = " OVERDUE" if r["status"] in subject_requests.OPEN and r["due_on"] < today else ""
    return (f"#{r['id']} {r['kind']:<13} {r['status']:<12} received {r['received_on']} "
            f"due {r['due_on']} subject {r['subject_hash'][:8]}{flag}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("kind", choices=subject_requests.KINDS)
    add.add_argument("--dni", required=True)
    add.add_argument("--by", required=True)
    add.add_argument("--received", type=date.fromisoformat)
    add.add_argument("--channel", default="")
    for name in ("identity", "review", "answer", "refuse", "note", "show"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        if name != "show":
            p.add_argument("--by", required=True)
    sub.choices["identity"].add_argument("--how", required=True)
    sub.choices["answer"].add_argument("--outcome", required=True)
    sub.choices["refuse"].add_argument("--basis", required=True)
    sub.choices["note"].add_argument("--text", required=True)
    lst = sub.add_parser("list")
    lst.add_argument("--overdue", action="store_true")
    args = ap.parse_args(argv)
    today = subject_requests.today()
    try:
        if args.cmd == "add":
            r = subject_requests.register(args.kind, args.dni, args.by, args.received, args.channel)
            print(_line(r, today))
        elif args.cmd == "identity":
            subject_requests.set_identity_check(args.id, args.how, args.by)
        elif args.cmd == "review":
            subject_requests.start_review(args.id, args.by)
        elif args.cmd == "answer":
            subject_requests.answer(args.id, args.outcome, args.by)
        elif args.cmd == "refuse":
            subject_requests.refuse(args.id, args.basis, args.by)
        elif args.cmd == "note":
            subject_requests.note(args.id, args.text, args.by)
        elif args.cmd == "show":
            r = subject_requests.get(args.id)
            print(_line(r, today))
            print(f"channel: {r['channel'] or '-'}; identity: {r['identity_check'] or 'NOT VERIFIED'}; "
                  f"outcome: {r['outcome'] or '-'}")
            for e in r["events"]:
                print(f"  {e['ts']:%Y-%m-%d %H:%M} {e['actor']}: {e['event']} {e['detail']}")
        elif args.cmd == "list":
            rows = subject_requests.overdue(today) if args.overdue else subject_requests.list_open()
            for r in rows:
                print(_line(r, today))
            if args.overdue and rows:
                return 1
    except subject_requests.RequestError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
