#include "qg/qg_scanner.hpp"
#include <cmath>
#include <iostream>

int main() {
    for (size_t dim : {1024, 2048}) {
        symqg::QGScanner scanner(dim, 64);
        std::vector<uint8_t> lut(dim * 4, 127);
        std::vector<uint8_t> codes(dim * 8, 0);
        std::vector<float> factors(64 * 3, 0), result(64);
        std::fill(factors.begin() + 64, factors.begin() + 128, 1);
        scanner.scan_neighbors(result.data(), lut.data(), 0, 0, 1, 0,
                               codes.data(), factors.data());
        const float expected = 2 * (dim / 4) * 127;
        for (float value : result) {
            if (value != expected) {
                std::cerr << dim << ": " << value << " != " << expected << '\n';
                return 1;
            }
        }
    }
    std::cout << "PASS unsigned FastScan sums across INT16_MAX\n";
}
