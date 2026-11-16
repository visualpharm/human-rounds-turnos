#!/bin/bash
# Refresh the Mi Salud Digital slot dump for Human Rounds — Turnos. No AI tokens.
# Run by the com.sana-turnos.msd-refresh LaunchAgent (and safe to run by hand).
# Exit 3 = token expired (log into misaluddigital in Chrome); last good dump kept.
cd "$(dirname "$0")/../.." || exit 1
mkdir -p logs
PY=/opt/homebrew/bin/python3
[ -x "$PY" ] || PY=$(command -v python3)
echo "--- $(date '+%Y-%m-%d %H:%M:%S') ---" >> logs/msd-refresh.log
exec "$PY" scripts/tools/msd_pull.py --partido 60 >> logs/msd-refresh.log 2>&1
