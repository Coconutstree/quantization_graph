#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace adaptive_route {

struct RouteArtifacts {
    std::vector<uint8_t> codes;
    std::vector<float> scales;
    std::vector<float> mean;
    std::vector<float> components;
    size_t rows{0};
    size_t input_dim{0};
    size_t route_dim{0};
};

RouteArtifacts load_artifacts(const std::string &codes_path,
                              const std::string &scales_path,
                              const std::string &mean_npy_path,
                              const std::string &components_npy_path,
                              size_t rows,
                              size_t route_dim,
                              size_t input_dim);

}  // namespace adaptive_route
