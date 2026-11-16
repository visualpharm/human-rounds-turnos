#!/usr/bin/env python3
"""subject_export.py — the copy of one person's data (Ley 25.326 art. 14).

READ ONLY: nothing is written to the database. Writes one zip (JSON per
registry entry, the files on disk, and a manifest of everything searched).

    python3 scripts/subject_export.py --dni 20111222 [--phone 2255123456] \\
        [--email a@b.org] [--confirm-shared] [--out subject-export.zip] [--operator <name>] \\
        [--request <ledger id>]

DATABASE_URL (and the access-log key) come from the same environment the app
uses. The run is recorded in the clinical access log (action `exportacion`)
like every staff read of clinical data — a run whose row cannot be written does
not happen. Prints counts only, never a name, a DNI or a phone.

Identity check first (docs/architecture/derechos-de-los-titulares.md): hand the
zip to the registered email or in person, never to the first address given.
"""
import argparse
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import access_log, subject_export, subject_requests  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dni", required=True)
    ap.add_argument("--phone", help="defaults to the account's phone")
    ap.add_argument("--email", help="defaults to the account's email")
    ap.add_argument("--confirm-shared", action="store_true",
                    help="the phone/email that another account also uses IS this person's")
    ap.add_argument("--out", help="zip path (default: subject-export-<hash8>.zip in the cwd)")
    ap.add_argument("--operator", default=getpass.getuser(),
                    help="who runs it, for the access log")
    ap.add_argument("--request", type=int,
                    help="ledger id (scripts/subject_requests.py) of the access request this answers")
    args = ap.parse_args(argv)
    try:
        subject = subject_export.resolve_subject(args.dni, args.phone, args.email,
                                                 confirm_shared=args.confirm_shared)
        if args.request:
            ledger = subject_requests.get(args.request)
            if ledger["kind"] != "access" or ledger["subject_hash"] != subject.dni_hash:
                raise ValueError(f"request #{args.request} is not an access request of this person")
    except (ValueError, subject_requests.RequestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    identity = {"usuario": f"script:{args.operator}", "rol": "script"}
    try:
        access_log.record(identity, "exportacion", subject.dni)
    except access_log.AccessLogUnavailable as exc:
        print(f"refused: the access row could not be written ({exc})", file=sys.stderr)
        return 3
    out = args.out or f"subject-export-{subject.dni_hash[:8]}.zip"
    manifest = subject_export.export_to_path(subject, out)
    found = sum(s.get("found", 0) for s in manifest["searched"])
    if args.request:
        subject_requests.record_export(args.request, args.operator, f"{found} rows")
    print(f"wrote {out}: {found} rows in {sum(1 for s in manifest['searched'] if s.get('found'))} "
          f"places, {sum(1 for f in manifest['files'] if f.get('included'))} files")
    for note in manifest["notes"]:
        print("note:", note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
