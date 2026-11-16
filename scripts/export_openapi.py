#!/usr/bin/env python3
"""export_openapi.py — write the application's OpenAPI schema to a file.

The same schema `GET /openapi.json` serves to a signed-in admin
(`routers/api_docs.build_schema` over the composed `app.app`), without starting
a server or a database: it comes from the route table and the Pydantic models
alone. For a reader of the API, a client generator, or a diff between two
releases.

    python3 scripts/export_openapi.py --out openapi.json
    python3 scripts/export_openapi.py              # to standard output

`info.version` is the build's commit when `SOURCE_COMMIT` (or the platform's
equivalent, `core/version.py`) is set, otherwise «unknown».
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def schema_text():
    from app import main as app_module
    from app.routers import api_docs
    return json.dumps(api_docs.build_schema(app_module.app), ensure_ascii=False, indent=2) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--out", help="file to write (default: standard output)")
    args = parser.parse_args(argv)
    text = schema_text()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
