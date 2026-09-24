#include "direct_io.hpp"
#include "query_page_cache.hpp"
#include "svs/index/vamana/search_buffer.h"

#include <openssl/evp.h>
#include <omp.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <sstream>
#include <span>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

namespace {

constexpr std::size_t kPage = qgraph05::kPageSize;
constexpr std::size_t kSvsHeader = 1024;
constexpr int kTopK = 10;
constexpr const char* kMethod = "OG-LVQ-DiskPort";
constexpr const char* kLayer = "05c";
constexpr const char* kSourceSuite = "03_system_fair";
constexpr const char* kSourceKernel = "local LVQ4 decoder; official distance parity pending";
constexpr const char* kPortKind = "experimental_disk_port_pending_official_parity";

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

std::string shell_quote(const std::string& value) {
    std::string out = "'";
    for (char c : value) {
        if (c == '\'') out += "'\\''";
        else out += c;
    }
    out += "'";
    return out;
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
    Matrix m{size / row_size, static_cast<std::size_t>(dim), std::vector<float>((size / row_size) * dim)};
    in.seekg(0);
    for (std::size_t i = 0; i < m.rows; ++i) {
        int32_t cur = 0;
        in.read(reinterpret_cast<char*>(&cur), 4);
        if (cur != dim) throw std::runtime_error("mixed fvecs dimensions");
        in.read(reinterpret_cast<char*>(m.data.data() + i * m.dim), dim * 4);
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
        rows.emplace_back(width);
        in.read(reinterpret_cast<char*>(rows.back().data()), width * 4);
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
    return order;
}

std::string read_text(const fs::path& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot read " + path.string());
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

std::string toml_string(const std::string& text, const std::string& key) {
    const std::string needle = key + " = '";
    auto pos = text.find(needle);
    if (pos == std::string::npos) throw std::runtime_error("missing toml key " + key);
    pos += needle.size();
    auto end = text.find('\'', pos);
    return text.substr(pos, end - pos);
}

std::uint64_t toml_u64(const std::string& text, const std::string& key) {
    const std::string needle = key + " = ";
    auto pos = text.find(needle);
    if (pos == std::string::npos) throw std::runtime_error("missing toml key " + key);
    pos += needle.size();
    auto end = text.find_first_of("\r\n", pos);
    return std::stoull(text.substr(pos, end - pos));
}

double json_double_or(const std::string& text, const std::string& key, double fallback) {
    const std::string needle = "\"" + key + "\":";
    auto pos = text.find(needle);
    if (pos == std::string::npos) return fallback;
    pos += needle.size();
    while (pos < text.size() && std::isspace(static_cast<unsigned char>(text[pos]))) ++pos;
    auto end = text.find_first_of(",}\r\n", pos);
    if (end == std::string::npos) return fallback;
    try {
        return std::stod(text.substr(pos, end - pos));
    } catch (...) {
        return fallback;
    }
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
        throw std::runtime_error("OG-LVQ disk port requires row records <= 4 KiB");
    }
    return Layout{record_bytes, kPage / record_bytes, rows};
}

struct Meta {
    std::size_t n = 0;
    std::size_t dim = 0;
    std::size_t degree = 0;
    std::size_t graph_record_bytes = 0;
    std::size_t graph_records_per_page = 0;
    std::size_t lvq_record_bytes = 0;
    std::size_t lvq_records_per_page = 0;
    std::size_t centroid_count = 0;
    std::uint32_t entry_point = 0;
    double build_time_ms = 0.0;
};

void write_meta(const fs::path& path, const Meta& meta) {
    std::ofstream out(path);
    out << "n=" << meta.n << "\n";
    out << "dim=" << meta.dim << "\n";
    out << "degree=" << meta.degree << "\n";
    out << "graph_record_bytes=" << meta.graph_record_bytes << "\n";
    out << "graph_records_per_page=" << meta.graph_records_per_page << "\n";
    out << "lvq_record_bytes=" << meta.lvq_record_bytes << "\n";
    out << "lvq_records_per_page=" << meta.lvq_records_per_page << "\n";
    out << "centroid_count=" << meta.centroid_count << "\n";
    out << "entry_point=" << meta.entry_point << "\n";
    out << "build_time_ms=" << std::setprecision(9) << meta.build_time_ms << "\n";
}

Meta read_meta(const fs::path& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("missing metadata " + path.string());
    std::unordered_map<std::string, std::string> kv;
    std::string line;
    while (std::getline(in, line)) {
        const auto pos = line.find('=');
        if (pos != std::string::npos) kv[line.substr(0, pos)] = line.substr(pos + 1);
    }
    Meta m;
    m.n = std::stoull(kv.at("n"));
    m.dim = std::stoull(kv.at("dim"));
    m.degree = std::stoull(kv.at("degree"));
    m.graph_record_bytes = std::stoull(kv.at("graph_record_bytes"));
    m.graph_records_per_page = std::stoull(kv.at("graph_records_per_page"));
    m.lvq_record_bytes = std::stoull(kv.at("lvq_record_bytes"));
    m.lvq_records_per_page = std::stoull(kv.at("lvq_records_per_page"));
    m.centroid_count = std::stoull(kv.at("centroid_count"));
    m.entry_point = static_cast<std::uint32_t>(std::stoul(kv.at("entry_point")));
    m.build_time_ms = std::stod(kv.at("build_time_ms"));
    return m;
}

void copy_svs_rows_to_pages(
        const fs::path& source,
        const fs::path& target,
        const Layout& layout) {
    std::ifstream in(source, std::ios::binary);
    if (!in) throw std::runtime_error("cannot read " + source.string());
    in.seekg(static_cast<std::streamoff>(kSvsHeader));
    std::vector<char> pages(layout.pages() * kPage, 0);
    std::vector<char> row(layout.record_bytes);
    for (std::size_t id = 0; id < layout.rows; ++id) {
        in.read(row.data(), static_cast<std::streamsize>(row.size()));
        if (!in) throw std::runtime_error("truncated " + source.string());
        std::memcpy(
                pages.data() + layout.page(id) * kPage + layout.offset(id),
                row.data(),
                row.size());
    }
    write_padded_pages(target, pages);
}

void export_index(const Args& args) {
    fs::path root(args.require("disk-index-dir"));
    fs::create_directories(root);
    if (fs::exists(root / "index.meta") &&
        fs::exists(root / "graph.pages") &&
        fs::exists(root / "lvq4.pages") &&
        fs::exists(root / "centroids.f32")) {
        std::cerr << "[OG-LVQ export] reuse existing disk pages: " << root << "\n";
        return;
    }
    const std::string dataset = args.require("dataset");
    const fs::path base_path = fs::path(args.require("data-root")) / dataset / (dataset + "_base.fvecs");
    std::cerr << "[OG-LVQ export] loading base: " << base_path << "\n";
    Matrix base = read_fvecs(base_path);
    const auto started = Clock::now();
    const fs::path source_official =
            fs::path(args.require("source-results-root")) / "03_system_fair" / dataset /
            "indexes" / "OG-LVQ" / "LVQ4_R64_W400";
    fs::path official = source_official;
    double build_ms = 0.0;
    if (fs::exists(official / "config/svs_config.toml") &&
        fs::exists(official / "graph/svs_config.toml") &&
        fs::exists(official / "data/svs_config.toml")) {
        std::cerr << "[OG-LVQ export] reuse 03_system_fair official SVS index: "
                  << official << "\n";
        const fs::path build_json =
                fs::path(args.require("source-results-root")) / "03_system_fair" / dataset /
                "indexes" / "OG-LVQ" / "OG-LVQ_LVQ4_R64_W400_build.json";
        if (fs::exists(build_json)) {
            build_ms = json_double_or(read_text(build_json), "build_time_ms", 0.0);
        }
    } else {
        official = root / "official_svs";
        fs::create_directories(official);
        std::ostringstream cmd;
        cmd << "python3 experiments/03_system_fair/adapters/_scripts/svs_run.py"
            << " --mode build"
            << " --base " << shell_quote(base_path.string())
            << " --out " << shell_quote(official.string())
            << " --R 64 --W 400 --primary 4 --residual 0 --alpha 1.2 --threads 3";
        std::cerr << "[OG-LVQ export] building official SVS index\n";
        int rc = std::system(cmd.str().c_str());
        if (rc != 0) throw std::runtime_error("official SVS OG-LVQ build failed");
        build_ms = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
    }

    const std::string graph_cfg = read_text(official / "graph/svs_config.toml");
    const std::string data_cfg = read_text(official / "data/svs_config.toml");
    const std::string config_cfg = read_text(official / "config/svs_config.toml");
    Meta meta;
    meta.n = static_cast<std::size_t>(toml_u64(graph_cfg, "num_vertices"));
    meta.dim = static_cast<std::size_t>(toml_u64(data_cfg, "logical_dimensions"));
    meta.degree = static_cast<std::size_t>(toml_u64(graph_cfg, "max_degree"));
    meta.entry_point = static_cast<uint32_t>(toml_u64(config_cfg, "entry_point"));
    meta.centroid_count = static_cast<std::size_t>(toml_u64(data_cfg, "num_vectors"));
    if (meta.n != base.rows || meta.dim != base.dim) {
        throw std::runtime_error("SVS index metadata does not match base vectors");
    }
    const fs::path graph_bin = official / "graph" / toml_string(graph_cfg, "binary_file");
    fs::path lvq_bin = official / "data/lvq_data_0.svs";
    if (!fs::exists(lvq_bin)) {
        lvq_bin = official / "data" / toml_string(data_cfg, "binary_file");
    }
    const fs::path centroid_bin = official / "data" / "data_1.svs";
    meta.graph_record_bytes = (meta.degree + 1) * sizeof(uint32_t);
    meta.lvq_record_bytes = (fs::file_size(lvq_bin) - kSvsHeader) / meta.n;
    auto graph_layout = make_layout(meta.n, meta.graph_record_bytes);
    auto lvq_layout = make_layout(meta.n, meta.lvq_record_bytes);
    meta.graph_records_per_page = graph_layout.records_per_page;
    meta.lvq_records_per_page = lvq_layout.records_per_page;
    meta.build_time_ms = build_ms;
    std::cerr << "[OG-LVQ export] converting graph rows to pages\n";
    copy_svs_rows_to_pages(graph_bin, root / "graph.pages", graph_layout);
    std::cerr << "[OG-LVQ export] converting LVQ rows to pages\n";
    copy_svs_rows_to_pages(lvq_bin, root / "lvq4.pages", lvq_layout);
    {
        std::ifstream in(centroid_bin, std::ios::binary);
        if (!in) throw std::runtime_error("cannot read SVS centroids");
        in.seekg(static_cast<std::streamoff>(kSvsHeader));
        std::vector<float> centroids(meta.centroid_count * meta.dim);
        in.read(reinterpret_cast<char*>(centroids.data()), static_cast<std::streamsize>(centroids.size() * sizeof(float)));
        std::ofstream out(root / "centroids.f32", std::ios::binary);
        const uint64_t c = meta.centroid_count, d = meta.dim;
        out.write(reinterpret_cast<const char*>(&c), sizeof(c));
        out.write(reinterpret_cast<const char*>(&d), sizeof(d));
        out.write(reinterpret_cast<const char*>(centroids.data()), static_cast<std::streamsize>(centroids.size() * sizeof(float)));
    }
    write_meta(root / "index.meta", meta);
    std::cerr << "[OG-LVQ export] wrote disk index: " << root << "\n";
}

struct QueryStats {
    std::uint64_t query_cache_hits=0, query_cache_misses=0, query_cache_evictions=0;
    std::uint64_t query_cache_allocated_bytes=0, peak_rss_bytes=0;
    double latency_us = 0.0, prep_us = 0.0, traverse_us = 0.0, recall = 0.0, io_wait_us = 0.0;
    std::uint64_t bytes_read = 0, io_requests = 0, coalesced = 0, duplicates = 0;
    std::uint64_t visited_nodes = 0, distance_calls = 0;
};

struct DiskRows {
    qgraph05::DirectAioReader graph_reader;
    qgraph05::DirectAioReader lvq_reader;
    Meta meta;
    QueryStats* stats;
    qgraph05::QueryPageCache cache;
    std::vector<char> graph_cache, lvq_cache;
    DiskRows(const fs::path& root, Meta m, QueryStats* s)
            : graph_reader(root / "graph.pages"), lvq_reader(root / "lvq4.pages"),
              meta(std::move(m)), stats(s) {}
    const char* read_row(
            qgraph05::DirectAioReader& reader,
            std::vector<char>& current,
            std::uint32_t id,
            std::size_t record_bytes,
            std::size_t records_per_page) {
        qgraph05::cached_pages(reader, cache, &reader == &graph_reader ? 0 : 1,
                              id / records_per_page, 1, current, *stats);
        return current.data() + (id % records_per_page) * record_bytes;
    }
    std::span<const uint32_t> graph(std::uint32_t id) {
        const char* row = read_row(graph_reader, graph_cache, id, meta.graph_record_bytes, meta.graph_records_per_page);
        return {reinterpret_cast<const uint32_t*>(row), meta.degree + 1};
    }
    const unsigned char* lvq(std::uint32_t id) {
        return reinterpret_cast<const unsigned char*>(
                read_row(lvq_reader, lvq_cache, id, meta.lvq_record_bytes, meta.lvq_records_per_page));
    }
};

struct Centroids {
    std::size_t count = 0, dim = 0;
    std::vector<float> data;
};

Centroids read_centroids(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    uint64_t c = 0, d = 0;
    in.read(reinterpret_cast<char*>(&c), sizeof(c));
    in.read(reinterpret_cast<char*>(&d), sizeof(d));
    Centroids out{static_cast<std::size_t>(c), static_cast<std::size_t>(d), std::vector<float>(c * d)};
    in.read(reinterpret_cast<char*>(out.data.data()), static_cast<std::streamsize>(out.data.size() * sizeof(float)));
    return out;
}

std::size_t turbo_linear(std::size_t logical) {
    constexpr std::size_t lanes = 16, epl = 8;
    const std::size_t block = logical / (lanes * epl);
    const std::size_t rem = logical % (lanes * epl);
    const std::size_t lane = rem % lanes;
    const std::size_t within = rem / lanes;
    return block * (lanes * epl) + lane * epl + within;
}

uint8_t get_4bit_turbo(const unsigned char* packed, std::size_t logical) {
    const std::size_t linear = turbo_linear(logical);
    const uint8_t byte = packed[linear / 2];
    return (linear & 1) ? static_cast<uint8_t>(byte >> 4) : static_cast<uint8_t>(byte & 0x0f);
}

float lvq_distance(const unsigned char* row, const Meta& meta, const Centroids& centroids, const float* query) {
    const std::size_t code_bytes = meta.lvq_record_bytes - (2 * sizeof(float) + sizeof(uint8_t));
    float scale = 0.0f, bias = 0.0f;
    std::memcpy(&scale, row + code_bytes, sizeof(float));
    std::memcpy(&bias, row + code_bytes + sizeof(float), sizeof(float));
    const uint8_t selector = row[code_bytes + 2 * sizeof(float)];
    const float* centroid = centroids.data.data() + std::min<std::size_t>(selector, centroids.count - 1) * meta.dim;
    float acc = 0.0f;
    for (std::size_t j = 0; j < meta.dim; ++j) {
        const float decoded = scale * static_cast<float>(get_4bit_turbo(row, j)) + bias + centroid[j];
        const float diff = query[j] - decoded;
        acc += diff * diff;
    }
    return acc;
}

std::vector<int32_t> search_one(DiskRows& rows, const Centroids& centroids, const float* query,
                                int ef, QueryStats& stats) {
    // Match the official default (visited filter disabled) and top-k capacity adjustment.
    svs::index::vamana::SearchBuffer<std::uint32_t, std::less<>> pool(
        static_cast<std::size_t>(std::max(ef, kTopK)), std::less<>{}, false);
    pool.push_back(svs::SearchNeighbor<std::uint32_t>{rows.meta.entry_point,
        lvq_distance(rows.lvq(rows.meta.entry_point), rows.meta, centroids, query)});
    pool.sort();
    ++stats.distance_calls;
    while (!pool.done()) {
        const std::uint32_t u = pool.next().id();
        ++stats.visited_nodes;
        auto grow = rows.graph(u);
        const std::size_t degree = std::min<std::size_t>(grow.front(), rows.meta.degree);
        for (std::size_t i = 0; i < degree; ++i) {
            const auto v = grow[i + 1];
            if (v >= rows.meta.n) throw std::runtime_error("invalid graph neighbor");
            if (pool.emplace_visited(v)) continue;
            const float d = lvq_distance(rows.lvq(v), rows.meta, centroids, query);
            ++stats.distance_calls;
            pool.insert(svs::SearchNeighbor<std::uint32_t>{v, d});
        }
    }
    std::vector<int32_t> result;
    for (std::size_t i = 0; i < pool.size() && i < kTopK; ++i) result.push_back(static_cast<int32_t>(pool[i].id()));
    while (result.size() < kTopK) result.push_back(-1);
    return result;
}

double recall_at_10(const std::vector<int32_t>& ids, const std::vector<int32_t>& truth) {
    std::unordered_set<int32_t> wanted;
    for (std::size_t i = 0; i < truth.size() && i < kTopK; ++i) wanted.insert(truth[i]);
    int hits = 0;
    for (int32_t id : ids) if (wanted.count(id)) ++hits;
    return static_cast<double>(hits) / kTopK;
}

double pct(std::vector<double> values, double p) {
    std::sort(values.begin(), values.end());
    const double pos = (values.size() - 1) * p;
    const auto lo = static_cast<std::size_t>(std::floor(pos));
    const auto hi = static_cast<std::size_t>(std::ceil(pos));
    return values[lo] * (1.0 - (pos - lo)) + values[hi] * (pos - lo);
}

void write_artifact(const Args& args, const Meta& meta, const fs::path& result_path,
                    const fs::path& trace_path, const fs::path& index_root,
                    const std::vector<QueryStats>& stats, int ef, double wall_seconds,
                    bool export_only = false) {
    const std::uint64_t index_bytes = fs::file_size(index_root / "graph.pages") +
            fs::file_size(index_root / "lvq4.pages") + fs::file_size(index_root / "centroids.f32") +
            fs::file_size(index_root / "index.meta");
    const std::uint64_t resident_bytes = fs::file_size(index_root / "centroids.f32") + 4096;
    std::ofstream out(result_path);
    out << "{\n";
    out << "  \"schema_version\": 2,\n";
    out << "  \"status\": \"done\",\n";
    out << "  \"layer\": \"05c\",\n";
    out << "  \"dataset\": " << json_string(args.require("dataset")) << ",\n";
    out << "  \"method\": \"OG-LVQ-DiskPort\",\n";
    out << "  \"source_suite\": \"" << kSourceSuite << "\",\n";
    out << "  \"source_kernel\": \"" << kSourceKernel << "\",\n";
    out << "  \"port_kind\": \"" << kPortKind << "\",\n";
    out << "  \"storage_mode\": \"hybrid_disk\",\n";
    out << "  \"cache_mode\": " << json_string(args.require("cache-mode")) << ",\n";
    out << "  \"phase\": " << json_string(export_only ? "export" : args.require("phase")) << ",\n";
    out << "  \"run_id\": " << json_string(args.require("run-id")) << ",\n";
    out << "  \"repeat_id\": " << args.require("repeat-id") << ",\n";
    out << "  \"workers\": " << args.require("workers") << ",\n";
    out << "  \"warmup_queries\": " << args.require("warmup-queries") << ",\n";
    out << "  \"query_split_sha256\": " << json_string(args.require("query-split-sha256")) << ",\n";
    out << "  \"query_order_sha256\": " << json_string(args.require("query-order-sha256")) << ",\n";
    out << "  \"query_order_seed\": " << args.require("query-order-seed") << ",\n";
    out << "  \"index_path\": " << json_string(fs::absolute(index_root).string()) << ",\n";
    out << "  \"formal_ready\": " << (export_only ? "true" : "false") << ",\n";
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
    out << "  \"simd\": \"svs_lvq4_turbo16x8_layout_compatible\",\n";
    out << "  \"base_count\": " << meta.n << ",\n";
    out << "  \"dimension\": " << meta.dim << ",\n";
    out << "  \"search_dram_budget_gib\": " << args.require("search-dram-budget-gib") << ",\n";
    out << "  \"resident_bytes\": " << resident_bytes << ",\n";
    out << "  \"codebook_bytes\": " << fs::file_size(index_root / "centroids.f32") << ",\n";
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
    out << "  \"query_trace_sha256\": " << json_string(fs::exists(trace_path) ? sha256(trace_path) : "") << ",\n";
    if (export_only) {
        out << "  \"index_size_bytes\": " << index_bytes << ",\n";
        out << "  \"summary_rows\": []\n";
    } else {
        std::vector<double> lat;
        double recall = 0.0, prep = 0.0, traverse = 0.0, io_wait = 0.0;
        std::uint64_t bytes = 0, requests = 0, visited = 0, dist = 0;
        for (const auto& s : stats) {
            lat.push_back(s.latency_us); recall += s.recall; prep += s.prep_us;
            traverse += s.traverse_us; io_wait += s.io_wait_us; bytes += s.bytes_read; requests += s.io_requests;
            visited += s.visited_nodes; dist += s.distance_calls;
        }
        const double n = static_cast<double>(stats.size());
        out << "  \"summary_rows\": [{\n";
        out << "    \"config_id\": \"LVQ4_R64_W400\",\n";
        out << "    \"search_param\": \"W=" << ef << "\",\n";
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
        out << "    \"rerank_us\": 0,\n";
        out << "    \"visited_nodes\": " << (static_cast<double>(visited) / n) << ",\n";
        out << "    \"distance_evaluations\": " << (static_cast<double>(dist) / n) << "\n";
        out << "  }]\n";
    }
    out << "}\n";
}

void run_search_single(const Args& args) {
    fs::path root(args.require("disk-index-dir"));
    if (!fs::exists(root / "index.meta")) export_index(args);
    Meta meta = read_meta(root / "index.meta");
    Matrix queries = read_fvecs(args.require("query"));
    auto gt = read_ivecs(args.require("groundtruth"));
    auto order = read_query_order(args.require("query-order"), queries.rows);
    auto centroids = read_centroids(root / "centroids.f32");
    const int ef = args.values.count("integration-widths") ? static_cast<int>(args.number("integration-widths")) : 100;
    const int workers = std::max<int>(1, static_cast<int>(args.number("workers")));
    qgraph05::check_query_cache_budget(workers, std::stod(args.require("search-dram-budget-gib")));
    std::vector<QueryStats> stats(queries.rows);
    std::vector<std::vector<int32_t>> top10(queries.rows);
    fs::create_directories(fs::path(args.require("query-trace")).parent_path());
    auto run_query = [&](std::size_t qid, bool record) {
        QueryStats local;
        const auto t0 = Clock::now();
        const float* query = queries.data.data() + qid * queries.dim;
        const auto t1 = Clock::now();
        DiskRows rows(root, meta, &local);
        auto ids = search_one(rows, centroids, query, ef, local);
        const auto t2 = Clock::now();
        local.recall = recall_at_10(ids, gt[qid]);
        local.prep_us = std::chrono::duration<double, std::micro>(t1 - t0).count();
        local.traverse_us = std::chrono::duration<double, std::micro>(t2 - t1).count();
        local.latency_us = std::chrono::duration<double, std::micro>(t2 - t0).count();
        local.peak_rss_bytes=qgraph05::measured_peak_rss();
        if (record) stats[qid] = local;
        if (record) top10[qid] = std::move(ids);
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
        trace << "{\"layer\":\"05c\",\"storage_mode\":\"hybrid_disk\",\"cache_mode\":"
              << json_string(args.require("cache-mode"))
              << ",\"dataset\":" << json_string(args.require("dataset"))
              << ",\"method\":\"OG-LVQ-DiskPort\",\"config_id\":\"LVQ4_R64_W400\","
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
              << ",\"rerank_us\":0,\"visited_nodes\":" << s.visited_nodes
              << ",\"distance_evaluations\":" << s.distance_calls
              << ",\"io_requests\":" << s.io_requests
              << ",\"sectors_4k\":" << (s.bytes_read / kPage)
              << ",\"bytes_read\":" << s.bytes_read
              << ",\"average_read_bytes\":" << (s.io_requests ? s.bytes_read / s.io_requests : 0)
              << ",\"coalesced_requests\":" << s.coalesced
              << ",\"duplicate_pages_removed\":" << s.duplicates
              << ",\"shared_cache_hits\":0,\"shared_cache_misses\":0,"
              << "\"query_cache_hits\":" << s.query_cache_hits << ",\"query_cache_misses\":" << s.query_cache_misses << ","
              << "\"result_ids\":[";
        for (std::size_t i = 0; i < top10[qid].size(); ++i) {
            if (i) trace << ",";
            trace << top10[qid][i];
        }
        trace << "]}\n";
    }
    trace.close();
    write_artifact(args, meta, args.require("result-json"), args.require("query-trace"), root, stats, ef, wall_seconds);
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
        if (args.require("contract-version") != "2") throw std::runtime_error("only contract version 2 is supported");
        if (args.require("method") != kMethod || args.require("layer") != kLayer) {
            throw std::runtime_error("unsupported method/layer for OG-LVQ port");
        }
        if (args.require("storage-mode") != "hybrid_disk" ||
            args.require("direct-io") != "required" ||
            args.require("native-aio") != "required") {
            throw std::runtime_error("OG-LVQ 05C requires hybrid_disk + O_DIRECT + native AIO");
        }
        const std::string phase = args.require("phase");
        if (phase == "export") {
            export_index(args);
            std::ofstream(args.require("query-trace")).close();
            Meta meta = read_meta(fs::path(args.require("disk-index-dir")) / "index.meta");
            write_artifact(args, meta, args.require("result-json"), args.require("query-trace"),
                           args.require("disk-index-dir"), {}, 0, 0.0, true);
            std::cout << "OG-LVQ-DiskPort export done\n";
        } else {
            run_search(args);
            std::cout << "OG-LVQ-DiskPort search done\n";
        }
        return 0;
    } catch (const std::exception& exc) {
        std::cerr << "ERROR: " << exc.what() << "\n";
        return 2;
    }
}
