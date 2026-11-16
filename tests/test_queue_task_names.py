"""test_queue_task_names.py — the queue's task names are durable identities and never change.

procrastinate stores the task name of every job (`procrastinate_jobs.task_name`), of every
periodic defer and of every `task:<name>` lock. A job queued under a name the worker no longer
registers is never run, so renaming a module must not rename its tasks: the names below look like
module paths for historical reasons only, and moving the code (docs/LAYOUT.md) leaves them as
they are. A new task adds its name here; changing one is a data migration, not a rename.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app import worker  # noqa: E402
from app.core import launch_signals  # noqa: E402

BUSINESS_TASKS = frozenset({
    "ops.backup_full.run",
    "ops.data_backups.sweep",
    "ops.housekeeping.sweep",
    "ops.translate_warmup.sweep",
    "modules.turnos.waitlist.sweep",
    "modules.turnos.referrals.retention_sweep",
    "connectors.hsi.hsi_sync.tick",
    "notifications.reminders.sweep",
    "ai.chart_summary.pregen_sweep",
})
MAINTENANCE_TASKS = (
    "worker.maintenance.remove_old_jobs",
    "worker.maintenance.retry_stalled_jobs",
    "worker.maintenance.hsi_sync_watchdog",
    "worker.maintenance.data_backups_watchdog",
)
SIGNALS_TASK = "worker.maintenance.launch_signals"


class TestQueueTaskNames(unittest.TestCase):
    def test_the_business_jobs_keep_their_names(self):
        self.assertEqual({job.task_name for job in worker._JOBS}, BUSINESS_TASKS)

    def test_the_maintenance_tasks_keep_their_names(self):
        self.assertEqual(worker._MAINT_TASKS, MAINTENANCE_TASKS)
        self.assertEqual(worker._SIGNALS_TASK, SIGNALS_TASK)

    def test_what_a_worker_registers_with_launch_signals_off_and_on(self):
        with mock.patch.object(launch_signals, "any_active", return_value=False):
            self.assertEqual(worker._maint_tasks(), MAINTENANCE_TASKS)
        with mock.patch.object(launch_signals, "any_active", return_value=True):
            self.assertEqual(worker._maint_tasks(), MAINTENANCE_TASKS + (SIGNALS_TASK,))


if __name__ == "__main__":
    unittest.main()
