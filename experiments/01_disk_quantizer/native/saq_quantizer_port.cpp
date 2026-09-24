#define QGRAPH05_QUANTIZER_PORT_LIBRARY
#include "quantizer_port.cpp"

#include "index/ivf.hpp"
#include "quantization/saq_estimator.hpp"

namespace {

using saqlib::DistType;
using saqlib::ExFactor;
using saqlib::FloatVec;
using saqlib::IVF;
using saqlib::KFastScanSize;
using saqlib::PID;
using saqlib::SaqCluData;
using saqlib::SaqCluEstimator;
using saqlib::SaqData;
using saqlib::SearcherConfig;

constexpr uint64_t kSaqModelMagic = 0x5147524153513035ULL;
constexpr uint64_t kInvalidBlock = std::numeric_limits<uint64_t>::max();

struct SaqLocation {
    uint64_t block = kInvalidBlock;
    uint8_t local = 0;
};

struct SaqDiskModel {
    uint64_t base_count = 0;
    uint64_t dimension = 0;
    uint64_t cluster_count = 0;
    uint64_t block_count = 0;
    uint64_t record_size = 0;
    uint64_t padded_dimension = 0;
    std::unique_ptr<SaqData> data;
    std::vector<SaqLocation> locations;
    std::vector<uint32_t> block_cluster;
    std::vector<uint32_t> block_valid;
    std::vector<float> centroids;
};

template <typename T>
void write_scalar(std::ofstream& out, const T& value) {
    out.write(reinterpret_cast<const char*>(&value), sizeof(value));
}

template <typename T>
void read_scalar(std::ifstream& in, T& value) {
    in.read(reinterpret_cast<char*>(&value), sizeof(value));
}

size_t saq_record_size(const SaqData& data) {
    size_t bytes = 2 * sizeof(uint32_t);
    for (const auto& [dimension, bits] : data.quant_plan) {
        const size_t short_bytes = bits ? dimension * KFastScanSize / 8 : 0;
        const size_t long_bytes = bits ? dimension * (bits - 1) / 8 : 0;
        bytes += 2 * KFastScanSize * sizeof(float);
        bytes += short_bytes;
        bytes += KFastScanSize * (long_bytes + sizeof(ExFactor));
    }
    return ((bytes + 63) / 64) * 64;
}

template <typename T>
void append_record(std::vector<std::byte>& record, size_t& offset, const T* data, size_t count) {
    const size_t bytes = count * sizeof(T);
    if (offset + bytes > record.size()) throw std::runtime_error("SAQ record overflow");
    std::memcpy(record.data() + offset, data, bytes);
    offset += bytes;
}

std::vector<std::byte> serialize_saq_block(
        const SaqCluData& cluster,
        uint32_t cluster_id,
        size_t block_index,
        uint32_t valid,
        const SaqData& data,
        size_t record_size) {
    std::vector<std::byte> record(record_size);
    size_t offset = 0;
    append_record(record, offset, &valid, 1);
    append_record(record, offset, &cluster_id, 1);
    for (size_t segment_id = 0; segment_id < data.quant_plan.size(); ++segment_id) {
        const auto& source = cluster.get_segment(segment_id);
        const auto [dimension, bits] = data.quant_plan[segment_id];
        const size_t short_bytes = bits ? dimension * KFastScanSize / 8 : 0;
        const size_t long_bytes = bits ? dimension * (bits - 1) / 8 : 0;
        append_record(record, offset, source.factor_o_l2norm(block_index), KFastScanSize);
        append_record(record, offset, source.factor_ip_cent_oa(block_index), KFastScanSize);
        if (short_bytes) {
            append_record(record, offset, source.short_code(block_index), short_bytes);
        }
        for (size_t local = 0; local < KFastScanSize; ++local) {
            const size_t source_local = block_index * KFastScanSize + local;
            if (local < valid) {
                if (long_bytes) {
                    append_record(record, offset, source.long_code(source_local), long_bytes);
                }
                const ExFactor factor = source.long_factor(source_local);
                append_record(record, offset, &factor, 1);
            } else {
                offset += long_bytes + sizeof(ExFactor);
                if (offset > record.size()) throw std::runtime_error("SAQ padding overflow");
            }
        }
    }
    return record;
}

void write_saq_model(const fs::path& path, const IVF& ivf, const SaqDiskModel& model) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out) throw std::runtime_error("cannot create SAQ disk model");
    write_scalar(out, kSaqModelMagic);
    write_scalar(out, model.base_count);
    write_scalar(out, model.dimension);
    write_scalar(out, model.cluster_count);
    write_scalar(out, model.block_count);
    write_scalar(out, model.record_size);
    write_scalar(out, model.padded_dimension);
    ivf.get_saq_data()->save(out);
    for (const auto& location : model.locations) {
        write_scalar(out, location.block);
        write_scalar(out, location.local);
    }
    out.write(
            reinterpret_cast<const char*>(model.block_cluster.data()),
            model.block_cluster.size() * sizeof(uint32_t));
    out.write(
            reinterpret_cast<const char*>(model.block_valid.data()),
            model.block_valid.size() * sizeof(uint32_t));
    out.write(
            reinterpret_cast<const char*>(model.centroids.data()),
            model.centroids.size() * sizeof(float));
    if (!out) throw std::runtime_error("failed to finish SAQ disk model");
}

SaqDiskModel load_saq_model(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open SAQ disk model " + path.string());
    uint64_t magic = 0;
    SaqDiskModel model;
    read_scalar(in, magic);
    read_scalar(in, model.base_count);
    read_scalar(in, model.dimension);
    read_scalar(in, model.cluster_count);
    read_scalar(in, model.block_count);
    read_scalar(in, model.record_size);
    read_scalar(in, model.padded_dimension);
    if (magic != kSaqModelMagic) throw std::runtime_error("SAQ disk model magic mismatch");
    model.data = std::make_unique<SaqData>();
    model.data->load(in);
    if (model.data->num_dim != model.dimension ||
        saq_record_size(*model.data) != model.record_size) {
        throw std::runtime_error("SAQ disk model metadata mismatch");
    }
    model.locations.resize(model.base_count);
    for (auto& location : model.locations) {
        read_scalar(in, location.block);
        read_scalar(in, location.local);
    }
    model.block_cluster.resize(model.block_count);
    model.block_valid.resize(model.block_count);
    model.centroids.resize(model.cluster_count * model.padded_dimension);
    in.read(
            reinterpret_cast<char*>(model.block_cluster.data()),
            model.block_cluster.size() * sizeof(uint32_t));
    in.read(
            reinterpret_cast<char*>(model.block_valid.data()),
            model.block_valid.size() * sizeof(uint32_t));
    in.read(
            reinterpret_cast<char*>(model.centroids.data()),
            model.centroids.size() * sizeof(float));
    if (!in) throw std::runtime_error("truncated SAQ disk model");
    return model;
}

std::string saq_index_identity(
        const ContractArgs& args,
        const VecsInfo& base_info,
        const fs::path& source_index) {
    std::ostringstream out;
    out << "schema=1\nmethod=SAQ_B4\n"
        << "base_path=" << fs::absolute(base_info.path).string() << "\n"
        << "base_bytes=" << fs::file_size(base_info.path) << "\n"
        << "source_index=" << fs::absolute(source_index).string() << "\n"
        << "source_index_bytes=" << fs::file_size(source_index) << "\n"
        << "input_manifest_sha256=" << args.require("input-manifest-sha256") << "\n"
        << "implementation_fingerprint=" << args.require("implementation-fingerprint") << "\n"
        << "native_binary_sha256=" << args.require("native-binary-sha256") << "\n";
    return out.str();
}

IndexInfo ensure_saq_index(
        const ContractArgs& args,
        const VecsInfo& base_info) {
    const fs::path root = args.require("disk-index-dir");
    fs::create_directories(root);
    const fs::path pages = root / "index.pages";
    const fs::path model_path = root / "model.bin";
    const fs::path meta = root / "index.meta.json";
    const fs::path identity = root / "index.identity";
    const fs::path source_index = fs::path(args.require("saq-data-root")) /
            args.require("dataset") / "ivf4096_b4_caq_adj_seg_pca.index";
    if (!fs::is_regular_file(source_index)) {
        throw std::runtime_error("official SAQ B4 index is missing: " + source_index.string());
    }
    const std::string expected_identity = saq_index_identity(args, base_info, source_index);
    if (fs::is_regular_file(pages) && fs::is_regular_file(model_path) &&
        fs::is_regular_file(meta) && fs::is_regular_file(identity) &&
        read_text(identity) == expected_identity && fs::file_size(pages) % kPage == 0) {
        SaqDiskModel model = load_saq_model(model_path);
        if (model.base_count == base_info.count && model.dimension == base_info.dim &&
            fs::file_size(pages) >= model.block_count * model.record_size) {
            return {pages, model_path, meta, model.base_count,
                    static_cast<size_t>(model.dimension), static_cast<size_t>(model.record_size)};
        }
    }
    if (args.require("phase") != "export") {
        throw std::runtime_error(
                "SAQ 05A disk index is missing or stale; run the formal export phase first");
    }

    IVF ivf;
    ivf.load(source_index.c_str());
    if (ivf.num_data() != base_info.count || ivf.num_dim() != base_info.dim) {
        throw std::runtime_error("official SAQ index does not match the raw base dataset");
    }
    const SaqData& data = *ivf.get_saq_data();
    SaqDiskModel model;
    model.base_count = ivf.num_data();
    model.dimension = ivf.num_dim();
    model.cluster_count = ivf.get_pclusters().size();
    model.record_size = saq_record_size(data);
    for (const auto& [dimension, bits] : data.quant_plan) {
        (void) bits;
        model.padded_dimension += dimension;
    }
    for (const auto& cluster : ivf.get_pclusters()) model.block_count += cluster.num_blocks_;
    model.locations.resize(model.base_count);
    model.block_cluster.reserve(model.block_count);
    model.block_valid.reserve(model.block_count);
    model.centroids.resize(model.cluster_count * model.padded_dimension);

    const fs::path pages_tmp = root / "index.pages.tmp";
    const fs::path model_tmp = root / "model.bin.tmp";
    const fs::path identity_tmp = root / "index.identity.tmp";
    std::ofstream payload(pages_tmp, std::ios::binary | std::ios::trunc);
    if (!payload) throw std::runtime_error("cannot create SAQ payload pages");
    uint64_t block_id = 0;
    for (size_t cluster_id = 0; cluster_id < ivf.get_pclusters().size(); ++cluster_id) {
        const auto& cluster = ivf.get_pclusters()[cluster_id];
        size_t centroid_offset = cluster_id * model.padded_dimension;
        for (size_t segment_id = 0; segment_id < data.quant_plan.size(); ++segment_id) {
            const auto& centroid = cluster.get_segment(segment_id).centroid();
            std::copy(
                    centroid.data(), centroid.data() + centroid.size(),
                    model.centroids.begin() + static_cast<std::ptrdiff_t>(centroid_offset));
            centroid_offset += centroid.size();
        }
        for (size_t block = 0; block < cluster.num_blocks_; ++block, ++block_id) {
            const uint32_t valid = static_cast<uint32_t>(std::min<size_t>(
                    KFastScanSize, cluster.num_vec_ - block * KFastScanSize));
            const std::vector<std::byte> record = serialize_saq_block(
                    cluster, static_cast<uint32_t>(cluster_id), block, valid,
                    data, model.record_size);
            payload.write(reinterpret_cast<const char*>(record.data()), record.size());
            model.block_cluster.push_back(static_cast<uint32_t>(cluster_id));
            model.block_valid.push_back(valid);
            for (size_t local = 0; local < valid; ++local) {
                const PID id = cluster.ids()[block * KFastScanSize + local];
                if (id >= model.locations.size() ||
                    model.locations[id].block != kInvalidBlock) {
                    throw std::runtime_error("invalid or duplicate vector id in SAQ index");
                }
                model.locations[id] = {block_id, static_cast<uint8_t>(local)};
            }
        }
        if ((cluster_id + 1) % 256 == 0 || cluster_id + 1 == model.cluster_count) {
            std::cerr << "05A SAQ export clusters " << (cluster_id + 1)
                      << "/" << model.cluster_count << "\n";
        }
    }
    if (block_id != model.block_count ||
        std::any_of(model.locations.begin(), model.locations.end(),
                    [](const SaqLocation& value) { return value.block == kInvalidBlock; })) {
        throw std::runtime_error("SAQ block/location export is incomplete");
    }
    const uint64_t raw_bytes = model.block_count * model.record_size;
    const size_t padding = (kPage - raw_bytes % kPage) % kPage;
    const std::array<char, kPage> zero{};
    payload.write(zero.data(), padding);
    payload.close();
    if (!payload) throw std::runtime_error("failed to finish SAQ payload pages");
    write_saq_model(model_tmp, ivf, model);
    fs::rename(pages_tmp, pages);
    fs::rename(model_tmp, model_path);

    std::ofstream metadata(meta, std::ios::trunc);
    metadata << "{\n"
             << "  \"schema_version\": 1,\n"
             << "  \"method\": \"SAQ_B4\",\n"
             << "  \"layout\": \"official_fastscan_32_vector_blocks\",\n"
             << "  \"base_count\": " << model.base_count << ",\n"
             << "  \"dimension\": " << model.dimension << ",\n"
             << "  \"block_count\": " << model.block_count << ",\n"
             << "  \"record_size\": " << model.record_size << ",\n"
             << "  \"page_size\": " << kPage << ",\n"
             << "  \"source_index_sha256\": " << json_string(sha256(source_index)) << ",\n"
             << "  \"pages_sha256\": " << json_string(sha256(pages)) << ",\n"
             << "  \"model_sha256\": " << json_string(sha256(model_path)) << "\n"
             << "}\n";
    metadata.close();
    {
        std::ofstream identity_out(identity_tmp, std::ios::trunc);
        identity_out << expected_identity;
        if (!identity_out) throw std::runtime_error("failed to write SAQ index identity");
    }
    fs::rename(identity_tmp, identity);
    return {pages, model_path, meta, model.base_count,
            static_cast<size_t>(model.dimension), static_cast<size_t>(model.record_size)};
}

template <typename T>
void take_record(
        const std::vector<std::byte>& record, size_t& offset, T* output, size_t count) {
    const size_t bytes = count * sizeof(T);
    if (offset + bytes > record.size()) throw std::runtime_error("truncated SAQ block record");
    std::memcpy(output, record.data() + offset, bytes);
    offset += bytes;
}

void load_saq_record(
        const std::vector<std::byte>& record,
        uint64_t expected_block,
        const SaqDiskModel& model,
        SaqCluData& target) {
    size_t offset = 0;
    uint32_t valid = 0, cluster_id = 0;
    take_record(record, offset, &valid, 1);
    take_record(record, offset, &cluster_id, 1);
    if (expected_block >= model.block_count ||
        valid != model.block_valid[expected_block] ||
        cluster_id != model.block_cluster[expected_block]) {
        throw std::runtime_error("SAQ block header/model mismatch");
    }
    size_t centroid_offset = cluster_id * model.padded_dimension;
    for (size_t segment_id = 0; segment_id < model.data->quant_plan.size(); ++segment_id) {
        auto& destination = target.get_segment(segment_id);
        const auto [dimension, bits] = model.data->quant_plan[segment_id];
        const size_t short_bytes = bits ? dimension * KFastScanSize / 8 : 0;
        const size_t long_bytes = bits ? dimension * (bits - 1) / 8 : 0;
        take_record(record, offset, destination.factor_o_l2norm(0), KFastScanSize);
        take_record(record, offset, destination.factor_ip_cent_oa(0), KFastScanSize);
        if (short_bytes) {
            take_record(record, offset, destination.short_code(0), short_bytes);
        }
        for (size_t local = 0; local < KFastScanSize; ++local) {
            if (long_bytes) {
                take_record(record, offset, destination.long_code(local), long_bytes);
            }
            ExFactor factor;
            take_record(record, offset, &factor, 1);
            destination.long_factor(local) = factor;
        }
        std::copy(
                model.centroids.begin() + static_cast<std::ptrdiff_t>(centroid_offset),
                model.centroids.begin() + static_cast<std::ptrdiff_t>(centroid_offset + dimension),
                destination.centroid().data());
        centroid_offset += dimension;
    }
}

SweepResult run_saq_sweep(
        const SaqDiskModel& model,
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
    const size_t total_query_count = queries.size() / model.dimension;
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
    for (size_t worker = 0; worker < workers; ++worker) {
        threads.emplace_back([&, worker] {
            try {
                (void) worker;
                std::optional<DirectAioReader> reader;
                if (disk_mode) reader.emplace(index.pages);
                SaqCluData block(
                        KFastScanSize,
                        model.data->quant_plan,
                        model.data->cfg.use_compact_layout);
                std::vector<std::byte> record(model.record_size);
                std::vector<std::pair<uint64_t, size_t>> grouped;
                grouped.reserve(width);
                std::vector<uint64_t> pages;
                pages.reserve(width * ((model.record_size + kPage - 1) / kPage + 1));
                SearcherConfig searcher_cfg;
                searcher_cfg.searcher_vars_bound_m = 4.0f;
                searcher_cfg.dist_type = DistType::L2Sqr;
                while (!failed.load(std::memory_order_relaxed)) {
                    const size_t position = cursor.fetch_add(1);
                    if (position >= query_count) break;
                    const size_t qid = order[position];
                    if (qid >= total_query_count) throw std::runtime_error("query id out of bounds");
                    QueryStat stat;
                    const auto total_start = std::chrono::steady_clock::now();
                    const auto queue_start = std::chrono::steady_clock::now();
                    grouped.clear();
                    for (size_t rank = 0; rank < width; ++rank) {
                        const int32_t id = candidates[qid * candidate_width + rank];
                        if (id < 0 || static_cast<uint64_t>(id) >= model.base_count) {
                            throw std::runtime_error("candidate id out of SAQ range");
                        }
                        const SaqLocation location = model.locations[id];
                        if (location.block == kInvalidBlock) {
                            throw std::runtime_error("candidate is absent from SAQ mapping");
                        }
                        grouped.emplace_back(location.block, rank);
                    }
                    std::sort(grouped.begin(), grouped.end());
                    stat.queue_compute_us = std::chrono::duration<double, std::micro>(
                            std::chrono::steady_clock::now() - queue_start).count();
                    std::optional<PageReadBatch> batch;
                    if (disk_mode) {
                        pages.clear();
                        uint64_t prior = kInvalidBlock;
                        for (const auto& [block_id, rank] : grouped) {
                            (void) rank;
                            if (block_id != prior) {
                                append_code_pages(pages, model.record_size, block_id);
                                prior = block_id;
                            }
                        }
                        const auto io_start = std::chrono::steady_clock::now();
                        batch.emplace(reader->read_pages(pages));
                        stat.io_wait_us = std::chrono::duration<double, std::micro>(
                                std::chrono::steady_clock::now() - io_start).count();
                        stat.io = batch->stats();
                    }
                    const auto distance_start = std::chrono::steady_clock::now();
                    const float* query_ptr = queries.data() + qid * model.dimension;
                    const Eigen::Map<const FloatVec> query(query_ptr, model.dimension);
                    SaqCluEstimator<DistType::L2Sqr> estimator(
                            *model.data, searcher_cfg, query);
                    size_t begin = 0;
                    while (begin < grouped.size()) {
                        const uint64_t block_id = grouped[begin].first;
                        if (disk_mode) {
                            copy_code_from_batch(
                                    *batch, model.record_size, block_id, record.data());
                        } else {
                            copy_code_from_memory(
                                    resident, model.record_size, block_id, record.data());
                        }
                        load_saq_record(record, block_id, model, block);
                        estimator.prepare(&block);
                        __m512 fast[2], variance[2];
                        estimator.compFastDist(0, fast);
                        estimator.varsEstDist(0, variance);
                        size_t end = begin + 1;
                        while (end < grouped.size() && grouped[end].first == block_id) ++end;
                        for (size_t item = begin; item < end; ++item) {
                            const size_t rank = grouped[item].second;
                            const int32_t id = candidates[qid * candidate_width + rank];
                            const size_t local = model.locations[id].local;
                            if (local >= model.block_valid[block_id]) {
                                throw std::runtime_error("SAQ candidate local index is invalid");
                            }
                            result.distances[qid * width + rank] =
                                    estimator.compAccurateDist(local);
                        }
                        begin = end;
                    }
                    stat.distance_compute_us = std::chrono::duration<double, std::micro>(
                            std::chrono::steady_clock::now() - distance_start).count();
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
    for (const uint32_t qid : order) latencies.push_back(result.stats[qid].latency_us);
    result.p99_us = percentile(latencies, 0.99);
    return result;
}

int run_saq(int argc, char** argv) {
    const ContractArgs args = ContractArgs::parse(argc, argv);
    if (args.require("contract-version") != "2" || args.require("layer") != "05a" ||
        args.require("method") != "SAQ_B4") {
        throw std::runtime_error("this binary only implements contract v2 05a:SAQ_B4");
    }
    const bool disk_mode = args.require("storage-mode") == "payload_on_ssd";
    if (!disk_mode && args.require("storage-mode") != "resident") {
        throw std::runtime_error("SAQ 05A storage mode must be resident or payload_on_ssd");
    }
    const fs::path data_root = args.require("data-root");
    const fs::path base_path = data_root / args.require("dataset") /
            (args.require("dataset") + "_base.fvecs");
    const VecsInfo base_info = inspect_vecs(base_path);
    const IndexInfo index = ensure_saq_index(args, base_info);
    if (args.require("phase") == "export") {
        write_artifact(args, index, {}, disk_mode, 0, "", 0.0, 0.0);
        return 0;
    }
    SaqDiskModel model = load_saq_model(index.model);
    const VecsInfo raw_query_info = inspect_vecs(args.require("query"));
    if (raw_query_info.dim != model.dimension) throw std::runtime_error("raw query dimension mismatch");
    const size_t query_count = raw_query_info.count;
    const std::vector<float> raw_queries = load_fvecs_prefix(raw_query_info, query_count);
    const fs::path pca_path = fs::path(args.require("saq-data-root")) /
            args.require("dataset") / (args.require("dataset") + "_query_pca.fvecs");
    const VecsInfo pca_info = inspect_vecs(pca_path);
    if (pca_info.dim != model.dimension) throw std::runtime_error("SAQ PCA query dimension mismatch");
    const size_t query_offset = args.number("candidate-row-offset");
    const std::vector<float> pca_queries = load_fvecs_range(pca_info, query_offset, query_count);
    constexpr size_t candidate_width = 1000;
    const fs::path candidate_path = fs::path(args.require("work-root")) /
            "01_quantizer_fair" / args.require("dataset") / "fixed_candidates_k1000.bin";
    const std::vector<int32_t> candidates = load_candidates_at(
            candidate_path, query_offset, query_count, candidate_width);
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
    const size_t max_width = *std::max_element(widths.begin(), widths.end());
    const std::vector<float> exact_full = exact_distances(
            base_info, raw_queries, candidates, query_count,
            candidate_width, max_width);
    std::vector<std::byte> resident;
    if (!disk_mode) resident = load_resident(index.pages);
    const size_t warmup_count = std::min(args.number("warmup-queries"), order.size());
    const std::vector<uint32_t> warmup_order(
            order.begin(), order.begin() + static_cast<std::ptrdiff_t>(warmup_count));
    std::vector<SweepResult> sweeps;
    sweeps.reserve(widths.size());
    for (const size_t width : widths) {
        if (!warmup_order.empty()) {
            (void) run_saq_sweep(
                    model, index, disk_mode, resident, pca_queries, candidates,
                    warmup_order, candidate_width, width, args.number("workers"));
        }
        SweepResult sweep = run_saq_sweep(
                model, index, disk_mode, resident, pca_queries, candidates,
                order, candidate_width, width, args.number("workers"));
        const std::vector<float> exact = prefix_matrix(
                exact_full, query_count, max_width, width);
        std::vector<double> latencies;
        for (const auto& stat : sweep.stats) latencies.push_back(stat.latency_us);
        sweep.metrics = compute_metrics(
                exact, sweep.distances, exact, sweep.distances,
                query_count, width, latencies);
        sweeps.push_back(std::move(sweep));
    }

    const fs::path reference_path = fs::path(args.require("disk-index-dir")) /
            "resident_validation_distances.bin";
    const fs::path parity_path = fs::path(args.require("disk-index-dir")) / "parity.passed";
    const std::string phase = args.require("phase");
    std::string reference_hash;
    double max_distance_delta = 0.0;
    double max_recall_delta = 0.0;
    uint64_t query_comparisons = 0;
    if (phase == "validate" || phase == "validation") {
        if (!disk_mode) {
            write_reference(reference_path, sweeps);
            reference_hash = sha256(reference_path);
            // Measure an independent O_DIRECT reference outside reported timing.
            std::vector<SweepResult> disk_reference;
            for (const size_t width : widths) {
                disk_reference.push_back(run_saq_sweep(
                    model, index, true, {}, pca_queries, candidates, order,
                    candidate_width, width, args.number("workers")));
            }
            std::tie(max_distance_delta, query_comparisons) =
                    compare_reference(reference_path, disk_reference);
            if (max_distance_delta > 1e-5)
                throw std::runtime_error("resident/direct-disk distance parity exceeds 1e-5");
            write_parity_status(parity_path, reference_hash, max_distance_delta, 0.0, query_comparisons);
        } else {
            std::tie(max_distance_delta, query_comparisons) = compare_reference(reference_path, sweeps);
            reference_hash = sha256(reference_path);
            if (max_distance_delta > 1e-5) {
                throw std::runtime_error("SAQ resident/direct-disk distance parity exceeds 1e-5");
            }
            write_parity_status(parity_path, reference_hash, max_distance_delta, 0.0, query_comparisons);
        }
    } else if (phase == "test") {
        std::tie(reference_hash, max_distance_delta, max_recall_delta, query_comparisons) =
                read_parity_status(parity_path);
        if (sha256(reference_path) != reference_hash)
            throw std::runtime_error("parity reference changed; rerun validation");
    } else {
        throw std::runtime_error("unsupported SAQ phase " + phase);
    }
    write_artifact(
            args, index, sweeps, disk_mode,
            disk_mode ? 0 : resident.size(), reference_hash,
            max_distance_delta, max_recall_delta, query_comparisons);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        google::InitGoogleLogging(argv[0]);
        return run_saq(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << "\n";
        return 2;
    }
}
