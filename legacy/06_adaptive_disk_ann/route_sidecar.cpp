#include "route_sidecar.hpp"

#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace adaptive_route {
namespace {

std::size_t file_size(int fd) {
    struct stat st {};
    if (::fstat(fd, &st) != 0) throw std::runtime_error("fstat failed");
    return static_cast<std::size_t>(st.st_size);
}

float half_to_float(std::uint16_t value) {
    const std::uint32_t sign = static_cast<std::uint32_t>(value & 0x8000U) << 16U;
    const std::uint32_t exponent = (value >> 10U) & 0x1FU;
    const std::uint32_t mantissa = value & 0x03FFU;
    std::uint32_t bits = 0;
    if (exponent == 0) {
        if (mantissa == 0) bits = sign;
        else {
            std::uint32_t m = mantissa;
            int e = -1;
            do { m <<= 1U; --e; } while ((m & 0x0400U) == 0);
            bits = sign | static_cast<std::uint32_t>(e + 127) << 23U | ((m & 0x03FFU) << 13U);
        }
    } else if (exponent == 0x1FU) {
        bits = sign | 0x7F800000U | (mantissa << 13U);
    } else {
        bits = sign | ((exponent + 112U) << 23U) | (mantissa << 13U);
    }
    float result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}

}  // namespace

RouteSidecar::RouteSidecar(const std::filesystem::path &code_path,
                           const std::filesystem::path &scale_path,
                           std::size_t rows, std::size_t route_dim)
    : rows_(rows), route_dim_(route_dim), code_bytes_per_row_((route_dim + 7U) / 8U) {
    if (rows_ == 0 || route_dim_ == 0) throw std::invalid_argument("invalid route sidecar shape");
    code_fd_ = ::open(code_path.c_str(), O_RDONLY);
    scale_fd_ = ::open(scale_path.c_str(), O_RDONLY);
    if (code_fd_ < 0 || scale_fd_ < 0) throw std::runtime_error("cannot open route sidecar");
    if (file_size(code_fd_) != rows_ * code_bytes_per_row_ || file_size(scale_fd_) != rows_ * sizeof(std::uint16_t))
        throw std::runtime_error("route sidecar size mismatch");
    void *code_map = ::mmap(nullptr, file_size(code_fd_), PROT_READ, MAP_PRIVATE, code_fd_, 0);
    void *scale_map = ::mmap(nullptr, file_size(scale_fd_), PROT_READ, MAP_PRIVATE, scale_fd_, 0);
    if (code_map == MAP_FAILED || scale_map == MAP_FAILED) throw std::runtime_error("mmap route sidecar failed");
    codes_ = static_cast<const std::uint8_t *>(code_map);
    scales_ = static_cast<const std::uint16_t *>(scale_map);
}

RouteSidecar::~RouteSidecar() {
    if (codes_) ::munmap(const_cast<std::uint8_t *>(codes_), rows_ * code_bytes_per_row_);
    if (scales_) ::munmap(const_cast<std::uint16_t *>(scales_), rows_ * sizeof(std::uint16_t));
    if (code_fd_ >= 0) ::close(code_fd_);
    if (scale_fd_ >= 0) ::close(scale_fd_);
}

const std::uint8_t *RouteSidecar::code(std::size_t id) const {
    if (id >= rows_) throw std::out_of_range("route id out of range");
    return codes_ + id * code_bytes_per_row_;
}

float RouteSidecar::scale(std::size_t id) const {
    if (id >= rows_) throw std::out_of_range("route id out of range");
    return half_to_float(scales_[id]);
}

float RouteSidecar::asymmetric_score(const float *query_projected, std::size_t id) const {
    const std::uint8_t *bits = code(id);
    const float s = scale(id);
    float dot = 0.0f;
    for (std::size_t i = 0; i < route_dim_; ++i)
        dot += query_projected[i] ? (bits[i >> 3] >> (i & 7U) & 1U ? query_projected[i] : -query_projected[i]) : 0.0f;
    return static_cast<float>(route_dim_) * s * s - 2.0f * s * dot;
}

}  // namespace adaptive_route
