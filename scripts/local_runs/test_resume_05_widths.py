import json
import tempfile
import unittest
from pathlib import Path
from resume_05_widths import checkpoint, resume_widths
from measure_05_process import digest


class ResumeTests(unittest.TestCase):
    def test_preserve_completed_and_measure_only_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = ["binary", "--native-binary-sha256", "hash", "--query-split-sha256", "split",
                       "--query-order-sha256", "order", "--dataset", "dbpedia", "--method", "OG"]
            def write(path, data):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data))
            def artifact(result, trace, width):
                trace.parent.mkdir(parents=True, exist_ok=True)
                trace.write_text(json.dumps(dict(search_width=width, query_id=0, result_ids=list(range(10)))) + "\n")
                write(result, dict(status="done", direct_io=True, cache_mode="c0", workers=32,
                      native_binary_sha256="hash", query_split_sha256="split", query_order_sha256="order",
                      dataset="dbpedia", method="OG", query_trace_sha256=digest(trace),
                      summary_rows=[dict(search_width=width, query_count=1)]))
            write(root / "command.json", command)
            old = root / "result.json.w30.tmp"
            trace = root / "queries.jsonl.w30.tmp"
            artifact(old, trace, 30)
            original_hash = digest(old)
            calls = []
            def measure(widths, part):
                calls.extend(widths)
                artifact(part / "result.json", part / "queries.jsonl", widths[0])
                write(part / "memory_measurement.json", {"user_address_space_budget_passed": True})
            resume_widths(command, root, [30, 40], 1, lambda c, p, w: w, measure, write, lambda *a: None)
            self.assertEqual(calls, [40])
            self.assertEqual(digest(old), original_hash)
            self.assertEqual(json.loads((root / "memory_measurement.json").read_text())["missing_evidence_widths"], [30])
            calls.clear()
            resume_widths(command, root, [30, 40], 1, lambda c, p, w: w, measure, write, lambda *a: None)
            self.assertEqual(calls, [])
            trace.write_text("corrupted")
            with self.assertRaises(ValueError):
                checkpoint(old, trace, command, 30, 1)
            with self.assertRaises(ValueError):
                resume_widths(command + ["--workers", "1"], root, [30, 40], 1,
                              lambda c, p, w: w, measure, write, lambda *a: None)


if __name__ == "__main__":
    unittest.main()
