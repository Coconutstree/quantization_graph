# GIST1M: fixed 1-bit database × four query codecs

This single-thread microbenchmark isolates the distance-scan kernel requested
for GIST1M. It scans 10 queries one by one against all 1,000,000 database
vectors. Queries are never batched together.

## Representation

- GIST input dimension: 960.
- The input is padded to 1024 dimensions and transformed with the same seeded
  random-sign Hadamard rotation used by `RaBitQSpace`.
- The database always stores only the sign plane: 1024 bits = 128 bytes/vector.
- Database bits use a 32-candidate blocked/transposed layout. Every kernel
  computes 32 distances per outer iteration.
- Query codecs: FP32, scaled sign/B1, signed INT4, and signed INT8.
- INT4/INT8 use two's-complement query bit planes. Each plane is AND-ed with
  the fixed database sign code and reduced with an AVX-512 nibble-popcount LUT.
- B1 uses XOR followed by the same nibble-popcount LUT.
- FP32 uses AVX-512 mask-select and reuses each 16-float query block across 16
  database candidates.

The reported scan time includes writing one FP32 distance for every database
point. Reading/rotating/quantizing the query is reported separately. Loading or
building the database code is excluded from scan time.

Explicit L2 prefetch can be compared with no software prefetch via
`--prefetch-blocks`. Since the database scan is sequential, hardware prefetch
may already be sufficient; the benchmark reports both instead of assuming the
software hint is beneficial.

## Build and run

```bash
cmake -S experiments/04_query_codec_1bit_scan \
  -B /tmp/query_codec_1bit_scan_build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/query_codec_1bit_scan_build -j
/tmp/query_codec_1bit_scan_build/query_codec_1bit_scan \
  --base data/gist/gist_base.fvecs \
  --queries data/gist/gist_query.fvecs \
  --data-count 1000000 --query-count 10 --rounds 3 \
  --prefetch-blocks 0,8 --cpu 0
```

The first full run constructs a 128 MB cache at
`/tmp/gist1m_fht_1bit_block32_v1.bin`. Later runs validate and load it. The
per-scan results are written to
`results/04_query_codec_1bit_scan/gist/gist1m_query_codec_scan.csv`.

For a quick correctness/build check, use a distinct small cache:

```bash
/tmp/query_codec_1bit_scan_build/query_codec_1bit_scan \
  --data-count 3200 --query-count 2 --rounds 1 \
  --prefetch-blocks 0,2 --cache /tmp/gist_3200_1bit.bin \
  --csv /tmp/gist_3200_scan.csv --cpu 0
```

