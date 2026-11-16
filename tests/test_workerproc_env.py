"""The env of a real worker.py in the tests scales its wait for a pooled connection with the load.

A worker's first connection goes through the pool with a 3 s wait (`TURNOS_DB_POOL_TIMEOUT`,
core/db.py). At load ~180 on 2026-11-07 that wait ran out inside `queue_infra.ensure_schema()`,
the worker logged «no se pudo asegurar el esquema: DbUnavailable» and exited 1 before its health
line, and `test_rapid_spawn_sigterm_never_orphans_worker_row` failed with «worker nunca logueó».
`worker_env` hands the worker the same ceiling times `_wait.load_factor()`, as every other wait of
the harness; a caller's explicit value still wins.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _wait  # noqa: E402
import _workerproc  # noqa: E402


class TestWorkerEnvPoolWait(unittest.TestCase):
    def test_quiet_machine_keeps_the_default(self):
        with mock.patch.object(_wait, "load_factor", return_value=1.0):
            env = _workerproc.worker_env("postgresql://x/y")
        self.assertEqual(env["TURNOS_DB_POOL_TIMEOUT"], "3")

    def test_loaded_machine_waits_longer(self):
        with mock.patch.object(_wait, "load_factor", return_value=4.0):
            env = _workerproc.worker_env("postgresql://x/y")
        self.assertEqual(env["TURNOS_DB_POOL_TIMEOUT"], "12")

    def test_the_caller_wins(self):
        with mock.patch.object(_wait, "load_factor", return_value=4.0):
            env = _workerproc.worker_env("postgresql://x/y", {"TURNOS_DB_POOL_TIMEOUT": "1"})
        self.assertEqual(env["TURNOS_DB_POOL_TIMEOUT"], "1")


if __name__ == "__main__":
    unittest.main()
