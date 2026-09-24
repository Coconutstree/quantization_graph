import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import sampled_05_parity as sampled
from run_optimized_05_queue import configured, set_arg


class SampledParityTests(unittest.TestCase):
    def test_deterministic_bounded_selection(self):
        ids = sampled.sample_ids(9000)
        self.assertEqual(len(ids), 32)
        self.assertEqual(len(set(ids)), 32)
        self.assertLess(ids[-1], 9000)
        self.assertEqual(sampled.sample_ids(3), [0, 1, 2])
        with self.assertRaises(ValueError):
            sampled.sample_ids(0)

    def test_record_selection_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "in", Path(tmp) / "out"
            records = [struct.pack("<Iff", 2, i, i + .5) for i in range(5)]
            src.write_bytes(b"".join(records))
            sampled.extract_records(src, dst, [4, 1])
            self.assertEqual(dst.read_bytes(), records[4] + records[1])
            src.write_bytes(b"broken")
            with self.assertRaises(ValueError):
                sampled.extract_records(src, Path(tmp) / "bad", [0])

    def test_rust_sample_only_96_comparisons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "vectors"
            src.write_bytes(b"".join(struct.pack("<If", 1, i) for i in range(64)))
            command = [sys.executable, "--method", "Ours-Disk", "--query", str(src),
                       "--groundtruth", str(src)]
            calls = []
            def measure(cmd, folder, **kwargs):
                calls.append(cmd)
                folder.mkdir(parents=True)
                (folder / "result.parity.json").write_text(json.dumps({
                    "query_comparisons": 96, "max_recall_delta": 0,
                    "mean_top10_overlap": 1, "mean_visited_count_relative_delta": 0,
                    "mean_distance_count_relative_delta": 0}))
            with patch.object(sampled, "measure", measure):
                result = sampled.run_preflight(command, root / "preflight", 64, configured, set_arg)
            self.assertTrue(result["passed"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][calls[0].index("--integration-widths") + 1], "10,100,580")
            self.assertEqual(calls[0][calls[0].index("--warmup-queries") + 1], "0")
            full = configured(command, root / "full", [10, 100, 580])
            self.assertEqual(full[full.index("--parity-mode") + 1], "external")
            self.assertEqual(result["scope"], "sampled_preflight_not_full_query_parity")


if __name__ == "__main__":
    unittest.main()
