#include <faiss/IndexHNSW.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;

struct Args {
    std::string dataset;
    fs::path data_root = "data";
    fs::path out_root = "results";
    fs::path candidate_root = "work";
    size_t candidate_size = 1000;
    size_t search_k = 2000;
    size_t max_queries = 0;
    size_t max_base = 0;
    int hnsw_m = 32;
    int ef_construction = 200;
    int ef_search = 2000;
    int seed = 20260813;
    bool force = false;
};

struct VecsInfo {
    size_t count = 0;
    size_t dim = 0;
    size_t row_size = 0;
    fs::path path;
};

struct Timer {
    using clock = std::chrono::steady_clock;
    clock::time_point start = clock::now();

    double seconds() const {
        return std::chrono::duration<double>(clock::now() - start).count();
    }
};

[[noreturn]] void usage(const char* argv0) {
    std::cerr
            << "Usage: " << argv0
            << " --dataset DATASET [--data-root data] [--out-root results]\n"
            << "       [--candidate-root work]\n"
            << "       [--candidate-size 1000] [--search-k 2000]\n"
            << "       [--max-queries 0] [--max-base 0] [--hnsw-M 32]\n"
            << "       [--efConstruction 200] [--efSearch 2000]\n"
            << "       [--seed 20260813] [--force]\n";
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
        } else if (key == "--search-k") {
            args.search_k = std::stoull(need_value(key));
        } else if (key == "--max-queries") {
            args.max_queries = std::stoull(need_value(key));
        } else if (key == "--max-base") {
            args.max_base = std::stoull(need_value(key));
        } else if (key == "--hnsw-M") {
            args.hnsw_m = std::stoi(need_value(key));
        } else if (key == "--efConstruction") {
            args.ef_construction = std::stoi(need_value(key));
        } else if (key == "--efSearch") {
            args.ef_search = std::stoi(need_value(key));
        } else if (key == "--seed") {
            args.seed = std::stoi(need_value(key));
        } else if (key == "--force") {
            args.force = true;
        } else if (key == "--help" || key == "-h") {
            usage(argv[0]);
        } else {
            throw std::runtime_error("unknown argument: " + key);
        }
    }
    if (args.dataset.empty()) {
        usage(argv[0]);
    }
    if (args.candidate_size == 0 || args.search_k == 0) {
        throw std::runtime_error("candidate-size/search-k must be positive");
    }
    if (args.search_k < args.candidate_size) {
        throw std::runtime_error("search-k must be at least candidate-size");
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

VecsInfo inspect_vecs(const fs::path& path, size_t value_size) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + path.string());
    }
    in.seekg(0, std::ios::end);
    const size_t size = static_cast<size_t>(in.tellg());
    const int32_t dim_i32 = read_i32_at(in, 0);
    if (dim_i32 <= 0) {
        throw std::runtime_error("invalid dimension in " + path.string());
    }
    const size_t dim = static_cast<size_t>(dim_i32);
    const size_t row_size = 4 + dim * value_size;
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

std::vector<std::vector<int32_t>> load_ivecs_prefix(const VecsInfo& info, size_t n) {
    n = std::min(n, info.count);
    std::ifstream in(info.path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + info.path.string());
    }
    std::vector<std::vector<int32_t>> rows;
    rows.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        int32_t dim = 0;
        in.read(reinterpret_cast<char*>(&dim), sizeof(dim));
        if (dim != static_cast<int32_t>(info.dim)) {
            throw std::runtime_error("dimension mismatch in " + info.path.string());
        }
        std::vector<int32_t> row(info.dim);
        in.read(reinterpret_cast<char*>(row.data()),
                static_cast<std::streamsize>(info.dim * sizeof(int32_t)));
        if (!in) {
            throw std::runtime_error("truncated ivecs file: " + info.path.string());
        }
        rows.push_back(std::move(row));
    }
    return rows;
}

uint64_t fnv1a_update(uint64_t hash, const void* data, size_t size) {
    const auto* bytes = static_cast<const uint8_t*>(data);
    for (size_t i = 0; i < size; ++i) {
        hash ^= bytes[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string hex_u64(uint64_t value) {
    std::ostringstream out;
    out << std::hex << value;
    return out.str();
}

int main(int argc, char** argv) {
    try {
        const Args args = parse_args(argc, argv);
        const fs::path dataset_dir = args.data_root / args.dataset;
        const fs::path base_path = dataset_dir / (args.dataset + "_base.fvecs");
        const fs::path query_path = dataset_dir / (args.dataset + "_query.fvecs");
        const fs::path gt_path = dataset_dir / (args.dataset + "_groundtruth.ivecs");

        const VecsInfo base_info = inspect_vecs(base_path, sizeof(float));
        const VecsInfo query_info = inspect_vecs(query_path, sizeof(float));
        const VecsInfo gt_info = inspect_vecs(gt_path, sizeof(int32_t));
        if (base_info.dim != query_info.dim) {
            throw std::runtime_error("base/query dimensions differ");
        }
        if (query_info.count != gt_info.count) {
            throw std::runtime_error("query/groundtruth counts differ");
        }
        const size_t indexed_base_count = args.max_base == 0
                ? base_info.count
                : std::min(args.max_base, base_info.count);
        if (args.candidate_size > indexed_base_count) {
            throw std::runtime_error("candidate-size exceeds indexed base count");
        }
        const size_t query_count = args.max_queries == 0
                ? query_info.count
                : std::min(args.max_queries, query_info.count);
        const size_t d = base_info.dim;
        const size_t search_k = std::min(args.search_k, indexed_base_count);

        const fs::path out_dir =
                args.candidate_root / "01_quantizer_fair" / args.dataset;
        const fs::path bin_path =
                out_dir / ("fixed_candidates_k" + std::to_string(args.candidate_size) + ".bin");
        const fs::path meta_path =
                out_dir / ("fixed_candidates_k" + std::to_string(args.candidate_size) + ".meta.json");
        if ((fs::exists(bin_path) || fs::exists(meta_path)) && !args.force) {
            throw std::runtime_error("candidate outputs exist; pass --force to overwrite");
        }

        std::cerr << "Loading base/query/groundtruth for " << args.dataset
                  << " base=" << indexed_base_count << "/" << base_info.count
                  << " query=" << query_count
                  << " dim=" << d << "\n";
        const std::vector<float> base = load_fvecs_prefix(base_info, indexed_base_count);
        const std::vector<float> queries = load_fvecs_prefix(query_info, query_count);
        const std::vector<std::vector<int32_t>> gt = load_ivecs_prefix(gt_info, query_count);

        faiss::IndexHNSWFlat index(d, args.hnsw_m, faiss::METRIC_L2);
        index.hnsw.efConstruction = args.ef_construction;
        index.hnsw.efSearch = args.ef_search;
        index.verbose = true;

        Timer build_timer;
        index.add(static_cast<faiss::idx_t>(indexed_base_count), base.data());
        const double build_seconds = build_timer.seconds();
        std::cerr << "FP32 HNSW build seconds=" << build_seconds << "\n";

        std::vector<faiss::idx_t> labels(query_count * search_k);
        std::vector<float> distances(query_count * search_k);
        Timer search_timer;
        index.search(
                static_cast<faiss::idx_t>(query_count),
                queries.data(),
                static_cast<faiss::idx_t>(search_k),
                distances.data(),
                labels.data());
        const double search_seconds = search_timer.seconds();
        std::cerr << "FP32 HNSW search seconds=" << search_seconds << "\n";

        fs::create_directories(out_dir);
        std::ofstream bin(bin_path, std::ios::binary);
        if (!bin) {
            throw std::runtime_error("cannot open output: " + bin_path.string());
        }

        std::mt19937_64 rng(static_cast<uint64_t>(args.seed));
        uint64_t fingerprint = 1469598103934665603ULL;
        size_t fallback_random_count = 0;
        size_t hnsw_negative_count = 0;
        for (size_t qi = 0; qi < query_count; ++qi) {
            std::vector<int32_t> row;
            row.reserve(args.candidate_size);
            std::unordered_set<int32_t> seen;
            seen.reserve(args.candidate_size * 2);

            for (int32_t id : gt[qi]) {
                if (id >= 0 && static_cast<size_t>(id) < indexed_base_count &&
                    seen.insert(id).second) {
                    row.push_back(id);
                    if (row.size() == args.candidate_size) {
                        break;
                    }
                }
            }

            for (size_t rank = 0; rank < search_k && row.size() < args.candidate_size; ++rank) {
                const faiss::idx_t label = labels[qi * search_k + rank];
                if (label < 0 || static_cast<size_t>(label) >= indexed_base_count) {
                    continue;
                }
                const int32_t id = static_cast<int32_t>(label);
                if (seen.insert(id).second) {
                    row.push_back(id);
                    ++hnsw_negative_count;
                }
            }

            while (row.size() < args.candidate_size) {
                const int32_t id = static_cast<int32_t>(rng() % indexed_base_count);
                if (seen.insert(id).second) {
                    row.push_back(id);
                    ++fallback_random_count;
                }
            }

            bin.write(
                    reinterpret_cast<const char*>(row.data()),
                    static_cast<std::streamsize>(row.size() * sizeof(int32_t)));
            fingerprint = fnv1a_update(
                    fingerprint, row.data(), row.size() * sizeof(int32_t));
        }
        bin.close();

        std::ofstream meta(meta_path);
        meta << "{\n"
             << "  \"dataset\": \"" << args.dataset << "\",\n"
             << "  \"status\": \"ok\",\n"
             << "  \"strategy\": \"groundtruth_then_fp32_hnsw_hard_negatives\",\n"
             << "  \"candidate_size\": " << args.candidate_size << ",\n"
             << "  \"base_count\": " << base_info.count << ",\n"
             << "  \"indexed_base_count\": " << indexed_base_count << ",\n"
             << "  \"query_count\": " << query_count << ",\n"
             << "  \"dimension\": " << d << ",\n"
             << "  \"groundtruth_k\": " << gt_info.dim << ",\n"
             << "  \"hard_negative_source\": \"faiss::IndexHNSWFlat_fp32\",\n"
             << "  \"hnsw_M\": " << args.hnsw_m << ",\n"
             << "  \"efConstruction\": " << args.ef_construction << ",\n"
             << "  \"efSearch\": " << args.ef_search << ",\n"
             << "  \"search_k\": " << search_k << ",\n"
             << "  \"seed\": " << args.seed << ",\n"
             << "  \"build_seconds\": " << build_seconds << ",\n"
             << "  \"search_seconds\": " << search_seconds << ",\n"
             << "  \"hnsw_negative_count\": " << hnsw_negative_count << ",\n"
             << "  \"fallback_random_count\": " << fallback_random_count << ",\n"
             << "  \"fingerprint_fnv1a64\": \"" << hex_u64(fingerprint) << "\",\n"
             << "  \"binary_format\": {\n"
             << "    \"dtype\": \"int32_little_endian\",\n"
             << "    \"shape\": [" << query_count << ", " << args.candidate_size << "],\n"
             << "    \"row_headers\": false\n"
             << "  },\n"
             << "  \"paths\": {\n"
             << "    \"candidate_bin\": \"" << bin_path.string() << "\",\n"
             << "    \"candidate_meta\": \"" << meta_path.string() << "\",\n"
             << "    \"out_root\": \"" << args.out_root.string() << "\",\n"
             << "    \"candidate_root\": \"" << args.candidate_root.string() << "\",\n"
             << "    \"base\": \"" << base_path.string() << "\",\n"
             << "    \"query\": \"" << query_path.string() << "\",\n"
             << "    \"groundtruth\": \"" << gt_path.string() << "\"\n"
             << "  },\n"
             << "  \"notes\": [\n"
             << "    \"Method-independent candidate set: every row starts with valid groundtruth ids, then fills with FP32 HNSW nearest-neighbor hard negatives.\",\n"
             << "    \"All quantizers must consume this exact candidate file; do not regenerate per method.\"\n"
             << "  ]\n"
             << "}\n";

        std::cout << "wrote " << bin_path << " and " << meta_path << "\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
}
