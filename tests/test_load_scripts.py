"""scripts/load_hot_paths.py and scripts/load_report.py — the pure parts.

The load run itself needs a container and a database (docs/architecture/capacity.md); what is
pinned here is what the numbers in a report depend on: percentiles, the status accounting
(a 503 is counted on its own), the refusal to aim at anything that is not a local install, and
the chart's axis rounding.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import load_hot_paths  # noqa: E402
import load_report  # noqa: E402


class TestPercentile(unittest.TestCase):
    def test_nearest_rank(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertEqual(load_hot_paths.percentile(values, 50), 5)
        self.assertEqual(load_hot_paths.percentile(values, 95), 10)
        self.assertEqual(load_hot_paths.percentile(values, 10), 1)
        self.assertIsNone(load_hot_paths.percentile([], 50))


class TestSummarise(unittest.TestCase):
    def test_503_is_counted_on_its_own_and_as_a_server_error(self):
        samples = [(200, 0.010)] * 6 + [(503, 3.0)] * 2 + [(500, 0.1), (0, 30.0)]
        s = load_hot_paths.summarise(samples, seconds=5.0)
        self.assertEqual(s["requests"], 10)
        self.assertEqual(s["rps"], 2.0)
        self.assertEqual(s["unavailable_503"], 2)
        self.assertEqual(s["server_errors"], 4)  # 2 x 503, the 500, the client-side failure (0)
        self.assertEqual(s["statuses"], {"200": 6, "503": 2, "500": 1, "0": 1})
        self.assertEqual(s["max_ms"], 30000.0)

    def test_an_empty_window_does_not_divide_by_zero(self):
        s = load_hot_paths.summarise([], seconds=0)
        self.assertEqual((s["requests"], s["rps"], s["p95_ms"]), (0, 0.0, None))


class TestTargetGuard(unittest.TestCase):
    def test_only_local_targets_are_accepted(self):
        for base in ("http://127.0.0.1:17700", "http://localhost:7700", "http://192.168.1.5:7700"):
            with self.subTest(base=base):
                load_hot_paths.check_target(base, allow_other=False)

    def test_a_public_host_is_refused_unless_forced(self):
        for base in ("https://pinamar.humanrounds.org", "https://salud.pinamar.gob.ar", "http://8.8.8.8"):
            with self.subTest(base=base):
                with self.assertRaises(SystemExit):
                    load_hot_paths.check_target(base, allow_other=False)
        load_hot_paths.check_target("https://pinamar.humanrounds.org", allow_other=True)


class TestChart(unittest.TestCase):
    def test_nice_ceiling_rounds_up_to_a_round_step(self):
        for top, expected in ((1.0, 1), (3.0, 4), (63.0, 100), (57, 100), (3476, 4000)):
            with self.subTest(top=top):
                self.assertAlmostEqual(load_report.nice_ceiling(top), expected)
        for top in (7, 23, 480, 1200):
            self.assertGreaterEqual(load_report.nice_ceiling(top), top)

    def test_table_and_svg_render_from_a_result(self):
        level = {"concurrency": 10,
                 "all": {"rps": 40.0, "p50_ms": 100.0, "p95_ms": 300.0, "p99_ms": 400.0,
                         "server_errors": 2, "unavailable_503": 1},
                 "postgres": {"connections_max": 9}}
        runs = [{"label": "a", "levels": {10: level}}]
        self.assertIn("| 10 | 40 | 100 | 300 | 400 | 1 | 1 | 9 |", load_report.table(runs))
        svg = load_report.svg(runs)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Throughput", svg)


if __name__ == "__main__":
    unittest.main()
