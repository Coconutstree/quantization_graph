"""Unit tests for the unified Ours method entry (no third-party framework)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from legacy.ours_experiments import run_ours  # noqa: E402


class TestPrepareSplits(unittest.TestCase):
    def test_split_files(self) -> None:
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = base / "data"
            (data / "smoke").mkdir(parents=True)
            queries = np.arange(200 * 4, dtype=np.float32).reshape(200, 4)
            gt = np.arange(200 * 10, dtype=np.int64).reshape(200, 10)

            def write_fvecs(path: Path, arr: np.ndarray) -> None:
                arr = np.ascontiguousarray(arr, dtype=np.float32)
                with path.open("wb") as f:
                    for row in arr:
                        f.write(np.int32(arr.shape[1]).tobytes())
                        f.write(row.tobytes())

            def write_ivecs(path: Path, arr: np.ndarray) -> None:
                arr = np.ascontiguousarray(arr, dtype=np.int32)
                with path.open("wb") as f:
                    for row in arr:
                        f.write(np.int32(row.size).tobytes())
                        f.write(row.tobytes())

            write_fvecs(data / "smoke" / "smoke_query.fvecs", queries)
            write_ivecs(data / "smoke" / "smoke_groundtruth.ivecs", gt)
            out = base / "results"
            val_q, val_gt, test_q, test_gt = run_ours.prepare_query_splits(
                "smoke", data, out, 100
            )
            self.assertTrue(val_q.exists())
            self.assertTrue(val_gt.exists())
            self.assertTrue(test_q.exists())
            self.assertTrue(test_gt.exists())
            self.assertEqual(run_ours._fvec_count(test_q), 100)
            self.assertEqual(run_ours._fvec_count(val_q), 100)


class TestConfig(unittest.TestCase):
    def test_config_valid(self) -> None:
        import json

        cfg = json.loads((_REPO / "Ours" / "config.json").read_text())
        self.assertEqual(cfg["R"], 32)
        self.assertEqual(cfg["L_build"], 400)
        self.assertEqual(cfg["degrees"], [32, 64])
        self.assertEqual(cfg["centroid_count"], 1)
        self.assertIn(cfg["threads"], (32, 64))


if __name__ == "__main__":
    unittest.main()
