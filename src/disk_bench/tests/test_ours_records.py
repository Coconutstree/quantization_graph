"""Frozen-validation and memory-ledger checks for the formal Ours record cache."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orchestrator
from diskfair.ours_records import POLICY, prepare_record_args, profile_args
from diskfair.ours_pca import sha
from native_contract import validate_ours_record_cache, flatten_artifact


class RecordPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = self.root / "manifest.json"
        ranks = self.root / "ranks.u32"
        ranks.write_bytes((1).to_bytes(4, "little"))
        self.plan = dict(required_bytes=100, budget_bytes=200)
        data = dict(policy=POLICY, source_phase="validation", route_plan=self.plan,
                    source_graph_sha256="graph", native_binary_sha256="binary",
                    ranks_sha256=sha(ranks), pca_assets_sha256="", index_dir=str(self.root))
        self.manifest.write_text(json.dumps(data))
        dynamic = dict(reserved_bytes=20, capacity_nodes=2, resident_nodes=1,
                       valid_payload_bytes=10, hits=[3,4], misses=[1,2], node_insertions=1, node_evictions=0)
        cache = dict(budget_bytes=80, reserved_bytes=50, static_reserved_bytes=30,
                     static_nodes=1, static_hits=[1,2], dynamic=dynamic)
        self.artifact = dict(ours_route_plan=self.plan, cache_mode="standard", layer="05c",
                            cache_bytes=70, ours_graph_cache_bytes=20,
                            ours_record_cache_reserved_bytes=50, ours_record_cache_budget_bytes=80,
                            ours_record_cache_policy=POLICY, ours_hot_profile_manifest=str(self.manifest),
                            ours_hot_profile_sha256=sha(self.manifest), ours_hot_ranks_sha256=sha(ranks),
                            source_graph_sha256="graph", native_binary_sha256="binary",
                            ours_record_cache_stats=[dict(width=40,beam=1,cache=cache)],
                            summary_rows=[dict(search_width=40,beam_width=1)])

    def test_records_only_cannot_hide_graph_cache(self):
        a=copy.deepcopy(self.artifact);a["ours_cache_allocation"]="records_only"
        with self.assertRaisesRegex(ValueError,"records_only"): validate_ours_record_cache(a)
        a["ours_graph_cache_bytes"]=0; a["cache_bytes"]=50
        a["ours_record_cache_budget_bytes"]=100
        a["ours_record_cache_stats"][0]["cache"]["budget_bytes"]=100
        validate_ours_record_cache(a)

    def test_ledger_and_statistics_flow_to_csv(self):
        validate_ours_record_cache(self.artifact)
        row = flatten_artifact(self.root/"result.json",self.artifact)[0]
        self.assertEqual(row["cache_bytes"],70)
        self.assertEqual(row["dynamic_record_part_hits"],7)
        self.assertEqual(row["hot_record_part_hits"],3)

    def test_overallocation_and_missing_config_rejected(self):
        for field,value in (("ours_record_cache_reserved_bytes",81),("ours_record_cache_stats",[])):
            a=copy.deepcopy(self.artifact);a[field]=value
            with self.assertRaises(ValueError):validate_ours_record_cache(a)

    def test_modified_profile_and_wrong_route_rejected(self):
        with self.assertRaisesRegex(ValueError,"route_plan"):
            profile_args(self.manifest,dict(route_plan={}))
        (self.root/"ranks.u32").write_bytes(bytes(8))
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            profile_args(self.manifest,{})
        with self.assertRaises(ValueError):validate_ours_record_cache(self.artifact)

    def test_c0_requires_zero_shared_allocation(self):
        a=copy.deepcopy(self.artifact);a["cache_mode"]="c0"
        with self.assertRaisesRegex(ValueError,"C0"):validate_ours_record_cache(a)
        self.assertEqual(prepare_record_args(["--cache-mode","c0"],self.root/"r.json",
                                           phase="test",tuning_lock=None),["--ours-record-cache-policy","off"])

    def test_test_reuses_frozen_profile_and_never_trains(self):
        artifact = self.root/"test.json"
        artifact.with_suffix(".route_plan.json").write_text(json.dumps(self.plan))
        cmd=["--cache-mode","standard","--native-binary-sha256","binary",
             "--ours-graph-sha256","graph","--disk-index-dir",str(self.root)]
        lock=self.root/"lock.json"
        selected={k:self.artifact[k] for k in ("ours_record_cache_policy","ours_hot_profile_manifest","ours_hot_profile_sha256")}
        lock.write_text(json.dumps(dict(selected={"Ours-Disk::hybrid_disk":selected})))
        with patch("diskfair.ours_records.run_measured",side_effect=AssertionError("test trained profile")):
            args=prepare_record_args(cmd,artifact,phase="test",tuning_lock=lock)
        self.assertIn("hot_dynamic",args)
        with self.assertRaisesRegex(ValueError,"frozen validation"):
            prepare_record_args(cmd,artifact,phase="test",tuning_lock=None)
        self.manifest.write_text(self.manifest.read_text()+"\n")
        with self.assertRaisesRegex(ValueError,"frozen validation"):
            prepare_record_args(cmd,artifact,phase="test",tuning_lock=lock)


if __name__ == "__main__":
    unittest.main(verbosity=2)
