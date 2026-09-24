#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <immintrin.h>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <sched.h>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

constexpr std::size_t kInputDim = 960;
constexpr std::size_t kCodeDim = 1024;
constexpr std::size_t kWords = kCodeDim / 64;
constexpr std::size_t kBatch = 32;
constexpr std::size_t kBlockWords = kWords * kBatch;
constexpr std::size_t kBlockBytes = kBlockWords * sizeof(std::uint64_t);
constexpr std::uint32_t kDefaultSeed = 100;

static_assert(kCodeDim % 64 == 0);
static_assert(kBlockBytes == 4096);

struct Options {
    std::filesystem::path base = "data/gist/gist_base.fvecs";
    std::filesystem::path queries = "data/gist/gist_query.fvecs";
    std::filesystem::path cache = "/tmp/gist1m_fht_1bit_block32_v1.bin";
    std::filesystem::path csv = "results/04_query_codec_1bit_scan/gist/gist1m_query_codec_scan.csv";
    std::size_t data_count = 1'000'000;
    std::size_t query_count = 10;
    std::size_t rounds = 3;
    std::vector<std::size_t> prefetch_blocks{0, 8};
    std::uint32_t seed = kDefaultSeed;
    int cpu = 0;
    bool write_cache = true;
};

[[noreturn]] void usage(const char *program, int status) {
    std::ostream &out = status == 0 ? std::cout : std::cerr;
    out << "Usage: " << program << " [options]\n"
        << "  --base PATH               GIST base .fvecs\n"
        << "  --queries PATH            GIST query .fvecs\n"
        << "  --cache PATH              cached blocked 1-bit database\n"
        << "  --csv PATH                per-scan CSV output\n"
        << "  --data-count N            database rows; multiple of 32\n"
        << "  --query-count N           number of queries (default 10)\n"
        << "  --rounds N                measured repetitions (default 3)\n"
        << "  --prefetch-blocks LIST    comma list, e.g. 0,8\n"
        << "  --seed N                  random FHT sign seed\n"
        << "  --cpu N                   pin the single benchmark thread\n"
        << "  --no-cache-write          do not persist generated DB bits\n"
        << "  --help\n";
    std::exit(status);
}

std::size_t parse_size(std::string_view value, std::string_view flag) {
    std::size_t consumed = 0;
    const auto parsed = std::stoull(std::string(value), &consumed);
    if (consumed != value.size()) {
        throw std::invalid_argument("invalid value for " + std::string(flag));
    }
    return static_cast<std::size_t>(parsed);
}

std::vector<std::size_t> parse_size_list(std::string_view value) {
    std::vector<std::size_t> result;
    std::size_t start = 0;
    while (start <= value.size()) {
        const std::size_t comma = value.find(',', start);
        const std::size_t end = comma == std::string_view::npos ? value.size() : comma;
        if (end == start) throw std::invalid_argument("empty prefetch value");
        result.push_back(parse_size(value.substr(start, end - start), "--prefetch-blocks"));
        if (comma == std::string_view::npos) break;
        start = comma + 1;
    }
    std::sort(result.begin(), result.end());
    result.erase(std::unique(result.begin(), result.end()), result.end());
    return result;
}

Options parse_options(int argc, char **argv) {
    Options options;
    auto need_value = [&](int &i, std::string_view flag) -> std::string_view {
        if (++i >= argc) throw std::invalid_argument("missing value for " + std::string(flag));
        return argv[i];
    };
    for (int i = 1; i < argc; ++i) {
        const std::string_view flag = argv[i];
        if (flag == "--help") usage(argv[0], 0);
        if (flag == "--base") options.base = need_value(i, flag);
        else if (flag == "--queries") options.queries = need_value(i, flag);
        else if (flag == "--cache") options.cache = need_value(i, flag);
        else if (flag == "--csv") options.csv = need_value(i, flag);
        else if (flag == "--data-count") options.data_count = parse_size(need_value(i, flag), flag);
        else if (flag == "--query-count") options.query_count = parse_size(need_value(i, flag), flag);
        else if (flag == "--rounds") options.rounds = parse_size(need_value(i, flag), flag);
        else if (flag == "--prefetch-blocks") options.prefetch_blocks = parse_size_list(need_value(i, flag));
        else if (flag == "--seed") options.seed = static_cast<std::uint32_t>(parse_size(need_value(i, flag), flag));
        else if (flag == "--cpu") options.cpu = static_cast<int>(parse_size(need_value(i, flag), flag));
        else if (flag == "--no-cache-write") options.write_cache = false;
        else throw std::invalid_argument("unknown option: " + std::string(flag));
    }
    if (options.data_count == 0 || options.data_count % kBatch != 0) {
        throw std::invalid_argument("--data-count must be a nonzero multiple of 32");
    }
    if (options.query_count == 0 || options.rounds == 0 || options.prefetch_blocks.empty()) {
        throw std::invalid_argument("query count, rounds, and prefetch list must be nonempty");
    }
    return options;
}

void pin_to_cpu(int cpu) {
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (::sched_setaffinity(0, sizeof(set), &set) != 0) {
        throw std::runtime_error("sched_setaffinity(cpu=" + std::to_string(cpu) + ") failed: " +
            std::strerror(errno));
    }
}

template <typename T>
class AlignedBuffer {
 public:
    AlignedBuffer() = default;
    explicit AlignedBuffer(std::size_t count) { reset(count); }
    AlignedBuffer(const AlignedBuffer &) = delete;
    AlignedBuffer &operator=(const AlignedBuffer &) = delete;
    AlignedBuffer(AlignedBuffer &&other) noexcept : data_(other.data_), size_(other.size_) {
        other.data_ = nullptr;
        other.size_ = 0;
    }
    AlignedBuffer &operator=(AlignedBuffer &&other) noexcept {
        if (this != &other) {
            std::free(data_);
            data_ = other.data_;
            size_ = other.size_;
            other.data_ = nullptr;
            other.size_ = 0;
        }
        return *this;
    }
    ~AlignedBuffer() { std::free(data_); }

    void reset(std::size_t count) {
        std::free(data_);
        data_ = nullptr;
        size_ = 0;
        if (count == 0) return;
        void *raw = nullptr;
        constexpr std::size_t kAlignment = 2U * 1024U * 1024U;
        if (::posix_memalign(&raw, kAlignment, count * sizeof(T)) != 0) {
            throw std::bad_alloc();
        }
        data_ = static_cast<T *>(raw);
        size_ = count;
#ifdef MADV_HUGEPAGE
        ::madvise(data_, size_ * sizeof(T), MADV_HUGEPAGE);
#endif
    }
    T *data() { return data_; }
    const T *data() const { return data_; }
    std::size_t size() const { return size_; }
    T &operator[](std::size_t i) { return data_[i]; }
    const T &operator[](std::size_t i) const { return data_[i]; }

 private:
    T *data_ = nullptr;
    std::size_t size_ = 0;
};

struct MappedFile {
    int fd = -1;
    const std::uint8_t *data = nullptr;
    std::size_t size = 0;

    explicit MappedFile(const std::filesystem::path &path) {
        fd = ::open(path.c_str(), O_RDONLY);
        if (fd < 0) throw std::runtime_error("cannot open " + path.string() + ": " + std::strerror(errno));
        struct stat st {};
        if (::fstat(fd, &st) != 0) {
            const std::string message = std::strerror(errno);
            ::close(fd);
            fd = -1;
            throw std::runtime_error("cannot stat " + path.string() + ": " + message);
        }
        size = static_cast<std::size_t>(st.st_size);
        void *mapping = ::mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
        if (mapping == MAP_FAILED) {
            const std::string message = std::strerror(errno);
            ::close(fd);
            fd = -1;
            throw std::runtime_error("cannot mmap " + path.string() + ": " + message);
        }
        data = static_cast<const std::uint8_t *>(mapping);
#ifdef MADV_SEQUENTIAL
        ::madvise(const_cast<std::uint8_t *>(data), size, MADV_SEQUENTIAL);
#endif
    }
    MappedFile(const MappedFile &) = delete;
    MappedFile &operator=(const MappedFile &) = delete;
    ~MappedFile() {
        if (data) ::munmap(const_cast<std::uint8_t *>(data), size);
        if (fd >= 0) ::close(fd);
    }
};

struct alignas(64) CacheHeader {
    char magic[16];
    std::uint32_t version;
    std::uint32_t input_dim;
    std::uint32_t code_dim;
    std::uint32_t batch;
    std::uint64_t data_count;
    std::uint64_t source_size;
    std::uint64_t seed;
    std::uint64_t payload_bytes;
};
static_assert(sizeof(CacheHeader) == 64);

std::vector<float> make_fht_signs(std::uint32_t seed) {
    std::vector<float> signs(kCodeDim);
    std::mt19937 rng(seed);
    std::uniform_int_distribution<int> bernoulli(0, 1);
    const float scale = 1.0f / std::sqrt(static_cast<float>(kCodeDim));
    for (float &value : signs) value = (bernoulli(rng) ? scale : -scale);
    return signs;
}

void hadamard(std::vector<float> &values) {
    for (std::size_t step = 1; step < kCodeDim; step <<= 1U) {
        for (std::size_t block = 0; block < kCodeDim; block += step << 1U) {
            std::size_t i = 0;
#if defined(__AVX512F__)
            if (step >= 16) {
                for (; i + 16 <= step; i += 16) {
                    const __m512 a = _mm512_loadu_ps(values.data() + block + i);
                    const __m512 b = _mm512_loadu_ps(values.data() + block + step + i);
                    _mm512_storeu_ps(values.data() + block + i, _mm512_add_ps(a, b));
                    _mm512_storeu_ps(values.data() + block + step + i, _mm512_sub_ps(a, b));
                }
            }
#endif
            for (; i < step; ++i) {
                const float a = values[block + i];
                const float b = values[block + step + i];
                values[block + i] = a + b;
                values[block + step + i] = a - b;
            }
        }
    }
}

std::vector<float> rotate(const float *raw, const std::vector<float> &signs) {
    std::vector<float> result(kCodeDim, 0.0f);
    for (std::size_t i = 0; i < kInputDim; ++i) result[i] = raw[i] * signs[i];
    hadamard(result);
    return result;
}

std::uint32_t load_u32(const void *address) {
    std::uint32_t value;
    std::memcpy(&value, address, sizeof(value));
    return value;
}

float load_f32(const void *address) {
    float value;
    std::memcpy(&value, address, sizeof(value));
    return value;
}

bool load_cache(
    const Options &options,
    std::uint64_t source_size,
    AlignedBuffer<std::uint64_t> &database) {
    std::ifstream input(options.cache, std::ios::binary);
    if (!input) return false;
    CacheHeader header{};
    input.read(reinterpret_cast<char *>(&header), sizeof(header));
    const std::uint64_t expected_bytes = options.data_count * kWords * sizeof(std::uint64_t);
    const bool valid = input && std::memcmp(header.magic, "GIST1M1BITBLK1", 15) == 0 &&
        header.version == 1 && header.input_dim == kInputDim && header.code_dim == kCodeDim &&
        header.batch == kBatch && header.data_count == options.data_count &&
        header.source_size == source_size && header.seed == options.seed &&
        header.payload_bytes == expected_bytes;
    if (!valid) return false;
    database.reset(expected_bytes / sizeof(std::uint64_t));
    input.read(reinterpret_cast<char *>(database.data()), static_cast<std::streamsize>(expected_bytes));
    if (!input) throw std::runtime_error("truncated cache: " + options.cache.string());
    std::cerr << "Loaded 1-bit DB cache: " << options.cache << " ("
              << expected_bytes / 1'000'000.0 << " MB)\n";
    return true;
}

void save_cache(
    const Options &options,
    std::uint64_t source_size,
    const AlignedBuffer<std::uint64_t> &database) {
    if (!options.write_cache) return;
    if (!options.cache.parent_path().empty()) {
        std::filesystem::create_directories(options.cache.parent_path());
    }
    CacheHeader header{};
    std::memcpy(header.magic, "GIST1M1BITBLK1", 15);
    header.version = 1;
    header.input_dim = kInputDim;
    header.code_dim = kCodeDim;
    header.batch = kBatch;
    header.data_count = options.data_count;
    header.source_size = source_size;
    header.seed = options.seed;
    header.payload_bytes = database.size() * sizeof(std::uint64_t);
    std::ofstream output(options.cache, std::ios::binary | std::ios::trunc);
    if (!output) throw std::runtime_error("cannot create cache: " + options.cache.string());
    output.write(reinterpret_cast<const char *>(&header), sizeof(header));
    output.write(reinterpret_cast<const char *>(database.data()),
        static_cast<std::streamsize>(header.payload_bytes));
    if (!output) throw std::runtime_error("failed writing cache: " + options.cache.string());
    std::cerr << "Saved 1-bit DB cache: " << options.cache << "\n";
}

AlignedBuffer<std::uint64_t> build_or_load_database(
    const Options &options,
    const std::vector<float> &signs,
    double &build_seconds) {
    const MappedFile source(options.base);
    constexpr std::size_t row_bytes = sizeof(std::uint32_t) + kInputDim * sizeof(float);
    if (source.size % row_bytes != 0 || options.data_count > source.size / row_bytes) {
        throw std::runtime_error("base fvecs size/count mismatch");
    }
    AlignedBuffer<std::uint64_t> database;
    const auto start = Clock::now();
    if (load_cache(options, source.size, database)) {
        build_seconds = std::chrono::duration<double>(Clock::now() - start).count();
        return database;
    }

    database.reset(options.data_count * kWords);
    std::vector<float> raw(kInputDim);
    for (std::size_t row = 0; row < options.data_count; ++row) {
        const std::uint8_t *record = source.data + row * row_bytes;
        if (load_u32(record) != kInputDim) {
            throw std::runtime_error("base fvecs dimension changed at row " + std::to_string(row));
        }
        for (std::size_t i = 0; i < kInputDim; ++i) {
            raw[i] = load_f32(record + sizeof(std::uint32_t) + i * sizeof(float));
        }
        const std::vector<float> transformed = rotate(raw.data(), signs);
        const std::size_t block = row / kBatch;
        const std::size_t lane = row % kBatch;
        for (std::size_t word = 0; word < kWords; ++word) {
            std::uint64_t bits = 0;
            for (std::size_t bit = 0; bit < 64; ++bit) {
                if (transformed[word * 64 + bit] > 0.0f) bits |= std::uint64_t{1} << bit;
            }
            database[(block * kWords + word) * kBatch + lane] = bits;
        }
        if ((row + 1) % 100'000 == 0) {
            std::cerr << "Encoded " << row + 1 << '/' << options.data_count << " base vectors\n";
        }
    }
    build_seconds = std::chrono::duration<double>(Clock::now() - start).count();
    save_cache(options, source.size, database);
    return database;
}

std::vector<std::vector<float>> load_queries(const std::filesystem::path &path, std::size_t count) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot open queries: " + path.string());
    std::vector<std::vector<float>> queries;
    queries.reserve(count);
    for (std::size_t row = 0; row < count; ++row) {
        std::uint32_t dim = 0;
        input.read(reinterpret_cast<char *>(&dim), sizeof(dim));
        if (!input || dim != kInputDim) {
            throw std::runtime_error("query fvecs missing/wrong dimension at row " + std::to_string(row));
        }
        queries.emplace_back(kInputDim);
        input.read(reinterpret_cast<char *>(queries.back().data()),
            static_cast<std::streamsize>(kInputDim * sizeof(float)));
        if (!input) throw std::runtime_error("truncated query fvecs");
    }
    return queries;
}

struct QueryRepresentations {
    std::vector<float> fp32;
    std::array<std::uint64_t, kWords> b1{};
    std::vector<std::uint64_t> int4_planes;
    std::vector<std::uint64_t> int8_planes;
    float fp32_sum = 0.0f;
    float fp32_norm2 = 0.0f;
    float b1_scale = 1.0f;
    float b1_constant = 0.0f;
    float int4_scale = 1.0f;
    float int8_scale = 1.0f;
    float int4_constant = 0.0f;
    float int8_constant = 0.0f;
    std::int64_t int4_sum = 0;
    std::int64_t int8_sum = 0;
    double rotate_us = 0.0;
    double fp32_prepare_us = 0.0;
    double b1_prepare_us = 0.0;
    double int4_prepare_us = 0.0;
    double int8_prepare_us = 0.0;
};

template <int Bits>
void make_integer_query(
    const std::vector<float> &query,
    std::vector<std::uint64_t> &planes,
    float &scale,
    float &distance_constant,
    std::int64_t &code_sum) {
    constexpr int qmax = (1 << (Bits - 1)) - 1;
    float max_abs = 0.0f;
    for (float value : query) max_abs = std::max(max_abs, std::abs(value));
    scale = max_abs > 0.0f ? max_abs / static_cast<float>(qmax) : 1.0f;
    planes.assign(Bits * kWords, 0);
    code_sum = 0;
    std::int64_t code_norm2 = 0;
    for (std::size_t i = 0; i < kCodeDim; ++i) {
        int code = static_cast<int>(std::lrint(query[i] / scale));
        code = std::clamp(code, -qmax, qmax);
        code_sum += code;
        code_norm2 += static_cast<std::int64_t>(code) * code;
        const std::uint8_t encoded = static_cast<std::uint8_t>(static_cast<std::int8_t>(code));
        for (int plane = 0; plane < Bits; ++plane) {
            if ((encoded >> plane) & 1U) {
                planes[static_cast<std::size_t>(plane) * kWords + i / 64] |=
                    std::uint64_t{1} << (i % 64);
            }
        }
    }
    distance_constant = static_cast<float>(kCodeDim) +
        scale * scale * static_cast<float>(code_norm2);
}

QueryRepresentations prepare_query(const float *raw, const std::vector<float> &signs) {
    QueryRepresentations result;
    auto start = Clock::now();
    result.fp32 = rotate(raw, signs);
    result.rotate_us = std::chrono::duration<double, std::micro>(Clock::now() - start).count();

    start = Clock::now();
    for (float value : result.fp32) {
        result.fp32_sum += value;
        result.fp32_norm2 += value * value;
    }
    result.fp32_prepare_us = std::chrono::duration<double, std::micro>(Clock::now() - start).count();

    start = Clock::now();
    float abs_sum = 0.0f;
    for (std::size_t i = 0; i < kCodeDim; ++i) {
        if (result.fp32[i] > 0.0f) result.b1[i / 64] |= std::uint64_t{1} << (i % 64);
        abs_sum += std::abs(result.fp32[i]);
    }
    result.b1_scale = abs_sum / static_cast<float>(kCodeDim);
    result.b1_constant = static_cast<float>(kCodeDim) *
        (1.0f + result.b1_scale * result.b1_scale);
    result.b1_prepare_us = std::chrono::duration<double, std::micro>(Clock::now() - start).count();

    start = Clock::now();
    make_integer_query<4>(result.fp32, result.int4_planes, result.int4_scale,
        result.int4_constant, result.int4_sum);
    result.int4_prepare_us = std::chrono::duration<double, std::micro>(Clock::now() - start).count();

    start = Clock::now();
    make_integer_query<8>(result.fp32, result.int8_planes, result.int8_scale,
        result.int8_constant, result.int8_sum);
    result.int8_prepare_us = std::chrono::duration<double, std::micro>(Clock::now() - start).count();
    return result;
}

inline __m512i popcount_bytes_to_u64(__m512i value, __m512i lut, __m512i nibble_mask) {
    const __m512i lo = _mm512_and_si512(value, nibble_mask);
    const __m512i hi = _mm512_and_si512(_mm512_srli_epi16(value, 4), nibble_mask);
    const __m512i bytes = _mm512_add_epi8(
        _mm512_shuffle_epi8(lut, lo), _mm512_shuffle_epi8(lut, hi));
    return _mm512_sad_epu8(bytes, _mm512_setzero_si512());
}

inline __m512i popcount_lut() {
    const __m128i lut128 = _mm_setr_epi8(
        0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4);
    return _mm512_broadcast_i32x4(lut128);
}

inline void prefetch_db_word(
    const std::uint64_t *database,
    std::size_t block,
    std::size_t word,
    std::size_t block_count,
    std::size_t lookahead) {
    if (lookahead == 0 || block + lookahead >= block_count) return;
    const char *address = reinterpret_cast<const char *>(
        database + ((block + lookahead) * kWords + word) * kBatch);
    _mm_prefetch(address + 0, _MM_HINT_T1);
    _mm_prefetch(address + 64, _MM_HINT_T1);
    _mm_prefetch(address + 128, _MM_HINT_T1);
    _mm_prefetch(address + 192, _MM_HINT_T1);
}

#if defined(__GNUC__) || defined(__clang__)
#define NOINLINE __attribute__((noinline))
#else
#define NOINLINE
#endif

NOINLINE void scan_fp32(
    const std::uint64_t *database,
    std::size_t count,
    const QueryRepresentations &query,
    float *distances,
    std::size_t prefetch_blocks) {
    const std::size_t block_count = count / kBatch;
    const float constant = static_cast<float>(kCodeDim) + query.fp32_norm2;
    for (std::size_t block = 0; block < block_count; ++block) {
        const std::uint64_t *base = database + block * kBlockWords;
        for (std::size_t group = 0; group < 2; ++group) {
            __m512 acc[16];
            for (auto &lane : acc) lane = _mm512_setzero_ps();
            for (std::size_t word = 0; word < kWords; ++word) {
                if (group == 0) prefetch_db_word(database, block, word, block_count, prefetch_blocks);
                const std::uint64_t *db_words = base + word * kBatch + group * 16;
                for (std::size_t quarter = 0; quarter < 4; ++quarter) {
                    const __m512 q = _mm512_loadu_ps(
                        query.fp32.data() + word * 64 + quarter * 16);
                    const unsigned shift = static_cast<unsigned>(quarter * 16);
                    for (std::size_t candidate = 0; candidate < 16; ++candidate) {
                        const __mmask16 mask = static_cast<__mmask16>(db_words[candidate] >> shift);
                        acc[candidate] = _mm512_add_ps(
                            acc[candidate], _mm512_maskz_mov_ps(mask, q));
                    }
                }
            }
            for (std::size_t candidate = 0; candidate < 16; ++candidate) {
                const float selected = _mm512_reduce_add_ps(acc[candidate]);
                const float dot = 2.0f * selected - query.fp32_sum;
                distances[block * kBatch + group * 16 + candidate] = constant - 2.0f * dot;
            }
        }
    }
}

NOINLINE void scan_b1(
    const std::uint64_t *database,
    std::size_t count,
    const QueryRepresentations &query,
    float *distances,
    std::size_t prefetch_blocks) {
    const std::size_t block_count = count / kBatch;
    const __m512i lut = popcount_lut();
    const __m512i nibble_mask = _mm512_set1_epi8(0x0f);
    for (std::size_t block = 0; block < block_count; ++block) {
        __m512i mismatch[4] = {
            _mm512_setzero_si512(), _mm512_setzero_si512(),
            _mm512_setzero_si512(), _mm512_setzero_si512()};
        const std::uint64_t *base = database + block * kBlockWords;
        for (std::size_t word = 0; word < kWords; ++word) {
            prefetch_db_word(database, block, word, block_count, prefetch_blocks);
            const __m512i q = _mm512_set1_epi64(static_cast<long long>(query.b1[word]));
            for (std::size_t group = 0; group < 4; ++group) {
                const __m512i db = _mm512_loadu_si512(base + word * kBatch + group * 8);
                mismatch[group] = _mm512_add_epi64(
                    mismatch[group], popcount_bytes_to_u64(_mm512_xor_si512(db, q), lut, nibble_mask));
            }
        }
        for (std::size_t group = 0; group < 4; ++group) {
            alignas(64) std::uint64_t lanes[8];
            _mm512_store_si512(lanes, mismatch[group]);
            for (std::size_t lane = 0; lane < 8; ++lane) {
                const float signed_dot = static_cast<float>(kCodeDim) - 2.0f * lanes[lane];
                distances[block * kBatch + group * 8 + lane] =
                    query.b1_constant - 2.0f * query.b1_scale * signed_dot;
            }
        }
    }
}

template <int Bits>
NOINLINE void scan_integer(
    const std::uint64_t *database,
    std::size_t count,
    const std::vector<std::uint64_t> &query_planes,
    std::int64_t query_sum,
    float query_scale,
    float distance_constant,
    float *distances,
    std::size_t prefetch_blocks) {
    const std::size_t block_count = count / kBatch;
    const __m512i lut = popcount_lut();
    const __m512i nibble_mask = _mm512_set1_epi8(0x0f);
    __m256i weights[Bits];
    for (int plane = 0; plane < Bits; ++plane) {
        const int weight = plane + 1 == Bits ? -(1 << (Bits - 1)) : (1 << plane);
        weights[plane] = _mm256_set1_epi32(weight);
    }
    for (std::size_t block = 0; block < block_count; ++block) {
        __m256i selected[4] = {
            _mm256_setzero_si256(), _mm256_setzero_si256(),
            _mm256_setzero_si256(), _mm256_setzero_si256()};
        const std::uint64_t *base = database + block * kBlockWords;
        for (std::size_t word = 0; word < kWords; ++word) {
            prefetch_db_word(database, block, word, block_count, prefetch_blocks);
            for (std::size_t group = 0; group < 4; ++group) {
                const __m512i db = _mm512_loadu_si512(base + word * kBatch + group * 8);
                for (int plane = 0; plane < Bits; ++plane) {
                    const __m512i q = _mm512_set1_epi64(static_cast<long long>(
                        query_planes[static_cast<std::size_t>(plane) * kWords + word]));
                    const __m512i counts64 = popcount_bytes_to_u64(
                        _mm512_and_si512(db, q), lut, nibble_mask);
                    const __m256i counts32 = _mm512_cvtepi64_epi32(counts64);
                    selected[group] = _mm256_add_epi32(
                        selected[group], _mm256_mullo_epi32(counts32, weights[plane]));
                }
            }
        }
        for (std::size_t group = 0; group < 4; ++group) {
            alignas(32) std::int32_t lanes[8];
            _mm256_store_si256(reinterpret_cast<__m256i *>(lanes), selected[group]);
            for (std::size_t lane = 0; lane < 8; ++lane) {
                const std::int64_t integer_dot = 2LL * lanes[lane] - query_sum;
                distances[block * kBatch + group * 8 + lane] = distance_constant -
                    2.0f * query_scale * static_cast<float>(integer_dot);
            }
        }
    }
}

std::uint64_t database_word(
    const std::uint64_t *database,
    std::size_t candidate,
    std::size_t word) {
    const std::size_t block = candidate / kBatch;
    const std::size_t lane = candidate % kBatch;
    return database[(block * kWords + word) * kBatch + lane];
}

template <int Bits>
int decode_query_code(const std::vector<std::uint64_t> &planes, std::size_t dim) {
    unsigned value = 0;
    for (int plane = 0; plane < Bits; ++plane) {
        value |= static_cast<unsigned>((planes[static_cast<std::size_t>(plane) * kWords + dim / 64]
            >> (dim % 64)) & 1U) << plane;
    }
    if (value & (1U << (Bits - 1))) value |= ~((1U << Bits) - 1U);
    return static_cast<int>(value);
}

void validate_kernels(
    const std::uint64_t *database,
    std::size_t count,
    const QueryRepresentations &query,
    AlignedBuffer<float> &distances) {
    const std::size_t checked = std::min<std::size_t>(count, 64);
    scan_fp32(database, count, query, distances.data(), 0);
    for (std::size_t candidate = 0; candidate < checked; ++candidate) {
        float dot = 0.0f;
        for (std::size_t i = 0; i < kCodeDim; ++i) {
            const int sign = ((database_word(database, candidate, i / 64) >> (i % 64)) & 1U) ? 1 : -1;
            dot += query.fp32[i] * sign;
        }
        const float expected = static_cast<float>(kCodeDim) + query.fp32_norm2 - 2.0f * dot;
        if (std::abs(expected - distances[candidate]) > 2e-3f * std::max(1.0f, std::abs(expected))) {
            throw std::runtime_error("FP32 SIMD validation failed");
        }
    }

    scan_b1(database, count, query, distances.data(), 0);
    for (std::size_t candidate = 0; candidate < checked; ++candidate) {
        std::uint64_t mismatches = 0;
        for (std::size_t word = 0; word < kWords; ++word) {
            mismatches += static_cast<std::uint64_t>(__builtin_popcountll(
                database_word(database, candidate, word) ^ query.b1[word]));
        }
        const float signed_dot = static_cast<float>(kCodeDim) - 2.0f * mismatches;
        const float expected = query.b1_constant - 2.0f * query.b1_scale * signed_dot;
        if (std::abs(expected - distances[candidate]) > 1e-4f * std::max(1.0f, std::abs(expected))) {
            throw std::runtime_error("B1 SIMD validation failed");
        }
    }

    scan_integer<4>(database, count, query.int4_planes, query.int4_sum,
        query.int4_scale, query.int4_constant, distances.data(), 0);
    for (std::size_t candidate = 0; candidate < checked; ++candidate) {
        std::int64_t dot = 0;
        for (std::size_t i = 0; i < kCodeDim; ++i) {
            const int sign = ((database_word(database, candidate, i / 64) >> (i % 64)) & 1U) ? 1 : -1;
            dot += static_cast<std::int64_t>(decode_query_code<4>(query.int4_planes, i)) * sign;
        }
        const float expected = query.int4_constant - 2.0f * query.int4_scale * static_cast<float>(dot);
        if (std::abs(expected - distances[candidate]) > 1e-4f * std::max(1.0f, std::abs(expected))) {
            throw std::runtime_error("INT4 SIMD validation failed");
        }
    }

    scan_integer<8>(database, count, query.int8_planes, query.int8_sum,
        query.int8_scale, query.int8_constant, distances.data(), 0);
    for (std::size_t candidate = 0; candidate < checked; ++candidate) {
        std::int64_t dot = 0;
        for (std::size_t i = 0; i < kCodeDim; ++i) {
            const int sign = ((database_word(database, candidate, i / 64) >> (i % 64)) & 1U) ? 1 : -1;
            dot += static_cast<std::int64_t>(decode_query_code<8>(query.int8_planes, i)) * sign;
        }
        const float expected = query.int8_constant - 2.0f * query.int8_scale * static_cast<float>(dot);
        if (std::abs(expected - distances[candidate]) > 1e-4f * std::max(1.0f, std::abs(expected))) {
            throw std::runtime_error("INT8 SIMD validation failed");
        }
    }
    std::cerr << "Scalar/SIMD validation passed for all four codecs\n";
}

enum class Codec : int { FP32 = 0, B1 = 1, INT4 = 2, INT8 = 3 };

const char *codec_name(Codec codec) {
    switch (codec) {
        case Codec::FP32: return "fp32";
        case Codec::B1: return "b1";
        case Codec::INT4: return "int4";
        case Codec::INT8: return "int8";
    }
    return "unknown";
}

void run_codec(
    Codec codec,
    const std::uint64_t *database,
    std::size_t count,
    const QueryRepresentations &query,
    float *distances,
    std::size_t prefetch_blocks) {
    switch (codec) {
        case Codec::FP32:
            scan_fp32(database, count, query, distances, prefetch_blocks);
            return;
        case Codec::B1:
            scan_b1(database, count, query, distances, prefetch_blocks);
            return;
        case Codec::INT4:
            scan_integer<4>(database, count, query.int4_planes, query.int4_sum,
                query.int4_scale, query.int4_constant, distances, prefetch_blocks);
            return;
        case Codec::INT8:
            scan_integer<8>(database, count, query.int8_planes, query.int8_sum,
                query.int8_scale, query.int8_constant, distances, prefetch_blocks);
            return;
    }
}

double sampled_checksum(const float *distances, std::size_t count) {
    double checksum = 0.0;
    constexpr std::size_t samples = 1024;
    for (std::size_t i = 0; i < samples; ++i) {
        const std::size_t index = (i * 104729ULL) % count;
        checksum += distances[index] * static_cast<double>((i % 17) + 1);
    }
    return checksum;
}

struct ScanResult {
    std::size_t query = 0;
    std::size_t round = 0;
    std::size_t prefetch_blocks = 0;
    Codec codec = Codec::FP32;
    double scan_ms = 0.0;
    double checksum = 0.0;
};

struct PrepResult {
    std::size_t query = 0;
    double rotate_us = 0.0;
    double fp32_us = 0.0;
    double b1_us = 0.0;
    double int4_us = 0.0;
    double int8_us = 0.0;
};

double percentile(std::vector<double> values, double quantile) {
    std::sort(values.begin(), values.end());
    const std::size_t index = static_cast<std::size_t>(
        std::ceil(quantile * static_cast<double>(values.size()))) - 1;
    return values[std::min(index, values.size() - 1)];
}

void write_csv(const Options &options, const std::vector<ScanResult> &results) {
    if (!options.csv.parent_path().empty()) std::filesystem::create_directories(options.csv.parent_path());
    std::ofstream output(options.csv, std::ios::trunc);
    if (!output) throw std::runtime_error("cannot create CSV: " + options.csv.string());
    output << "query,round,prefetch_blocks,codec,data_count,scan_ms,ns_per_distance,million_distances_per_s,checksum\n";
    output << std::setprecision(10);
    for (const ScanResult &result : results) {
        const double ns_per_distance = result.scan_ms * 1e6 / static_cast<double>(options.data_count);
        const double million_per_s = static_cast<double>(options.data_count) / (result.scan_ms * 1000.0);
        output << result.query << ',' << result.round << ',' << result.prefetch_blocks << ','
               << codec_name(result.codec) << ',' << options.data_count << ',' << result.scan_ms << ','
               << ns_per_distance << ',' << million_per_s << ',' << result.checksum << '\n';
    }
}

void print_summary(const Options &options, const std::vector<ScanResult> &results,
    const std::vector<PrepResult> &prep, double db_seconds) {
    std::cout << "\nConfiguration\n"
              << "  data=" << options.data_count << " x " << kCodeDim << " bits ("
              << options.data_count * kCodeDim / 8.0 / 1e6 << " MB), queries="
              << options.query_count << ", rounds=" << options.rounds << ", batch=" << kBatch
              << ", CPU=" << options.cpu << "\n"
              << "  DB cache load/build seconds=" << std::fixed << std::setprecision(3) << db_seconds << "\n\n";
    std::cout << "Scan results (each sample is one query scanned over all data)\n";
    std::cout << std::left << std::setw(8) << "codec" << std::right << std::setw(10) << "prefetch"
              << std::setw(12) << "mean_ms" << std::setw(12) << "median_ms"
              << std::setw(12) << "p95_ms" << std::setw(14) << "ns/dist"
              << std::setw(14) << "Mdist/s" << '\n';
    for (std::size_t prefetch : options.prefetch_blocks) {
        for (int codec_value = 0; codec_value < 4; ++codec_value) {
            const Codec codec = static_cast<Codec>(codec_value);
            std::vector<double> samples;
            for (const ScanResult &result : results) {
                if (result.prefetch_blocks == prefetch && result.codec == codec) samples.push_back(result.scan_ms);
            }
            const double mean = std::accumulate(samples.begin(), samples.end(), 0.0) / samples.size();
            const double median = percentile(samples, 0.5);
            const double p95 = percentile(samples, 0.95);
            const double ns = median * 1e6 / static_cast<double>(options.data_count);
            const double throughput = static_cast<double>(options.data_count) / (median * 1000.0);
            std::cout << std::left << std::setw(8) << codec_name(codec) << std::right << std::setw(10) << prefetch
                      << std::setw(12) << std::fixed << std::setprecision(3) << mean
                      << std::setw(12) << median << std::setw(12) << p95
                      << std::setw(14) << ns << std::setw(14) << throughput << '\n';
        }
    }

    auto prep_median = [&](auto field) {
        std::vector<double> values;
        for (const PrepResult &row : prep) values.push_back(row.*field);
        return percentile(values, 0.5);
    };
    std::cout << "\nMedian query preparation (one query at a time)\n"
              << "  common FHT rotation: " << prep_median(&PrepResult::rotate_us) << " us\n"
              << "  fp32 metadata:       " << prep_median(&PrepResult::fp32_us) << " us\n"
              << "  b1 quantization:     " << prep_median(&PrepResult::b1_us) << " us\n"
              << "  int4 bit planes:     " << prep_median(&PrepResult::int4_us) << " us\n"
              << "  int8 bit planes:     " << prep_median(&PrepResult::int8_us) << " us\n"
              << "\nPer-scan CSV: " << options.csv << "\n";
}

}  // namespace

int main(int argc, char **argv) {
    try {
        const Options options = parse_options(argc, argv);
#if !defined(__AVX512F__) || !defined(__AVX512BW__)
        throw std::runtime_error("benchmark must be compiled with AVX-512F/BW enabled");
#else
        if (!__builtin_cpu_supports("avx512f") || !__builtin_cpu_supports("avx512bw")) {
            throw std::runtime_error("CPU does not support AVX-512F/BW");
        }
#endif
        pin_to_cpu(options.cpu);
        std::cerr << "Pinned benchmark thread to CPU " << options.cpu << "\n";

        const std::vector<float> signs = make_fht_signs(options.seed);
        double db_seconds = 0.0;
        AlignedBuffer<std::uint64_t> database = build_or_load_database(options, signs, db_seconds);
        const std::vector<std::vector<float>> raw_queries = load_queries(options.queries, options.query_count);
        AlignedBuffer<float> distances(options.data_count);

        const QueryRepresentations first_query = prepare_query(raw_queries.front().data(), signs);
        validate_kernels(database.data(), options.data_count, first_query, distances);
        for (int codec = 0; codec < 4; ++codec) {
            run_codec(static_cast<Codec>(codec), database.data(), options.data_count,
                first_query, distances.data(), options.prefetch_blocks.front());
        }
        std::cerr << "Warmup complete\n";

        std::vector<ScanResult> results;
        std::vector<PrepResult> prep_results;
        results.reserve(options.query_count * options.rounds * options.prefetch_blocks.size() * 4);
        prep_results.reserve(options.query_count);
        for (std::size_t query_id = 0; query_id < options.query_count; ++query_id) {
            const QueryRepresentations query = prepare_query(raw_queries[query_id].data(), signs);
            prep_results.push_back({query_id, query.rotate_us, query.fp32_prepare_us,
                query.b1_prepare_us, query.int4_prepare_us, query.int8_prepare_us});
            for (std::size_t round = 0; round < options.rounds; ++round) {
                for (std::size_t pf_index = 0; pf_index < options.prefetch_blocks.size(); ++pf_index) {
                    const std::size_t prefetch = options.prefetch_blocks[pf_index];
                    const std::size_t rotation = (query_id + round + pf_index) % 4;
                    for (std::size_t position = 0; position < 4; ++position) {
                        const Codec codec = static_cast<Codec>((position + rotation) % 4);
                        const auto start = Clock::now();
                        run_codec(codec, database.data(), options.data_count,
                            query, distances.data(), prefetch);
                        const double elapsed_ms = std::chrono::duration<double, std::milli>(
                            Clock::now() - start).count();
                        const double checksum = sampled_checksum(distances.data(), options.data_count);
                        results.push_back({query_id, round, prefetch, codec, elapsed_ms, checksum});
                    }
                }
            }
            std::cerr << "Measured query " << query_id + 1 << '/' << options.query_count << "\n";
        }
        write_csv(options, results);
        print_summary(options, results, prep_results, db_seconds);
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}

