# The test database's colima VM dies when the executor that started it is retired

**Symptom (28.10.2026):** the colima VM holding the test Postgres (`127.0.0.1:5433`, container `postgresql`) was
stopped from outside at least four times in a morning (before 10:13, 10:35, 11:02:14, 11:08:28); pre-commit runs of
other executors lost their database mid-run (`docker.sock` gone). `~/.colima/_lima/colima/ha.stderr.log` ends with
`Received SIGTERM, shutting down the host agent`; no memory pressure (MemWatch level 1), no LaunchAgent, cron or script
of ours stops colima.

**Cause:** the hub core. `core/reducer._for_good` queues an `end_tree` action when an executor is retired, closed or
falls asleep; `hosts/common.Trees.end_tree` → `platforms/posix.end_tagged` sends SIGTERM (then SIGKILL) to every
process whose environment carries `HUB_TREE=<session>@<hub folder>`. Every process a session starts carries it, and
`colima start` typed in the session's shell hands it to `limactl hostagent` / `usernet`, which outlive the shell. The
journal (`~/.hub-core/journal.jsonl`) shows the pair each time: `session.close_requested` → `host.done end_tree "ended 2"`
→ hostagent SIGTERM 2–6 s after. Live proof 11:16: a `colima start` by `legal-salud` gave `HUB_TREE=legal-salud@…` to
hostagent and usernet (`ps -wwE`).

**Fix in this repo:** `scripts/test-db-up.sh` starts colima and the container with `env -u HUB_TREE`, so the VM belongs to
no session (`test/test_test_db_up.py`). A VM already started with a stamp stays exposed until it is restarted through the
script.

**Core side (hub-dev):** `end_tagged` has no exemption for shared infrastructure; a deny-list of image names
(`limactl`, `colima`, `qemu`, `dockerd`) or an env opt-out would cover every project, not only this one.
