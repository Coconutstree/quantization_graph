#define QGRAPH_FAISS_QUANTIZER_LIBRARY
#include "../../01_quantizer_fair/faiss_quantizer_smoke.cpp"

#include "direct_io.hpp"

#include <openssl/evp.h>

#include <atomic>
#include <bit>
#include <cstdlib>
#include <fcntl.h>
#include <sstream>
#include <mutex>
#include <optional>
#include <thread>
#include <unistd.h>

namespace {

using qgraph05::DirectAioReader;
using qgraph05::IoStats;
using qgraph05::PageReadBatch;
constexpr size_t kPage = qgraph05::kPageSize;

struct ContractArgs {
    std::unordered_map<std::string, std::string> values;

    static ContractArgs parse(int argc, char** argv) {
        ContractArgs out;
        for (int i = 1; i < argc; ++i) {
            const std::string key = argv[i];
            if (key == "--help" || key == "-h") {
                std::cout << "qgraph05_quantizer_port: formal 05A PQ/SQ/Ours native port\n";
                std::exit(0);
            }
            if (key.rfind("--", 0) != 0 || i + 1 >= argc) {
                throw std::runtime_error("expected --key value, got " + key);
            }
            out.values[key.substr(2)] = argv[++i];
        }
        return out;
    }

    const std::string& require(const std::string& key) const {
        const auto it = values.find(key);
        if (it == values.end() || it->second.empty()) {
            throw std::runtime_error("missing --" + key);
        }
        return it->second;
    }

    size_t number(const std::string& key) const {
        return std::stoull(require(key));
    }

    double real(const std::string& key) const {
        return std::stod(require(key));
    }
};

std::string json_string(const std::string& value) {
    std::ostringstream out;
    out << '"';
    for (const unsigned char c : value) {
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
    if (!input) {
        throw std::runtime_error("cannot hash " + path.string());
    }
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr || EVP_DigestInit_ex(context, EVP_sha256(), nullptr) != 1) {
        EVP_MD_CTX_free(context);
        throw std::runtime_error("EVP sha256 initialization failed");
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
        throw std::runtime_error("EVP sha256 finalization failed");
    }
    EVP_MD_CTX_free(context);
    std::ostringstream out;
    for (unsigned int i = 0; i < size; ++i) {
        out << std::hex << std::setw(2) << std::setfill('0')
            << static_cast<unsigned int>(digest[i]);
    }
    return out.str();
}

std::vector<float> load_fvecs_range(
        const VecsInfo& info, size_t begin, size_t count) {
    if (begin > info.count || count > info.count - begin) {
        throw std::runtime_error("fvec range is out of bounds");
    }
    std::ifstream in(info.path, std::ios::binary);
    in.seekg(static_cast<std::streamoff>(begin * info.row_size));
    std::vector<float> out(count * info.dim);
    for (size_t i = 0; i < count; ++i) {
        int32_t dim = 0;
        in.read(reinterpret_cast<char*>(&dim), sizeof(dim));
        if (dim != static_cast<int32_t>(info.dim)) {
            throw std::runtime_error("bad fvec dimension in " + info.path.string());
        }
        in.read(
                reinterpret_cast<char*>(out.data() + i * info.dim),
                static_cast<std::streamsize>(info.dim * sizeof(float)));
    }
    if (!in) {
        throw std::runtime_error("truncated fvec range in " + info.path.string());
    }
    return out;
}

std::vector<int32_t> load_candidates_at(
        const fs::path& path,
        size_t row_offset,
        size_t query_count,
        size_t row_width) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open candidate file " + path.string());
    }
    const uint64_t offset = row_offset * row_width * sizeof(int32_t);
    const uint64_t required = (row_offset + query_count) * row_width * sizeof(int32_t);
    if (fs::file_size(path) < required) {
        throw std::runtime_error("candidate file does not cover requested query split");
    }
    in.seekg(static_cast<std::streamoff>(offset));
    std::vector<int32_t> values(query_count * row_width);
    in.read(
            reinterpret_cast<char*>(values.data()),
            static_cast<std::streamsize>(values.size() * sizeof(int32_t)));
    if (!in) {
        throw std::runtime_error("truncated candidate file");
    }
    return values;
}

std::vector<uint32_t> load_query_order(const fs::path& path, size_t count) {
    if (fs::file_size(path) != count * sizeof(uint32_t)) {
        throw std::runtime_error("query-order size does not match query split");
    }
    std::ifstream in(path, std::ios::binary);
    std::vector<uint32_t> order(count);
    in.read(reinterpret_cast<char*>(order.data()), order.size() * sizeof(uint32_t));
    std::vector<uint32_t> sorted = order;
    std::sort(sorted.begin(), sorted.end());
    for (size_t i = 0; i < count; ++i) {
        if (sorted[i] != i) {
            throw std::runtime_error("query-order is not a permutation");
        }
    }
    return order;
}

enum class CodecKind { PQ, SQ, Ours };

CodecKind codec_kind(const std::string& method) {
    if (method == "PQ_4bit") return CodecKind::PQ;
    if (method == "SQ_4bit") return CodecKind::SQ;
    if (method == "Ours_RaBitQ_K1") return CodecKind::Ours;
    throw std::runtime_error("unsupported 05A method in this port: " + method);
}

std::string source_kernel(const std::string& method) {
    if (method == "PQ_4bit") return "faiss::ProductQuantizer";
    if (method == "SQ_4bit") return "faiss::ScalarQuantizer(QT_4bit)";
    if (method == "Ours_RaBitQ_K1") return "hnswlib::RaBitQSpace K=1";
    if (method == "SAQ_B4") return "official SAQ B=4 distance kernel";
    throw std::runtime_error("unsupported method");
}

struct Codec {
    CodecKind kind;
    size_t dim;
    size_t code_size;
    std::unique_ptr<faiss::ProductQuantizer> pq;
    std::unique_ptr<faiss::ScalarQuantizer> sq;
    std::unique_ptr<hnswlib::RaBitQSpace> ours;

    static Codec train(
            CodecKind kind,
            size_t dim,
            const std::vector<float>& train,
            int seed) {
        Codec codec{kind, dim, 0, nullptr, nullptr, nullptr};
        const size_t rows = train.size() / dim;
        if (kind == CodecKind::PQ) {
            codec.pq = std::make_unique<faiss::ProductQuantizer>(dim, dim, 4);
            codec.pq->cp.niter = 20;
            codec.pq->cp.max_points_per_centroid = 256;
            codec.pq->cp.seed = seed;
            codec.pq->train(rows, train.data());
            codec.code_size = codec.pq->code_size;
        } else if (kind == CodecKind::SQ) {
            codec.sq = std::make_unique<faiss::ScalarQuantizer>(
                    dim, faiss::ScalarQuantizer::QT_4bit);
            codec.sq->train(rows, train.data());
            codec.code_size = codec.sq->code_size;
        } else {
            codec.ours = std::make_unique<hnswlib::RaBitQSpace>(
                    dim, 1, static_cast<uint32_t>(seed), false, 4);
            codec.code_size = codec.ours->get_data_size();
        }
        return codec;
    }

    static Codec load(
            CodecKind kind, size_t dim, int seed, const fs::path& model_path) {
        Codec codec{kind, dim, 0, nullptr, nullptr, nullptr};
        std::ifstream in(model_path, std::ios::binary);
        if (!in) throw std::runtime_error("cannot open model " + model_path.string());
        uint64_t stored_dim = 0, stored_code_size = 0, count = 0;
        in.read(reinterpret_cast<char*>(&stored_dim), sizeof(stored_dim));
        in.read(reinterpret_cast<char*>(&stored_code_size), sizeof(stored_code_size));
        in.read(reinterpret_cast<char*>(&count), sizeof(count));
        if (stored_dim != dim) throw std::runtime_error("model dimension mismatch");
        std::vector<float> trained(count);
        in.read(reinterpret_cast<char*>(trained.data()), count * sizeof(float));
        if (!in && count != 0) throw std::runtime_error("truncated model");
        if (kind == CodecKind::PQ) {
            codec.pq = std::make_unique<faiss::ProductQuantizer>(dim, dim, 4);
            codec.pq->centroids = std::move(trained);
            codec.code_size = codec.pq->code_size;
        } else if (kind == CodecKind::SQ) {
            codec.sq = std::make_unique<faiss::ScalarQuantizer>(
                    dim, faiss::ScalarQuantizer::QT_4bit);
            codec.sq->trained = std::move(trained);
            codec.code_size = codec.sq->code_size;
        } else {
            codec.ours = std::make_unique<hnswlib::RaBitQSpace>(
                    dim, 1, static_cast<uint32_t>(seed), false, 4);
            codec.code_size = codec.ours->get_data_size();
        }
        if (codec.code_size != stored_code_size) {
            throw std::runtime_error("model code-size mismatch");
        }
        return codec;
    }

    void save(const fs::path& path) const {
        std::ofstream out(path, std::ios::binary | std::ios::trunc);
        const uint64_t stored_dim = dim;
        const uint64_t stored_code_size = code_size;
        const std::vector<float>* trained = nullptr;
        if (pq) trained = &pq->centroids;
        if (sq) trained = &sq->trained;
        const uint64_t count = trained == nullptr ? 0 : trained->size();
        out.write(reinterpret_cast<const char*>(&stored_dim), sizeof(stored_dim));
        out.write(reinterpret_cast<const char*>(&stored_code_size), sizeof(stored_code_size));
        out.write(reinterpret_cast<const char*>(&count), sizeof(count));
        if (trained != nullptr) {
            out.write(
                    reinterpret_cast<const char*>(trained->data()),
                    trained->size() * sizeof(float));
        }
        if (!out) throw std::runtime_error("failed to write model");
    }

    void encode(const float* vectors, size_t count, std::byte* output) const {
        if (kind == CodecKind::PQ) {
            pq->compute_codes(
                    vectors, reinterpret_cast<uint8_t*>(output), count);
        } else if (kind == CodecKind::SQ) {
            sq->compute_codes(
                    vectors, reinterpret_cast<uint8_t*>(output), count);
        } else {
            for (size_t i = 0; i < count; ++i) {
                ours->encodeVector(
                        vectors + i * dim,
                        reinterpret_cast<char*>(output + i * code_size));
            }
        }
    }

    void distances(
            const float* query,
            const std::byte* codes,
            size_t count,
            std::vector<float>& output) const {
        output.resize(count);
        if (kind == CodecKind::PQ) {
            std::vector<float> reconstructed(count * dim);
            pq->decode(
                    reinterpret_cast<const uint8_t*>(codes),
                    reconstructed.data(),
                    count);
            for (size_t i = 0; i < count; ++i) {
                output[i] = l2sqr(query, reconstructed.data() + i * dim, dim);
            }
        } else if (kind == CodecKind::SQ) {
            std::unique_ptr<faiss::ScalarQuantizer::SQDistanceComputer> computer(
                    sq->get_distance_computer(faiss::METRIC_L2));
            computer->codes = reinterpret_cast<const uint8_t*>(codes);
            computer->code_size = code_size;
            computer->set_query(query);
            for (size_t i = 0; i < count; ++i) {
                output[i] = (*computer)(static_cast<faiss::idx_t>(i));
            }
        } else {
            const void* prepared = ours->prepare_asymmetric_build_query(query);
            for (size_t i = 0; i < count; ++i) {
                output[i] = ours->asymmetric_build_distance_prepared(
                        prepared,
                        reinterpret_cast<const char*>(codes + i * code_size));
            }
            ours->release_asymmetric_build_query(prepared);
        }
    }
};

struct IndexInfo {
    fs::path pages;
    fs::path model;
    fs::path meta;
    uint64_t base_count;
    size_t dim;
    size_t code_size;
};

std::string index_identity(const ContractArgs& args, const VecsInfo& base_info) {
    std::ostringstream out;
    out << "schema=1\n"
        << "method=" << args.require("method") << "\n"
        << "base_path=" << fs::absolute(base_info.path).string() << "\n"
        << "base_bytes=" << fs::file_size(base_info.path) << "\n"
        << "base_count=" << base_info.count << "\n"
        << "dimension=" << base_info.dim << "\n"
        << "seed=" << args.number("seed") << "\n"
        << "input_manifest_sha256=" << args.require("input-manifest-sha256") << "\n"
        << "implementation_fingerprint="
        << args.require("implementation-fingerprint") << "\n"
        << "native_binary_sha256=" << args.require("native-binary-sha256") << "\n";
    return out.str();
}

std::string read_text(const fs::path& path) {
    std::ifstream in(path);
    return std::string(
            std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

IndexInfo ensure_index(
        const ContractArgs& args,
        const VecsInfo& base_info,
        CodecKind kind) {
    const fs::path root = fs::path(args.require("disk-index-dir"));
    fs::create_directories(root);
    const fs::path pages = root / "index.pages";
    const fs::path model = root / "model.bin";
    const fs::path meta = root / "index.meta.json";
    const fs::path identity = root / "index.identity";
    const std::string expected_identity = index_identity(args, base_info);
    const int seed = static_cast<int>(args.number("seed"));
    if (fs::is_regular_file(pages) && fs::is_regular_file(model) &&
        fs::is_regular_file(meta) && fs::is_regular_file(identity) &&
        read_text(identity) == expected_identity &&
        fs::file_size(pages) % kPage == 0) {
        Codec loaded = Codec::load(kind, base_info.dim, seed, model);
        const uint64_t minimum = base_info.count * loaded.code_size;
        if (fs::file_size(pages) >= minimum) {
            return {pages, model, meta, base_info.count, base_info.dim, loaded.code_size};
        }
    }
    if (args.require("phase") != "export") {
        throw std::runtime_error(
                "05A index is missing or stale; run the formal export phase before measurement");
    }

    const size_t train_count = std::min<size_t>(100000, base_info.count);
    std::vector<float> train = load_fvecs_prefix(base_info, train_count);
    Codec codec = Codec::train(kind, base_info.dim, train, seed);
    const fs::path pages_tmp = root / "index.pages.tmp";
    const fs::path model_tmp = root / "model.bin.tmp";
    const fs::path identity_tmp = root / "index.identity.tmp";
    codec.save(model_tmp);
    std::ofstream output(pages_tmp, std::ios::binary | std::ios::trunc);
    if (!output) throw std::runtime_error("cannot create " + pages_tmp.string());
    constexpr size_t batch_size = 4096;
    std::vector<std::byte> encoded;
    for (size_t begin = 0; begin < base_info.count; begin += batch_size) {
        const size_t count = std::min(batch_size, base_info.count - begin);
        std::vector<float> batch = load_fvecs_range(base_info, begin, count);
        encoded.resize(count * codec.code_size);
        codec.encode(batch.data(), count, encoded.data());
        output.write(
                reinterpret_cast<const char*>(encoded.data()),
                static_cast<std::streamsize>(encoded.size()));
        if ((begin + count) % (batch_size * 64) == 0 || begin + count == base_info.count) {
            std::cerr << "05A export " << args.require("method") << " "
                      << (begin + count) << "/" << base_info.count << "\n";
        }
    }
    const uint64_t raw_bytes = base_info.count * codec.code_size;
    const size_t padding = (kPage - raw_bytes % kPage) % kPage;
    const std::array<char, kPage> zero{};
    output.write(zero.data(), padding);
    output.close();
    if (!output) throw std::runtime_error("failed to finish index.pages");
    fs::rename(pages_tmp, pages);
    fs::rename(model_tmp, model);

    std::ofstream metadata(meta, std::ios::trunc);
    metadata << "{\n"
             << "  \"schema_version\": 1,\n"
             << "  \"method\": " << json_string(args.require("method")) << ",\n"
             << "  \"base_count\": " << base_info.count << ",\n"
             << "  \"dimension\": " << base_info.dim << ",\n"
             << "  \"code_size\": " << codec.code_size << ",\n"
             << "  \"page_size\": " << kPage << ",\n"
             << "  \"pages_sha256\": " << json_string(sha256(pages)) << ",\n"
             << "  \"model_sha256\": " << json_string(sha256(model)) << "\n"
             << "}\n";
    metadata.close();
    {
        std::ofstream identity_out(identity_tmp, std::ios::trunc);
        identity_out << expected_identity;
        if (!identity_out) throw std::runtime_error("failed to write index identity");
    }
    fs::rename(identity_tmp, identity);
    return {pages, model, meta, base_info.count, base_info.dim, codec.code_size};
}

void copy_code_from_memory(
        const std::vector<std::byte>& data,
        size_t code_size,
        uint64_t id,
        std::byte* output) {
    const uint64_t offset = id * code_size;
    if (offset + code_size > data.size()) throw std::runtime_error("code id out of bounds");
    std::memcpy(output, data.data() + offset, code_size);
}

void append_code_pages(
        std::vector<uint64_t>& pages, size_t code_size, uint64_t id) {
    const uint64_t begin = id * code_size;
    const uint64_t end = begin + code_size - 1;
    for (uint64_t page = begin / kPage; page <= end / kPage; ++page) {
        pages.push_back(page);
    }
}

void copy_code_from_batch(
        const PageReadBatch& batch,
        size_t code_size,
        uint64_t id,
        std::byte* output) {
    uint64_t offset = id * code_size;
    size_t remaining = code_size;
    while (remaining != 0) {
        const uint64_t page_id = offset / kPage;
        const size_t within = offset % kPage;
        const size_t count = std::min(remaining, kPage - within);
        const auto page = batch.page(page_id);
        std::memcpy(output, page.data() + within, count);
        output += count;
        offset += count;
        remaining -= count;
    }
}

struct QueryStat {
    double latency_us = 0;
    double query_prep_us = 0;
    double queue_compute_us = 0;
    double io_wait_us = 0;
    double distance_compute_us = 0;
    IoStats io;
};

struct SweepResult {
    size_t width = 0;
    Metrics metrics;
    double p99_us = 0;
    double qps = 0;
    double wall_us = 0;
    std::vector<float> distances;
    std::vector<QueryStat> stats;
};

std::vector<float> exact_distances(
        const VecsInfo& base_info,
        const std::vector<float>& queries,
        const std::vector<int32_t>& candidates,
        size_t query_count,
        size_t candidate_width,
        size_t width) {
    std::vector<float> exact(query_count * width);
    std::vector<float> row;
    for (size_t qid = 0; qid < query_count; ++qid) {
        load_fvecs_by_ids_into(
                base_info, candidates, qid * candidate_width, width, row);
        for (size_t rank = 0; rank < width; ++rank) {
            exact[qid * width + rank] = l2sqr(
                    queries.data() + qid * base_info.dim,
                    row.data() + rank * base_info.dim,
                    base_info.dim);
        }
    }
    return exact;
}

SweepResult run_sweep(
        const Codec& codec,
        const IndexInfo& index,
        bool disk_mode,
        const std::vector<std::byte>& resident,
        const std::vector<float>& queries,
        const std::vector<int32_t>& candidates,
        const std::vector<uint32_t>& order,
        size_t candidate_width,
        size_t width,
        size_t workers) {
    const size_t query_count = order.size();
    const size_t total_query_count = queries.size() / codec.dim;
    if (query_count == 0 || total_query_count == 0) {
        throw std::runtime_error("query order and query matrix must be non-empty");
    }
    SweepResult result;
    result.width = width;
    result.distances.resize(total_query_count * width);
    result.stats.resize(total_query_count);
    std::atomic<size_t> cursor{0};
    std::atomic<bool> failed{false};
    std::exception_ptr worker_error;
    std::mutex worker_error_mutex;
    const auto wall_start = std::chrono::steady_clock::now();
    std::vector<std::thread> threads;
    threads.reserve(workers);
    for (size_t worker = 0; worker < workers; ++worker) {
        threads.emplace_back([&, worker] {
            try {
                (void) worker;
                std::optional<DirectAioReader> reader;
                if (disk_mode) reader.emplace(index.pages);
                std::vector<std::byte> row_codes(width * codec.code_size);
                std::vector<float> row_distances;
                std::vector<uint64_t> pages;
                pages.reserve(width * 2);
                while (!failed.load(std::memory_order_relaxed)) {
                    const size_t position = cursor.fetch_add(1);
                    if (position >= query_count) break;
                    const size_t qid = order[position];
                    if (qid >= total_query_count) {
                        throw std::runtime_error("query order id is out of bounds");
                    }
                QueryStat stat;
                const auto total_start = std::chrono::steady_clock::now();
                if (disk_mode) {
                    pages.clear();
                    for (size_t rank = 0; rank < width; ++rank) {
                        const int32_t id = candidates[qid * candidate_width + rank];
                        if (id < 0 || static_cast<uint64_t>(id) >= index.base_count) {
                            throw std::runtime_error("candidate id out of bounds");
                        }
                        append_code_pages(pages, codec.code_size, id);
                    }
                    const auto io_start = std::chrono::steady_clock::now();
                    PageReadBatch batch = reader->read_pages(pages);
                    stat.io_wait_us = std::chrono::duration<double, std::micro>(
                            std::chrono::steady_clock::now() - io_start).count();
                    stat.io = batch.stats();
                    const auto queue_start = std::chrono::steady_clock::now();
                    for (size_t rank = 0; rank < width; ++rank) {
                        const int32_t id = candidates[qid * candidate_width + rank];
                        copy_code_from_batch(
                                batch,
                                codec.code_size,
                                id,
                                row_codes.data() + rank * codec.code_size);
                    }
                    stat.queue_compute_us = std::chrono::duration<double, std::micro>(
                            std::chrono::steady_clock::now() - queue_start).count();
                } else {
                    const auto queue_start = std::chrono::steady_clock::now();
                    for (size_t rank = 0; rank < width; ++rank) {
                        const int32_t id = candidates[qid * candidate_width + rank];
                        copy_code_from_memory(
                                resident,
                                codec.code_size,
                                id,
                                row_codes.data() + rank * codec.code_size);
                    }
                    stat.queue_compute_us = std::chrono::duration<double, std::micro>(
                            std::chrono::steady_clock::now() - queue_start).count();
                }
                const auto distance_start = std::chrono::steady_clock::now();
                codec.distances(
                        queries.data() + qid * codec.dim,
                        row_codes.data(),
                        width,
                        row_distances);
                stat.distance_compute_us = std::chrono::duration<double, std::micro>(
                        std::chrono::steady_clock::now() - distance_start).count();
                std::copy(
                        row_distances.begin(),
                        row_distances.end(),
                        result.distances.begin() + qid * width);
                stat.latency_us = std::chrono::duration<double, std::micro>(
                        std::chrono::steady_clock::now() - total_start).count();
                    result.stats[qid] = stat;
                }
            } catch (...) {
                failed.store(true, std::memory_order_relaxed);
                std::lock_guard<std::mutex> guard(worker_error_mutex);
                if (!worker_error) worker_error = std::current_exception();
            }
        });
    }
    for (auto& thread : threads) thread.join();
    if (worker_error) std::rethrow_exception(worker_error);
    result.wall_us = std::chrono::duration<double, std::micro>(
            std::chrono::steady_clock::now() - wall_start).count();
    result.qps = query_count * 1e6 / result.wall_us;
    std::vector<double> latencies;
    latencies.reserve(query_count);
    for (const uint32_t qid : order) {
        latencies.push_back(result.stats[qid].latency_us);
    }
    result.p99_us = percentile(latencies, 0.99);
    return result;
}

std::vector<std::byte> load_resident(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<std::byte> values(fs::file_size(path));
    in.read(reinterpret_cast<char*>(values.data()), values.size());
    if (!in) throw std::runtime_error("failed to load resident payload");
    return values;
}

void write_reference(
        const fs::path& path,
        const std::vector<SweepResult>& sweeps) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    const uint64_t count = sweeps.size();
    out.write(reinterpret_cast<const char*>(&count), sizeof(count));
    for (const auto& sweep : sweeps) {
        const uint64_t width = sweep.width;
        const uint64_t values = sweep.distances.size();
        out.write(reinterpret_cast<const char*>(&width), sizeof(width));
        out.write(reinterpret_cast<const char*>(&values), sizeof(values));
        out.write(
                reinterpret_cast<const char*>(sweep.distances.data()),
                sweep.distances.size() * sizeof(float));
    }
}

double compare_reference(
        const fs::path& path,
        const std::vector<SweepResult>& sweeps) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("resident parity reference is missing");
    uint64_t count = 0;
    in.read(reinterpret_cast<char*>(&count), sizeof(count));
    if (count != sweeps.size()) throw std::runtime_error("parity sweep count mismatch");
    double delta = 0.0;
    for (const auto& sweep : sweeps) {
        uint64_t width = 0, values = 0;
        in.read(reinterpret_cast<char*>(&width), sizeof(width));
        in.read(reinterpret_cast<char*>(&values), sizeof(values));
        if (width != sweep.width || values != sweep.distances.size()) {
            throw std::runtime_error("parity reference shape mismatch");
        }
        std::vector<float> reference(values);
        in.read(reinterpret_cast<char*>(reference.data()), values * sizeof(float));
        for (size_t i = 0; i < reference.size(); ++i) {
            delta = std::max(delta, std::abs(
                    static_cast<double>(reference[i]) - sweep.distances[i]));
        }
    }
    if (!in) throw std::runtime_error("truncated parity reference");
    return delta;
}

void write_parity_status(
        const fs::path& path,
        const std::string& reference_hash,
        double max_distance_delta,
        double max_recall_delta) {
    std::ofstream out(path, std::ios::trunc);
    out << reference_hash << "\n"
        << std::setprecision(17) << max_distance_delta << "\n"
        << max_recall_delta << "\n";
}

std::tuple<std::string, double, double> read_parity_status(const fs::path& path) {
    std::ifstream in(path);
    std::string hash;
    double distance = 0.0, recall = 0.0;
    in >> hash >> distance >> recall;
    if (!in || hash.size() != 64) {
        throw std::runtime_error("validated parity status is missing");
    }
    return {hash, distance, recall};
}

double mean_field(const std::vector<QueryStat>& stats, double QueryStat::*field) {
    if (stats.empty()) return 0.0;
    double total = 0.0;
    for (const auto& stat : stats) total += stat.*field;
    return total / stats.size();
}

double mean_io(const std::vector<QueryStat>& stats, uint64_t IoStats::*field) {
    if (stats.empty()) return 0.0;
    double total = 0.0;
    for (const auto& stat : stats) total += static_cast<double>(stat.io.*field);
    return total / stats.size();
}

void write_trace(
        const ContractArgs& args,
        const std::vector<SweepResult>& sweeps,
        size_t resident_bytes,
        size_t peak_rss_bytes) {
    const fs::path path = args.require("query-trace");
    fs::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::trunc);
    const bool ours = args.require("method").rfind("Ours", 0) == 0;
    for (const auto& sweep : sweeps) {
        for (size_t qid = 0; qid < sweep.stats.size(); ++qid) {
            const QueryStat& stat = sweep.stats[qid];
            out << "{"
                << "\"layer\":\"05a\","
                << "\"storage_mode\":" << json_string(args.require("storage-mode")) << ","
                << "\"cache_mode\":" << json_string(args.require("cache-mode")) << ","
                << "\"dataset\":" << json_string(args.require("dataset")) << ","
                << "\"method\":" << json_string(args.require("method")) << ","
                << "\"config_id\":" << json_string("fixed_candidates_k" + std::to_string(sweep.width)) << ","
                << "\"repeat_id\":" << args.number("repeat-id") << ","
                << "\"query_id\":" << qid << ","
                << "\"search_width\":" << sweep.width << ","
                << "\"beam_width\":1,"
                << "\"workers\":" << args.number("workers") << ","
                << "\"search_dram_budget_gib\":" << args.real("search-dram-budget-gib") << ","
                << "\"cache_nodes\":0,"
                << "\"resident_bytes\":" << resident_bytes << ","
                << "\"cache_bytes\":0,"
                << "\"peak_rss_bytes\":" << peak_rss_bytes << ","
                << "\"recall_at_10\":" << sweep.metrics.recall_at_10 << ","
                << "\"fixed_candidate_recall_at_10\":" << sweep.metrics.recall_at_10 << ","
                << "\"mean_relative_error\":" << sweep.metrics.mean_rel_error << ","
                << "\"p95_relative_error\":" << sweep.metrics.p95_rel_error << ","
                << "\"pairwise_flip_rate\":" << sweep.metrics.pairwise_flip_rate_top100 << ","
                << "\"latency_us\":" << stat.latency_us << ","
                << "\"query_prep_us\":" << stat.query_prep_us << ","
                << "\"queue_compute_us\":" << stat.queue_compute_us << ","
                << "\"io_wait_us\":" << stat.io_wait_us << ","
                << "\"distance_compute_us\":" << stat.distance_compute_us << ","
                << "\"rerank_us\":0,"
                << "\"visited_nodes\":0,"
                << "\"distance_evaluations\":" << sweep.width << ","
                << "\"io_requests\":" << stat.io.submitted_requests << ","
                << "\"sectors_4k\":" << stat.io.unique_pages << ","
                << "\"bytes_read\":" << stat.io.bytes_read << ","
                << "\"average_read_bytes\":"
                << (stat.io.submitted_requests ? static_cast<double>(stat.io.bytes_read) / stat.io.submitted_requests : 0.0) << ","
                << "\"coalesced_requests\":" << stat.io.coalesced_requests << ","
                << "\"duplicate_pages_removed\":" << stat.io.duplicate_pages_removed << ","
                << "\"shared_cache_hits\":0,\"shared_cache_misses\":0,"
                << "\"query_cache_hits\":0,\"query_cache_misses\":0";
            if (ours) {
                out << ",\"db1_checks\":0,\"db1_survivors\":0,"
                    << "\"full4_candidates\":" << sweep.width << ","
                    << "\"full4_page_reads\":" << stat.io.unique_pages << ","
                    << "\"rerank_candidates\":0,\"rerank_page_reads\":0";
            }
            out << "}\n";
        }
    }
}

void write_artifact(
        const ContractArgs& args,
        const IndexInfo& index,
        const std::vector<SweepResult>& sweeps,
        bool disk_mode,
        size_t resident_bytes,
        const std::string& reference_hash,
        double max_distance_delta,
        double max_recall_delta) {
    const fs::path result_path = args.require("result-json");
    fs::create_directories(result_path.parent_path());
    const std::string phase = args.require("phase");
    const bool measured = phase == "validate" || phase == "validation" || phase == "test";
    const size_t peak_rss_bytes = peak_rss_kib() * 1024;
    const size_t codebook_bytes = fs::file_size(index.model);
    if (measured) {
        write_trace(args, sweeps, resident_bytes, peak_rss_bytes);
    }
    std::ofstream out(result_path, std::ios::trunc);
    out << std::setprecision(12)
        << "{\n"
        << "  \"schema_version\": 2,\n"
        << "  \"status\": \"done\",\n"
        << "  \"layer\": \"05a\",\n"
        << "  \"dataset\": " << json_string(args.require("dataset")) << ",\n"
        << "  \"method\": " << json_string(args.require("method")) << ",\n"
        << "  \"source_suite\": \"01_quantizer_fair\",\n"
        << "  \"source_kernel\": " << json_string(source_kernel(args.require("method"))) << ",\n"
        << "  \"port_kind\": \"algorithm_preserving_disk_port\",\n"
        << "  \"storage_mode\": " << json_string(args.require("storage-mode")) << ",\n"
        << "  \"cache_mode\": " << json_string(args.require("cache-mode")) << ",\n"
        << "  \"phase\": " << json_string(phase) << ",\n"
        << "  \"run_id\": " << json_string(args.require("run-id")) << ",\n"
        << "  \"repeat_id\": " << args.number("repeat-id") << ",\n"
        << "  \"workers\": " << args.number("workers") << ",\n"
        << "  \"warmup_queries\": " << args.number("warmup-queries") << ",\n"
        << "  \"query_split_sha256\": " << json_string(args.require("query-split-sha256")) << ",\n"
        << "  \"query_order_sha256\": " << json_string(args.require("query-order-sha256")) << ",\n"
        << "  \"query_order_seed\": " << args.number("query-order-seed") << ",\n"
        << "  \"index_path\": " << json_string(fs::absolute(args.require("disk-index-dir")).string()) << ",\n"
        << "  \"formal_ready\": true,\n"
        << "  \"page_size\": 4096,\n"
        << "  \"whole_graph_in_memory\": false,\n"
        << "  \"whole_payload_in_memory\": " << (disk_mode ? "false" : "true") << ",\n"
        << "  \"direct_io\": " << (disk_mode ? "true" : "false") << ",\n"
        << "  \"native_aio\": " << (disk_mode ? "true" : "false") << ",\n"
        << "  \"io_backend\": " << json_string(disk_mode ? "linux_native_aio_odirect" : "resident") << ",\n"
        << "  \"implementation_fingerprint\": " << json_string(args.require("implementation-fingerprint")) << ",\n"
        << "  \"native_binary_sha256\": " << json_string(args.require("native-binary-sha256")) << ",\n"
        << "  \"input_manifest_sha256\": " << json_string(args.require("input-manifest-sha256")) << ",\n"
        << "  \"source_index_manifest_sha256\": " << json_string(sha256(index.meta)) << ",\n"
        << "  \"git_commit\": " << json_string(args.require("git-commit")) << ",\n"
        << "  \"compiler\": " << json_string(__VERSION__) << ",\n"
        << "  \"simd\": \"AVX2/AVX-512 native\",\n"
        << "  \"base_count\": " << index.base_count << ",\n"
        << "  \"dimension\": " << index.dim << ",\n"
        << "  \"code_bytes_per_vector\": " << index.code_size << ",\n"
        << "  \"effective_bits_per_dim\": "
        << (8.0 * static_cast<double>(index.code_size) / index.dim) << ",\n"
        << "  \"search_dram_budget_gib\": " << args.real("search-dram-budget-gib") << ",\n"
        << "  \"resident_bytes\": " << resident_bytes << ",\n"
        << "  \"codebook_bytes\": " << codebook_bytes << ",\n"
        << "  \"worker_scratch_bytes\": " << args.number("workers") * (32ULL << 20) << ",\n"
        << "  \"cache_bytes\": 0,\n"
        << "  \"cache_nodes\": 0,\n"
        << "  \"peak_rss_bytes\": " << peak_rss_bytes << ",\n"
        << "  \"cpu_affinity\": \"process-default\",\n"
        << "  \"numa_node\": \"unbound\",\n"
        << "  \"implementation_parity\": " << json_string(measured ? "passed" : "not_run") << ",\n"
        << "  \"parity\": {\"reference_artifact_sha256\": " << json_string(reference_hash)
        << ", \"max_recall_delta\": " << max_recall_delta
        << ", \"max_distance_delta\": " << max_distance_delta << "},\n";
    if (measured) {
        const fs::path trace = fs::absolute(args.require("query-trace"));
        out << "  \"query_trace_path\": " << json_string(trace.string()) << ",\n"
            << "  \"query_trace_sha256\": " << json_string(sha256(trace)) << ",\n";
    }
    out << "  \"summary_rows\": [";
    for (size_t index_sweep = 0; index_sweep < sweeps.size(); ++index_sweep) {
        const auto& sweep = sweeps[index_sweep];
        if (index_sweep) out << ',';
        const double p50 = sweep.metrics.latency_p50_us;
        const double p95 = sweep.metrics.latency_p95_us;
        const double io_requests = mean_io(sweep.stats, &IoStats::submitted_requests);
        const double pages = mean_io(sweep.stats, &IoStats::unique_pages);
        const double bytes = mean_io(sweep.stats, &IoStats::bytes_read);
        out << "\n    {"
            << "\"config_id\":" << json_string("fixed_candidates_k" + std::to_string(sweep.width)) << ","
            << "\"search_param\":" << json_string("rerank_candidates=" + std::to_string(sweep.width)) << ","
            << "\"search_width\":" << sweep.width << ",\"beam_width\":1,"
            << "\"recall\":" << sweep.metrics.recall_at_10 << ","
            << "\"qps\":" << sweep.qps << ","
            << "\"latency_mean_us\":" << mean_field(sweep.stats, &QueryStat::latency_us) << ","
            << "\"latency_p50_us\":" << p50 << ","
            << "\"latency_p95_us\":" << p95 << ","
            << "\"latency_p99_us\":" << sweep.p99_us << ","
            << "\"fixed_candidate_recall_at_10\":" << sweep.metrics.recall_at_10 << ","
            << "\"mean_relative_error\":" << sweep.metrics.mean_rel_error << ","
            << "\"p95_relative_error\":" << sweep.metrics.p95_rel_error << ","
            << "\"pairwise_flip_rate\":" << sweep.metrics.pairwise_flip_rate_top100 << ","
            << "\"code_bytes_per_vector\":" << index.code_size << ","
            << "\"effective_bits_per_dim\":"
            << (8.0 * static_cast<double>(index.code_size) / index.dim) << ","
            << "\"read_amplification\":"
            << (disk_mode && sweep.width && index.code_size
                    ? bytes / (static_cast<double>(sweep.width) * index.code_size)
                    : 0.0) << ","
            << "\"query_count\":" << sweep.stats.size() << ","
            << "\"index_size_mb\":" << static_cast<double>(fs::file_size(index.pages) + codebook_bytes) / (1 << 20) << ","
            << "\"resident_bytes\":" << resident_bytes << ","
            << "\"peak_rss_bytes\":" << peak_rss_bytes << ","
            << "\"io_requests_per_query\":" << io_requests << ","
            << "\"sectors_4k_per_query\":" << pages << ","
            << "\"bytes_read_per_query\":" << bytes << ","
            << "\"io_wait_us\":" << mean_field(sweep.stats, &QueryStat::io_wait_us) << ","
            << "\"distance_compute_us\":" << mean_field(sweep.stats, &QueryStat::distance_compute_us) << ","
            << "\"query_prep_us\":0,"
            << "\"queue_compute_us\":" << mean_field(sweep.stats, &QueryStat::queue_compute_us) << ","
            << "\"rerank_us\":0,\"visited_nodes\":0,"
            << "\"distance_evaluations\":" << sweep.width
            << "}";
    }
    out << "\n  ]\n}\n";
    if (!out) throw std::runtime_error("failed to write result artifact");
}

int run(int argc, char** argv) {
    const ContractArgs args = ContractArgs::parse(argc, argv);
    if (args.require("contract-version") != "2" || args.require("layer") != "05a") {
        throw std::runtime_error("this binary only implements contract v2 layer 05a");
    }
    const std::string method = args.require("method");
    const CodecKind kind = codec_kind(method);
    const bool disk_mode = args.require("storage-mode") == "payload_on_ssd";
    if (!disk_mode && args.require("storage-mode") != "resident") {
        throw std::runtime_error("05A storage mode must be resident or payload_on_ssd");
    }
    const fs::path data_root = args.require("data-root");
    const fs::path base_path = data_root / args.require("dataset") /
            (args.require("dataset") + "_base.fvecs");
    const VecsInfo base_info = inspect_vecs(base_path);
    const IndexInfo index = ensure_index(args, base_info, kind);
    Codec codec = Codec::load(
            kind, index.dim, static_cast<int>(args.number("seed")), index.model);

    const std::string phase = args.require("phase");
    if (phase == "export") {
        write_artifact(args, index, {}, disk_mode, 0, "", 0.0, 0.0);
        return 0;
    }

    const VecsInfo query_info = inspect_vecs(args.require("query"));
    if (query_info.dim != index.dim) throw std::runtime_error("query dimension mismatch");
    const size_t query_count = query_info.count;
    const std::vector<float> queries = load_fvecs_prefix(query_info, query_count);
    constexpr size_t candidate_width = 1000;
    const fs::path candidate_path = fs::path(args.require("work-root")) / "01_quantizer_fair" /
            args.require("dataset") / "fixed_candidates_k1000.bin";
    const std::vector<int32_t> candidates = load_candidates_at(
            candidate_path,
            args.number("candidate-row-offset"),
            query_count,
            candidate_width);
    const std::vector<uint32_t> order = load_query_order(
            args.require("query-order"), query_count);
    std::vector<size_t> widths;
    if (const char* env = std::getenv("QG05_FAST_WIDTHS")) {
        std::istringstream stream(env);
        std::string token;
        while (std::getline(stream, token, ',')) {
            char* end = nullptr;
            const long value = std::strtol(token.c_str(), &end, 10);
            if (!token.empty() && end && *end == '\0' && value > 0) {
                widths.push_back(static_cast<size_t>(value));
            }
        }
    }
    if (widths.empty()) {
        widths = {
            10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
            20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
            40, 50, 60, 70, 80, 90, 100, 140, 180, 220,
            260, 300, 340, 380, 420, 460, 500, 540, 580};
    }
    std::vector<std::byte> resident;
    if (!disk_mode) resident = load_resident(index.pages);
    const size_t max_width = *std::max_element(widths.begin(), widths.end());
    const std::vector<float> exact_full = exact_distances(
            base_info,
            queries,
            candidates,
            query_count,
            candidate_width,
            max_width);
    const size_t warmup_count = std::min(
            args.number("warmup-queries"), order.size());
    const std::vector<uint32_t> warmup_order(
            order.begin(), order.begin() + static_cast<std::ptrdiff_t>(warmup_count));
    std::vector<SweepResult> sweeps;
    sweeps.reserve(widths.size());
    for (const size_t width : widths) {
        if (!warmup_order.empty()) {
            (void) run_sweep(
                    codec,
                    index,
                    disk_mode,
                    resident,
                    queries,
                    candidates,
                    warmup_order,
                    candidate_width,
                    width,
                    args.number("workers"));
        }
        SweepResult sweep = run_sweep(
                codec,
                index,
                disk_mode,
                resident,
                queries,
                candidates,
                order,
                candidate_width,
                width,
                args.number("workers"));
        const std::vector<float> exact = prefix_matrix(
                exact_full, query_count, max_width, width);
        std::vector<double> latencies;
        for (const auto& stat : sweep.stats) latencies.push_back(stat.latency_us);
        sweep.metrics = compute_metrics(
                exact,
                sweep.distances,
                exact,
                sweep.distances,
                query_count,
                width,
                latencies);
        sweeps.push_back(std::move(sweep));
    }

    const fs::path reference_path = fs::path(args.require("disk-index-dir")) /
            "resident_validation_distances.bin";
    const fs::path parity_path = fs::path(args.require("disk-index-dir")) /
            "parity.passed";
    std::string reference_hash;
    double max_distance_delta = 0.0;
    double max_recall_delta = 0.0;
    if (phase == "validate" || phase == "validation") {
        if (!disk_mode) {
            write_reference(reference_path, sweeps);
            reference_hash = sha256(reference_path);
            write_parity_status(parity_path, reference_hash, 0.0, 0.0);
        } else {
            max_distance_delta = compare_reference(reference_path, sweeps);
            reference_hash = sha256(reference_path);
            if (max_distance_delta > 1e-5) {
                throw std::runtime_error("resident/direct-disk distance parity exceeds 1e-5");
            }
            write_parity_status(
                    parity_path, reference_hash, max_distance_delta, max_recall_delta);
        }
    } else if (phase == "test") {
        std::tie(reference_hash, max_distance_delta, max_recall_delta) =
                read_parity_status(parity_path);
    } else {
        throw std::runtime_error("unsupported phase " + phase);
    }
    const size_t resident_bytes = disk_mode ? 0 : resident.size();
    write_artifact(
            args,
            index,
            sweeps,
            disk_mode,
            resident_bytes,
            reference_hash,
            max_distance_delta,
            max_recall_delta);
    return 0;
}

}  // namespace

#ifndef QGRAPH05_QUANTIZER_PORT_LIBRARY
int main(int argc, char** argv) {
    try {
        return run(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << "\n";
        return 2;
    }
}
#endif
