#!/bin/bash
# db-heal-local.sh — diagnose + auto-heal the dev machine turnos stack (:7700).
#
# Failure classes seen on this Mac:
#
#   1) No turnos/.env / DATABASE_URL unset → /ready says
#      "DATABASE_URL no configurada", login shows "base de datos no disponible".
#      Restarting Postgres does NOTHING. Fix: restore .env, kickstart LaunchAgent.
#
#   2) Compose/db down or port not published → app can't reach Postgres.
#      `docker compose up -d db` (+ override publishing 127.0.0.1:55432) heals the
#      throwaway compose DB. The REAL patient DB on this Mac is usually brew/
#      host Postgres on :5432 (see turnos-paleta/.env) — restart that if down.
#
#   3) Schema lag (missing relation) → Alembic forward revision, not a restart.
#      See docs/solutions/deploy/missing-table-is-not-a-dead-database.md
#
# Usage (from turnos/):
#   ./scripts/db-heal-local.sh
#   ./scripts/db-heal-local.sh --dry-run

set -euo pipefail

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd -- "$ROOT"

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

run() {
  if [[ "$DRY" -eq 1 ]]; then
    echo "DRY: $*"
  else
    echo "+ $*"
    eval "$@"
  fi
}

echo "=== $(date -Iseconds) db-heal-local start ==="

# --- 1. .env present --------------------------------------------------------
if [[ ! -f .env ]]; then
  echo "-- missing .env --"
  if [[ -f ../turnos-paleta/.env ]]; then
    echo "copying DATABASE_URL from turnos-paleta/.env into a fresh .env"
    run "cp .env.example .env"
    run "python3 - <<'PY'
from pathlib import Path
import re
src = Path('../turnos-paleta/.env').read_text().splitlines()
url = next(l.split('=',1)[1].strip().strip('\"') for l in src if l.startswith('DATABASE_URL='))
p = Path('.env'); t = p.read_text()
t = re.sub(r'^DATABASE_URL=.*$', 'DATABASE_URL=' + url, t, count=1, flags=re.M)
p.write_text(t)
print('DATABASE_URL restored (host/db only):', url.split('@')[-1])
PY"
  else
    echo "FATAL: no .env and no turnos-paleta/.env to copy from"
    exit 2
  fi
else
  echo "-- .env present --"
fi

# --- 2. Host Postgres on :5432 (real patient DB) ----------------------------
if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
  echo "-- host Postgres :5432 ready --"
else
  echo "-- host Postgres :5432 DOWN — trying brew restart --"
  if command -v brew >/dev/null 2>&1; then
    run "brew services restart postgresql@16 || brew services restart postgresql@14 || brew services restart postgresql"
    sleep 3
  fi
  if ! pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "WARNING: :5432 still down — check the DSN in .env"
  fi
fi

# --- 3. Compose throwaway DB (optional, for TEST_DATABASE_URL) --------------
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  run "docker compose up -d db"
  if pg_isready -h 127.0.0.1 -p 55432 >/dev/null 2>&1; then
    echo "-- compose DB :55432 ready --"
  else
    echo "-- compose DB :55432 not published yet (override may be missing) --"
  fi
else
  echo "-- docker unavailable, skip compose db --"
fi

# --- 4. Schema: report missing lab_report_analysis, don't invent DDL --------
python3 - <<'PY'
import os, re, subprocess
from pathlib import Path
from urllib.parse import urlparse
text = Path('.env').read_text()
m = re.search(r'^DATABASE_URL=(.*)$', text, re.M)
if not m:
    print('-- no DATABASE_URL in .env — skip schema probe --'); raise SystemExit
url = m.group(1).strip().strip('"')
u = urlparse(url)
env = os.environ.copy()
if u.password: env['PGPASSWORD'] = u.password
try:
    out = subprocess.check_output([
        'psql','-h', u.hostname or '127.0.0.1','-p', str(u.port or 5432),
        '-U', u.username or 'postgres','-d', (u.path or '/').lstrip('/') or 'postgres',
        '-tAc', "SELECT COALESCE(to_regclass('public.lab_report_analysis')::text,'MISSING')"
    ], env=env, text=True, timeout=10).strip()
    print(f'-- lab_report_analysis on {u.hostname}:{u.port or 5432} = {out} --')
    if out == 'MISSING':
        print('   schema lag: run `alembic upgrade head` (0006_lab_report_analysis); restart will not help')
except Exception as e:
    print(f'-- schema probe failed: {type(e).__name__} --')
PY

# --- 5. Kick the LaunchAgent so pools re-read .env --------------------------
AGENT="gui/$(id -u)/com.openclaw.sana-turnos"
if launchctl print "$AGENT" >/dev/null 2>&1; then
  run "launchctl kickstart -k '$AGENT'"
  sleep 2
else
  echo "-- LaunchAgent $AGENT not loaded — start server.py yourself --"
fi

# --- 6. Verify --------------------------------------------------------------
ready=$(curl -fsS --connect-timeout 3 http://127.0.0.1:7700/ready || echo '{"status":"unreachable"}')
echo "-- /ready => $ready --"
echo "=== $(date -Iseconds) db-heal-local end ==="
