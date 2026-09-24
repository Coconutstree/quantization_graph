#include "direct_io.hpp"
#include "query_page_cache.hpp"

#include "qg/qg.hpp"
#include "qg/qg_builder.hpp"
#include "qg/qg_query.hpp"
#include "qg/qg_scanner.hpp"
#include "space/l2.hpp"

#include <openssl/evp.h>
#include <omp.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cfloat>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

namespace {

constexpr std::size_t kPage = qgraph05::kPageSize;
constexpr int kTopK = 10;
constexpr const char* kMethod = "SymphonyQG-DiskPort";
constexpr const char* kLayer = "05c";
constexpr const char* kSourceSuite = "03_system_fair";
constexpr const char* kSourceKernel = "official SymphonyQG FastScan LUT+SIMD";
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

    std::size_t number(const std::string& key) const {
        return static_cast<std::size_t>(std::stoull(require(key)));
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

std::size_t ceil_log2(std::size_t x) {
    std::size_t p = 0;
    std::size_t v = 1;
    while (v < x) {
        v <<= 1;
        ++p;
    }
    return p;
}

struct Meta {
    std::size_t n = 0;
    std::size_t dim = 0;
    std::size_t degree = 0;
    std::size_t build_l = 0;
    std::size_t padded_dim = 0;
    std::size_t code_offset = 0;
    std::size_t factor_offset = 0;
    std::size_t neighbor_offset = 0;
    std::size_t row_floats = 0;
    std::size_t row_bytes = 0;
    std::size_t pages_per_row = 0;
    std::string implementation_fingerprint;
    std::string input_manifest_sha256;
    std::uint32_t entry_point = 0;
    double build_time_ms = 0.0;
    std::uint64_t build_distance_computations = 0;
};

Meta make_meta(std::size_t n, std::size_t dim, std::size_t degree) {
    Meta meta;
    meta.n = n;
    meta.dim = dim;
    meta.degree = degree;
    meta.padded_dim = std::size_t{1} << ceil_log2(dim);
    meta.code_offset = dim;
    meta.factor_offset = meta.code_offset + meta.padded_dim / 64 * 2 * degree;
    meta.neighbor_offset = meta.factor_offset + sizeof(symqg::Factor) * degree / sizeof(float);
    meta.row_floats = meta.neighbor_offset + degree;
    meta.row_bytes = meta.row_floats * sizeof(float);
    meta.pages_per_row = (meta.row_bytes + kPage - 1) / kPage;
    return meta;
}

void write_meta(const fs::path& path, const Meta& meta) {
    std::ofstream out(path);
    out << "n=" << meta.n << "\n";
    out << "dim=" << meta.dim << "\n";
    out << "degree=" << meta.degree << "\n";
    out << "build_l=" << meta.build_l << "\n";
    out << "padded_dim=" << meta.padded_dim << "\n";
    out << "code_offset=" << meta.code_offset << "\n";
    out << "factor_offset=" << meta.factor_offset << "\n";
    out << "neighbor_offset=" << meta.neighbor_offset << "\n";
    out << "row_floats=" << meta.row_floats << "\n";
    out << "row_bytes=" << meta.row_bytes << "\n";
    out << "pages_per_row=" << meta.pages_per_row << "\n";
    out << "implementation_fingerprint=" << meta.implementation_fingerprint << "\n";
    out << "input_manifest_sha256=" << meta.input_manifest_sha256 << "\n";
    out << "entry_point=" << meta.entry_point << "\n";
    out << "build_time_ms=" << std::setprecision(9) << meta.build_time_ms << "\n";
    out << "build_distance_computations=" << meta.build_distance_computations << "\n";
}

Meta read_meta(const fs::path& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("missing SymphonyQG metadata " + path.string());
    std::unordered_map<std::string, std::string> kv;
    std::string line;
    while (std::getline(in, line)) {
        const auto pos = line.find('=');
        if (pos != std::string::npos) kv[line.substr(0, pos)] = line.substr(pos + 1);
    }
    Meta meta;
    meta.n = std::stoull(kv.at("n"));
    meta.dim = std::stoull(kv.at("dim"));
    meta.degree = std::stoull(kv.at("degree"));
    meta.build_l = kv.count("build_l") ? std::stoull(kv.at("build_l")) : 0;
    meta.padded_dim = std::stoull(kv.at("padded_dim"));
    meta.code_offset = std::stoull(kv.at("code_offset"));
    meta.factor_offset = std::stoull(kv.at("factor_offset"));
    meta.neighbor_offset = std::stoull(kv.at("neighbor_offset"));
    meta.row_floats = std::stoull(kv.at("row_floats"));
    meta.row_bytes = std::stoull(kv.at("row_bytes"));
    meta.pages_per_row = std::stoull(kv.at("pages_per_row"));
    meta.implementation_fingerprint = kv.count("implementation_fingerprint")
            ? kv.at("implementation_fingerprint")
            : "";
    meta.input_manifest_sha256 = kv.count("input_manifest_sha256")
            ? kv.at("input_manifest_sha256")
            : "";
    meta.entry_point = static_cast<std::uint32_t>(std::stoul(kv.at("entry_point")));
    meta.build_time_ms = std::stod(kv.at("build_time_ms"));
    meta.build_distance_computations = kv.count("build_distance_computations")
            ? std::stoull(kv.at("build_distance_computations"))
            : 0;
    return meta;
}

bool reusable_index(const fs::path& root, const Args& args) {
    const fs::path meta_path = root / "index.meta";
    const fs::path rows_path = root / "node_rows.pages";
    const fs::path rotator_path = root / "rotator.bin";
    if (!fs::exists(meta_path) || !fs::exists(rows_path) || !fs::exists(rotator_path)) {
        return false;
    }
    try {
        const Meta meta = read_meta(meta_path);
        if (meta.n == 0 || meta.degree != 64 || meta.build_l != 400 ||
            meta.pages_per_row == 0 ||
            meta.implementation_fingerprint != args.require("implementation-fingerprint") ||
            meta.input_manifest_sha256 != args.require("input-manifest-sha256")) {
            return false;
        }
        const std::uint64_t rows_bytes = meta.n * meta.pages_per_row * kPage;
        const std::uint64_t raw_rotator_bytes = meta.padded_dim * sizeof(float);
        const std::uint64_t rotator_bytes =
                ((raw_rotator_bytes + kPage - 1) / kPage) * kPage;
        return fs::file_size(rows_path) == rows_bytes &&
               fs::file_size(rotator_path) == rotator_bytes;
    } catch (const std::exception&) {
        return false;
    }
}

void write_padded_file(const fs::path& path, const std::vector<char>& bytes) {
    fs::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::binary);
    out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    const std::size_t padding = (kPage - (bytes.size() % kPage)) % kPage;
    std::array<char, kPage> zero{};
    if (padding) out.write(zero.data(), static_cast<std::streamsize>(padding));
}

void export_index(const Args& args) {
    fs::path root(args.require("disk-index-dir"));
    fs::create_directories(root);
    if (reusable_index(root, args)) {
        std::cerr << "[SymphonyQG export] reuse verified disk pages: " << root << "\n";
        return;
    }
    if (fs::exists(root / "index.meta")) {
        std::cerr << "[SymphonyQG export] existing index failed provenance/geometry checks; rebuilding\n";
    }
    const std::string dataset = args.require("dataset");
    Matrix base = read_fvecs(fs::path(args.require("data-root")) / dataset / (dataset + "_base.fvecs"));
    const std::size_t degree = 64;
    if (degree % 32 != 0 || base.dim > 2048) {
        throw std::runtime_error("unsupported SymphonyQG disk-port geometry");
    }
    Meta meta = make_meta(base.rows, base.dim, degree);
    meta.build_l = 400;
    meta.implementation_fingerprint = args.require("implementation-fingerprint");
    meta.input_manifest_sha256 = args.require("input-manifest-sha256");
    const auto started = Clock::now();
    symqg::QuantizedGraph qg(base.rows, degree, base.dim);
    symqg::QGBuilder builder(qg, 400, base.data.data(), 9999);
    builder.build(3);
    fs::path raw = root / "official_qg.index";
    qg.save_index(raw.c_str());
    const double build_ms =
            std::chrono::duration<double, std::milli>(Clock::now() - started).count();
    meta.build_distance_computations = builder.build_distance_count();

    std::ifstream in(raw, std::ios::binary);
    if (!in) throw std::runtime_error("failed to read built SymphonyQG index");
    in.read(reinterpret_cast<char*>(&meta.entry_point), sizeof(symqg::PID));
    std::vector<char> row(meta.row_bytes);
    std::vector<char> pages(meta.n * meta.pages_per_row * kPage, 0);
    for (std::size_t id = 0; id < meta.n; ++id) {
        in.read(row.data(), static_cast<std::streamsize>(row.size()));
        if (!in) throw std::runtime_error("truncated SymphonyQG row data");
        std::memcpy(
                pages.data() + id * meta.pages_per_row * kPage,
                row.data(),
                row.size());
    }
    const std::size_t rotator_bytes = meta.padded_dim * sizeof(float);
    std::vector<char> rotator(rotator_bytes);
    in.read(rotator.data(), static_cast<std::streamsize>(rotator.size()));
    if (!in) throw std::runtime_error("truncated SymphonyQG rotator");
    write_padded_file(root / "node_rows.pages", pages);
    write_padded_file(root / "rotator.bin", rotator);
    fs::remove(raw);
    meta.build_time_ms = build_ms;
    write_meta(root / "index.meta", meta);
}

struct QueryStats {
    std::uint64_t query_cache_hits=0, query_cache_misses=0, query_cache_evictions=0;
    std::uint64_t query_cache_allocated_bytes=0, peak_rss_bytes=0;
    double latency_us = 0.0;
    double prep_us = 0.0;
    double traverse_us = 0.0;
    double sort_us = 0.0;
    double io_wait_us = 0.0;
    std::uint64_t bytes_read = 0;
    std::uint64_t io_requests = 0;
    std::uint64_t coalesced = 0;
    std::uint64_t duplicates = 0;
    std::uint64_t visited_nodes = 0;
    std::uint64_t distance_calls = 0;
    double recall = 0.0;
};

struct RowReader {
    qgraph05::DirectAioReader reader;
    const Meta& meta;
    QueryStats* stats;
    qgraph05::QueryPageCache cache;
    std::vector<char> current_row;

    RowReader(const fs::path& path, const Meta& meta_ref, QueryStats* stat_ref)
            : reader(path), meta(meta_ref), stats(stat_ref) {}

    const char* row(std::uint32_t id) {
        qgraph05::cached_pages(reader, cache, 0, static_cast<std::uint64_t>(id)*meta.pages_per_row,
                              meta.pages_per_row, current_row, *stats);
        return current_row.data();
    }
};

template <class Reader>
std::vector<int32_t> search_one(
        Reader& rows,
        const Meta& meta,
        const symqg::QGQuery& q_obj,
        const symqg::QGScanner& scanner,
        const float* query,
        int ef,
        QueryStats& stats) {
    if (ef < 1) throw std::invalid_argument("SymphonyQG search width must be positive");
    symqg::buffer::SearchBuffer pool(ef);
    symqg::HashBasedBooleanSet seen(std::min<std::size_t>(meta.n / 10, static_cast<std::size_t>(ef) * ef));
    pool.insert(meta.entry_point, FLT_MAX);
    symqg::buffer::ResultBuffer exact(kTopK);
    std::vector<float> appro(meta.degree);
    while (pool.has_next()) {
        const std::uint32_t u = pool.pop();
        if (seen.get(u)) continue;
        seen.set(u);
        ++stats.visited_nodes;
        const char* row = rows.row(u);
        const float* cur = reinterpret_cast<const float*>(row);
        const float sqr_y = symqg::space::l2_sqr(q_obj.query_data(), cur, meta.dim);
        const auto* packed = reinterpret_cast<const uint8_t*>(cur + meta.code_offset);
        const float* factor = cur + meta.factor_offset;
        scanner.scan_neighbors(
                appro.data(),
                q_obj.lut().data(),
                sqr_y,
                q_obj.lower_val(),
                q_obj.width(),
                q_obj.sumq(),
                packed,
                factor);
        ++stats.distance_calls;
        const auto* neighbors = reinterpret_cast<const symqg::PID*>(cur + meta.neighbor_offset);
        for (std::size_t i = 0; i < meta.degree; ++i) {
            const auto v = neighbors[i];
            if (v == symqg::kPidMax || v >= meta.n) throw std::runtime_error("Invalid SymphonyQG neighbor");
            if (pool.is_full(appro[i]) || seen.get(v)) continue;
            // Native CPU prefetch is not a demand read of a candidate's disk row.
            pool.insert(v, appro[i]);
        }
        exact.insert(u, sqr_y);
    }
    // Mirror QuantizedGraph::update_results, including its snapshot and tie rules.
    if (!exact.is_full()) {
        auto ids = exact.ids();
        for (symqg::PID id : ids) {
            const auto* cur = reinterpret_cast<const float*>(rows.row(id));
            const auto* ptr = reinterpret_cast<const symqg::PID*>(cur + meta.neighbor_offset);
            // Subsequent disk reads reuse current_row, so retain these neighbor IDs.
            std::vector<symqg::PID> neighbors(ptr, ptr + meta.degree);
            for (auto v : neighbors) {
                if (v >= meta.n) throw std::runtime_error("Invalid SymphonyQG neighbor");
                if (!seen.get(v)) {
                    seen.set(v);
                    const auto* vector = reinterpret_cast<const float*>(rows.row(v));
                    exact.insert(v, symqg::space::l2_sqr(query, vector, meta.dim));
                }
            }
            if (exact.is_full()) break;
        }
    }
    std::vector<symqg::PID> ids(kTopK);
    exact.copy_results(ids.data());
    std::vector<int32_t> result(ids.begin(), ids.end());
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

double pct(std::vector<double> values, double p) {
    std::sort(values.begin(), values.end());
    const double pos = (values.size() - 1) * p;
    const auto lo = static_cast<std::size_t>(std::floor(pos));
    const auto hi = static_cast<std::size_t>(std::ceil(pos));
    return values[lo] * (1.0 - (pos - lo)) + values[hi] * (pos - lo);
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
    std::vector<double> lat;
    double recall = 0.0, prep = 0.0, traverse = 0.0, sort = 0.0, io_wait = 0.0;
    std::uint64_t bytes = 0, requests = 0, visited = 0, dist = 0;
    for (const auto& s : stats) {
        lat.push_back(s.latency_us);
        recall += s.recall;
        prep += s.prep_us;
        traverse += s.traverse_us;
        sort += s.sort_us;
        io_wait += s.io_wait_us;
        bytes += s.bytes_read;
        requests += s.io_requests;
        visited += s.visited_nodes;
        dist += s.distance_calls;
    }
    const double n = static_cast<double>(stats.size());
    const std::uint64_t index_bytes =
            fs::file_size(index_root / "node_rows.pages") +
            fs::file_size(index_root / "rotator.bin") +
            fs::file_size(index_root / "index.meta");
    const std::uint64_t resident_bytes = meta.padded_dim * sizeof(float) + 4096;
    std::ofstream out(result_path);
    out << "{\n";
    out << "  \"schema_version\": 2,\n";
    out << "  \"status\": \"done\",\n";
    out << "  \"layer\": \"05c\",\n";
    out << "  \"dataset\": " << json_string(args.require("dataset")) << ",\n";
    out << "  \"method\": \"SymphonyQG-DiskPort\",\n";
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
    out << "  \"build_distance_computations\": " << meta.build_distance_computations << ",\n";
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
    out << "  \"simd\": \"symphonyqg_fastscan_avx512\",\n";
    out << "  \"base_count\": " << meta.n << ",\n";
    out << "  \"dimension\": " << meta.dim << ",\n";
    out << "  \"build_l\": " << meta.build_l << ",\n";
    out << "  \"search_dram_budget_gib\": " << args.require("search-dram-budget-gib") << ",\n";
    out << "  \"resident_bytes\": " << resident_bytes << ",\n";
    out << "  \"codebook_bytes\": 0,\n";
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
    out << "    \"config_id\": \"QG_R64_EF400_t3\",\n";
    out << "    \"search_param\": \"ef=" << ef << "\",\n";
    out << "    \"search_width\": " << ef << ",\n";
    out << "    \"beam_width\": 1,\n";
    out << "    \"recall\": " << (recall / n) << ",\n";
    out << "    \"qps\": " << (n / wall_seconds) << ",\n";
    out << "    \"latency_mean_us\": " << (std::accumulate(lat.begin(), lat.end(), 0.0) / n) << ",\n";
    out << "    \"latency_p50_us\": " << pct(lat, 0.50) << ",\n";
    out << "    \"latency_p95_us\": " << pct(lat, 0.95) << ",\n";
    out << "    \"latency_p99_us\": " << pct(lat, 0.99) << ",\n";
    out << "    \"query_count\": " << stats.size() << ",\n";
    out << "    \"index_size_mb\": " << (static_cast<double>(index_bytes) / (1024.0 * 1024.0)) << ",\n";
    out << "    \"resident_bytes\": " << resident_bytes << ",\n";
    out << "    \"peak_rss_bytes\": " << qgraph05::measured_peak_rss() << ",\n";
    out << "    \"io_requests_per_query\": " << (static_cast<double>(requests) / n) << ",\n";
    out << "    \"sectors_4k_per_query\": " << (static_cast<double>(bytes / kPage) / n) << ",\n";
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
            fs::file_size(index_root / "node_rows.pages") +
            fs::file_size(index_root / "rotator.bin") +
            fs::file_size(index_root / "index.meta");
    const std::uint64_t resident_bytes = meta.padded_dim * sizeof(float) + 4096;
    std::ofstream out(result_path);
    out << "{\n";
    out << "  \"schema_version\": 2,\n";
    out << "  \"status\": \"done\",\n";
    out << "  \"layer\": \"05c\",\n";
    out << "  \"dataset\": " << json_string(args.require("dataset")) << ",\n";
    out << "  \"method\": \"SymphonyQG-DiskPort\",\n";
    out << "  \"source_suite\": \"" << kSourceSuite << "\",\n";
    out << "  \"source_kernel\": \"" << kSourceKernel << "\",\n";
    out << "  \"port_kind\": \"" << kPortKind << "\",\n";
    out << "  \"storage_mode\": \"hybrid_disk\",\n";
    out << "  \"cache_mode\": " << json_string(args.require("cache-mode")) << ",\n";
    out << "  \"phase\": \"export\",\n";
    out << "  \"build_distance_computations\": " << meta.build_distance_computations << ",\n";
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
    out << "  \"resident_bytes\": " << resident_bytes << ",\n";
    out << "  \"codebook_bytes\": 0,\n";
    out << "  \"index_size_bytes\": " << index_bytes << ",\n";
    out << "  \"summary_rows\": []\n";
    out << "}\n";
}

void run_search_single(const Args& args) {
    fs::path root(args.require("disk-index-dir"));
    export_index(args);
    Meta meta = read_meta(root / "index.meta");
    Matrix queries = read_fvecs(args.require("query"));
    auto gt = read_ivecs(args.require("groundtruth"));
    auto order = read_query_order(args.require("query-order"), queries.rows);
    if (queries.rows != gt.size()) throw std::runtime_error("query/gt size mismatch");

    symqg::FHTRotator rotator(meta.dim);
    {
        std::ifstream input(root / "rotator.bin", std::ios::binary);
        rotator.load(input);
    }
    const int ef = args.values.count("integration-widths") ? static_cast<int>(args.number("integration-widths")) : 100;
    const int workers = std::max<int>(1, static_cast<int>(args.number("workers")));
    qgraph05::check_query_cache_budget(workers, std::stod(args.require("search-dram-budget-gib")));
    std::vector<QueryStats> stats(queries.rows);
    std::vector<std::vector<int32_t>> top10(queries.rows);
    fs::create_directories(fs::path(args.require("query-trace")).parent_path());
    auto run_query = [&](std::size_t qid, bool record) {
        QueryStats local;
        const auto t0 = Clock::now();
        symqg::QGScanner local_scanner(meta.padded_dim, meta.degree);
        symqg::QGQuery q_obj(queries.data.data() + qid * queries.dim, meta.padded_dim);
        q_obj.query_prepare(rotator, local_scanner);
        const auto t1 = Clock::now();
        RowReader reader(root / "node_rows.pages", meta, &local);
        auto ids = search_one(
                reader,
                meta,
                q_obj,
                local_scanner,
                queries.data.data() + qid * queries.dim,
                ef,
                local);
        const auto t2 = Clock::now();
        local.recall = recall_at_10(ids, gt[qid]);
        const auto t3 = Clock::now();
        local.prep_us = std::chrono::duration<double, std::micro>(t1 - t0).count();
        local.traverse_us = std::chrono::duration<double, std::micro>(t2 - t1).count();
        local.sort_us = std::chrono::duration<double, std::micro>(t3 - t2).count();
        local.latency_us = std::chrono::duration<double, std::micro>(t3 - t0).count();
        local.peak_rss_bytes=qgraph05::measured_peak_rss();
        if (record) {
            stats[qid] = local;
            top10[qid] = std::move(ids);
        }
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
              << ",\"method\":\"SymphonyQG-DiskPort\",\"config_id\":\"QG_R64_EF400_t3\","
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
        for (std::size_t i = 0; i < top10[qid].size(); ++i) {
            if (i) trace << ",";
            trace << top10[qid][i];
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
        std::cerr << "[SymphonyQG search] start width=" << width << " workers="
                  << args.require("workers") << "\n";
        Args one = args;
        one.values["integration-widths"] = std::to_string(width);
        fs::path tmp_json = final_json;
        tmp_json += ".w" + std::to_string(width) + ".tmp";
        fs::path tmp_trace = final_trace;
        tmp_trace += ".w" + std::to_string(width) + ".tmp";
        one.values["result-json"] = tmp_json.string();
        one.values["query-trace"] = tmp_trace.string();
        run_search_single(one);
        std::cerr << "[SymphonyQG search] completed width=" << width << "\n";
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
            throw std::runtime_error("unsupported method/layer for SymphonyQG port");
        }
        if (args.require("storage-mode") != "hybrid_disk" ||
            args.require("direct-io") != "required" ||
            args.require("native-aio") != "required") {
            throw std::runtime_error("SymphonyQG 05C requires hybrid_disk + O_DIRECT + native AIO");
        }
        const std::string phase = args.require("phase");
        if (phase == "export") {
            export_index(args);
            std::ofstream(args.require("query-trace")).close();
            write_export_artifact(
                    args,
                    read_meta(fs::path(args.require("disk-index-dir")) / "index.meta"),
                    args.require("result-json"),
                    args.require("disk-index-dir"));
            std::cout << "SymphonyQG-DiskPort export done\n";
        } else {
            run_search(args);
            std::cout << "SymphonyQG-DiskPort search done\n";
        }
        return 0;
    } catch (const std::exception& exc) {
        std::cerr << "ERROR: " << exc.what() << "\n";
        return 2;
    }
}
