"""Public disk experiment names; native protocol IDs remain stable."""

from pathlib import Path
import json
import re

LAYER_DIRS = {
    "05a": "01_disk_quantizer",
    "05b": "02_disk_shared_graph",
    "05c": "03_disk_system",
}
LAYER_ALIASES = {"01": "05a", "02": "05b", "03": "05c", "05": "05c"}
MEMORY_EXPERIMENT = "05_memory_budget"
LAYOUT_VERSION = "paper-results-v2"


def normalize_layers(value: str) -> tuple[str, ...]:
    memory_experiment(value)
    items = tuple(x.strip() for x in value.split(",") if x.strip())
    if items == ("all",):
        return tuple(LAYER_DIRS)
    layers = tuple(LAYER_ALIASES.get(x, x) for x in items)
    if not layers or any(x not in LAYER_DIRS for x in layers):
        raise ValueError("layers must be 01,02,03 or all; use 05 alone for memory budgets")
    if len(set(layers)) != len(layers):
        raise ValueError("duplicate experiment layers")
    return layers


def memory_experiment(selection: str) -> str | None:
    """05 is an independent public experiment using the existing 05c native ports."""
    items = tuple(x.strip() for x in selection.split(",") if x.strip())
    if "05" in items:
        if items != ("05",):
            raise ValueError("05 memory budgets require a separate run; do not mix with 01/02/03")
        return MEMORY_EXPERIMENT
    return None


def run_experiment(run_root: Path) -> str | None:
    path = run_root / "protocol.json"
    value = json.loads(path.read_text()).get("experiment") if path.exists() else None
    if value not in (None, MEMORY_EXPERIMENT):
        raise ValueError(f"unknown public experiment in protocol: {value}")
    return value


def experiment_directory(layer: str, run_root: Path) -> str:
    experiment = run_experiment(run_root)
    if experiment == MEMORY_EXPERIMENT:
        if layer != "05c":
            raise ValueError("05 memory-budget runs require native system layer 05c")
        return experiment
    return LAYER_DIRS[layer]


def validate_run_id(run_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id):
        raise ValueError("run-id must start with a letter/digit and contain only letters, digits, _, . or -")
    return run_id


def run_metadata_root(out_root: Path, run_id: str) -> Path:
    return out_root / "manifests" / validate_run_id(run_id)


def dataset_run_root(run_root: Path, layer: str, dataset: str) -> Path:
    """run_root is <results>/manifests/<run-id>; no duplicated result tree."""
    validate_run_id(dataset)
    if run_root.parent.name != "manifests":
        raise ValueError("run-root must be <results>/manifests/<run-id>")
    return run_root.parent.parent / experiment_directory(layer, run_root) / dataset / validate_run_id(run_root.name)
