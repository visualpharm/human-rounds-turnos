"""`python3 -m app` — the process entrypoint: runs app/asgi_main.py as the main program."""
import runpy

runpy.run_module("app.asgi_main", run_name="__main__", alter_sys=True)
