#include "svs/index/vamana/search_buffer.h"
#include <functional>
#include <stdexcept>

int main() {
    using Buffer = svs::index::vamana::SearchBuffer<unsigned, std::less<>>;
    using Node = svs::SearchNeighbor<unsigned>;
    auto check = [](bool condition) {
        if (!condition) throw std::runtime_error("official buffer regression");
    };
    Buffer buffer(2, std::less<>{}, false);
    buffer.push_back(Node{9, 1.0f});
    buffer.sort();
    buffer.insert(Node{1, 1.0f});
    check(buffer[0].id() == 9 && buffer[1].id() == 1);
    buffer.insert(Node{1, 1.0f});
    check(buffer.size() == 2);
    check(!buffer.emplace_visited(9) && !buffer.emplace_visited(9));
    check(buffer.next().id() == 9);
    check(buffer.next().id() == 1 && buffer.done());
    buffer.insert(Node{2, 0.0f});
    check(!buffer.done() && buffer.next().id() == 2);
    Buffer window(svs::index::vamana::SearchBufferConfig{1, 2});
    window.push_back(Node{0, 0.0f});
    window.push_back(Node{1, 1.0f});
    window.sort();
    window.next();
    check(window.done() && window.size() == 2);
}
