"""NON-FORMAL Python graph-search prototype for unit diagnostics only.

The loop is shared by all methods: same visited / frontier / beam semantics,
same direct-I/O adjacency reader. Methods only replace the navigation payload
accessor and query preparation. Ours additionally supports the four ablations:

1. full4-resident / no-gate
2. db1-resident / full4-on-ssd (DB1 gate)
3. + page coalescing
4. + query-local page reuse (also used by rerank)

Formal 05B/05C preserve the native 02/03 search loops and do not import this
module.
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .diskio import (
    DirectPageReader,
    IOStats,
    NodeLayout,
    QueryPageCache,
    extract_record_from_pages,
    extract_node,
)
from .quantizers import unpack_db1


class PayloadAccessor:
    """Abstract payload distance accessor."""

    name = "base"
    payload_pages_enabled = False

    def prepare_query(self, query: np.ndarray) -> Any:
        raise NotImplementedError

    def distance_batch(
        self,
        qctx: Any,
        ids: np.ndarray,
        reader: DirectPageReader | None = None,
        layout: NodeLayout | None = None,
        qcache: QueryPageCache | None = None,
        stats: IOStats | None = None,
        coalesce: bool = True,
        reuse: bool = True,
    ) -> np.ndarray:
        raise NotImplementedError

    def extra_record_fields(self, qctx: Any, stats: IOStats) -> dict[str, Any]:
        return {}


class ResidentCodeAccessor(PayloadAccessor):
    """Navigation codes fully resident (PQ/SQ/SAQ and Ours full4-resident)."""

    name = "resident"

    def __init__(self, quantizer, codes: np.ndarray, ours_factors: dict | None = None):
        self.quantizer = quantizer
        self.codes = codes
        self.ours_factors = ours_factors

    def prepare_query(self, query: np.ndarray) -> Any:
        return self.quantizer.prepare_query(query)

    def distance_batch(
        self,
        qctx,
        ids: np.ndarray,
        reader=None,
        layout=None,
        qcache=None,
        stats=None,
        coalesce=True,
        reuse=True,
    ) -> np.ndarray:
        if self.quantizer.name == "Ours_RaBitQ_K1":
            return self.quantizer.distances_from_codes(
                qctx,
                self.codes[ids],
                db1_scales=self.ours_factors["db1_scale"][ids],
                norm_sqr=self.ours_factors["norm_sqr"][ids],
                res_scale=self.ours_factors["res_scale"][ids],
            )
        return self.quantizer.distances_from_codes(qctx, self.codes[ids])


class OursGatedAccessor(PayloadAccessor):
    """Ours DB1 gate: DB1/factors resident, full4 payload on SSD."""

    name = "db1-gated-ssd"
    payload_pages_enabled = True

    def __init__(
        self,
        quantizer,
        db1_codes: np.ndarray,
        factors: dict,
        payload_layout: NodeLayout,
        epsilon0: float = 1.9,
        payload_reader=None,
    ):
        self.quantizer = quantizer
        self.db1_codes = db1_codes  # packed DB1 only (dim/8 bytes per vector)
        self.factors = factors
        self.layout = payload_layout
        self.epsilon0 = epsilon0
        self.payload_reader = payload_reader

    def prepare_query(self, query: np.ndarray) -> Any:
        return self.quantizer.prepare_query(query)

    def db1_est_distances(self, qctx, ids: np.ndarray) -> np.ndarray:
        db1 = self.db1_codes[ids]
        sign = np.where(
            unpack_db1(db1).astype(np.int8) > 0, 1.0, -1.0
        )
        ip = np.einsum("d,nd->n", qctx["pq"], sign) * self.factors["db1_scale"][ids]
        x_hat_norm_sqr = (self.factors["db1_scale"][ids] ** 2) * self.quantizer.dim
        return qctx["q_norm_sqr"] + x_hat_norm_sqr - 2.0 * ip

    def distance_batch(
        self,
        qctx,
        ids: np.ndarray,
        reader=None,
        layout=None,
        qcache=None,
        stats=None,
        coalesce=True,
        reuse=True,
    ) -> np.ndarray:
        codes = np.empty((ids.size, self.layout.record_bytes), dtype=np.uint8)
        pages = sorted({p for i in ids for p in self.layout.node_span_pages(int(i))})
        if not reuse and qcache is not None:
            qcache.clear()
        pr = self.payload_reader if self.payload_reader is not None else reader
        if coalesce:
            got = pr.read_pages(pages, query_cache=qcache, stats=stats)
        else:
            stats = stats or IOStats()
            got = {}
            for p in pages:
                rec = qcache.get(p) if qcache is not None else None
                if rec is None:
                    got[p] = pr.read_pages([p], query_cache=qcache, stats=stats).get(p)
                else:
                    got[p] = rec
        for j, nid in enumerate(ids):
            codes[j] = np.frombuffer(
                extract_record_from_pages(got, self.layout, int(nid)), dtype=np.uint8
            )
        return self.quantizer.distances_from_codes(
            qctx,
            codes,
            db1_scales=self.factors["db1_scale"][ids],
            norm_sqr=self.factors["norm_sqr"][ids],
            res_scale=self.factors["res_scale"][ids],
        )


@dataclass
class SearchResult:
    query_id: int
    top_ids: np.ndarray
    top_dist: np.ndarray
    stats: IOStats
    visited_nodes: int
    distance_evaluations: int
    db1_checks: int
    db1_survivors: int
    full4_candidates: int
    full4_page_reads: int
    latency_us: float
    io_wait_us: float
    distance_compute_us: float
    queue_compute_us: float
    gate: str = "none"
    rerank_candidates: int = 0
    rerank_page_reads: int = 0
    rerank_us: float = 0.0


def disk_search(
    query: np.ndarray,
    *,
    entry: int,
    L: int,
    adj_reader: DirectPageReader,
    adj_layout: NodeLayout,
    payload: PayloadAccessor,
    k: int = 10,
    beam: int = 1,
    coalesce: bool = True,
    reuse: bool = True,
    query_cache: QueryPageCache | None = None,
    shared_cache=None,
    max_expansions: int = 0,
    rerank: int = 0,
    max_node_id: int | None = None,
) -> SearchResult:
    """Beam search over a page-backed Vamana graph."""
    t_start = time.perf_counter()
    stats = IOStats()
    if max_node_id is not None and entry >= max_node_id:
        # DiskANN start points are extra nodes without payload; begin at their
        # first data neighbor instead.
        start_pages = adj_reader.read_pages(
            adj_layout.node_span_pages(int(entry)), stats=stats
        )
        rec = extract_node(start_pages, adj_layout, int(entry))
        arr = np.frombuffer(rec, dtype="<u4")
        deg = int(arr[0])
        entry = next(
            (int(nb) for nb in arr[1 : 1 + deg] if nb < max_node_id),
            0,
        )
    qctx = payload.prepare_query(query)
    visited: set[int] = {entry}
    results: list[tuple[float, int]] = []  # max-heap of best L (by -dist)
    frontier: list[tuple[float, int]] = []  # min-heap of candidate (dist, id)

    def push_result(dist: float, nid: int) -> None:
        if len(results) < L:
            heapq.heappush(results, (-dist, nid))
        elif dist < -results[0][0]:
            heapq.heapreplace(results, (-dist, nid))

    seed_ids = np.array([entry], dtype=np.int64)
    t_d = time.perf_counter()
    entry_dist = payload.distance_batch(
        qctx, seed_ids, adj_reader, adj_layout, query_cache, stats, coalesce, reuse
    )[0]
    t_dist = (time.perf_counter() - t_d) * 1e6
    heapq.heappush(results, (-entry_dist, entry))
    heapq.heappush(frontier, (entry_dist, entry))

    db1_checks = 0
    db1_survivors = 0
    full4_candidates = 0
    full4_page_reads = 0
    expansions = 0
    evals = 1
    t_gate = 0.0
    t_heap = 0.0
    t_adj = 0.0
    io_before = (stats.io_requests, stats.sectors_4k)

    while frontier:
        to_expand: list[tuple[float, int]] = []
        while frontier and len(to_expand) < beam:
            d, nid = heapq.heappop(frontier)
            if d > -results[0][0] and len(results) >= L:
                # standard DiskANN termination: nothing in the frontier can
                # beat the current beam, so stop expanding.
                break
            to_expand.append((d, nid))
        if not to_expand:
            continue

        t_q = time.perf_counter()
        adj_pages_needed: set[int] = set()
        for _, nid in to_expand:
            adj_pages_needed.update(adj_layout.node_span_pages(int(nid)))
        adj_pages = adj_reader.read_pages(
            sorted(adj_pages_needed),
            query_cache=query_cache,
            shared_cache=shared_cache,
            stats=stats,
        )
        t_adj = (time.perf_counter() - t_q) * 1e6

        neighbor_ids: set[int] = set()
        for _, nid in to_expand:
            rec = extract_node(adj_pages, adj_layout, int(nid))
            arr = np.frombuffer(rec, dtype="<u4")
            deg = int(arr[0])
            for nb in arr[1 : 1 + deg]:
                nb = int(nb)
                if max_node_id is not None and nb >= max_node_id:
                    continue
                if nb not in visited:
                    visited.add(nb)
                    neighbor_ids.add(nb)
            expansions += 1
            if max_expansions and expansions >= max_expansions:
                break

        if not neighbor_ids:
            continue
        nb_arr = np.fromiter(sorted(neighbor_ids), dtype=np.int64)

        if isinstance(payload, OursGatedAccessor):
            t_g = time.perf_counter()
            est = payload.db1_est_distances(qctx, nb_arr)
            db1_checks += nb_arr.size
            kth = -results[0][0] if len(results) >= L else np.inf
            if np.isfinite(kth):
                keep = est <= kth * payload.epsilon0
            else:
                keep = np.ones(nb_arr.size, dtype=bool)
            db1_survivors += int(keep.sum())
            gate_ids = nb_arr[keep]
            t_gate = (time.perf_counter() - t_g) * 1e6
        else:
            gate_ids = nb_arr
            t_gate = 0.0

        if gate_ids.size == 0:
            continue
        full4_candidates += int(gate_ids.size)

        t_d = time.perf_counter()
        dist = payload.distance_batch(
            qctx, gate_ids, adj_reader, adj_layout, query_cache, stats, coalesce, reuse
        )
        t_dist += (time.perf_counter() - t_d) * 1e6
        evals += int(gate_ids.size)
        if payload.payload_pages_enabled:
            full4_page_reads += stats.sectors_4k - io_before[1]
            io_before = (stats.io_requests, stats.sectors_4k)

        t_h = time.perf_counter()
        for j, nid in enumerate(gate_ids):
            if dist[j] < -results[0][0] or len(results) < L:
                heapq.heappush(frontier, (float(dist[j]), int(nid)))
            push_result(float(dist[j]), int(nid))
        t_heap = (time.perf_counter() - t_h) * 1e6

    rerank_candidates = 0
    rerank_page_reads = 0
    rerank_us = 0.0
    pool = results  # elements are (-dist, id)
    if rerank > 0 and results:
        pool = heapq.nlargest(rerank, results, key=lambda x: x[0])
        ids = np.fromiter((nid for _, nid in pool), dtype=np.int64)
        rerank_candidates = int(ids.size)
        t_r = time.perf_counter()
        io_before = (stats.io_requests, stats.sectors_4k)
        dist_r = payload.distance_batch(
            qctx, ids, adj_reader, adj_layout, query_cache, stats, coalesce, reuse
        )
        rerank_page_reads = int(stats.sectors_4k - io_before[1])
        rerank_us = (time.perf_counter() - t_r) * 1e6
        pool = [
            (-float(dist_r[j]), int(ids[j])) for j in range(ids.size)
        ]
    top = heapq.nlargest(k, pool, key=lambda x: x[0])
    top_ids = np.array([nid for _, nid in top], dtype=np.int64)
    top_dist = np.array([-d for d, _ in top], dtype=np.float64)
    latency = (time.perf_counter() - t_start) * 1e6
    return SearchResult(
        query_id=0,
        top_ids=top_ids,
        top_dist=top_dist,
        stats=stats,
        visited_nodes=len(visited),
        distance_evaluations=evals,
        db1_checks=db1_checks,
        db1_survivors=db1_survivors,
        full4_candidates=full4_candidates,
        full4_page_reads=full4_page_reads,
        rerank_candidates=rerank_candidates,
        rerank_page_reads=rerank_page_reads,
        rerank_us=rerank_us,
        latency_us=latency,
        io_wait_us=stats.io_wait_us,
        distance_compute_us=t_dist,
        queue_compute_us=t_gate + t_heap + t_adj,
    )
