#include "route_sidecar.hpp"

#include <cassert>
#include <cstdint>
#include <fstream>
#include <iostream>

int main() {
    const char *codes = "/tmp/adaptive_route_codes.bin";
    const char *scales = "/tmp/adaptive_route_scales.f16";
    { std::ofstream out(codes, std::ios::binary); const std::uint8_t row[] = {0x0f}; out.write(reinterpret_cast<const char *>(row), 1); }
    { std::ofstream out(scales, std::ios::binary); const std::uint16_t one = 0x3c00; out.write(reinterpret_cast<const char *>(&one), 2); }
    adaptive_route::RouteSidecar sidecar(codes, scales, 1, 4);
    const float query[] = {1, 1, 1, 1};
    assert(sidecar.resident_bytes() == 3);
    assert(sidecar.asymmetric_score(query, 0) < 1e-5f);
    std::cout << "route sidecar parity: OK\n";
    return 0;
}

