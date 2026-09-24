#include <faiss/IndexHNSW.h>
#include <faiss/IndexPQ.h>
#include <faiss/IndexScalarQuantizer.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;

struct Args {
    std::string dataset;
    std::string suite = "03_system_fair";
    std::string methods = "PQ,SQ";
    fs::path data_root = "data";
    fs::path out_root = "results";
    size_t max_train = 100000;
    size_t max_queries = 0;
    size_t max_base = 0;
    int hnsw_m = 32;
    int ef_construction = 400;
    int pq_m = 0;
    int pq_nbits = 4;
    size_t k = 1;
    size_t rerank_cap = 460;
    std::vector<int> ef_values;
    int repeat_id = 0;
    int seed = 20260813;
    bool overwrite = false;
};

struct VecsInfo {
    size_t count = 0;
    size_t dim = 0;
    size_t row_size = 0;
    fs::path path;
};

struct IndexByteStats {
    size_t storage_bytes = 0;
    size_t graph_bytes = 0;
    size_t total_bytes = 0;
};

struct MemoryStats {
    double current_rss_mb = 0.0;
    double peak_rss_mb = 0.0;
};

struct Timer {
    using clock = std::chrono::steady_clock;
    clock::time_point start = clock::now();

    double seconds() const {
        return std::chrono::duration<double>(clock::now() - start).count();
    }

    double us() const {
        return std::chrono::duration<double, std::micro>(clock::now() - start).count();
    }
};

[[noreturn]] void usage(const char* argv0) {
    std::cerr
            << "Usage: " << argv0 << " --dataset DATASET [options]\n"
            << "  --suite 03_system_fair|01_quantizer_fair\n"
            << "  --methods PQ,SQ\n"
            << "  --data-root data --out-root results\n"
            << "  --max-train 100000 --max-queries 0 --max-base 0\n"
            << "  --hnsw-M 32 --efConstruction 400 --k 1\n"
            << "  --pq-m 0 --pq-nbits 4  # pq-m=0 means pq_m=dimension\n"
            << "  --rerank-candidates 460\n"
            << "  --ef-list 10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460\n"
            << "  --repeat-id 0 --seed 20260813 --overwrite\n";
    std::exit(2);
}

std::vector<int> default_ef_values() {
    std::vector<int> values;
    for (int i = 10; i <= 30; ++i) {
        values.push_back(i);
    }
    for (int i = 40; i <= 100; i += 10) {
        values.push_back(i);
    }
    for (int i = 140; i <= 460; i += 40) {
        values.push_back(i);
    }
    return values;
}

std::vector<std::string> split_csv(const std::string& text) {
    std::vector<std::string> out;
    size_t start = 0;
    while (start <= text.size()) {
        const size_t pos = text.find(',', start);
        std::string item = text.substr(
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

std::vector<int> parse_ef_list(const std::string& text) {
    std::vector<int> out;
    for (const std::string& item : split_csv(text)) {
        out.push_back(std::stoi(item));
    }
    if (out.empty()) {
        throw std::runtime_error("--ef-list must not be empty");
    }
    return out;
}

Args parse_args(int argc, char** argv) {
    Args args;
    args.ef_values = default_ef_values();
    for (int i = 1; i < argc; ++i) {
        const std::string key = argv[i];
        auto need = [&](const std::string& name) -> std::string {
            if (i + 1 >= argc) {
                throw std::runtime_error("missing value for " + name);
            }
            return argv[++i];
        };
        if (key == "--dataset") {
            args.dataset = need(key);
        } else if (key == "--suite") {
            args.suite = need(key);
        } else if (key == "--methods") {
            args.methods = need(key);
        } else if (key == "--data-root") {
            args.data_root = need(key);
        } else if (key == "--out-root") {
            args.out_root = need(key);
        } else if (key == "--max-train") {
            args.max_train = std::stoull(need(key));
        } else if (key == "--max-queries") {
            args.max_queries = std::stoull(need(key));
        } else if (key == "--max-base") {
            args.max_base = std::stoull(need(key));
        } else if (key == "--hnsw-M") {
            args.hnsw_m = std::stoi(need(key));
        } else if (key == "--efConstruction") {
            args.ef_construction = std::stoi(need(key));
        } else if (key == "--pq-m") {
            args.pq_m = std::stoi(need(key));
        } else if (key == "--pq-nbits") {
            args.pq_nbits = std::stoi(need(key));
        } else if (key == "--k") {
            args.k = std::stoull(need(key));
        } else if (key == "--rerank-candidates") {
            args.rerank_cap = std::stoull(need(key));
        } else if (key == "--ef-list") {
            args.ef_values = parse_ef_list(need(key));
        } else if (key == "--repeat-id") {
            args.repeat_id = std::stoi(need(key));
        } else if (key == "--seed") {
            args.seed = std::stoi(need(key));
        } else if (key == "--overwrite") {
            args.overwrite = true;
        } else if (key == "--help" || key == "-h") {
            usage(argv[0]);
        } else {
            throw std::runtime_error("unknown argument: " + key);
        }
    }
    if (args.dataset.empty()) {
        usage(argv[0]);
    }
    if (args.k == 0 || args.rerank_cap < args.k) {
        throw std::runtime_error("--rerank-candidates must be at least --k");
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

VecsInfo inspect_vecs(const fs::path& path, bool is_float) {
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
    const size_t row_size = 4 + dim * (is_float ? sizeof(float) : sizeof(int32_t));
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

std::vector<int32_t> load_ivecs_prefix(const VecsInfo& info, size_t n) {
    n = std::min(n, info.count);
    std::ifstream in(info.path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + info.path.string());
    }
    std::vector<int32_t> out(n * info.dim);
    for (size_t i = 0; i < n; ++i) {
        int32_t dim = 0;
        in.read(reinterpret_cast<char*>(&dim), sizeof(dim));
        if (dim != static_cast<int32_t>(info.dim)) {
            throw std::runtime_error("dimension mismatch in " + info.path.string());
        }
        in.read(reinterpret_cast<char*>(out.data() + i * info.dim),
                static_cast<std::streamsize>(info.dim * sizeof(int32_t)));
        if (!in) {
            throw std::runtime_error("truncated ivecs file: " + info.path.string());
        }
    }
    return out;
}

float l2sqr(const float* a, const float* b, size_t d) {
    float sum = 0.0f;
    for (size_t i = 0; i < d; ++i) {
        const float diff = a[i] - b[i];
        sum += diff * diff;
    }
    return sum;
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

void ensure_parent(const fs::path& path) {
    fs::create_directories(path.parent_path());
}

void write_header_if_needed(const fs::path& path) {
    if (fs::exists(path) && fs::file_size(path) > 0) {
        return;
    }
    ensure_parent(path);
    std::ofstream out(path);
    out << "dataset,method,recall,qps,avg_latency_us,p50_us,p95_us,p99_us,"
           "ef,k,rerank_candidates,search_param,suite,metric,nominal_bpd,"
           "actual_bytes_per_vector,index_size_mb,peak_rss_mb,current_rss_mb,"
           "build_time_s,train_time_s,"
           "add_time_s,query_time_s,rerank_time_us_per_query,M,efConstruction,"
           "pq_m,pq_nbits,sq_type,base_count,query_count,gt_width,repeat_id,"
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

void write_01_recall_qps_header_if_needed(const fs::path& path) {
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

fs::path output_csv_path(
        const fs::path& csv_dir,
        const std::string& suite,
        const std::string& method) {
    if (suite == "01_quantizer_fair") {
        return csv_dir / (canonical_method_name(method) + "_recall_qps.csv");
    }
    return csv_dir / ("faiss_" + method + "_hnsw_rerank_sweep.csv");
}

void write_csv_header_for_suite(const fs::path& path, const std::string& suite) {
    if (suite == "01_quantizer_fair") {
        write_01_recall_qps_header_if_needed(path);
        return;
    }
    write_header_if_needed(path);
}

fs::path raw_log_path(
        const fs::path& raw_dir,
        const std::string& dataset,
        const std::string& method,
        int hnsw_m,
        int ef_construction) {
    return raw_dir /
            (dataset + "_" + canonical_method_name(method) + "_M" +
             std::to_string(hnsw_m) + "_efConstruction" +
             std::to_string(ef_construction) + "_efSearch_sweep.log");
}

IndexByteStats index_byte_stats(const faiss::Index& index) {
    IndexByteStats stats;
    if (const auto* hnsw = dynamic_cast<const faiss::IndexHNSW*>(&index)) {
        stats.storage_bytes = hnsw->storage == nullptr
                ? 0
                : hnsw->ntotal * hnsw->storage->sa_code_size();
        stats.graph_bytes =
                hnsw->hnsw.levels.size() * sizeof(int) +
                hnsw->hnsw.offsets.size() * sizeof(size_t) +
                hnsw->hnsw.neighbors.byte_size();
        stats.total_bytes = stats.storage_bytes + stats.graph_bytes;
        return stats;
    }
    stats.storage_bytes = index.ntotal * index.sa_code_size();
    stats.total_bytes = stats.storage_bytes;
    return stats;
}

MemoryStats read_memory_stats() {
    std::ifstream in("/proc/self/status");
    MemoryStats stats;
    std::string line;
    while (std::getline(in, line)) {
        std::istringstream iss(line);
        std::string key;
        size_t kb = 0;
        std::string unit;
        iss >> key >> kb >> unit;
        if (key == "VmRSS:") {
            stats.current_rss_mb = static_cast<double>(kb) / 1024.0;
        } else if (key == "VmHWM:") {
            stats.peak_rss_mb = static_cast<double>(kb) / 1024.0;
        }
    }
    return stats;
}

std::unique_ptr<faiss::IndexHNSW> make_index(
        const std::string& method,
        size_t d,
        int graph_m,
        int ef_construction,
        int pq_m,
        int pq_nbits,
        int seed) {
    std::unique_ptr<faiss::IndexHNSW> index;
    if (method == "PQ") {
        auto pq_index = std::make_unique<faiss::IndexHNSWPQ>(
                static_cast<int>(d), pq_m, graph_m, pq_nbits, faiss::METRIC_L2);
        if (auto* storage = dynamic_cast<faiss::IndexPQ*>(pq_index->storage)) {
            storage->pq.cp.niter = 20;
            storage->pq.cp.max_points_per_centroid = 256;
            storage->pq.cp.seed = seed;
        }
        index = std::move(pq_index);
    } else if (method == "SQ") {
        index = std::make_unique<faiss::IndexHNSWSQ>(
                static_cast<int>(d),
                faiss::ScalarQuantizer::QT_4bit,
                graph_m,
                faiss::METRIC_L2);
    } else {
        throw std::runtime_error("unsupported method: " + method);
    }
    index->hnsw.efConstruction = ef_construction;
    return index;
}

struct BuildStats {
    double train_s = 0.0;
    double add_s = 0.0;
};

BuildStats train_add_index(
        faiss::IndexHNSW& index,
        const std::vector<float>& train,
        const std::vector<float>& base,
        size_t d) {
    BuildStats stats;
    {
        Timer timer;
        if (!index.is_trained) {
            index.train(static_cast<faiss::idx_t>(train.size() / d), train.data());
        }
        stats.train_s = timer.seconds();
    }
    {
        Timer timer;
        index.add(static_cast<faiss::idx_t>(base.size() / d), base.data());
        stats.add_s = timer.seconds();
    }
    return stats;
}

struct QueryStats {
    double recall = 0.0;
    double qps = 0.0;
    double mean_us = 0.0;
    double p50_us = 0.0;
    double p95_us = 0.0;
    double p99_us = 0.0;
    double rerank_us_per_query = 0.0;
    double query_time_s = 0.0;
};

void append_csv_row(
        std::ofstream& csv,
        const Args& args,
        const std::string& method,
        const QueryStats& q,
        const MemoryStats& memory,
        double nominal_bpd,
        double actual_bytes,
        double index_mb,
        double build_s,
        const BuildStats& build,
        int pq_m,
        int pq_nbits,
        size_t base_count,
        size_t query_count,
        size_t gt_width,
        int ef,
        size_t rerank,
        const std::string& faiss_commit) {
    if (args.suite == "01_quantizer_fair") {
        csv << args.suite << "," << args.dataset << ","
            << canonical_method_name(method) << ",L2," << args.k << ","
            << nominal_bpd << "," << actual_bytes << "," << index_mb << ","
            << memory.peak_rss_mb << "," << build_s * 1000.0 << ","
            << build.train_s * 1000.0 << "," << build.add_s * 1000.0
            << ",efSearch," << ef << "," << q.recall << "," << q.qps << ","
            << q.mean_us << "," << q.p50_us << "," << q.p95_us
            << ",NaN,1," << args.repeat_id << ","
            << faiss_commit << "\n";
        return;
    }
    csv << args.dataset << "," << method << "," << q.recall << ","
        << q.qps << "," << q.mean_us << "," << q.p50_us << ","
        << q.p95_us << "," << q.p99_us << "," << ef << "," << args.k << ","
        << rerank << ",efSearch=" << ef << ";rerank_cap=" << args.rerank_cap
        << "," << args.suite << ",L2," << nominal_bpd << "," << actual_bytes
        << "," << index_mb << "," << memory.peak_rss_mb << ","
        << memory.current_rss_mb << "," << build_s << "," << build.train_s
        << "," << build.add_s << "," << q.query_time_s << ","
        << q.rerank_us_per_query << "," << args.hnsw_m << ","
        << args.ef_construction << "," << (method == "PQ" ? pq_m : 0) << ","
        << (method == "PQ" ? pq_nbits : 0) << ","
        << (method == "SQ" ? "QT_4bit" : "") << "," << base_count << ","
        << query_count << "," << gt_width << "," << args.repeat_id << ","
        << faiss_commit << "\n";
}

void write_raw_preamble(
        std::ofstream& raw,
        const Args& args,
        const std::string& method,
        double nominal_bpd,
        double actual_bytes,
        double index_mb,
        const BuildStats& build,
        double build_s,
        size_t base_count,
        size_t query_count,
        size_t d,
        size_t gt_width,
        const std::string& faiss_commit) {
    raw << "experiment_profile=hnsw_efsearch_recall_qps\n"
        << "suite=" << args.suite << "\n"
        << "dataset=" << args.dataset << "\n"
        << "method=" << canonical_method_name(method) << "\n"
        << "backend=Faiss IndexHNSW" << method << "\n"
        << "metric=L2\n"
        << "k=" << args.k << "\n"
        << "nominal_bpd=" << nominal_bpd << "\n"
        << "actual_bytes_per_vector=" << actual_bytes << "\n"
        << "base_count=" << base_count << "\n"
        << "query_count=" << query_count << "\n"
        << "dimension=" << d << "\n"
        << "groundtruth_width=" << gt_width << "\n"
        << "hnsw_M=" << args.hnsw_m << "\n"
        << "efConstruction=" << args.ef_construction << "\n"
        << "rerank_cap=" << args.rerank_cap << "\n"
        << "index_size_mb=" << index_mb << "\n"
        << "build_time_s=" << build_s << "\n"
        << "train_time_s=" << build.train_s << "\n"
        << "add_time_s=" << build.add_s << "\n"
        << "repeat_id=" << args.repeat_id << "\n"
        << "seed=" << args.seed << "\n"
        << "git_commit=" << faiss_commit << "\n"
        << "\n"
        << "# Each data row below is one efSearch value. Increasing efSearch "
           "usually improves recall and lowers QPS.\n"
        << "# rerank_candidates is at least k for Recall@k and capped at rerank_cap; "
           "it is not the x-axis.\n"
        << "efSearch recall_at_" << args.k
        << " qps latency_mean_us latency_p50_us latency_p95_us latency_p99_us "
           "rerank_candidates query_time_s rerank_us_per_query peak_rss_mb\n";
}

void append_raw_row(
        std::ofstream& raw,
        const QueryStats& q,
        const MemoryStats& memory,
        int ef,
        size_t rerank) {
    raw << ef << " " << q.recall << " " << q.qps << " " << q.mean_us << " "
        << q.p50_us << " " << q.p95_us << " " << q.p99_us << " " << rerank
        << " " << q.query_time_s << " " << q.rerank_us_per_query << " "
        << memory.peak_rss_mb << "\n";
    raw.flush();
}

QueryStats run_sweep_point(
        faiss::IndexHNSW& index,
        const std::vector<float>& base,
        const std::vector<float>& queries,
        const std::vector<int32_t>& gt,
        size_t d,
        size_t gt_width,
        size_t k,
        int ef,
        size_t rerank_candidates) {
    const size_t query_count = queries.size() / d;
    std::vector<faiss::idx_t> labels(rerank_candidates);
    std::vector<float> distances(rerank_candidates);
    std::vector<std::pair<float, int32_t>> exact_candidates;
    exact_candidates.reserve(rerank_candidates);
    std::vector<double> latencies;
    latencies.reserve(query_count);

    size_t hits = 0;
    double rerank_total_us = 0.0;
    Timer total_timer;
    index.hnsw.efSearch = ef;
    for (size_t qi = 0; qi < query_count; ++qi) {
        const float* q = queries.data() + qi * d;
        Timer query_timer;
        index.search(
                1,
                q,
                static_cast<faiss::idx_t>(rerank_candidates),
                distances.data(),
                labels.data());

        Timer rerank_timer;
        exact_candidates.clear();
        for (size_t i = 0; i < rerank_candidates; ++i) {
            const faiss::idx_t id = labels[i];
            if (id < 0 || static_cast<size_t>(id) >= base.size() / d) {
                continue;
            }
            const float exact = l2sqr(q, base.data() + static_cast<size_t>(id) * d, d);
            exact_candidates.emplace_back(exact, static_cast<int32_t>(id));
        }
        const size_t keep = std::min(k, exact_candidates.size());
        std::partial_sort(
                exact_candidates.begin(),
                exact_candidates.begin() + static_cast<std::ptrdiff_t>(keep),
                exact_candidates.end(),
                [](const auto& a, const auto& b) {
                    if (a.first == b.first) {
                        return a.second < b.second;
                    }
                    return a.first < b.first;
                });
        rerank_total_us += rerank_timer.us();

        std::unordered_set<int32_t> truth;
        const size_t gt_keep = std::min(k, gt_width);
        truth.reserve(gt_keep);
        for (size_t j = 0; j < gt_keep; ++j) {
            truth.insert(gt[qi * gt_width + j]);
        }
        for (size_t j = 0; j < keep; ++j) {
            if (truth.count(exact_candidates[j].second) != 0) {
                ++hits;
            }
        }
        latencies.push_back(query_timer.us());
    }

    QueryStats stats;
    stats.query_time_s = total_timer.seconds();
    stats.recall = query_count == 0 ? 0.0
                                    : static_cast<double>(hits) /
                    static_cast<double>(query_count * k);
    stats.qps = stats.query_time_s > 0.0
            ? static_cast<double>(query_count) / stats.query_time_s
            : 0.0;
    stats.mean_us = query_count == 0
            ? 0.0
            : stats.query_time_s * 1e6 / static_cast<double>(query_count);
    stats.p50_us = percentile(latencies, 0.50);
    stats.p95_us = percentile(latencies, 0.95);
    stats.p99_us = percentile(latencies, 0.99);
    stats.rerank_us_per_query = query_count == 0
            ? 0.0
            : rerank_total_us / static_cast<double>(query_count);
    return stats;
}

int main(int argc, char** argv) {
    try {
        const Args args = parse_args(argc, argv);
        const fs::path dataset_dir = args.data_root / args.dataset;
        const fs::path base_path = dataset_dir / (args.dataset + "_base.fvecs");
        const fs::path query_path = dataset_dir / (args.dataset + "_query.fvecs");
        const fs::path gt_path = dataset_dir / (args.dataset + "_groundtruth.ivecs");

        const VecsInfo base_info = inspect_vecs(base_path, true);
        const VecsInfo query_info = inspect_vecs(query_path, true);
        const VecsInfo gt_info = inspect_vecs(gt_path, false);
        if (base_info.dim != query_info.dim || query_info.count != gt_info.count) {
            throw std::runtime_error("dataset dimensions/counts do not match");
        }

        const size_t base_count = args.max_base == 0
                ? base_info.count
                : std::min(args.max_base, base_info.count);
        const size_t query_count = args.max_queries == 0
                ? query_info.count
                : std::min(args.max_queries, query_info.count);
        const size_t train_count = std::min(args.max_train, base_count);
        const size_t d = base_info.dim;
        const int pq_m = args.pq_m == 0 ? static_cast<int>(d) : args.pq_m;
        const int pq_nbits = args.pq_nbits;

        if (d % static_cast<size_t>(pq_m) != 0) {
            throw std::runtime_error("dimension is not divisible by PQ m");
        }
        if (pq_nbits <= 0 || pq_nbits > 16) {
            throw std::runtime_error("--pq-nbits must be in [1, 16]");
        }
        if (args.k > gt_info.dim) {
            throw std::runtime_error("k exceeds groundtruth width");
        }

        std::cerr << "Loading " << args.dataset << " base=" << base_count
                  << " query=" << query_count << " train=" << train_count
                  << " dim=" << d << "\n";
        const std::vector<float> base = load_fvecs_prefix(base_info, base_count);
        const std::vector<float> train(train_count * d == base.size()
                        ? base
                        : std::vector<float>(base.begin(), base.begin() + train_count * d));
        const std::vector<float> queries = load_fvecs_prefix(query_info, query_count);
        const std::vector<int32_t> gt = load_ivecs_prefix(gt_info, query_count);

        const fs::path csv_dir = args.out_root / args.dataset / "csv" / args.suite;
        const fs::path raw_dir = args.out_root / args.dataset / "raw" / args.suite;
        fs::create_directories(csv_dir);
        fs::create_directories(raw_dir);

        const std::string faiss_commit = "a424dcb809fd725c44dd976d9063febd4837d16a";
        for (const std::string& method : split_csv(args.methods)) {
            const fs::path csv_path = output_csv_path(csv_dir, args.suite, method);
            if (args.overwrite) {
                ensure_parent(csv_path);
                std::ofstream(csv_path, std::ios::trunc).close();
            }
            write_csv_header_for_suite(csv_path, args.suite);

            std::cerr << method << ": build HNSW M=" << args.hnsw_m
                      << " efConstruction=" << args.ef_construction << "\n";
            std::unique_ptr<faiss::IndexHNSW> index = make_index(
                    method,
                    d,
                    args.hnsw_m,
                    args.ef_construction,
                    pq_m,
                    pq_nbits,
                    args.seed + args.repeat_id);
            const BuildStats build = train_add_index(*index, train, base, d);
            const double build_s = build.train_s + build.add_s;
            const double nominal_bpd = method == "PQ"
                    ? static_cast<double>(pq_m * pq_nbits) / static_cast<double>(d)
                    : 4.0;
            const double actual_bytes = method == "PQ"
                    ? static_cast<double>(pq_m * pq_nbits) / 8.0
                    : std::ceil(static_cast<double>(d) * 4.0 / 8.0);
            const IndexByteStats bytes = index_byte_stats(*index);
            const double index_mb =
                    static_cast<double>(bytes.total_bytes) / (1024.0 * 1024.0);
            const fs::path method_raw_path = raw_log_path(
                    raw_dir,
                    args.dataset,
                    method,
                    args.hnsw_m,
                    args.ef_construction);
            std::ofstream raw(method_raw_path, std::ios::trunc);
            if (!raw) {
                throw std::runtime_error("cannot open raw log: " + method_raw_path.string());
            }
            raw << std::setprecision(10);
            write_raw_preamble(
                    raw,
                    args,
                    method,
                    nominal_bpd,
                    actual_bytes,
                    index_mb,
                    build,
                    build_s,
                    base_count,
                    query_count,
                    d,
                    gt_info.dim,
                    faiss_commit);

            std::ofstream csv(csv_path, std::ios::app);
            csv << std::setprecision(10);
            QueryStats last_query_stats;
            for (int ef : args.ef_values) {
                if (ef < 0) {
                    continue;
                }
                const size_t ef_budget = static_cast<size_t>(ef);
                const size_t rerank = std::min<size_t>(
                        std::max(args.k, ef_budget), args.rerank_cap);
                std::cerr << method << ": ef=" << ef
                          << " rerank_candidates=" << rerank << "\n";
                const QueryStats q = run_sweep_point(
                        *index,
                        base,
                        queries,
                        gt,
                        d,
                        gt_info.dim,
                        args.k,
                        ef,
                        rerank);
                last_query_stats = q;
                const MemoryStats memory = read_memory_stats();
                append_csv_row(
                        csv,
                        args,
                        method,
                        q,
                        memory,
                        nominal_bpd,
                        actual_bytes,
                        index_mb,
                        build_s,
                        build,
                        pq_m,
                        pq_nbits,
                        base_count,
                        query_count,
                        gt_info.dim,
                        ef,
                        rerank,
                        faiss_commit);
                append_raw_row(raw, q, memory, ef, rerank);
                csv.flush();
                std::cout << method << " ef=" << ef << " rerank=" << rerank
                          << " recall=" << q.recall << " qps=" << q.qps
                          << " avg_us=" << q.mean_us << std::endl;
            }
            const MemoryStats final_memory = read_memory_stats();
            std::cerr << "METHOD_SUMMARY"
                      << " dataset=" << args.dataset
                      << " method=" << method
                      << " suite=" << args.suite
                      << " nominal_bpd=" << nominal_bpd
                      << " actual_bytes_per_vector=" << actual_bytes
                      << " index_size_mb=" << index_mb
                      << " storage_bytes=" << bytes.storage_bytes
                      << " graph_bytes=" << bytes.graph_bytes
                      << " total_index_bytes=" << bytes.total_bytes
                      << " peak_rss_mb=" << final_memory.peak_rss_mb
                      << " current_rss_mb=" << final_memory.current_rss_mb
                      << " build_time_s=" << build_s
                      << " train_time_s=" << build.train_s
                      << " add_time_s=" << build.add_s
                      << " last_query_time_s=" << last_query_stats.query_time_s
                      << "\n";
            std::cerr << method << ": wrote " << csv_path << "\n";
            std::cerr << method << ": raw log " << method_raw_path << "\n";
        }
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
}
