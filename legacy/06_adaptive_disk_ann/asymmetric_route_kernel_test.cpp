#include "asymmetric_route_kernel.hpp"

#include <cmath>
#include <cstdint>
#include <iostream>
#include <random>
#include <vector>

int main() {
    constexpr std::size_t dim = 257;
    std::mt19937 rng(17);
    std::normal_distribution<float> normal(0.0f, 1.0f);
    std::uniform_real_distribution<float> scale_dist(0.1f, 2.0f);
    std::vector<float> query(dim);
    for (float &value : query) value = normal(rng);
    std::vector<std::uint8_t> code((dim + 7U) / 8U, 0);
    for (std::size_t i = 0; i < dim; ++i) {
        if ((rng() & 1U) != 0) code[i >> 3] |= static_cast<std::uint8_t>(1U << (i & 7U));
    }
    const float scale = scale_dist(rng);
    const float fast = adaptive_route::asymmetric_score(query.data(), code.data(), dim, scale);
    float explicit_distance = 0.0f;
    float query_norm = 0.0f;
    for (std::size_t i = 0; i < dim; ++i) {
        const bool positive = (code[i >> 3] >> (i & 7U)) & 1U;
        const float reconstructed = scale * (positive ? 1.0f : -1.0f);
        const float delta = query[i] - reconstructed;
        explicit_distance += delta * delta;
        query_norm += query[i] * query[i];
    }
    if (std::fabs((fast + query_norm) - explicit_distance) > 1e-4f) {
        std::cerr << "asymmetric kernel parity failure\n";
        return 1;
    }
    std::cout << "asymmetric kernel parity: OK\n";
    return 0;
}

