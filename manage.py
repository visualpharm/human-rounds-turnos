"""manage.py — kept at the root so `python3 manage.py <command>` still works.

The command line is app/manage.py (`python3 -m app.manage <command>`). This file only runs it,
arguments included; it is removed once nothing calls the old path (docs/LAYOUT.md). Importing it
is an error.
"""
import runpy

if __name__ == "manage":
    raise ImportError("manage moved into the application package: import app.manage")

runpy.run_module("app.manage", run_name="__main__", alter_sys=True)
