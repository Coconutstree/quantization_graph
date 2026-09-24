#include "direct_io.hpp"
#include "query_page_cache.hpp"

#include "glass/graph.hpp"
#include "glass/nsg/nsg.hpp"
#include "glass/quant/sq4u_quant.hpp"

#include <openssl/evp.h>
#include <omp.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <cmath>
#include <numeric>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
using Quant = glass::SQ4QuantizerUniform<glass::Metric::L2>;

namespace {

constexpr std::size_t kPage = qgraph05::kPageSize;
constexpr int kTopK = 10;
constexpr const char* kMethod = "Glass-NSG-DiskPort";
constexpr const char* kLayer = "05c";
constexpr const char* kSourceSuite = "03_system_fair";
constexpr const char* kSourceKernel = "official Glass NSG SQ4U distance kernel";
constexpr const char* kPortKind = "algorithm_preserving_disk_port";

struct Args {
    std::unordered_map<std::string, std::string> values;

    static Args parse(int argc, char** argv) {
        Args out;
        for (int i = 1; i < argc; ++i) {
            std::string key = argv[i];
            if (key.rfind("--", 0) != 0 || i + 1 >= argc) {
                throw std::runtime_error("expected --key value, got " + key);
            }
            out.values[key.substr(2)] = argv[++i];
        }
        return out;
    }

    const std::string& require(const std::string& key) const {
        auto it = values.find(key);
        if (it == values.end() || it->second.empty()) {
            throw std::runtime_error("missing --" + key);
        }
        return it->second;
    }

    std::string optional(const std::string& key, const std::string& fallback = "") const {
        auto it = values.find(key);
        return it == values.end() ? fallback : it->second;
    }

    std::size_t number(const std::string& key) const {
        return static_cast<std::size_t>(std::stoull(require(key)));
    }

    double real(const std::string& key) const {
        return std::stod(require(key));
    }
};

std::string json_string(const std::string& value) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : value) {
        switch (c) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (c < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<int>(c) << std::dec;
                } else {
                    out << static_cast<char>(c);
                }
        }
    }
    out << '"';
    return out.str();
}

std::string sha256(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot hash " + path.string());
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr || EVP_DigestInit_ex(context, EVP_sha256(), nullptr) != 1) {
        EVP_MD_CTX_free(context);
        throw std::runtime_error("EVP sha256 init failed");
    }
    std::array<char, 1 << 20> buffer{};
    while (input) {
        input.read(buffer.data(), buffer.size());
        const auto count = input.gcount();
        if (count > 0 && EVP_DigestUpdate(context, buffer.data(), count) != 1) {
            EVP_MD_CTX_free(context);
            throw std::runtime_error("EVP sha256 update failed");
        }
    }
    std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
    unsigned int size = 0;
    if (EVP_DigestFinal_ex(context, digest.data(), &size) != 1) {
        EVP_MD_CTX_free(context);
        throw std::runtime_error("EVP sha256 final failed");
    }
    EVP_MD_CTX_free(context);
    std::ostringstream out;
    for (unsigned int i = 0; i < size; ++i) {
        out << std::hex << std::setw(2) << std::setfill('0')
            << static_cast<unsigned int>(digest[i]);
    }
    return out.str();
}

struct Matrix {
    std::size_t rows = 0;
    std::size_t dim = 0;
    std::vector<float> data;
};

Matrix read_fvecs(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open fvecs " + path.string());
    int32_t dim = 0;
    in.read(reinterpret_cast<char*>(&dim), 4);
    if (dim <= 0) throw std::runtime_error("bad fvecs dimension");
    const std::uintmax_t size = fs::file_size(path);
    const std::size_t row_size = 4 + static_cast<std::size_t>(dim) * 4;
    if (size % row_size != 0) throw std::runtime_error("malformed fvecs " + path.string());
    const std::size_t rows = size / row_size;
    in.seekg(0);
    Matrix m{rows, static_cast<std::size_t>(dim), std::vector<float>(rows * dim)};
    for (std::size_t i = 0; i < rows; ++i) {
        int32_t cur = 0;
        in.read(reinterpret_cast<char*>(&cur), 4);
        if (cur != dim) throw std::runtime_error("mixed fvecs dimensions");
        in.read(reinterpret_cast<char*>(m.data.data() + i * dim), dim * 4);
    }
    return m;
}

std::vector<std::vector<int32_t>> read_ivecs(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open ivecs " + path.string());
    std::vector<std::vector<int32_t>> rows;
    while (true) {
        int32_t width = 0;
        in.read(reinterpret_cast<char*>(&width), 4);
        if (!in) break;
        if (width <= 0) throw std::runtime_error("bad ivecs width");
        rows.emplace_back(width);
        in.read(reinterpret_cast<char*>(rows.back().data()), width * 4);
        if (!in) throw std::runtime_error("truncated ivecs");
    }
    return rows;
}

std::vector<uint32_t> read_query_order(const fs::path& path, std::size_t count) {
    if (fs::file_size(path) != count * sizeof(uint32_t)) {
        throw std::runtime_error("query-order size mismatch");
    }
    std::ifstream in(path, std::ios::binary);
    std::vector<uint32_t> order(count);
    in.read(reinterpret_cast<char*>(order.data()), order.size() * sizeof(uint32_t));
    std::vector<uint32_t> sorted = order;
    std::sort(sorted.begin(), sorted.end());
    for (std::size_t i = 0; i < count; ++i) {
        if (sorted[i] != i) throw std::runtime_error("query-order is not a permutation");
    }
    return order;
}

void write_padded_pages(const fs::path& path, const std::vector<char>& bytes) {
    fs::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::binary);
    out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    const std::size_t padding = (kPage - (bytes.size() % kPage)) % kPage;
    std::array<char, kPage> zero{};
    if (padding) out.write(zero.data(), static_cast<std::streamsize>(padding));
}

struct Layout {
    std::size_t record_bytes = 0;
    std::size_t records_per_page = 0;
    std::size_t rows = 0;

    std::uint64_t page(std::size_t id) const { return id / records_per_page; }
    std::size_t offset(std::size_t id) const { return (id % records_per_page) * record_bytes; }
    std::uint64_t pages() const { return (rows + records_per_page - 1) / records_per_page; }
};

Layout make_layout(std::size_t rows, std::size_t record_bytes) {
    if (record_bytes == 0 || record_bytes > kPage) {
        throw std::runtime_error("Glass disk port requires records <= 4 KiB");
    }
    return Layout{record_bytes, kPage / record_bytes, rows};
}

struct Meta {
    std::size_t n = 0;
    std::size_t dim = 0;
    std::size_t graph_k = 0;
    std::size_t build_l = 0;
    std::size_t graph_record_bytes = 0;
    std::size_t graph_records_per_page = 0;
    std::size_t code_size = 0;
    std::size_t code_records_per_page = 0;
    std::string implementation_fingerprint;
    std::string input_manifest_sha256;
    std::vector<int32_t> eps;
    float cal_min = 0.0f;
    float cal_dif = 0.0f;
    double build_time_ms = 0.0;
};

void write_meta(const fs::path& path, const Meta& meta) {
    std::ofstream out(path);
    out << "n=" << meta.n << "\n";
    out << "dim=" << meta.dim << "\n";
    out << "graph_k=" << meta.graph_k << "\n";
    out << "build_l=" << meta.build_l << "\n";
    out << "graph_record_bytes=" << meta.graph_record_bytes << "\n";
    out << "graph_records_per_page=" << meta.graph_records_per_page << "\n";
    out << "code_size=" << meta.code_size << "\n";
    out << "code_records_per_page=" << meta.code_records_per_page << "\n";
    out << "implementation_fingerprint=" << meta.implementation_fingerprint << "\n";
    out << "input_manifest_sha256=" << meta.input_manifest_sha256 << "\n";
    out << "cal_min=" << std::setprecision(9) << meta.cal_min << "\n";
    out << "cal_dif=" << std::setprecision(9) << meta.cal_dif << "\n";
    out << "build_time_ms=" << std::setprecision(9) << meta.build_time_ms << "\n";
    out << "eps=";
    for (std::size_t i = 0; i < meta.eps.size(); ++i) {
        if (i) out << ",";
        out << meta.eps[i];
    }
    out << "\n";
}

Meta read_meta(const fs::path& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("missing Glass metadata " + path.string());
    std::unordered_map<std::string, std::string> kv;
    std::string line;
    while (std::getline(in, line)) {
        const auto pos = line.find('=');
        if (pos != std::string::npos) kv[line.substr(0, pos)] = line.substr(pos + 1);
    }
    Meta meta;
    meta.n = std::stoull(kv.at("n"));
    meta.dim = std::stoull(kv.at("dim"));
    meta.graph_k = std::stoull(kv.at("graph_k"));
    meta.build_l = kv.count("build_l") ? std::stoull(kv.at("build_l")) : 0;
    meta.graph_record_bytes = std::stoull(kv.at("graph_record_bytes"));
    meta.graph_records_per_page = std::stoull(kv.at("graph_records_per_page"));
    meta.code_size = std::stoull(kv.at("code_size"));
    meta.code_records_per_page = std::stoull(kv.at("code_records_per_page"));
    meta.implementation_fingerprint = kv.count("implementation_fingerprint")
            ? kv.at("implementation_fingerprint")
            : "";
    meta.input_manifest_sha256 = kv.count("input_manifest_sha256")
            ? kv.at("input_manifest_sha256")
            : "";
    meta.cal_min = std::stof(kv.at("cal_min"));
    meta.cal_dif = std::stof(kv.at("cal_dif"));
    meta.build_time_ms = std::stod(kv.at("build_time_ms"));
    std::stringstream ss(kv.at("eps"));
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (!item.empty()) meta.eps.push_back(std::stoi(item));
    }
    if (meta.eps.empty()) meta.eps.push_back(0);
    return meta;
}

bool reusable_index(const fs::path& root, const Args& args) {
    const fs::path meta_path = root / "index.meta";
    const fs::path graph_path = root / "graph.pages";
    const fs::path codes_path = root / "sq4u_codes.pages";
    if (!fs::exists(meta_path) || !fs::exists(graph_path) || !fs::exists(codes_path)) {
        return false;
    }
    try {
        const Meta meta = read_meta(meta_path);
        if (meta.n == 0 || meta.graph_k != 64 || meta.build_l != 400 ||
            meta.graph_records_per_page == 0 || meta.code_records_per_page == 0 ||
            meta.implementation_fingerprint != args.require("implementation-fingerprint") ||
            meta.input_manifest_sha256 != args.require("input-manifest-sha256")) {
            return false;
        }
        const std::uint64_t graph_bytes =
                ((meta.n + meta.graph_records_per_page - 1) / meta.graph_records_per_page) * kPage;
        const std::uint64_t code_bytes =
                ((meta.n + meta.code_records_per_page - 1) / meta.code_records_per_page) * kPage;
        return fs::file_size(graph_path) == graph_bytes && fs::file_size(codes_path) == code_bytes;
    } catch (const std::exception&) {
        return false;
    }
}

void export_index(const Args& args) {
    if (args.require("method") != kMethod || args.require("layer") != kLayer) {
        throw std::runtime_error("qgraph05_glass_disk_port implements only 05c:Glass-NSG-DiskPort");
    }
    fs::path root(args.require("disk-index-dir"));
    fs::create_directories(root);
    if (reusable_index(root, args)) {
        std::cerr << "[Glass export] reuse existing disk pages: " << root << "\n";
        return;
    }
    if (fs::exists(root / "index.meta")) {
        std::cerr << "[Glass export] existing index failed provenance/geometry checks; rebuilding\n";
    }
    fs::path data_root(args.require("data-root"));
    const std::string dataset = args.require("dataset");
    std::cerr << "[Glass export] loading base for " << dataset << "\n";
    Matrix base = read_fvecs(data_root / dataset / (dataset + "_base.fvecs"));

    const auto start = Clock::now();
    const int R = 64;
    const int L = 400;
    std::cerr << "[Glass export] building official NSG graph\n";
    auto builder = glass::create_nsg("SQ4U", R, L);
    if (!builder) throw std::runtime_error("failed to create official Glass NSG builder");
    glass::Graph<int32_t> graph = builder->Build(base.data.data(), base.rows, base.dim);

    std::cerr << "[Glass export] training SQ4U and encoding base\n";
    Quant quant(static_cast<int32_t>(base.dim));
    quant.train(base.data.data(), static_cast<int32_t>(base.rows));
    quant.add(base.data.data(), static_cast<int32_t>(base.rows));

    const Layout graph_layout = make_layout(base.rows, graph.K * sizeof(int32_t));
    const Layout code_layout = make_layout(base.rows, quant.code_size());
    std::vector<char> graph_bytes(graph_layout.pages() * kPage, 0);
    std::cerr << "[Glass export] writing graph pages\n";
    for (std::size_t id = 0; id < base.rows; ++id) {
        std::memcpy(
                graph_bytes.data() + graph_layout.page(id) * kPage + graph_layout.offset(id),
                graph.edges(static_cast<int32_t>(id)),
                graph_layout.record_bytes);
    }
    std::vector<char> code_bytes(code_layout.pages() * kPage, 0);
    std::cerr << "[Glass export] writing SQ4U code pages\n";
    for (std::size_t id = 0; id < base.rows; ++id) {
        std::memcpy(
                code_bytes.data() + code_layout.page(id) * kPage + code_layout.offset(id),
                quant.get_code(static_cast<int32_t>(id)),
                code_layout.record_bytes);
    }
    write_padded_pages(root / "graph.pages", graph_bytes);
    write_padded_pages(root / "sq4u_codes.pages", code_bytes);

    const double build_ms =
            std::chrono::duration<double, std::milli>(Clock::now() - start).count();
    Meta meta;
    meta.n = base.rows;
    meta.dim = base.dim;
    meta.graph_k = graph.K;
    meta.build_l = L;
    meta.graph_record_bytes = graph_layout.record_bytes;
    meta.graph_records_per_page = graph_layout.records_per_page;
    meta.code_size = quant.code_size();
    meta.code_records_per_page = code_layout.records_per_page;
    meta.implementation_fingerprint = args.require("implementation-fingerprint");
    meta.input_manifest_sha256 = args.require("input-manifest-sha256");
    meta.eps = graph.eps.empty() ? std::vector<int32_t>{0} : graph.eps;
    meta.cal_min = quant.calibrator.min;
    meta.cal_dif = quant.calibrator.dif;
    meta.build_time_ms = build_ms;
    write_meta(root / "index.meta", meta);
    std::cerr << "[Glass export] wrote disk index: " << root << "\n";
}

struct QueryStats {
    std::uint64_t query_cache_hits=0, query_cache_misses=0, query_cache_evictions=0;
    std::uint64_t query_cache_allocated_bytes=0, peak_rss_bytes=0;
    double latency_us = 0.0;
    double prep_us = 0.0;
    double traverse_us = 0.0;
    double sort_us = 0.0;
    double io_wait_us = 0.0;
    std::uint64_t graph_pages = 0;
    std::uint64_t code_pages = 0;
    std::uint64_t bytes_read = 0;
    std::uint64_t io_requests = 0;
    std::uint64_t coalesced = 0;
    std::uint64_t duplicates = 0;
    std::uint64_t visited_nodes = 0;
    std::uint64_t distance_calls = 0;
    double recall = 0.0;
};

struct DiskAccess {
    qgraph05::DirectAioReader graph_reader;
    qgraph05::DirectAioReader code_reader;
    Layout graph_layout;
    Layout code_layout;
    qgraph05::QueryPageCache cache;
    std::vector<char> graph_cache, code_cache;
    QueryStats* stats;

    DiskAccess(const fs::path& graph, const fs::path& codes, Layout gl, Layout cl, QueryStats* s)
            : graph_reader(graph), code_reader(codes), graph_layout(gl), code_layout(cl), stats(s) {}

    const char* graph_record(std::size_t id) {
        const auto page = graph_layout.page(id);
        const auto misses=cache.misses;
        qgraph05::cached_pages(graph_reader,cache,0,page,1,graph_cache,*stats);
        stats->graph_pages+=cache.misses-misses;
        return graph_cache.data() + graph_layout.offset(id);
    }

    const uint8_t* code_record(std::size_t id) {
        const auto page = code_layout.page(id);
        const auto misses=cache.misses;
        qgraph05::cached_pages(code_reader,cache,1,page,1,code_cache,*stats);
        stats->code_pages+=cache.misses-misses;
        return reinterpret_cast<const uint8_t*>(code_cache.data() + code_layout.offset(id));
    }
};

std::vector<int32_t> search_one(
        DiskAccess& disk,
        const Meta& meta,
        const Quant::ComputerType& computer,
        int ef,
        QueryStats& stats) {
    // Match the official GraphSearcher::Search used by the experiment 03 adapter:
    // Both expansion window and candidate capacity are max(k, ef).
    // LinearPool owns upstream tie handling, visited bits and cursor reopening.
    glass::LinearPool<int32_t> pool;
    pool.reset(static_cast<int32_t>(meta.n), std::max(ef, kTopK), std::max(ef, kTopK));
    for (const int32_t ep : meta.eps) {
        ++stats.distance_calls;
        pool.insert(ep, computer(disk.code_record(ep)));
        pool.set_visited(ep);
    }
    while (pool.has_next()) {
        const int32_t u = pool.pop();
        ++stats.visited_nodes;
        const auto* edges = reinterpret_cast<const int32_t*>(disk.graph_record(u));
        for (std::size_t i = 0; i < meta.graph_k; ++i) {
            const int32_t v = edges[i];
            if (v == -1) break;
            if (pool.check_visited(v)) continue;
            pool.set_visited(v);
            ++stats.distance_calls;
            pool.insert(v, computer(disk.code_record(v)));
        }
    }
    std::vector<int32_t> result;
    for (int i = 0; i < pool.size() && i < kTopK; ++i) {
        result.push_back(pool.id(i));
    }
    while (result.size() < kTopK) result.push_back(-1);
    return result;
}

double recall_at_10(const std::vector<int32_t>& ids, const std::vector<int32_t>& truth) {
    std::unordered_set<int32_t> wanted;
    for (std::size_t i = 0; i < truth.size() && i < kTopK; ++i) wanted.insert(truth[i]);
    int hits = 0;
    for (int32_t id : ids) {
        if (wanted.count(id)) ++hits;
    }
    return static_cast<double>(hits) / kTopK;
}

std::string percentile(std::vector<double> values, double p) {
    if (values.empty()) return "0";
    std::sort(values.begin(), values.end());
    const double pos = (values.size() - 1) * p;
    const auto lo = static_cast<std::size_t>(std::floor(pos));
    const auto hi = static_cast<std::size_t>(std::ceil(pos));
    const double frac = pos - lo;
    const double value = values[lo] * (1.0 - frac) + values[hi] * frac;
    std::ostringstream out;
    out << std::setprecision(9) << value;
    return out.str();
}

void write_artifact(
        const Args& args,
        const Meta& meta,
        const fs::path& result_path,
        const fs::path& trace_path,
        const fs::path& index_root,
        const std::vector<QueryStats>& stats,
        int ef,
        double wall_seconds) {
    std::vector<double> latencies;
    double recall = 0.0, prep = 0.0, traverse = 0.0, sort = 0.0, io_wait = 0.0;
    std::uint64_t bytes = 0, io_requests = 0, sectors = 0, visited = 0, dist = 0;
    for (const auto& s : stats) {
        latencies.push_back(s.latency_us);
        recall += s.recall;
        prep += s.prep_us;
        traverse += s.traverse_us;
        sort += s.sort_us;
        io_wait += s.io_wait_us;
        bytes += s.bytes_read;
        io_requests += s.io_requests;
        sectors += s.bytes_read / kPage;
        visited += s.visited_nodes;
        dist += s.distance_calls;
    }
    const double n = static_cast<double>(stats.size());
    recall /= n;
    const double mean_latency = std::accumulate(latencies.begin(), latencies.end(), 0.0) / n;
    const double qps = n / wall_seconds;
    const std::uint64_t index_bytes =
            fs::file_size(index_root / "graph.pages") +
            fs::file_size(index_root / "sq4u_codes.pages") +
            fs::file_size(index_root / "index.meta");
    const std::uint64_t resident_bytes =
            sizeof(Meta) + meta.eps.size() * sizeof(int32_t) + meta.dim * sizeof(float);
    std::ofstream out(result_path);
    out << "{\n";
    out << "  \"schema_version\": 2,\n";
    out << "  \"status\": \"done\",\n";
    out << "  \"layer\": \"05c\",\n";
    out << "  \"dataset\": " << json_string(args.require("dataset")) << ",\n";
    out << "  \"method\": \"Glass-NSG-DiskPort\",\n";
    out << "  \"source_suite\": \"" << kSourceSuite << "\",\n";
    out << "  \"source_kernel\": \"" << kSourceKernel << "\",\n";
    out << "  \"port_kind\": \"" << kPortKind << "\",\n";
    out << "  \"storage_mode\": \"hybrid_disk\",\n";
    out << "  \"cache_mode\": " << json_string(args.require("cache-mode")) << ",\n";
    out << "  \"phase\": " << json_string(args.require("phase")) << ",\n";
    out << "  \"run_id\": " << json_string(args.require("run-id")) << ",\n";
    out << "  \"repeat_id\": " << args.require("repeat-id") << ",\n";
    out << "  \"workers\": " << args.require("workers") << ",\n";
    out << "  \"warmup_queries\": " << args.require("warmup-queries") << ",\n";
    out << "  \"query_split_sha256\": " << json_string(args.require("query-split-sha256")) << ",\n";
    out << "  \"query_order_sha256\": " << json_string(args.require("query-order-sha256")) << ",\n";
    out << "  \"query_order_seed\": " << args.require("query-order-seed") << ",\n";
    out << "  \"index_path\": " << json_string(fs::absolute(index_root).string()) << ",\n";
    out << "  \"formal_ready\": false,\n";
    out << "  \"page_size\": 4096,\n";
    out << "  \"whole_graph_in_memory\": false,\n";
    out << "  \"whole_payload_in_memory\": false,\n";
    out << "  \"direct_io\": " << (qgraph05::reference_mmap_enabled() ? "false" : "true") << ",\n";
    out << "  \"native_aio\": " << (qgraph05::reference_mmap_enabled() ? "false" : "true") << ",\n";
    out << "  \"io_backend\": " << json_string(qgraph05::reference_mmap_enabled()
            ? "reference_mmap_not_performance" : "linux_native_aio_odirect") << ",\n";
    out << "  \"implementation_fingerprint\": " << json_string(args.require("implementation-fingerprint")) << ",\n";
    out << "  \"native_binary_sha256\": " << json_string(args.require("native-binary-sha256")) << ",\n";
    out << "  \"input_manifest_sha256\": " << json_string(args.require("input-manifest-sha256")) << ",\n";
    out << "  \"source_index_manifest_sha256\": " << json_string(sha256(index_root / "index.meta")) << ",\n";
    out << "  \"git_commit\": " << json_string(args.require("git-commit")) << ",\n";
    out << "  \"compiler\": " << json_string(std::string("gcc ") + __VERSION__) << ",\n";
    out << "  \"simd\": \"glass_sq4u_native\",\n";
    out << "  \"base_count\": " << meta.n << ",\n";
    out << "  \"dimension\": " << meta.dim << ",\n";
    out << "  \"build_l\": " << meta.build_l << ",\n";
    out << "  \"search_dram_budget_gib\": " << args.require("search-dram-budget-gib") << ",\n";
    out << "  \"resident_bytes\": " << resident_bytes << ",\n";
    out << "  \"codebook_bytes\": 8,\n";
    out << "  \"worker_scratch_bytes\": null,\n";
    out << "  \"query_cache_policy\": \"4k_lru_per_query_v1\",\n";
    out << "  \"query_cache_budget_per_worker_bytes\": " << qgraph05::QueryPageCache::default_budget << ",\n";
    out << "  \"memory_accounting_complete\": false,\n";
    out << "  \"cache_bytes\": 0,\n";
    out << "  \"cache_nodes\": 0,\n";
    out << "  \"peak_rss_bytes\": " << qgraph05::measured_peak_rss() << ",\n";
    out << "  \"cpu_affinity\": \"uncontrolled\",\n";
    out << "  \"numa_node\": 0,\n";
    out << "  \"implementation_parity\": \"not_verified\",\n";
    out << "  \"parity\": null,\n";
    out << "  \"measurement_scope\": \"test_only_excludes_warmup\",\n";
    out << "  \"storage_cache_protocol\": \"uncontrolled\",\n";
    out << "  \"query_trace_path\": " << json_string(fs::absolute(trace_path).string()) << ",\n";
    out << "  \"query_trace_sha256\": " << json_string(sha256(trace_path)) << ",\n";
    out << "  \"summary_rows\": [{\n";
    out << "    \"config_id\": \"NSG_R64_L400_SQ4U\",\n";
    out << "    \"search_param\": \"ef=" << ef << "\",\n";
    out << "    \"search_width\": " << ef << ",\n";
    out << "    \"beam_width\": 1,\n";
    out << "    \"recall\": " << recall << ",\n";
    out << "    \"qps\": " << qps << ",\n";
    out << "    \"latency_mean_us\": " << mean_latency << ",\n";
    out << "    \"latency_p50_us\": " << percentile(latencies, 0.50) << ",\n";
    out << "    \"latency_p95_us\": " << percentile(latencies, 0.95) << ",\n";
    out << "    \"latency_p99_us\": " << percentile(latencies, 0.99) << ",\n";
    out << "    \"query_count\": " << stats.size() << ",\n";
    out << "    \"index_size_mb\": " << (static_cast<double>(index_bytes) / (1024.0 * 1024.0)) << ",\n";
    out << "    \"resident_bytes\": " << resident_bytes << ",\n";
    out << "    \"peak_rss_bytes\": " << qgraph05::measured_peak_rss() << ",\n";
    out << "    \"io_requests_per_query\": " << (static_cast<double>(io_requests) / n) << ",\n";
    out << "    \"sectors_4k_per_query\": " << (static_cast<double>(sectors) / n) << ",\n";
    out << "    \"bytes_read_per_query\": " << (static_cast<double>(bytes) / n) << ",\n";
    out << "    \"io_wait_us\": " << (io_wait / n) << ",\n";
    out << "    \"distance_compute_us\": " << (traverse / n) << ",\n";
    out << "    \"query_prep_us\": " << (prep / n) << ",\n";
    out << "    \"queue_compute_us\": " << (traverse / n) << ",\n";
    out << "    \"rerank_us\": " << (sort / n) << ",\n";
    out << "    \"visited_nodes\": " << (static_cast<double>(visited) / n) << ",\n";
    out << "    \"distance_evaluations\": " << (static_cast<double>(dist) / n) << "\n";
    out << "  }]\n";
    out << "}\n";
}

void write_export_artifact(
        const Args& args,
        const Meta& meta,
        const fs::path& result_path,
        const fs::path& index_root) {
    const std::uint64_t index_bytes =
            fs::file_size(index_root / "graph.pages") +
            fs::file_size(index_root / "sq4u_codes.pages") +
            fs::file_size(index_root / "index.meta");
    std::ofstream out(result_path);
    out << "{\n";
    out << "  \"schema_version\": 2,\n";
    out << "  \"status\": \"done\",\n";
    out << "  \"layer\": \"05c\",\n";
    out << "  \"dataset\": " << json_string(args.require("dataset")) << ",\n";
    out << "  \"method\": \"Glass-NSG-DiskPort\",\n";
    out << "  \"source_suite\": \"" << kSourceSuite << "\",\n";
    out << "  \"source_kernel\": \"" << kSourceKernel << "\",\n";
    out << "  \"port_kind\": \"" << kPortKind << "\",\n";
    out << "  \"storage_mode\": \"hybrid_disk\",\n";
    out << "  \"cache_mode\": " << json_string(args.require("cache-mode")) << ",\n";
    out << "  \"phase\": \"export\",\n";
    out << "  \"run_id\": " << json_string(args.require("run-id")) << ",\n";
    out << "  \"repeat_id\": " << args.require("repeat-id") << ",\n";
    out << "  \"workers\": " << args.require("workers") << ",\n";
    out << "  \"warmup_queries\": " << args.require("warmup-queries") << ",\n";
    out << "  \"query_split_sha256\": " << json_string(args.require("query-split-sha256")) << ",\n";
    out << "  \"query_order_sha256\": " << json_string(args.require("query-order-sha256")) << ",\n";
    out << "  \"query_order_seed\": " << args.require("query-order-seed") << ",\n";
    out << "  \"index_path\": " << json_string(fs::absolute(index_root).string()) << ",\n";
    out << "  \"formal_ready\": true,\n";
    out << "  \"page_size\": 4096,\n";
    out << "  \"whole_graph_in_memory\": false,\n";
    out << "  \"whole_payload_in_memory\": false,\n";
    out << "  \"direct_io\": true,\n";
    out << "  \"native_aio\": true,\n";
    out << "  \"io_backend\": \"linux_native_aio_odirect\",\n";
    out << "  \"implementation_fingerprint\": " << json_string(args.require("implementation-fingerprint")) << ",\n";
    out << "  \"native_binary_sha256\": " << json_string(args.require("native-binary-sha256")) << ",\n";
    out << "  \"input_manifest_sha256\": " << json_string(args.require("input-manifest-sha256")) << ",\n";
    out << "  \"source_index_manifest_sha256\": " << json_string(sha256(index_root / "index.meta")) << ",\n";
    out << "  \"base_count\": " << meta.n << ",\n";
    out << "  \"dimension\": " << meta.dim << ",\n";
    out << "  \"search_dram_budget_gib\": " << args.require("search-dram-budget-gib") << ",\n";
    out << "  \"resident_bytes\": 0,\n";
    out << "  \"codebook_bytes\": 8,\n";
    out << "  \"index_size_bytes\": " << index_bytes << ",\n";
    out << "  \"summary_rows\": []\n";
    out << "}\n";
}

void run_search_single(const Args& args) {
    fs::path root(args.require("disk-index-dir"));
    const Meta meta = read_meta(root / "index.meta");
    if (meta.build_l != 400 ||
        meta.implementation_fingerprint != args.require("implementation-fingerprint") ||
        meta.input_manifest_sha256 != args.require("input-manifest-sha256")) {
        throw std::runtime_error("Glass index provenance or build_l does not match this run");
    }
    Matrix queries = read_fvecs(args.require("query"));
    auto gt = read_ivecs(args.require("groundtruth"));
    if (queries.rows != gt.size()) throw std::runtime_error("query/groundtruth size mismatch");
    auto order = read_query_order(args.require("query-order"), queries.rows);

    Quant quant(static_cast<int32_t>(meta.dim));
    quant.calibrator.min = meta.cal_min;
    quant.calibrator.dif = meta.cal_dif;

    const int ef = static_cast<int>(
            args.values.count("integration-widths") ? args.number("integration-widths") : 100);
    const int workers = std::max<int>(1, static_cast<int>(args.number("workers")));
    qgraph05::check_query_cache_budget(workers, std::stod(args.require("search-dram-budget-gib")));
    std::vector<QueryStats> stats(queries.rows);
    std::vector<std::vector<int32_t>> ids(queries.rows);
    fs::create_directories(fs::path(args.require("query-trace")).parent_path());
    auto run_query = [&](std::size_t qid, bool record) {
        QueryStats local;
        const auto q0 = Clock::now();
        auto computer = quant.get_computer(queries.data.data() + qid * queries.dim);
        const auto q1 = Clock::now();
        DiskAccess disk(
                root / "graph.pages",
                root / "sq4u_codes.pages",
                Layout{meta.graph_record_bytes, meta.graph_records_per_page, meta.n},
                Layout{meta.code_size, meta.code_records_per_page, meta.n},
                &local);
        ids[qid] = search_one(disk, meta, computer, ef, local);
        const auto q2 = Clock::now();
        local.recall = recall_at_10(ids[qid], gt[qid]);
        const auto q3 = Clock::now();
        local.prep_us = std::chrono::duration<double, std::micro>(q1 - q0).count();
        local.traverse_us = std::chrono::duration<double, std::micro>(q2 - q1).count();
        local.sort_us = std::chrono::duration<double, std::micro>(q3 - q2).count();
        local.latency_us = std::chrono::duration<double, std::micro>(q3 - q0).count();
        local.peak_rss_bytes=qgraph05::measured_peak_rss();
        if (record) stats[qid] = local;
    };
    const auto warmup_count = std::min(order.size(), static_cast<std::size_t>(args.number("warmup-queries")));
#pragma omp parallel for schedule(dynamic) num_threads(workers)
    for (std::size_t pos = 0; pos < warmup_count; ++pos) run_query(order[pos], false);
    std::cerr << "[query] warmup completed: " << warmup_count << " queries\n";
    const auto wall_start = Clock::now();
#pragma omp parallel for schedule(dynamic) num_threads(workers)
    for (std::size_t pos = 0; pos < order.size(); ++pos) run_query(order[pos], true);
    const double wall_seconds = std::chrono::duration<double>(Clock::now() - wall_start).count();

    std::ofstream trace(args.require("query-trace"));
    for (std::size_t qid = 0; qid < queries.rows; ++qid) {
        const auto& s = stats[qid];
        trace << "{"
              << "\"layer\":\"05c\",\"storage_mode\":\"hybrid_disk\",\"cache_mode\":"
              << json_string(args.require("cache-mode"))
              << ",\"dataset\":" << json_string(args.require("dataset"))
              << ",\"method\":\"Glass-NSG-DiskPort\",\"config_id\":\"NSG_R64_L400_SQ4U\","
              << "\"repeat_id\":" << args.require("repeat-id")
              << ",\"query_id\":" << qid
              << ",\"search_width\":" << ef
              << ",\"beam_width\":1,\"workers\":" << args.require("workers")
              << ",\"search_dram_budget_gib\":" << args.require("search-dram-budget-gib")
              << ",\"cache_nodes\":0,\"resident_bytes\":0,\"cache_bytes\":0,\"peak_rss_bytes\":" << s.peak_rss_bytes << ","
              << "\"query_cache_allocated_bytes\":" << s.query_cache_allocated_bytes << ","
              << "\"query_cache_evictions\":" << s.query_cache_evictions << ","
              << "\"recall_at_10\":" << s.recall
              << ",\"latency_us\":" << s.latency_us
              << ",\"query_prep_us\":" << s.prep_us
              << ",\"queue_compute_us\":" << s.traverse_us
              << ",\"io_wait_us\":" << s.io_wait_us << ",\"distance_compute_us\":" << s.traverse_us
              << ",\"rerank_us\":" << s.sort_us
              << ",\"visited_nodes\":" << s.visited_nodes
              << ",\"distance_evaluations\":" << s.distance_calls
              << ",\"io_requests\":" << s.io_requests
              << ",\"sectors_4k\":" << (s.bytes_read / kPage)
              << ",\"bytes_read\":" << s.bytes_read
              << ",\"average_read_bytes\":" << (s.io_requests ? s.bytes_read / s.io_requests : 0)
              << ",\"coalesced_requests\":" << s.coalesced
              << ",\"duplicate_pages_removed\":" << s.duplicates
              << ",\"shared_cache_hits\":0,\"shared_cache_misses\":0,"
              << "\"query_cache_hits\":" << s.query_cache_hits << ",\"query_cache_misses\":" << s.query_cache_misses << ""
              << ",\"result_ids\":[";
        for (std::size_t i = 0; i < ids[qid].size(); ++i) {
            if (i) trace << ",";
            trace << ids[qid][i];
        }
        trace << "]}\n";
    }
    trace.close();
    write_artifact(
            args,
            meta,
            args.require("result-json"),
            args.require("query-trace"),
            root,
            stats,
            ef,
            wall_seconds);
}


std::vector<int> requested_widths(const Args& args) {
    std::vector<int> widths;
    auto parse_csv = [&](const std::string& text) {
        std::stringstream ss(text);
        std::string item;
        while (std::getline(ss, item, ',')) {
            if (item.empty()) continue;
            const int value = std::stoi(item);
            if (value <= 0) throw std::runtime_error("search width must be positive");
            widths.push_back(value);
        }
    };
    if (const char* env = std::getenv("QG05_FAST_WIDTHS")) {
        parse_csv(env);
    } else if (args.values.count("integration-widths")) {
        parse_csv(args.values.at("integration-widths"));
    } else {
        for (int value = 1; value <= 30; ++value) widths.push_back(value);
        for (int value = 40; value <= 100; value += 10) widths.push_back(value);
        for (int value = 140; value <= 580; value += 40) widths.push_back(value);
    }
    if (widths.empty()) throw std::runtime_error("empty search width list");
    return widths;
}

std::string read_text_file(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    std::ostringstream buffer;
    buffer << input.rdbuf();
    return buffer.str();
}

std::string extract_summary_body(const std::string& text) {
    const std::string key = "\"summary_rows\": [";
    const auto begin_key = text.find(key);
    if (begin_key == std::string::npos) throw std::runtime_error("summary_rows not found");
    const auto begin = begin_key + key.size();
    int depth = 1;
    bool in_string = false;
    bool escaped = false;
    for (std::size_t i = begin; i < text.size(); ++i) {
        const char ch = text[i];
        if (escaped) { escaped = false; continue; }
        if (ch == '\\') { escaped = in_string; continue; }
        if (ch == '"') { in_string = !in_string; continue; }
        if (in_string) continue;
        if (ch == '[') ++depth;
        if (ch == ']') {
            --depth;
            if (depth == 0) return text.substr(begin, i - begin);
        }
    }
    throw std::runtime_error("summary_rows array did not terminate");
}

std::string replace_summary_body(const std::string& text, const std::string& body) {
    const std::string key = "\"summary_rows\": [";
    const auto begin_key = text.find(key);
    if (begin_key == std::string::npos) throw std::runtime_error("summary_rows not found");
    const auto begin = begin_key + key.size();
    int depth = 1;
    bool in_string = false;
    bool escaped = false;
    for (std::size_t i = begin; i < text.size(); ++i) {
        const char ch = text[i];
        if (escaped) { escaped = false; continue; }
        if (ch == '\\') { escaped = in_string; continue; }
        if (ch == '"') { in_string = !in_string; continue; }
        if (in_string) continue;
        if (ch == '[') ++depth;
        if (ch == ']') {
            --depth;
            if (depth == 0) return text.substr(0, begin) + body + text.substr(i);
        }
    }
    throw std::runtime_error("summary_rows array did not terminate");
}

void replace_all(std::string& text, const std::string& from, const std::string& to) {
    if (from.empty()) return;
    std::size_t pos = 0;
    while ((pos = text.find(from, pos)) != std::string::npos) {
        text.replace(pos, from.size(), to);
        pos += to.size();
    }
}

void run_search(const Args& args) {
    const auto widths = requested_widths(args);
    const fs::path final_json(args.require("result-json"));
    const fs::path final_trace(args.require("query-trace"));
    std::vector<fs::path> jsons;
    std::vector<fs::path> traces;
    for (int width : widths) {
        Args one = args;
        one.values["integration-widths"] = std::to_string(width);
        fs::path tmp_json = final_json;
        tmp_json += ".w" + std::to_string(width) + ".tmp";
        fs::path tmp_trace = final_trace;
        tmp_trace += ".w" + std::to_string(width) + ".tmp";
        one.values["result-json"] = tmp_json.string();
        one.values["query-trace"] = tmp_trace.string();
        run_search_single(one);
        jsons.push_back(tmp_json);
        traces.push_back(tmp_trace);
    }

    fs::create_directories(final_trace.parent_path());
    {
        std::ofstream out(final_trace, std::ios::binary);
        for (const auto& trace : traces) {
            std::ifstream in(trace, std::ios::binary);
            out << in.rdbuf();
        }
    }

    std::vector<std::string> bodies;
    for (const auto& json : jsons) {
        std::string body = extract_summary_body(read_text_file(json));
        const auto first = body.find_first_not_of(" \n\r\t");
        const auto last = body.find_last_not_of(" \n\r\t");
        bodies.push_back(first == std::string::npos ? std::string() : body.substr(first, last - first + 1));
    }
    std::ostringstream joined;
    for (std::size_t i = 0; i < bodies.size(); ++i) {
        if (i) joined << ",\n";
        joined << bodies[i];
    }

    std::string final_doc = replace_summary_body(read_text_file(jsons.front()), joined.str());
    replace_all(final_doc, json_string(fs::absolute(traces.front()).string()), json_string(fs::absolute(final_trace).string()));
    replace_all(final_doc, json_string(sha256(traces.front())), json_string(sha256(final_trace)));
    fs::create_directories(final_json.parent_path());
    std::ofstream out(final_json, std::ios::binary);
    out << final_doc;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        Args args = Args::parse(argc, argv);
        if (args.require("contract-version") != "2") {
            throw std::runtime_error("only contract version 2 is supported");
        }
        if (args.require("method") != kMethod || args.require("layer") != kLayer) {
            throw std::runtime_error("unsupported method/layer for Glass port");
        }
        if (args.require("storage-mode") != "hybrid_disk" ||
            args.require("direct-io") != "required" ||
            args.require("native-aio") != "required") {
            throw std::runtime_error("Glass 05C requires hybrid_disk + O_DIRECT + native AIO");
        }
        const std::string phase = args.require("phase");
        if (phase == "export") {
            export_index(args);
            std::ofstream trace(args.require("query-trace"));
            trace.close();
            write_export_artifact(
                    args,
                    read_meta(fs::path(args.require("disk-index-dir")) / "index.meta"),
                    args.require("result-json"),
                    args.require("disk-index-dir"));
            std::cout << "Glass-NSG-DiskPort export done\n";
        } else {
            export_index(args);
            run_search(args);
            std::cout << "Glass-NSG-DiskPort search done\n";
        }
        return 0;
    } catch (const std::exception& exc) {
        std::cerr << "ERROR: " << exc.what() << "\n";
        return 2;
    }
}
