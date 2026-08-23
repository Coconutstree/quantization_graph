"""Unit tests for the 05 disk suite (format, packing, quantizer parity, I/O).

Runs standalone (no pytest dependency):

    python experiments/05_disk_system_fair/tests/test_disk_suite.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

_PKG = Path(__file__).resolve().parents[1]
if "diskfair" not in sys.modules:
    import types

    _mod = types.ModuleType("diskfair")
    _mod.__path__ = [str(_PKG)]
    sys.modules["diskfair"] = _mod

from diskfair.diskio import (
    DirectPageReader,
    IOStats,
    NodeLayout,
    PageWriter,
    PageWriterAligned,
    QueryPageCache,
    extract_node,
    nodes_for_pages,
    plan_reads,
)
from diskfair.format import IndexBuilder, verify_index
from diskfair.quantizers import (
    OursRaBitQ,
    PQQuantizer4,
    SAQQuantizer4,
    SQQuantizer4,
    pack_3bit,
    pack_db1,
    pack_nibbles,
    unpack_3bit,
    unpack_db1,
    unpack_nibbles,
)


def test_nibble_roundtrip():
    codes = np.array([[0, 1, 15, 8, 7, 3]], dtype=np.uint8)
    packed = pack_nibbles(codes)
    assert packed.shape == (1, 3)
    assert np.array_equal(unpack_nibbles(packed)[0, :6], codes[0])


def test_3bit_roundtrip():
    rng = np.random.default_rng(7)
    codes = rng.integers(0, 8, size=(5, 960)).astype(np.uint8)
    packed = pack_3bit(codes)
    assert packed.shape == (5, 360)
    assert np.array_equal(unpack_3bit(packed)[:, :960], codes)


def test_db1_roundtrip():
    bits = np.array([[1, 0, 1, 1, 0, 0, 1, 0, 1, 1]], dtype=np.uint8)
    packed = pack_db1(bits)
    assert packed.shape == (1, 2)
    out = unpack_db1(packed)[0, :10]
    assert np.array_equal(out, bits[0])


def test_page_layout_packed():
    layout = NodeLayout(record_bytes=480, count=1000)
    assert layout.records_per_page == 8
    assert layout.node_offset(0) == 0
    assert layout.node_page(8) == 0
    assert layout.node_page(9) == 1
    assert layout.total_pages == 1000 * 480 // 4096 + 1


def test_page_layout_aligned():
    layout = NodeLayout(record_bytes=4608, count=10)
    assert layout.pages_per_record == 2
    assert layout.node_offset(1) == 8192
    assert layout.node_pages(1) == [2, 3]
    assert layout.total_pages == 20


def test_pages_and_extract(tmp_path):
    layout = NodeLayout(record_bytes=64, count=100)
    w = PageWriter(tmp_path / "index.pages", layout)
    records = [bytes([i % 256]) * 64 for i in range(100)]
    for i, r in enumerate(records):
        assert w.append(r) == i
    total = w.flush()
    assert total % 4096 == 0
    with DirectPageReader(tmp_path / "index.pages", direct=False) as reader:
        stats = IOStats()
        pages = reader.read_pages([0, 1, 0, 1], stats=stats)
        assert stats.duplicate_pages_removed == 2
        assert stats.io_requests == 1  # adjacent pages 0..1 coalesced into one run
        assert stats.coalesced_requests == 1
        assert stats.sectors_4k == 2
        assert extract_node(pages, layout, 0) == records[0]
        assert extract_node(pages, layout, 63) == records[63]


def test_cache_is_namespaced_by_file(tmp_path):
    """Adjacency page 0 must never satisfy a payload-file page-0 read."""
    layout = NodeLayout(record_bytes=64, count=64)
    first = PageWriter(tmp_path / "adjacency.pages", layout)
    second = PageWriter(tmp_path / "payload.pages", layout)
    for _ in range(64):
        first.append(b"A" * 64)
        second.append(b"P" * 64)
    first.flush()
    second.flush()
    cache = QueryPageCache()
    with DirectPageReader(tmp_path / "adjacency.pages", direct=False) as adj_reader:
        assert adj_reader.read_pages([0], query_cache=cache)[0][:8] == b"A" * 8
    with DirectPageReader(tmp_path / "payload.pages", direct=False) as payload_reader:
        assert payload_reader.read_pages([0], query_cache=cache)[0][:8] == b"P" * 8


def test_buffered_reader_not_reported_as_direct(tmp_path):
    layout = NodeLayout(record_bytes=64, count=64)
    writer = PageWriter(tmp_path / "buffered.pages", layout)
    for _ in range(64):
        writer.append(b"B" * 64)
    writer.flush()
    with DirectPageReader(tmp_path / "buffered.pages", direct=False) as reader:
        assert not reader.direct_ok
        assert reader.fallback_reason == "direct I/O explicitly disabled"


def test_straddling_record_extract(tmp_path):
    # 480-byte records straddle 4 KiB pages (e.g. offset 4000)
    layout = NodeLayout(record_bytes=480, count=64)
    w = PageWriter(tmp_path / "straddle.pages", layout)
    records = [bytes([i % 256]) * 480 for i in range(64)]
    for i, r in enumerate(records):
        w.append(r)
    w.flush()
    assert layout.node_offset(8) % 4096 + 480 > 4096  # straddles pages 0/1
    assert layout.node_span_pages(8) == [0, 1]
    with DirectPageReader(tmp_path / "straddle.pages", direct=False) as reader:
        pages = reader.read_pages(
            {p for i in range(64) for p in layout.node_span_pages(i)}
        )
        for i in range(64):
            assert extract_node(pages, layout, i) == records[i]


def test_pages_aligned_writer(tmp_path):
    layout = NodeLayout(record_bytes=4096, count=3)
    w = PageWriterAligned(tmp_path / "aligned.pages", layout)
    for i in range(3):
        w.append(bytes([i]) * 4096)
    total = w.flush()
    assert total == 12288


def test_plan_reads_coalescing():
    stats = IOStats()
    hits, misses, stats = plan_reads([1, 2, 3, 5, 5, 6], QueryPageCache(), None)
    assert stats.requested_pages == 6
    assert stats.duplicate_pages_removed == 1
    assert stats.pre_coalesce_requests == 5
    assert stats.io_requests == 2  # [1,2,3] and [5,6]
    assert stats.coalesced_requests == 3
    assert stats.bytes_read == 5 * 4096


def test_index_builder_verify(tmp_path):
    builder = IndexBuilder(
        tmp_path / "idx",
        layer="05A",
        method="PQ_4bit",
        dataset="gist",
        config_id="smoke",
        base_count=100,
        dimension=960,
        record_bytes=480,
        resident_bytes_per_node=0,
    )
    for i in range(100):
        builder.append(bytes([i % 256]) * 480)
    builder.finish(nominal_bpd=4.0, effective_bpd=4.0)
    meta = verify_index(tmp_path / "idx", "05A", "PQ_4bit")
    assert meta["page_count"] == builder.layout.total_pages
    assert meta["pages_file_sha256"]


def test_quantizer_sizes_and_parity(qcls):
    rng = np.random.default_rng(3)
    x = rng.standard_normal((2000, 64)).astype(np.float32)
    qvec = rng.standard_normal((4, 64)).astype(np.float32)
    q = qcls(dim=64)
    q.train(x[:1500])
    codes = q.encode(x[1500:])
    expected_bytes = (64 * 4 + 7) // 8
    if qcls is SAQQuantizer4:
        expected_bytes = 32
    assert codes.shape[1] == expected_bytes
    qctx = q.prepare_query(qvec[0])
    d1 = q.distances_from_codes(qctx, codes[:10])
    # exact reconstruction must equal distances_from_codes
    d2 = q.distances_from_codes(qctx, codes[:10])
    np.testing.assert_allclose(d1, d2, rtol=1e-6)
    assert d1.shape == (10,)


def test_ours_quantizer_sizes_and_parity():
    rng = np.random.default_rng(4)
    x = rng.standard_normal((5000, 128)).astype(np.float32)
    qvec = rng.standard_normal((3, 128)).astype(np.float32)
    q = OursRaBitQ(dim=128)
    q.train(x[:4000])
    enc = q.encode_with_factors(x[4000:])
    assert enc["codes"].shape[1] == (128 // 8) + 48
    assert enc["norm_sqr"].shape == (1000,)
    assert enc["res_scale"].shape == (1000, q.n_blocks)
    assert np.all(enc["res_scale"] > 0)
    qctx = q.prepare_query(qvec[0])
    d = q.distances_from_codes(
        qctx,
        enc["codes"][:20],
        db1_scales=enc["db1_scale"][:20],
        norm_sqr=enc["norm_sqr"][:20],
        res_scale=enc["res_scale"][:20],
    )
    assert d.shape == (20,)
    assert np.all(np.isfinite(d))


def test_odirect_reader_if_supported(tmp_path):
    layout = NodeLayout(record_bytes=128, count=64)
    w = PageWriter(tmp_path / "io.pages", layout)
    for i in range(64):
        w.append(bytes([i]) * 128)
    w.flush()
    try:
        with DirectPageReader(tmp_path / "io.pages", direct=True) as reader:
            assert reader.direct_ok
    except OSError:
        print("skip: O_DIRECT unsupported on this filesystem")


def main() -> int:
    failed = 0
    tmp = Path(tempfile.mkdtemp(prefix="diskfair_test_"))
    cases = [
        (test_nibble_roundtrip, ()),
        (test_3bit_roundtrip, ()),
        (test_db1_roundtrip, ()),
        (test_page_layout_packed, ()),
        (test_page_layout_aligned, ()),
        (test_pages_and_extract, (tmp,)),
        (test_cache_is_namespaced_by_file, (tmp,)),
        (test_buffered_reader_not_reported_as_direct, (tmp,)),
        (test_straddling_record_extract, (tmp,)),
        (test_pages_aligned_writer, (tmp,)),
        (test_plan_reads_coalescing, ()),
        (test_index_builder_verify, (tmp,)),
        (test_quantizer_sizes_and_parity, (PQQuantizer4,)),
        (test_quantizer_sizes_and_parity, (SQQuantizer4,)),
        (test_quantizer_sizes_and_parity, (SAQQuantizer4,)),
        (test_ours_quantizer_sizes_and_parity, ()),
        (test_odirect_reader_if_supported, (tmp,)),
    ]
    for fn, args in cases:
        try:
            fn(*args)
            print(f"PASS {fn.__name__} {getattr(args[0], '__name__', '') if args else ''}")
        except Exception as exc:
            import traceback

            print(f"FAIL {fn.__name__}: {exc}")
            traceback.print_exc()
            failed += 1
    print(f"{len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
