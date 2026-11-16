#!/bin/bash
# test-db-up.sh — bring up the test lane (colima VM + the `postgresql` container, 127.0.0.1:5433).
#
# Start colima ONLY through this script. The hub stamps every process a session starts with HUB_TREE=<session>@<hub>
# and, when an executor is retired or goes to sleep (core/reducer._for_good), stops everything carrying its stamp
# (hosts/common.Trees.end_tree). A `colima start` typed in an executor's shell hands that stamp to limactl's hostagent,
# so the VM, and with it the test database, dies with the executor, under every other executor's running pre-commit
# (2026-10-28: 11:02:14 and 11:08:28, six seconds after silent-401 and sintomas-explain were retired).
# Dropping the stamp makes the VM nobody's child. No-op when the lane already answers.
#
# Once the lane answers, scripts/lane_janitor.py drops day-old idle test databases, trims the VM disk after a drop
# and warns past 85% full (2026-11-07: the VM disk at 100% failed every routes tier). It never fails this script.
set -u
port=5433
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
up() { nc -z 127.0.0.1 "$port" >/dev/null 2>&1; }
janitor() { python3 "$here/lane_janitor.py" || true; }

if up; then
    echo "test-db-up: 127.0.0.1:$port already answers"
    janitor
    exit 0
fi
colima status >/dev/null 2>&1 || env -u HUB_TREE colima start || exit 1
env -u HUB_TREE docker start postgresql >/dev/null || exit 1
for _ in $(seq 1 30); do
    up && { echo "test-db-up: 127.0.0.1:$port answers"; janitor; exit 0; }
    sleep 1
done
echo "test-db-up: 127.0.0.1:$port does not answer after 30 s" >&2
exit 1
