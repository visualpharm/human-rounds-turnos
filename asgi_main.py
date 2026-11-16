"""asgi_main.py — kept at the root so `python3 asgi_main.py` still starts the server.

The process entrypoint is app/asgi_main.py (`python3 -m app` in the image). This file only runs it,
exactly as `python3 -m app.asgi_main` would; it is removed once no installation starts the old
path (docs/LAYOUT.md). Importing it is an error, so nothing patches or reads this copy by mistake.
"""
import runpy

if __name__ == "asgi_main":
    raise ImportError("asgi_main moved into the application package: import app.asgi_main")

runpy.run_module("app.asgi_main", run_name="__main__", alter_sys=True)
