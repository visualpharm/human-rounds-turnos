"""The browser half of smoke_prod.py / smoke_demo.py: the appointment cell of the
public lists measured by scripts/smoke_listing_layout.mjs (ui review 2026-11-15).

The pages are the three kinds of table that carry «Más horarios»: a center's
doctors, a specialty's services and doctors, the doctors' directory.
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_listing_layout.mjs"
WIDTHS = 2  # 390 and 1440, smoke_listing_layout.WIDTHS
TIMEOUT = 600

PAGES_PROD = ("/center/hospital-pinamar", "/d/cardiologia", "/doctors")
PAGES_DEMO = ("/center/sampleton-hospital", "/d/cardiologia", "/doctors")


def _main_checkout():
    """A worktree has no node_modules: PW_ROOT points at the main checkout."""
    try:
        common = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return str(Path(common).parent) if common else None


def _text(raw):
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")


def listing_layout(base, pages, auth=None):
    """(failures, lines). Every page × width must answer with its own line;
    the basic-auth credential travels in the environment, never in argv."""
    env = dict(os.environ)
    if "PW_ROOT" not in env and not (ROOT / "node_modules" / "playwright").exists():
        main = _main_checkout()
        if main:
            env["PW_ROOT"] = main
    if auth:
        env["SMOKE_LAYOUT_AUTH"] = auth
    try:
        r = subprocess.run(["node", str(SCRIPT), base, *pages], env=env,
                           capture_output=True, text=True, timeout=TIMEOUT)
        out, code = r.stdout, r.returncode
    except subprocess.TimeoutExpired as exc:
        out, code = _text(exc.stdout), f"tiempo agotado ({TIMEOUT}s)"
    except OSError as exc:
        return 1, [f"[FALLA] celda de turnos: no corrio ({type(exc).__name__})"]
    lines = [ln for ln in out.splitlines() if ln.strip()]
    measured = [ln for ln in lines if ln.startswith(("[OK]", "[FALLA] celda de turnos /"))]
    if code != 0 or len(measured) != len(pages) * WIDTHS:
        if not any(ln.startswith("[FALLA]") for ln in lines):
            lines.append(f"[FALLA] celda de turnos: {len(measured)} de "
                         f"{len(pages) * WIDTHS} medidas (salida {code})")
    return sum(ln.startswith("[FALLA]") for ln in lines), lines
