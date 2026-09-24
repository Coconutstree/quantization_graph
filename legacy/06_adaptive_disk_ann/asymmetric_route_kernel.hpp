#pragma once

#include <cstddef>
#include <cstdint>

namespace adaptive_route {

// Returns the candidate-ranking part of ||q - scale*sign(code)||^2.
// The query norm is omitted because it is constant for one query.
float asymmetric_score(const float *query, const std::uint8_t *code,
                       std::size_t dim, float scale);

}  // namespace adaptive_route

