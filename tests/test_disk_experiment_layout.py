"""Regression checks for the public experiment and result-directory migration."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "disk_bench"))
import orchestrator
from diskfair.layout import (LAYER_DIRS, normalize_layers, validate_run_id, run_metadata_root,
                            dataset_run_root, MEMORY_EXPERIMENT, memory_experiment)
from diskfair.native_contract import flatten_artifact


class DiskLayoutTests(unittest.TestCase):
    def test_public_experiments_and_entry_points(self):
        formal = {*LAYER_DIRS.values(), MEMORY_EXPERIMENT}
        self.assertEqual({p.name for p in (ROOT / "experiments").iterdir()
                          if p.is_dir() and not p.name.startswith(("_", "."))},
                         formal | {"04_ours_memory_budget"})
        for name in formal:
            entry = ROOT / "experiments" / name / "run.py"
            proc = subprocess.run([sys.executable, str(entry), "--help"],
                                  cwd="/tmp", capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_each_experiment_owns_its_native_implementation(self):
        for relative in (
            "01_disk_quantizer/native/quantizer_port.cpp",
            "01_disk_quantizer/native/saq_quantizer_port.cpp",
            "01_disk_quantizer/tools/faiss_hard_negative_candidates.cpp",
            "02_disk_shared_graph/native/src/main.rs",
            "02_disk_shared_graph/native/src/ours_port.rs",
            "03_disk_system/native/glass_disk_port.cpp",
            "03_disk_system/native_diskann/src/main.rs",
            "03_disk_system/adapters/run_official_disk_baseline.py",
        ):
            self.assertTrue((ROOT / "experiments" / relative).is_file(), relative)
        self.assertFalse((ROOT / "Ours/experiments").exists())

    def test_entry_cannot_silently_run_another_layer(self):
        proc = subprocess.run([sys.executable, str(ROOT / "experiments/01_disk_quantizer/run.py"),
                               "--phase", "doctor", "--layers", "03"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("only its own experiment layer", proc.stderr)

    def test_aliases_and_run_id_containment(self):
        self.assertEqual(normalize_layers("01,02,03"), ("05a", "05b", "05c"))
        self.assertEqual(normalize_layers("all"), normalize_layers("05a,05b,05c"))
        self.assertEqual(normalize_layers("05"), ("05c",))
        self.assertEqual(memory_experiment("05"), MEMORY_EXPERIMENT)
        self.assertIsNone(memory_experiment("05c"))
        for value in ("01,05a", "04", "", "all,01", "03,05", "05,01", "05,05"):
            with self.assertRaises(ValueError):
                normalize_layers(value)
        for value in ("../old", "/tmp/run", ".", "a/b", ""):
            with self.assertRaises(ValueError):
                validate_run_id(value)

    def test_memory_entry_is_distinct_despite_reusing_native_05c(self):
        for default, selection in (("03", "05"), ("05", "03"), ("05", "05c")):
            args = orchestrator.make_parser(default).parse_args(["--phase", "doctor", "--layers", selection])
            with self.assertRaisesRegex(orchestrator.ContractError, "only its own experiment"):
                orchestrator.configure_experiment(args, default)
        args = orchestrator.make_parser("05").parse_args(["--phase", "doctor"])
        orchestrator.configure_experiment(args, "05")
        self.assertEqual((args.layers, args.experiment_group, args.experiment),
                         (("05c",), "budget_scan", MEMORY_EXPERIMENT))
        unified = orchestrator.make_parser().parse_args(["--phase", "doctor", "--layers", "05"])
        orchestrator.configure_experiment(unified)
        self.assertEqual(unified.experiment, MEMORY_EXPERIMENT)
        for default, group in (("03", "budget_scan"), ("05", "primary"), ("05", "thread_scaling")):
            args = orchestrator.make_parser(default).parse_args(["--phase", "doctor", "--experiment-group", group])
            with self.assertRaises(orchestrator.ContractError):
                orchestrator.configure_experiment(args, default)

    def test_memory_results_have_their_own_namespace_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            run = run_metadata_root(out, "memory_b05")
            run.mkdir(parents=True)
            protocol = run / "protocol.json"
            # A historical budget_scan run is not silently moved to experiment 05.
            protocol.write_text(json.dumps({"experiment_group": "budget_scan"}))
            self.assertEqual(dataset_run_root(run, "05c", "gist"), out / "03_disk_system/gist/memory_b05")
            protocol.write_text(json.dumps({"experiment": MEMORY_EXPERIMENT, "experiment_group": "budget_scan"}))
            path = dataset_run_root(run, "05c", "gist")
            self.assertEqual(path, out / "05_memory_budget/gist/memory_b05")
            orchestrator._finalize_dataset_manifest(run, out, "memory_b05", "05c", "gist")
            manifest = json.loads((path / "manifest.json").read_text())
            self.assertEqual(manifest["experiment"], MEMORY_EXPERIMENT)
            self.assertEqual((path / manifest["run_metadata_relative"]).resolve(), run.resolve())
            self.assertFalse((out / "03_disk_system").exists())
            with self.assertRaises(ValueError):
                dataset_run_root(run, "05b", "gist")

    def test_plot_rejects_03_and_05_run_confusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "manifests/run1"
            run.mkdir(parents=True)
            for experiment, selected in ((MEMORY_EXPERIMENT, "03"), (None, "05")):
                (run / "protocol.json").write_text(json.dumps({"experiment": experiment}))
                proc = subprocess.run([sys.executable, str(ROOT / "scripts/plot_disk_experiments.py"),
                                       "--run-root", str(run), "--layers", selected, "--datasets", "tiny"],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("public experiment does not match this run", proc.stderr)

    def test_custom_output_has_one_authoritative_dataset_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "custom"
            run = run_metadata_root(out, "fixture")
            for layer, name in LAYER_DIRS.items():
                source = dataset_run_root(run, layer, "tiny")
                self.assertEqual(source, out / name / "tiny" / "fixture")
                (source / "tables").mkdir(parents=True)
                csv = source / "tables/formal_test_rows.csv"
                csv.write_text("experiment,qps\n" + name + ",10\n")
                before = csv.read_bytes()
                orchestrator._finalize_dataset_manifest(run, out, "fixture", layer, "tiny")
                self.assertEqual(csv.read_bytes(), before)
                manifest = json.loads((source / "manifest.json").read_text())
                self.assertFalse(manifest["formal_ready"])
                self.assertEqual((source / manifest["run_metadata_relative"]).resolve(), run.resolve())
            self.assertFalse((out / "published").exists())
            self.assertFalse((out / "runs").exists())

    def test_native_artifacts_are_grouped_by_method_and_phase(self):
        from diskfair.native_contract import specs_for
        item = orchestrator.WorkItem(specs_for("05a", ["PQ_4bit"])[0], "resident", 0, 32, 2.0, "c0")
        root = Path("/tmp/results/manifests/run1")
        artifact = orchestrator._artifact_path(root, "05a", "gist", "validation", item)
        self.assertEqual(artifact.parent, Path("/tmp/results/01_disk_quantizer/gist/run1/raw/PQ_4bit/validation"))

    def test_csv_experiment_name_preserves_native_protocol_identity(self):
        for layer, name in LAYER_DIRS.items():
            rows = flatten_artifact(Path("result.json"), {"layer": layer, "summary_rows": [{}]})
            self.assertEqual(rows[0]["experiment"], name)
            self.assertEqual(rows[0]["layer"], layer)
        row = flatten_artifact(Path("result.json"), {"layer": "05c", "experiment": MEMORY_EXPERIMENT,
                                                   "summary_rows": [{}]})[0]
        self.assertEqual(row["experiment"], MEMORY_EXPERIMENT)
        self.assertEqual(row["layer"], "05c")


if __name__ == "__main__":
    unittest.main()
