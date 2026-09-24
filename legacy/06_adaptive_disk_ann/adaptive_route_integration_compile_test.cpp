#include "Ours/core/hnswlib/rabitq_hnsw.h"

int main() {
    using namespace hnswlib;
    std::vector<uint8_t> codes(1, 0x1f);
    std::vector<float> scales(1, 1.0f);
    auto storage = std::make_shared<AdaptiveRouteCodeStorage>(
        std::move(codes), std::move(scales), 1, 5);
    if (!storage->isAdaptive() || storage->asymmetricScore(0,
            std::vector<float>{1, 1, 1, 1, 1}.data(), 5) >= 0.0f)
        return 1;
    return 0;
}
