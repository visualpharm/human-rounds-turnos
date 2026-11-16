#!/usr/bin/env python3
"""Import a private normalized clinical-history JSON archive into Postgres.

Input shape matches the legacy connector fixtures:
    {"schema_version": 1, "patients": {"<dni>": [{...}, ...]}}

The import atomically replaces each DNI for the selected source. The source
file is never copied into the application repository.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db_clinical  # noqa: E402


def import_archive(path, source="local_history"):
    with open(path, encoding="utf-8") as f:
        archive = json.load(f)
    patients = archive.get("patients") if isinstance(archive, dict) else None
    if not isinstance(patients, dict):
        raise ValueError("archive must contain a patients object")
    counts = {}
    for raw_dni, records in patients.items():
        if not isinstance(records, list):
            raise ValueError(f"patient {raw_dni!r} must contain a record list")
        dni = "".join(c for c in str(raw_dni) if c.isdigit())
        counts[dni] = db_clinical.clinical_history_replace(dni, records, source)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--source", default="local_history")
    args = parser.parse_args(argv)
    counts = import_archive(args.path, args.source)
    total = sum(counts.values())
    print(f"clinical history: {total} records imported for {len(counts)} patient(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
