#include <faiss/impl/DistanceComputer.h>
#include <faiss/impl/ProductQuantizer.h>
#include <faiss/impl/ScalarQuantizer.h>

#include "hnswlib/space_rabitq.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;

struct Args {
    std::string dataset;
    fs::path data_root = "data";
    fs::path out_root = "results";
    fs::path candidate_root = "work";
    size_t candidate_size = 1000;
    size_t max_train = 100000;
    size_t max_queries = 0;
    std::string methods = "PQ,SQ";
    std::string rerank_candidates;
    size_t ours_centroids = 1;
    int repeat_id = 0;
    int seed = 20260813;
    size_t vector_batch_size = 4096;
    bool overwrite_summary = false;
    bool accuracy_only = false;
};

struct VecsInfo {
    size_t count = 0;
    size_t dim = 0;
    size_t row_size = 0;
    fs::path path;
};

struct Timer {
    using clock = std::chrono::steady_clock;
    clock::time_point start;

    Timer() : start(clock::now()) {}

    double ms() const {
        const auto end = clock::now();
        return std::chrono::duration<double, std::milli>(end - start).count();
    }
};

size_t peak_rss_kib() {
    std::ifstream in("/proc/self/status");
    std::string line;
    while (std::getline(in, line)) {
        if (line.rfind("VmHWM:", 0) == 0) {
            std::istringstream iss(line.substr(7));
            size_t kib = 0;
            iss >> kib;
            return kib;
        }
    }
    return 0;
}

void print_index_storage(
        const std::string& method,
        size_t primary_bytes,
        size_t auxiliary_bytes,
        size_t residual_bytes) {
    const double mb = 1000000.0;
    const size_t index_bytes = primary_bytes + auxiliary_bytes;
    const size_t total_bytes = index_bytes + residual_bytes;
    std::cout << method << " Index storage size: "
              << static_cast<double>(total_bytes) / mb << " MB"
              << " (index=" << static_cast<double>(index_bytes) / mb << " MB"
              << ", auxiliary=" << static_cast<double>(auxiliary_bytes) / mb
              << " MB"
              << ", residual=" << static_cast<double>(residual_bytes) / mb
              << " MB"
              << ", total_bytes=" << total_bytes << ")\n";
}

[[noreturn]] void usage(const char* argv0) {
    std::cerr
            << "Usage: " << argv0
            << " --dataset DATASET [--data-root data] [--out-root results]\n"
            << "       [--candidate-root work]\n"
            << "       [--candidate-size 1000] [--max-train 100000]\n"
            << "       [--max-queries 0] [--methods PQ,SQ]\n"
            << "       [--rerank-candidates 10,20,40]\n"
            << "       [--ours-centroids 1]\n"
            << "       [--repeat-id 0] [--seed 20260813]\n"
            << "       [--vector-batch-size 4096]\n"
            << "       [--accuracy-only]\n"
            << "       [--overwrite-summary]\n";
    std::exit(2);
}

Args parse_args(int argc, char** argv) {
    Args args;
    for (int i = 1; i < argc; ++i) {
        const std::string key = argv[i];
        auto need_value = [&](const std::string& name) -> std::string {
            if (i + 1 >= argc) {
                throw std::runtime_error("missing value for " + name);
            }
            return argv[++i];
        };
        if (key == "--dataset") {
            args.dataset = need_value(key);
        } else if (key == "--data-root") {
            args.data_root = need_value(key);
        } else if (key == "--out-root") {
            args.out_root = need_value(key);
        } else if (key == "--candidate-root") {
            args.candidate_root = need_value(key);
        } else if (key == "--candidate-size") {
            args.candidate_size = std::stoull(need_value(key));
        } else if (key == "--max-train") {
            args.max_train = std::stoull(need_value(key));
        } else if (key == "--max-queries") {
            args.max_queries = std::stoull(need_value(key));
        } else if (key == "--methods") {
            args.methods = need_value(key);
        } else if (key == "--rerank-candidates") {
            args.rerank_candidates = need_value(key);
        } else if (key == "--ours-centroids") {
            args.ours_centroids = std::stoull(need_value(key));
        } else if (key == "--repeat-id") {
            args.repeat_id = std::stoi(need_value(key));
        } else if (key == "--seed") {
            args.seed = std::stoi(need_value(key));
        } else if (key == "--vector-batch-size") {
            args.vector_batch_size = std::stoull(need_value(key));
        } else if (key == "--accuracy-only") {
            args.accuracy_only = true;
        } else if (key == "--overwrite-summary") {
            args.overwrite_summary = true;
        } else if (key == "--help" || key == "-h") {
            usage(argv[0]);
        } else {
            throw std::runtime_error("unknown argument: " + key);
        }
    }
    if (args.dataset.empty()) {
        usage(argv[0]);
    }
    if (args.candidate_size == 0 || args.max_train == 0) {
        throw std::runtime_error("candidate-size/max-train must be positive");
    }
    if (args.vector_batch_size == 0) {
        throw std::runtime_error("vector-batch-size must be positive");
    }
    return args;
}

int32_t read_i32_at(std::ifstream& in, std::streamoff offset) {
    int32_t value = 0;
    in.seekg(offset);
    in.read(reinterpret_cast<char*>(&value), sizeof(value));
    if (!in) {
        throw std::runtime_error("failed to read int32");
    }
    return value;
}

VecsInfo inspect_vecs(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + path.string());
    }
    in.seekg(0, std::ios::end);
    const size_t size = static_cast<size_t>(in.tellg());
    if (size < 4) {
        throw std::runtime_error("file too small: " + path.string());
    }
    const int32_t dim_i32 = read_i32_at(in, 0);
    if (dim_i32 <= 0) {
        throw std::runtime_error("invalid dimension in " + path.string());
    }
    const size_t dim = static_cast<size_t>(dim_i32);
    const size_t row_size = 4 + dim * sizeof(float);
    if (size % row_size != 0) {
        throw std::runtime_error("file size not divisible by row size: " + path.string());
    }
    return VecsInfo{size / row_size, dim, row_size, path};
}

std::vector<float> load_fvecs_prefix(const VecsInfo& info, size_t n) {
    n = std::min(n, info.count);
    std::ifstream in(info.path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + info.path.string());
    }
    std::vector<float> out(n * info.dim);
    for (size_t i = 0; i < n; ++i) {
        int32_t dim = 0;
        in.read(reinterpret_cast<char*>(&dim), sizeof(dim));
        if (dim != static_cast<int32_t>(info.dim)) {
            throw std::runtime_error("dimension mismatch in " + info.path.string());
        }
        in.read(reinterpret_cast<char*>(out.data() + i * info.dim),
                static_cast<std::streamsize>(info.dim * sizeof(float)));
        if (!in) {
            throw std::runtime_error("truncated fvecs file: " + info.path.string());
        }
    }
    return out;
}

std::vector<float> load_fvecs_by_ids(
        const VecsInfo& info,
        const std::vector<int32_t>& ids) {
    std::ifstream in(info.path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + info.path.string());
    }
    std::vector<float> out(ids.size() * info.dim);
    for (size_t i = 0; i < ids.size(); ++i) {
        const int32_t id = ids[i];
        if (id < 0 || static_cast<size_t>(id) >= info.count) {
            throw std::runtime_error("candidate id out of range");
        }
        const std::streamoff offset =
                static_cast<std::streamoff>(static_cast<size_t>(id) * info.row_size);
        in.seekg(offset);
        int32_t dim = 0;
        in.read(reinterpret_cast<char*>(&dim), sizeof(dim));
        if (dim != static_cast<int32_t>(info.dim)) {
            throw std::runtime_error("dimension mismatch while loading id");
        }
        in.read(reinterpret_cast<char*>(out.data() + i * info.dim),
                static_cast<std::streamsize>(info.dim * sizeof(float)));
        if (!in) {
            throw std::runtime_error("truncated fvecs file while loading id");
        }
    }
    return out;
}

void load_fvecs_by_ids_into(
        const VecsInfo& info,
        const std::vector<int32_t>& ids,
        size_t begin,
        size_t count,
        std::vector<float>& out) {
    std::ifstream in(info.path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + info.path.string());
    }
    out.resize(count * info.dim);
    for (size_t i = 0; i < count; ++i) {
        const int32_t id = ids[begin + i];
        if (id < 0 || static_cast<size_t>(id) >= info.count) {
            throw std::runtime_error("candidate id out of range");
        }
        const std::streamoff offset =
                static_cast<std::streamoff>(static_cast<size_t>(id) * info.row_size);
        in.seekg(offset);
        int32_t dim = 0;
        in.read(reinterpret_cast<char*>(&dim), sizeof(dim));
        if (dim != static_cast<int32_t>(info.dim)) {
            throw std::runtime_error("dimension mismatch while loading id");
        }
        in.read(reinterpret_cast<char*>(out.data() + i * info.dim),
                static_cast<std::streamsize>(info.dim * sizeof(float)));
        if (!in) {
            throw std::runtime_error("truncated fvecs file while loading id");
        }
    }
}

std::vector<uint8_t> load_codes(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + path.string());
    }
    in.seekg(0, std::ios::end);
    const size_t size = static_cast<size_t>(in.tellg());
    in.seekg(0);
    std::vector<uint8_t> out(size);
    in.read(reinterpret_cast<char*>(out.data()), static_cast<std::streamsize>(size));
    if (!in && size != 0) {
        throw std::runtime_error("failed to read codes: " + path.string());
    }
    return out;
}

std::vector<int32_t> load_candidates(
        const fs::path& path,
        size_t query_count,
        size_t candidate_file_size,
        size_t candidate_size) {
    if (candidate_size > candidate_file_size) {
        throw std::runtime_error("candidate_size cannot exceed candidate_file_size");
    }
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + path.string());
    }
    std::vector<int32_t> out(query_count * candidate_size);
    std::vector<int32_t> row(candidate_file_size);
    for (size_t qi = 0; qi < query_count; ++qi) {
        in.read(reinterpret_cast<char*>(row.data()),
                static_cast<std::streamsize>(row.size() * sizeof(int32_t)));
        if (!in) {
            throw std::runtime_error("truncated candidate file: " + path.string());
        }
        std::copy(
                row.begin(),
                row.begin() + static_cast<std::ptrdiff_t>(candidate_size),
                out.begin() + static_cast<std::ptrdiff_t>(qi * candidate_size));
    }
    return out;
}

std::vector<int32_t> load_ivecs_prefix(
        const fs::path& path,
        size_t query_count,
        size_t k) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + path.string());
    }
    std::vector<int32_t> out(query_count * k);
    for (size_t qi = 0; qi < query_count; ++qi) {
        int32_t dim = 0;
        in.read(reinterpret_cast<char*>(&dim), sizeof(dim));
        if (!in) {
            throw std::runtime_error("truncated ivecs file: " + path.string());
        }
        if (dim < static_cast<int32_t>(k)) {
            throw std::runtime_error("groundtruth k is smaller than requested k");
        }
        in.read(reinterpret_cast<char*>(out.data() + qi * k),
                static_cast<std::streamsize>(k * sizeof(int32_t)));
        if (!in) {
            throw std::runtime_error("truncated ivecs row: " + path.string());
        }
        if (static_cast<size_t>(dim) > k) {
            in.seekg(
                    static_cast<std::streamoff>((static_cast<size_t>(dim) - k) * sizeof(int32_t)),
                    std::ios::cur);
        }
    }
    return out;
}

std::vector<int32_t> unique_candidate_ids(
        const std::vector<int32_t>& candidates,
        size_t query_count,
        size_t candidate_size) {
    std::unordered_set<int32_t> seen;
    seen.reserve(query_count * candidate_size);
    std::vector<int32_t> ids;
    ids.reserve(query_count * candidate_size);
    for (int32_t id : candidates) {
        if (seen.insert(id).second) {
            ids.push_back(id);
        }
    }
    std::sort(ids.begin(), ids.end());
    return ids;
}

float l2sqr(const float* a, const float* b, size_t d) {
    float sum = 0.0f;
    for (size_t i = 0; i < d; ++i) {
        const float diff = a[i] - b[i];
        sum += diff * diff;
    }
    return sum;
}

float inner_product(const float* a, const float* b, size_t d) {
    float sum = 0.0f;
    for (size_t i = 0; i < d; ++i) {
        sum += a[i] * b[i];
    }
    return sum;
}

uint64_t pq_decode_8bit(const uint8_t* code, size_t m) {
    return code[m];
}

float pq_distance(
        const faiss::ProductQuantizer& pq,
        const float* query,
        const uint8_t* code,
        std::vector<float>& table) {
    pq.compute_distance_table(query, table.data());
    float distance = 0.0f;
    for (size_t m = 0; m < pq.M; ++m) {
        distance += table[m * pq.ksub + pq_decode_8bit(code, m)];
    }
    return distance;
}

struct Metrics {
    double mean_rel_error = 0.0;
    double p95_rel_error = 0.0;
    double mean_abs_error = 0.0;
    double mean_ip_rel_error = 0.0;
    double p95_ip_rel_error = 0.0;
    double recall_at_10 = 0.0;
    double top10_overlap = 0.0;
    double pairwise_flip_rate_top100 = 0.0;
    double latency_p50_us = 0.0;
    double latency_p95_us = 0.0;
};

constexpr double kRelativeErrorDistanceFloor = 1e-6;

std::vector<size_t> topk_indices(
        const std::vector<float>& values,
        size_t row,
        size_t row_width,
        size_t k) {
    k = std::min(k, row_width);
    std::vector<size_t> idx(row_width);
    std::iota(idx.begin(), idx.end(), 0);
    const size_t offset = row * row_width;
    std::partial_sort(
            idx.begin(),
            idx.begin() + static_cast<std::ptrdiff_t>(k),
            idx.end(),
            [&](size_t a, size_t b) {
                if (values[offset + a] == values[offset + b]) {
                    return a < b;
                }
                return values[offset + a] < values[offset + b];
            });
    idx.resize(k);
    return idx;
}

double fixed_candidate_recall_at_k(
        const std::vector<float>& exact,
        const std::vector<float>& approx,
        size_t query_count,
        size_t candidate_size,
        size_t k) {
    if (query_count == 0 || candidate_size == 0 || k == 0) {
        return 0.0;
    }
    k = std::min(k, candidate_size);
    double total = 0.0;
    for (size_t qi = 0; qi < query_count; ++qi) {
        const std::vector<size_t> exact_top = topk_indices(exact, qi, candidate_size, k);
        const std::vector<size_t> approx_top = topk_indices(approx, qi, candidate_size, k);
        std::unordered_set<size_t> exact_set(exact_top.begin(), exact_top.end());
        size_t hit = 0;
        for (size_t rank : approx_top) {
            if (exact_set.count(rank) != 0) {
                ++hit;
            }
        }
        total += static_cast<double>(hit) / static_cast<double>(k);
    }
    return total / static_cast<double>(query_count);
}

double fixed_target_recall_at_k(
        const std::vector<float>& exact_full,
        const std::vector<float>& approx_prefix,
        size_t query_count,
        size_t full_width,
        size_t prefix_width,
        size_t k) {
    if (query_count == 0 || full_width == 0 || prefix_width == 0 || k == 0) {
        return 0.0;
    }
    if (prefix_width > full_width) {
        throw std::runtime_error("prefix width exceeds full candidate width");
    }
    const size_t target_k = std::min(k, full_width);
    double total = 0.0;
    for (size_t qi = 0; qi < query_count; ++qi) {
        const std::vector<size_t> exact_top =
                topk_indices(exact_full, qi, full_width, target_k);
        const std::vector<size_t> approx_top =
                topk_indices(approx_prefix, qi, prefix_width, target_k);
        std::unordered_set<size_t> exact_set(exact_top.begin(), exact_top.end());
        size_t hit = 0;
        for (size_t rank : approx_top) {
            if (exact_set.count(rank) != 0) {
                ++hit;
            }
        }
        total += static_cast<double>(hit) / static_cast<double>(target_k);
    }
    return total / static_cast<double>(query_count);
}

double groundtruth_recall_at_k(
        const std::vector<int32_t>& groundtruth,
        const std::vector<int32_t>& candidates,
        const std::vector<float>& approx_prefix,
        size_t query_count,
        size_t candidate_width,
        size_t prefix_width,
        size_t k) {
    if (query_count == 0 || candidate_width == 0 || prefix_width == 0 || k == 0) {
        return 0.0;
    }
    if (prefix_width > candidate_width) {
        throw std::runtime_error("prefix width exceeds candidate width");
    }
    double total = 0.0;
    for (size_t qi = 0; qi < query_count; ++qi) {
        std::unordered_set<int32_t> gt_set;
        gt_set.reserve(k);
        for (size_t i = 0; i < k; ++i) {
            gt_set.insert(groundtruth[qi * k + i]);
        }
        const std::vector<size_t> approx_top =
                topk_indices(approx_prefix, qi, prefix_width, k);
        size_t hit = 0;
        for (size_t rank : approx_top) {
            const int32_t base_id = candidates[qi * candidate_width + rank];
            if (gt_set.count(base_id) != 0) {
                ++hit;
            }
        }
        total += static_cast<double>(hit) / static_cast<double>(k);
    }
    return total / static_cast<double>(query_count);
}

double pairwise_flip_rate_at_k(
        const std::vector<float>& exact,
        const std::vector<float>& approx,
        size_t query_count,
        size_t candidate_size,
        size_t k) {
    if (query_count == 0 || candidate_size == 0 || k < 2) {
        return 0.0;
    }
    k = std::min(k, candidate_size);
    double flipped = 0.0;
    double pairs = 0.0;
    for (size_t qi = 0; qi < query_count; ++qi) {
        const std::vector<size_t> exact_top = topk_indices(exact, qi, candidate_size, k);
        const size_t offset = qi * candidate_size;
        for (size_t i = 0; i < exact_top.size(); ++i) {
            for (size_t j = i + 1; j < exact_top.size(); ++j) {
                const size_t a = exact_top[i];
                const size_t b = exact_top[j];
                const bool exact_less = exact[offset + a] < exact[offset + b] ||
                        (exact[offset + a] == exact[offset + b] && a < b);
                const bool approx_less = approx[offset + a] < approx[offset + b] ||
                        (approx[offset + a] == approx[offset + b] && a < b);
                if (exact_less != approx_less) {
                    flipped += 1.0;
                }
                pairs += 1.0;
            }
        }
    }
    return pairs > 0.0 ? flipped / pairs : 0.0;
}

double percentile(std::vector<double> values, double p) {
    if (values.empty()) {
        return 0.0;
    }
    std::sort(values.begin(), values.end());
    const size_t idx = std::min(
            values.size() - 1,
            static_cast<size_t>(std::ceil(values.size() * p)) - 1);
    return values[idx];
}

Metrics compute_metrics(
        const std::vector<float>& exact,
        const std::vector<float>& approx,
        const std::vector<float>& exact_ip,
        const std::vector<float>& approx_ip,
        size_t query_count,
        size_t candidate_size,
        const std::vector<double>& query_latency_us) {
    std::vector<double> rel;
    rel.reserve(exact.size());
    double rel_sum = 0.0;
    double abs_sum = 0.0;
    std::vector<double> ip_rel;
    ip_rel.reserve(exact_ip.size());
    double ip_rel_sum = 0.0;
    for (size_t i = 0; i < exact.size(); ++i) {
        const double abs_err = std::abs(static_cast<double>(approx[i]) - exact[i]);
        const double denom = static_cast<double>(exact[i]);
        if (denom > kRelativeErrorDistanceFloor) {
            const double r = abs_err / denom;
            rel_sum += r;
            rel.push_back(r);
        }
        abs_sum += abs_err;
    }
    for (size_t i = 0; i < exact_ip.size(); ++i) {
        const double abs_err =
                std::abs(static_cast<double>(approx_ip[i]) - exact_ip[i]);
        const double denom = std::abs(static_cast<double>(exact_ip[i]));
        if (denom > kRelativeErrorDistanceFloor) {
            const double r = abs_err / denom;
            ip_rel_sum += r;
            ip_rel.push_back(r);
        }
    }
    std::sort(rel.begin(), rel.end());
    std::sort(ip_rel.begin(), ip_rel.end());
    const size_t p95_idx =
            rel.empty() ? 0 : std::min(rel.size() - 1, static_cast<size_t>(std::ceil(rel.size() * 0.95)) - 1);
    const size_t ip_p95_idx =
            ip_rel.empty() ? 0 : std::min(ip_rel.size() - 1, static_cast<size_t>(std::ceil(ip_rel.size() * 0.95)) - 1);
    return Metrics{
            rel.empty() ? 0.0 : rel_sum / rel.size(),
            rel.empty() ? 0.0 : rel[p95_idx],
            exact.empty() ? 0.0 : abs_sum / exact.size(),
            ip_rel.empty() ? 0.0 : ip_rel_sum / ip_rel.size(),
            ip_rel.empty() ? 0.0 : ip_rel[ip_p95_idx],
            fixed_candidate_recall_at_k(exact, approx, query_count, candidate_size, 10),
            fixed_candidate_recall_at_k(exact, approx, query_count, candidate_size, 10),
            pairwise_flip_rate_at_k(exact, approx, query_count, candidate_size, 100),
            percentile(query_latency_us, 0.50),
            percentile(query_latency_us, 0.95)};
}

void ensure_parent(const fs::path& path) {
    fs::create_directories(path.parent_path());
}

void append_summary_header_if_needed(const fs::path& path) {
    if (fs::exists(path) && fs::file_size(path) > 0) {
        return;
    }
    ensure_parent(path);
    std::ofstream out(path);
    out << "dataset,method,recall,qps,latency_mean_us,latency_p50_us,"
           "latency_p95_us,k,search_param,rerank,suite,metric,nominal_bpd,"
           "actual_bytes_per_vector,index_size_mb,peak_rss_mb,build_time_ms,"
           "train_time_ms,encode_time_ms,distance_time_ms,graph_degree,build_ef,"
           "query_count,candidate_size,unique_candidate_count,distance_calls,"
           "mean_relative_error,p95_relative_error,mean_ip_relative_error,"
           "p95_ip_relative_error,mean_absolute_error,threads,"
           "top10_overlap,pairwise_flip_rate_top100,repeat_id,git_commit\n";
}

void write_raw_header(const fs::path& path) {
    ensure_parent(path);
    std::ofstream out(path);
    out << "dataset,method,query_id,candidate_rank,base_id,exact_l2,compressed_l2,relative_error\n";
}

double query_qps(size_t query_count, double distance_ms) {
    return distance_ms > 0.0
            ? static_cast<double>(query_count) * 1000.0 / distance_ms
            : 0.0;
}

double query_latency_mean_us(size_t query_count, double distance_ms) {
    return query_count > 0
            ? distance_ms * 1000.0 / static_cast<double>(query_count)
            : 0.0;
}

void append_summary(
        const fs::path& path,
        const std::string& dataset,
        const std::string& method,
        double nominal_bpd,
        double actual_bytes_per_vector,
        double index_size_mb,
        double peak_rss_mb,
        double train_ms,
        double encode_ms,
        double distance_ms,
        size_t query_count,
        size_t candidate_size,
        size_t unique_count,
        const Metrics& metrics,
        const std::string& commit,
        int repeat_id) {
    append_summary_header_if_needed(path);
    std::ofstream out(path, std::ios::app);
    const double distance_calls =
            static_cast<double>(query_count) * static_cast<double>(candidate_size);
    const double qps = query_qps(query_count, distance_ms);
    const double latency_mean_us = query_latency_mean_us(query_count, distance_ms);
    out << dataset << "," << method << "," << metrics.recall_at_10 << ","
        << qps << "," << latency_mean_us << "," << metrics.latency_p50_us
        << "," << metrics.latency_p95_us << ",10,fixed_candidates_k"
        << candidate_size << ",none,01_quantizer_fair,L2," << nominal_bpd
        << "," << actual_bytes_per_vector << "," << index_size_mb << ","
        << peak_rss_mb << "," << (train_ms + encode_ms) << "," << train_ms
        << "," << encode_ms << "," << distance_ms
        << ",0,0," << query_count << "," << candidate_size << ","
        << unique_count << "," << distance_calls << "," << metrics.mean_rel_error
        << "," << metrics.p95_rel_error << "," << metrics.mean_ip_rel_error
        << "," << metrics.p95_ip_rel_error << "," << metrics.mean_abs_error
        << ",1," << metrics.top10_overlap << ","
        << metrics.pairwise_flip_rate_top100 << "," << repeat_id << ","
        << commit << "\n";
}

void write_accuracy_header_if_needed(const fs::path& path) {
    if (fs::exists(path) && fs::file_size(path) > 0) {
        return;
    }
    ensure_parent(path);
    std::ofstream out(path);
    out << "suite,dataset,method,metric,nominal_bpd,actual_bytes_per_vector,"
           "index_size_mb,train_time_ms,encode_time_ms,mean_relative_error,"
           "p95_relative_error,mean_ip_relative_error,p95_ip_relative_error,"
           "mean_absolute_error,top10_overlap,pairwise_flip_rate,"
           "fixed_candidate_recall,query_count,candidate_size,threads,repeat_id,"
           "git_commit\n";
}

void write_recall_qps_header_if_needed(const fs::path& path) {
    if (fs::exists(path) && fs::file_size(path) > 0) {
        return;
    }
    ensure_parent(path);
    std::ofstream out(path);
    out << "suite,dataset,method,metric,k,nominal_bpd,actual_bytes_per_vector,"
           "index_size_mb,peak_rss_mb,build_time_ms,train_time_ms,encode_time_ms,"
           "search_param_name,search_param_value,recall,qps,latency_mean_us,"
           "latency_p50_us,latency_p95_us,distance_calls,threads,repeat_id,"
           "git_commit\n";
}

std::string canonical_method_name(const std::string& method) {
    if (method == "PQ") {
        return "PQ_4bit";
    }
    if (method == "SQ") {
        return "SQ_4bit";
    }
    return method;
}

void write_accuracy_row(
        const fs::path& path,
        const std::string& dataset,
        const std::string& method,
        double nominal_bpd,
        double actual_bytes_per_vector,
        double index_size_mb,
        double train_ms,
        double encode_ms,
        size_t query_count,
        size_t candidate_size,
        const Metrics& metrics,
        const std::string& commit,
        int repeat_id) {
    std::ofstream(path, std::ios::trunc).close();
    write_accuracy_header_if_needed(path);
    std::ofstream out(path, std::ios::app);
    out << "01_quantizer_fair," << dataset << ","
        << canonical_method_name(method) << ",L2," << nominal_bpd << ","
        << actual_bytes_per_vector << "," << index_size_mb << "," << train_ms
        << "," << encode_ms << "," << metrics.mean_rel_error
        << "," << metrics.p95_rel_error
        << "," << metrics.mean_ip_rel_error
        << "," << metrics.p95_ip_rel_error
        << "," << metrics.mean_abs_error << "," << metrics.top10_overlap
        << "," << metrics.pairwise_flip_rate_top100 << ","
        << metrics.recall_at_10 << "," << query_count << ","
        << candidate_size << ",1," << repeat_id << "," << commit << "\n";
}

void reset_recall_qps_file(const fs::path& path) {
    std::ofstream(path, std::ios::trunc).close();
    write_recall_qps_header_if_needed(path);
}

void append_recall_qps_row(
        const fs::path& path,
        const std::string& dataset,
        const std::string& method,
        double nominal_bpd,
        double actual_bytes_per_vector,
        double index_size_mb,
        double peak_rss_mb,
        double train_ms,
        double encode_ms,
        double distance_ms,
        size_t query_count,
        size_t candidate_size,
        const Metrics& metrics,
        const std::string& commit,
        int repeat_id) {
    write_recall_qps_header_if_needed(path);
    std::ofstream out(path, std::ios::app);
    const double distance_calls =
            static_cast<double>(query_count) * static_cast<double>(candidate_size);
    out << "01_quantizer_fair," << dataset << ","
        << canonical_method_name(method) << ",L2,10," << nominal_bpd << ","
        << actual_bytes_per_vector << "," << index_size_mb << ","
        << peak_rss_mb << "," << (train_ms + encode_ms) << "," << train_ms
        << "," << encode_ms
        << ",rerank_candidates," << candidate_size << ","
        << metrics.recall_at_10 << "," << query_qps(query_count, distance_ms)
        << ","
        << query_latency_mean_us(query_count, distance_ms)
        << "," << metrics.latency_p50_us << "," << metrics.latency_p95_us
        << "," << distance_calls << ",1," << repeat_id << "," << commit
        << "\n";
}

std::vector<std::string> split_methods(const std::string& methods) {
    std::vector<std::string> out;
    size_t start = 0;
    while (start <= methods.size()) {
        const size_t pos = methods.find(',', start);
        std::string item = methods.substr(
                start, pos == std::string::npos ? std::string::npos : pos - start);
        item.erase(std::remove_if(item.begin(), item.end(), ::isspace), item.end());
        if (!item.empty()) {
            out.push_back(item);
        }
        if (pos == std::string::npos) {
            break;
        }
        start = pos + 1;
    }
    return out;
}

std::vector<size_t> parse_rerank_candidates(
        const std::string& text,
        size_t fallback_candidate_size) {
    std::vector<size_t> out;
    if (text.empty()) {
        out.push_back(fallback_candidate_size);
        return out;
    }
    std::stringstream ss(text);
    std::string item;
    while (std::getline(ss, item, ',')) {
        item.erase(std::remove_if(item.begin(), item.end(), ::isspace), item.end());
        if (!item.empty()) {
            const size_t value = std::stoull(item);
            if (value == 0) {
                throw std::runtime_error("rerank-candidates values must be positive");
            }
            out.push_back(value);
        }
    }
    if (out.empty()) {
        throw std::runtime_error("empty rerank-candidates list");
    }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
    return out;
}

std::vector<float> prefix_matrix(
        const std::vector<float>& full,
        size_t query_count,
        size_t full_width,
        size_t prefix_width) {
    std::vector<float> out(query_count * prefix_width);
    for (size_t qi = 0; qi < query_count; ++qi) {
        std::copy(
                full.begin() + static_cast<std::ptrdiff_t>(qi * full_width),
                full.begin() + static_cast<std::ptrdiff_t>(qi * full_width + prefix_width),
                out.begin() + static_cast<std::ptrdiff_t>(qi * prefix_width));
    }
    return out;
}

std::vector<float> train_kmeans_centroids(
        const float* data,
        size_t count,
        size_t dim,
        size_t k,
        size_t sample_count,
        uint32_t seed,
        int iters) {
    sample_count = std::min(sample_count, count);
    std::mt19937 rng(seed);
    std::vector<size_t> sample(sample_count);
    for (size_t i = 0; i < sample_count; ++i) {
        sample[i] = static_cast<size_t>(rng()) % count;
    }

    std::vector<float> centroids(k * dim);
    for (size_t c = 0; c < k; ++c) {
        std::copy(
                data + sample[c % sample_count] * dim,
                data + (sample[c % sample_count] + 1) * dim,
                centroids.data() + c * dim);
    }

    std::vector<float> next(k * dim);
    std::vector<size_t> counts(k);
    for (int iter = 0; iter < iters; ++iter) {
        std::fill(next.begin(), next.end(), 0.0f);
        std::fill(counts.begin(), counts.end(), 0);
        for (size_t i = 0; i < sample_count; ++i) {
            const float* v = data + sample[i] * dim;
            size_t best = 0;
            float best_d = std::numeric_limits<float>::infinity();
            for (size_t c = 0; c < k; ++c) {
                const float* cen = centroids.data() + c * dim;
                float d = 0.0f;
                for (size_t j = 0; j < dim; ++j) {
                    const float diff = v[j] - cen[j];
                    d += diff * diff;
                }
                if (d < best_d) {
                    best_d = d;
                    best = c;
                }
            }
            float* dst = next.data() + best * dim;
            for (size_t j = 0; j < dim; ++j) {
                dst[j] += v[j];
            }
            ++counts[best];
        }
        for (size_t c = 0; c < k; ++c) {
            float* dst = centroids.data() + c * dim;
            if (counts[c] == 0) {
                continue;
            }
            const float inv = 1.0f / static_cast<float>(counts[c]);
            for (size_t j = 0; j < dim; ++j) {
                dst[j] = next[c * dim + j] * inv;
            }
        }
    }
    return centroids;
}

#ifndef QGRAPH_FAISS_QUANTIZER_LIBRARY
int main(int argc, char** argv) {
    try {
        const Args args = parse_args(argc, argv);
        const fs::path dataset_dir = args.data_root / args.dataset;
        const fs::path base_path = dataset_dir / (args.dataset + "_base.fvecs");
        const fs::path query_path = dataset_dir / (args.dataset + "_query.fvecs");
        const size_t candidate_file_size = args.candidate_size;
        const std::vector<size_t> rerank_points =
                parse_rerank_candidates(args.rerank_candidates, candidate_file_size);
        std::vector<size_t> sweep_points = rerank_points;
        if (sweep_points.empty() || sweep_points.back() != candidate_file_size) {
            sweep_points.push_back(candidate_file_size);
        }
        const size_t candidate_size = candidate_file_size;
        const fs::path cand_path = args.candidate_root / "01_quantizer_fair" /
                args.dataset /
                ("fixed_candidates_k" + std::to_string(candidate_file_size) + ".bin");

        const VecsInfo base_info = inspect_vecs(base_path);
        const VecsInfo query_info = inspect_vecs(query_path);
        if (base_info.dim != query_info.dim) {
            throw std::runtime_error("base/query dimensions differ");
        }

        const size_t query_count = args.max_queries == 0
                ? query_info.count
                : std::min(args.max_queries, query_info.count);
        const size_t train_count = std::min(args.max_train, base_info.count);
        const size_t d = base_info.dim;

        std::cerr << "Loading train/query/candidate data for " << args.dataset
                  << " train=" << train_count << " queries=" << query_count
                  << " dim=" << d << "\n";

        const std::vector<float> train = load_fvecs_prefix(base_info, train_count);
        const std::vector<float> queries = load_fvecs_prefix(query_info, query_count);
        const std::vector<int32_t> candidates =
                load_candidates(cand_path, query_count, candidate_file_size, candidate_size);
        const std::vector<int32_t> unique_ids =
                unique_candidate_ids(candidates, query_count, candidate_size);
        std::cerr << "unique_candidate_count=" << unique_ids.size()
                  << " candidate_file_size=" << candidate_file_size
                  << " max_rerank_candidates=" << candidate_size
                  << " vector_batch_size=" << args.vector_batch_size << "\n";

        std::unordered_map<int32_t, size_t> id_to_local;
        id_to_local.reserve(unique_ids.size());
        for (size_t i = 0; i < unique_ids.size(); ++i) {
            id_to_local.emplace(unique_ids[i], i);
        }

        const fs::path csv_dir =
                args.out_root / "01_quantizer_fair" / args.dataset / "csv";
        const fs::path work_dir =
                args.candidate_root / "01_quantizer_fair" / args.dataset;
        const fs::path summary_path = csv_dir / "faiss_quantizer_summary.csv";
        if (args.overwrite_summary) {
            ensure_parent(summary_path);
            std::ofstream(summary_path, std::ios::trunc).close();
        }

        std::cerr << "Computing exact L2 distances for fixed candidates\n";
        std::vector<float> exact(query_count * candidate_size);
        std::vector<float> exact_ip(query_count * candidate_size);
        std::vector<float> row_vectors;
        for (size_t qi = 0; qi < query_count; ++qi) {
            load_fvecs_by_ids_into(
                    base_info,
                    candidates,
                    qi * candidate_size,
                    candidate_size,
                    row_vectors);
            const float* q = queries.data() + qi * d;
            for (size_t rank = 0; rank < candidate_size; ++rank) {
                const float* xb = row_vectors.data() + rank * d;
                exact[qi * candidate_size + rank] = l2sqr(q, xb, d);
                exact_ip[qi * candidate_size + rank] = inner_product(q, xb, d);
            }
            if ((qi + 1) % 1000 == 0 || qi + 1 == query_count) {
                std::cerr << "  exact progress " << (qi + 1) << "/"
                          << query_count << "\n";
            }
        }

        const std::string faiss_commit = "a424dcb809fd725c44dd976d9063febd4837d16a";
        for (const std::string& method : split_methods(args.methods)) {
            double train_ms = 0.0;
            double encode_ms = 0.0;
            double actual_bytes = 0.0;
            double nominal_bpd = 4.0;
            double index_size_mb = 0.0;
            double peak_rss_mb = 0.0;
            size_t primary_bytes = 0;
            size_t auxiliary_bytes = 0;
            size_t residual_bytes = 0;
            const std::string method_for_output =
                    method == "Ours"
                            ? "Ours_RaBitQ_K" +
                                    std::to_string(args.ours_centroids)
                            : method;
            const fs::path accuracy_path =
                    csv_dir / (canonical_method_name(method_for_output) + "_accuracy.csv");
            const fs::path recall_qps_path =
                    csv_dir / (canonical_method_name(method_for_output) + "_recall_qps.csv");
            if (!args.accuracy_only) {
                reset_recall_qps_file(recall_qps_path);
            }

            auto emit_point = [&](size_t rerank,
                                  double distance_ms,
                                  const std::vector<float>& approx,
                                  const std::vector<float>& approx_ip,
                                  const std::vector<double>& query_latency_us) {
                const std::vector<float> exact_prefix =
                        prefix_matrix(exact, query_count, candidate_size, rerank);
                const std::vector<float> exact_ip_prefix =
                        prefix_matrix(exact_ip, query_count, candidate_size, rerank);
                Metrics metrics = compute_metrics(
                        exact_prefix,
                        approx,
                        exact_ip_prefix,
                        approx_ip,
                        query_count,
                        rerank,
                        query_latency_us);
                if (!args.accuracy_only) {
                    append_recall_qps_row(
                            recall_qps_path,
                            args.dataset,
                            method_for_output,
                            nominal_bpd,
                            actual_bytes,
                            index_size_mb,
                            peak_rss_mb,
                            train_ms,
                            encode_ms,
                            distance_ms,
                            query_count,
                            rerank,
                            metrics,
                            faiss_commit,
                            args.repeat_id);
                }

                if (rerank == candidate_size) {
                    write_accuracy_row(
                            accuracy_path,
                            args.dataset,
                            method_for_output,
                            nominal_bpd,
                            actual_bytes,
                            index_size_mb,
                            train_ms,
                            encode_ms,
                            query_count,
                            rerank,
                            metrics,
                            faiss_commit,
                            args.repeat_id);

                    append_summary(
                            summary_path,
                            args.dataset,
                            method_for_output,
                            nominal_bpd,
                            actual_bytes,
                            index_size_mb,
                            peak_rss_mb,
                            train_ms,
                            encode_ms,
                            distance_ms,
                            query_count,
                            rerank,
                            unique_ids.size(),
                            metrics,
                            faiss_commit,
                            args.repeat_id);
                }

                std::cerr << method_for_output << " rerank=" << rerank
                          << " recall=" << metrics.recall_at_10
                          << " qps=" << query_qps(query_count, distance_ms)
                          << " latency_p95_us=" << metrics.latency_p95_us
                          << " p95_rel_error=" << metrics.p95_rel_error
                          << " mean_ip_rel_error=" << metrics.mean_ip_rel_error << "\n";
            };

            if (method == "PQ") {
                const size_t M = d;
                const size_t nbits = 4;
                if (M == 0 || d % M != 0) {
                    throw std::runtime_error("cannot choose PQ M for dimension");
                }
                faiss::ProductQuantizer pq(d, M, nbits);
                pq.cp.niter = 20;
                pq.cp.max_points_per_centroid = 256;
                pq.cp.seed = args.seed + args.repeat_id;
                {
                    Timer timer;
                    pq.train(train_count, train.data());
                    train_ms = timer.ms();
                }
                actual_bytes = static_cast<double>(pq.code_size);
                nominal_bpd = static_cast<double>(M * nbits) / static_cast<double>(d);

                std::vector<uint8_t> codes(unique_ids.size() * pq.code_size);
                {
                    std::cerr << "PQ encode start unique=" << unique_ids.size()
                              << " code_size=" << pq.code_size << "\n";
                    Timer timer;
                    std::vector<float> batch_vectors;
                    for (size_t begin = 0; begin < unique_ids.size();
                         begin += args.vector_batch_size) {
                        const size_t count = std::min(
                                args.vector_batch_size, unique_ids.size() - begin);
                        load_fvecs_by_ids_into(
                                base_info, unique_ids, begin, count, batch_vectors);
                        pq.compute_codes(
                                batch_vectors.data(),
                                codes.data() + begin * pq.code_size,
                                count);
                        if ((begin + count) % (args.vector_batch_size * 64) == 0 ||
                            begin + count == unique_ids.size()) {
                            std::cerr << "  PQ encode progress " << (begin + count)
                                      << "/" << unique_ids.size() << "\n";
                        }
                    }
                    encode_ms = timer.ms();
                }

                primary_bytes = base_info.count * pq.code_size;
                auxiliary_bytes =
                        static_cast<size_t>(d) * (1ULL << nbits) * sizeof(float);
                index_size_mb = static_cast<double>(
                                        primary_bytes + auxiliary_bytes + residual_bytes) /
                                1048576.0;
                peak_rss_mb = peak_rss_kib() / 1024.0;
                print_index_storage(
                        method_for_output, primary_bytes, auxiliary_bytes, residual_bytes);

                for (size_t rerank : sweep_points) {
                    std::vector<float> approx(query_count * rerank);
                    std::vector<float> approx_ip(query_count * rerank);
                    std::vector<double> query_latency_us;
                    query_latency_us.reserve(query_count);
                    std::vector<uint8_t> row_codes(rerank * pq.code_size);
                    std::vector<float> row_recons(rerank * d);
                    Timer timer;
                    for (size_t qi = 0; qi < query_count; ++qi) {
                        Timer query_timer;
                        const float* q = queries.data() + qi * d;
                        for (size_t rank = 0; rank < rerank; ++rank) {
                            const int32_t id = candidates[qi * candidate_size + rank];
                            const size_t local = id_to_local.at(id);
                            const uint8_t* code = codes.data() + local * pq.code_size;
                            std::memcpy(
                                    row_codes.data() + rank * pq.code_size,
                                    code,
                                    pq.code_size);
                        }
                        pq.decode(row_codes.data(), row_recons.data(), rerank);
                        for (size_t rank = 0; rank < rerank; ++rank) {
                            const float* xb = row_recons.data() + rank * d;
                            approx[qi * rerank + rank] = l2sqr(q, xb, d);
                            approx_ip[qi * rerank + rank] =
                                    inner_product(q, xb, d);
                        }
                        query_latency_us.push_back(query_timer.ms() * 1000.0);
                    }
                    emit_point(
                            rerank,
                            timer.ms(),
                            approx,
                            approx_ip,
                            query_latency_us);
                }
            } else if (method == "SQ") {
                faiss::ScalarQuantizer sq(d, faiss::ScalarQuantizer::QT_4bit);
                {
                    Timer timer;
                    sq.train(train_count, train.data());
                    train_ms = timer.ms();
                }
                actual_bytes = static_cast<double>(sq.code_size);
                nominal_bpd = 4.0;

                std::vector<uint8_t> codes(unique_ids.size() * sq.code_size);
                {
                    std::cerr << "SQ encode start unique=" << unique_ids.size()
                              << " code_size=" << sq.code_size << "\n";
                    Timer timer;
                    std::vector<float> batch_vectors;
                    for (size_t begin = 0; begin < unique_ids.size();
                         begin += args.vector_batch_size) {
                        const size_t count = std::min(
                                args.vector_batch_size, unique_ids.size() - begin);
                        load_fvecs_by_ids_into(
                                base_info, unique_ids, begin, count, batch_vectors);
                        sq.compute_codes(
                                batch_vectors.data(),
                                codes.data() + begin * sq.code_size,
                                count);
                        if ((begin + count) % (args.vector_batch_size * 64) == 0 ||
                            begin + count == unique_ids.size()) {
                            std::cerr << "  SQ encode progress " << (begin + count)
                                      << "/" << unique_ids.size() << "\n";
                        }
                    }
                    encode_ms = timer.ms();
                }

                primary_bytes = base_info.count * sq.code_size;
                auxiliary_bytes = static_cast<size_t>(d) * 2 * sizeof(float);
                index_size_mb = static_cast<double>(
                                        primary_bytes + auxiliary_bytes + residual_bytes) /
                                1048576.0;
                peak_rss_mb = peak_rss_kib() / 1024.0;
                print_index_storage(
                        method_for_output, primary_bytes, auxiliary_bytes, residual_bytes);

                std::unique_ptr<faiss::ScalarQuantizer::SQDistanceComputer> dc(
                        sq.get_distance_computer(faiss::METRIC_L2));
                dc->codes = codes.data();
                dc->code_size = sq.code_size;
                std::unique_ptr<faiss::ScalarQuantizer::SQDistanceComputer> ip_dc(
                        sq.get_distance_computer(faiss::METRIC_INNER_PRODUCT));
                ip_dc->codes = codes.data();
                ip_dc->code_size = sq.code_size;

                for (size_t rerank : sweep_points) {
                    std::vector<float> approx(query_count * rerank);
                    std::vector<float> approx_ip(query_count * rerank);
                    std::vector<double> query_latency_us;
                    query_latency_us.reserve(query_count);
                    Timer timer;
                    for (size_t qi = 0; qi < query_count; ++qi) {
                        Timer query_timer;
                        const float* q = queries.data() + qi * d;
                        dc->set_query(q);
                        ip_dc->set_query(q);
                        for (size_t rank = 0; rank < rerank; ++rank) {
                            const int32_t id = candidates[qi * candidate_size + rank];
                            const size_t local = id_to_local.at(id);
                            const float compressed = (*dc)(static_cast<faiss::idx_t>(local));
                            approx[qi * rerank + rank] = compressed;
                            approx_ip[qi * rerank + rank] =
                                    (*ip_dc)(static_cast<faiss::idx_t>(local));
                        }
                        query_latency_us.push_back(query_timer.ms() * 1000.0);
                    }
                    emit_point(
                            rerank,
                            timer.ms(),
                            approx,
                            approx_ip,
                            query_latency_us);
                }
            } else if (method == "Ours_RaBitQ_K1" || method == "Ours") {
                hnswlib::RaBitQSpace space(
                        d,
                        args.ours_centroids,
                        static_cast<uint32_t>(args.seed),
                        false,
                        4);
                actual_bytes = static_cast<double>(space.get_data_size());
                nominal_bpd = 4.0;
                std::vector<float> trained_centroids;
                if (args.ours_centroids > 1) {
                    Timer timer;
                    trained_centroids = train_kmeans_centroids(
                            train.data(),
                            train_count,
                            d,
                            args.ours_centroids,
                            30000,
                            static_cast<uint32_t>(args.seed),
                            6);
                    space.setCentroids(
                            trained_centroids.data(), args.ours_centroids);
                    train_ms = timer.ms();
                }

                std::vector<char> codes(unique_ids.size() * space.get_data_size());
                {
                    std::cerr << method_for_output << " encode start unique="
                              << unique_ids.size()
                              << " code_size=" << space.get_data_size() << "\n";
                    Timer timer;
                    std::vector<float> batch_vectors;
                    for (size_t begin = 0; begin < unique_ids.size();
                         begin += args.vector_batch_size) {
                        const size_t count = std::min(
                                args.vector_batch_size, unique_ids.size() - begin);
                        load_fvecs_by_ids_into(
                                base_info, unique_ids, begin, count, batch_vectors);
                        for (size_t i = 0; i < count; ++i) {
                            space.encodeVector(
                                    batch_vectors.data() + i * d,
                                    codes.data() + (begin + i) * space.get_data_size());
                        }
                        if ((begin + count) % (args.vector_batch_size * 64) == 0 ||
                            begin + count == unique_ids.size()) {
                            std::cerr << "  " << method_for_output
                                      << " encode progress "
                                      << (begin + count) << "/"
                                      << unique_ids.size() << "\n";
                        }
                    }
                    encode_ms = timer.ms();
                }

                {
                    const size_t record_bytes = space.get_data_size();
                    const size_t residual_record_bytes =
                            space.get_residual_disk_record_bytes();
                    const size_t primary_record_bytes =
                            record_bytes > residual_record_bytes
                                    ? record_bytes - residual_record_bytes
                                    : record_bytes;
                    primary_bytes = base_info.count * primary_record_bytes;
                    residual_bytes = base_info.count * residual_record_bytes;
                    auxiliary_bytes =
                            static_cast<size_t>(d) * 2 * sizeof(float) +
                            args.ours_centroids * d * sizeof(float);
                }
                index_size_mb = static_cast<double>(
                                        primary_bytes + auxiliary_bytes + residual_bytes) /
                                1048576.0;
                peak_rss_mb = peak_rss_kib() / 1024.0;
                print_index_storage(
                        method_for_output, primary_bytes, auxiliary_bytes, residual_bytes);

                for (size_t rerank : sweep_points) {
                    std::vector<float> approx(query_count * rerank);
                    std::vector<float> approx_ip(query_count * rerank);
                    std::vector<double> query_latency_us;
                    query_latency_us.reserve(query_count);
                    Timer timer;
                    for (size_t qi = 0; qi < query_count; ++qi) {
                        Timer query_timer;
                        // Ours production path uses a Float32 query against the
                        // encoded payload (asymmetric distance), matching PQ/SQ
                        // and SAQ in this experiment. Query encoding is only
                        // used for the graph build, not for candidate rerank.
                        const void* prepared =
                                space.prepare_asymmetric_build_query(
                                        queries.data() + qi * d);
                        for (size_t rank = 0; rank < rerank; ++rank) {
                            const int32_t id = candidates[qi * candidate_size + rank];
                            const size_t local = id_to_local.at(id);
                            const float compressed =
                                    space.asymmetric_build_distance_prepared(
                                            prepared,
                                            codes.data() +
                                                    local * space.get_data_size());
                            approx[qi * rerank + rank] = compressed;
                            approx_ip[qi * rerank + rank] =
                                    space.asymmetric_build_inner_product_prepared(
                                            prepared,
                                            codes.data() +
                                                    local * space.get_data_size());
                        }
                        space.release_asymmetric_build_query(prepared);
                        query_latency_us.push_back(query_timer.ms() * 1000.0);
                    }
                    emit_point(
                            rerank,
                            timer.ms(),
                            approx,
                            approx_ip,
                            query_latency_us);
                }
            } else {
                throw std::runtime_error("unsupported method for this runner: " + method);
            }
        }

        std::cout << "wrote " << summary_path << "\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
}
#endif
