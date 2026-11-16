"""Queue scheduling contracts for the data-volume backup producer.

No database, B2 credentials, patient data, or network is used here.  The
integration worker suite owns real PostgreSQL/process contention; these tests
pin the scheduling shape that makes arbitrary legacy intervals safe.
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.ops import data_backups_queue  # noqa: E402


class _FakeTask:
    def __init__(self):
        self.configures = []
        self.defers = []

    def configure(self, **kwargs):
        self.configures.append(kwargs)
        return self

    async def defer_async(self, **kwargs):
        self.defers.append(kwargs)


class _FakeApp:
    def __init__(self):
        self.tasks = {}
        self.body = None

    def task(self, **kwargs):
        def _decorate(fn):
            self.tasks[kwargs["name"]] = _FakeTask()
            self.body = fn
            return fn
        return _decorate


class TestDataBackupsQueue(unittest.TestCase):
    def test_arbitrary_interval_self_reschedules_from_completion_not_cron(self):
        app = _FakeApp()
        data_backups_queue.register(app, 45)
        task = app.tasks[data_backups_queue._TASK_NAME]
        with mock.patch("app.ops.data_backups.tick", return_value=0):
            asyncio.run(app.body(0))
        self.assertEqual(len(task.configures), 1)
        self.assertEqual(task.configures[0]["queueing_lock"],
                         data_backups_queue._queueing_lock())
        self.assertEqual(task.configures[0]["schedule_in"], {"seconds": 45 * 60})
        self.assertNotIn("schedule_at", task.configures[0],
                         "the current job has not succeeded yet; last-success scheduling here "
                         "would create an immediate follow-up")

    def test_cron_override_is_rejected_instead_of_approximating(self):
        with self.assertRaises(ValueError):
            data_backups_queue.register(_FakeApp(), 45, cron="*/45 * * * *")


if __name__ == "__main__":
    unittest.main()
