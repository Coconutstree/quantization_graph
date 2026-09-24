#include "asymmetric_route_kernel.hpp"

namespace adaptive_route {

float asymmetric_score(const float *query, const std::uint8_t *code,
                       std::size_t dim, float scale) {
    float dot = 0.0f;
    for (std::size_t i = 0; i < dim; ++i) {
        const bool positive = (code[i >> 3] >> (i & 7U)) & 1U;
        dot += query[i] * (positive ? 1.0f : -1.0f);
    }
    return static_cast<float>(dim) * scale * scale - 2.0f * scale * dot;
}

}  // namespace adaptive_route

