// Regression: compare disk search to the official single-query API used by experiment 03.
#define main glass_disk_port_main
#include "../../../experiments/03_disk_system/native/glass_disk_port.cpp"
#undef main
#include "glass/searcher/graph_searcher.hpp"
#include <unistd.h>
#include <random>

int main() {
    omp_set_num_threads(1);
    constexpr int n = 64, dim = 64, degree = 64;
    auto directory = fs::temp_directory_path() / ("glass-official-audit-" + std::to_string(getpid()));
    fs::create_directories(directory);
    glass::Graph<int32_t> graph(n, degree);
    graph.eps = {63};
    for (int u = 0; u < n; ++u) {
        int offset = 0;
        for (int v = n - 1; v >= 0; --v)
            if (v != u) graph.at(u, offset++) = v;
        graph.at(u, offset) = -1;
    }
    glass::GraphSearcher<Quant> original(std::move(graph));
    std::vector<float> base(n * dim);
    for (int u = 0; u < n; ++u)
        std::fill(base.begin() + u * dim, base.begin() + (u + 1) * dim, float(u % 4));
    for (bool tied : {true, false}) {
    if (!tied) {
        std::mt19937 rng(20260916);
        std::uniform_real_distribution<float> value(-3.0f, 3.0f);
        for (auto& x : base) x = value(rng);
    }
    original.SetData(base.data(), n, dim);
    auto gl = make_layout(n, degree * sizeof(int32_t));
    auto cl = make_layout(n, original.quant.code_size());
    std::vector<char> graphs(gl.pages() * kPage, 0), codes(cl.pages() * kPage, 0);
    for (int u = 0; u < n; ++u) {
        std::memcpy(graphs.data() + gl.page(u) * kPage + gl.offset(u), original.graph.edges(u), gl.record_bytes);
        std::memcpy(codes.data() + cl.page(u) * kPage + cl.offset(u), original.quant.get_code(u), cl.record_bytes);
    }
    write_padded_pages(directory / "graph.pages", graphs);
    write_padded_pages(directory / "codes.pages", codes);
    Meta meta;
    meta.n = n; meta.dim = dim; meta.graph_k = degree; meta.eps = {63};
    int comparisons = 0, mismatches = 0, membership_mismatches = 0;
    for (int width : {1, 10, 20, 64, 580}) {
        original.SetEf(width);
        for (float value : {0.0f, 0.5f, 2.0f}) {
            std::vector<float> query(dim, value);
            std::vector<int32_t> expected(kTopK);
            original.Search(query.data(), kTopK, expected.data());
            auto computer = original.quant.get_computer(query.data());
            QueryStats stats;
            DiskAccess disk(directory / "graph.pages", directory / "codes.pages", gl, cl, &stats);
            auto actual = search_one(disk, meta, computer, width, stats);
            ++comparisons;
            if (expected != actual) {
                ++mismatches;
                std::cout << "MISMATCH width=" << width << " query=" << value << " upstream=";
                for (auto id : expected) std::cout << id << ',';
                std::cout << " disk=";
                for (auto id : actual) std::cout << id << ',';
                std::cout << '\n';
            }
            std::sort(expected.begin(), expected.end());
            std::sort(actual.begin(), actual.end());
            membership_mismatches += expected != actual;
        }
    }
    std::cout << "comparisons=" << comparisons << " ordered_mismatches=" << mismatches
              << " membership_mismatches=" << membership_mismatches << '\n';
    if (mismatches) return 2;
    }
    fs::remove_all(directory);
    return 0;
}
