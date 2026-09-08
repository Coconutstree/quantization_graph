"""Dataset-level metric, split, and DRAM feasibility policy for suite 05."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class DatasetPolicy:
    source_metric: str
    vectors_unit_normalized: bool
    runner_metric: str
    l2_compatible: bool
    default_validation_queries: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


DATASET_POLICIES: dict[str, DatasetPolicy] = {
    "agnews": DatasetPolicy("squared_l2", False, "squared_l2", True, 200),
    "dbpedia": DatasetPolicy("cosine", True, "squared_l2", True, 1000),
    "gist": DatasetPolicy("squared_l2", False, "squared_l2", True, 200),
    "sift10m": DatasetPolicy("squared_l2", False, "squared_l2", True, 200),
    "deep1B": DatasetPolicy("angular", True, "squared_l2", True, 1000),
    "msmarco": DatasetPolicy("inner_product", True, "squared_l2", True, 200),
    "bigann10m": DatasetPolicy("squared_l2", False, "squared_l2", True, 1000),
    # The official Cohere neighbors are ordered by inner product, while the
    # vectors are not unit-normalized. The current suite-05 kernels are L2-only.
    "cohere10m": DatasetPolicy("inner_product", False, "squared_l2", False, 1000),
}


def policy_for(dataset: str) -> DatasetPolicy:
    try:
        return DATASET_POLICIES[dataset]
    except KeyError as exc:
        raise ValueError(
            f"dataset {dataset!r} has no metric/split policy; add it to dataset_policy.py"
        ) from exc


def validation_query_count(dataset: str, total_queries: int, requested: int | None) -> int:
    if total_queries < 2:
        raise ValueError(f"{dataset}: at least two queries are required for validation/test")
    count = requested if requested not in (None, 0) else policy_for(dataset).default_validation_queries
    if count <= 0 or count >= total_queries:
        raise ValueError(
            f"{dataset}: validation queries must satisfy 0 < validation < {total_queries}; "
            f"got {count}"
        )
    return count


def metric_compatibility_error(dataset: str) -> str | None:
    policy = policy_for(dataset)
    if policy.l2_compatible:
        return None
    return (
        f"{dataset}: official ground truth uses {policy.source_metric}, but suite 05 "
        f"currently implements {policy.runner_metric}; non-normalized vectors do not "
        "preserve neighbor order between these metrics"
    )


def resident_budget_errors(
    dataset: str,
    base_count: int,
    dimension: int,
    layers: Iterable[str],
    budget_gib: float,
) -> list[str]:
    """Return unavoidable resident-code violations before expensive exports."""
    layers = set(layers)
    budget_bytes = int(budget_gib * (1 << 30))
    db1_bytes = (base_count * dimension + 7) // 8
    full4_bytes = (base_count * dimension * 4 + 7) // 8
    errors: list[str] = []
    if "05b" in layers and full4_bytes > budget_bytes:
        errors.append(
            f"{dataset}: 05B full4-resident/no-gate needs at least "
            f"{full4_bytes / (1 << 30):.2f} GiB before metadata/cache, exceeding "
            f"the {budget_gib:g} GiB budget"
        )
    if "05c" in layers and db1_bytes > budget_bytes:
        errors.append(
            f"{dataset}: Ours DB1 alone needs at least {db1_bytes / (1 << 30):.2f} GiB, "
            f"exceeding the {budget_gib:g} GiB 05C budget before metadata/cache"
        )
    return errors
