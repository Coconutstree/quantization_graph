#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <new>
#include <random>
#include <vector>

#include "Ours/core/hnswlib/space_rabitq.h"
#include "Ours/core/hnswlib/vamana_index.h"

extern "C" {

struct RabitqDistanceInterval {
    float estimate;
    float lower_bound;
    float upper_bound;
};

struct RabitqPaperEstimate {
    float lower_bound;
    float short_ip;
    float alpha;
    float ip_hat;
    float error_bound;
    std::uint8_t valid;
};

struct RabitqPaperFactors {
    float norm_sqr;
    float data_norm;
    float cross_scale;
    float error_cross_scale;
    std::uint8_t valid;
};

static_assert(
    sizeof(RabitqPaperFactors) == sizeof(hnswlib::PaperPruneFactors<float>),
    "RabitqPaperFactors must match hnswlib::PaperPruneFactors<float> layout");

void *rabitq_space_new(
    std::size_t dim,
    std::uint32_t seed,
    std::size_t centroid_count,
    std::size_t residual_bits,
    std::size_t residual_block_size,
    bool residual_mse,
    bool residual_fp16) {
    try {
        hnswlib::RaBitQSpace::ResidualQuantizationConfig config;
        switch (residual_bits) {
            case 4:
                config.bits = hnswlib::RaBitQSpace::ResidualQuantizationBits::B4;
                break;
            case 8:
                config.bits = hnswlib::RaBitQSpace::ResidualQuantizationBits::B8;
                break;
            default:
                return nullptr;
        }
        if (centroid_count == 0 || centroid_count > 256) {
            return nullptr;
        }
        config.block_size = residual_block_size;
        config.enabled = true;
        config.enable_block_scaling = true;
        config.mse_optimal_scale = residual_mse;
        config.scale_fp16 = residual_fp16;
        return new hnswlib::RaBitQSpace(dim, centroid_count, seed, true, config);
    } catch (...) {
        return nullptr;
    }
}

void rabitq_space_delete(void *space) {
    delete static_cast<hnswlib::RaBitQSpace *>(space);
}

bool rabitq_space_set_center(void *space, const float *center) {
    try {
        static_cast<hnswlib::RaBitQSpace *>(space)->setGlobalCenter(center);
        return true;
    } catch (...) {
        return false;
    }
}

bool rabitq_space_set_centroids(
    void *space,
    const float *centroids,
    std::size_t centroid_count) {
    try {
        static_cast<hnswlib::RaBitQSpace *>(space)->setCentroids(centroids, centroid_count);
        return true;
    } catch (...) {
        return false;
    }
}

bool rabitq_train_centroids(
    const float *data,
    std::size_t count,
    std::size_t dim,
    std::size_t k,
    std::size_t sample_count,
    std::uint32_t seed,
    int iters,
    float *out) {
    try {
        if (data == nullptr || out == nullptr || count == 0 || dim == 0 ||
            k == 0 || k > 256 || sample_count == 0) {
            return false;
        }
        sample_count = std::min(sample_count, count);
        std::mt19937 rng(seed);
        std::vector<std::size_t> sample(sample_count);
        for (std::size_t i = 0; i < sample_count; ++i) {
            sample[i] = static_cast<std::size_t>(rng()) % count;
        }

        std::vector<float> centroids(k * dim);
        for (std::size_t c = 0; c < k; ++c) {
            std::copy(data + sample[c % sample_count] * dim,
                      data + (sample[c % sample_count] + 1) * dim,
                      centroids.data() + c * dim);
        }

        std::vector<float> next(k * dim);
        std::vector<std::size_t> counts(k);
        for (int iter = 0; iter < iters; ++iter) {
            std::fill(next.begin(), next.end(), 0.0f);
            std::fill(counts.begin(), counts.end(), 0);
            for (std::size_t i = 0; i < sample_count; ++i) {
                const float *v = data + sample[i] * dim;
                std::size_t best = 0;
                float best_d = std::numeric_limits<float>::infinity();
                for (std::size_t c = 0; c < k; ++c) {
                    const float *cen = centroids.data() + c * dim;
                    float d = 0.0f;
                    for (std::size_t j = 0; j < dim; ++j) {
                        const float diff = v[j] - cen[j];
                        d += diff * diff;
                    }
                    if (d < best_d) {
                        best_d = d;
                        best = c;
                    }
                }
                float *dst = next.data() + best * dim;
                for (std::size_t j = 0; j < dim; ++j) {
                    dst[j] += v[j];
                }
                ++counts[best];
            }
            for (std::size_t c = 0; c < k; ++c) {
                float *dst = centroids.data() + c * dim;
                if (counts[c] == 0) {
                    continue;
                }
                const float inv = 1.0f / static_cast<float>(counts[c]);
                for (std::size_t j = 0; j < dim; ++j) {
                    dst[j] = next[c * dim + j] * inv;
                }
            }
        }
        std::copy(centroids.begin(), centroids.end(), out);
        return true;
    } catch (...) {
        return false;
    }
}

std::size_t rabitq_full_record_bytes(const void *space) {
    return static_cast<const hnswlib::RaBitQSpace *>(space)->get_full_data_size();
}

std::size_t rabitq_compact_record_bytes(const void *space) {
    return const_cast<hnswlib::RaBitQSpace *>(
        static_cast<const hnswlib::RaBitQSpace *>(space))->get_data_size();
}

std::size_t rabitq_residual_record_bytes(const void *space) {
    return static_cast<const hnswlib::RaBitQSpace *>(space)->get_residual_disk_record_bytes();
}

std::size_t rabitq_residual_block_size(const void *space) {
    return static_cast<const hnswlib::RaBitQSpace *>(space)->get_residual_block_size();
}

std::size_t rabitq_residual_bits(const void *space) {
    return static_cast<const hnswlib::RaBitQSpace *>(space)->get_residual_bits();
}

bool rabitq_encode_full(const void *space, const float *raw, void *encoded_out) {
    try {
        static_cast<const hnswlib::RaBitQSpace *>(space)->encodeVectorFull(raw, encoded_out);
        return true;
    } catch (...) {
        return false;
    }
}

bool rabitq_copy_compact(const void *space, const void *full_encoded, void *compact_out) {
    try {
        static_cast<const hnswlib::RaBitQSpace *>(space)->copyCompactPayloadFromFull(
            full_encoded, compact_out);
        return true;
    } catch (...) {
        return false;
    }
}

bool rabitq_copy_residual_record(const void *space, const void *full_encoded, void *residual_out) {
    try {
        static_cast<const hnswlib::RaBitQSpace *>(space)->copyResidualRecordFromFull(
            full_encoded, residual_out);
        return true;
    } catch (...) {
        return false;
    }
}

bool rabitq_build_vamana_graph(
    void *space,
    const void *compact_records_in,
    std::size_t record_count,
    std::size_t compact_stride,
    std::size_t r,
    std::size_t l_build,
    float alpha,
    std::size_t beam_width,
    std::size_t batch_size,
    std::size_t refine_passes,
    std::size_t prune_candidate_cap,
    std::size_t build_early_stop_hops,
    std::uint32_t *degrees_out,
    std::uint32_t *edges_out,
    std::size_t edge_stride,
    std::uint8_t *paper_msb_out,
    void *paper_factors_out,
    std::size_t *paper_msb_stride_out,
    std::size_t *paper_factor_bytes_out) {
    try {
        if (space == nullptr || (compact_records_in == nullptr && record_count != 0) ||
            degrees_out == nullptr || edges_out == nullptr || edge_stride < r ||
            paper_msb_out == nullptr || paper_factors_out == nullptr ||
            paper_msb_stride_out == nullptr || paper_factor_bytes_out == nullptr) {
            return false;
        }
        hnswlib::RaBitQSpace *typed = static_cast<hnswlib::RaBitQSpace *>(space);
        const std::size_t compact_bytes = typed->get_data_size();
        if (compact_stride < compact_bytes) {
            return false;
        }
        std::vector<char> compact_records(record_count * compact_bytes);
        const char *compact = static_cast<const char *>(compact_records_in);
        for (std::size_t id = 0; id < record_count; ++id) {
            std::memcpy(
                compact_records.data() + id * compact_bytes,
                compact + id * compact_stride,
                compact_bytes);
        }

        hnswlib::VamanaIndex index(typed, record_count, r, l_build, alpha, beam_width);
        if (prune_candidate_cap != 0) {
            index.setPruneCandidateCap(prune_candidate_cap);
        }
        if (build_early_stop_hops != 0) {
            index.setBuildEarlyStopHops(build_early_stop_hops);
        }
        index.buildEncodedSymmetricBulk(std::move(compact_records), record_count, batch_size);
        if (refine_passes != 0) {
            index.refineGraphSymmetric(refine_passes, batch_size);
        }

        index.exportPaperPruneSidecar(paper_msb_out, paper_factors_out);
        *paper_msb_stride_out = index.paperPruneSidecarStride();
        *paper_factor_bytes_out = sizeof(hnswlib::PaperPruneFactors<float>);

        const auto &graph = index.graph();
        if (graph.size() != record_count) {
            return false;
        }
        for (std::size_t id = 0; id < record_count; ++id) {
            if (graph[id].size() > edge_stride) {
                return false;
            }
            degrees_out[id] = static_cast<std::uint32_t>(graph[id].size());
            std::uint32_t *dst = edges_out + id * edge_stride;
            for (std::size_t j = 0; j < graph[id].size(); ++j) {
                dst[j] = graph[id][j];
            }
        }
        return true;
    } catch (...) {
        return false;
    }
}

std::size_t rabitq_paper_msb_code_bytes(const void *space) {
    try {
        return static_cast<const hnswlib::RaBitQSpace *>(space)->paper_msb_code_bytes();
    } catch (...) {
        return 0;
    }
}

std::size_t rabitq_paper_factor_bytes() {
    return sizeof(hnswlib::PaperPruneFactors<float>);
}

float rabitq_symmetric_distance(const void *space, const void *lhs, const void *rhs) {
    try {
        hnswlib::RaBitQSpace *typed = const_cast<hnswlib::RaBitQSpace *>(
            static_cast<const hnswlib::RaBitQSpace *>(space));
        return typed->get_dist_func()(lhs, rhs, typed->get_dist_func_param());
    } catch (...) {
        return std::numeric_limits<float>::infinity();
    }
}

const void *rabitq_prepare_query(const void *space, const float *query) {
    try {
        return const_cast<hnswlib::RaBitQSpace *>(
            static_cast<const hnswlib::RaBitQSpace *>(space))->prepare_query(query);
    } catch (...) {
        return nullptr;
    }
}

void rabitq_release_query(const void *space, const void *prepared) {
    try {
        const_cast<hnswlib::RaBitQSpace *>(
            static_cast<const hnswlib::RaBitQSpace *>(space))->release_query(prepared);
    } catch (...) {
    }
}

float rabitq_query_distance(const void *space, const void *prepared, const void *encoded) {
    try {
        return const_cast<hnswlib::RaBitQSpace *>(
            static_cast<const hnswlib::RaBitQSpace *>(space))->query_distance(prepared, encoded);
    } catch (...) {
        return std::numeric_limits<float>::infinity();
    }
}

RabitqPaperEstimate rabitq_paper_estimate(
    const void *space,
    const void *prepared,
    const void *encoded,
    float epsilon0) {
    try {
        const hnswlib::PaperPruneEstimate<float> estimate =
            const_cast<hnswlib::RaBitQSpace *>(
                static_cast<const hnswlib::RaBitQSpace *>(space))->compute_paper_prune_estimate(
                prepared, encoded, epsilon0);
        return RabitqPaperEstimate{
            estimate.lower_bound,
            estimate.short_ip,
            estimate.alpha,
            estimate.ip_hat,
            estimate.error_bound,
            estimate.valid ? static_cast<std::uint8_t>(1) : static_cast<std::uint8_t>(0),
        };
    } catch (...) {
        return RabitqPaperEstimate{0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0};
    }
}

RabitqPaperEstimate rabitq_paper_estimate_sidecar(
    const void *space,
    const void *prepared,
    const std::uint8_t *msb_code,
    const void *factors,
    float epsilon0) {
    try {
        const hnswlib::PaperPruneEstimate<float> estimate =
            static_cast<const hnswlib::RaBitQSpace *>(space)
                ->compute_paper_prune_estimate_sidecar(
                    prepared,
                    msb_code,
                    *static_cast<const hnswlib::PaperPruneFactors<float> *>(factors),
                    epsilon0);
        return RabitqPaperEstimate{
            estimate.lower_bound,
            estimate.short_ip,
            estimate.alpha,
            estimate.ip_hat,
            estimate.error_bound,
            estimate.valid ? static_cast<std::uint8_t>(1) : static_cast<std::uint8_t>(0),
        };
    } catch (...) {
        return RabitqPaperEstimate{0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0};
    }
}

float rabitq_query_distance_with_paper(
    const void *space,
    const void *prepared,
    const void *encoded,
    float short_ip) {
    try {
        return const_cast<hnswlib::RaBitQSpace *>(
            static_cast<const hnswlib::RaBitQSpace *>(space))->query_distance_with_paper_msb(
            prepared, encoded, short_ip);
    } catch (...) {
        return std::numeric_limits<float>::infinity();
    }
}

RabitqDistanceInterval rabitq_residual_distance(
    const void *space,
    const void *prepared,
    const void *encoded,
    float long_distance) {
    try {
        const hnswlib::DistanceInterval interval =
            const_cast<hnswlib::RaBitQSpace *>(
                static_cast<const hnswlib::RaBitQSpace *>(space))->compute_residual_distance_interval(
                prepared, encoded, long_distance);
        return RabitqDistanceInterval{
            interval.estimate,
            interval.lower_bound,
            interval.upper_bound,
        };
    } catch (...) {
        return RabitqDistanceInterval{long_distance, long_distance, long_distance};
    }
}

RabitqDistanceInterval rabitq_residual_distance_from_record(
    const void *space,
    const void *prepared,
    const void *encoded,
    const void *residual_record,
    float long_distance) {
    try {
        const hnswlib::DistanceInterval interval =
            const_cast<hnswlib::RaBitQSpace *>(
                static_cast<const hnswlib::RaBitQSpace *>(space))->compute_residual_distance_interval_from_record(
                prepared, encoded, residual_record, long_distance);
        return RabitqDistanceInterval{
            interval.estimate,
            interval.lower_bound,
            interval.upper_bound,
        };
    } catch (...) {
        return RabitqDistanceInterval{long_distance, long_distance, long_distance};
    }
}

bool rabitq_paper_estimate_batch(
    const void *space,
    const void *prepared,
    const std::uint32_t *ids,
    std::size_t count,
    const void *encoded_base,
    std::size_t encoded_stride,
    float epsilon0,
    RabitqPaperEstimate *out) {
    hnswlib::RaBitQSpace *typed = const_cast<hnswlib::RaBitQSpace *>(
        static_cast<const hnswlib::RaBitQSpace *>(space));
    const char *encoded_bytes = static_cast<const char *>(encoded_base);
    for (std::size_t i = 0; i < count; ++i) {
        try {
            const std::size_t id = ids[i];
            if (i + 4 < count) {
                const std::size_t next_id = ids[i + 4];
                __builtin_prefetch(encoded_bytes + next_id * encoded_stride, 0, 3);
            }
            const hnswlib::PaperPruneEstimate<float> estimate =
                typed->compute_paper_prune_estimate(
                    prepared, encoded_bytes + id * encoded_stride, epsilon0);
            out[i] = RabitqPaperEstimate{
                estimate.lower_bound,
                estimate.short_ip,
                estimate.alpha,
                estimate.ip_hat,
                estimate.error_bound,
                estimate.valid ? static_cast<std::uint8_t>(1) : static_cast<std::uint8_t>(0),
            };
        } catch (...) {
            out[i] = RabitqPaperEstimate{0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0};
        }
    }
    return true;
}

bool rabitq_paper_estimate_batch_sidecar(
    const void *space,
    const void *prepared,
    const std::uint32_t *ids,
    std::size_t count,
    const std::uint8_t *msb_base,
    std::size_t msb_stride,
    const void *factors_base,
    std::size_t factors_stride,
    float epsilon0,
    RabitqPaperEstimate *out) {
    hnswlib::RaBitQSpace *typed = const_cast<hnswlib::RaBitQSpace *>(
        static_cast<const hnswlib::RaBitQSpace *>(space));
    const char *factor_bytes = static_cast<const char *>(factors_base);
    for (std::size_t i = 0; i < count; ++i) {
        try {
            const std::size_t id = ids[i];
            if (i + 4 < count) {
                const std::size_t next_id = ids[i + 4];
                __builtin_prefetch(msb_base + next_id * msb_stride, 0, 3);
            }
            const hnswlib::PaperPruneEstimate<float> estimate =
                typed->compute_paper_prune_estimate_sidecar(
                    prepared,
                    msb_base + id * msb_stride,
                    *reinterpret_cast<const hnswlib::PaperPruneFactors<float> *>(
                        factor_bytes + id * factors_stride),
                    epsilon0);
            out[i] = RabitqPaperEstimate{
                estimate.lower_bound,
                estimate.short_ip,
                estimate.alpha,
                estimate.ip_hat,
                estimate.error_bound,
                estimate.valid ? static_cast<std::uint8_t>(1) : static_cast<std::uint8_t>(0),
            };
        } catch (...) {
            out[i] = RabitqPaperEstimate{0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0};
        }
    }
    return true;
}

bool rabitq_query_distance_batch(
    const void *space,
    const void *prepared,
    const std::uint32_t *ids,
    const std::uint8_t *modes,
    std::size_t count,
    const void *encoded_base,
    std::size_t encoded_stride,
    const float *short_ips,
    float *out_distances) {
    hnswlib::RaBitQSpace *typed = const_cast<hnswlib::RaBitQSpace *>(
        static_cast<const hnswlib::RaBitQSpace *>(space));
    const char *encoded_bytes = static_cast<const char *>(encoded_base);
    for (std::size_t i = 0; i < count; ++i) {
        try {
            const std::size_t id = ids[i];
            if (i + 4 < count) {
                const std::size_t next_id = ids[i + 4];
                __builtin_prefetch(encoded_bytes + next_id * encoded_stride, 0, 3);
            }
            const void *encoded = encoded_bytes + id * encoded_stride;
            out_distances[i] = modes[i] != 0
                ? typed->query_distance_with_paper_msb(prepared, encoded, short_ips[i])
                : typed->query_distance(prepared, encoded);
        } catch (...) {
            out_distances[i] = std::numeric_limits<float>::infinity();
        }
    }
    return true;
}

bool rabitq_residual_distance_batch(
    const void *space,
    const void *prepared,
    const std::uint32_t *ids,
    std::size_t count,
    const void *encoded_base,
    std::size_t encoded_stride,
    const void *residual_base,
    std::size_t residual_stride,
    const float *long_distances,
    RabitqDistanceInterval *out) {
    hnswlib::RaBitQSpace *typed = const_cast<hnswlib::RaBitQSpace *>(
        static_cast<const hnswlib::RaBitQSpace *>(space));
    const char *encoded_bytes = static_cast<const char *>(encoded_base);
    const char *residual_bytes = static_cast<const char *>(residual_base);
    for (std::size_t i = 0; i < count; ++i) {
        try {
            const std::size_t id = ids[i];
            if (i + 4 < count) {
                const std::size_t next_id = ids[i + 4];
                __builtin_prefetch(residual_bytes + next_id * residual_stride, 0, 3);
            }
            const hnswlib::DistanceInterval interval =
                typed->compute_residual_distance_interval_from_record(
                    prepared,
                    encoded_bytes + id * encoded_stride,
                    residual_bytes + id * residual_stride,
                    long_distances[i]);
            out[i] = RabitqDistanceInterval{
                interval.estimate,
                interval.lower_bound,
                interval.upper_bound,
            };
        } catch (...) {
            out[i] = RabitqDistanceInterval{
                long_distances[i], long_distances[i], long_distances[i]};
        }
    }
    return true;
}

}
