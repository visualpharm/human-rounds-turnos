"""The live docs describe the runtime that actually ships: FastAPI served by uvicorn.

`server.py` and its `http.server.ThreadingHTTPServer` were retired on 2026-08-27
(`asgi_main.py` is the only entrypoint). The README and the comments that explain
the process model must not keep telling a reader the opposite. Dated design
notes under `docs/roadmap/` and `docs/solutions/` are history and are not checked.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]

# Files that describe the CURRENT process model.
LIVE_FILES = (
    "README.md",
    "app/core/db.py",
    "app/ops/setup.py",
    "docs/architecture/setup-wizard.md",
    "docs/architecture/deploy.md",
)


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class TestLiveDocsRuntimeClaims(unittest.TestCase):
    def test_no_live_file_presents_the_stdlib_server_as_the_runtime(self):
        for name in LIVE_FILES:
            text = _read(name)
            with self.subTest(file=name):
                self.assertNotIn("no ASGI/WSGI", text)
                self.assertNotIn("ThreadingHTTPServer", text.replace(
                    "antes del 2026-08-27, del `ThreadingHTTPServer`", ""))
                self.assertNotIn("python3 server.py", text)

    def test_readme_names_fastapi_and_uvicorn_and_the_single_entrypoint(self):
        text = _read("README.md")
        stack = text[text.index("## Tech stack"):]
        for needle in ("FastAPI", "uvicorn", "asgi_main.py"):
            with self.subTest(needle=needle):
                self.assertIn(needle, stack)
        self.assertNotIn("No framework, on purpose", stack)

    def test_the_dockerfile_still_starts_the_documented_entrypoint(self):
        # the application package's entrypoint (app/asgi_main.py through app/__main__.py)
        self.assertIn('CMD ["python3", "-m", "app"]', _read("Dockerfile"))


if __name__ == "__main__":
    unittest.main()
