"""worker.py — kept at the root so `python3 worker.py` still starts the queue worker.

The worker is app/worker.py (the web process starts it by that path). This file only runs it,
exactly as `python3 -m app.worker` would, arguments included (`python3 worker.py --jobs`); it is
removed once nothing calls the old path (docs/LAYOUT.md). Importing it is an error.
"""
import runpy

if __name__ == "worker":
    raise ImportError("worker moved into the application package: import app.worker")

runpy.run_module("app.worker", run_name="__main__", alter_sys=True)
