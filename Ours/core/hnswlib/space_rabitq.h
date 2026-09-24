#pragma once

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <functional>
#include <immintrin.h>
#include <istream>
#include <limits>
#include <memory>
#include <ostream>
#include <queue>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <unordered_map>
#include <vector>

#include <cerrno>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "hnswlib.h"

namespace hnswlib {

enum class RaBitQCodeLayout : uint8_t {
    SequentialNibble = 0,
    Turbo128 = 1,
};

enum class QueryCoarseCodec : uint8_t {
    Full = 0,
    B1 = 1,
    Int4 = 2,
    Int8 = 3,
    B1Main = 4,
    FullStaged = 5,
    FullRerankScalar = 6,
};

class RaBitQSpace : public SpaceInterface<float> {
 public:
    enum class CentroidQueryMode : uint8_t {
        Eager = 0,
        Lazy = 1,
    };
    static constexpr size_t kTotalBits = 4;
    static constexpr size_t kShortBits = 1;
    static constexpr size_t kPaperBatch = 32;
    static constexpr size_t kRemainingBits = 3;
    static constexpr uint32_t kUnsignedMax = 15;
    static constexpr uint32_t kRemainingMax = 7;
    static constexpr uint32_t kMsbWeight = 8;
    static constexpr float kUnsignedOffset = 7.5f;
    static constexpr float kRemainingOffset = 3.5f;
    static constexpr size_t kResidualBlockSize = 16;
    static constexpr double kExtendedRaBitQErrorConstant = 5.75;

    enum class ResidualQuantizationBits : uint8_t {
        B1 = 1,
        B2 = 2,
        B4 = 4,
        B8 = 8,
        B16 = 16
    };

    struct ResidualQuantizationConfig {
        ResidualQuantizationBits bits = ResidualQuantizationBits::B4;
        size_t block_size = kResidualBlockSize;
        bool enabled = true;
        bool enable_block_scaling = true;
        bool mse_optimal_scale = false;
        bool scale_fp16 = false;
    };

    struct RaBitQConfig {
        size_t dimension = 0;
        ResidualQuantizationConfig residual;
    };

    struct EncodedHeader {
        float norm_sqr;
        float long_scale;
    };

    struct ShortCodeFactors {
        float short_scale;
        float error_scale;
    };

    struct ResidualCodeFactors {
        float residual_norm_sqr;
        float long_residual_inner_product;
    };

    struct QueryContext {
        std::vector<float> rotated_residual;
        std::vector<float> rotated_residual_even;
        std::vector<float> rotated_residual_odd;
        mutable std::vector<float> residual_nibble_lut;
        mutable std::vector<float> residual_2bit_lut;
        float query_norm = 0.0f;
        float query_norm_sqr = 0.0f;
        float half_sum_residual = 0.0f;
        float rotated_residual_sum = 0.0f;
        float positive_sum_residual = 0.0f;
        // Quantized query state for the coarse filter (built per centroid in
        // buildCentroidQuery). b1 uses the same bit layout as the paper-prune
        // MSB sidecar (bit i of byte i>>3); INT4/INT8 use centered per-query
        // global-scale quantization q_hat_i = scale * x_i.
        std::vector<uint8_t> b1_code;
        std::vector<int8_t> b1_signed;
        int64_t b1_signed_sum = 0;
        float b1_short_scale = 0.0f;
        float b1_error_scale = 0.0f;
        float b1_alpha = 0.0f;
        std::vector<int8_t> int8_code;
        std::vector<int8_t> int4_code;
        float int8_scale = 1.0f;
        float int4_scale = 1.0f;
        float int8_error_norm = 0.0f;
        float int4_error_norm = 0.0f;
        int64_t int8_code_sum = 0;
        int64_t int4_code_sum = 0;
        mutable bool residual_nibble_lut_ready = false;
        mutable bool residual_2bit_lut_ready = false;
    };

    struct PreparedQuery {
        std::vector<QueryContext> centroid_queries;
        std::vector<uint8_t> ready;
        std::vector<float> rotated_query;
        const float *raw_query = nullptr;
        double raw_query_norm_sqr = 0.0;
        size_t active_centroid_count = 0;
        QueryCoarseCodec query_coarse_codec{QueryCoarseCodec::Full};
    };

    struct LongCodeIps {
        float short_ip;
        float remaining_ip;
    };

    struct BuildPreparedSlot {
        PreparedQuery query;
        const RaBitQSpace *owner = nullptr;
        bool busy = false;
    };

    struct SymmetricBuildPreparedQuery {
        EncodedHeader header{};
        const uint8_t *code = nullptr;
        double query_norm = 0.0;
        double query_ip_norm = 0.0;
        uint8_t centroid_id = 0;
        const RaBitQSpace *owner = nullptr;
        bool valid = false;
        bool busy = false;
    };

 private:
    size_t dim_{0};
    size_t code_dim_{0};
    size_t compact_code_bytes_{0};
    size_t short_factor_bytes_{0};
    size_t residual_factor_bytes_{0};
    size_t residual_block_size_{kResidualBlockSize};
    size_t residual_block_count_{0};
    size_t residual_scale_bytes_{0};
    size_t residual_bits_{8};
    size_t residual_code_bytes_{0};
    size_t centroid_count_{1};
    size_t centroid_id_bytes_{1};
    bool nested4x4_layout_{false};
    size_t nested4x4_hot_aux_bytes_{0};
    size_t full_data_size_{0};
    size_t residual_disk_record_bytes_{0};
    size_t data_size_{0};
    float inv_sqrt_code_dim_{1.0f};
    bool external_residual_storage_{false};
    mutable int residual_fd_{-1};
    mutable const char *residual_mmap_{nullptr};
    mutable size_t residual_mmap_bytes_{0};
    mutable size_t residual_record_count_{0};
    bool legacy_payload_without_centroid_{false};
    CentroidQueryMode centroid_query_mode_{CentroidQueryMode::Eager};
    RaBitQCodeLayout code_layout_{RaBitQCodeLayout::SequentialNibble};
    QueryCoarseCodec query_coarse_codec_{QueryCoarseCodec::Full};

    DISTFUNC<float> fstdistfunc_{nullptr};

    uint32_t random_seed_{100};
    std::vector<float> fht_signs_;
    ResidualQuantizationConfig residual_config_;
    std::vector<float> centroids_;
    std::vector<float> rotated_centroids_;
    std::vector<float> centroid_norm_sqr_;
    std::vector<float> centroid_pair_norm_sqr_;
    std::vector<std::vector<QueryContext>> centroid_delta_queries_;

    static std::vector<std::unique_ptr<BuildPreparedSlot>> &buildPreparedPool() {
        thread_local std::vector<std::unique_ptr<BuildPreparedSlot>> pool;
        return pool;
    }

    static std::vector<std::unique_ptr<SymmetricBuildPreparedQuery>>
        &symmetricBuildPreparedPool() {
        thread_local std::vector<std::unique_ptr<SymmetricBuildPreparedQuery>> pool;
        return pool;
    }

    static size_t roundUp64(size_t value) {
        const size_t rounded = ((value + 63U) / 64U) * 64U;
        size_t power = 1;
        while (power < rounded) {
            power <<= 1U;
        }
        return power;
    }

    static float exrabitqDistance(const void *lhs, const void *rhs, const void *space_ptr) {
        const RaBitQSpace *space = static_cast<const RaBitQSpace *>(space_ptr);
        return space->distanceBetweenEncoded(
            static_cast<const char *>(lhs),
            static_cast<const char *>(rhs));
    }

    EncodedHeader loadHeader(const void *encoded) const {
        EncodedHeader value;
        std::memcpy(&value, encoded, sizeof(EncodedHeader));
        return value;
    }

    static bool envModeIsNested4x4() {
        return false;
    }

    size_t nested4x4Low4RecordBytes() const {
        return compact_code_bytes_;
    }

    size_t externalResidualRecordBytes() const {
        return nested4x4_layout_ ? nested4x4Low4RecordBytes() : residual_disk_record_bytes_;
    }

    size_t nested4x4CodeOffset() const {
        return sizeof(EncodedHeader) + sizeof(ShortCodeFactors) + residual_scale_bytes_;
    }

    size_t codeOffsetExternal() const {
        return nested4x4_layout_
            ? nested4x4CodeOffset()
            : sizeof(EncodedHeader) + sizeof(ShortCodeFactors);
    }

    size_t codeOffsetFull() const {
        return nested4x4_layout_
            ? nested4x4CodeOffset()
            : sizeof(EncodedHeader) + sizeof(ShortCodeFactors) + sizeof(ResidualCodeFactors) +
                residual_scale_bytes_;
    }

    size_t centroidOffsetExternal() const {
        return codeOffsetExternal() + compact_code_bytes_;
    }

    size_t centroidOffsetFull() const {
        return codeOffsetFull() + compact_code_bytes_;
    }

    const uint8_t *codeBytes(const void *encoded) const {
        const size_t offset = external_residual_storage_ ? codeOffsetExternal() : codeOffsetFull();
        return reinterpret_cast<const uint8_t *>(
            static_cast<const char *>(encoded) + offset);
    }

    uint8_t *codeBytes(void *encoded) const {
        const size_t offset = external_residual_storage_ ? codeOffsetExternal() : codeOffsetFull();
        return reinterpret_cast<uint8_t *>(
            static_cast<char *>(encoded) + offset);
    }

    const uint8_t *codeBytesFull(const void *encoded) const {
        return reinterpret_cast<const uint8_t *>(
            static_cast<const char *>(encoded) + codeOffsetFull());
    }

    uint8_t *codeBytesFull(void *encoded) const {
        return reinterpret_cast<uint8_t *>(
            static_cast<char *>(encoded) + codeOffsetFull());
    }

    const uint8_t *residualCodeBytes(const void *encoded) const {
        if (nested4x4_layout_) {
            return reinterpret_cast<const uint8_t *>(
                static_cast<const char *>(encoded) + centroidOffsetFull() + centroid_id_bytes_);
        }
        return reinterpret_cast<const uint8_t *>(
            static_cast<const char *>(encoded) + centroidOffsetFull() + centroid_id_bytes_);
    }

    uint8_t *residualCodeBytes(void *encoded) const {
        if (nested4x4_layout_) {
            return reinterpret_cast<uint8_t *>(
                static_cast<char *>(encoded) + centroidOffsetFull() + centroid_id_bytes_);
        }
        return reinterpret_cast<uint8_t *>(
            static_cast<char *>(encoded) + centroidOffsetFull() + centroid_id_bytes_);
    }

    const uint8_t *centroidId(const void *encoded) const {
        if (legacy_payload_without_centroid_) {
            static const uint8_t legacy_centroid_id = 0;
            return &legacy_centroid_id;
        }
        const size_t offset = external_residual_storage_ ? centroidOffsetExternal() : centroidOffsetFull();
        return reinterpret_cast<const uint8_t *>(static_cast<const char *>(encoded) + offset);
    }

    uint8_t *centroidId(void *encoded) const {
        const size_t offset = external_residual_storage_ ? centroidOffsetExternal() : centroidOffsetFull();
        return reinterpret_cast<uint8_t *>(static_cast<char *>(encoded) + offset);
    }

    const uint8_t *centroidIdFull(const void *encoded) const {
        return reinterpret_cast<const uint8_t *>(static_cast<const char *>(encoded) + centroidOffsetFull());
    }

    uint8_t *centroidIdFull(void *encoded) const {
        return reinterpret_cast<uint8_t *>(static_cast<char *>(encoded) + centroidOffsetFull());
    }

    void setCentroidId(void *encoded, uint8_t id) const {
        *centroidId(encoded) = id;
    }

    const float *residualScales(const void *encoded) const {
        if (nested4x4_layout_) {
            return reinterpret_cast<const float *>(
                static_cast<const char *>(encoded) + sizeof(EncodedHeader) + sizeof(ShortCodeFactors));
        }
        return reinterpret_cast<const float *>(
            static_cast<const char *>(encoded) + sizeof(EncodedHeader) + sizeof(ShortCodeFactors) +
                sizeof(ResidualCodeFactors));
    }

    float *residualScales(void *encoded) const {
        if (nested4x4_layout_) {
            return reinterpret_cast<float *>(
                static_cast<char *>(encoded) + sizeof(EncodedHeader) + sizeof(ShortCodeFactors));
        }
        return reinterpret_cast<float *>(
            static_cast<char *>(encoded) + sizeof(EncodedHeader) + sizeof(ShortCodeFactors) +
                sizeof(ResidualCodeFactors));
    }

    static uint8_t codeValue(const uint8_t *packed_code, size_t index) {
        const uint8_t byte = packed_code[index >> 1U];
        return static_cast<uint8_t>((index & 1U) ? (byte >> 4U) : (byte & 0x0FU));
    }

    static void setCodeValue(uint8_t *packed_code, size_t index, uint8_t value) {
        value = static_cast<uint8_t>(value & 0x0FU);
        uint8_t &byte = packed_code[index >> 1U];
        if (index & 1U) {
            byte = static_cast<uint8_t>((byte & 0x0FU) | (value << 4U));
        } else {
            byte = static_cast<uint8_t>((byte & 0xF0U) | value);
        }
    }

    static uint8_t turbo128CodeValue(const uint8_t *code, size_t index) {
        const size_t block = index / 128U;
        const size_t within = index % 128U;
        const size_t lane = within % 16U;
        const size_t slot = within / 16U;
        const uint8_t byte = code[block * 64U + lane * 4U + (slot >> 1U)];
        return static_cast<uint8_t>((slot & 1U) ? (byte >> 4U) : (byte & 0x0FU));
    }

    uint8_t primaryCodeValue(const uint8_t *code, size_t index) const {
        return code_layout_ == RaBitQCodeLayout::Turbo128
            ? turbo128CodeValue(code, index)
            : codeValue(code, index);
    }

    static uint16_t floatToHalfScalar(float value) {
        uint32_t bits = 0;
        std::memcpy(&bits, &value, sizeof(bits));
        const uint32_t sign = (bits >> 16U) & 0x8000U;
        uint32_t mantissa = bits & 0x007FFFFFU;
        int32_t exponent = static_cast<int32_t>((bits >> 23U) & 0xFFU) - 127 + 15;
        if (exponent <= 0) {
            if (exponent < -10) {
                return static_cast<uint16_t>(sign);
            }
            mantissa = (mantissa | 0x00800000U) >> static_cast<uint32_t>(1 - exponent);
            return static_cast<uint16_t>(sign | ((mantissa + 0x00001000U) >> 13U));
        }
        if (exponent >= 31) {
            return static_cast<uint16_t>(sign | 0x7C00U);
        }
        return static_cast<uint16_t>(
            sign | (static_cast<uint32_t>(exponent) << 10U) |
            ((mantissa + 0x00001000U) >> 13U));
    }

    static float halfToFloatScalar(uint16_t value) {
        const uint32_t sign = static_cast<uint32_t>(value & 0x8000U) << 16U;
        uint32_t exponent = (value >> 10U) & 0x1FU;
        uint32_t mantissa = value & 0x03FFU;
        uint32_t bits = 0;
        if (exponent == 0) {
            if (mantissa == 0) {
                bits = sign;
            } else {
                exponent = 1;
                while ((mantissa & 0x0400U) == 0) {
                    mantissa <<= 1U;
                    --exponent;
                }
                mantissa &= 0x03FFU;
                bits = sign | ((exponent + 127 - 15) << 23U) | (mantissa << 13U);
            }
        } else if (exponent == 31) {
            bits = sign | 0x7F800000U | (mantissa << 13U);
        } else {
            bits = sign | ((exponent + 127 - 15) << 23U) | (mantissa << 13U);
        }
        float result = 0.0f;
        std::memcpy(&result, &bits, sizeof(result));
        return result;
    }

    size_t residualScaleElementBytes() const {
        return residual_config_.scale_fp16 ? sizeof(uint16_t) : sizeof(float);
    }

    const void *residualScaleBytes(const void *encoded) const {
        return residualScales(encoded);
    }

    void *residualScaleBytes(void *encoded) const {
        return residualScales(encoded);
    }

    void storeResidualScale(void *scale_storage, size_t block, float scale) const {
        if (residual_config_.scale_fp16) {
            uint16_t half = floatToHalfScalar(scale);
            std::memcpy(
                static_cast<char *>(scale_storage) + block * sizeof(uint16_t),
                &half,
                sizeof(half));
        } else {
            std::memcpy(
                static_cast<char *>(scale_storage) + block * sizeof(float),
                &scale,
                sizeof(scale));
        }
    }

    float loadResidualScale(const void *scale_storage, size_t block) const {
        if (residual_config_.scale_fp16) {
            uint16_t half = 0;
            std::memcpy(
                &half,
                static_cast<const char *>(scale_storage) + block * sizeof(uint16_t),
                sizeof(half));
            return halfToFloatScalar(half);
        }
        float scale = 0.0f;
        std::memcpy(
            &scale,
            static_cast<const char *>(scale_storage) + block * sizeof(float),
            sizeof(scale));
        return scale;
    }

    const float *decodeResidualScales(const void *scale_storage) const {
        thread_local std::vector<float> decoded_scales;
        if (!residual_config_.scale_fp16) {
            return reinterpret_cast<const float *>(scale_storage);
        }
        decoded_scales.assign(residual_block_count_, 0.0f);
        for (size_t block = 0; block < residual_block_count_; ++block) {
            decoded_scales[block] = loadResidualScale(scale_storage, block);
        }
        return decoded_scales.data();
    }

 public:
    static bool is_supported_residual_bits(size_t bits) {
        return bits == 1 || bits == 2 || bits == 4 || bits == 8 || bits == 16;
    }

    static int32_t residual_quantized_max(size_t bits) {
        if (!is_supported_residual_bits(bits)) {
            throw std::invalid_argument("unsupported residual quantization bits");
        }
        if (bits == 1) {
            return 1;
        }
        return static_cast<int32_t>((uint32_t{1} << (bits - 1U)) - 1U);
    }

    static size_t residual_packed_code_size_bytes(size_t dimension, size_t bits) {
        if (!is_supported_residual_bits(bits)) {
            throw std::invalid_argument("unsupported residual quantization bits");
        }
        if (dimension > (std::numeric_limits<size_t>::max() - 7U) / bits) {
            throw std::overflow_error("residual packed code size overflow");
        }
        return (dimension * bits + 7U) / 8U;
    }

    static double primary_code_empirical_error_reference(size_t dimension) {
        if (dimension == 0) {
            throw std::invalid_argument("dimension must be positive");
        }
        return kExtendedRaBitQErrorConstant /
               (16.0 * std::sqrt(static_cast<double>(dimension)));
    }

    static double residual_coordinate_error_bound(double block_scale, size_t residual_bits) {
        if (!is_supported_residual_bits(residual_bits)) {
            throw std::invalid_argument("unsupported residual quantization bits");
        }
        return residual_bits == 1
            ? std::abs(block_scale)
            : 0.5 * std::abs(block_scale);
    }

    static void pack_residual_codes(
        const int32_t *codes,
        size_t dimension,
        size_t residual_bits,
        uint8_t *output) {
        const size_t byte_count = residual_packed_code_size_bytes(dimension, residual_bits);
        std::memset(output, 0, byte_count);
        if (residual_bits == 1) {
            for (size_t i = 0; i < dimension; ++i) {
                if (codes[i] != -1 && codes[i] != 1) {
                    throw std::invalid_argument("1-bit residual code must be -1 or +1");
                }
                if (codes[i] > 0) {
                    output[i >> 3U] = static_cast<uint8_t>(
                        output[i >> 3U] | static_cast<uint8_t>(1U << (i & 7U)));
                }
            }
            return;
        }

        const int32_t qmax = residual_quantized_max(residual_bits);
        if (residual_bits == 8) {
            for (size_t i = 0; i < dimension; ++i) {
                if (codes[i] < -qmax || codes[i] > qmax) {
                    throw std::invalid_argument("8-bit residual code is out of range");
                }
                output[i] = static_cast<uint8_t>(static_cast<int8_t>(codes[i]));
            }
            return;
        }
        if (residual_bits == 16) {
            for (size_t i = 0; i < dimension; ++i) {
                if (codes[i] < -qmax || codes[i] > qmax) {
                    throw std::invalid_argument("16-bit residual code is out of range");
                }
                const uint16_t raw = static_cast<uint16_t>(static_cast<int16_t>(codes[i]));
                output[2U * i] = static_cast<uint8_t>(raw & 0xFFU);
                output[2U * i + 1U] = static_cast<uint8_t>(raw >> 8U);
            }
            return;
        }

        const uint32_t mask = (uint32_t{1} << residual_bits) - 1U;
        size_t bit_offset = 0;
        for (size_t i = 0; i < dimension; ++i) {
            if (codes[i] < -qmax || codes[i] > qmax) {
                throw std::invalid_argument("packed residual code is out of range");
            }
            const uint32_t unsigned_code = static_cast<uint32_t>(codes[i]) & mask;
            const size_t byte_index = bit_offset >> 3U;
            const size_t shift = bit_offset & 7U;
            output[byte_index] = static_cast<uint8_t>(output[byte_index] | (unsigned_code << shift));
            if (shift + residual_bits > 8U) {
                output[byte_index + 1U] = static_cast<uint8_t>(
                    output[byte_index + 1U] | (unsigned_code >> (8U - shift)));
            }
            bit_offset += residual_bits;
        }
    }

    static void unpack_residual_codes(
        const uint8_t *packed,
        size_t dimension,
        size_t residual_bits,
        int32_t *output) {
        (void) residual_packed_code_size_bytes(dimension, residual_bits);
        if (residual_bits == 1) {
            for (size_t i = 0; i < dimension; ++i) {
                output[i] = (packed[i >> 3U] & static_cast<uint8_t>(1U << (i & 7U))) ? 1 : -1;
            }
            return;
        }

        (void) residual_quantized_max(residual_bits);
        if (residual_bits == 8) {
            for (size_t i = 0; i < dimension; ++i) {
                output[i] = static_cast<int32_t>(static_cast<int8_t>(packed[i]));
            }
            return;
        }
        if (residual_bits == 16) {
            for (size_t i = 0; i < dimension; ++i) {
                const uint16_t raw = static_cast<uint16_t>(
                    static_cast<uint16_t>(packed[2U * i]) |
                    (static_cast<uint16_t>(packed[2U * i + 1U]) << 8U));
                output[i] = static_cast<int32_t>(static_cast<int16_t>(raw));
            }
            return;
        }

        const uint32_t mask = (uint32_t{1} << residual_bits) - 1U;
        size_t bit_offset = 0;
        for (size_t i = 0; i < dimension; ++i) {
            const size_t byte_index = bit_offset >> 3U;
            const size_t shift = bit_offset & 7U;
            uint32_t raw = static_cast<uint32_t>(packed[byte_index] >> shift);
            if (shift + residual_bits > 8U) {
                raw |= static_cast<uint32_t>(packed[byte_index + 1U]) << (8U - shift);
            }
            uint32_t value = raw & mask;
            const uint32_t sign_bit = uint32_t{1} << (residual_bits - 1U);
            if (value & sign_bit) {
                value |= ~mask;
            }
            output[i] = static_cast<int32_t>(value);
            bit_offset += residual_bits;
        }
    }

 private:
    static size_t configuredResidualBits(size_t explicit_bits) {
        if (explicit_bits != 0) {
            if (!is_supported_residual_bits(explicit_bits)) {
                throw std::invalid_argument("explicit residual bits must be 1, 2, 4, 8, or 16");
            }
            return explicit_bits;
        }
        const char *value = std::getenv("RABITQ_RESIDUAL_BITS");
        if (value == nullptr || value[0] == '\0') {
            return 8;
        }
        char *end = nullptr;
        const unsigned long parsed = std::strtoul(value, &end, 10);
        if (end == value || *end != '\0') {
            throw std::invalid_argument("RABITQ_RESIDUAL_BITS must be 1, 2, 4, 8, or 16");
        }
        if (is_supported_residual_bits(static_cast<size_t>(parsed))) {
            return static_cast<size_t>(parsed);
        }
        throw std::invalid_argument("RABITQ_RESIDUAL_BITS must be 1, 2, 4, 8, or 16");
    }

    static ResidualQuantizationBits residualBitsEnum(size_t bits) {
        switch (bits) {
            case 1: return ResidualQuantizationBits::B1;
            case 2: return ResidualQuantizationBits::B2;
            case 4: return ResidualQuantizationBits::B4;
            case 8: return ResidualQuantizationBits::B8;
            case 16: return ResidualQuantizationBits::B16;
            default:
                throw std::invalid_argument("residual bits must be 1, 2, 4, 8, or 16");
        }
    }

    static ResidualQuantizationConfig makeResidualConfig(size_t explicit_bits) {
        ResidualQuantizationConfig config;
        config.bits = residualBitsEnum(configuredResidualBits(explicit_bits));
        if (const char *value = std::getenv("RABITQ_RESIDUAL_BLOCK_SIZE")) {
            char *end = nullptr;
            const unsigned long parsed = std::strtoul(value, &end, 10);
            if (end != value && *end == '\0' && parsed > 0) {
                config.block_size = static_cast<size_t>(parsed);
            } else {
                throw std::invalid_argument("RABITQ_RESIDUAL_BLOCK_SIZE must be a positive integer");
            }
        }
        if (const char *value = std::getenv("RABITQ_RESIDUAL_SCALE_MODE")) {
            if (std::strcmp(value, "max_abs") == 0) {
                config.mse_optimal_scale = false;
            } else if (std::strcmp(value, "mse") == 0 || std::strcmp(value, "mse_optimal") == 0) {
                config.mse_optimal_scale = true;
            } else {
                throw std::invalid_argument("RABITQ_RESIDUAL_SCALE_MODE must be max_abs or mse");
            }
        }
        if (const char *value = std::getenv("RABITQ_RESIDUAL_SCALE_STORAGE")) {
            if (std::strcmp(value, "fp32") == 0) {
                config.scale_fp16 = false;
            } else if (std::strcmp(value, "fp16") == 0) {
                config.scale_fp16 = true;
            } else {
                throw std::invalid_argument("RABITQ_RESIDUAL_SCALE_STORAGE must be fp32 or fp16");
            }
        }
        return config;
    }

    static ResidualQuantizationConfig validateResidualConfig(ResidualQuantizationConfig config) {
        const size_t bits = static_cast<size_t>(config.bits);
        if (!is_supported_residual_bits(bits)) {
            throw std::invalid_argument("residual bits must be 1, 2, 4, 8, or 16");
        }
        if (!config.enabled) {
            throw std::invalid_argument("RaBitQ residual refinement must remain enabled");
        }
        if (config.block_size == 0) {
            throw std::invalid_argument("residual block size must be positive");
        }
        return config;
    }

    static float residualMaxAbsScale(
        const std::vector<float> &errors,
        size_t begin,
        size_t end,
        size_t residual_bits,
        int32_t residual_qmax) {
        float max_abs_residual_error = 0.0f;
        for (size_t i = begin; i < end; ++i) {
            max_abs_residual_error =
                std::max(max_abs_residual_error, static_cast<float>(std::abs(errors[i])));
        }
        if (max_abs_residual_error <= 0.0f) {
            return 0.0f;
        }
        return residual_bits == 1
            ? max_abs_residual_error
            : max_abs_residual_error / static_cast<float>(residual_qmax);
    }

    static float residualMseOptimalScale(
        const std::vector<float> &errors,
        size_t begin,
        size_t end,
        size_t residual_bits,
        int32_t residual_qmin,
        int32_t residual_qmax) {
        float scale = residualMaxAbsScale(errors, begin, end, residual_bits, residual_qmax);
        if (scale == 0.0f || !std::isfinite(scale) || residual_bits == 1) {
            return scale;
        }
        for (size_t iter = 0; iter < 8; ++iter) {
            double numerator = 0.0;
            double denominator = 0.0;
            for (size_t i = begin; i < end; ++i) {
                const int32_t q = std::max(
                    residual_qmin,
                    std::min(
                        residual_qmax,
                        static_cast<int32_t>(std::round(
                            static_cast<double>(errors[i]) / static_cast<double>(scale)))));
                numerator += static_cast<double>(errors[i]) * static_cast<double>(q);
                denominator += static_cast<double>(q) * static_cast<double>(q);
            }
            if (denominator <= 0.0) {
                break;
            }
            const float next_scale = static_cast<float>(numerator / denominator);
            if (next_scale <= 0.0f || !std::isfinite(next_scale)) {
                break;
            }
            if (std::abs(next_scale - scale) <= 1e-6f * std::max(1.0f, scale)) {
                scale = next_scale;
                break;
            }
            scale = next_scale;
        }
        return scale;
    }

    int32_t residualQuantizedValue(const uint8_t *packed_code, size_t index) const {
        if (residual_bits_ == 1) {
            return (packed_code[index >> 3U] & static_cast<uint8_t>(1U << (index & 7U))) ? 1 : -1;
        }
        if (residual_bits_ == 8) {
            return static_cast<int32_t>(static_cast<int8_t>(packed_code[index]));
        }
        if (residual_bits_ == 16) {
            const uint16_t raw = static_cast<uint16_t>(
                static_cast<uint16_t>(packed_code[2U * index]) |
                (static_cast<uint16_t>(packed_code[2U * index + 1U]) << 8U));
            return static_cast<int32_t>(static_cast<int16_t>(raw));
        }
        if (residual_bits_ == 4) {
            const uint8_t nibble = codeValue(packed_code, index);
            return nibble >= 8U ? static_cast<int32_t>(nibble) - 16 : static_cast<int32_t>(nibble);
        }
        const size_t bit_offset = index * residual_bits_;
        const size_t byte_index = bit_offset >> 3U;
        const size_t shift = bit_offset & 7U;
        uint32_t raw = static_cast<uint32_t>(packed_code[byte_index] >> shift);
        if (shift + residual_bits_ > 8U) {
            raw |= static_cast<uint32_t>(packed_code[byte_index + 1U]) << (8U - shift);
        }
        const uint32_t mask = (uint32_t{1} << residual_bits_) - 1U;
        uint32_t value = raw & mask;
        const uint32_t sign_bit = uint32_t{1} << (residual_bits_ - 1U);
        if (value & sign_bit) {
            value |= ~mask;
        }
        return static_cast<int32_t>(value);
    }

    const ShortCodeFactors *shortFactors(const void *encoded) const {
        return reinterpret_cast<const ShortCodeFactors *>(
            static_cast<const char *>(encoded) + sizeof(EncodedHeader));
    }

    ShortCodeFactors *shortFactors(void *encoded) const {
        return reinterpret_cast<ShortCodeFactors *>(
            static_cast<char *>(encoded) + sizeof(EncodedHeader));
    }

    const ResidualCodeFactors *residualFactors(const void *encoded) const {
        return reinterpret_cast<const ResidualCodeFactors *>(
            static_cast<const char *>(encoded) + sizeof(EncodedHeader) + sizeof(ShortCodeFactors));
    }

    ResidualCodeFactors *residualFactors(void *encoded) const {
        return reinterpret_cast<ResidualCodeFactors *>(
            static_cast<char *>(encoded) + sizeof(EncodedHeader) + sizeof(ShortCodeFactors));
    }

    const ResidualCodeFactors *residualFactorsFromRecord(const void *record) const {
        return reinterpret_cast<const ResidualCodeFactors *>(record);
    }

    const float *residualScalesFromRecord(const void *record) const {
        return reinterpret_cast<const float *>(
            static_cast<const char *>(record) + sizeof(ResidualCodeFactors));
    }

    const uint8_t *residualCodeBytesFromRecord(const void *record) const {
        return reinterpret_cast<const uint8_t *>(
            static_cast<const char *>(record) + sizeof(ResidualCodeFactors) + residual_scale_bytes_);
    }

    const char *externalResidualRecord(size_t id) const {
        if (residual_mmap_ == nullptr) {
            throw std::runtime_error("RaBitQ external residual storage is not open");
        }
        const size_t record_bytes = externalResidualRecordBytes();
        const size_t offset = id * record_bytes;
        if (offset + record_bytes > residual_mmap_bytes_) {
            throw std::runtime_error("RaBitQ external residual id is outside residual file");
        }
        return residual_mmap_ + offset;
    }

    void fastQuantizeAbs(const float *abs_unit_data, uint8_t *abs_code, float &ip_norm) const {
        constexpr double eps = 1e-5;
        constexpr int n_enum = 10;
        double max_o = 0.0;
        for (size_t i = 0; i < code_dim_; ++i) {
            max_o = std::max(max_o, static_cast<double>(abs_unit_data[i]));
        }
        if (max_o <= eps) {
            std::memset(abs_code, 0, code_dim_);
            ip_norm = 1.0f;
            return;
        }

        const double t_start = static_cast<double>((kRemainingMax / 3U)) / max_o;
        const double t_end = (static_cast<double>(kRemainingMax) + n_enum) / max_o;
        thread_local std::vector<int> cur_code;
        cur_code.assign(code_dim_, 0);

        double sqr_denominator = static_cast<double>(code_dim_) * 0.25;
        double numerator = 0.0;
        for (size_t i = 0; i < code_dim_; ++i) {
            cur_code[i] = static_cast<int>(t_start * abs_unit_data[i] + eps);
            cur_code[i] = std::min<int>(cur_code[i], static_cast<int>(kRemainingMax));
            sqr_denominator += cur_code[i] * cur_code[i] + cur_code[i];
            numerator += (cur_code[i] + 0.5) * abs_unit_data[i];
        }

        std::priority_queue<
            std::pair<double, size_t>,
            std::vector<std::pair<double, size_t>>,
            std::greater<std::pair<double, size_t>>> next_t;
        for (size_t i = 0; i < code_dim_; ++i) {
            if (abs_unit_data[i] > eps) {
                next_t.emplace(static_cast<double>(cur_code[i] + 1) / abs_unit_data[i], i);
            }
        }

        double best_t = 0.0;
        double max_ip = 0.0;
        while (!next_t.empty()) {
            const double cur_t = next_t.top().first;
            const size_t update_id = next_t.top().second;
            next_t.pop();

            ++cur_code[update_id];
            const int update_code = cur_code[update_id];
            sqr_denominator += 2.0 * update_code;
            numerator += abs_unit_data[update_id];

            const double cur_ip = numerator / std::sqrt(sqr_denominator);
            if (cur_ip > max_ip) {
                max_ip = cur_ip;
                best_t = cur_t;
            }

            if (update_code < static_cast<int>(kRemainingMax)) {
                const double candidate_t = static_cast<double>(update_code + 1) / abs_unit_data[update_id];
                if (candidate_t < t_end) {
                    next_t.emplace(candidate_t, update_id);
                }
            }
        }

        numerator = 0.0;
        for (size_t i = 0; i < code_dim_; ++i) {
            int value = static_cast<int>(best_t * abs_unit_data[i] + eps);
            value = std::min<int>(value, static_cast<int>(kRemainingMax));
            abs_code[i] = static_cast<uint8_t>(value);
            numerator += (value + 0.5) * abs_unit_data[i];
        }

        ip_norm = numerator > eps ? static_cast<float>(1.0 / numerator) : 1.0f;
        if (!std::isfinite(ip_norm)) {
            ip_norm = 1.0f;
        }
    }

    void hadamard(std::vector<float> &values) const {
        for (size_t step = 1; step < code_dim_; step <<= 1U) {
            if (step >= 16) {
#if defined(__AVX512F__)
                const size_t vec = 16;
                for (size_t block = 0; block < code_dim_; block += (step << 1U)) {
                    size_t i = 0;
                    for (; i + vec <= step; i += vec) {
                        const __m512 a = _mm512_loadu_ps(values.data() + block + i);
                        const __m512 b =
                            _mm512_loadu_ps(values.data() + block + step + i);
                        _mm512_storeu_ps(
                            values.data() + block + i, _mm512_add_ps(a, b));
                        _mm512_storeu_ps(
                            values.data() + block + step + i, _mm512_sub_ps(a, b));
                    }
                    for (; i < step; ++i) {
                        const float a = values[block + i];
                        const float b = values[block + step + i];
                        values[block + i] = a + b;
                        values[block + step + i] = a - b;
                    }
                }
#else
                for (size_t block = 0; block < code_dim_; block += (step << 1U)) {
                    for (size_t i = 0; i < step; ++i) {
                        const float a = values[block + i];
                        const float b = values[block + step + i];
                        values[block + i] = a + b;
                        values[block + step + i] = a - b;
                    }
                }
#endif
                continue;
            }
            for (size_t block = 0; block < code_dim_; block += (step << 1U)) {
                for (size_t i = 0; i < step; ++i) {
                    const float a = values[block + i];
                    const float b = values[block + step + i];
                    values[block + i] = a + b;
                    values[block + step + i] = a - b;
                }
            }
        }
    }

    void rotate(const float *raw_vector, std::vector<float> &rotated) const {
        rotated.assign(code_dim_, 0.0f);
        if (random_seed_ == 0) {
            std::copy(raw_vector, raw_vector + dim_, rotated.begin());
            return;
        }
        for (size_t i = 0; i < dim_; ++i) {
            rotated[i] = raw_vector[i] * fht_signs_[i];
        }
        hadamard(rotated);
    }

    float dotRemainingUint8FloatAvxDispatch(const uint8_t *remaining_code, const QueryContext &query) const {
#if defined(__AVX512F__) && defined(__AVX512BW__)
        return dotRemainingUint8FloatAvx512(remaining_code, query);
#elif defined(__AVX2__)
        return dotRemainingUint8FloatAvx2(remaining_code, query);
#else
        return dotRemainingUint8FloatScalar(remaining_code, query);
#endif
    }

    float dotRemainingUint8FloatScalar(const uint8_t *remaining_code, const QueryContext &query) const {
        float result = 0.0f;
        for (size_t i = 0; i < code_dim_; ++i) {
            result += static_cast<float>(codeValue(remaining_code, i) & kRemainingMax) * query.rotated_residual[i];
        }
        return result;
    }

#if defined(__AVX2__)
    float dotRemainingUint8FloatAvx2(const uint8_t *remaining_code, const QueryContext &query) const {
        __m256 sum = _mm256_setzero_ps();
        const __m128i low_mask = _mm_set1_epi8(static_cast<char>(kRemainingMax));
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 8 <= pair_count; pair += 8) {
            const __m128i packed = _mm_loadl_epi64(reinterpret_cast<const __m128i *>(remaining_code + pair));
            const __m128i lo = _mm_and_si128(packed, low_mask);
            const __m128i hi = _mm_and_si128(_mm_srli_epi16(packed, 4), low_mask);
            const __m256 lo_f = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(lo));
            const __m256 hi_f = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(hi));
            sum = _mm256_add_ps(sum, _mm256_mul_ps(lo_f, _mm256_loadu_ps(query.rotated_residual_even.data() + pair)));
            sum = _mm256_add_ps(sum, _mm256_mul_ps(hi_f, _mm256_loadu_ps(query.rotated_residual_odd.data() + pair)));
        }

        alignas(32) float lanes[8];
        _mm256_store_ps(lanes, sum);
        float result = 0.0f;
        for (float lane : lanes) {
            result += lane;
        }
        for (; pair < pair_count; ++pair) {
            const uint8_t packed = remaining_code[pair];
            result += static_cast<float>(packed & kRemainingMax) * query.rotated_residual_even[pair];
            result += static_cast<float>((packed >> 4U) & kRemainingMax) * query.rotated_residual_odd[pair];
        }
        return result;
    }
#endif

#if defined(__AVX512F__) && defined(__AVX512BW__)
    float dotRemainingUint8FloatAvx512(const uint8_t *remaining_code, const QueryContext &query) const {
        __m512 sum = _mm512_setzero_ps();
        const __m128i low_mask = _mm_set1_epi8(static_cast<char>(kRemainingMax));
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 16 <= pair_count; pair += 16) {
            const __m128i packed = _mm_loadu_si128(reinterpret_cast<const __m128i *>(remaining_code + pair));
            const __m128i lo = _mm_and_si128(packed, low_mask);
            const __m128i hi = _mm_and_si128(_mm_srli_epi16(packed, 4), low_mask);
            const __m512 lo_f = _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(lo));
            const __m512 hi_f = _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(hi));
            sum = _mm512_add_ps(sum, _mm512_mul_ps(lo_f, _mm512_loadu_ps(query.rotated_residual_even.data() + pair)));
            sum = _mm512_add_ps(sum, _mm512_mul_ps(hi_f, _mm512_loadu_ps(query.rotated_residual_odd.data() + pair)));
        }

        float result = _mm512_reduce_add_ps(sum);
        for (; pair < pair_count; ++pair) {
            const uint8_t packed = remaining_code[pair];
            result += static_cast<float>(packed & kRemainingMax) * query.rotated_residual_even[pair];
            result += static_cast<float>((packed >> 4U) & kRemainingMax) * query.rotated_residual_odd[pair];
        }
        return result;
    }
#endif

    float nested4x4High4DotDispatch(
        const QueryContext &query,
        const uint8_t *high4_code,
        const float *scales) const {
#if defined(__AVX2__)
        if (residual_block_size_ == 16U) {
            return nested4x4High4DotAvx2(query, high4_code, scales);
        }
#endif
        return nested4x4High4DotScalar(query, high4_code, scales);
    }

    float nested4x4Full8DotDispatch(
        const QueryContext &query,
        const uint8_t *high4_code,
        const uint8_t *low4_code,
        const float *scales) const {
#if defined(__AVX2__)
        if (residual_block_size_ == 16U) {
            return nested4x4Full8DotAvx2(query, high4_code, low4_code, scales);
        }
#endif
        return nested4x4Full8DotScalar(query, high4_code, low4_code, scales);
    }

    float nested4x4High4DotScalar(
        const QueryContext &query,
        const uint8_t *high4_code,
        const float *scales) const {
        double dot = 0.0;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            for (size_t i = begin; i < end; ++i) {
                const float midpoint =
                    (static_cast<float>(codeValue(high4_code, i)) * 16.0f - 120.0f) * scale;
                dot += static_cast<double>(midpoint) *
                       static_cast<double>(query.rotated_residual[i]);
            }
        }
        return static_cast<float>(dot);
    }

    float nested4x4Full8DotScalar(
        const QueryContext &query,
        const uint8_t *high4_code,
        const uint8_t *low4_code,
        const float *scales) const {
        double dot = 0.0;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            for (size_t i = begin; i < end; ++i) {
                const uint8_t raw8 = static_cast<uint8_t>(
                    (codeValue(high4_code, i) << 4U) | codeValue(low4_code, i));
                const float decoded = (static_cast<float>(raw8) - 127.5f) * scale;
                dot += static_cast<double>(decoded) *
                       static_cast<double>(query.rotated_residual[i]);
            }
        }
        return static_cast<float>(dot);
    }

#if defined(__AVX2__)
    static float reduce256(__m256 value) {
        alignas(32) float lanes[8];
        _mm256_store_ps(lanes, value);
        return lanes[0] + lanes[1] + lanes[2] + lanes[3] +
               lanes[4] + lanes[5] + lanes[6] + lanes[7];
    }

    float nested4x4High4DotAvx2(
        const QueryContext &query,
        const uint8_t *high4_code,
        const float *scales) const {
        __m256 sum = _mm256_setzero_ps();
        const __m128i low_mask = _mm_set1_epi8(0x0F);
        const __m256 sixteen = _mm256_set1_ps(16.0f);
        const __m256 midpoint_offset = _mm256_set1_ps(120.0f);
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            size_t i = begin;
            const __m256 scale_v = _mm256_set1_ps(scale);
            for (; i + 16U <= end; i += 16U) {
                const __m128i packed = _mm_loadl_epi64(
                    reinterpret_cast<const __m128i *>(high4_code + (i >> 1U)));
                const __m128i lo = _mm_and_si128(packed, low_mask);
                const __m128i hi = _mm_and_si128(_mm_srli_epi16(packed, 4), low_mask);
                const __m128i vals = _mm_unpacklo_epi8(lo, hi);
                __m256 v0 = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(vals));
                __m256 v1 = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(_mm_srli_si128(vals, 8)));
                v0 = _mm256_mul_ps(_mm256_sub_ps(_mm256_mul_ps(v0, sixteen), midpoint_offset), scale_v);
                v1 = _mm256_mul_ps(_mm256_sub_ps(_mm256_mul_ps(v1, sixteen), midpoint_offset), scale_v);
                sum = _mm256_add_ps(
                    sum,
                    _mm256_mul_ps(v0, _mm256_loadu_ps(query.rotated_residual.data() + i)));
                sum = _mm256_add_ps(
                    sum,
                    _mm256_mul_ps(v1, _mm256_loadu_ps(query.rotated_residual.data() + i + 8U)));
            }
            for (; i < end; ++i) {
                const float midpoint =
                    (static_cast<float>(codeValue(high4_code, i)) * 16.0f - 120.0f) * scale;
                sum = _mm256_add_ps(
                    sum,
                    _mm256_set_ps(0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f,
                                  midpoint * query.rotated_residual[i]));
            }
        }
        return reduce256(sum);
    }

    float nested4x4Full8DotAvx2(
        const QueryContext &query,
        const uint8_t *high4_code,
        const uint8_t *low4_code,
        const float *scales) const {
        __m256 sum = _mm256_setzero_ps();
        const __m128i low_mask = _mm_set1_epi8(0x0F);
        const __m256 offset = _mm256_set1_ps(127.5f);
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            size_t i = begin;
            const __m256 scale_v = _mm256_set1_ps(scale);
            for (; i + 16U <= end; i += 16U) {
                const __m128i high_packed = _mm_loadl_epi64(
                    reinterpret_cast<const __m128i *>(high4_code + (i >> 1U)));
                const __m128i low_packed = _mm_loadl_epi64(
                    reinterpret_cast<const __m128i *>(low4_code + (i >> 1U)));
                const __m128i high_lo = _mm_and_si128(high_packed, low_mask);
                const __m128i high_hi = _mm_and_si128(_mm_srli_epi16(high_packed, 4), low_mask);
                const __m128i low_lo = _mm_and_si128(low_packed, low_mask);
                const __m128i low_hi = _mm_and_si128(_mm_srli_epi16(low_packed, 4), low_mask);
                const __m128i high_vals = _mm_unpacklo_epi8(high_lo, high_hi);
                const __m128i low_vals = _mm_unpacklo_epi8(low_lo, low_hi);
                const __m128i raw_vals =
                    _mm_or_si128(_mm_slli_epi16(high_vals, 4), low_vals);
                __m256 v0 = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(raw_vals));
                __m256 v1 = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(_mm_srli_si128(raw_vals, 8)));
                v0 = _mm256_mul_ps(_mm256_sub_ps(v0, offset), scale_v);
                v1 = _mm256_mul_ps(_mm256_sub_ps(v1, offset), scale_v);
                sum = _mm256_add_ps(
                    sum,
                    _mm256_mul_ps(v0, _mm256_loadu_ps(query.rotated_residual.data() + i)));
                sum = _mm256_add_ps(
                    sum,
                    _mm256_mul_ps(v1, _mm256_loadu_ps(query.rotated_residual.data() + i + 8U)));
            }
            for (; i < end; ++i) {
                const uint8_t raw8 = static_cast<uint8_t>(
                    (codeValue(high4_code, i) << 4U) | codeValue(low4_code, i));
                const float decoded = (static_cast<float>(raw8) - 127.5f) * scale;
                sum = _mm256_add_ps(
                    sum,
                    _mm256_set_ps(0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f,
                                  decoded * query.rotated_residual[i]));
            }
        }
        return reduce256(sum);
    }
#endif

    float shortCodeIpFromFullCodeAvxDispatch(const QueryContext &query, const uint8_t *code) const {
#if defined(__AVX512F__) && defined(__AVX512BW__)
        return shortCodeIpFromFullCodeAvx512(query, code);
#elif defined(__AVX2__)
        return shortCodeIpFromFullCodeAvx2(query, code);
#else
        return shortCodeIpFromFullCodeScalar(query, code);
#endif
    }

    float shortCodeIpFromFullCodeScalar(const QueryContext &query, const uint8_t *code) const {
        float selected_sum = 0.0f;
        for (size_t i = 0; i < code_dim_; ++i) {
            selected_sum += (codeValue(code, i) >> kRemainingBits) ? query.rotated_residual[i] : 0.0f;
        }
        return selected_sum - query.half_sum_residual;
    }

#if defined(__AVX2__)
    float shortCodeIpFromFullCodeAvx2(const QueryContext &query, const uint8_t *code) const {
        __m256 sum = _mm256_setzero_ps();
        const __m128i one_mask = _mm_set1_epi8(1);
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 8 <= pair_count; pair += 8) {
            const __m128i packed = _mm_loadl_epi64(reinterpret_cast<const __m128i *>(code + pair));
            const __m128i lo_bit = _mm_and_si128(_mm_srli_epi16(packed, kRemainingBits), one_mask);
            const __m128i hi_bit = _mm_and_si128(_mm_srli_epi16(packed, kTotalBits + kRemainingBits), one_mask);
            const __m256 lo_f = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(lo_bit));
            const __m256 hi_f = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(hi_bit));
            sum = _mm256_add_ps(sum, _mm256_mul_ps(lo_f, _mm256_loadu_ps(query.rotated_residual_even.data() + pair)));
            sum = _mm256_add_ps(sum, _mm256_mul_ps(hi_f, _mm256_loadu_ps(query.rotated_residual_odd.data() + pair)));
        }

        alignas(32) float lanes[8];
        _mm256_store_ps(lanes, sum);
        float selected_sum = 0.0f;
        for (float lane : lanes) {
            selected_sum += lane;
        }
        for (; pair < pair_count; ++pair) {
            const uint8_t packed = code[pair];
            selected_sum += ((packed >> kRemainingBits) & 1U) ? query.rotated_residual_even[pair] : 0.0f;
            selected_sum += ((packed >> (kTotalBits + kRemainingBits)) & 1U) ? query.rotated_residual_odd[pair] : 0.0f;
        }
        return selected_sum - query.half_sum_residual;
    }
#endif

#if defined(__AVX512F__) && defined(__AVX512BW__)
    float shortCodeIpFromFullCodeAvx512(const QueryContext &query, const uint8_t *code) const {
        __m512 sum = _mm512_setzero_ps();
        const __m128i one_mask = _mm_set1_epi8(1);
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 16 <= pair_count; pair += 16) {
            const __m128i packed = _mm_loadu_si128(reinterpret_cast<const __m128i *>(code + pair));
            const __m128i lo_bit = _mm_and_si128(_mm_srli_epi16(packed, kRemainingBits), one_mask);
            const __m128i hi_bit = _mm_and_si128(_mm_srli_epi16(packed, kTotalBits + kRemainingBits), one_mask);
            const __m512 lo_f = _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(lo_bit));
            const __m512 hi_f = _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(hi_bit));
            sum = _mm512_add_ps(sum, _mm512_mul_ps(lo_f, _mm512_loadu_ps(query.rotated_residual_even.data() + pair)));
            sum = _mm512_add_ps(sum, _mm512_mul_ps(hi_f, _mm512_loadu_ps(query.rotated_residual_odd.data() + pair)));
        }

        float selected_sum = _mm512_reduce_add_ps(sum);
        for (; pair < pair_count; ++pair) {
            const uint8_t packed = code[pair];
            selected_sum += ((packed >> kRemainingBits) & 1U) ? query.rotated_residual_even[pair] : 0.0f;
            selected_sum += ((packed >> (kTotalBits + kRemainingBits)) & 1U) ? query.rotated_residual_odd[pair] : 0.0f;
        }
        return selected_sum - query.half_sum_residual;
    }
#endif

    float topTwoBitsIpScalar(const QueryContext &query, const uint8_t *code) const {
        float sum = 0.0f;
        const size_t pair_count = code_dim_ >> 1U;
        for (size_t pair = 0; pair < pair_count; ++pair) {
            const uint8_t packed = code[pair];
            sum += static_cast<float>((packed >> 2U) & 0x03U) * query.rotated_residual_even[pair];
            sum += static_cast<float>((packed >> 6U) & 0x03U) * query.rotated_residual_odd[pair];
        }
        return sum;
    }

#if defined(__AVX2__)
    float topTwoBitsIpAvx2(const QueryContext &query, const uint8_t *code) const {
        __m256 sum = _mm256_setzero_ps();
        const __m128i mask = _mm_set1_epi8(0x03);
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 8 <= pair_count; pair += 8) {
            const __m128i packed = _mm_loadl_epi64(reinterpret_cast<const __m128i *>(code + pair));
            const __m128i lo = _mm_and_si128(_mm_srli_epi16(packed, 2), mask);
            const __m128i hi = _mm_and_si128(_mm_srli_epi16(packed, 6), mask);
            sum = _mm256_add_ps(sum, _mm256_mul_ps(
                _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(lo)),
                _mm256_loadu_ps(query.rotated_residual_even.data() + pair)));
            sum = _mm256_add_ps(sum, _mm256_mul_ps(
                _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(hi)),
                _mm256_loadu_ps(query.rotated_residual_odd.data() + pair)));
        }
        alignas(32) float lanes[8];
        _mm256_store_ps(lanes, sum);
        float result = 0.0f;
        for (float lane : lanes) result += lane;
        for (; pair < pair_count; ++pair) {
            const uint8_t packed = code[pair];
            result += static_cast<float>((packed >> 2U) & 0x03U) * query.rotated_residual_even[pair];
            result += static_cast<float>((packed >> 6U) & 0x03U) * query.rotated_residual_odd[pair];
        }
        return result;
    }
#endif

#if defined(__AVX512F__) && defined(__AVX512BW__)
    float topTwoBitsIpAvx512(const QueryContext &query, const uint8_t *code) const {
        __m512 sum = _mm512_setzero_ps();
        const __m128i mask = _mm_set1_epi8(0x03);
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 16 <= pair_count; pair += 16) {
            const __m128i packed = _mm_loadu_si128(reinterpret_cast<const __m128i *>(code + pair));
            const __m128i lo = _mm_and_si128(_mm_srli_epi16(packed, 2), mask);
            const __m128i hi = _mm_and_si128(_mm_srli_epi16(packed, 6), mask);
            sum = _mm512_add_ps(sum, _mm512_mul_ps(
                _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(lo)),
                _mm512_loadu_ps(query.rotated_residual_even.data() + pair)));
            sum = _mm512_add_ps(sum, _mm512_mul_ps(
                _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(hi)),
                _mm512_loadu_ps(query.rotated_residual_odd.data() + pair)));
        }
        float result = _mm512_reduce_add_ps(sum);
        for (; pair < pair_count; ++pair) {
            const uint8_t packed = code[pair];
            result += static_cast<float>((packed >> 2U) & 0x03U) * query.rotated_residual_even[pair];
            result += static_cast<float>((packed >> 6U) & 0x03U) * query.rotated_residual_odd[pair];
        }
        return result;
    }
#endif

    float topTwoBitsIpDispatch(const QueryContext &query, const uint8_t *code) const {
#if defined(__AVX512F__) && defined(__AVX512BW__)
        return topTwoBitsIpAvx512(query, code);
#elif defined(__AVX2__)
        return topTwoBitsIpAvx2(query, code);
#else
        return topTwoBitsIpScalar(query, code);
#endif
    }

    LongCodeIps longCodeIpsTurboScalar(const QueryContext &query, const uint8_t *code) const {
        float selected_sum = 0.0f;
        float remaining_ip = 0.0f;
        for (size_t i = 0; i < code_dim_; ++i) {
            const uint8_t value = turbo128CodeValue(code, i);
            selected_sum += (value >> kRemainingBits) ? query.rotated_residual[i] : 0.0f;
            remaining_ip += static_cast<float>(value & kRemainingMax) * query.rotated_residual[i];
        }
        return LongCodeIps{selected_sum - query.half_sum_residual, remaining_ip};
    }

#if defined(__AVX512F__)
    LongCodeIps longCodeIpsTurboAvx512(const QueryContext &query, const uint8_t *code) const {
        __m512 short_sum = _mm512_setzero_ps();
        __m512 remaining_sum = _mm512_setzero_ps();
        const __m512i nibble_mask = _mm512_set1_epi32(0x0F);
        const __m512i remaining_mask = _mm512_set1_epi32(static_cast<int>(kRemainingMax));
        const __m512i one_mask = _mm512_set1_epi32(1);
        for (size_t block = 0; block < code_dim_ / 128U; ++block) {
            const __m512i words = _mm512_loadu_si512(
                reinterpret_cast<const void *>(code + block * 64U));
            for (size_t slot = 0; slot < 8U; ++slot) {
                const __m512i values = _mm512_and_si512(
                    _mm512_srlv_epi32(words, _mm512_set1_epi32(static_cast<int>(slot * 4U))),
                    nibble_mask);
                const __m512 query_values = _mm512_loadu_ps(
                    query.rotated_residual.data() + block * 128U + slot * 16U);
                const __m512 remaining = _mm512_cvtepi32_ps(
                    _mm512_and_si512(values, remaining_mask));
                const __m512 short_bits = _mm512_cvtepi32_ps(
                    _mm512_and_si512(_mm512_srli_epi32(values, kRemainingBits), one_mask));
                remaining_sum = _mm512_add_ps(
                    remaining_sum, _mm512_mul_ps(remaining, query_values));
                short_sum = _mm512_add_ps(
                    short_sum, _mm512_mul_ps(short_bits, query_values));
            }
        }
        return LongCodeIps{
            _mm512_reduce_add_ps(short_sum) - query.half_sum_residual,
            _mm512_reduce_add_ps(remaining_sum)};
    }
#endif

    LongCodeIps longCodeIpsTurboDispatch(const QueryContext &query, const uint8_t *code) const {
#if defined(__AVX512F__)
        return longCodeIpsTurboAvx512(query, code);
#else
        return longCodeIpsTurboScalar(query, code);
#endif
    }

    LongCodeIps longCodeIpsDispatch(const QueryContext &query, const uint8_t *code) const {
        if (code_layout_ == RaBitQCodeLayout::Turbo128) {
            return longCodeIpsTurboDispatch(query, code);
        }
#if defined(__AVX512F__) && defined(__AVX512BW__)
        return longCodeIpsAvx512(query, code);
#elif defined(__AVX2__)
        return longCodeIpsAvx2(query, code);
#else
        return longCodeIpsScalar(query, code);
#endif
    }

    LongCodeIps longCodeIpsScalar(const QueryContext &query, const uint8_t *code) const {
        float selected_sum = 0.0f;
        float remaining_ip = 0.0f;
        const size_t pair_count = code_dim_ >> 1U;
        for (size_t pair = 0; pair < pair_count; ++pair) {
            const uint8_t packed = code[pair];
            const uint8_t lo = static_cast<uint8_t>(packed & 0x0FU);
            const uint8_t hi = static_cast<uint8_t>(packed >> 4U);
            selected_sum += (lo >> kRemainingBits) ? query.rotated_residual_even[pair] : 0.0f;
            selected_sum += (hi >> kRemainingBits) ? query.rotated_residual_odd[pair] : 0.0f;
            remaining_ip += static_cast<float>(lo & kRemainingMax) * query.rotated_residual_even[pair];
            remaining_ip += static_cast<float>(hi & kRemainingMax) * query.rotated_residual_odd[pair];
        }
        return LongCodeIps{selected_sum - query.half_sum_residual, remaining_ip};
    }

#if defined(__AVX2__)
    LongCodeIps longCodeIpsAvx2(const QueryContext &query, const uint8_t *code) const {
        __m256 short_sum = _mm256_setzero_ps();
        __m256 remaining_sum = _mm256_setzero_ps();
        const __m128i remaining_mask = _mm_set1_epi8(static_cast<char>(kRemainingMax));
        const __m128i one_mask = _mm_set1_epi8(1);
        const __m256i zero = _mm256_setzero_si256();
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 8 <= pair_count; pair += 8) {
            const __m128i packed = _mm_loadl_epi64(reinterpret_cast<const __m128i *>(code + pair));
            const __m128i lo = _mm_and_si128(packed, remaining_mask);
            const __m128i hi = _mm_and_si128(_mm_srli_epi16(packed, 4), remaining_mask);
            const __m128i lo_bit = _mm_and_si128(_mm_srli_epi16(packed, kRemainingBits), one_mask);
            const __m128i hi_bit = _mm_and_si128(_mm_srli_epi16(packed, kTotalBits + kRemainingBits), one_mask);
            const __m256 even_q = _mm256_loadu_ps(query.rotated_residual_even.data() + pair);
            const __m256 odd_q = _mm256_loadu_ps(query.rotated_residual_odd.data() + pair);
            remaining_sum = _mm256_add_ps(
                remaining_sum,
                _mm256_mul_ps(_mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(lo)), even_q));
            remaining_sum = _mm256_add_ps(
                remaining_sum,
                _mm256_mul_ps(_mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(hi)), odd_q));
            short_sum = _mm256_add_ps(
                short_sum,
                _mm256_and_ps(
                    _mm256_castsi256_ps(_mm256_cmpgt_epi32(
                        _mm256_cvtepu8_epi32(lo_bit), zero)),
                    even_q));
            short_sum = _mm256_add_ps(
                short_sum,
                _mm256_and_ps(
                    _mm256_castsi256_ps(_mm256_cmpgt_epi32(
                        _mm256_cvtepu8_epi32(hi_bit), zero)),
                    odd_q));
        }

        alignas(32) float short_lanes[8];
        alignas(32) float remaining_lanes[8];
        _mm256_store_ps(short_lanes, short_sum);
        _mm256_store_ps(remaining_lanes, remaining_sum);
        float selected_sum = 0.0f;
        float remaining_ip = 0.0f;
        for (size_t lane = 0; lane < 8; ++lane) {
            selected_sum += short_lanes[lane];
            remaining_ip += remaining_lanes[lane];
        }
        for (; pair < pair_count; ++pair) {
            const uint8_t packed = code[pair];
            const uint8_t lo = static_cast<uint8_t>(packed & 0x0FU);
            const uint8_t hi = static_cast<uint8_t>(packed >> 4U);
            selected_sum += (lo >> kRemainingBits) ? query.rotated_residual_even[pair] : 0.0f;
            selected_sum += (hi >> kRemainingBits) ? query.rotated_residual_odd[pair] : 0.0f;
            remaining_ip += static_cast<float>(lo & kRemainingMax) * query.rotated_residual_even[pair];
            remaining_ip += static_cast<float>(hi & kRemainingMax) * query.rotated_residual_odd[pair];
        }
        return LongCodeIps{selected_sum - query.half_sum_residual, remaining_ip};
    }
#endif

#if defined(__AVX512F__) && defined(__AVX512BW__)
    LongCodeIps longCodeIpsAvx512(const QueryContext &query, const uint8_t *code) const {
        __m512 short_sum = _mm512_setzero_ps();
        __m512 remaining_sum = _mm512_setzero_ps();
        const __m128i remaining_mask = _mm_set1_epi8(static_cast<char>(kRemainingMax));
        const __m128i nibble_mask = _mm_set1_epi8(0x0F);
        const __m128i selected_threshold = _mm_set1_epi8(
            static_cast<char>(kRemainingMax));
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 16 <= pair_count; pair += 16) {
            const __m128i packed = _mm_loadu_si128(reinterpret_cast<const __m128i *>(code + pair));
            const __m128i lo = _mm_and_si128(packed, remaining_mask);
            const __m128i hi = _mm_and_si128(_mm_srli_epi16(packed, 4), remaining_mask);
            const __m128i raw_lo = _mm_and_si128(packed, nibble_mask);
            const __m128i raw_hi = _mm_and_si128(
                _mm_srli_epi16(packed, 4), nibble_mask);
            const __mmask16 lo_selected = static_cast<__mmask16>(
                _mm_movemask_epi8(_mm_cmpgt_epi8(raw_lo, selected_threshold)));
            const __mmask16 hi_selected = static_cast<__mmask16>(
                _mm_movemask_epi8(_mm_cmpgt_epi8(raw_hi, selected_threshold)));
            const __m512 even_q = _mm512_loadu_ps(query.rotated_residual_even.data() + pair);
            const __m512 odd_q = _mm512_loadu_ps(query.rotated_residual_odd.data() + pair);
            remaining_sum = _mm512_add_ps(
                remaining_sum,
                _mm512_mul_ps(_mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(lo)), even_q));
            remaining_sum = _mm512_add_ps(
                remaining_sum,
                _mm512_mul_ps(_mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(hi)), odd_q));
            short_sum = _mm512_mask_add_ps(
                short_sum, lo_selected, short_sum, even_q);
            short_sum = _mm512_mask_add_ps(
                short_sum, hi_selected, short_sum, odd_q);
        }

        float selected_sum = _mm512_reduce_add_ps(short_sum);
        float remaining_ip = _mm512_reduce_add_ps(remaining_sum);
        for (; pair < pair_count; ++pair) {
            const uint8_t packed = code[pair];
            const uint8_t lo = static_cast<uint8_t>(packed & 0x0FU);
            const uint8_t hi = static_cast<uint8_t>(packed >> 4U);
            selected_sum += (lo >> kRemainingBits) ? query.rotated_residual_even[pair] : 0.0f;
            selected_sum += (hi >> kRemainingBits) ? query.rotated_residual_odd[pair] : 0.0f;
            remaining_ip += static_cast<float>(lo & kRemainingMax) * query.rotated_residual_even[pair];
            remaining_ip += static_cast<float>(hi & kRemainingMax) * query.rotated_residual_odd[pair];
        }
        return LongCodeIps{selected_sum - query.half_sum_residual, remaining_ip};
    }
#endif

    float queryDistanceLong(const QueryContext &query, const void *encoded) const {
        const EncodedHeader header = loadHeader(encoded);
        if (header.long_scale <= 0.0f || !std::isfinite(header.long_scale)) {
            return header.norm_sqr + query.query_norm_sqr;
        }
        const LongCodeIps ips = longCodeIpsDispatch(query, codeBytes(encoded));
        return queryDistanceLongWithIps(query, header, ips.short_ip, ips.remaining_ip);
    }

    float queryDistanceLongWithShortIp(
        const QueryContext &query,
        const void *encoded,
        const EncodedHeader &header,
        float short_ip) const {
        if (header.long_scale <= 0.0f || !std::isfinite(header.long_scale)) {
            return header.norm_sqr + query.query_norm_sqr;
        }
        const float remaining_ip = dotRemainingUint8FloatAvxDispatch(codeBytes(encoded), query);
        return queryDistanceLongWithIps(query, header, short_ip, remaining_ip);
    }

    float queryDistanceLongWithIps(
        const QueryContext &query,
        const EncodedHeader &header,
        float short_ip,
        float remaining_ip) const {
        const float signed_long_ip =
            static_cast<float>(kMsbWeight) * short_ip + remaining_ip -
            static_cast<float>(kRemainingMax) * query.half_sum_residual;
        const float estimated_inner = header.long_scale * signed_long_ip;
        return header.norm_sqr + query.query_norm_sqr -
               estimated_inner;
    }

    float queryDistanceLowerBound(const QueryContext &query, const void *encoded) const {
        const EncodedHeader header = loadHeader(encoded);
        const ShortCodeFactors factors = *shortFactors(encoded);
        const float ip_xb_q = shortCodeIp(query, codeBytes(encoded));
        return queryDistanceLowerBoundWithShortIp(query, header, factors, ip_xb_q);
    }

    // Lower bound in the same distance system as queryDistanceLong(). The
    // remaining three bits are bounded, not decoded.
    float queryDistanceShortLowerBoundForLong(
        const QueryContext &query,
        const void *encoded) const {
        const EncodedHeader header = loadHeader(encoded);
        if (header.long_scale <= 0.0f || !std::isfinite(header.long_scale))
            return header.norm_sqr + query.query_norm_sqr;
        const float short_ip = shortCodeIp(query, codeBytes(encoded));
        const float maximum_remaining_ip =
            static_cast<float>(kRemainingMax) * query.positive_sum_residual;
        const float maximum_signed_ip =
            static_cast<float>(kMsbWeight) * short_ip + maximum_remaining_ip -
            static_cast<float>(kRemainingMax) * query.half_sum_residual;
        return header.norm_sqr + query.query_norm_sqr -
            header.long_scale * maximum_signed_ip;
    }

    float queryDistanceTwoBitLowerBoundForLong(
        const QueryContext &query,
        const void *encoded) const {
        const EncodedHeader header = loadHeader(encoded);
        if (header.long_scale <= 0.0f || !std::isfinite(header.long_scale))
            return header.norm_sqr + query.query_norm_sqr;
        const float top_two_ip = topTwoBitsIpDispatch(query, codeBytes(encoded));
        const float maximum_signed_ip =
            4.0f * top_two_ip - 7.5f * query.rotated_residual_sum +
            3.0f * query.positive_sum_residual;
        return header.norm_sqr + query.query_norm_sqr -
            header.long_scale * maximum_signed_ip;
    }

    float queryDistanceLowerBoundWithShortIp(
        const QueryContext &query,
        const EncodedHeader &header,
        const ShortCodeFactors &factors,
        float ip_xb_q) const {
        const float compensated_ip = ip_xb_q * factors.short_scale;
        const float err = factors.error_scale * query.query_norm;
        return header.norm_sqr + query.query_norm_sqr - (compensated_ip + err);
    }

    DistanceInterval computeShortDistanceIntervalTyped(const QueryContext &query, const void *encoded) const {
        const EncodedHeader header = loadHeader(encoded);
        const ShortCodeFactors factors = *shortFactors(encoded);
        const float short_ip = shortCodeIp(query, codeBytes(encoded));
        const float compensated_ip = short_ip * factors.short_scale;
        const float err = factors.error_scale * query.query_norm;
        const float estimate = header.norm_sqr + query.query_norm_sqr - compensated_ip;
        return DistanceInterval{
            estimate,
            estimate - err,
            estimate + err};
    }

    DistanceInterval computeLongDistanceIntervalTyped(const QueryContext &query, const void *encoded) const {
        const float distance = queryDistanceLong(query, encoded);
        const ShortCodeFactors factors = *shortFactors(encoded);
        const float err = factors.error_scale * query.query_norm;
        return DistanceInterval{
            distance,
            distance - err,
            distance + err};
    }

    DistanceInterval computeResidualDistanceIntervalTyped(
        const QueryContext &query,
        const void *encoded,
        float long_distance) const {
        if (external_residual_storage_) {
            throw std::runtime_error("single residual distance cannot use external residual storage");
        }
        const ResidualCodeFactors factors = *residualFactors(encoded);
        const float *residual_scales = decodeResidualScales(residualScaleBytes(encoded));
        const float q_residual_ip =
            residualIp(query, residualCodeBytes(encoded), residual_scales);
        (void) factors;
        const float distance = long_distance - 2.0f * q_residual_ip;
        const float err = residualDistanceErrorBound(query, residual_scales);
        return DistanceInterval{distance, distance - err, distance + err};
    }

    DistanceInterval computeResidualDistanceIntervalFromRecord(
        const QueryContext &query,
        const void *record,
        float long_distance) const {
        const ResidualCodeFactors factors = *residualFactorsFromRecord(record);
        const float *residual_scales = decodeResidualScales(residualScalesFromRecord(record));
        const float q_residual_ip =
            residualIp(query, residualCodeBytesFromRecord(record), residual_scales);
        (void) factors;
        const float distance = long_distance - 2.0f * q_residual_ip;
        const float err = residualDistanceErrorBound(query, residual_scales);
        return DistanceInterval{distance, distance - err, distance + err};
    }

    float nested4x4ResidualDistance(
        const QueryContext &query,
        const void *encoded,
        const uint8_t *residual_code,
        float long_distance) const {
        const float *residual_scales = decodeResidualScales(residualScaleBytes(encoded));
        const float q_residual_ip =
            residualIp(query, residual_code, residual_scales);
        const ShortCodeFactors correction = *shortFactors(encoded);
        return long_distance - 2.0f * q_residual_ip +
               2.0f * correction.error_scale + correction.short_scale;
    }

    void encodeVectorNested4x4Full(const float *raw_vector, void *encoded_out) const {
        if (centroid_count_ != 1) {
            throw std::runtime_error("nested4x4 layout does not support multi-centroid RaBitQ");
        }
        thread_local std::vector<float> residual;
        residual.assign(dim_, 0.0f);
        float residual_norm_sqr = 0.0f;
        for (size_t i = 0; i < dim_; ++i) {
            residual[i] = raw_vector[i] - centroids_[i];
            residual_norm_sqr += residual[i] * residual[i];
        }

        const float residual_norm = std::sqrt(residual_norm_sqr);
        if (residual_norm > 0.0f) {
            const float inv_norm = 1.0f / residual_norm;
            for (float &value : residual) {
                value *= inv_norm;
            }
        }

        thread_local std::vector<float> rotated_unit;
        rotate(residual.data(), rotated_unit);

        EncodedHeader header{0.0f, 0.0f};
        uint8_t *code = codeBytesFull(encoded_out);
        uint8_t *residual_code = residualCodeBytes(encoded_out);
        float *residual_scales = residualScales(encoded_out);
        ShortCodeFactors *factors = shortFactors(encoded_out);
        header.norm_sqr = residual_norm_sqr;
        std::memset(static_cast<char *>(encoded_out), 0, full_data_size_);
        *centroidIdFull(encoded_out) = 0;
        *factors = ShortCodeFactors{0.0f, 0.0f};

        thread_local std::vector<float> abs_unit;
        thread_local std::vector<uint8_t> abs_code;
        abs_unit.assign(code_dim_, 0.0f);
        abs_code.assign(code_dim_, 0);
        for (size_t i = 0; i < code_dim_; ++i) {
            abs_unit[i] = std::abs(rotated_unit[i]);
        }
        float ip_norm = 1.0f;
        fastQuantizeAbs(abs_unit.data(), abs_code.data(), ip_norm);
        header.long_scale = 2.0f * residual_norm * ip_norm;

        double o_obar = 0.0;
        double half_l1_norm = 0.0;
        const double inv_sqrt_d = 1.0 / std::sqrt(static_cast<double>(code_dim_));
        for (size_t i = 0; i < code_dim_; ++i) {
            const bool positive = rotated_unit[i] > 0.0f;
            const uint8_t magnitude = abs_code[i];
            const uint8_t packed_value = positive
                                             ? static_cast<uint8_t>(kMsbWeight + magnitude)
                                             : static_cast<uint8_t>(kRemainingMax - magnitude);
            setCodeValue(code, i, packed_value);

            const double sign = positive ? 1.0 : -1.0;
            o_obar += static_cast<double>(rotated_unit[i]) * sign * inv_sqrt_d;
            half_l1_norm += 0.5 * std::abs(static_cast<double>(rotated_unit[i]));
        }
        if (residual_norm > 0.0f && half_l1_norm > 1e-12) {
            factors->short_scale = static_cast<float>(2.0 * residual_norm / half_l1_norm);
            if (!std::isfinite(o_obar)) {
                o_obar = 0.8;
            }
            o_obar = std::min(0.999999, std::max(1e-6, o_obar));
            const double o2 = o_obar * o_obar;
            const double fac_err_bound =
                std::ldexp(kExtendedRaBitQErrorConstant, -static_cast<int>(kTotalBits)) /
                std::sqrt(static_cast<double>(code_dim_));
            factors->error_scale = static_cast<float>(
                std::sqrt(std::max(0.0, (1.0 - o2) / o2)) * fac_err_bound * 2.0 * residual_norm);
        }

        thread_local std::vector<float> quantization_error;
        quantization_error.assign(code_dim_, 0.0f);
        for (size_t i = 0; i < code_dim_; ++i) {
            const double true_value = static_cast<double>(residual_norm) *
                                      static_cast<double>(rotated_unit[i]);
            const double x4_value = 0.5 * static_cast<double>(header.long_scale) *
                                    (static_cast<double>(codeValue(code, i)) -
                                     static_cast<double>(kUnsignedOffset));
            quantization_error[i] = static_cast<float>(true_value - x4_value);
        }

        thread_local std::vector<int32_t> residual_codes;
        residual_codes.assign(code_dim_, residual_bits_ == 1 ? -1 : 0);
        const int32_t residual_qmax = residual_quantized_max(residual_bits_);
        const int32_t residual_qmin = -residual_qmax;
        double decoded_residual_norm_sqr = 0.0;
        double x4_residual_ip = 0.0;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            const float residual_scale = residual_config_.mse_optimal_scale
                ? residualMseOptimalScale(
                    quantization_error,
                    begin,
                    end,
                    residual_bits_,
                    residual_qmin,
                    residual_qmax)
                : residualMaxAbsScale(
                    quantization_error,
                    begin,
                    end,
                    residual_bits_,
                    residual_qmax);
            residual_scales[block] = residual_scale;
            if (residual_scale == 0.0f || !std::isfinite(residual_scale)) {
                continue;
            }
            for (size_t i = begin; i < end; ++i) {
                residual_codes[i] = residual_bits_ == 1
                    ? (quantization_error[i] >= 0.0f ? 1 : -1)
                    : std::max(
                        residual_qmin,
                        std::min(
                            residual_qmax,
                            static_cast<int32_t>(std::round(
                                static_cast<double>(quantization_error[i]) /
                                     static_cast<double>(residual_scale)))));
            }
        }
        pack_residual_codes(residual_codes.data(), code_dim_, residual_bits_, residual_code);
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float residual_scale = residual_scales[block];
            if (residual_scale == 0.0f || !std::isfinite(residual_scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            for (size_t i = begin; i < end; ++i) {
                const double decoded_residual =
                    static_cast<double>(residual_scale) *
                    static_cast<double>(residual_codes[i]);
                const double x4_value = 0.5 * static_cast<double>(header.long_scale) *
                                        (static_cast<double>(codeValue(code, i)) -
                                         static_cast<double>(kUnsignedOffset));
                decoded_residual_norm_sqr += decoded_residual * decoded_residual;
                x4_residual_ip += x4_value * decoded_residual;
            }
        }
        factors->short_scale = static_cast<float>(decoded_residual_norm_sqr);
        factors->error_scale = static_cast<float>(x4_residual_ip);
        std::memcpy(encoded_out, &header, sizeof(header));
    }

    float shortCodeIp(const QueryContext &query, const uint8_t *full_code) const {
        if (code_layout_ == RaBitQCodeLayout::Turbo128) {
            return longCodeIpsTurboDispatch(query, full_code).short_ip;
        }
        return shortCodeIpFromFullCodeAvxDispatch(query, full_code);
    }

    float residualSignIp(const QueryContext &query, const uint8_t *residual_code) const {
        ensureResidualNibbleLut(query);
        const float *lut = query.residual_nibble_lut.data();
        float positive_sum = 0.0f;
        size_t group = 0;
        for (size_t byte_index = 0; byte_index < residual_code_bytes_; ++byte_index) {
            const uint8_t packed = residual_code[byte_index];
            positive_sum += lut[(group << 4U) + (packed & 0x0FU)];
            ++group;
            if (group < ((code_dim_ + 3U) >> 2U)) {
                positive_sum += lut[(group << 4U) + (packed >> 4U)];
                ++group;
            }
        }
        return 2.0f * positive_sum - query.rotated_residual_sum;
    }

    float residualIp(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        switch (residual_bits_) {
            case 1:
                return residual_block_size_ == kResidualBlockSize
                    ? residualInt1IpLut(query, residual_code, residual_scales)
                    : residualInt1IpScalar(query, residual_code, residual_scales);
            case 2:
                return residual_block_size_ == kResidualBlockSize
                    ? residualInt2IpLut(query, residual_code, residual_scales)
                    : residualInt2IpScalar(query, residual_code, residual_scales);
            case 4:
                return residual_block_size_ == kResidualBlockSize
                    ? residualInt4IpAvxDispatch(query, residual_code, residual_scales)
                    : residualInt4IpScalar(query, residual_code, residual_scales);
            case 8:
                return residualInt8IpAvxDispatch(query, residual_code, residual_scales);
            case 16:
                return residualInt16IpAvxDispatch(query, residual_code, residual_scales);
            default:
                throw std::logic_error("unsupported residual bits in residualIp");
        }
    }

    float residualDistanceErrorBound(const QueryContext &query, const float *residual_scales) const {
        double residual_error_norm_sqr = 0.0;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            const double per_dim_error = residual_coordinate_error_bound(scale, residual_bits_);
            residual_error_norm_sqr +=
                static_cast<double>(end - begin) * per_dim_error * per_dim_error;
        }
        return static_cast<float>(
            2.0 * static_cast<double>(query.query_norm) * std::sqrt(residual_error_norm_sqr));
    }

    float residualPackedIpScalar(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        float result = 0.0f;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            float block_ip = 0.0f;
            for (size_t i = begin; i < end; ++i) {
                block_ip += static_cast<float>(residualQuantizedValue(residual_code, i)) *
                            query.rotated_residual[i];
            }
            result += scale * block_ip;
        }
        return result;
    }

    float residualInt1IpScalar(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        float result = 0.0f;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            float block_ip = 0.0f;
            for (size_t i = begin; i < end; ++i) {
                const int32_t q =
                    (residual_code[i >> 3U] & static_cast<uint8_t>(1U << (i & 7U))) ? 1 : -1;
                block_ip += static_cast<float>(q) * query.rotated_residual[i];
            }
            result += scale * block_ip;
        }
        return result;
    }

    float residualInt1IpLut(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        ensureResidualNibbleLut(query);
        const float *lut = query.residual_nibble_lut.data();
        float result = 0.0f;
        constexpr size_t groups_per_block = kResidualBlockSize / 4U;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t group_begin = block * groups_per_block;
            const size_t group_end = std::min((code_dim_ + 3U) >> 2U, group_begin + groups_per_block);
            float positive_sum = 0.0f;
            float block_sum = 0.0f;
            for (size_t group = group_begin; group < group_end; ++group) {
                const uint8_t packed = residual_code[group >> 1U];
                const uint8_t nibble = (group & 1U)
                    ? static_cast<uint8_t>(packed >> 4U)
                    : static_cast<uint8_t>(packed & 0x0FU);
                const float *group_lut = lut + (group << 4U);
                positive_sum += group_lut[nibble];
                block_sum += group_lut[15U];
            }
            result += scale * (2.0f * positive_sum - block_sum);
        }
        return result;
    }

    float residualInt2IpScalar(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        float result = 0.0f;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            float block_ip = 0.0f;
            for (size_t i = begin; i < end; ++i) {
                const uint8_t raw = static_cast<uint8_t>(
                    (residual_code[i >> 2U] >> ((i & 3U) << 1U)) & 0x03U);
                const int32_t q = (raw & 0x02U) ? static_cast<int32_t>(raw) - 4 : raw;
                block_ip += static_cast<float>(q) * query.rotated_residual[i];
            }
            result += scale * block_ip;
        }
        return result;
    }

    float residualInt2IpLut(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        ensureResidual2BitLut(query);
        const float *lut = query.residual_2bit_lut.data();
        float result = 0.0f;
        constexpr size_t groups_per_block = kResidualBlockSize / 4U;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t group_begin = block * groups_per_block;
            const size_t group_end = std::min((code_dim_ + 3U) >> 2U, group_begin + groups_per_block);
            float block_ip = 0.0f;
            for (size_t group = group_begin; group < group_end; ++group) {
                block_ip += lut[(group << 8U) + residual_code[group]];
            }
            result += scale * block_ip;
        }
        return result;
    }

    float residualInt8IpAvxDispatch(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
#if defined(__AVX2__)
        return residualInt8IpAvx2(query, residual_code, residual_scales);
#else
        return residualInt8IpScalar(query, residual_code, residual_scales);
#endif
    }

    float residualInt8IpScalar(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        float result = 0.0f;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            float block_ip = 0.0f;
            for (size_t i = begin; i < end; ++i) {
                const int32_t q = static_cast<int32_t>(static_cast<int8_t>(residual_code[i]));
                block_ip += static_cast<float>(q) * query.rotated_residual[i];
            }
            result += scale * block_ip;
        }
        return result;
    }

    float residualInt16IpAvxDispatch(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
#if defined(__AVX2__)
        return residualInt16IpAvx2(query, residual_code, residual_scales);
#else
        return residualInt16IpScalar(query, residual_code, residual_scales);
#endif
    }

    float residualInt16IpScalar(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        float result = 0.0f;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            double block_ip = 0.0;
            for (size_t i = begin; i < end; ++i) {
                const size_t offset = 2U * i;
                const uint16_t raw = static_cast<uint16_t>(residual_code[offset]) |
                                     static_cast<uint16_t>(residual_code[offset + 1U] << 8U);
                const int32_t q = static_cast<int32_t>(static_cast<int16_t>(raw));
                block_ip += static_cast<double>(q) * static_cast<double>(query.rotated_residual[i]);
            }
            result += static_cast<float>(static_cast<double>(scale) * block_ip);
        }
        return result;
    }

#if defined(__AVX2__)
    float residualInt8IpAvx2(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        float result = 0.0f;
        alignas(32) float lanes[8];
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            __m256 sum = _mm256_setzero_ps();
            size_t i = begin;
            for (; i + 16U <= end; i += 16U) {
                const __m128i packed = _mm_loadu_si128(
                    reinterpret_cast<const __m128i *>(residual_code + i));
                const __m256 lo = _mm256_cvtepi32_ps(_mm256_cvtepi8_epi32(packed));
                const __m128i hi8 = _mm_srli_si128(packed, 8);
                const __m256 hi = _mm256_cvtepi32_ps(_mm256_cvtepi8_epi32(hi8));
                sum = _mm256_add_ps(
                    sum,
                    _mm256_mul_ps(lo, _mm256_loadu_ps(query.rotated_residual.data() + i)));
                sum = _mm256_add_ps(
                    sum,
                    _mm256_mul_ps(hi, _mm256_loadu_ps(query.rotated_residual.data() + i + 8U)));
            }
            _mm256_store_ps(lanes, sum);
            float block_ip = lanes[0] + lanes[1] + lanes[2] + lanes[3] +
                             lanes[4] + lanes[5] + lanes[6] + lanes[7];
            for (; i < end; ++i) {
                const int32_t q = static_cast<int32_t>(static_cast<int8_t>(residual_code[i]));
                block_ip += static_cast<float>(q) * query.rotated_residual[i];
            }
            result += scale * block_ip;
        }
        return result;
    }

    float residualInt16IpAvx2(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        float result = 0.0f;
        alignas(32) float lanes[8];
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            __m256 sum = _mm256_setzero_ps();
            size_t i = begin;
            for (; i + 16U <= end; i += 16U) {
                const __m256i packed = _mm256_loadu_si256(
                    reinterpret_cast<const __m256i *>(residual_code + 2U * i));
                const __m128i lo16 = _mm256_castsi256_si128(packed);
                const __m128i hi16 = _mm256_extracti128_si256(packed, 1);
                const __m256 lo = _mm256_cvtepi32_ps(_mm256_cvtepi16_epi32(lo16));
                const __m256 hi = _mm256_cvtepi32_ps(_mm256_cvtepi16_epi32(hi16));
                sum = _mm256_add_ps(
                    sum,
                    _mm256_mul_ps(lo, _mm256_loadu_ps(query.rotated_residual.data() + i)));
                sum = _mm256_add_ps(
                    sum,
                    _mm256_mul_ps(hi, _mm256_loadu_ps(query.rotated_residual.data() + i + 8U)));
            }
            _mm256_store_ps(lanes, sum);
            double block_ip = static_cast<double>(lanes[0] + lanes[1] + lanes[2] + lanes[3] +
                                                  lanes[4] + lanes[5] + lanes[6] + lanes[7]);
            for (; i < end; ++i) {
                const size_t offset = 2U * i;
                const uint16_t raw = static_cast<uint16_t>(residual_code[offset]) |
                                     static_cast<uint16_t>(residual_code[offset + 1U] << 8U);
                const int32_t q = static_cast<int32_t>(static_cast<int16_t>(raw));
                block_ip += static_cast<double>(q) * static_cast<double>(query.rotated_residual[i]);
            }
            result += static_cast<float>(static_cast<double>(scale) * block_ip);
        }
        return result;
    }
#endif

    float residualInt4IpAvxDispatch(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
#if defined(__AVX2__)
        return residualInt4IpAvx2(query, residual_code, residual_scales);
#else
        return residualInt4IpScalar(query, residual_code, residual_scales);
#endif
    }

    float residualInt4IpScalar(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        float result = 0.0f;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            float block_ip = 0.0f;
            for (size_t i = begin; i < end; ++i) {
                const uint8_t raw = codeValue(residual_code, i);
                const int32_t q = raw >= 8U ? static_cast<int32_t>(raw) - 16 : raw;
                block_ip += static_cast<float>(q) * query.rotated_residual[i];
            }
            result += scale * block_ip;
        }
        return result;
    }

#if defined(__AVX2__)
    float residualInt4IpAvx2(
        const QueryContext &query,
        const uint8_t *residual_code,
        const float *residual_scales) const {
        float result = 0.0f;
        const __m128i low_mask = _mm_set1_epi8(0x0F);
        const __m256i seven = _mm256_set1_epi32(7);
        const __m256i sixteen = _mm256_set1_epi32(16);
        alignas(32) float lanes[8];
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const float scale = residual_scales[block];
            if (scale == 0.0f || !std::isfinite(scale)) {
                continue;
            }
            const size_t pair = block * (kResidualBlockSize >> 1U);
            const __m128i packed = _mm_loadl_epi64(
                reinterpret_cast<const __m128i *>(residual_code + pair));
            const __m128i lo8 = _mm_and_si128(packed, low_mask);
            const __m128i hi8 = _mm_and_si128(_mm_srli_epi16(packed, 4), low_mask);

            __m256i lo32 = _mm256_cvtepu8_epi32(lo8);
            __m256i hi32 = _mm256_cvtepu8_epi32(hi8);
            lo32 = _mm256_sub_epi32(
                lo32,
                _mm256_and_si256(_mm256_cmpgt_epi32(lo32, seven), sixteen));
            hi32 = _mm256_sub_epi32(
                hi32,
                _mm256_and_si256(_mm256_cmpgt_epi32(hi32, seven), sixteen));

            const __m256 lo_f = _mm256_cvtepi32_ps(lo32);
            const __m256 hi_f = _mm256_cvtepi32_ps(hi32);
            __m256 sum = _mm256_mul_ps(
                lo_f,
                _mm256_loadu_ps(query.rotated_residual_even.data() + pair));
            sum = _mm256_add_ps(
                sum,
                _mm256_mul_ps(
                    hi_f,
                    _mm256_loadu_ps(query.rotated_residual_odd.data() + pair)));
            _mm256_store_ps(lanes, sum);
            float block_ip = 0.0f;
            for (float lane : lanes) {
                block_ip += lane;
            }
            result += scale * block_ip;
        }
        return result;
    }
#endif

    void ensureResidualNibbleLut(const QueryContext &query) const {
        if (query.residual_nibble_lut_ready) {
            return;
        }
        const size_t group_count = (code_dim_ + 3U) >> 2U;
        query.residual_nibble_lut.assign(group_count * 16U, 0.0f);
        for (size_t group = 0; group < group_count; ++group) {
            const size_t base_dim = group << 2U;
            float values[4] = {0.0f, 0.0f, 0.0f, 0.0f};
            for (size_t lane = 0; lane < 4U; ++lane) {
                const size_t dim = base_dim + lane;
                if (dim < code_dim_) {
                    values[lane] = query.rotated_residual[dim];
                }
            }
            float *group_lut = query.residual_nibble_lut.data() + (group << 4U);
            for (uint8_t mask = 1; mask < 16U; ++mask) {
                const uint8_t lsb = static_cast<uint8_t>(mask & static_cast<uint8_t>(-mask));
                const uint8_t lane =
                    lsb == 1U ? 0U :
                    lsb == 2U ? 1U :
                    lsb == 4U ? 2U : 3U;
                group_lut[mask] = group_lut[mask ^ lsb] + values[lane];
            }
        }
        query.residual_nibble_lut_ready = true;
    }

    void ensureResidual2BitLut(const QueryContext &query) const {
        if (query.residual_2bit_lut_ready) {
            return;
        }
        const size_t group_count = (code_dim_ + 3U) >> 2U;
        query.residual_2bit_lut.assign(group_count * 256U, 0.0f);
        for (size_t group = 0; group < group_count; ++group) {
            const size_t base_dim = group << 2U;
            float values[4] = {0.0f, 0.0f, 0.0f, 0.0f};
            for (size_t lane = 0; lane < 4U; ++lane) {
                const size_t dim = base_dim + lane;
                if (dim < code_dim_) {
                    values[lane] = query.rotated_residual[dim];
                }
            }
            float *group_lut = query.residual_2bit_lut.data() + (group << 8U);
            for (uint32_t packed = 0; packed < 256U; ++packed) {
                float sum = 0.0f;
                for (size_t lane = 0; lane < 4U; ++lane) {
                    const uint32_t raw = (packed >> (lane << 1U)) & 0x03U;
                    const int32_t q = (raw & 0x02U) ? static_cast<int32_t>(raw) - 4
                                                    : static_cast<int32_t>(raw);
                    sum += static_cast<float>(q) * values[lane];
                }
                group_lut[packed] = sum;
            }
        }
        query.residual_2bit_lut_ready = true;
    }

    static void setResidualSignBit(uint8_t *residual_code, size_t index, bool positive) {
        const uint8_t mask = static_cast<uint8_t>(1U << (index & 7U));
        if (positive) {
            residual_code[index >> 3U] = static_cast<uint8_t>(residual_code[index >> 3U] | mask);
        } else {
            residual_code[index >> 3U] = static_cast<uint8_t>(residual_code[index >> 3U] & ~mask);
        }
    }

    void batchShortCodeIp(
        const QueryContext &query,
        const void *const *data_points,
        size_t count,
        float *short_ips) const {
        for (size_t i = 0; i < count; ++i) {
            short_ips[i] = shortCodeIp(query, codeBytes(data_points[i]));
        }
    }

    static int32_t centeredNibbleProductTimesFour(uint8_t lhs, uint8_t rhs) {
        return (2 * static_cast<int32_t>(lhs) - 15) *
               (2 * static_cast<int32_t>(rhs) - 15);
    }

    int64_t centeredNibbleDotTimesFourScalar(
        const uint8_t *lhs,
        const uint8_t *rhs,
        size_t pair_begin = 0) const {
        const size_t pair_count = code_dim_ >> 1U;
        int64_t sum = 0;
        for (size_t pair = pair_begin; pair < pair_count; ++pair) {
            const uint8_t lhs_byte = lhs[pair];
            const uint8_t rhs_byte = rhs[pair];
            sum += centeredNibbleProductTimesFour(
                static_cast<uint8_t>(lhs_byte & 0x0FU),
                static_cast<uint8_t>(rhs_byte & 0x0FU));
            sum += centeredNibbleProductTimesFour(
                static_cast<uint8_t>(lhs_byte >> 4U),
                static_cast<uint8_t>(rhs_byte >> 4U));
        }
        if ((code_dim_ & 1U) != 0U) {
            sum += centeredNibbleProductTimesFour(
                static_cast<uint8_t>(lhs[pair_count] & 0x0FU),
                static_cast<uint8_t>(rhs[pair_count] & 0x0FU));
        }
        return sum;
    }

#if defined(__AVX512F__) && defined(__AVX512BW__) && defined(__AVX512VNNI__)
    int64_t centeredNibbleDotTimesFourAvx512Vnni(
        const uint8_t *lhs,
        const uint8_t *rhs) const {
        const __m512i nibble_mask = _mm512_set1_epi8(0x0F);
        const __m512i fifteen = _mm512_set1_epi8(15);
        const __m512i ones = _mm512_set1_epi8(1);
        __m512i unsigned_signed_dot = _mm512_setzero_si512();
        __m512i rhs_centered_sum = _mm512_setzero_si512();
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 64U <= pair_count; pair += 64U) {
            const __m512i lhs_packed = _mm512_loadu_si512(
                reinterpret_cast<const void *>(lhs + pair));
            const __m512i rhs_packed = _mm512_loadu_si512(
                reinterpret_cast<const void *>(rhs + pair));
            const __m512i lhs_lo = _mm512_and_si512(lhs_packed, nibble_mask);
            const __m512i lhs_hi = _mm512_and_si512(
                _mm512_srli_epi16(lhs_packed, 4), nibble_mask);
            const __m512i rhs_lo = _mm512_and_si512(rhs_packed, nibble_mask);
            const __m512i rhs_hi = _mm512_and_si512(
                _mm512_srli_epi16(rhs_packed, 4), nibble_mask);
            const __m512i rhs_lo_centered = _mm512_sub_epi8(
                _mm512_add_epi8(rhs_lo, rhs_lo), fifteen);
            const __m512i rhs_hi_centered = _mm512_sub_epi8(
                _mm512_add_epi8(rhs_hi, rhs_hi), fifteen);

            unsigned_signed_dot = _mm512_dpbusd_epi32(
                unsigned_signed_dot, lhs_lo, rhs_lo_centered);
            unsigned_signed_dot = _mm512_dpbusd_epi32(
                unsigned_signed_dot, lhs_hi, rhs_hi_centered);
            rhs_centered_sum = _mm512_dpbusd_epi32(
                rhs_centered_sum, ones, rhs_lo_centered);
            rhs_centered_sum = _mm512_dpbusd_epi32(
                rhs_centered_sum, ones, rhs_hi_centered);
        }

        const int64_t dot = static_cast<int64_t>(
            _mm512_reduce_add_epi32(unsigned_signed_dot));
        const int64_t centered_rhs = static_cast<int64_t>(
            _mm512_reduce_add_epi32(rhs_centered_sum));
        return 2 * dot - 15 * centered_rhs +
               centeredNibbleDotTimesFourScalar(lhs, rhs, pair);
    }
#endif

#if defined(__AVX2__)
    int64_t centeredNibbleDotTimesFourAvx2(
        const uint8_t *lhs,
        const uint8_t *rhs) const {
        const __m128i nibble_mask = _mm_set1_epi8(0x0F);
        const __m128i fifteen = _mm_set1_epi8(15);
        __m256i sum = _mm256_setzero_si256();
        const size_t pair_count = code_dim_ >> 1U;
        size_t pair = 0;
        for (; pair + 16U <= pair_count; pair += 16U) {
            const __m128i lhs_packed = _mm_loadu_si128(
                reinterpret_cast<const __m128i *>(lhs + pair));
            const __m128i rhs_packed = _mm_loadu_si128(
                reinterpret_cast<const __m128i *>(rhs + pair));
            const __m128i lhs_lo = _mm_and_si128(lhs_packed, nibble_mask);
            const __m128i lhs_hi = _mm_and_si128(
                _mm_srli_epi16(lhs_packed, 4), nibble_mask);
            const __m128i rhs_lo = _mm_and_si128(rhs_packed, nibble_mask);
            const __m128i rhs_hi = _mm_and_si128(
                _mm_srli_epi16(rhs_packed, 4), nibble_mask);
            const __m128i lhs_lo_centered = _mm_sub_epi8(
                _mm_add_epi8(lhs_lo, lhs_lo), fifteen);
            const __m128i lhs_hi_centered = _mm_sub_epi8(
                _mm_add_epi8(lhs_hi, lhs_hi), fifteen);
            const __m128i rhs_lo_centered = _mm_sub_epi8(
                _mm_add_epi8(rhs_lo, rhs_lo), fifteen);
            const __m128i rhs_hi_centered = _mm_sub_epi8(
                _mm_add_epi8(rhs_hi, rhs_hi), fifteen);
            sum = _mm256_add_epi32(
                sum,
                _mm256_madd_epi16(
                    _mm256_cvtepi8_epi16(lhs_lo_centered),
                    _mm256_cvtepi8_epi16(rhs_lo_centered)));
            sum = _mm256_add_epi32(
                sum,
                _mm256_madd_epi16(
                    _mm256_cvtepi8_epi16(lhs_hi_centered),
                    _mm256_cvtepi8_epi16(rhs_hi_centered)));
        }

        alignas(32) int32_t lanes[8];
        _mm256_store_si256(reinterpret_cast<__m256i *>(lanes), sum);
        int64_t total = 0;
        for (size_t lane = 0; lane < 8U; ++lane) total += lanes[lane];
        return total + centeredNibbleDotTimesFourScalar(lhs, rhs, pair);
    }
#endif

    int64_t centeredNibbleDotTimesFourDispatch(
        const uint8_t *lhs,
        const uint8_t *rhs) const {
#if defined(__AVX512F__) && defined(__AVX512BW__) && defined(__AVX512VNNI__)
        return centeredNibbleDotTimesFourAvx512Vnni(lhs, rhs);
#elif defined(__AVX2__)
        return centeredNibbleDotTimesFourAvx2(lhs, rhs);
#else
        return centeredNibbleDotTimesFourScalar(lhs, rhs);
#endif
    }

    double centeredPrimaryCodeInnerProductScalar(
        const uint8_t *lhs_code,
        const uint8_t *rhs_code) const {
        double code_ip = 0.0;
        for (size_t i = 0; i < code_dim_; ++i) {
            const double lhs_y = static_cast<double>(primaryCodeValue(lhs_code, i)) -
                static_cast<double>(kUnsignedOffset);
            const double rhs_y = static_cast<double>(primaryCodeValue(rhs_code, i)) -
                static_cast<double>(kUnsignedOffset);
            code_ip += lhs_y * rhs_y;
        }
        return code_ip;
    }

    double centeredPrimaryCodeInnerProduct(
        const uint8_t *lhs_code,
        const uint8_t *rhs_code) const {
        if (code_layout_ == RaBitQCodeLayout::SequentialNibble) {
            return 0.25 * static_cast<double>(
                centeredNibbleDotTimesFourDispatch(lhs_code, rhs_code));
        }
        return centeredPrimaryCodeInnerProductScalar(lhs_code, rhs_code);
    }

    void buildCentroidDeltaContexts() {
        centroid_delta_queries_.assign(
            centroid_count_, std::vector<QueryContext>(centroid_count_));
        centroid_pair_norm_sqr_.assign(centroid_count_ * centroid_count_, 0.0f);
        const size_t pair_count = code_dim_ >> 1U;
        for (size_t a = 0; a < centroid_count_; ++a) {
            const float *ca = centroids_.data() + a * dim_;
            const float *ra = rotated_centroids_.data() + a * code_dim_;
            for (size_t b = 0; b < centroid_count_; ++b) {
                const float *cb = centroids_.data() + b * dim_;
                const float *rb = rotated_centroids_.data() + b * code_dim_;
                double pair_norm_sqr = 0.0;
                for (size_t i = 0; i < dim_; ++i) {
                    const double delta = static_cast<double>(ca[i]) - static_cast<double>(cb[i]);
                    pair_norm_sqr += delta * delta;
                }
                centroid_pair_norm_sqr_[a * centroid_count_ + b] =
                    static_cast<float>(pair_norm_sqr);

                QueryContext &query = centroid_delta_queries_[a][b];
                query.rotated_residual.assign(code_dim_, 0.0f);
                for (size_t i = 0; i < code_dim_; ++i) {
                    query.rotated_residual[i] = ra[i] - rb[i];
                }
                query.query_norm_sqr = static_cast<float>(pair_norm_sqr);
                query.query_norm = std::sqrt(std::max(0.0, pair_norm_sqr));
                query.rotated_residual_even.assign(pair_count, 0.0f);
                query.rotated_residual_odd.assign(pair_count, 0.0f);
                query.half_sum_residual = 0.0f;
                query.rotated_residual_sum = 0.0f;
                query.positive_sum_residual = 0.0f;
                query.residual_nibble_lut_ready = false;
                query.residual_2bit_lut_ready = false;
                for (size_t pair = 0; pair < pair_count; ++pair) {
                    const float even_value = query.rotated_residual[pair << 1U];
                    const float odd_value = query.rotated_residual[(pair << 1U) + 1U];
                    query.rotated_residual_even[pair] = even_value;
                    query.rotated_residual_odd[pair] = odd_value;
                    query.half_sum_residual += even_value + odd_value;
                    query.rotated_residual_sum += even_value + odd_value;
                    query.positive_sum_residual += std::max(0.0f, even_value) +
                        std::max(0.0f, odd_value);
                }
                query.half_sum_residual *= 0.5f;
            }
        }
    }

    // Estimate 2 * <query_vector, residual> for an encoded record, using the
    // same long-code inner-product machinery as the asymmetric distance path
    // (queryDistanceLongWithIps returns norm_sqr + query_norm_sqr - this value).
    float estimateTwiceResidualIp(
        const QueryContext &query,
        const EncodedHeader &header,
        const uint8_t *code) const {
        if (header.long_scale <= 0.0f || !std::isfinite(header.long_scale)) {
            return 0.0f;
        }
        const LongCodeIps ips = longCodeIpsDispatch(query, code);
        const float signed_long_ip =
            static_cast<float>(kMsbWeight) * ips.short_ip + ips.remaining_ip -
            static_cast<float>(kRemainingMax) * query.half_sum_residual;
        return header.long_scale * signed_long_ip;
    }

    float estimateTwiceResidualIp(const QueryContext &query, const void *encoded) const {
        return estimateTwiceResidualIp(
            query, loadHeader(encoded), codeBytes(encoded));
    }

    float distanceBetweenEncoded(const char *lhs, const char *rhs) const {
        const EncodedHeader lhs_header = loadHeader(lhs);
        const EncodedHeader rhs_header = loadHeader(rhs);
        const double lhs_norm = std::sqrt(std::max(0.0, static_cast<double>(lhs_header.norm_sqr)));
        const double rhs_norm = std::sqrt(std::max(0.0, static_cast<double>(rhs_header.norm_sqr)));
        const uint8_t lhs_cid = *centroidId(lhs);
        const uint8_t rhs_cid = *centroidId(rhs);
        const bool cross_centroid =
            centroid_count_ > 1 && lhs_cid < centroid_count_ && rhs_cid < centroid_count_ &&
            lhs_cid != rhs_cid;
        const float centroid_extra = cross_centroid
            ? centroid_pair_norm_sqr_[lhs_cid * centroid_count_ + rhs_cid]
            : 0.0f;
        if (!(lhs_norm > 0.0) || !(rhs_norm > 0.0) ||
            !(lhs_header.long_scale > 0.0f) || !(rhs_header.long_scale > 0.0f) ||
            !std::isfinite(lhs_header.long_scale) || !std::isfinite(rhs_header.long_scale)) {
            return static_cast<float>(
                static_cast<double>(lhs_header.norm_sqr) +
                static_cast<double>(rhs_header.norm_sqr) + static_cast<double>(centroid_extra));
        }
        const uint8_t *lhs_code = codeBytes(lhs);
        const uint8_t *rhs_code = codeBytes(rhs);

        const double code_ip = centeredPrimaryCodeInnerProduct(lhs_code, rhs_code);

        // Encoding stores long_scale = 2 * ||o|| * ip_norm, where
        // ip_norm = 1 / <o_bar, o_unit>.  Consequently the expression below
        // is exactly the symmetric RaBitQ estimator
        //   <o_bar,q_bar> / (<o_bar,o_unit><q_bar,q_unit>).
        const double lhs_ip_norm =
            static_cast<double>(lhs_header.long_scale) / (2.0 * lhs_norm);
        const double rhs_ip_norm =
            static_cast<double>(rhs_header.long_scale) / (2.0 * rhs_norm);
        double estimated_unit_ip = code_ip * lhs_ip_norm * rhs_ip_norm;
        if (!std::isfinite(estimated_unit_ip)) estimated_unit_ip = 0.0;
        estimated_unit_ip = std::max(-1.0, std::min(1.0, estimated_unit_ip));
        const double residual_ip = lhs_norm * rhs_norm * estimated_unit_ip;
        float distance = static_cast<float>(
            static_cast<double>(lhs_header.norm_sqr) + static_cast<double>(rhs_header.norm_sqr) -
            2.0 * residual_ip);
        if (cross_centroid) {
            // ||x - y||^2 = ||r_a - r_b||^2 + ||c_a - c_b||^2
            //              + 2 <c_a - c_b, r_a - r_b>.
            const QueryContext &delta = centroid_delta_queries_[lhs_cid][rhs_cid];
            distance += centroid_extra
                + estimateTwiceResidualIp(delta, lhs_header, lhs_code)
                - estimateTwiceResidualIp(delta, rhs_header, rhs_code);
        }
        return distance;
    }

    void initializeSymmetricBuildPrepared(
        SymmetricBuildPreparedQuery &prepared,
        const void *encoded_query) const {
        if (encoded_query == nullptr)
            throw std::invalid_argument("RaBitQ symmetric build query received null data");
        prepared.header = loadHeader(encoded_query);
        prepared.code = codeBytes(encoded_query);
        prepared.centroid_id = *centroidId(encoded_query);
        prepared.owner = this;
        prepared.query_norm = std::sqrt(std::max(
            0.0, static_cast<double>(prepared.header.norm_sqr)));
        prepared.valid =
            prepared.query_norm > 0.0 &&
            prepared.header.long_scale > 0.0f &&
            std::isfinite(prepared.header.long_scale);
        prepared.query_ip_norm = prepared.valid
            ? static_cast<double>(prepared.header.long_scale) /
                  (2.0 * prepared.query_norm)
            : 0.0;
    }

    float symmetricBuildDistanceFromPrepared(
        const SymmetricBuildPreparedQuery &query,
        const void *encoded_database) const {
        if (encoded_database == nullptr)
            throw std::invalid_argument("RaBitQ symmetric build distance received null database");
        const EncodedHeader rhs_header = loadHeader(encoded_database);
        const double rhs_norm = std::sqrt(std::max(
            0.0, static_cast<double>(rhs_header.norm_sqr)));
        const uint8_t db_cid = *centroidId(encoded_database);
        const bool cross_centroid =
            query.centroid_id < centroid_count_ && db_cid < centroid_count_ &&
            query.centroid_id != db_cid;
        const float centroid_extra = cross_centroid
            ? centroid_pair_norm_sqr_[query.centroid_id * centroid_count_ + db_cid]
            : 0.0f;
        if (!query.valid || !(rhs_norm > 0.0) ||
            !(rhs_header.long_scale > 0.0f) ||
            !std::isfinite(rhs_header.long_scale)) {
            return static_cast<float>(
                static_cast<double>(query.header.norm_sqr) +
                static_cast<double>(rhs_header.norm_sqr) + static_cast<double>(centroid_extra));
        }
        const double rhs_ip_norm =
            static_cast<double>(rhs_header.long_scale) / (2.0 * rhs_norm);
        const double code_ip = centeredPrimaryCodeInnerProduct(
            query.code, codeBytes(encoded_database));
        double estimated_unit_ip = code_ip * query.query_ip_norm * rhs_ip_norm;
        if (!std::isfinite(estimated_unit_ip)) estimated_unit_ip = 0.0;
        estimated_unit_ip = std::max(-1.0, std::min(1.0, estimated_unit_ip));
        const double residual_ip = query.query_norm * rhs_norm * estimated_unit_ip;
        float distance = static_cast<float>(
            static_cast<double>(query.header.norm_sqr) +
            static_cast<double>(rhs_header.norm_sqr) - 2.0 * residual_ip);
        if (cross_centroid) {
            const QueryContext &delta =
                centroid_delta_queries_[query.centroid_id][db_cid];
            distance += centroid_extra
                + estimateTwiceResidualIp(delta, query.header, query.code)
                - estimateTwiceResidualIp(delta, rhs_header, codeBytes(encoded_database));
        }
        return distance;
    }

    float distanceBetweenEncodedScalarReference(
        const char *lhs,
        const char *rhs) const {
        const EncodedHeader lhs_header = loadHeader(lhs);
        const EncodedHeader rhs_header = loadHeader(rhs);
        const double lhs_norm = std::sqrt(std::max(
            0.0, static_cast<double>(lhs_header.norm_sqr)));
        const double rhs_norm = std::sqrt(std::max(
            0.0, static_cast<double>(rhs_header.norm_sqr)));
        if (!(lhs_norm > 0.0) || !(rhs_norm > 0.0) ||
            !(lhs_header.long_scale > 0.0f) || !(rhs_header.long_scale > 0.0f) ||
            !std::isfinite(lhs_header.long_scale) || !std::isfinite(rhs_header.long_scale)) {
            return static_cast<float>(
                static_cast<double>(lhs_header.norm_sqr) +
                static_cast<double>(rhs_header.norm_sqr));
        }
        const double code_ip = centeredPrimaryCodeInnerProductScalar(
            codeBytes(lhs), codeBytes(rhs));
        const double lhs_ip_norm =
            static_cast<double>(lhs_header.long_scale) / (2.0 * lhs_norm);
        const double rhs_ip_norm =
            static_cast<double>(rhs_header.long_scale) / (2.0 * rhs_norm);
        double estimated_unit_ip = code_ip * lhs_ip_norm * rhs_ip_norm;
        if (!std::isfinite(estimated_unit_ip)) estimated_unit_ip = 0.0;
        estimated_unit_ip = std::max(-1.0, std::min(1.0, estimated_unit_ip));
        const double residual_ip = lhs_norm * rhs_norm * estimated_unit_ip;
        return static_cast<float>(
            static_cast<double>(lhs_header.norm_sqr) +
            static_cast<double>(rhs_header.norm_sqr) - 2.0 * residual_ip);
    }

 public:
#ifdef HNSWLIB_RABITQ_TESTING
    float symmetric_distance_scalar_reference(
        const void *lhs,
        const void *rhs) const {
        return distanceBetweenEncodedScalarReference(
            static_cast<const char *>(lhs), static_cast<const char *>(rhs));
    }

    float b1_signed_dot_for_test(
        const void *prepared_query,
        const uint8_t *msb_code) const {
        const PreparedQuery &prepared = *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        uint64_t mismatches = 0;
        const size_t bytes = (code_dim_ + 7U) / 8U;
        for (size_t j = 0; j < bytes; ++j) {
            mismatches += static_cast<uint64_t>(__builtin_popcountll(
                static_cast<uint64_t>(query.b1_code[j]) ^
                static_cast<uint64_t>(msb_code[j])));
        }
        return static_cast<float>(code_dim_) - 2.0f * static_cast<float>(mismatches);
    }

    float b1_short_scale_for_test(const void *prepared_query) const {
        return static_cast<const PreparedQuery *>(prepared_query)
            ->centroid_queries[0].b1_short_scale;
    }

    float int_scale_for_test(const void *prepared_query, bool int8_mode) const {
        const QueryContext &query =
            static_cast<const PreparedQuery *>(prepared_query)->centroid_queries[0];
        return int8_mode ? query.int8_scale : query.int4_scale;
    }

    int64_t int_code_sum_for_test(const void *prepared_query, bool int8_mode) const {
        const QueryContext &query =
            static_cast<const PreparedQuery *>(prepared_query)->centroid_queries[0];
        return int8_mode ? query.int8_code_sum : query.int4_code_sum;
    }
#endif

    explicit RaBitQSpace(
        size_t dim,
        size_t centroid_count = 1,
        uint32_t random_seed = 100,
        bool external_residual_storage = false,
        size_t residual_bits = 0)
        : RaBitQSpace(
              dim,
              centroid_count,
              random_seed,
              external_residual_storage,
              makeResidualConfig(residual_bits)) {
    }

    explicit RaBitQSpace(
        size_t dim,
        size_t centroid_count,
        uint32_t random_seed,
        bool external_residual_storage,
        ResidualQuantizationConfig residual_config)
        : dim_(dim),
          code_dim_(roundUp64(dim)),
          compact_code_bytes_((code_dim_ + 1U) / 2U),
          short_factor_bytes_(sizeof(ShortCodeFactors)),
          residual_factor_bytes_(sizeof(ResidualCodeFactors)),
          residual_block_size_(validateResidualConfig(residual_config).block_size),
          residual_block_count_((code_dim_ + residual_block_size_ - 1U) / residual_block_size_),
          residual_scale_bytes_(
              residual_block_count_ *
              (validateResidualConfig(residual_config).scale_fp16 ? sizeof(uint16_t) : sizeof(float))),
          residual_bits_(static_cast<size_t>(validateResidualConfig(residual_config).bits)),
          residual_code_bytes_(residual_packed_code_size_bytes(code_dim_, residual_bits_)),
          centroid_count_(centroid_count),
          nested4x4_layout_(envModeIsNested4x4()),
          nested4x4_hot_aux_bytes_(nested4x4_layout_ ? residual_scale_bytes_ : 0),
          full_data_size_(nested4x4_layout_
              ? sizeof(EncodedHeader) + short_factor_bytes_ + residual_scale_bytes_ +
                  compact_code_bytes_ + centroid_id_bytes_ + compact_code_bytes_
              : sizeof(EncodedHeader) + short_factor_bytes_ + residual_factor_bytes_ +
                  residual_scale_bytes_ + compact_code_bytes_ + centroid_id_bytes_ + residual_code_bytes_),
          residual_disk_record_bytes_(nested4x4_layout_
              ? compact_code_bytes_
              : residual_factor_bytes_ + residual_scale_bytes_ + residual_code_bytes_),
          data_size_(external_residual_storage
              ? sizeof(EncodedHeader) + short_factor_bytes_ + nested4x4_hot_aux_bytes_ +
                  compact_code_bytes_ + centroid_id_bytes_
              : full_data_size_),
          inv_sqrt_code_dim_(1.0f / std::sqrt(static_cast<float>(code_dim_))),
          external_residual_storage_(external_residual_storage),
          fstdistfunc_(exrabitqDistance),
          random_seed_(random_seed),
          fht_signs_(code_dim_, inv_sqrt_code_dim_),
          residual_config_(validateResidualConfig(residual_config)),
          centroids_(centroid_count_ * dim_, 0.0f),
          rotated_centroids_(centroid_count_ * code_dim_, 0.0f),
          centroid_norm_sqr_(centroid_count_, 0.0f) {
        if (centroid_count_ == 0 || centroid_count_ > 256) {
            throw std::invalid_argument("RaBitQSpace centroid_count must be in [1, 256]");
        }
        if (centroid_count_ > 1 && nested4x4_layout_) {
            throw std::invalid_argument("nested4x4 layout does not support multi-centroid RaBitQ");
        }
        if (random_seed != 0) {
            setRandomRotation(random_seed);
        } else {
            setIdentityRotation();
        }
    }

    ~RaBitQSpace() override {
        closeExternalResidualStorage();
    }

    void setIdentityRotation() {
        random_seed_ = 0;
        std::fill(fht_signs_.begin(), fht_signs_.end(), inv_sqrt_code_dim_);
    }

    void setRandomRotation(uint32_t seed) {
        random_seed_ = seed;
        std::mt19937 rng(seed);
        std::uniform_int_distribution<int> bernoulli(0, 1);
        for (size_t i = 0; i < code_dim_; ++i) {
            fht_signs_[i] = (bernoulli(rng) ? 1.0f : -1.0f) * inv_sqrt_code_dim_;
        }
        for (size_t centroid_id = 0; centroid_id < centroid_count_; ++centroid_id) {
            rotate(centroids_.data() + centroid_id * dim_,
                   rotated_centroids_.data() + centroid_id * code_dim_);
        }
        buildCentroidDeltaContexts();
    }

    void setGlobalCenter(const float *raw_center) {
        setCentroids(raw_center, 1);
    }

    void setCentroids(const float *raw_centroids, size_t centroid_count) {
        if (centroid_count == 0) {
            throw std::invalid_argument("RaBitQSpace requires at least one center");
        }
        if (centroid_count != centroid_count_) {
            throw std::invalid_argument("RaBitQSpace centroid_count does not match constructor");
        }
        std::copy(raw_centroids, raw_centroids + centroid_count_ * dim_, centroids_.begin());
        for (size_t centroid_id = 0; centroid_id < centroid_count_; ++centroid_id) {
            const float *centroid = centroids_.data() + centroid_id * dim_;
            double norm = 0.0;
            for (size_t i = 0; i < dim_; ++i) {
                norm += static_cast<double>(centroid[i]) * static_cast<double>(centroid[i]);
            }
            centroid_norm_sqr_[centroid_id] = static_cast<float>(norm);
            rotate(centroid, rotated_centroids_.data() + centroid_id * code_dim_);
        }
        buildCentroidDeltaContexts();
    }

    void rotate(const float *raw_vector, float *rotated_out) const {
        thread_local std::vector<float> rotated;
        rotate(raw_vector, rotated);
        std::copy(rotated.begin(), rotated.end(), rotated_out);
    }

    uint8_t assignCentroid(const float *raw_vector) const {
        size_t best = 0;
        float best_distance = std::numeric_limits<float>::infinity();
        for (size_t centroid_id = 0; centroid_id < centroid_count_; ++centroid_id) {
            const float *centroid = centroids_.data() + centroid_id * dim_;
            float distance = 0.0f;
            for (size_t i = 0; i < dim_; ++i) {
                const float diff = raw_vector[i] - centroid[i];
                distance += diff * diff;
            }
            if (distance < best_distance) {
                best_distance = distance;
                best = centroid_id;
            }
        }
        return static_cast<uint8_t>(best);
    }

    size_t get_dim() const {
        return dim_;
    }

    size_t get_code_dim() const {
        return code_dim_;
    }

    size_t get_compact_code_bytes() const {
        return compact_code_bytes_;
    }

    size_t get_residual_code_bytes() const {
        return residual_code_bytes_;
    }

    size_t get_residual_bits() const {
        return residual_bits_;
    }

    size_t get_residual_block_size() const {
        return residual_block_size_;
    }

    bool get_residual_mse_optimal_scale() const {
        return residual_config_.mse_optimal_scale;
    }

    const ResidualQuantizationConfig &get_residual_config() const {
        return residual_config_;
    }

    double get_primary_code_empirical_error_reference() const {
        return primary_code_empirical_error_reference(code_dim_);
    }

    size_t get_full_data_size() const {
        return full_data_size_;
    }

#ifdef HNSWLIB_RABITQ_TESTING
    float symmetric_distance_for_test(const void *lhs, const void *rhs) const {
        return distanceBetweenEncoded(
            static_cast<const char *>(lhs), static_cast<const char *>(rhs));
    }

    size_t centroid_count_for_test() const {
        return centroid_count_;
    }
#endif

    size_t get_residual_disk_record_bytes() const {
        return externalResidualRecordBytes();
    }

    bool external_residual_storage_enabled() const {
        return external_residual_storage_;
    }

    void copyExternalResidualRecord(size_t internal_id, void *destination) const {
        if (!external_residual_storage_ || residual_mmap_ == nullptr)
            throw std::runtime_error("RaBitQ external residual storage is not open");
        if (internal_id >= residual_record_count_)
            throw std::out_of_range("RaBitQ residual internal ID is outside storage");
        std::memcpy(destination,
                    residual_mmap_ + internal_id * residual_disk_record_bytes_,
                    residual_disk_record_bytes_);
    }

    size_t get_centroid_count() const {
        return centroid_count_;
    }

    static void convertSequentialToTurbo(
        const uint8_t *sequential,
        uint8_t *turbo,
        size_t code_dimension) {
        if (code_dimension == 0 || code_dimension % 128U != 0U) {
            throw std::invalid_argument("Turbo128 code dimension must be a non-zero multiple of 128");
        }
        std::memset(turbo, 0, code_dimension / 2U);
        for (size_t i = 0; i < code_dimension; ++i) {
            const uint8_t value = codeValue(sequential, i);
            const size_t block = i / 128U;
            const size_t within = i % 128U;
            const size_t lane = within % 16U;
            const size_t slot = within / 16U;
            uint8_t &byte = turbo[block * 64U + lane * 4U + (slot >> 1U)];
            if (slot & 1U) byte = static_cast<uint8_t>(byte | (value << 4U));
            else byte = static_cast<uint8_t>(byte | value);
        }
    }

    static void unpackTurbo128(
        const uint8_t *turbo,
        uint8_t *values,
        size_t code_dimension) {
        if (code_dimension == 0 || code_dimension % 128U != 0U) {
            throw std::invalid_argument("Turbo128 code dimension must be a non-zero multiple of 128");
        }
        for (size_t i = 0; i < code_dimension; ++i) {
            values[i] = turbo128CodeValue(turbo, i);
        }
    }

    void set_code_layout(RaBitQCodeLayout layout) {
        if (nested4x4_layout_ && layout != RaBitQCodeLayout::SequentialNibble) {
            throw std::invalid_argument("nested4x4 does not support Turbo128");
        }
        code_layout_ = layout;
    }

    RaBitQCodeLayout get_code_layout() const {
        return code_layout_;
    }

    void saveCentroids(
        std::ostream &output,
        uint32_t training_seed,
        uint64_t training_sample_count) const {
        static const char magic[8] = {'R','B','C','E','N','T','0','1'};
        const uint32_t version = 1;
        const uint64_t dimension = static_cast<uint64_t>(dim_);
        const uint64_t count = static_cast<uint64_t>(centroid_count_);
        output.write(magic, sizeof(magic));
        output.write(reinterpret_cast<const char *>(&version), sizeof(version));
        output.write(reinterpret_cast<const char *>(&dimension), sizeof(dimension));
        output.write(reinterpret_cast<const char *>(&count), sizeof(count));
        output.write(reinterpret_cast<const char *>(&training_seed), sizeof(training_seed));
        output.write(reinterpret_cast<const char *>(&training_sample_count), sizeof(training_sample_count));
        output.write(
            reinterpret_cast<const char *>(centroids_.data()),
            static_cast<std::streamsize>(centroids_.size() * sizeof(float)));
        if (!output.good()) {
            throw std::runtime_error("RaBitQSpace failed to save centroid file");
        }
    }

    void loadCentroids(
        std::istream &input,
        uint32_t *training_seed = nullptr,
        uint64_t *training_sample_count = nullptr) {
        char magic[8] = {};
        uint32_t version = 0;
        uint64_t dimension = 0;
        uint64_t count = 0;
        uint32_t seed = 0;
        uint64_t sample_count = 0;
        input.read(magic, sizeof(magic));
        input.read(reinterpret_cast<char *>(&version), sizeof(version));
        input.read(reinterpret_cast<char *>(&dimension), sizeof(dimension));
        input.read(reinterpret_cast<char *>(&count), sizeof(count));
        input.read(reinterpret_cast<char *>(&seed), sizeof(seed));
        input.read(reinterpret_cast<char *>(&sample_count), sizeof(sample_count));
        if (!input.good() || std::memcmp(magic, "RBCENT01", 8) != 0 || version != 1) {
            throw std::runtime_error("RaBitQSpace invalid centroid file header");
        }
        if (dimension != dim_ || count != centroid_count_) {
            throw std::runtime_error("RaBitQSpace centroid file dimension or count mismatch");
        }
        std::vector<float> loaded(centroid_count_ * dim_, 0.0f);
        input.read(
            reinterpret_cast<char *>(loaded.data()),
            static_cast<std::streamsize>(loaded.size() * sizeof(float)));
        if (!input.good()) {
            throw std::runtime_error("RaBitQSpace truncated centroid file");
        }
        for (float value : loaded) {
            if (!std::isfinite(value)) {
                throw std::runtime_error("RaBitQSpace centroid file contains non-finite values");
            }
        }
        setCentroids(loaded.data(), centroid_count_);
        if (training_seed) *training_seed = seed;
        if (training_sample_count) *training_sample_count = sample_count;
    }

    void saveState(std::ostream &output) const {
        const std::string magic = nested4x4_layout_
            ? "EXRBTQ35"
            : (code_layout_ == RaBitQCodeLayout::Turbo128 ? "EXRBTQ40" : "EXRBTQ31");
        output.write(magic.data(), magic.size());

        const uint64_t dim = static_cast<uint64_t>(dim_);
        const uint64_t code_dim = static_cast<uint64_t>(code_dim_);
        const uint32_t total_bits = static_cast<uint32_t>(kTotalBits);
        const uint32_t short_bits = static_cast<uint32_t>(kShortBits);
        const uint32_t remaining_bits = static_cast<uint32_t>(kRemainingBits);
        const uint64_t encoded_header_size = static_cast<uint64_t>(sizeof(EncodedHeader));
        const uint64_t short_factor_size = static_cast<uint64_t>(sizeof(ShortCodeFactors));
        const uint64_t residual_factor_size = static_cast<uint64_t>(sizeof(ResidualCodeFactors));
        const uint64_t residual_scale_bytes = static_cast<uint64_t>(residual_scale_bytes_);
        const uint32_t residual_bits = static_cast<uint32_t>(residual_bits_);
        const uint64_t full_code_bytes = static_cast<uint64_t>(compact_code_bytes_);
        const uint64_t residual_code_bytes = static_cast<uint64_t>(residual_code_bytes_);
        const uint64_t data_size = static_cast<uint64_t>(data_size_);
        const uint64_t full_data_size = static_cast<uint64_t>(full_data_size_);
        const uint64_t residual_disk_record_bytes = static_cast<uint64_t>(residual_disk_record_bytes_);
        const uint8_t external_residual_storage = external_residual_storage_ ? 1U : 0U;
        const uint64_t residual_block_size = static_cast<uint64_t>(residual_block_size_);
        const uint64_t centroid_count = static_cast<uint64_t>(centroid_count_);
        const uint32_t flags =
            (residual_config_.enabled ? 1U : 0U) |
            (residual_config_.enable_block_scaling ? 2U : 0U) |
            (residual_config_.mse_optimal_scale ? 4U : 0U) |
            (residual_config_.scale_fp16 ? 8U : 0U);
        output.write(reinterpret_cast<const char *>(&dim), sizeof(dim));
        output.write(reinterpret_cast<const char *>(&code_dim), sizeof(code_dim));
        output.write(reinterpret_cast<const char *>(&total_bits), sizeof(total_bits));
        output.write(reinterpret_cast<const char *>(&short_bits), sizeof(short_bits));
        output.write(reinterpret_cast<const char *>(&remaining_bits), sizeof(remaining_bits));
        output.write(reinterpret_cast<const char *>(&encoded_header_size), sizeof(encoded_header_size));
        output.write(reinterpret_cast<const char *>(&short_factor_size), sizeof(short_factor_size));
        output.write(reinterpret_cast<const char *>(&residual_factor_size), sizeof(residual_factor_size));
        output.write(reinterpret_cast<const char *>(&residual_scale_bytes), sizeof(residual_scale_bytes));
        output.write(reinterpret_cast<const char *>(&residual_bits), sizeof(residual_bits));
        output.write(reinterpret_cast<const char *>(&full_code_bytes), sizeof(full_code_bytes));
        output.write(reinterpret_cast<const char *>(&residual_code_bytes), sizeof(residual_code_bytes));
        output.write(reinterpret_cast<const char *>(&data_size), sizeof(data_size));
        output.write(reinterpret_cast<const char *>(&full_data_size), sizeof(full_data_size));
        output.write(reinterpret_cast<const char *>(&residual_disk_record_bytes), sizeof(residual_disk_record_bytes));
        output.write(reinterpret_cast<const char *>(&external_residual_storage), sizeof(external_residual_storage));
        output.write(reinterpret_cast<const char *>(&residual_block_size), sizeof(residual_block_size));
        output.write(reinterpret_cast<const char *>(&flags), sizeof(flags));
        output.write(reinterpret_cast<const char *>(&centroid_count), sizeof(centroid_count));
        if (code_layout_ == RaBitQCodeLayout::Turbo128) {
            const uint8_t layout_id = static_cast<uint8_t>(code_layout_);
            output.write(reinterpret_cast<const char *>(&layout_id), sizeof(layout_id));
        }
        output.write(reinterpret_cast<const char *>(&random_seed_), sizeof(random_seed_));
        output.write(reinterpret_cast<const char *>(centroids_.data()), centroids_.size() * sizeof(float));
        output.write(reinterpret_cast<const char *>(fht_signs_.data()), fht_signs_.size() * sizeof(float));

        if (!output.good()) {
            throw std::runtime_error("RaBitQSpace failed to save ExRaBitQ state");
        }
    }

    void loadState(std::istream &input) {
        char magic[8];
        input.read(magic, sizeof(magic));
        const std::string magic_value(magic, sizeof(magic));
        const bool turbo_format = magic_value == "EXRBTQ40";
        const bool multi_centroid_format = magic_value == "EXRBTQ31" || turbo_format;
        const bool new_format = magic_value == "EXRBTQ30";
        const bool nested4x4_format = magic_value == "EXRBTQ35";
        const bool old_format = magic_value == "EXRBTQ22";
        if (!input.good() || (!multi_centroid_format && !new_format && !nested4x4_format && !old_format)) {
            throw std::runtime_error(
                "Old or incompatible RaBitQ index format. Please rebuild the index.");
        }

        uint64_t stored_dim = 0;
        uint64_t stored_code_dim = 0;
        uint32_t stored_total_bits = 0;
        uint32_t stored_short_bits = kShortBits;
        uint32_t stored_remaining_bits = 0;
        uint64_t stored_header_size = 0;
        uint64_t stored_factor_size = 0;
        uint64_t stored_residual_factor_size = 0;
        uint64_t stored_residual_scale_bytes = 0;
        uint32_t stored_residual_bits = 0;
        uint64_t stored_full_code_bytes = 0;
        uint64_t stored_residual_code_bytes = 0;
        uint64_t stored_data_size = 0;
        uint64_t stored_full_data_size = 0;
        uint64_t stored_residual_disk_record_bytes = 0;
        uint8_t stored_external_residual_storage = 0;
        uint64_t stored_residual_block_size = kResidualBlockSize;
        uint64_t stored_centroid_count = 1;
        uint32_t stored_flags = 3U;
        uint8_t stored_layout_id = 0;
        input.read(reinterpret_cast<char *>(&stored_dim), sizeof(stored_dim));
        input.read(reinterpret_cast<char *>(&stored_code_dim), sizeof(stored_code_dim));
        input.read(reinterpret_cast<char *>(&stored_total_bits), sizeof(stored_total_bits));
        if (new_format || nested4x4_format || multi_centroid_format) {
            input.read(reinterpret_cast<char *>(&stored_short_bits), sizeof(stored_short_bits));
        }
        input.read(reinterpret_cast<char *>(&stored_remaining_bits), sizeof(stored_remaining_bits));
        input.read(reinterpret_cast<char *>(&stored_header_size), sizeof(stored_header_size));
        input.read(reinterpret_cast<char *>(&stored_factor_size), sizeof(stored_factor_size));
        input.read(reinterpret_cast<char *>(&stored_residual_factor_size), sizeof(stored_residual_factor_size));
        input.read(reinterpret_cast<char *>(&stored_residual_scale_bytes), sizeof(stored_residual_scale_bytes));
        input.read(reinterpret_cast<char *>(&stored_residual_bits), sizeof(stored_residual_bits));
        input.read(reinterpret_cast<char *>(&stored_full_code_bytes), sizeof(stored_full_code_bytes));
        input.read(reinterpret_cast<char *>(&stored_residual_code_bytes), sizeof(stored_residual_code_bytes));
        input.read(reinterpret_cast<char *>(&stored_data_size), sizeof(stored_data_size));
        input.read(reinterpret_cast<char *>(&stored_full_data_size), sizeof(stored_full_data_size));
        input.read(reinterpret_cast<char *>(&stored_residual_disk_record_bytes), sizeof(stored_residual_disk_record_bytes));
        input.read(reinterpret_cast<char *>(&stored_external_residual_storage), sizeof(stored_external_residual_storage));
        if (new_format || nested4x4_format) {
            input.read(reinterpret_cast<char *>(&stored_residual_block_size), sizeof(stored_residual_block_size));
            input.read(reinterpret_cast<char *>(&stored_flags), sizeof(stored_flags));
        }
        if (multi_centroid_format) {
            input.read(reinterpret_cast<char *>(&stored_residual_block_size), sizeof(stored_residual_block_size));
            input.read(reinterpret_cast<char *>(&stored_flags), sizeof(stored_flags));
            input.read(reinterpret_cast<char *>(&stored_centroid_count), sizeof(stored_centroid_count));
            if (turbo_format) {
                input.read(reinterpret_cast<char *>(&stored_layout_id), sizeof(stored_layout_id));
            }
        }

        if (!input.good()) {
            throw std::runtime_error("RaBitQSpace failed to read ExRaBitQ state header");
        }
        if (stored_layout_id > static_cast<uint8_t>(RaBitQCodeLayout::Turbo128)) {
            throw std::runtime_error("RaBitQ index has invalid code layout");
        }
        if (!is_supported_residual_bits(stored_residual_bits)) {
            throw std::runtime_error("RaBitQ index has unsupported residual bits");
        }
        if (!multi_centroid_format && centroid_count_ != 1) {
            throw std::runtime_error("Old EXRBTQ index can only be loaded as K=1");
        }
        const bool stored_nested4x4_layout = nested4x4_format ||
            (stored_external_residual_storage != 0 &&
             stored_residual_bits == 4 &&
             stored_data_size == sizeof(EncodedHeader) + sizeof(ShortCodeFactors) +
                 residual_scale_bytes_ + compact_code_bytes_ &&
             stored_full_data_size == sizeof(EncodedHeader) + sizeof(ShortCodeFactors) +
                 residual_scale_bytes_ + compact_code_bytes_ + compact_code_bytes_);
        const size_t expected_external_record_bytes = stored_nested4x4_layout
            ? compact_code_bytes_
            : residual_disk_record_bytes_;
        const bool stored_record_size_ok = stored_nested4x4_layout
            ? (stored_residual_disk_record_bytes == residual_factor_bytes_ + residual_scale_bytes_ +
                   residual_code_bytes_ ||
               stored_residual_disk_record_bytes == compact_code_bytes_)
            : (stored_residual_disk_record_bytes == residual_disk_record_bytes_);

        if (stored_dim != dim_ ||
            stored_code_dim != code_dim_ ||
            stored_total_bits != kTotalBits ||
            stored_short_bits != kShortBits ||
            stored_remaining_bits != kRemainingBits ||
            stored_header_size != sizeof(EncodedHeader) ||
            stored_factor_size != sizeof(ShortCodeFactors) ||
            stored_residual_factor_size != sizeof(ResidualCodeFactors) ||
            stored_residual_scale_bytes != residual_scale_bytes_ ||
            stored_residual_bits != residual_bits_ ||
            stored_full_code_bytes != compact_code_bytes_ ||
            stored_residual_code_bytes != residual_code_bytes_ ||
            !stored_record_size_ok ||
            stored_residual_block_size != residual_block_size_ ||
            stored_centroid_count != centroid_count_ ||
            ((new_format || nested4x4_format || multi_centroid_format) && ((stored_flags & 1U) == 0U)) ||
            ((new_format || nested4x4_format || multi_centroid_format) &&
             (((stored_flags & 4U) != 0U) != residual_config_.mse_optimal_scale)) ||
            ((new_format || nested4x4_format || multi_centroid_format) &&
             (((stored_flags & 8U) != 0U) != residual_config_.scale_fp16)) ||
            (stored_external_residual_storage != 0) != external_residual_storage_) {
            throw std::runtime_error("Old or incompatible RaBitQ index format. Please rebuild the index.");
        }

        nested4x4_layout_ = stored_nested4x4_layout;
        code_layout_ = static_cast<RaBitQCodeLayout>(stored_layout_id);
        legacy_payload_without_centroid_ = !multi_centroid_format;
        nested4x4_hot_aux_bytes_ = nested4x4_layout_ ? residual_scale_bytes_ : 0;
        data_size_ = static_cast<size_t>(stored_data_size);
        full_data_size_ = static_cast<size_t>(stored_full_data_size);
        residual_disk_record_bytes_ = expected_external_record_bytes;

        centroids_.assign(centroid_count_ * dim_, 0.0f);
        rotated_centroids_.assign(centroid_count_ * code_dim_, 0.0f);
        centroid_norm_sqr_.assign(centroid_count_, 0.0f);
        fht_signs_.assign(code_dim_, 0.0f);
        input.read(reinterpret_cast<char *>(&random_seed_), sizeof(random_seed_));
        input.read(reinterpret_cast<char *>(centroids_.data()), centroids_.size() * sizeof(float));
        input.read(reinterpret_cast<char *>(fht_signs_.data()), fht_signs_.size() * sizeof(float));

        if (!input.good()) {
            throw std::runtime_error("RaBitQSpace failed to read ExRaBitQ state payload");
        }
        for (size_t centroid_id = 0; centroid_id < centroid_count_; ++centroid_id) {
            const float *centroid = centroids_.data() + centroid_id * dim_;
            double norm = 0.0;
            for (size_t i = 0; i < dim_; ++i) {
                norm += static_cast<double>(centroid[i]) * static_cast<double>(centroid[i]);
            }
            centroid_norm_sqr_[centroid_id] = static_cast<float>(norm);
            rotate(centroid, rotated_centroids_.data() + centroid_id * code_dim_);
        }
    }

    void closeExternalResidualStorage() const {
        if (residual_mmap_ != nullptr) {
            ::munmap(const_cast<char *>(residual_mmap_), residual_mmap_bytes_);
            residual_mmap_ = nullptr;
            residual_mmap_bytes_ = 0;
        }
        if (residual_fd_ >= 0) {
            ::close(residual_fd_);
            residual_fd_ = -1;
        }
        residual_record_count_ = 0;
    }

    void openExternalResidualStorage(const std::string &path, size_t record_count) const {
        if (!external_residual_storage_) {
            return;
        }
        closeExternalResidualStorage();
        residual_fd_ = ::open(path.c_str(), O_RDONLY);
        if (residual_fd_ < 0) {
            throw std::runtime_error("RaBitQ failed to open external residual file: " + path);
        }
        struct stat st;
        if (::fstat(residual_fd_, &st) != 0) {
            closeExternalResidualStorage();
            throw std::runtime_error("RaBitQ failed to stat external residual file: " + path);
        }
        const size_t expected_bytes = record_count * externalResidualRecordBytes();
        if (static_cast<size_t>(st.st_size) != expected_bytes) {
            closeExternalResidualStorage();
            throw std::runtime_error("RaBitQ external residual file size mismatch: " + path);
        }
        if (expected_bytes == 0) {
            return;
        }
        void *mapped = ::mmap(nullptr, expected_bytes, PROT_READ, MAP_SHARED, residual_fd_, 0);
        if (mapped == MAP_FAILED) {
            closeExternalResidualStorage();
            throw std::runtime_error("RaBitQ failed to mmap external residual file: " + path);
        }
        residual_mmap_ = static_cast<const char *>(mapped);
        residual_mmap_bytes_ = expected_bytes;
        residual_record_count_ = record_count;
#ifdef MADV_RANDOM
        ::madvise(const_cast<char *>(residual_mmap_), residual_mmap_bytes_, MADV_RANDOM);
#endif
    }

    size_t get_data_size() override {
        return data_size_;
    }

    DISTFUNC<float> get_dist_func() override {
        return fstdistfunc_;
    }

    void *get_dist_func_param() override {
        return this;
    }

    const QueryContext &queryForEncoded(const void *prepared_query, const void *data_point) const {
        if (data_point == nullptr) {
            throw std::runtime_error("RaBitQ query received null data point");
        }
        PreparedQuery &prepared = *const_cast<PreparedQuery *>(
            static_cast<const PreparedQuery *>(prepared_query));
        const uint8_t id = *centroidId(data_point);
        if (static_cast<size_t>(id) >= prepared.centroid_queries.size()) {
            throw std::runtime_error("RaBitQ payload centroid_id is out of range");
        }
        if (!prepared.ready[id]) {
            buildCentroidQuery(prepared, id);
        }
        return prepared.centroid_queries[id];
    }

    static bool optimizedIntQueryPreparationEnabled() {
        static const bool enabled =
            std::getenv("RABITQ_DISABLE_SIMD_INT_PREP") == nullptr;
        return enabled;
    }

    void buildCentroidQuery(PreparedQuery &prepared, size_t centroid_id) const {
        QueryContext &query = prepared.centroid_queries[centroid_id];
        query.b1_signed_sum = 0;
        query.b1_short_scale = 0.0f;
        query.b1_error_scale = 0.0f;
        query.b1_alpha = 0.0f;
        query.int8_scale = 1.0f;
        query.int4_scale = 1.0f;
        query.int8_error_norm = 0.0f;
        query.int4_error_norm = 0.0f;
        query.int8_code_sum = 0;
        query.int4_code_sum = 0;
        query.rotated_residual.assign(code_dim_, 0.0f);
        const float *rotated_centroid = rotated_centroids_.data() + centroid_id * code_dim_;
        for (size_t i = 0; i < code_dim_; ++i) {
            query.rotated_residual[i] = prepared.rotated_query[i] - rotated_centroid[i];
        }

        double dot_q_centroid = 0.0;
        const float *centroid = centroids_.data() + centroid_id * dim_;
        for (size_t i = 0; i < dim_; ++i) {
            dot_q_centroid += static_cast<double>(prepared.raw_query[i]) * centroid[i];
        }
        query.query_norm_sqr = static_cast<float>(
            prepared.raw_query_norm_sqr + centroid_norm_sqr_[centroid_id] - 2.0 * dot_q_centroid);
        query.query_norm_sqr = std::max(0.0f, query.query_norm_sqr);

        const size_t pair_count = code_dim_ >> 1U;
        query.rotated_residual_even.assign(pair_count, 0.0f);
        query.rotated_residual_odd.assign(pair_count, 0.0f);
        query.half_sum_residual = 0.0f;
        query.rotated_residual_sum = 0.0f;
        query.positive_sum_residual = 0.0f;
        query.residual_nibble_lut_ready = false;
        query.residual_2bit_lut_ready = false;
        const QueryCoarseCodec codec = prepared.query_coarse_codec;
        const bool need_b1 =
            codec == QueryCoarseCodec::B1 ||
            codec == QueryCoarseCodec::B1Main;
        const bool need_int4 = codec == QueryCoarseCodec::Int4;
        const bool need_int8 = codec == QueryCoarseCodec::Int8;
        float int_max_abs = 0.0f;
        for (size_t pair = 0; pair < pair_count; ++pair) {
            const float even_value = query.rotated_residual[pair << 1U];
            const float odd_value = query.rotated_residual[(pair << 1U) + 1U];
            query.rotated_residual_even[pair] = even_value;
            query.rotated_residual_odd[pair] = odd_value;
            query.half_sum_residual += even_value + odd_value;
            query.rotated_residual_sum += even_value + odd_value;
            query.positive_sum_residual += std::max(0.0f, even_value) +
                std::max(0.0f, odd_value);
            if (need_int4 || need_int8) {
                int_max_abs = std::max(
                    int_max_abs,
                    std::max(std::fabs(even_value), std::fabs(odd_value)));
            }
        }
        query.half_sum_residual *= 0.5f;
        query.query_norm = std::sqrt(query.query_norm_sqr);

        // Build only the representation selected for this query.  The formal
        // Full path needs the rotated FP32 residual only; constructing B1,
        // INT4 and INT8 codes here used to waste a full hash/RNG/quantization
        // pass on every query.
        if (need_b1) {
            const size_t b1_bytes = (code_dim_ + 7U) / 8U;
            query.b1_code.assign(b1_bytes, 0);
            query.b1_signed.assign(code_dim_, 0);
            float abs_sum = 0.0f;
            for (size_t i = 0; i < code_dim_; ++i) {
                const float r = query.rotated_residual[i];
                abs_sum += std::fabs(r);
                if (r > 0.0f) {
                    query.b1_code[i >> 3U] = static_cast<uint8_t>(
                        query.b1_code[i >> 3U] |
                        static_cast<uint8_t>(1U << (i & 7U)));
                    query.b1_signed[i] = 1;
                    query.b1_signed_sum += 1;
                } else {
                    query.b1_signed[i] = -1;
                    query.b1_signed_sum -= 1;
                }
            }
            if (query.query_norm > 0.0f && abs_sum > 0.0f && code_dim_ > 1) {
                const float a_q = abs_sum / query.query_norm;
                const float alpha_q = std::min(
                    1.0f, a_q / std::sqrt(static_cast<float>(code_dim_)));
                const float alpha2 = alpha_q * alpha_q;
                query.b1_alpha = alpha_q;
                query.b1_short_scale = 4.0f * query.query_norm / a_q;
                query.b1_error_scale =
                    2.0f * query.query_norm *
                    std::sqrt(std::max(0.0f, (1.0f - alpha2) / alpha2)) /
                    std::sqrt(static_cast<float>(code_dim_ - 1));
            }
        }
        if (need_int4 || need_int8) {
            if (need_int4) query.int4_code.assign(code_dim_, 0);
            if (need_int8) query.int8_code.assign(code_dim_, 0);
            float max_abs = int_max_abs;
            if (!optimizedIntQueryPreparationEnabled()) {
                // Diagnostic legacy path: retain the separate max pass so the
                // environment switch isolates the complete preparation
                // optimization, including pass fusion and buffer reuse.
                max_abs = 0.0f;
                for (size_t i = 0; i < code_dim_; ++i) {
                    max_abs = std::max(
                        max_abs, std::fabs(query.rotated_residual[i]));
                }
            }
            if (max_abs > 0.0f) {
                query.int8_scale = max_abs / 128.0f;
                query.int4_scale = max_abs / 8.0f;
            // RaBitQ 3.3.1 randomized scalar quantization: x = floor(v + u)
            // with u ~ U[0,1) makes E[q_hat] = q exactly, so per-coordinate
            // query-quantization errors cancel across dimensions (Thm 3.3)
            // and need no explicit term in the coarse bound. The dither is
            // seeded deterministically from the raw query bytes and centroid
            // id, so batch/legacy parity and cross-run reproducibility hold
            // while still drawing pseudo-random u_i in [0,1).
            uint64_t dither_seed = 1469598103934665603ULL;  // FNV-1a basis
            const uint8_t *raw_bytes =
                reinterpret_cast<const uint8_t *>(prepared.raw_query);
            for (size_t i = 0; i < dim_ * sizeof(float); ++i) {
                dither_seed ^= raw_bytes[i];
                dither_seed *= 1099511628211ULL;
            }
            dither_seed ^= static_cast<uint64_t>(centroid_id) +
                0x9E3779B97F4A7C15ULL +
                (dither_seed << 6U) + (dither_seed >> 2U);
            std::mt19937_64 dither_rng(dither_seed);
            std::uniform_real_distribution<double> u01(0.0, 1.0);
            double err8_sqr = 0.0;
            double err4_sqr = 0.0;
#if defined(__AVX512F__)
            const bool use_simd_int_prep =
                optimizedIntQueryPreparationEnabled();
#else
            const bool use_simd_int_prep = false;
#endif
            size_t i = 0;
#if defined(__AVX512F__)
            if (use_simd_int_prep) {
                const float scale = need_int8
                    ? query.int8_scale : query.int4_scale;
                const __m512d scale8 = _mm512_set1_pd(
                    static_cast<double>(scale));
                const __m256i minimum = _mm256_set1_epi32(
                    need_int8 ? -128 : -8);
                const __m256i maximum = _mm256_set1_epi32(
                    need_int8 ? 127 : 7);
                alignas(64) double dithers[8];
                alignas(32) int32_t codes[8];
                for (; i + 8U <= code_dim_; i += 8U) {
                    for (size_t lane = 0; lane < 8U; ++lane) {
                        // Consume both values in the original order. This
                        // keeps the optimized code bit-identical to the
                        // scalar preparation path and makes the env switch a
                        // clean performance-only ablation.
                        const double u8 = u01(dither_rng);
                        const double u4 = u01(dither_rng);
                        dithers[lane] = need_int8 ? u8 : u4;
                    }
                    const __m256 residual8 = _mm256_loadu_ps(
                        query.rotated_residual.data() + i);
                    const __m512d residual8d =
                        _mm512_cvtps_pd(residual8);
                    const __m512d quantized = _mm512_floor_pd(
                        _mm512_add_pd(
                            _mm512_div_pd(residual8d, scale8),
                            _mm512_load_pd(dithers)));
                    __m256i values = _mm512_cvttpd_epi32(quantized);
                    values = _mm256_max_epi32(
                        minimum, _mm256_min_epi32(maximum, values));
                    _mm256_store_si256(
                        reinterpret_cast<__m256i *>(codes), values);
                    for (size_t lane = 0; lane < 8U; ++lane) {
                        const int32_t x = codes[lane];
                        const float r = query.rotated_residual[i + lane];
                        const double error = static_cast<double>(r) -
                            static_cast<double>(scale) *
                            static_cast<double>(x);
                        if (need_int8) {
                            err8_sqr += error * error;
                            query.int8_code[i + lane] =
                                static_cast<int8_t>(x);
                            query.int8_code_sum += x;
                        } else {
                            err4_sqr += error * error;
                            query.int4_code[i + lane] =
                                static_cast<int8_t>(x);
                            query.int4_code_sum += x;
                        }
                    }
                }
            }
#endif
            for (; i < code_dim_; ++i) {
                const float r = query.rotated_residual[i];
                // Draw both dithers to preserve the pre-optimization code
                // stream for each codec exactly.
                const double u8 = u01(dither_rng);
                const double u4 = u01(dither_rng);
                if (need_int8) {
                    int32_t x8 = static_cast<int32_t>(std::floor(
                        static_cast<double>(r) / query.int8_scale + u8));
                    x8 = std::max(-128, std::min(127, x8));
                    const double e8 = static_cast<double>(r) -
                        static_cast<double>(query.int8_scale) *
                        static_cast<double>(x8);
                    err8_sqr += e8 * e8;
                    query.int8_code[i] = static_cast<int8_t>(x8);
                    query.int8_code_sum += x8;
                }
                if (need_int4) {
                    int32_t x4 = static_cast<int32_t>(std::floor(
                        static_cast<double>(r) / query.int4_scale + u4));
                    x4 = std::max(-8, std::min(7, x4));
                    const double e4 = static_cast<double>(r) -
                        static_cast<double>(query.int4_scale) *
                        static_cast<double>(x4);
                    err4_sqr += e4 * e4;
                    query.int4_code[i] = static_cast<int8_t>(x4);
                    query.int4_code_sum += x4;
                }
            }
            if (need_int8)
                query.int8_error_norm = static_cast<float>(std::sqrt(err8_sqr));
            if (need_int4)
                query.int4_error_norm = static_cast<float>(std::sqrt(err4_sqr));
            }
        }
        prepared.ready[centroid_id] = 1;
        ++prepared.active_centroid_count;
    }

    void initializePreparedQuery(
        PreparedQuery &prepared,
        const float *raw_query,
        bool build_eager_centroids) const {
        if (raw_query == nullptr)
            throw std::invalid_argument("RaBitQ query preparation received null data");
        // The public query object is thread-local. Preserve each centroid's
        // vector capacity across queries instead of destroying and allocating
        // rotated/query-code/LUT buffers for every search.
        if (optimizedIntQueryPreparationEnabled()) {
            prepared.centroid_queries.resize(centroid_count_);
        } else {
            prepared.centroid_queries.assign(
                centroid_count_, QueryContext{});
        }
        prepared.ready.assign(centroid_count_, 0);
        prepared.raw_query = raw_query;
        prepared.active_centroid_count = 0;
        prepared.query_coarse_codec = query_coarse_codec_;
        rotate(raw_query, prepared.rotated_query);

        double raw_query_norm_sqr = 0.0;
        for (size_t i = 0; i < dim_; ++i) {
            const double value = raw_query[i];
            raw_query_norm_sqr += value * value;
        }
        prepared.raw_query_norm_sqr = raw_query_norm_sqr;

        if (build_eager_centroids && centroid_query_mode_ == CentroidQueryMode::Eager) {
            for (size_t centroid_id = 0; centroid_id < centroid_count_; ++centroid_id) {
                buildCentroidQuery(prepared, centroid_id);
            }
        }
    }

    const void *prepare_query(const void *query_data) override {
        const float *raw_query = static_cast<const float *>(query_data);
        // PCA routing and full-dimensional verification coexist on each worker.
        // Preparing one quantizer must not overwrite the other's query context.
        thread_local std::unordered_map<const RaBitQSpace *, PreparedQuery> prepared_storage_by_space;
        PreparedQuery *prepared = &prepared_storage_by_space[this];
        initializePreparedQuery(*prepared, raw_query, true);
        return prepared;
    }

    size_t query_active_centroids(const void *prepared_query) const override {
        return static_cast<const PreparedQuery *>(prepared_query)->active_centroid_count;
    }

    void set_centroid_query_mode(CentroidQueryMode mode) {
        centroid_query_mode_ = mode;
    }

    CentroidQueryMode get_centroid_query_mode() const {
        return centroid_query_mode_;
    }

    void set_query_coarse_codec(QueryCoarseCodec codec) {
        query_coarse_codec_ = codec;
    }

    QueryCoarseCodec get_query_coarse_codec() const {
        return query_coarse_codec_;
    }

    void release_query(const void *prepared_query) override {
        (void) prepared_query;
    }

    float query_distance(const void *prepared_query, const void *data_point) override {
        const QueryContext &query = queryForEncoded(prepared_query, data_point);
        return queryDistanceLong(query, data_point);
    }

    // 4-bit data x 1-bit query main distance: the query residual is reduced to
    // its sign code and substituted into the existing long-distance estimator
    // (signed_long_ip = <c - 7.5, q_hat>), with all dots evaluated as integer
    // dot products against the +/-1 query code. Top-k full-precision rerank
    // (residual distance) is applied by the caller.
    float query_distance_b1(const void *prepared_query, const void *data_point) const {
        const QueryContext &query = queryForEncoded(prepared_query, data_point);
        const EncodedHeader header = loadHeader(data_point);
        if (!(header.long_scale > 0.0f) || !std::isfinite(header.long_scale)) {
            return header.norm_sqr + query.query_norm_sqr;
        }
        const int64_t dot_cq = quantizedLongCodeDotDispatch(
            codeBytes(data_point), query.b1_signed.data());
        const double signed_long_ip =
            static_cast<double>(dot_cq) - 7.5 * static_cast<double>(query.b1_signed_sum);
        const double est_ip = static_cast<double>(header.long_scale) * signed_long_ip;
        return header.norm_sqr + query.query_norm_sqr - static_cast<float>(est_ip);
    }

    float asymmetric_build_distance(
        const void *raw_query,
        const void *encoded_database) override {
        if (raw_query == nullptr || encoded_database == nullptr)
            throw std::invalid_argument("RaBitQ asymmetric build distance received null data");
        PreparedQuery prepared;
        initializePreparedQuery(
            prepared, static_cast<const float *>(raw_query), false);
        const QueryContext &query = queryForEncoded(&prepared, encoded_database);
        return queryDistanceLong(query, encoded_database);
    }

    const void *prepare_asymmetric_build_query(const void *raw_query) override {
        if (raw_query == nullptr)
            throw std::invalid_argument("RaBitQ asymmetric build query received null data");
        std::vector<std::unique_ptr<BuildPreparedSlot>> &pool = buildPreparedPool();
        BuildPreparedSlot *slot = nullptr;
        for (const std::unique_ptr<BuildPreparedSlot> &candidate : pool) {
            if (!candidate->busy) {
                slot = candidate.get();
                break;
            }
        }
        if (slot == nullptr) {
            pool.emplace_back(new BuildPreparedSlot());
            slot = pool.back().get();
        }
        slot->owner = this;
        slot->busy = true;
        try {
            // Build-distance contexts are lazy: only centroids encountered by
            // the rhs payloads are materialized, matching the old one-shot path.
            initializePreparedQuery(
                slot->query, static_cast<const float *>(raw_query), false);
        } catch (...) {
            slot->busy = false;
            slot->owner = nullptr;
            throw;
        }
        return &slot->query;
    }

    float asymmetric_build_distance_prepared(
        const void *prepared_query,
        const void *encoded_database) override {
        if (prepared_query == nullptr || encoded_database == nullptr)
            throw std::invalid_argument("RaBitQ prepared asymmetric distance received null data");
        const QueryContext &query = queryForEncoded(prepared_query, encoded_database);
        return queryDistanceLong(query, encoded_database);
    }

    float asymmetric_build_inner_product_prepared(
        const void *prepared_query,
        const void *encoded_database) {
        if (prepared_query == nullptr || encoded_database == nullptr)
            throw std::invalid_argument("RaBitQ prepared asymmetric IP received null data");
        const QueryContext &query = queryForEncoded(prepared_query, encoded_database);
        const EncodedHeader header = loadHeader(encoded_database);
        const float distance = queryDistanceLong(query, encoded_database);
        // L2^2 = ||q||^2 + ||x||^2 - 2*IP  =>  IP = (||q||^2 + ||x||^2 - L2^2)/2
        return 0.5f * (query.query_norm_sqr + header.norm_sqr - distance);
    }

    void release_asymmetric_build_query(const void *prepared_query) override {
        if (prepared_query == nullptr) return;
        std::vector<std::unique_ptr<BuildPreparedSlot>> &pool = buildPreparedPool();
        for (const std::unique_ptr<BuildPreparedSlot> &candidate : pool) {
            if (&candidate->query == prepared_query && candidate->owner == this) {
                candidate->busy = false;
                candidate->owner = nullptr;
                return;
            }
        }
    }

    bool supports_symmetric_build_prepared() const override {
        return true;
    }

    const void *prepare_symmetric_build_query(const void *encoded_query) override {
        std::vector<std::unique_ptr<SymmetricBuildPreparedQuery>> &pool =
            symmetricBuildPreparedPool();
        SymmetricBuildPreparedQuery *slot = nullptr;
        for (const std::unique_ptr<SymmetricBuildPreparedQuery> &candidate : pool) {
            if (!candidate->busy) {
                slot = candidate.get();
                break;
            }
        }
        if (slot == nullptr) {
            pool.emplace_back(new SymmetricBuildPreparedQuery());
            slot = pool.back().get();
        }
        slot->busy = true;
        try {
            initializeSymmetricBuildPrepared(*slot, encoded_query);
        } catch (...) {
            slot->busy = false;
            slot->owner = nullptr;
            throw;
        }
        return slot;
    }

    float symmetric_build_distance_prepared(
        const void *prepared_query,
        const void *encoded_database) override {
        if (prepared_query == nullptr)
            throw std::invalid_argument("RaBitQ symmetric prepared distance received null query");
        const SymmetricBuildPreparedQuery &query =
            *static_cast<const SymmetricBuildPreparedQuery *>(prepared_query);
        if (query.owner != this)
            throw std::invalid_argument("RaBitQ symmetric prepared query belongs to another space");
        return symmetricBuildDistanceFromPrepared(query, encoded_database);
    }

    void symmetric_build_distance_batch(
        const void *prepared_query,
        const void *const *data_points,
        size_t count,
        float *distances,
        size_t prefetch_distance) override {
        if (prepared_query == nullptr)
            throw std::invalid_argument("RaBitQ symmetric batch distance received null query");
        const SymmetricBuildPreparedQuery &query =
            *static_cast<const SymmetricBuildPreparedQuery *>(prepared_query);
        if (query.owner != this)
            throw std::invalid_argument("RaBitQ symmetric prepared query belongs to another space");
        for (size_t i = 0; i < count; ++i) {
            const size_t pf = i + prefetch_distance;
            if (pf < count) {
#if defined(__GNUC__) || defined(__clang__)
                __builtin_prefetch(data_points[pf], 0, 1);
#endif
            }
            distances[i] = symmetricBuildDistanceFromPrepared(query, data_points[i]);
        }
    }

    void release_symmetric_build_query(const void *prepared_query) override {
        if (prepared_query == nullptr) return;
        std::vector<std::unique_ptr<SymmetricBuildPreparedQuery>> &pool =
            symmetricBuildPreparedPool();
        for (const std::unique_ptr<SymmetricBuildPreparedQuery> &candidate : pool) {
            if (candidate.get() == prepared_query && candidate->owner == this) {
                candidate->busy = false;
                candidate->owner = nullptr;
                candidate->code = nullptr;
                candidate->valid = false;
                return;
            }
        }
    }

    float compute_short_lower_bound(
        const void *prepared_query,
        const void *data_point) override {
        return queryDistanceShortLowerBoundForLong(
            queryForEncoded(prepared_query, data_point), data_point);
    }

    float compute_two_bit_lower_bound(
        const void *prepared_query,
        const void *data_point) override {
        if (code_layout_ != RaBitQCodeLayout::SequentialNibble)
            throw std::runtime_error("2-bit lower bound requires Sequential layout");
        return queryDistanceTwoBitLowerBoundForLong(
            queryForEncoded(prepared_query, data_point), data_point);
    }

    PaperPruneEstimate<float> compute_paper_prune_estimate(
        const void *prepared_query,
        const void *data_point,
        float epsilon0) override {
        const QueryContext &query = queryForEncoded(prepared_query, data_point);
        const EncodedHeader header = loadHeader(data_point);
        const ShortCodeFactors factors = *shortFactors(data_point);
        PaperPruneEstimate<float> result;
        if (code_dim_ <= 1 || epsilon0 < 0.0f || !std::isfinite(epsilon0) ||
            header.norm_sqr <= 0.0f || query.query_norm <= 0.0f ||
            factors.short_scale <= 0.0f || !std::isfinite(factors.short_scale))
            return result;

        const float data_norm = std::sqrt(header.norm_sqr);
        const float alpha_raw = 4.0f * data_norm /
            (factors.short_scale * std::sqrt(static_cast<float>(code_dim_)));
        if (!(alpha_raw > 0.0f) || !std::isfinite(alpha_raw)) return result;
        const float alpha = std::min(1.0f, alpha_raw);
        const float short_ip = shortCodeIp(query, codeBytes(data_point));
        const float cross_estimate = short_ip * factors.short_scale;
        const float two_norms = 2.0f * data_norm * query.query_norm;
        const float ip_hat = cross_estimate / two_norms;
        const float alpha2 = alpha * alpha;
        const float error_bound = std::sqrt(std::max(0.0f, (1.0f - alpha2) / alpha2)) *
            epsilon0 / std::sqrt(static_cast<float>(code_dim_ - 1));
        const float ip_upper = std::max(-1.0f, std::min(1.0f, ip_hat + error_bound));
        result.lower_bound = header.norm_sqr + query.query_norm_sqr - two_norms * ip_upper;
        result.short_ip = short_ip;
        result.alpha = alpha;
        result.ip_hat = ip_hat;
        result.error_bound = error_bound;
        result.valid = std::isfinite(result.lower_bound);
        return result;
    }

    float query_distance_with_paper_msb(
        const void *prepared_query,
        const void *data_point,
        float short_ip) override {
        const QueryContext &query = queryForEncoded(prepared_query, data_point);
        return queryDistanceLongWithShortIp(query, data_point, loadHeader(data_point), short_ip);
    }

    // FP32 traversal after the fixed-1-bit DB gate. Interleave eight random
    // candidates so each pair of 16-wide even/odd query vectors is loaded once
    // and reused by eight independent remaining-3-bit accumulators.
    void query_distance_with_paper_msb_batch_by_id(
        const void *prepared_query,
        const uint32_t *ids,
        size_t count,
        const void *records_base,
        size_t record_stride,
        const float *short_ips,
        float *out_distances) const {
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const bool simd_ok =
            centroid_count_ == 1 &&
            code_layout_ == RaBitQCodeLayout::SequentialNibble &&
            (code_dim_ % 32U == 0U) &&
            (prepared.query_coarse_codec == QueryCoarseCodec::Full ||
             prepared.query_coarse_codec == QueryCoarseCodec::FullStaged ||
             prepared.query_coarse_codec == QueryCoarseCodec::FullRerankScalar);
#else
        const bool simd_ok = false;
#endif
        const char *record_bytes = static_cast<const char *>(records_base);
        if (!simd_ok) {
            for (size_t c = 0; c < count; ++c) {
                const void *record = record_bytes +
                    static_cast<size_t>(ids[c]) * record_stride;
                const QueryContext &query =
                    queryForEncoded(prepared_query, record);
                out_distances[c] = queryDistanceLongWithShortIp(
                    query, record, loadHeader(record), short_ips[c]);
            }
            return;
        }
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const QueryContext &query = prepared.centroid_queries[0];
        const __m128i remaining_mask = _mm_set1_epi8(
            static_cast<char>(kRemainingMax));
        const size_t pair_count = code_dim_ >> 1U;
        for (size_t group = 0; group < count; group += 8U) {
            const size_t lanes = std::min<size_t>(8U, count - group);
            const void *records[8] = {};
            const uint8_t *codes[8] = {};
            __m512 acc[8] = {
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
            };
            for (size_t lane = 0; lane < lanes; ++lane) {
#if defined(__GNUC__) || defined(__clang__)
                const size_t pf = group + lane + 8U;
                if (pf < count) {
                    __builtin_prefetch(
                        record_bytes + static_cast<size_t>(ids[pf]) *
                            record_stride,
                        0, 3);
                }
#endif
                records[lane] = record_bytes +
                    static_cast<size_t>(ids[group + lane]) * record_stride;
                codes[lane] = codeBytes(records[lane]);
            }
            for (size_t pair = 0; pair < pair_count; pair += 16U) {
                const __m512 even_q = _mm512_loadu_ps(
                    query.rotated_residual_even.data() + pair);
                const __m512 odd_q = _mm512_loadu_ps(
                    query.rotated_residual_odd.data() + pair);
                for (size_t lane = 0; lane < lanes; ++lane) {
                    const __m128i packed = _mm_loadu_si128(
                        reinterpret_cast<const __m128i *>(
                            codes[lane] + pair));
                    const __m128i lo = _mm_and_si128(
                        packed, remaining_mask);
                    const __m128i hi = _mm_and_si128(
                        _mm_srli_epi16(packed, 4), remaining_mask);
                    acc[lane] = _mm512_add_ps(
                        acc[lane],
                        _mm512_mul_ps(
                            _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(lo)),
                            even_q));
                    acc[lane] = _mm512_add_ps(
                        acc[lane],
                        _mm512_mul_ps(
                            _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(hi)),
                            odd_q));
                }
            }
            for (size_t lane = 0; lane < lanes; ++lane) {
                const EncodedHeader header = loadHeader(records[lane]);
                if (!(header.long_scale > 0.0f) ||
                    !std::isfinite(header.long_scale)) {
                    out_distances[group + lane] =
                        header.norm_sqr + query.query_norm_sqr;
                    continue;
                }
                out_distances[group + lane] = queryDistanceLongWithIps(
                    query, header, short_ips[group + lane],
                    _mm512_reduce_add_ps(acc[lane]));
            }
        }
#endif
    }

    // Complete FP32-query 4-bit distance for random graph candidates. This is
    // the mode-0 path used by B1 traversal and by the final full-query restore
    // for INT4/INT8. Interleave eight records so the even/odd query vectors are
    // loaded once while both the MSB and remaining-three-bit products are
    // accumulated for every candidate.
    void query_distance_batch_by_id(
        const void *prepared_query,
        const uint32_t *ids,
        size_t count,
        const void *records_base,
        size_t record_stride,
        float *out_distances) const {
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const bool simd_ok =
            centroid_count_ == 1 &&
            code_layout_ == RaBitQCodeLayout::SequentialNibble &&
            (code_dim_ % 32U == 0U);
#else
        const bool simd_ok = false;
#endif
        const char *record_bytes = static_cast<const char *>(records_base);
        if (!simd_ok) {
            for (size_t c = 0; c < count; ++c) {
                const void *record = record_bytes +
                    static_cast<size_t>(ids[c]) * record_stride;
                out_distances[c] = queryDistanceLong(
                    queryForEncoded(prepared_query, record), record);
            }
            return;
        }
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const QueryContext &query = prepared.centroid_queries[0];
        const __m128i remaining_mask = _mm_set1_epi8(
            static_cast<char>(kRemainingMax));
        const __m128i nibble_mask = _mm_set1_epi8(0x0F);
        const __m128i selected_threshold = _mm_set1_epi8(
            static_cast<char>(kRemainingMax));
        const size_t pair_count = code_dim_ >> 1U;
        for (size_t group = 0; group < count; group += 8U) {
            const size_t lanes = std::min<size_t>(8U, count - group);
            const void *records[8] = {};
            const uint8_t *codes[8] = {};
            __m512 short_acc[8] = {
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
            };
            __m512 remaining_acc[8] = {
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
            };
            for (size_t lane = 0; lane < lanes; ++lane) {
#if defined(__GNUC__) || defined(__clang__)
                const size_t pf = group + lane + 8U;
                if (pf < count) {
                    __builtin_prefetch(
                        record_bytes + static_cast<size_t>(ids[pf]) *
                            record_stride,
                        0, 3);
                }
#endif
                records[lane] = record_bytes +
                    static_cast<size_t>(ids[group + lane]) * record_stride;
                codes[lane] = codeBytes(records[lane]);
            }
            for (size_t pair = 0; pair < pair_count; pair += 16U) {
                const __m512 even_q = _mm512_loadu_ps(
                    query.rotated_residual_even.data() + pair);
                const __m512 odd_q = _mm512_loadu_ps(
                    query.rotated_residual_odd.data() + pair);
                for (size_t lane = 0; lane < lanes; ++lane) {
                    const __m128i packed = _mm_loadu_si128(
                        reinterpret_cast<const __m128i *>(
                            codes[lane] + pair));
                    const __m128i lo = _mm_and_si128(
                        packed, remaining_mask);
                    const __m128i hi = _mm_and_si128(
                        _mm_srli_epi16(packed, 4), remaining_mask);
                    const __m128i raw_lo = _mm_and_si128(
                        packed, nibble_mask);
                    const __m128i raw_hi = _mm_and_si128(
                        _mm_srli_epi16(packed, 4), nibble_mask);
                    const __mmask16 lo_selected = static_cast<__mmask16>(
                        _mm_movemask_epi8(
                            _mm_cmpgt_epi8(raw_lo, selected_threshold)));
                    const __mmask16 hi_selected = static_cast<__mmask16>(
                        _mm_movemask_epi8(
                            _mm_cmpgt_epi8(raw_hi, selected_threshold)));
                    remaining_acc[lane] = _mm512_add_ps(
                        remaining_acc[lane],
                        _mm512_mul_ps(
                            _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(lo)),
                            even_q));
                    remaining_acc[lane] = _mm512_add_ps(
                        remaining_acc[lane],
                        _mm512_mul_ps(
                            _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(hi)),
                            odd_q));
                    short_acc[lane] = _mm512_mask_add_ps(
                        short_acc[lane], lo_selected,
                        short_acc[lane], even_q);
                    short_acc[lane] = _mm512_mask_add_ps(
                        short_acc[lane], hi_selected,
                        short_acc[lane], odd_q);
                }
            }
            for (size_t lane = 0; lane < lanes; ++lane) {
                const EncodedHeader header = loadHeader(records[lane]);
                if (!(header.long_scale > 0.0f) ||
                    !std::isfinite(header.long_scale)) {
                    out_distances[group + lane] =
                        header.norm_sqr + query.query_norm_sqr;
                    continue;
                }
                out_distances[group + lane] = queryDistanceLongWithIps(
                    query, header,
                    _mm512_reduce_add_ps(short_acc[lane]) -
                        query.half_sum_residual,
                    _mm512_reduce_add_ps(remaining_acc[lane]));
            }
        }
#endif
    }

    // INT4/INT8 traversal distance after the fixed-1-bit DB gate. short_ip is
    // the quantized-query dot against the DB MSB plane computed by that gate,
    // so only the remaining three DB bits need to be decoded here.
    float query_distance_with_quantized_paper_msb(
        const void *prepared_query,
        const void *data_point,
        float short_ip) const {
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = queryForEncoded(prepared_query, data_point);
        const EncodedHeader header = loadHeader(data_point);
        if (!(header.long_scale > 0.0f) || !std::isfinite(header.long_scale)) {
            return header.norm_sqr + query.query_norm_sqr;
        }
        const bool int8_mode =
            prepared.query_coarse_codec == QueryCoarseCodec::Int8;
        const int8_t *x =
            int8_mode ? query.int8_code.data() : query.int4_code.data();
        const float scale = int8_mode ? query.int8_scale : query.int4_scale;
        const int64_t code_sum =
            int8_mode ? query.int8_code_sum : query.int4_code_sum;
        const int64_t remaining_dot = quantizedRemainingCodeDotDispatch(
            codeBytes(data_point), x);
        return finishQuantizedTraversalDistance(
            query, header, scale, code_sum, short_ip, remaining_dot);
    }

    // Common INT4/INT8 traversal case: every item in the bridge batch has a
    // valid 1-bit-gate short_ip. Interleave eight independent accumulators so
    // one 64-byte query load is reused across eight random DB records.
    void query_distance_with_quantized_paper_msb_batch_by_id(
        const void *prepared_query,
        const uint32_t *ids,
        size_t count,
        const void *records_base,
        size_t record_stride,
        const float *short_ips,
        float *out_distances) const {
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const bool simd_ok =
            centroid_count_ == 1 &&
            code_layout_ == RaBitQCodeLayout::SequentialNibble &&
            (code_dim_ % 64U == 0U) &&
            code_dim_ <= static_cast<size_t>(
                std::numeric_limits<int32_t>::max() /
                (static_cast<int32_t>(kRemainingMax) * 128)) &&
            (prepared.query_coarse_codec == QueryCoarseCodec::Int4 ||
             prepared.query_coarse_codec == QueryCoarseCodec::Int8);
#else
        const bool simd_ok = false;
#endif
        const char *record_bytes = static_cast<const char *>(records_base);
        if (!simd_ok) {
            for (size_t c = 0; c < count; ++c) {
                out_distances[c] = query_distance_with_quantized_paper_msb(
                    prepared_query,
                    record_bytes + static_cast<size_t>(ids[c]) * record_stride,
                    short_ips[c]);
            }
            return;
        }
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const QueryContext &query = prepared.centroid_queries[0];
        const bool int8_mode =
            prepared.query_coarse_codec == QueryCoarseCodec::Int8;
        const int8_t *x =
            int8_mode ? query.int8_code.data() : query.int4_code.data();
        const float scale = int8_mode ? query.int8_scale : query.int4_scale;
        const int64_t code_sum =
            int8_mode ? query.int8_code_sum : query.int4_code_sum;
        const __m256i remaining_mask = _mm256_set1_epi8(
            static_cast<char>(kRemainingMax));
        const __m512i ones16 = _mm512_set1_epi16(1);
        for (size_t group = 0; group < count; group += 8U) {
            const size_t lanes = std::min<size_t>(8U, count - group);
            const void *records[8] = {};
            const uint8_t *codes[8] = {};
            __m512i acc[8] = {
                _mm512_setzero_si512(), _mm512_setzero_si512(),
                _mm512_setzero_si512(), _mm512_setzero_si512(),
                _mm512_setzero_si512(), _mm512_setzero_si512(),
                _mm512_setzero_si512(), _mm512_setzero_si512(),
            };
            for (size_t lane = 0; lane < lanes; ++lane) {
#if defined(__GNUC__) || defined(__clang__)
                const size_t pf = group + lane + 8U;
                if (pf < count) {
                    __builtin_prefetch(
                        record_bytes + static_cast<size_t>(ids[pf]) *
                            record_stride,
                        0, 3);
                }
#endif
                records[lane] = record_bytes +
                    static_cast<size_t>(ids[group + lane]) * record_stride;
                codes[lane] = codeBytes(records[lane]);
            }
            for (size_t offset = 0; offset < code_dim_; offset += 64U) {
                const __m512i qbytes = _mm512_loadu_si512(
                    reinterpret_cast<const void *>(x + offset));
                for (size_t lane = 0; lane < lanes; ++lane) {
                    const __m256i packed = _mm256_loadu_si256(
                        reinterpret_cast<const __m256i *>(
                            codes[lane] + (offset >> 1U)));
                    const __m256i lo = _mm256_and_si256(
                        packed, remaining_mask);
                    const __m256i hi = _mm256_and_si256(
                        _mm256_srli_epi16(packed, 4), remaining_mask);
                    const __m256i interleave_lo =
                        _mm256_unpacklo_epi8(lo, hi);
                    const __m256i interleave_hi =
                        _mm256_unpackhi_epi8(lo, hi);
                    const __m256i db_low = _mm256_permute2x128_si256(
                        interleave_lo, interleave_hi, 0x20);
                    const __m256i db_high = _mm256_permute2x128_si256(
                        interleave_lo, interleave_hi, 0x31);
                    __m512i db_values = _mm512_castsi256_si512(db_low);
                    db_values = _mm512_inserti64x4(
                        db_values, db_high, 1);
                    const __m512i pair_sums =
                        _mm512_maddubs_epi16(db_values, qbytes);
                    acc[lane] = _mm512_add_epi32(
                        acc[lane], _mm512_madd_epi16(pair_sums, ones16));
                }
            }
            for (size_t lane = 0; lane < lanes; ++lane) {
                const int64_t remaining_dot = static_cast<int64_t>(
                    _mm512_reduce_add_epi32(acc[lane]));
                out_distances[group + lane] = finishQuantizedTraversalDistance(
                    query, loadHeader(records[lane]), scale, code_sum,
                    short_ips[group + lane], remaining_dot);
            }
        }
#endif
    }

    float finishQuantizedTraversalDistance(
        const QueryContext &query,
        const EncodedHeader &header,
        float scale,
        int64_t code_sum,
        float short_ip,
        int64_t remaining_dot) const {
        const double centered_remaining = static_cast<double>(scale) * (
            static_cast<double>(remaining_dot) -
            0.5 * static_cast<double>(kRemainingMax) *
                static_cast<double>(code_sum));
        const double signed_long_ip =
            static_cast<double>(kMsbWeight) * static_cast<double>(short_ip) +
            centered_remaining;
        return header.norm_sqr + query.query_norm_sqr - static_cast<float>(
            static_cast<double>(header.long_scale) * signed_long_ip);
    }

    size_t paper_msb_code_bytes() const override {
        return (code_dim_ + 7U) / 8U;
    }

    PaperPruneFactors<float> extract_paper_prune_sidecar(
        const void *data_point,
        uint8_t *msb_out) const override {
        const size_t bytes = paper_msb_code_bytes();
        std::memset(msb_out, 0, bytes);
        const uint8_t *code = codeBytes(data_point);
        for (size_t i = 0; i < code_dim_; ++i) {
            if ((primaryCodeValue(code, i) >> kRemainingBits) != 0)
                msb_out[i >> 3U] |= static_cast<uint8_t>(1U << (i & 7U));
        }
        const EncodedHeader header = loadHeader(data_point);
        const ShortCodeFactors factors = *shortFactors(data_point);
        PaperPruneFactors<float> out;
        out.norm_sqr = header.norm_sqr;
        if (code_dim_ <= 1 || header.norm_sqr <= 0.0f || factors.short_scale <= 0.0f)
            return out;
        const float data_norm = std::sqrt(header.norm_sqr);
        out.data_norm = data_norm;
        const float alpha = std::min(1.0f, 4.0f * data_norm /
            (factors.short_scale * std::sqrt(static_cast<float>(code_dim_))));
        if (!(alpha > 0.0f) || !std::isfinite(alpha)) return out;
        const float alpha2 = alpha * alpha;
        out.cross_scale = factors.short_scale;
        out.error_cross_scale = 2.0f * data_norm *
            std::sqrt(std::max(0.0f, (1.0f - alpha2) / alpha2)) /
            std::sqrt(static_cast<float>(code_dim_ - 1));
        out.valid = std::isfinite(out.error_cross_scale);
        return out;
    }

    // Full-precision-query 1-bit gate: masked-load dot of the FP32 query
    // residual over the MSB plane, closed with the RaBitQ 1-bit bound. Used
    // by the full codec.
    PaperPruneEstimate<float> computeFullGateEstimate(
        const QueryContext &query,
        const uint8_t *msb_code,
        const PaperPruneFactors<float> &factors,
        float epsilon0) const {
        PaperPruneEstimate<float> result;
        if (!factors.valid || !msb_code) return result;
        float selected_sum = 0.0f;
#if defined(__AVX512F__)
        __m512 sum = _mm512_setzero_ps();
        for (size_t i = 0; i < code_dim_; i += 16U) {
            uint16_t bits = 0;
            std::memcpy(&bits, msb_code + (i >> 3U), sizeof(bits));
            sum = _mm512_add_ps(sum, _mm512_maskz_loadu_ps(
                static_cast<__mmask16>(bits), query.rotated_residual.data() + i));
        }
        selected_sum = _mm512_reduce_add_ps(sum);
#else
        for (size_t i = 0; i < code_dim_; ++i)
            if ((msb_code[i >> 3U] >> (i & 7U)) & 1U)
                selected_sum += query.rotated_residual[i];
#endif
        const float short_ip = selected_sum - query.half_sum_residual;
        return finish_paper_prune_estimate_sidecar(short_ip, factors, query, epsilon0);
    }

    // Full-query gate for random graph candidates.  Process eight candidate
    // sidecars directly by id: every FP32 query block is loaded once and fed
    // to eight independent accumulators.  Unlike the older 32-candidate path,
    // this performs no heap allocation, staging copy, or scalar tail pass.
    void compute_full_paper_prune_estimate_batch_by_id(
        const void *prepared_query,
        const uint32_t *ids,
        size_t count,
        const uint8_t *msb_base,
        size_t msb_stride,
        const void *factors_base,
        size_t factors_stride,
        float epsilon0,
        PaperPruneEstimate<float> *out) const {
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        const char *factor_bytes = static_cast<const char *>(factors_base);
#if defined(__AVX512F__)
        const bool simd_ok = (code_dim_ % 16U) == 0U;
#else
        const bool simd_ok = false;
#endif
        if (!simd_ok) {
            for (size_t position = 0; position < count; ++position) {
                const size_t id = static_cast<size_t>(ids[position]);
                PaperPruneFactors<float> factors;
                std::memcpy(
                    &factors, factor_bytes + id * factors_stride,
                    sizeof(factors));
                out[position] = computeFullGateEstimate(
                    query, msb_base + id * msb_stride, factors, epsilon0);
            }
            return;
        }
#if defined(__AVX512F__)
        for (size_t group = 0; group < count; group += 8U) {
            const size_t lanes = std::min<size_t>(8U, count - group);
            const uint8_t *codes[8] = {};
            __m512 acc[8] = {
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
            };
            for (size_t lane = 0; lane < lanes; ++lane) {
                const size_t position = group + lane;
                const size_t id = static_cast<size_t>(ids[position]);
                codes[lane] = msb_base + id * msb_stride;
#if defined(__GNUC__) || defined(__clang__)
                for (size_t offset = 0; offset < msb_stride; offset += 64U) {
                    __builtin_prefetch(codes[lane] + offset, 0, 1);
                }
                __builtin_prefetch(
                    factor_bytes + id * factors_stride, 0, 1);
#endif
            }
            for (size_t dim = 0; dim < code_dim_; dim += 16U) {
                const __m512 qvec = _mm512_loadu_ps(
                    query.rotated_residual.data() + dim);
                for (size_t lane = 0; lane < lanes; ++lane) {
                    uint16_t bits = 0;
                    std::memcpy(
                        &bits, codes[lane] + (dim >> 3U), sizeof(bits));
                    acc[lane] = _mm512_add_ps(
                        acc[lane],
                        _mm512_maskz_mov_ps(
                            static_cast<__mmask16>(bits), qvec));
                }
            }
            for (size_t lane = 0; lane < lanes; ++lane) {
                const size_t position = group + lane;
                const size_t id = static_cast<size_t>(ids[position]);
                PaperPruneFactors<float> factors;
                std::memcpy(
                    &factors, factor_bytes + id * factors_stride,
                    sizeof(factors));
                const float selected_sum =
                    _mm512_reduce_add_ps(acc[lane]);
                out[position] = finish_paper_prune_estimate_sidecar(
                    selected_sum - query.half_sum_residual,
                    factors, query, epsilon0);
            }
        }
#endif
    }

    PaperPruneEstimate<float> compute_paper_prune_estimate_sidecar(
        const void *prepared_query,
        const uint8_t *msb_code,
        const PaperPruneFactors<float> &factors,
        float epsilon0) const override {
        PaperPruneEstimate<float> result;
        if (!factors.valid || !msb_code) return result;
        const PreparedQuery &prepared = *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        if (prepared.query_coarse_codec == QueryCoarseCodec::B1) {
            return compute_b1_paper_prune_estimate(query, msb_code, factors, epsilon0);
        }
        if (prepared.query_coarse_codec == QueryCoarseCodec::Int4 ||
            prepared.query_coarse_codec == QueryCoarseCodec::Int8) {
            // Query-codec comparisons keep the database side fixed at the
            // same 1-bit MSB sidecar. Only the query representation changes.
            return compute_paper_prune_estimate_int_msb_gate(
                prepared_query, msb_code, factors, epsilon0);
        }
        return computeFullGateEstimate(query, msb_code, factors, epsilon0);
    }

#if defined(__AVX512F__) && defined(__AVX512BW__)
    // 32-candidate lane-parallel paper estimate. Each candidate accumulates
    // over chunks in the exact same order as the single-candidate AVX-512
    // path (chunk ascending, 16 masked lanes added per chunk, one final
    // horizontal reduce), so short_ip / lower_bound are bit-identical.
    void compute_paper_prune_estimate_sidecar_batch(
        const void *prepared_query,
        const uint8_t *const *msb_codes,
        const PaperPruneFactors<float> *factors,
        float epsilon0,
        PaperPruneEstimate<float> *out) const {
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
        if (prepared.query_coarse_codec == QueryCoarseCodec::B1) {
            compute_b1_paper_prune_estimate_batch(
                prepared_query, msb_codes, factors, epsilon0, out);
            return;
        }
        if (prepared.query_coarse_codec == QueryCoarseCodec::Int4 ||
            prepared.query_coarse_codec == QueryCoarseCodec::Int8) {
            compute_paper_prune_estimate_int_msb_gate_batch(
                prepared_query, msb_codes, factors, epsilon0, out);
            return;
        }
        const QueryContext &query = prepared.centroid_queries[0];
        // 32 candidates = two 16-lane AVX-512 passes; only 16 accumulators are
        // live at a time so the zmm register file does not spill. Each
        // candidate keeps the same chunk-ascending accumulation order as the
        // single-candidate path, so results are bit-identical.
        for (size_t group = 0; group < 2; ++group) {
            __m512 acc[16];
            for (size_t c = 0; c < 16; ++c) {
                acc[c] = _mm512_setzero_ps();
            }
            for (size_t i = 0; i < code_dim_; i += 16U) {
                const __m512 qvec =
                    _mm512_loadu_ps(query.rotated_residual.data() + i);
                for (size_t c = 0; c < 16; ++c) {
                    const size_t cc = group * 16U + c;
                    uint16_t bits = 0;
                    std::memcpy(&bits, msb_codes[cc] + (i >> 3U), sizeof(bits));
                    const __m512 sel =
                        _mm512_maskz_mov_ps(static_cast<__mmask16>(bits), qvec);
                    acc[c] = _mm512_add_ps(acc[c], sel);
                }
            }
            for (size_t c = 0; c < 16; ++c) {
                const size_t cc = group * 16U + c;
                const float selected_sum = _mm512_reduce_add_ps(acc[c]);
                const float short_ip = selected_sum - query.half_sum_residual;
                out[cc] = finish_paper_prune_estimate_sidecar(
                    short_ip, factors[cc], query, epsilon0);
            }
        }
    }
#else
    void compute_paper_prune_estimate_sidecar_batch(
        const void *prepared_query,
        const uint8_t *const *msb_codes,
        const PaperPruneFactors<float> *factors,
        float epsilon0,
        PaperPruneEstimate<float> *out) const {
        for (size_t c = 0; c < kPaperBatch; ++c) {
            out[c] = compute_paper_prune_estimate_sidecar(
                prepared_query, msb_codes[c], factors[c], epsilon0);
        }
    }
#endif

    // B1 gate for random graph candidates. The old 32-lane path first copied
    // every sidecar into a temporary matrix and then used AVX-512 gathers plus
    // a nibble popcount LUT. On CPUs without AVX-512 VPOPCNTDQ, eight
    // interleaved scalar POPCNT streams are cheaper: the query word is loaded
    // once, candidate words are prefetched/directly loaded by id, and no
    // staging allocation or copy is required.
    void compute_b1_paper_prune_estimate_batch_by_id(
        const void *prepared_query,
        const uint32_t *ids,
        size_t count,
        const uint8_t *msb_base,
        size_t msb_stride,
        const void *factors_base,
        size_t factors_stride,
        float epsilon0,
        PaperPruneEstimate<float> *out) const {
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        const char *factor_bytes = static_cast<const char *>(factors_base);
        const size_t msb_bytes = (code_dim_ + 7U) / 8U;
        const size_t word_count = msb_bytes / sizeof(uint64_t);
        const size_t tail_offset = word_count * sizeof(uint64_t);
        const bool query_valid =
            query.b1_short_scale > 0.0f &&
            std::isfinite(query.b1_short_scale) &&
            query.query_norm > 0.0f;

        for (size_t group = 0; group < count; group += 8U) {
            const size_t lanes = std::min<size_t>(8U, count - group);
            const uint8_t *codes[8] = {};
            uint64_t mismatches[8] = {};
            for (size_t lane = 0; lane < lanes; ++lane) {
                const size_t position = group + lane;
                const size_t id = static_cast<size_t>(ids[position]);
                codes[lane] = msb_base + id * msb_stride;
#if defined(__GNUC__) || defined(__clang__)
                const size_t pf = position + 8U;
                if (pf < count) {
                    const size_t next_id = static_cast<size_t>(ids[pf]);
                    __builtin_prefetch(msb_base + next_id * msb_stride, 0, 3);
                    __builtin_prefetch(
                        factor_bytes + next_id * factors_stride, 0, 3);
                }
#endif
            }
            for (size_t word = 0; word < word_count; ++word) {
                uint64_t query_word = 0;
                std::memcpy(
                    &query_word,
                    query.b1_code.data() + word * sizeof(uint64_t),
                    sizeof(query_word));
                for (size_t lane = 0; lane < lanes; ++lane) {
                    uint64_t db_word = 0;
                    std::memcpy(
                        &db_word,
                        codes[lane] + word * sizeof(uint64_t),
                        sizeof(db_word));
                    mismatches[lane] += static_cast<uint64_t>(
                        __builtin_popcountll(query_word ^ db_word));
                }
            }
            for (size_t byte = tail_offset; byte < msb_bytes; ++byte) {
                const uint8_t query_byte = query.b1_code[byte];
                for (size_t lane = 0; lane < lanes; ++lane) {
                    mismatches[lane] += static_cast<uint64_t>(
                        __builtin_popcount(
                            static_cast<unsigned int>(
                                query_byte ^ codes[lane][byte])));
                }
            }
            for (size_t lane = 0; lane < lanes; ++lane) {
                const size_t position = group + lane;
                const size_t id = static_cast<size_t>(ids[position]);
                PaperPruneFactors<float> factors;
                std::memcpy(
                    &factors,
                    factor_bytes + id * factors_stride,
                    sizeof(factors));
                if (!query_valid || !factors.valid ||
                    factors.cross_scale <= 0.0f ||
                    !std::isfinite(factors.cross_scale) ||
                    factors.data_norm <= 0.0f) {
                    out[position] = PaperPruneEstimate<float>{};
                    continue;
                }
                const float signed_dot = static_cast<float>(code_dim_) -
                    2.0f * static_cast<float>(mismatches[lane]);
                const float short_ip_sym =
                    signed_dot * query.b1_short_scale / 8.0f;
                out[position] = finish_b1_paper_prune_estimate(
                    short_ip_sym, factors, query, epsilon0);
            }
        }
    }

    // Tail of the sidecar paper estimate: turns a computed short-code inner
    // product into the prune lower bound using the pre-extracted factors.
    // Shared by the single-call and the cross-record batched paths so their
    // float arithmetic stays identical.
    PaperPruneEstimate<float> finish_paper_prune_estimate_sidecar(
        float short_ip,
        const PaperPruneFactors<float> &factors,
        const QueryContext &query,
        float epsilon0) const {
        PaperPruneEstimate<float> result;
        if (!factors.valid) {
            return result;
        }
        const float cross_estimate = short_ip * factors.cross_scale;
        const float max_cross = 2.0f * factors.data_norm * query.query_norm;
        const float upper_cross = std::max(-max_cross, std::min(
            max_cross, cross_estimate + epsilon0 * factors.error_cross_scale * query.query_norm));
        result.lower_bound = factors.norm_sqr + query.query_norm_sqr - upper_cross;
        result.short_ip = short_ip;
        result.ip_hat = max_cross > 0.0f ? cross_estimate / max_cross : 0.0f;
        result.error_bound = max_cross > 0.0f
            ? epsilon0 * factors.error_cross_scale * query.query_norm / max_cross : 0.0f;
        result.valid = std::isfinite(result.lower_bound);
        return result;
    }

    // Symmetric 1-bit coarse bound: both query and database are reduced to
    // sign codes; the sign-code inner product is popcount-accelerated and
    // rescaled by each side's self-dot correction factor, mirroring the
    // DB-side short_scale machinery on the query side.
    PaperPruneEstimate<float> finish_b1_paper_prune_estimate(
        float short_ip_sym,
        const PaperPruneFactors<float> &factors,
        const QueryContext &query,
        float epsilon0) const {
        PaperPruneEstimate<float> result;
        if (!factors.valid) return result;
        const float cross_estimate = short_ip_sym * factors.cross_scale;
        const float max_cross = 2.0f * factors.data_norm * query.query_norm;
        // Symmetric 1-bit bound: the RaBitQ-form error bound applies to each
        // side (the x2 squared-L2 conversion is already inside both scale
        // terms), and the two sides are added so the query-side error of the
        // 1-bit sign code is covered. Dropping the query term makes the bound
        // unsafe (unit test: 9/64 bounds exceed the true distance).
        const float db_err = factors.error_cross_scale * query.query_norm;
        const float query_err = query.b1_error_scale * factors.data_norm;
        const float upper_cross = std::max(-max_cross, std::min(
            max_cross, cross_estimate + epsilon0 * (db_err + query_err)));
        result.lower_bound = factors.norm_sqr + query.query_norm_sqr - upper_cross;
        result.short_ip = short_ip_sym;
        result.alpha = query.b1_alpha;
        result.ip_hat = max_cross > 0.0f ? cross_estimate / max_cross : 0.0f;
        result.error_bound = max_cross > 0.0f
            ? epsilon0 * (db_err + query_err) / max_cross : 0.0f;
        result.valid = std::isfinite(result.lower_bound);
        return result;
    }

    PaperPruneEstimate<float> compute_b1_paper_prune_estimate(
        const QueryContext &query,
        const uint8_t *msb_code,
        const PaperPruneFactors<float> &factors,
        float epsilon0) const {
        PaperPruneEstimate<float> result;
        if (!factors.valid || !msb_code) return result;
        if (query.b1_short_scale <= 0.0f || !std::isfinite(query.b1_short_scale) ||
            query.query_norm <= 0.0f || factors.cross_scale <= 0.0f ||
            !std::isfinite(factors.cross_scale) || factors.data_norm <= 0.0f)
            return result;
        uint64_t mismatches = 0;
        const size_t bytes = (code_dim_ + 7U) / 8U;
        for (size_t j = 0; j < bytes; ++j) {
            mismatches += static_cast<uint64_t>(__builtin_popcountll(
                static_cast<uint64_t>(query.b1_code[j]) ^
                static_cast<uint64_t>(msb_code[j])));
        }
        const float signed_dot =
            static_cast<float>(code_dim_) - 2.0f * static_cast<float>(mismatches);
        // cross_estimate feeds the same finish as the full path, which works
        // in the "2 * inner product" convention, so the /16 here must be /8.
        const float short_ip_sym = signed_dot * query.b1_short_scale / 8.0f;
        return finish_b1_paper_prune_estimate(short_ip_sym, factors, query, epsilon0);
    }

    // True 32-lane popcount batch for the B1 symmetric bound: per 64-bit word,
    // gather the 32 candidates' MSB words, xor with the query word and popcount
    // each 64-bit lane via nibble-LUT + vpsadbw (no VPOPCNTDQ on this CPU).
    // Mismatch totals are accumulated in 32 u16 lanes (safe while code_dim_
    // fits in u16; otherwise fall back to per-candidate scalar).
    void compute_b1_paper_prune_estimate_batch(
        const void *prepared_query,
        const uint8_t *const *msb_codes,
        const PaperPruneFactors<float> *factors,
        float epsilon0,
        PaperPruneEstimate<float> *out) const {
        const PreparedQuery &prepared = *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        const size_t msb_bytes = (code_dim_ + 7U) / 8U;
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const bool simd_ok =
            code_dim_ <= 65535 && (msb_bytes % 8U == 0U) &&
            query.b1_short_scale > 0.0f && std::isfinite(query.b1_short_scale) &&
            query.query_norm > 0.0f;
#else
        const bool simd_ok = false;
#endif
        if (!simd_ok) {
            for (size_t c = 0; c < kPaperBatch; ++c) {
                out[c] = compute_b1_paper_prune_estimate(
                    query, msb_codes[c], factors[c], epsilon0);
            }
            return;
        }
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const __m512i nibble_mask = _mm512_set1_epi8(0x0F);
        const __m512i zero = _mm512_setzero_si512();
        // 16-entry byte-popcount LUT repeated in each 128-bit lane (vpshufb
        // selects within its own 128-bit lane).
        // vpshufb LUT per 128-bit lane; set_epi8 lists bytes high-to-low, so
        // each lane repeats [v15..v0] = [4,3,3,2,3,2,2,1,3,2,2,1,2,1,1,0].
        const __m512i popcnt_lut = _mm512_set_epi8(
            4, 3, 3, 2, 3, 2, 2, 1, 3, 2, 2, 1, 2, 1, 1, 0,
            4, 3, 3, 2, 3, 2, 2, 1, 3, 2, 2, 1, 2, 1, 1, 0,
            4, 3, 3, 2, 3, 2, 2, 1, 3, 2, 2, 1, 2, 1, 1, 0,
            4, 3, 3, 2, 3, 2, 2, 1, 3, 2, 2, 1, 2, 1, 1, 0);
        __m128i acc[4] = {
            _mm_setzero_si128(), _mm_setzero_si128(),
            _mm_setzero_si128(), _mm_setzero_si128(),
        };
        const size_t words = msb_bytes / 8U;
        for (size_t j = 0; j < words; ++j) {
            uint64_t query_word = 0;
            std::memcpy(&query_word, query.b1_code.data() + 8U * j, sizeof(query_word));
            const __m512i qb = _mm512_set1_epi64(static_cast<long long>(query_word));
            for (size_t g = 0; g < 4; ++g) {
                const long long stride = static_cast<long long>(msb_bytes);
                const __m512i idx = _mm512_set_epi64(
                    stride * static_cast<long long>(8 * g + 7),
                    stride * static_cast<long long>(8 * g + 6),
                    stride * static_cast<long long>(8 * g + 5),
                    stride * static_cast<long long>(8 * g + 4),
                    stride * static_cast<long long>(8 * g + 3),
                    stride * static_cast<long long>(8 * g + 2),
                    stride * static_cast<long long>(8 * g + 1),
                    stride * static_cast<long long>(8 * g + 0));
                const __m512i gathered = _mm512_i64gather_epi64(
                    idx, msb_codes[0] + 8U * j, 1);
                const __m512i x = _mm512_xor_si512(gathered, qb);
                const __m512i lo = _mm512_and_si512(x, nibble_mask);
                const __m512i hi = _mm512_and_si512(
                    _mm512_srli_epi16(x, 4), nibble_mask);
                __m512i pc = _mm512_shuffle_epi8(popcnt_lut, lo);
                pc = _mm512_add_epi8(pc, _mm512_shuffle_epi8(popcnt_lut, hi));
                const __m512i sums = _mm512_sad_epu8(pc, zero);
                const __m128i c16 = _mm512_cvtepi64_epi16(sums);
                acc[g] = _mm_add_epi16(acc[g], c16);
            }
        }
        for (size_t g = 0; g < 4; ++g) {
            uint16_t lanes[8];
            _mm_storeu_si128(reinterpret_cast<__m128i *>(lanes), acc[g]);
            for (size_t k = 0; k < 8; ++k) {
                const size_t c = 8U * g + k;
                const float signed_dot =
                    static_cast<float>(code_dim_) - 2.0f * static_cast<float>(lanes[k]);
                const float short_ip_sym = signed_dot * query.b1_short_scale / 8.0f;
                out[c] = finish_b1_paper_prune_estimate(
                    short_ip_sym, factors[c], query, epsilon0);
            }
        }
#endif
    }

    // Asymmetric fixed-1-bit-DB gate for INT4/INT8. Since the DB operand is a
    // mask, sum the selected signed query bytes directly instead of evaluating
    // 4/8 separate query bit planes.
    PaperPruneEstimate<float> compute_paper_prune_estimate_int_msb_gate(
        const void *prepared_query,
        const uint8_t *msb_code,
        const PaperPruneFactors<float> &factors,
        float epsilon0) const {
        PaperPruneEstimate<float> result;
        if (!factors.valid || !msb_code) return result;
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        if (query.query_norm <= 0.0f || factors.cross_scale <= 0.0f ||
            !std::isfinite(factors.cross_scale)) {
            return result;
        }
        const bool int8_mode =
            prepared.query_coarse_codec == QueryCoarseCodec::Int8;
        const int8_t *x =
            int8_mode ? query.int8_code.data() : query.int4_code.data();
        const int64_t code_sum =
            int8_mode ? query.int8_code_sum : query.int4_code_sum;
        const float scale = int8_mode ? query.int8_scale : query.int4_scale;
        const int64_t selected = maskedQuantizedQuerySum(msb_code, x);
        const float short_ip = 0.5f * scale * static_cast<float>(
            static_cast<int64_t>(2) * selected - code_sum);
        return finish_paper_prune_estimate_sidecar(
            short_ip, factors, query, epsilon0);
    }

    // Direct 32-candidate INT4/INT8 gate. For each 64-dimensional chunk, one
    // query-byte vector is shared by eight candidates; each candidate's 1-bit
    // DB word becomes an AVX-512 byte mask, followed by two horizontal integer
    // multiply-adds. This removes all 4/8 query-plane popcounts.
    void compute_paper_prune_estimate_int_msb_gate_batch(
        const void *prepared_query,
        const uint8_t *const *msb_codes,
        const PaperPruneFactors<float> *factors,
        float epsilon0,
        PaperPruneEstimate<float> *out) const {
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        const size_t msb_bytes = (code_dim_ + 7U) / 8U;
        const bool int8_mode =
            prepared.query_coarse_codec == QueryCoarseCodec::Int8;
        const int8_t *x =
            int8_mode ? query.int8_code.data() : query.int4_code.data();
#if defined(__AVX512F__) && defined(__AVX512BW__)
        bool contiguous = true;
        for (size_t c = 1; c < kPaperBatch; ++c) {
            if (msb_codes[c] != msb_codes[0] + c * msb_bytes) {
                contiguous = false;
                break;
            }
        }
        const bool simd_ok =
            contiguous && (code_dim_ % 64U == 0U) &&
            code_dim_ <= static_cast<size_t>(
                std::numeric_limits<int32_t>::max() / 128) &&
            query.query_norm > 0.0f;
#else
        const bool simd_ok = false;
#endif
        if (!simd_ok) {
            for (size_t c = 0; c < kPaperBatch; ++c) {
                out[c] = compute_paper_prune_estimate_int_msb_gate(
                    prepared_query, msb_codes[c], factors[c], epsilon0);
            }
            return;
        }
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const __m512i ones8 = _mm512_set1_epi8(1);
        const __m512i ones16 = _mm512_set1_epi16(1);
        int64_t selected[kPaperBatch] = {};
        for (size_t group = 0; group < 4U; ++group) {
            __m512i acc[8] = {
                _mm512_setzero_si512(), _mm512_setzero_si512(),
                _mm512_setzero_si512(), _mm512_setzero_si512(),
                _mm512_setzero_si512(), _mm512_setzero_si512(),
                _mm512_setzero_si512(), _mm512_setzero_si512(),
            };
            for (size_t offset = 0; offset < code_dim_; offset += 64U) {
                const __m512i qbytes = _mm512_loadu_si512(
                    reinterpret_cast<const void *>(x + offset));
                for (size_t lane = 0; lane < 8U; ++lane) {
                    const size_t candidate = 8U * group + lane;
                    uint64_t bits = 0;
                    std::memcpy(
                        &bits, msb_codes[candidate] + (offset >> 3U),
                        sizeof(bits));
                    const __m512i chosen = _mm512_maskz_mov_epi8(
                        static_cast<__mmask64>(bits), qbytes);
                    const __m512i pair_sums =
                        _mm512_maddubs_epi16(ones8, chosen);
                    acc[lane] = _mm512_add_epi32(
                        acc[lane], _mm512_madd_epi16(pair_sums, ones16));
                }
            }
            for (size_t lane = 0; lane < 8U; ++lane) {
                selected[8U * group + lane] = static_cast<int64_t>(
                    _mm512_reduce_add_epi32(acc[lane]));
            }
        }
        const int64_t code_sum =
            int8_mode ? query.int8_code_sum : query.int4_code_sum;
        const float scale = int8_mode ? query.int8_scale : query.int4_scale;
        for (size_t c = 0; c < kPaperBatch; ++c) {
            const float short_ip = 0.5f * scale * static_cast<float>(
                static_cast<int64_t>(2) * selected[c] - code_sum);
            out[c] = finish_paper_prune_estimate_sidecar(
                short_ip, factors[c], query, epsilon0);
        }
#endif
    }

    // INT4/INT8 fixed-1-bit gate for random graph candidates. Read sidecars
    // directly by id and share each 64-byte signed-query vector across eight
    // candidates. This preserves the integer gate arithmetic while removing
    // the old 32-record staging allocation and copy.
    void compute_int_paper_prune_estimate_batch_by_id(
        const void *prepared_query,
        const uint32_t *ids,
        size_t count,
        const uint8_t *msb_base,
        size_t msb_stride,
        const void *factors_base,
        size_t factors_stride,
        float epsilon0,
        PaperPruneEstimate<float> *out) const {
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        const bool int8_mode =
            prepared.query_coarse_codec == QueryCoarseCodec::Int8;
        const int8_t *x =
            int8_mode ? query.int8_code.data() : query.int4_code.data();
        const char *factor_bytes = static_cast<const char *>(factors_base);
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const bool simd_ok =
            (prepared.query_coarse_codec == QueryCoarseCodec::Int4 ||
             prepared.query_coarse_codec == QueryCoarseCodec::Int8) &&
            (code_dim_ % 64U == 0U) &&
            code_dim_ <= static_cast<size_t>(
                std::numeric_limits<int32_t>::max() / 128) &&
            query.query_norm > 0.0f;
#else
        const bool simd_ok = false;
#endif
        if (!simd_ok) {
            for (size_t position = 0; position < count; ++position) {
                const size_t id = static_cast<size_t>(ids[position]);
                PaperPruneFactors<float> factors;
                std::memcpy(
                    &factors, factor_bytes + id * factors_stride,
                    sizeof(factors));
                out[position] = compute_paper_prune_estimate_int_msb_gate(
                    prepared_query, msb_base + id * msb_stride,
                    factors, epsilon0);
            }
            return;
        }
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const __m512i ones8 = _mm512_set1_epi8(1);
        const __m512i ones16 = _mm512_set1_epi16(1);
        const int64_t code_sum =
            int8_mode ? query.int8_code_sum : query.int4_code_sum;
        const float scale = int8_mode ? query.int8_scale : query.int4_scale;
        for (size_t group = 0; group < count; group += 8U) {
            const size_t lanes = std::min<size_t>(8U, count - group);
            const uint8_t *codes[8] = {};
            __m512i acc[8] = {
                _mm512_setzero_si512(), _mm512_setzero_si512(),
                _mm512_setzero_si512(), _mm512_setzero_si512(),
                _mm512_setzero_si512(), _mm512_setzero_si512(),
                _mm512_setzero_si512(), _mm512_setzero_si512(),
            };
            for (size_t lane = 0; lane < lanes; ++lane) {
                const size_t position = group + lane;
                const size_t id = static_cast<size_t>(ids[position]);
                codes[lane] = msb_base + id * msb_stride;
#if defined(__GNUC__) || defined(__clang__)
                for (size_t offset = 0; offset < msb_stride; offset += 64U) {
                    __builtin_prefetch(codes[lane] + offset, 0, 1);
                }
                __builtin_prefetch(
                    factor_bytes + id * factors_stride, 0, 1);
#endif
            }
            for (size_t offset = 0; offset < code_dim_; offset += 64U) {
                const __m512i qbytes = _mm512_loadu_si512(
                    reinterpret_cast<const void *>(x + offset));
                for (size_t lane = 0; lane < lanes; ++lane) {
                    uint64_t bits = 0;
                    std::memcpy(
                        &bits, codes[lane] + (offset >> 3U),
                        sizeof(bits));
                    const __m512i chosen = _mm512_maskz_mov_epi8(
                        static_cast<__mmask64>(bits), qbytes);
                    const __m512i pair_sums =
                        _mm512_maddubs_epi16(ones8, chosen);
                    acc[lane] = _mm512_add_epi32(
                        acc[lane],
                        _mm512_madd_epi16(pair_sums, ones16));
                }
            }
            for (size_t lane = 0; lane < lanes; ++lane) {
                const size_t position = group + lane;
                const size_t id = static_cast<size_t>(ids[position]);
                PaperPruneFactors<float> factors;
                std::memcpy(
                    &factors, factor_bytes + id * factors_stride,
                    sizeof(factors));
                const int64_t selected = static_cast<int64_t>(
                    _mm512_reduce_add_epi32(acc[lane]));
                const float short_ip = 0.5f * scale * static_cast<float>(
                    static_cast<int64_t>(2) * selected - code_sum);
                out[position] = finish_paper_prune_estimate_sidecar(
                    short_ip, factors, query, epsilon0);
            }
        }
#endif
    }
    // INT4/INT8 asymmetric coarse bound: the query residual is quantized to a
    // centered global-scale integer code and substituted into the existing
    // long-distance estimator (signed_long_ip = <c - 7.5, q_hat>), i.e. the
    // estimation formula is unchanged and the quantized query is treated as
    // the original vector. Requires the full encoded record for the DB code.
    int64_t quantizedCodeDotDispatch(
        const uint8_t *code,
        const int8_t *x,
        uint8_t value_mask) const {
#if defined(__AVX2__)
        if (code_layout_ == RaBitQCodeLayout::SequentialNibble) {
            __m128i acc = _mm_setzero_si128();
            const __m128i low_mask = _mm_set1_epi8(
                static_cast<char>(value_mask));
            const __m128i shuf = _mm_setr_epi8(
                0, 2, 4, 6, 8, 10, 12, 14, 1, 3, 5, 7, 9, 11, 13, 15);
            const size_t pair_count = code_dim_ >> 1U;
            size_t pair = 0;
            for (; pair + 16U <= pair_count; pair += 16U) {
                const __m128i packed = _mm_loadu_si128(
                    reinterpret_cast<const __m128i *>(code + pair));
                const __m128i lo = _mm_and_si128(packed, low_mask);
                const __m128i hi = _mm_and_si128(
                    _mm_srli_epi16(packed, 4), low_mask);
                const __m128i xl = _mm_loadu_si128(
                    reinterpret_cast<const __m128i *>(x + (pair << 1U)));
                const __m128i xh = _mm_loadu_si128(
                    reinterpret_cast<const __m128i *>(x + (pair << 1U) + 16U));
                const __m128i dl = _mm_shuffle_epi8(xl, shuf);
                const __m128i dh = _mm_shuffle_epi8(xh, shuf);
                const __m128i x_even = _mm_unpacklo_epi64(dl, dh);
                const __m128i x_odd = _mm_unpackhi_epi64(dl, dh);
                __m128i d = _mm_maddubs_epi16(lo, x_even);
                d = _mm_add_epi16(d, _mm_maddubs_epi16(hi, x_odd));
                // Widen every chunk: 32 dims -> up to 16 * 15 * 127 products.
                __m128i lo32 = _mm_cvtepi16_epi32(d);
                __m128i hi32 = _mm_cvtepi16_epi32(_mm_srli_si128(d, 8));
                acc = _mm_add_epi32(acc, _mm_add_epi32(lo32, hi32));
            }
            int64_t total = 0;
            int32_t lanes[4];
            _mm_storeu_si128(reinterpret_cast<__m128i *>(lanes), acc);
            for (int32_t lane : lanes) total += lane;
            for (; pair < pair_count; ++pair) {
                total += static_cast<int64_t>(
                    codeValue(code, pair << 1U) & value_mask) *
                    static_cast<int64_t>(x[pair << 1U]);
                total += static_cast<int64_t>(
                    codeValue(code, (pair << 1U) + 1U) & value_mask) *
                    static_cast<int64_t>(x[(pair << 1U) + 1U]);
            }
            return total;
        }
#endif
        int64_t dot = 0;
        for (size_t i = 0; i < code_dim_; ++i) {
            dot += static_cast<int64_t>(
                primaryCodeValue(code, i) & value_mask) *
                static_cast<int64_t>(x[i]);
        }
        return dot;
    }

    int64_t quantizedLongCodeDotDispatch(
        const uint8_t *code,
        const int8_t *x) const {
        return quantizedCodeDotDispatch(code, x, 0x0FU);
    }

    int64_t quantizedRemainingCodeDotDispatch(
        const uint8_t *code,
        const int8_t *x) const {
        return quantizedCodeDotDispatch(code, x, kRemainingMax);
    }

    int64_t maskedQuantizedQuerySum(
        const uint8_t *msb_code,
        const int8_t *x) const {
#if defined(__AVX512F__) && defined(__AVX512BW__)
        const __m512i ones8 = _mm512_set1_epi8(1);
        const __m512i ones16 = _mm512_set1_epi16(1);
        int64_t total = 0;
        size_t offset = 0;
        for (; offset + 64U <= code_dim_; offset += 64U) {
            uint64_t bits = 0;
            std::memcpy(&bits, msb_code + (offset >> 3U), sizeof(bits));
            const __m512i qbytes = _mm512_loadu_si512(
                reinterpret_cast<const void *>(x + offset));
            const __m512i chosen = _mm512_maskz_mov_epi8(
                static_cast<__mmask64>(bits), qbytes);
            const __m512i pair_sums =
                _mm512_maddubs_epi16(ones8, chosen);
            total += static_cast<int64_t>(_mm512_reduce_add_epi32(
                _mm512_madd_epi16(pair_sums, ones16)));
        }
        for (; offset < code_dim_; ++offset) {
            if (((msb_code[offset >> 3U] >> (offset & 7U)) & 1U) != 0)
                total += static_cast<int64_t>(x[offset]);
        }
        return total;
#else
        int64_t total = 0;
        for (size_t i = 0; i < code_dim_; ++i) {
            if (((msb_code[i >> 3U] >> (i & 7U)) & 1U) != 0)
                total += static_cast<int64_t>(x[i]);
        }
        return total;
#endif
    }

    PaperPruneEstimate<float> compute_paper_prune_estimate_int_codec(
        const void *prepared_query,
        const void *data_point,
        const PaperPruneFactors<float> &factors,
        float epsilon0) const {
        PaperPruneEstimate<float> result;
        const PreparedQuery &prepared = *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        const bool int8_mode = prepared.query_coarse_codec == QueryCoarseCodec::Int8;
        const int8_t *x = int8_mode ? query.int8_code.data() : query.int4_code.data();
        const float scale = int8_mode ? query.int8_scale : query.int4_scale;
        const int64_t code_sum = int8_mode ? query.int8_code_sum : query.int4_code_sum;
        const EncodedHeader header = loadHeader(data_point);
        if (!(header.long_scale > 0.0f) || !std::isfinite(header.long_scale) ||
            !(scale > 0.0f) || !std::isfinite(scale) || query.query_norm <= 0.0f) {
            result.lower_bound = header.norm_sqr + query.query_norm_sqr;
            result.valid = std::isfinite(result.lower_bound);
            return result;
        }
        const uint8_t *code = codeBytes(data_point);
        const int64_t dot_cx = quantizedLongCodeDotDispatch(code, x);
        // ExRaBitQ 4-bit error bound for the DB side (tight for the long
        // estimator), replacing the looser 1-bit sidecar error_cross_scale.
        const float db_error_scale = shortFactors(data_point)->error_scale;
        return finish_int_paper_prune_estimate(
            query, header, db_error_scale, dot_cx, scale, code_sum, epsilon0);
    }

    PaperPruneEstimate<float> finish_int_paper_prune_estimate(
        const QueryContext &query,
        const EncodedHeader &header,
        float db_error_scale,
        int64_t dot_cx,
        float scale,
        int64_t code_sum,
        float epsilon0) const {
        PaperPruneEstimate<float> result;
        const double centered =
            static_cast<double>(dot_cx) - 7.5 * static_cast<double>(code_sum);
        const double signed_long_ip = static_cast<double>(scale) * centered;
        const double est_ip = static_cast<double>(header.long_scale) * signed_long_ip;
        const float est =
            header.norm_sqr + query.query_norm_sqr - static_cast<float>(est_ip);
        // DB-side ExRaBitQ 4-bit bound in the distance domain (its 2 * norm
        // factor is the x2 from d^2 = ||x||^2 + ||q||^2 - 2<x,q>). The query
        // side contributes no explicit term: with RaBitQ-style randomized
        // rounding (3.3.1) the query-quantization error is unbiased and
        // negligible (Thm 3.3), so it is absorbed into the DB-side bound.
        const double db_err = static_cast<double>(db_error_scale) *
            static_cast<double>(query.query_norm);
        result.lower_bound = est - static_cast<float>(epsilon0 * db_err);
        result.short_ip = static_cast<float>(signed_long_ip);
        result.ip_hat = 0.0f;
        result.error_bound = est - result.lower_bound;
        result.valid = std::isfinite(result.lower_bound);
        return result;
    }

    // INT4/INT8 32-candidate batch. A 32-lane transpose-based integer kernel
    // was measured slower than the per-candidate AVX2 dot (the 32 x (D/2) byte
    // transpose cost dominates), so the batch keeps one FFI per 32 candidates
    // and reuses the fast per-candidate integer dot. Results are bit-identical
    // to the single-candidate path by construction.
    void compute_paper_prune_estimate_int_codec_batch(
        const void *prepared_query,
        const uint8_t *records_base,
        size_t record_stride,
        const PaperPruneFactors<float> *factors,
        float epsilon0,
        PaperPruneEstimate<float> *out) const {
        for (size_t c = 0; c < kPaperBatch; ++c) {
            out[c] = compute_paper_prune_estimate_int_codec(
                prepared_query, records_base + c * record_stride, factors[c], epsilon0);
        }
    }

    static uint8_t primaryTopTwoBits(uint8_t value) {
        return static_cast<uint8_t>((value >> 2U) & 0x03U);
    }

    float compute_two_bit_lower_bound_scalar_for_test(
        const void *prepared_query,
        const void *data_point) const {
        const QueryContext &query = queryForEncoded(prepared_query, data_point);
        const EncodedHeader header = loadHeader(data_point);
        if (header.long_scale <= 0.0f || !std::isfinite(header.long_scale))
            return header.norm_sqr + query.query_norm_sqr;
        const float top_two_ip = topTwoBitsIpScalar(query, codeBytes(data_point));
        const float maximum_signed_ip = 4.0f * top_two_ip -
            7.5f * query.rotated_residual_sum + 3.0f * query.positive_sum_residual;
        return header.norm_sqr + query.query_norm_sqr -
            header.long_scale * maximum_signed_ip;
    }

    void query_distance_batch_k1(
        const void *prepared_query,
        const void *const *data_points,
        size_t count,
        float *distances,
        size_t prefetch_distance) override {
        if (centroid_count_ != 1 || code_layout_ != RaBitQCodeLayout::SequentialNibble) {
            SpaceInterface<float>::query_distance_batch_k1(
                prepared_query, data_points, count, distances, prefetch_distance);
            return;
        }
        PreparedQuery &prepared = *const_cast<PreparedQuery *>(
            static_cast<const PreparedQuery *>(prepared_query));
        if (!prepared.ready[0]) {
            buildCentroidQuery(prepared, 0);
        }
        const QueryContext &query = prepared.centroid_queries[0];
        for (size_t i = 0; i < count; ++i) {
            const size_t pf = i + prefetch_distance;
            if (pf < count) {
#if defined(__GNUC__) || defined(__clang__)
                __builtin_prefetch(data_points[pf], 0, 1);
#endif
            }
            distances[i] = queryDistanceLong(query, data_points[i]);
        }
    }

    bool compute_query_route_code(
        const void *prepared_query,
        const std::vector<uint32_t> &route_dims,
        uint32_t *route_code) const override {
        if (centroid_count_ != 1 || route_code == nullptr || route_dims.size() > 32) {
            return false;
        }
        PreparedQuery &prepared = *const_cast<PreparedQuery *>(
            static_cast<const PreparedQuery *>(prepared_query));
        if (!prepared.ready[0]) {
            buildCentroidQuery(prepared, 0);
        }
        const QueryContext &query = prepared.centroid_queries[0];
        uint32_t code = 0;
        for (size_t bit = 0; bit < route_dims.size(); ++bit) {
            const uint32_t dim = route_dims[bit];
            if (dim >= code_dim_) return false;
            if (query.rotated_residual[dim] >= 0.0f) code |= (uint32_t{1} << bit);
        }
        *route_code = code;
        return true;
    }

    bool compute_query_adaptive_route(
        const void *prepared_query,
        const float *projection_mean,
        const float *projection_components,
        size_t input_dim,
        size_t route_dim,
        std::vector<float> *projected_query) const override {
        if (!prepared_query || !projection_mean || !projection_components ||
            !projected_query || input_dim != dim_ || route_dim == 0) return false;
        const PreparedQuery &prepared = *static_cast<const PreparedQuery *>(prepared_query);
        projected_query->assign(route_dim, 0.0f);
        for (size_t r = 0; r < route_dim; ++r) {
            const float *row = projection_components + r * input_dim;
            float value = 0.0f;
            for (size_t c = 0; c < input_dim; ++c)
                value += (prepared.raw_query[c] - projection_mean[c]) * row[c];
            (*projected_query)[r] = value;
        }
        return true;
    }

    bool float32_query_distance(
        const void *prepared_query,
        const float *database_vector,
        float *distance) const override {
        if (!prepared_query || !database_vector || !distance) return false;
        const PreparedQuery &prepared = *static_cast<const PreparedQuery *>(prepared_query);
        double value = 0.0;
        for (size_t i = 0; i < dim_; ++i) {
            const double diff = static_cast<double>(prepared.raw_query[i]) - database_vector[i];
            value += diff * diff;
        }
        *distance = static_cast<float>(value);
        return true;
    }

    uint32_t compute_database_route_code(
        const float *raw_vector,
        const std::vector<uint32_t> &route_dims) const {
        if (centroid_count_ != 1 || raw_vector == nullptr || route_dims.size() > 32) {
            throw std::invalid_argument("route codes require K=1 and at most 32 dimensions");
        }
        thread_local std::vector<float> rotated;
        rotate(raw_vector, rotated);
        uint32_t code = 0;
        for (size_t bit = 0; bit < route_dims.size(); ++bit) {
            const uint32_t dim = route_dims[bit];
            if (dim >= code_dim_) throw std::out_of_range("route dimension is out of range");
            const float residual = rotated[dim] - rotated_centroids_[dim];
            if (residual >= 0.0f) code |= (uint32_t{1} << bit);
        }
        return code;
    }

    void compute_rotated_residual_k1(const float *raw_vector, std::vector<float> &out) const {
        if (centroid_count_ != 1) throw std::invalid_argument("route codes require K=1");
        rotate(raw_vector, out);
        for (size_t i = 0; i < code_dim_; ++i) out[i] -= rotated_centroids_[i];
    }

    float result_distance(const void *prepared_query, const void *data_point) override {
        return query_distance(prepared_query, data_point);
    }

    DistanceInterval compute_short_distance_interval(
        const void *prepared_query,
        const void *data_point) override {
        return computeShortDistanceIntervalTyped(
            queryForEncoded(prepared_query, data_point),
            data_point);
    }

    DistanceInterval compute_long_distance_interval(
        const void *prepared_query,
        const void *data_point) override {
        return computeLongDistanceIntervalTyped(
            queryForEncoded(prepared_query, data_point),
            data_point);
    }

    DistanceInterval compute_residual_distance_interval(
        const void *prepared_query,
        const void *data_point,
        float long_distance) override {
        return computeResidualDistanceIntervalTyped(
            queryForEncoded(prepared_query, data_point),
            data_point,
            long_distance);
    }

    DistanceInterval compute_residual_distance_interval_from_record(
        const void *prepared_query,
        const void *data_point,
        const void *record,
        float long_distance) {
        const QueryContext &query = queryForEncoded(prepared_query, data_point);
        return computeResidualDistanceIntervalFromRecord(query, record, long_distance);
    }

    // Residual rerank for random graph candidates.  Interleave eight records
    // so the query's even/odd AVX2 vectors are loaded once per 16 dimensions,
    // while each candidate keeps its own accumulator.  This also folds the
    // error-bound scale pass into the dot-product pass and avoids reading the
    // unused ResidualCodeFactors stored at the start of every record.
    void compute_residual_distance_intervals_batch_by_id(
        const void *prepared_query,
        const uint32_t *ids,
        size_t count,
        const void *encoded_base,
        size_t encoded_stride,
        const void *residual_base,
        size_t residual_stride,
        const float *long_distances,
        DistanceInterval *out) const {
        const char *encoded_bytes = static_cast<const char *>(encoded_base);
        const char *residual_bytes = static_cast<const char *>(residual_base);
#if defined(__AVX2__)
        const bool simd_ok =
            centroid_count_ == 1U && residual_bits_ == 4U &&
            residual_block_size_ == kResidualBlockSize &&
            (code_dim_ % kResidualBlockSize) == 0U;
#else
        const bool simd_ok = false;
#endif
        if (!simd_ok) {
            for (size_t position = 0; position < count; ++position) {
                const size_t id = static_cast<size_t>(ids[position]);
                const void *encoded = encoded_bytes + id * encoded_stride;
                const void *record = residual_bytes + id * residual_stride;
                out[position] = computeResidualDistanceIntervalFromRecord(
                    queryForEncoded(prepared_query, encoded),
                    record,
                    long_distances[position]);
            }
            return;
        }
#if defined(__AVX2__)
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        const __m128i low_mask = _mm_set1_epi8(0x0F);
        const __m256i seven = _mm256_set1_epi32(7);
        const __m256i sixteen = _mm256_set1_epi32(16);
        alignas(32) float product_lanes[8];
        for (size_t group = 0; group < count; group += 8U) {
            const size_t lanes = std::min<size_t>(8U, count - group);
            const void *scale_storage[8] = {};
            const uint8_t *codes[8] = {};
            float ips[8] = {};
            double error_norm_sqr[8] = {};
            for (size_t lane = 0; lane < lanes; ++lane) {
                const size_t id = static_cast<size_t>(ids[group + lane]);
                const void *record = residual_bytes + id * residual_stride;
                scale_storage[lane] = residualScalesFromRecord(record);
                codes[lane] = residualCodeBytesFromRecord(record);
#if defined(__GNUC__) || defined(__clang__)
                __builtin_prefetch(scale_storage[lane], 0, 1);
                for (size_t offset = 0; offset < residual_code_bytes_; offset += 64U) {
                    __builtin_prefetch(codes[lane] + offset, 0, 1);
                }
#endif
            }
            for (size_t block = 0; block < residual_block_count_; ++block) {
                const size_t pair = block * (kResidualBlockSize >> 1U);
                const __m256 query_even = _mm256_loadu_ps(
                    query.rotated_residual_even.data() + pair);
                const __m256 query_odd = _mm256_loadu_ps(
                    query.rotated_residual_odd.data() + pair);
                for (size_t lane = 0; lane < lanes; ++lane) {
                    const float scale = loadResidualScale(
                        scale_storage[lane], block);
                    if (scale == 0.0f || !std::isfinite(scale)) {
                        continue;
                    }
                    const __m128i packed = _mm_loadl_epi64(
                        reinterpret_cast<const __m128i *>(codes[lane] + pair));
                    const __m128i lo8 = _mm_and_si128(packed, low_mask);
                    const __m128i hi8 = _mm_and_si128(
                        _mm_srli_epi16(packed, 4), low_mask);
                    __m256i lo32 = _mm256_cvtepu8_epi32(lo8);
                    __m256i hi32 = _mm256_cvtepu8_epi32(hi8);
                    lo32 = _mm256_sub_epi32(
                        lo32,
                        _mm256_and_si256(
                            _mm256_cmpgt_epi32(lo32, seven), sixteen));
                    hi32 = _mm256_sub_epi32(
                        hi32,
                        _mm256_and_si256(
                            _mm256_cmpgt_epi32(hi32, seven), sixteen));
                    __m256 products = _mm256_mul_ps(
                        _mm256_cvtepi32_ps(lo32), query_even);
                    products = _mm256_add_ps(
                        products,
                        _mm256_mul_ps(
                            _mm256_cvtepi32_ps(hi32), query_odd));
                    _mm256_store_ps(product_lanes, products);
                    float block_ip = 0.0f;
                    for (float value : product_lanes) {
                        block_ip += value;
                    }
                    ips[lane] += scale * block_ip;
                    const double per_dim_error =
                        residual_coordinate_error_bound(scale, residual_bits_);
                    error_norm_sqr[lane] +=
                        static_cast<double>(kResidualBlockSize) *
                        per_dim_error * per_dim_error;
                }
            }
            for (size_t lane = 0; lane < lanes; ++lane) {
                const size_t position = group + lane;
                const float distance =
                    long_distances[position] - 2.0f * ips[lane];
                const float err = static_cast<float>(
                    2.0 * static_cast<double>(query.query_norm) *
                    std::sqrt(error_norm_sqr[lane]));
                out[position] = DistanceInterval{
                    distance, distance - err, distance + err};
            }
        }
#endif
    }

    // Final INT4/INT8 rerank: restore the complete FP32-query 4-bit base
    // distance and apply the residual correction in one eight-candidate
    // kernel.  A 32-dimensional query tile is loaded once and shared by the
    // base-code and the two 16-dimensional residual blocks.  Accumulation
    // order intentionally matches query_distance_batch_by_id followed by
    // compute_residual_distance_intervals_batch_by_id, keeping the fused and
    // legacy results bit-identical.
    void compute_full_residual_distance_intervals_batch_by_id(
        const void *prepared_query,
        const uint32_t *ids,
        size_t count,
        const void *encoded_base,
        size_t encoded_stride,
        const void *residual_base,
        size_t residual_stride,
        DistanceInterval *out) const {
        const char *encoded_bytes = static_cast<const char *>(encoded_base);
        const char *residual_bytes = static_cast<const char *>(residual_base);
#if defined(__AVX512F__) && defined(__AVX512BW__) && defined(__AVX512DQ__)
        const bool simd_ok =
            centroid_count_ == 1U &&
            code_layout_ == RaBitQCodeLayout::SequentialNibble &&
            residual_bits_ == 4U &&
            residual_block_size_ == kResidualBlockSize &&
            (code_dim_ % 32U) == 0U;
#else
        const bool simd_ok = false;
#endif
        if (!simd_ok) {
            for (size_t position = 0; position < count; ++position) {
                const size_t id = static_cast<size_t>(ids[position]);
                const void *encoded = encoded_bytes + id * encoded_stride;
                const void *record = residual_bytes + id * residual_stride;
                const QueryContext &query =
                    queryForEncoded(prepared_query, encoded);
                const float long_distance = queryDistanceLong(query, encoded);
                out[position] = computeResidualDistanceIntervalFromRecord(
                    query, record, long_distance);
            }
            return;
        }
#if defined(__AVX512F__) && defined(__AVX512BW__) && defined(__AVX512DQ__)
        const PreparedQuery &prepared =
            *static_cast<const PreparedQuery *>(prepared_query);
        const QueryContext &query = prepared.centroid_queries[0];
        const __m128i base_remaining_mask = _mm_set1_epi8(
            static_cast<char>(kRemainingMax));
        const __m128i base_nibble_mask = _mm_set1_epi8(0x0F);
        const __m128i selected_threshold = _mm_set1_epi8(
            static_cast<char>(kRemainingMax));
        const __m128i residual_low_mask = _mm_set1_epi8(0x0F);
        const __m256i seven = _mm256_set1_epi32(7);
        const __m256i sixteen = _mm256_set1_epi32(16);
        const size_t pair_count = code_dim_ >> 1U;
        alignas(32) float product_lanes[8];

        // Four lanes keep the eight long-code accumulators plus residual
        // temporaries resident in registers. Eight fused lanes caused spills
        // even though each standalone base/residual kernel favored eight.
        for (size_t group = 0; group < count; group += 4U) {
            const size_t lanes = std::min<size_t>(4U, count - group);
            const void *encoded_records[8] = {};
            const uint8_t *base_codes[8] = {};
            const void *residual_scales[8] = {};
            const uint8_t *residual_codes[8] = {};
            __m512 short_acc[8] = {
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
            };
            __m512 remaining_acc[8] = {
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
                _mm512_setzero_ps(), _mm512_setzero_ps(),
            };
            float residual_ips[8] = {};
            double error_norm_sqr[8] = {};

            for (size_t lane = 0; lane < lanes; ++lane) {
                const size_t id = static_cast<size_t>(ids[group + lane]);
                encoded_records[lane] =
                    encoded_bytes + id * encoded_stride;
                base_codes[lane] = codeBytes(encoded_records[lane]);
                const void *residual_record =
                    residual_bytes + id * residual_stride;
                residual_scales[lane] =
                    residualScalesFromRecord(residual_record);
                residual_codes[lane] =
                    residualCodeBytesFromRecord(residual_record);
#if defined(__GNUC__) || defined(__clang__)
                __builtin_prefetch(encoded_records[lane], 0, 1);
                __builtin_prefetch(residual_scales[lane], 0, 1);
                for (size_t offset = 0; offset < residual_code_bytes_;
                     offset += 64U) {
                    __builtin_prefetch(
                        residual_codes[lane] + offset, 0, 1);
                }
#endif
            }

            for (size_t pair = 0; pair < pair_count; pair += 16U) {
                const __m512 even_q = _mm512_loadu_ps(
                    query.rotated_residual_even.data() + pair);
                const __m512 odd_q = _mm512_loadu_ps(
                    query.rotated_residual_odd.data() + pair);
                const __m256 even_q_half[2] = {
                    _mm512_castps512_ps256(even_q),
                    _mm512_extractf32x8_ps(even_q, 1),
                };
                const __m256 odd_q_half[2] = {
                    _mm512_castps512_ps256(odd_q),
                    _mm512_extractf32x8_ps(odd_q, 1),
                };

                for (size_t lane = 0; lane < lanes; ++lane) {
                    const __m128i packed = _mm_loadu_si128(
                        reinterpret_cast<const __m128i *>(
                            base_codes[lane] + pair));
                    const __m128i lo = _mm_and_si128(
                        packed, base_remaining_mask);
                    const __m128i hi = _mm_and_si128(
                        _mm_srli_epi16(packed, 4), base_remaining_mask);
                    const __m128i raw_lo = _mm_and_si128(
                        packed, base_nibble_mask);
                    const __m128i raw_hi = _mm_and_si128(
                        _mm_srli_epi16(packed, 4), base_nibble_mask);
                    const __mmask16 lo_selected = static_cast<__mmask16>(
                        _mm_movemask_epi8(
                            _mm_cmpgt_epi8(raw_lo, selected_threshold)));
                    const __mmask16 hi_selected = static_cast<__mmask16>(
                        _mm_movemask_epi8(
                            _mm_cmpgt_epi8(raw_hi, selected_threshold)));
                    remaining_acc[lane] = _mm512_add_ps(
                        remaining_acc[lane],
                        _mm512_mul_ps(
                            _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(lo)),
                            even_q));
                    remaining_acc[lane] = _mm512_add_ps(
                        remaining_acc[lane],
                        _mm512_mul_ps(
                            _mm512_cvtepi32_ps(_mm512_cvtepu8_epi32(hi)),
                            odd_q));
                    short_acc[lane] = _mm512_mask_add_ps(
                        short_acc[lane], lo_selected,
                        short_acc[lane], even_q);
                    short_acc[lane] = _mm512_mask_add_ps(
                        short_acc[lane], hi_selected,
                        short_acc[lane], odd_q);

                    for (size_t half = 0; half < 2U; ++half) {
                        const size_t block = (pair >> 3U) + half;
                        const float scale = loadResidualScale(
                            residual_scales[lane], block);
                        if (scale == 0.0f || !std::isfinite(scale)) {
                            continue;
                        }
                        const __m128i residual_packed = _mm_loadl_epi64(
                            reinterpret_cast<const __m128i *>(
                                residual_codes[lane] + pair + half * 8U));
                        const __m128i residual_lo8 = _mm_and_si128(
                            residual_packed, residual_low_mask);
                        const __m128i residual_hi8 = _mm_and_si128(
                            _mm_srli_epi16(residual_packed, 4),
                            residual_low_mask);
                        __m256i residual_lo32 =
                            _mm256_cvtepu8_epi32(residual_lo8);
                        __m256i residual_hi32 =
                            _mm256_cvtepu8_epi32(residual_hi8);
                        residual_lo32 = _mm256_sub_epi32(
                            residual_lo32,
                            _mm256_and_si256(
                                _mm256_cmpgt_epi32(residual_lo32, seven),
                                sixteen));
                        residual_hi32 = _mm256_sub_epi32(
                            residual_hi32,
                            _mm256_and_si256(
                                _mm256_cmpgt_epi32(residual_hi32, seven),
                                sixteen));
                        __m256 products = _mm256_mul_ps(
                            _mm256_cvtepi32_ps(residual_lo32),
                            even_q_half[half]);
                        products = _mm256_add_ps(
                            products,
                            _mm256_mul_ps(
                                _mm256_cvtepi32_ps(residual_hi32),
                                odd_q_half[half]));
                        _mm256_store_ps(product_lanes, products);
                        float block_ip = 0.0f;
                        for (float value : product_lanes) {
                            block_ip += value;
                        }
                        residual_ips[lane] += scale * block_ip;
                        const double per_dim_error =
                            residual_coordinate_error_bound(
                                scale, residual_bits_);
                        error_norm_sqr[lane] +=
                            static_cast<double>(kResidualBlockSize) *
                            per_dim_error * per_dim_error;
                    }
                }
            }

            for (size_t lane = 0; lane < lanes; ++lane) {
                const EncodedHeader header =
                    loadHeader(encoded_records[lane]);
                float long_distance;
                if (!(header.long_scale > 0.0f) ||
                    !std::isfinite(header.long_scale)) {
                    long_distance = header.norm_sqr + query.query_norm_sqr;
                } else {
                    long_distance = queryDistanceLongWithIps(
                        query, header,
                        _mm512_reduce_add_ps(short_acc[lane]) -
                            query.half_sum_residual,
                        _mm512_reduce_add_ps(remaining_acc[lane]));
                }
                const float distance =
                    long_distance - 2.0f * residual_ips[lane];
                const float err = static_cast<float>(
                    2.0 * static_cast<double>(query.query_norm) *
                    std::sqrt(error_norm_sqr[lane]));
                out[group + lane] = DistanceInterval{
                    distance, distance - err, distance + err};
            }
        }
#endif
    }

    void batch_compute_residual_distance_intervals_by_id(
        const void *prepared_query,
        const size_t *internal_ids,
        const void *const *data_points,
        const float *long_distances,
        size_t count,
        DistanceInterval *intervals) override {
        for (size_t i = 0; i < count; ++i) {
            if (data_points[i] == nullptr) {
                throw std::runtime_error("RaBitQ residual batch received null data point");
            }
            if (external_residual_storage_ && internal_ids[i] >= residual_record_count_) {
                throw std::runtime_error("RaBitQ residual batch internal_id is out of range");
            }
            const QueryContext &query = queryForEncoded(prepared_query, data_points[i]);
            if (external_residual_storage_) {
                intervals[i] = computeResidualDistanceIntervalFromRecord(
                    query,
                    externalResidualRecord(internal_ids[i]),
                    long_distances[i]);
            } else {
                intervals[i] = computeResidualDistanceIntervalTyped(
                    query,
                    data_points[i],
                    long_distances[i]);
            }
        }
    }

    void batch_compute_nested4x4_distances_by_internal_id(
        const void *prepared_query,
        const size_t *internal_ids,
        const void *const *data_points,
        const float *high4_distances,
        size_t count,
        float *distances) override {
        static const bool use_residual4_rerank = []() {
            const char *value = std::getenv("RABITQ_NESTED_ENABLE_RESIDUAL4_RERANK");
            return value != nullptr && std::strcmp(value, "1") == 0;
        }();
        if (!nested4x4_layout_ || !use_residual4_rerank) {
            for (size_t i = 0; i < count; ++i) {
                distances[i] = high4_distances[i];
            }
            return;
        }
        for (size_t i = 0; i < count; ++i) {
            if (data_points[i] == nullptr) {
                throw std::runtime_error("RaBitQ nested residual batch received null data point");
            }
            if (external_residual_storage_ && internal_ids[i] >= residual_record_count_) {
                throw std::runtime_error("RaBitQ nested residual batch internal_id is out of range");
            }
            const QueryContext &query = queryForEncoded(prepared_query, data_points[i]);
            const uint8_t *residual_code = external_residual_storage_
                ? reinterpret_cast<const uint8_t *>(externalResidualRecord(internal_ids[i]))
                : residualCodeBytes(data_points[i]);
            distances[i] = nested4x4ResidualDistance(
                query,
                data_points[i],
                residual_code,
                high4_distances[i]);
        }
    }

    void encodeVectorFullWithCentroid(const float *raw_vector, uint8_t centroid_id, void *encoded_out) const {
        if (nested4x4_layout_) {
            encodeVectorNested4x4Full(raw_vector, encoded_out);
            return;
        }
        if (static_cast<size_t>(centroid_id) >= centroid_count_) {
            throw std::invalid_argument("RaBitQ encode centroid_id is out of range");
        }
        thread_local std::vector<float> residual;
        residual.assign(dim_, 0.0f);
        float residual_norm_sqr = 0.0f;
        const float *centroid = centroids_.data() + static_cast<size_t>(centroid_id) * dim_;
        for (size_t i = 0; i < dim_; ++i) {
            residual[i] = raw_vector[i] - centroid[i];
            residual_norm_sqr += residual[i] * residual[i];
        }

        const float residual_norm = std::sqrt(residual_norm_sqr);
        if (residual_norm > 0.0f) {
            const float inv_norm = 1.0f / residual_norm;
            for (float &value : residual) {
                value *= inv_norm;
            }
        }

        thread_local std::vector<float> rotated_unit;
        rotate(residual.data(), rotated_unit);

        EncodedHeader header{0.0f, 0.0f};
        uint8_t *code = codeBytesFull(encoded_out);
        uint8_t *residual_code = residualCodeBytes(encoded_out);
        void *residual_scale_storage = residualScaleBytes(encoded_out);
        ShortCodeFactors *factors = shortFactors(encoded_out);
        ResidualCodeFactors *residual_factors = residualFactors(encoded_out);
        *centroidIdFull(encoded_out) = centroid_id;
        header.norm_sqr = residual_norm * residual_norm;
        std::memset(code, 0, compact_code_bytes_);
        std::memset(residual_code, 0, residual_code_bytes_);
        std::memset(residual_scale_storage, 0, residual_scale_bytes_);
        *factors = ShortCodeFactors{0.0f, 0.0f};
        *residual_factors = ResidualCodeFactors{0.0f, 0.0f};

        thread_local std::vector<float> abs_unit;
        thread_local std::vector<uint8_t> abs_code;
        abs_unit.assign(code_dim_, 0.0f);
        abs_code.assign(code_dim_, 0);
        for (size_t i = 0; i < code_dim_; ++i) {
            abs_unit[i] = std::abs(rotated_unit[i]);
        }
        float ip_norm = 1.0f;
        fastQuantizeAbs(abs_unit.data(), abs_code.data(), ip_norm);
        header.long_scale = 2.0f * residual_norm * ip_norm;

        double o_obar = 0.0;
        double half_l1_norm = 0.0;
        const double inv_sqrt_d = 1.0 / std::sqrt(static_cast<double>(code_dim_));
        for (size_t i = 0; i < code_dim_; ++i) {
            const bool positive = rotated_unit[i] > 0.0f;
            const uint8_t magnitude = abs_code[i];
            const uint8_t packed_value = positive
                                             ? static_cast<uint8_t>(kMsbWeight + magnitude)
                                             : static_cast<uint8_t>(kRemainingMax - magnitude);
            setCodeValue(code, i, packed_value);

            const double sign = positive ? 1.0 : -1.0;
            o_obar += static_cast<double>(rotated_unit[i]) * sign * inv_sqrt_d;
            half_l1_norm += 0.5 * std::abs(static_cast<double>(rotated_unit[i]));
        }
        if (residual_norm > 0.0f && half_l1_norm > 1e-12) {
            factors->short_scale = static_cast<float>(2.0 * residual_norm / half_l1_norm);
            if (!std::isfinite(o_obar)) {
                o_obar = 0.8;
            }
            o_obar = std::min(0.999999, std::max(1e-6, o_obar));
            const double o2 = o_obar * o_obar;
            const double fac_err_bound =
                std::ldexp(kExtendedRaBitQErrorConstant, -static_cast<int>(kTotalBits)) /
                std::sqrt(static_cast<double>(code_dim_));
            factors->error_scale = static_cast<float>(
                std::sqrt(std::max(0.0, (1.0 - o2) / o2)) * fac_err_bound * 2.0 * residual_norm);
        }

        thread_local std::vector<float> quantization_error;
        quantization_error.assign(code_dim_, 0.0f);
        for (size_t i = 0; i < code_dim_; ++i) {
            const double true_value = static_cast<double>(residual_norm) *
                                      static_cast<double>(rotated_unit[i]);
            const double x4_value = 0.5 * static_cast<double>(header.long_scale) *
                                    (static_cast<double>(codeValue(code, i)) -
                                     static_cast<double>(kUnsignedOffset));
            const double error = true_value - x4_value;
            quantization_error[i] = static_cast<float>(error);
        }
        double decoded_residual_norm_sqr = 0.0;
        double x4_residual_ip = 0.0;
        thread_local std::vector<int32_t> residual_codes;
        residual_codes.assign(code_dim_, residual_bits_ == 1 ? -1 : 0);
        thread_local std::vector<float> residual_scale_values;
        residual_scale_values.assign(residual_block_count_, 0.0f);
        const int32_t residual_qmax = residual_quantized_max(residual_bits_);
        const int32_t residual_qmin = -residual_qmax;
        for (size_t block = 0; block < residual_block_count_; ++block) {
            const size_t begin = block * residual_block_size_;
            const size_t end = std::min(code_dim_, begin + residual_block_size_);
            const float residual_scale = residual_config_.mse_optimal_scale
                ? residualMseOptimalScale(
                    quantization_error,
                    begin,
                    end,
                    residual_bits_,
                    residual_qmin,
                    residual_qmax)
                : residualMaxAbsScale(
                    quantization_error,
                    begin,
                    end,
                    residual_bits_,
                    residual_qmax);
            storeResidualScale(residual_scale_storage, block, residual_scale);
            const float stored_residual_scale = loadResidualScale(residual_scale_storage, block);
            residual_scale_values[block] = stored_residual_scale;
            if (stored_residual_scale == 0.0f || !std::isfinite(stored_residual_scale)) {
                continue;
            }
            for (size_t i = begin; i < end; ++i) {
                const int32_t clipped = residual_bits_ == 1
                    ? (quantization_error[i] >= 0.0f ? 1 : -1)
                    : std::max(
                        residual_qmin,
                        std::min(
                            residual_qmax,
                            static_cast<int32_t>(std::round(
                                static_cast<double>(quantization_error[i]) /
                                static_cast<double>(stored_residual_scale)))));
                residual_codes[i] = clipped;
                const double decoded_residual =
                    static_cast<double>(stored_residual_scale) * static_cast<double>(clipped);
                const double x4_value = 0.5 * static_cast<double>(header.long_scale) *
                                        (static_cast<double>(codeValue(code, i)) -
                                         static_cast<double>(kUnsignedOffset));
                decoded_residual_norm_sqr += decoded_residual * decoded_residual;
                x4_residual_ip += x4_value * decoded_residual;
            }
        }
        pack_residual_codes(residual_codes.data(), code_dim_, residual_bits_, residual_code);
        residual_factors->residual_norm_sqr = static_cast<float>(decoded_residual_norm_sqr);
        residual_factors->long_residual_inner_product = static_cast<float>(x4_residual_ip);
        std::memcpy(encoded_out, &header, sizeof(header));
        if (code_layout_ == RaBitQCodeLayout::Turbo128) {
            thread_local std::vector<uint8_t> turbo_code;
            turbo_code.assign(compact_code_bytes_, 0);
            convertSequentialToTurbo(code, turbo_code.data(), code_dim_);
            std::memcpy(code, turbo_code.data(), compact_code_bytes_);
        }
    }

    void encodeVectorFull(const float *raw_vector, void *encoded_out) const {
        encodeVectorFullWithCentroid(raw_vector, assignCentroid(raw_vector), encoded_out);
    }

    void copyCompactPayloadFromFull(const void *full_encoded, void *compact_encoded) const {
        if (nested4x4_layout_) {
            std::memcpy(compact_encoded, full_encoded, data_size_);
            return;
        }
        std::memcpy(compact_encoded, full_encoded, sizeof(EncodedHeader) + sizeof(ShortCodeFactors));
        std::memcpy(
            static_cast<char *>(compact_encoded) + sizeof(EncodedHeader) + sizeof(ShortCodeFactors),
            codeBytesFull(full_encoded),
            compact_code_bytes_);
        *centroidId(compact_encoded) = *centroidIdFull(full_encoded);
    }

    void copyResidualRecordFromFull(const void *full_encoded, void *record_out) const {
        if (nested4x4_layout_) {
            std::memcpy(record_out, residualCodeBytes(full_encoded), nested4x4Low4RecordBytes());
            return;
        }
        std::memcpy(record_out, residualFactors(full_encoded), residual_factor_bytes_);
        std::memcpy(static_cast<char *>(record_out) + residual_factor_bytes_, residualScales(full_encoded), residual_scale_bytes_);
        std::memcpy(
            static_cast<char *>(record_out) + residual_factor_bytes_ + residual_scale_bytes_,
            residualCodeBytes(full_encoded),
            residual_code_bytes_);
    }

    void encodeVector(const float *raw_vector, void *encoded_out) const {
        if (!external_residual_storage_) {
            encodeVectorFull(raw_vector, encoded_out);
            return;
        }
        thread_local std::vector<char> full_encoded;
        full_encoded.assign(full_data_size_, 0);
        encodeVectorFull(raw_vector, full_encoded.data());
        copyCompactPayloadFromFull(full_encoded.data(), encoded_out);
    }

    std::vector<char> encodeVector(const float *raw_vector) const {
        std::vector<char> encoded(data_size_, 0);
        encodeVector(raw_vector, encoded.data());
        return encoded;
    }
};

}  // namespace hnswlib
