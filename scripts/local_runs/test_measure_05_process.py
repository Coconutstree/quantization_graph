import json
from pathlib import Path
import sys
import tempfile
import unittest

from measure_05_process import compare_traces, measure


class EvidenceTests(unittest.TestCase):
    def test_real_process_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            result = measure([sys.executable, "-c", "import time; x=bytearray(1024*1024); time.sleep(.15)"],
                             directory, budget=256 << 20)
            self.assertTrue(result["user_address_space_budget_passed"])
            self.assertGreater(result["kernel_wait4_peak_rss_bytes"], 0)

    def test_allocation_failure_is_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                measure([sys.executable, "-c", "x=bytearray(512*1024*1024)"], directory, budget=128 << 20)
            result = json.loads((Path(directory) / "memory_measurement.json").read_text())
            self.assertFalse(result["user_address_space_budget_passed"])

    def test_actual_parity_and_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a", Path(directory) / "b"
            row = dict(search_width=10, query_id=0, result_ids=list(range(10)),
                       recall_at_10=1, visited_nodes=12, distance_evaluations=14)
            a.write_text(json.dumps(row) + "\n")
            b.write_text(a.read_text())
            self.assertTrue(compare_traces(a, b)["passed"])
            row["result_ids"].reverse()
            b.write_text(json.dumps(row) + "\n")
            self.assertFalse(compare_traces(a, b)["passed"])

    def test_empty_parity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty"
            path.touch()
            with self.assertRaises(ValueError):
                compare_traces(path, path)


if __name__ == "__main__":
    unittest.main()
