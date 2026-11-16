---
title: Stale dev server survives pkill — kill by port, not process name
scope: repo
area: dev
tags: [devloop, port-7700, pkill, homebrew-python]
created: 2026-07-08
refs: [c554dcf]
status: active
---

## Problem
After editing server-side code, `pkill -f "python3 server.py"` + restart appeared to work (`curl` returned 200) but requests kept hitting the OLD code — fixes seemed to have no effect.

## Root cause
Homebrew Python on macOS runs as `/opt/homebrew/.../Python.app/Contents/MacOS/Python server.py` — the string `python3` never appears in the command line, so pkill matched nothing. The new instance then died with `OSError: [Errno 48] Address already in use`, buried in the nohup log, while the stale instance kept serving.

## Solution
Kill by listening port, then confirm the port is free before restarting:
```sh
lsof -nP -tiTCP:7700 -sTCP:LISTEN | xargs kill
lsof -nP -iTCP:7700 -sTCP:LISTEN   # must print nothing
PORT=7700 nohup python3 server.py > /tmp/sana-turnos-run.log 2>&1 &
```

## How to apply
Any restart of this app (or any python server on this Mac) goes through the port, not the process name. If behavior contradicts freshly-edited code, check the boot log for `Errno 48` first.

**Superseded for day-to-day restarts** where an installation configures a local
supervisor: use that supervisor's restart command instead of a manual port
kill. This entry's port-kill technique is still correct when the supervisor
process itself needs to be found or killed manually.
