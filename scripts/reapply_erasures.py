#!/usr/bin/env python3
"""reapply_erasures.py — carry out the recorded erasures again on a database restored from a backup
(Ley 25.326 art. 16): an erased person survives in every older backup.

DRY-RUN BY DEFAULT: without --apply it only counts, in a read-only transaction.

    python3 scripts/reapply_erasures.py --export /secure/erasures.json     # on the LIVE installation
    python3 scripts/reapply_erasures.py [--from-file /secure/erasures.json ...]            # the count
    python3 scripts/reapply_erasures.py [--from-file …] --apply [--operator <name>]        # the write
    python3 scripts/reapply_erasures.py --from-file OLD --export NEW       # a new file that keeps OLD's lines

Run it on the restored database, with the app and the workers stopped, after the migrations and
before anything is served. The tombstones (the person as an HMAC of the DNI, the mode, the instant)
come from the ledger of the restored database and from every --from-file: a backup older than an
erasure does not hold its ledger line, so `--export` writes the file from the live ledger, owner-only,
to be kept with the backup key and refreshed after each erasure (after a recovery, export again with the
old file as --from-file: the recovered ledger does not hold its lines). Idempotent: a second run
changes nothing. A DNI registered again after the erasure is a new person and is left alone. A person
with a live appointment, a pending HSI intent or an unresolved sync conflict is NOT touched (counted as
blocked): resolve it and run again, or pass --despite-blockers knowing that the pending HSI rows keep
their payload. The output is counts only, never a DNI, a name or a hash. Exit code: 0 done, 1 some
file could not be removed, 2 blocked, refused, unreadable input or an unreadable tombstone.
See docs/operations/backup-and-restore.md.
"""
import argparse
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import erasure_reapply, subject_requests  # noqa: E402


def _print(report, apply):
    print(f"tombstones: {report['ledger_entries']} ({report['unreadable_entries']} unreadable)")
    print(f"found in this database: {report['in_database']}")
    print(f"  already in place: {report['already_applied']}")
    print(f"  registered after the erasure (left alone): {report['skipped_registered_after']}")
    print(f"  {'applied' if apply else 'to apply'}: {report['applied'] if apply else report['to_apply']}")
    print(f"  blocked (live appointment, pending HSI intent or conflict): {report['blocked']}"
          f" of {report['with_live_blockers']} that have one")
    if report["shared_identifier_left_out"]:
        print(f"  phone or email shared with another account, left out: {report['shared_identifier_left_out']}")
    for label, rows in sorted(report["rows"].items()):
        print(f"    {label:<34} {rows:>5} rows")
    if apply:
        print(f"files: {report['files_removed']} removed, {report['files_failed']} failed; "
              f"refused: {report['refused']}; ledger lines written: {report['ledger_events_written']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--export", metavar="FILE", help="write the live ledger's tombstones to FILE and stop")
    ap.add_argument("--from-file", action="append", default=[], metavar="FILE",
                    help="tombstones exported before the restore (repeatable)")
    ap.add_argument("--apply", action="store_true", help="write (the default is a dry-run)")
    ap.add_argument("--despite-blockers", action="store_true",
                    help="also erase people who have a live appointment or a pending HSI step")
    ap.add_argument("--operator", default=getpass.getuser())
    args = ap.parse_args(argv)
    try:
        files = [erasure_reapply.read_file(p) for p in args.from_file]
        if args.export:
            count = erasure_reapply.export_file(args.export, [e for f in files for e in f])
            print(f"{count} tombstone(s) written (owner-only). Keep the file outside the database.")
            return 0
        sources = [subject_requests.erasures()] + files
    except (OSError, ValueError) as exc:
        print(f"error: tombstone file: {type(exc).__name__}: {exc if isinstance(exc, ValueError) else exc.strerror}",
              file=sys.stderr)
        return 2
    report = erasure_reapply.run(erasure_reapply.merge(*sources), apply=args.apply, operator=args.operator,
                                 despite_blockers=args.despite_blockers)
    _print(report, args.apply)
    if not args.apply:
        print("dry-run: nothing written. Repeat with --apply")
    if report["refused"] or report["unreadable_entries"] or (report["blocked"] and args.apply):
        return 2
    return 1 if report["files_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
