// Unit tests for the Ours query-time coarse-filter codecs:
//   full (baseline) / b1 (1-bit symmetric popcount) / int4 / int8
//   (asymmetric quantized query). The DB coarse operand is always the same
//   1-bit MSB sidecar.
//
// Build (mirrors the rabitq bridge toolchain):
//   g++ -std=c++17 -O2 -march=native -fopenmp -fPIC -I <repo> \
//       -DHNSWLIB_RABITQ_TESTING Ours/tests/query_coarse_codec_test.cpp -o /tmp/qcc_test
//   /tmp/qcc_test

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>

#include "Ours/core/hnswlib/space_rabitq.h"

namespace {

using hnswlib::PaperPruneEstimate;
using hnswlib::PaperPruneFactors;
using hnswlib::QueryCoarseCodec;
using hnswlib::RaBitQSpace;
using PreparedQuery = hnswlib::RaBitQSpace::PreparedQuery;

float true_sqr_dist(const std::vector<float> &a, const std::vector<float> &b) {
    double sum = 0.0;
    for (size_t i = 0; i < a.size(); ++i) {
        const double d = static_cast<double>(a[i]) - static_cast<double>(b[i]);
        sum += d * d;
    }
    return static_cast<float>(sum);
}

size_t topk_overlap(
    const std::vector<float> &by_estimate,
    const std::vector<float> &by_true,
    size_t k) {
    std::vector<size_t> est_order(by_estimate.size());
    std::vector<size_t> true_order(by_true.size());
    for (size_t i = 0; i < est_order.size(); ++i) {
        est_order[i] = i;
        true_order[i] = i;
    }
    std::stable_sort(est_order.begin(), est_order.end(), [&](size_t a, size_t b) {
        return by_estimate[a] < by_estimate[b];
    });
    std::stable_sort(true_order.begin(), true_order.end(), [&](size_t a, size_t b) {
        return by_true[a] < by_true[b];
    });
    size_t overlap = 0;
    for (size_t i = 0; i < k; ++i) {
        for (size_t j = 0; j < k; ++j) {
            if (est_order[i] == true_order[j]) {
                ++overlap;
                break;
            }
        }
    }
    return overlap;
}

double rank_correlation(
    const std::vector<float> &by_estimate,
    const std::vector<float> &by_true) {
    const size_t n = by_estimate.size();
    std::vector<size_t> est_order(n);
    std::vector<size_t> true_order(n);
    for (size_t i = 0; i < n; ++i) {
        est_order[i] = i;
        true_order[i] = i;
    }
    std::stable_sort(est_order.begin(), est_order.end(), [&](size_t a, size_t b) {
        return by_estimate[a] < by_estimate[b];
    });
    std::stable_sort(true_order.begin(), true_order.end(), [&](size_t a, size_t b) {
        return by_true[a] < by_true[b];
    });
    std::vector<size_t> rank_est(n);
    std::vector<size_t> rank_true(n);
    for (size_t i = 0; i < n; ++i) {
        rank_est[est_order[i]] = i;
        rank_true[true_order[i]] = i;
    }
    double mean_est = 0.0;
    double mean_true = 0.0;
    for (size_t i = 0; i < n; ++i) {
        mean_est += static_cast<double>(rank_est[i]);
        mean_true += static_cast<double>(rank_true[i]);
    }
    mean_est /= static_cast<double>(n);
    mean_true /= static_cast<double>(n);
    double num = 0.0;
    double den_e = 0.0;
    double den_t = 0.0;
    for (size_t i = 0; i < n; ++i) {
        const double de = static_cast<double>(rank_est[i]) - mean_est;
        const double dt = static_cast<double>(rank_true[i]) - mean_true;
        num += de * dt;
        den_e += de * de;
        den_t += dt * dt;
    }
    return num / std::sqrt(den_e * den_t);
}

int fail(const char *message) {
    std::fprintf(stderr, "FAIL: %s\n", message);
    return 1;
}

int check_bounds(
    const char *label,
    const std::vector<float> &bounds,
    const std::vector<float> &true_dist) {
    size_t safe = 0;
    for (size_t i = 0; i < bounds.size(); ++i) {
        if (bounds[i] <= true_dist[i] + 1e-3f) {
            ++safe;
        }
    }
    if (safe * 100 < bounds.size() * 95) {
        std::fprintf(stderr, "%s safety: %zu/%zu bounds exceed true distance\n",
                     label, bounds.size() - safe, bounds.size());
        return 1;
    }
    const size_t overlap = topk_overlap(bounds, true_dist, 8);
    const double corr = rank_correlation(bounds, true_dist);
    std::printf(
        "%s: top8_overlap=%zu rank_corr=%.4f safety=%zu/%zu\n",
        label, overlap, corr, safe, bounds.size());
    if (overlap < 3 || corr < 0.5) {
        return 1;
    }
    return 0;
}

}  // namespace

int main() {
    const size_t dim = 128;
    const size_t n = 64;
    const float epsilon0 = 1.9f;

    RaBitQSpace::ResidualQuantizationConfig residual_config;
    residual_config.bits = RaBitQSpace::ResidualQuantizationBits::B4;
    residual_config.block_size = 16;
    residual_config.mse_optimal_scale = true;
    residual_config.scale_fp16 = true;
    RaBitQSpace space(dim, 1, 100, true, residual_config);
    std::vector<float> center(dim, 0.05f);
    space.setGlobalCenter(center.data());

    std::mt19937 rng(20260813);
    std::normal_distribution<float> nd(0.0f, 1.0f);

    std::vector<std::vector<float>> db(n, std::vector<float>(dim));
    const size_t full_bytes = space.get_full_data_size();
    const size_t compact_bytes = space.get_data_size();
    std::vector<std::vector<uint8_t>> full(n, std::vector<uint8_t>(full_bytes));
    std::vector<std::vector<uint8_t>> compact(n, std::vector<uint8_t>(compact_bytes));
    for (size_t i = 0; i < n; ++i) {
        for (size_t j = 0; j < dim; ++j) {
            db[i][j] = nd(rng);
        }
        space.encodeVectorFull(db[i].data(), full[i].data());
        space.copyCompactPayloadFromFull(full[i].data(), compact[i].data());
    }

    const size_t msb_bytes = space.paper_msb_code_bytes();
    std::vector<uint8_t> msb(n * msb_bytes, 0);
    std::vector<PaperPruneFactors<float>> factors(n);
    const size_t residual_bytes = space.get_residual_disk_record_bytes();
    std::vector<uint8_t> residual(n * residual_bytes, 0);
    for (size_t i = 0; i < n; ++i) {
        factors[i] = space.extract_paper_prune_sidecar(
            compact[i].data(), msb.data() + i * msb_bytes);
        if (!factors[i].valid) {
            return fail("extract_paper_prune_sidecar produced invalid factors");
        }
        space.copyResidualRecordFromFull(
            full[i].data(), residual.data() + i * residual_bytes);
    }

    // ---- full: baseline sidecar path stays valid ----
    {
        space.set_query_coarse_codec(QueryCoarseCodec::Full);
        std::vector<float> query(dim);
        for (float &value : query) {
            value = nd(rng);
        }
        const void *prepared = space.prepare_query(query.data());
        if (prepared == nullptr) {
            return fail("prepare_query failed");
        }
        std::vector<float> true_dist(n);
        for (size_t i = 0; i < n; ++i) {
            true_dist[i] = true_sqr_dist(query, db[i]);
        }
        std::vector<float> bounds(n);
        for (size_t i = 0; i < n; ++i) {
            const PaperPruneEstimate<float> est =
                space.compute_paper_prune_estimate_sidecar(
                    prepared, msb.data() + i * msb_bytes, factors[i], epsilon0);
            if (!est.valid || !std::isfinite(est.lower_bound)) {
                return fail("full codec produced an invalid bound");
            }
            bounds[i] = est.lower_bound;
        }
        if (check_bounds("full", bounds, true_dist) != 0) {
            return fail("full codec bound quality checks");
        }
        space.release_query(prepared);
    }

    // ---- b1: popcount kernel must match the scalar bit reference exactly,
    // and short_ip must follow short_ip = signed_dot * b1_short_scale / 16 ----
    {
        space.set_query_coarse_codec(QueryCoarseCodec::B1);
        std::vector<float> query(dim);
        for (float &value : query) {
            value = nd(rng);
        }
        const void *prepared = space.prepare_query(query.data());
        if (prepared == nullptr) {
            return fail("prepare_query failed");
        }
        std::vector<float> true_dist(n);
        for (size_t i = 0; i < n; ++i) {
            true_dist[i] = true_sqr_dist(query, db[i]);
        }
        const PreparedQuery &prepared_view = *static_cast<const PreparedQuery *>(prepared);
        const auto &query_ctx = prepared_view.centroid_queries[0];
        std::vector<float> bounds(n);
        for (size_t i = 0; i < n; ++i) {
            const uint8_t *msb_ptr = msb.data() + i * msb_bytes;
            const PaperPruneEstimate<float> est =
                space.compute_paper_prune_estimate_sidecar(
                    prepared, msb_ptr, factors[i], epsilon0);
            if (!est.valid || !std::isfinite(est.lower_bound)) {
                return fail("b1 codec produced an invalid bound");
            }
            // Scalar bit-by-bit signed dot reference over the same bytes.
            int64_t scalar_dot = 0;
            for (size_t bit = 0; bit < msb_bytes * 8U; ++bit) {
                const bool qpos = ((query_ctx.b1_code[bit >> 3U] >> (bit & 7U)) & 1U) != 0;
                const bool xpos = ((msb_ptr[bit >> 3U] >> (bit & 7U)) & 1U) != 0;
                scalar_dot += (qpos == xpos) ? 1 : -1;
            }
            const float kernel_dot = space.b1_signed_dot_for_test(prepared, msb_ptr);
            if (kernel_dot != static_cast<float>(scalar_dot)) {
                return fail("b1 popcount kernel differs from scalar reference");
            }
            const float expected_short_ip =
                kernel_dot * space.b1_short_scale_for_test(prepared) / 8.0f;
            if (std::fabs(est.short_ip - expected_short_ip) >
                1e-4f * std::max(1.0f, std::fabs(expected_short_ip))) {
                return fail("b1 short_ip does not follow the symmetric formula");
            }
            bounds[i] = est.lower_bound;
        }
        if (check_bounds("b1", bounds, true_dist) != 0) {
            return fail("b1 codec bound quality checks");
        }
        space.release_query(prepared);
    }

    // ---- Legacy 4-bit-record INT estimator reference. This is not the
    //      query-codec benchmark's first-stage coarse path. ----
    for (QueryCoarseCodec codec : {QueryCoarseCodec::Int4, QueryCoarseCodec::Int8}) {
        space.set_query_coarse_codec(codec);
        std::vector<float> query(dim);
        for (float &value : query) {
            value = nd(rng);
        }
        const void *prepared = space.prepare_query(query.data());
        if (prepared == nullptr) {
            return fail("prepare_query failed");
        }
        std::vector<float> true_dist(n);
        for (size_t i = 0; i < n; ++i) {
            true_dist[i] = true_sqr_dist(query, db[i]);
        }
        const bool int8_mode = codec == QueryCoarseCodec::Int8;
        std::vector<float> bounds(n);
        for (size_t i = 0; i < n; ++i) {
            const PaperPruneEstimate<float> est =
                space.compute_paper_prune_estimate_int_codec(
                    prepared, compact[i].data(), factors[i], epsilon0);
            if (!est.valid || !std::isfinite(est.lower_bound)) {
                return fail("int codec produced an invalid bound");
            }
            bounds[i] = est.lower_bound;
        }
        if (int8_mode) {
            double err8 = 0.0;
            for (size_t i = 0; i < n; ++i) {
                err8 += std::fabs(static_cast<double>(bounds[i]) - true_dist[i]);
            }
            space.set_query_coarse_codec(QueryCoarseCodec::Int4);
            double err4 = 0.0;
            const void *prepared4 = space.prepare_query(query.data());
            if (prepared4 == nullptr) {
                return fail("prepare_query failed");
            }
            for (size_t i = 0; i < n; ++i) {
                const PaperPruneEstimate<float> e4 =
                    space.compute_paper_prune_estimate_int_codec(
                        prepared4, compact[i].data(), factors[i], epsilon0);
                err4 += std::fabs(static_cast<double>(e4.lower_bound) - true_dist[i]);
            }
            space.release_query(prepared4);
            if (err8 > err4 * 1.05) {
                return fail("int8 bound is not tighter than int4 on average");
            }
            space.set_query_coarse_codec(codec);
        }
        if (check_bounds(
                int8_mode ? "int8" : "int4", bounds, true_dist) != 0) {
            return fail("int codec bound quality checks");
        }
        space.release_query(prepared);
    }

    // ---- INT4/INT8 coarse gate: direct masked integer sum over fixed 1-bit DB ----
    for (QueryCoarseCodec codec : {QueryCoarseCodec::Int4, QueryCoarseCodec::Int8}) {
        space.set_query_coarse_codec(codec);
        std::vector<float> query(dim);
        for (float &value : query) {
            value = nd(rng);
        }
        const void *prepared = space.prepare_query(query.data());
        if (prepared == nullptr) {
            return fail("prepare_query failed");
        }
        std::vector<float> true_dist(n);
        for (size_t i = 0; i < n; ++i) {
            true_dist[i] = true_sqr_dist(query, db[i]);
        }
        const PreparedQuery &prepared_view = *static_cast<const PreparedQuery *>(prepared);
        const auto &query_ctx = prepared_view.centroid_queries[0];
        const bool int8_mode = codec == QueryCoarseCodec::Int8;
        const int8_t *x = int8_mode ? query_ctx.int8_code.data() : query_ctx.int4_code.data();
        const float scale = int8_mode ? query_ctx.int8_scale : query_ctx.int4_scale;
        const int64_t code_sum =
            int8_mode ? query_ctx.int8_code_sum : query_ctx.int4_code_sum;
        // SIMD preparation must consume the exact legacy dither stream and
        // produce byte-identical scalar quantization codes.
        uint64_t dither_seed = 1469598103934665603ULL;
        const uint8_t *raw_bytes =
            reinterpret_cast<const uint8_t *>(query.data());
        for (size_t i = 0; i < dim * sizeof(float); ++i) {
            dither_seed ^= raw_bytes[i];
            dither_seed *= 1099511628211ULL;
        }
        dither_seed ^= 0x9E3779B97F4A7C15ULL +
            (dither_seed << 6U) + (dither_seed >> 2U);
        std::mt19937_64 dither_rng(dither_seed);
        std::uniform_real_distribution<double> u01(0.0, 1.0);
        int64_t scalar_code_sum = 0;
        for (size_t i = 0; i < dim; ++i) {
            const double u8 = u01(dither_rng);
            const double u4 = u01(dither_rng);
            int32_t expected_code = static_cast<int32_t>(std::floor(
                static_cast<double>(query_ctx.rotated_residual[i]) /
                    static_cast<double>(scale) +
                (int8_mode ? u8 : u4)));
            expected_code = int8_mode
                ? std::max(-128, std::min(127, expected_code))
                : std::max(-8, std::min(7, expected_code));
            if (x[i] != static_cast<int8_t>(expected_code)) {
                return fail(
                    "SIMD INT query preparation differs from scalar code");
            }
            scalar_code_sum += expected_code;
        }
        if (scalar_code_sum != code_sum) {
            return fail(
                "SIMD INT query preparation code sum differs from scalar");
        }
        std::vector<float> bounds(n);
        for (size_t i = 0; i < n; ++i) {
            const uint8_t *msb_ptr = msb.data() + i * msb_bytes;
            const PaperPruneEstimate<float> est =
                space.compute_paper_prune_estimate_int_msb_gate(
                    prepared, msb_ptr, factors[i], epsilon0);
            if (!est.valid || !std::isfinite(est.lower_bound)) {
                return fail("int msb gate produced an invalid bound");
            }
            const PaperPruneEstimate<float> dispatched =
                space.compute_paper_prune_estimate_sidecar(
                    prepared, msb_ptr, factors[i], epsilon0);
            if (dispatched.valid != est.valid ||
                std::memcmp(&dispatched.lower_bound, &est.lower_bound,
                            sizeof(float)) != 0 ||
                std::memcmp(&dispatched.short_ip, &est.short_ip,
                            sizeof(float)) != 0) {
                return fail("int codec sidecar dispatch did not use the 1-bit DB gate");
            }
            int64_t selected = 0;
            for (size_t bit = 0; bit < dim; ++bit) {
                if (((msb_ptr[bit >> 3U] >> (bit & 7U)) & 1U) != 0) {
                    selected += x[bit];
                }
            }
            const float expected_short_ip = 0.5f * scale *
                static_cast<float>(static_cast<int64_t>(2) * selected - code_sum);
            if (std::fabs(est.short_ip - expected_short_ip) >
                1e-3f * std::max(1.0f, std::fabs(expected_short_ip))) {
                return fail("int msb gate short_ip differs from scalar reference");
            }
            const float reused_distance =
                space.query_distance_with_quantized_paper_msb(
                    prepared, compact[i].data(), est.short_ip);
            const PaperPruneEstimate<float> long_est =
                space.compute_paper_prune_estimate_int_codec(
                    prepared, compact[i].data(), factors[i], epsilon0);
            const float reference_distance =
                long_est.lower_bound + long_est.error_bound;
            if (std::fabs(reused_distance - reference_distance) >
                2e-3f * std::max(1.0f, std::fabs(reference_distance))) {
                return fail("quantized remaining-3-bit reuse differs from full 4-bit integer dot");
            }
            bounds[i] = est.lower_bound;
        }
        if (check_bounds(
                int8_mode ? "int8-msb-gate" : "int4-msb-gate",
                bounds, true_dist) != 0) {
            return fail("int msb gate bound quality checks");
        }
        const size_t count = 32;
        std::vector<const uint8_t *> msb_ptrs(count);
        std::vector<PaperPruneFactors<float>> factor_vec(count);
        for (size_t c = 0; c < count; ++c) {
            msb_ptrs[c] = msb.data() + c * msb_bytes;
            factor_vec[c] = factors[c];
        }
        PaperPruneEstimate<float> single[count];
        PaperPruneEstimate<float> batched[count];
        for (size_t c = 0; c < count; ++c) {
            single[c] = space.compute_paper_prune_estimate_int_msb_gate(
                prepared, msb_ptrs[c], factor_vec[c], epsilon0);
        }
        space.compute_paper_prune_estimate_int_msb_gate_batch(
            prepared, msb_ptrs.data(), factor_vec.data(), epsilon0, batched);
        for (size_t c = 0; c < count; ++c) {
            if (single[c].valid != batched[c].valid ||
                std::memcmp(&single[c].lower_bound, &batched[c].lower_bound,
                            sizeof(float)) != 0 ||
                std::memcmp(&single[c].short_ip, &batched[c].short_ip,
                            sizeof(float)) != 0) {
                return fail("int msb gate batch differs from per-candidate path");
            }
        }
        std::vector<uint8_t> compact_stage(count * compact_bytes);
        uint32_t ids[count];
        float short_ips[count];
        float expected_distances[count];
        float batch_distances[count];
        for (size_t c = 0; c < count; ++c) {
            std::memcpy(
                compact_stage.data() + c * compact_bytes,
                compact[c].data(), compact_bytes);
            ids[c] = static_cast<uint32_t>((c * 13U) % count);
            short_ips[c] = single[ids[c]].short_ip;
            expected_distances[c] =
                space.query_distance_with_quantized_paper_msb(
                    prepared, compact[ids[c]].data(), short_ips[c]);
        }
        for (size_t batch_count : {size_t(1), size_t(7), size_t(8),
                                   size_t(9), size_t(29), count}) {
            space.query_distance_with_quantized_paper_msb_batch_by_id(
                prepared, ids, batch_count, compact_stage.data(), compact_bytes,
                short_ips, batch_distances);
            for (size_t c = 0; c < batch_count; ++c) {
                if (std::memcmp(
                        &expected_distances[c], &batch_distances[c],
                        sizeof(float)) != 0) {
                    return fail(
                        "quantized remaining-3-bit batch differs from single path");
                }
            }
        }
        space.release_query(prepared);
    }

    // ---- Fixed-1-bit-DB sidecar batch vs per-candidate bit-exact parity
    //      for every query codec (full/b1/int4/int8) ----
    for (QueryCoarseCodec codec : {QueryCoarseCodec::Full, QueryCoarseCodec::B1,
                                   QueryCoarseCodec::Int4, QueryCoarseCodec::Int8}) {
        space.set_query_coarse_codec(codec);
        std::vector<float> query(dim);
        for (float &value : query) {
            value = nd(rng);
        }
        const void *prepared = space.prepare_query(query.data());
        if (prepared == nullptr) {
            return fail("prepare_query failed");
        }
        const size_t count = 32;
        std::vector<const uint8_t *> msb_ptrs(count);
        std::vector<PaperPruneFactors<float>> factor_vec(count);
        for (size_t c = 0; c < count; ++c) {
            msb_ptrs[c] = msb.data() + c * msb_bytes;
            factor_vec[c] = factors[c];
        }
        PaperPruneEstimate<float> single[count];
        PaperPruneEstimate<float> batched[count];
        for (size_t c = 0; c < count; ++c) {
            single[c] = space.compute_paper_prune_estimate_sidecar(
                prepared, msb_ptrs[c], factor_vec[c], epsilon0);
        }
        space.compute_paper_prune_estimate_sidecar_batch(
            prepared, msb_ptrs.data(), factor_vec.data(), epsilon0, batched);
        for (size_t c = 0; c < count; ++c) {
            if (single[c].valid != batched[c].valid ||
                std::memcmp(&single[c].lower_bound, &batched[c].lower_bound,
                            sizeof(float)) != 0 ||
                std::memcmp(&single[c].short_ip, &batched[c].short_ip,
                            sizeof(float)) != 0) {
                std::fprintf(
                    stderr, "batch parity mismatch codec=%d c=%zu\n",
                    static_cast<int>(codec), c);
                return fail("32-lane batch differs from per-candidate path");
            }
        }
        if (codec == QueryCoarseCodec::Full) {
            uint32_t gate_ids[count];
            PaperPruneEstimate<float> direct_gate[count];
            for (size_t c = 0; c < count; ++c) {
                gate_ids[c] = static_cast<uint32_t>((c * 13U) % count);
            }
            for (size_t batch_count : {size_t(1), size_t(7), size_t(8),
                                       size_t(9), size_t(29), count}) {
                space.compute_full_paper_prune_estimate_batch_by_id(
                    prepared, gate_ids, batch_count,
                    msb.data(), msb_bytes,
                    factor_vec.data(), sizeof(PaperPruneFactors<float>),
                    epsilon0, direct_gate);
                for (size_t c = 0; c < batch_count; ++c) {
                    const PaperPruneEstimate<float> &expected =
                        single[gate_ids[c]];
                    if (expected.valid != direct_gate[c].valid ||
                        std::memcmp(
                            &expected.lower_bound,
                            &direct_gate[c].lower_bound,
                            sizeof(float)) != 0 ||
                        std::memcmp(
                            &expected.short_ip,
                            &direct_gate[c].short_ip,
                            sizeof(float)) != 0) {
                        return fail(
                            "Full direct-by-id gate differs from single path");
                    }
                }
            }
            std::vector<uint8_t> compact_stage(count * compact_bytes);
            uint32_t ids[count];
            float short_ips[count];
            float expected_distances[count];
            float batch_distances[count];
            for (size_t c = 0; c < count; ++c) {
                std::memcpy(
                    compact_stage.data() + c * compact_bytes,
                    compact[c].data(), compact_bytes);
                ids[c] = static_cast<uint32_t>((c * 13U) % count);
                short_ips[c] = single[ids[c]].short_ip;
                expected_distances[c] = space.query_distance_with_paper_msb(
                    prepared, compact[ids[c]].data(), short_ips[c]);
            }
            for (size_t batch_count : {size_t(1), size_t(7), size_t(8),
                                       size_t(9), size_t(29), count}) {
                space.query_distance_with_paper_msb_batch_by_id(
                    prepared, ids, batch_count,
                    compact_stage.data(), compact_bytes,
                    short_ips, batch_distances);
                for (size_t c = 0; c < batch_count; ++c) {
                    if (std::memcmp(
                            &expected_distances[c], &batch_distances[c],
                            sizeof(float)) != 0) {
                        return fail(
                            "FP32 remaining-3-bit batch differs from single path");
                    }
                }
            }
        }
        if (codec == QueryCoarseCodec::B1) {
            uint32_t ids[count];
            PaperPruneEstimate<float> by_id[count];
            for (size_t c = 0; c < count; ++c) {
                ids[c] = static_cast<uint32_t>((c * 13U) % count);
            }
            for (size_t batch_count : {size_t(1), size_t(7), size_t(8),
                                       size_t(9), size_t(29), count}) {
                space.compute_b1_paper_prune_estimate_batch_by_id(
                    prepared, ids, batch_count,
                    msb.data(), msb_bytes,
                    factor_vec.data(), sizeof(PaperPruneFactors<float>),
                    epsilon0, by_id);
                for (size_t c = 0; c < batch_count; ++c) {
                    const PaperPruneEstimate<float> &expected = single[ids[c]];
                    if (expected.valid != by_id[c].valid ||
                        std::memcmp(
                            &expected.lower_bound, &by_id[c].lower_bound,
                            sizeof(float)) != 0 ||
                        std::memcmp(
                            &expected.short_ip, &by_id[c].short_ip,
                            sizeof(float)) != 0) {
                        return fail(
                            "B1 direct-by-id batch differs from single path");
                    }
                }
            }
        }
        if (codec == QueryCoarseCodec::Int4 ||
            codec == QueryCoarseCodec::Int8) {
            uint32_t ids[count];
            PaperPruneEstimate<float> by_id[count];
            for (size_t c = 0; c < count; ++c) {
                ids[c] = static_cast<uint32_t>((c * 13U) % count);
            }
            for (size_t batch_count : {size_t(1), size_t(7), size_t(8),
                                       size_t(9), size_t(29), count}) {
                space.compute_int_paper_prune_estimate_batch_by_id(
                    prepared, ids, batch_count,
                    msb.data(), msb_bytes,
                    factor_vec.data(), sizeof(PaperPruneFactors<float>),
                    epsilon0, by_id);
                for (size_t c = 0; c < batch_count; ++c) {
                    const PaperPruneEstimate<float> &expected = single[ids[c]];
                    if (expected.valid != by_id[c].valid ||
                        std::memcmp(
                            &expected.lower_bound, &by_id[c].lower_bound,
                            sizeof(float)) != 0 ||
                        std::memcmp(
                            &expected.short_ip, &by_id[c].short_ip,
                            sizeof(float)) != 0) {
                        return fail(
                            "INT direct-by-id batch differs from single path");
                    }
                }
            }
        }

        // Mode-0 always restores the complete FP32-query 4-bit distance. The
        // new eight-record kernel must be bit-exact for all query codecs.
        {
            std::vector<uint8_t> compact_stage(count * compact_bytes);
            uint32_t ids[count];
            float expected_distances[count];
            float batch_distances[count];
            for (size_t c = 0; c < count; ++c) {
                std::memcpy(
                    compact_stage.data() + c * compact_bytes,
                    compact[c].data(), compact_bytes);
                ids[c] = static_cast<uint32_t>((c * 13U) % count);
                expected_distances[c] = space.query_distance(
                    prepared, compact[ids[c]].data());
            }
            for (size_t batch_count : {size_t(1), size_t(7), size_t(8),
                                       size_t(9), size_t(29), count}) {
                space.query_distance_batch_by_id(
                    prepared, ids, batch_count,
                    compact_stage.data(), compact_bytes, batch_distances);
                for (size_t c = 0; c < batch_count; ++c) {
                    if (std::memcmp(
                            &expected_distances[c], &batch_distances[c],
                            sizeof(float)) != 0) {
                        return fail(
                            "complete 4-bit batch differs from single path");
                    }
                }
            }
        }
        space.release_query(prepared);
    }

    // ---- residual INT4 rerank: eight-record interleaving must remain
    //      bit-exact with the old per-candidate AVX2 path ----
    {
        space.set_query_coarse_codec(QueryCoarseCodec::Full);
        std::vector<float> query(dim);
        for (float &value : query) {
            value = nd(rng);
        }
        const void *prepared = space.prepare_query(query.data());
        if (prepared == nullptr) {
            return fail("prepare_query failed");
        }
        uint32_t ids[n];
        float long_distances[n];
        hnswlib::DistanceInterval expected[n];
        hnswlib::DistanceInterval batched[n];
        hnswlib::DistanceInterval fused[n];
        std::vector<uint8_t> compact_stage(n * compact_bytes);
        for (size_t c = 0; c < n; ++c) {
            std::memcpy(
                compact_stage.data() + c * compact_bytes,
                compact[c].data(), compact_bytes);
            ids[c] = static_cast<uint32_t>((c * 13U) % n);
            const size_t id = static_cast<size_t>(ids[c]);
            long_distances[c] = space.query_distance(
                prepared, compact[id].data());
            expected[c] = space.compute_residual_distance_interval_from_record(
                prepared,
                compact[id].data(),
                residual.data() + id * residual_bytes,
                long_distances[c]);
        }
        for (size_t batch_count : {size_t(1), size_t(7), size_t(8),
                                   size_t(9), size_t(29), n}) {
            space.compute_residual_distance_intervals_batch_by_id(
                prepared, ids, batch_count,
                compact_stage.data(), compact_bytes,
                residual.data(), residual_bytes,
                long_distances, batched);
            for (size_t c = 0; c < batch_count; ++c) {
                if (std::memcmp(
                        &expected[c].estimate, &batched[c].estimate,
                        sizeof(float)) != 0 ||
                    std::memcmp(
                        &expected[c].lower_bound, &batched[c].lower_bound,
                        sizeof(float)) != 0 ||
                    std::memcmp(
                        &expected[c].upper_bound, &batched[c].upper_bound,
                        sizeof(float)) != 0) {
                    return fail(
                        "residual INT4 batch differs from per-candidate path");
                }
            }
            space.compute_full_residual_distance_intervals_batch_by_id(
                prepared, ids, batch_count,
                compact_stage.data(), compact_bytes,
                residual.data(), residual_bytes, fused);
            for (size_t c = 0; c < batch_count; ++c) {
                if (std::memcmp(
                        &expected[c].estimate, &fused[c].estimate,
                        sizeof(float)) != 0 ||
                    std::memcmp(
                        &expected[c].lower_bound, &fused[c].lower_bound,
                        sizeof(float)) != 0 ||
                    std::memcmp(
                        &expected[c].upper_bound, &fused[c].upper_bound,
                        sizeof(float)) != 0) {
                    return fail(
                        "fused full/residual batch differs from legacy path");
                }
            }
        }
        space.release_query(prepared);
    }

    // ---- b1 main-distance sanity: finite and ordering correlates with truth ----
    {
        space.set_query_coarse_codec(QueryCoarseCodec::B1);
        std::vector<float> query(dim);
        for (float &value : query) {
            value = nd(rng);
        }
        const void *prepared = space.prepare_query(query.data());
        if (prepared == nullptr) {
            return fail("prepare_query failed");
        }
        std::vector<float> true_dist(n);
        std::vector<float> b1_dist(n);
        for (size_t i = 0; i < n; ++i) {
            true_dist[i] = true_sqr_dist(query, db[i]);
            b1_dist[i] = space.query_distance_b1(prepared, compact[i].data());
            if (!std::isfinite(b1_dist[i])) {
                return fail("b1 main distance is not finite");
            }
        }
        const size_t overlap = topk_overlap(b1_dist, true_dist, 8);
        const double corr = rank_correlation(b1_dist, true_dist);
        std::printf(
            "b1main: top8_overlap=%zu rank_corr=%.4f\n", overlap, corr);
        if (overlap < 3 || corr < 0.5) {
            return fail("b1 main distance ordering checks");
        }
        space.release_query(prepared);
    }

    std::printf("PASS: query_coarse_codec kernels (full/b1/int4/int8) ok\n");
    return 0;
}
