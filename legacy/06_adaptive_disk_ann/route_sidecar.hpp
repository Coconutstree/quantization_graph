#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <vector>

namespace adaptive_route {

class RouteSidecar {
 public:
    RouteSidecar(const std::filesystem::path &code_path,
                 const std::filesystem::path &scale_path,
                 std::size_t rows, std::size_t route_dim);
    RouteSidecar(const RouteSidecar &) = delete;
    RouteSidecar &operator=(const RouteSidecar &) = delete;
    ~RouteSidecar();

    std::size_t rows() const { return rows_; }
    std::size_t route_dim() const { return route_dim_; }
    std::size_t code_bytes_per_row() const { return code_bytes_per_row_; }
    std::size_t resident_bytes() const { return code_bytes_per_row_ * rows_ + sizeof(std::uint16_t) * rows_; }

    const std::uint8_t *code(std::size_t id) const;
    float scale(std::size_t id) const;
    float asymmetric_score(const float *query_projected, std::size_t id) const;

 private:
    int code_fd_{-1};
    int scale_fd_{-1};
    const std::uint8_t *codes_{nullptr};
    const std::uint16_t *scales_{nullptr};
    std::size_t rows_{0};
    std::size_t route_dim_{0};
    std::size_t code_bytes_per_row_{0};
};

}  // namespace adaptive_route

