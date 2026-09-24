#include <atomic>
#define main symphony_port_main
#include "symphonyqg_disk_port.cpp"
#undef main

int main() {
    const auto directory = fs::temp_directory_path() / ("symqg-parity-" + std::to_string(getpid()));
    fs::create_directories(directory);
    // Verify serialization byte fidelity, including rejection of tampered fields.
    {
        auto root = directory / "export-check";
        fs::create_directories(root);
        auto m = make_meta(2, 64, 32);
        std::vector<char> raw(sizeof(m.entry_point) + m.n * m.row_bytes + m.padded_dim * sizeof(float), 0);
        std::memcpy(raw.data(), &m.entry_point, sizeof(m.entry_point));
        for (std::size_t i = sizeof(m.entry_point); i < raw.size(); ++i) raw[i] = char(i % 127);
        auto original = root / "original.index";
        { std::ofstream f(original, std::ios::binary); f.write(raw.data(), raw.size()); }
        { std::ofstream f(root / "node_rows.pages", std::ios::binary);
          std::vector<char> row(m.pages_per_row * kPage, 0);
          for (std::size_t i = 0; i < m.n; ++i) {
              std::memcpy(row.data(), raw.data() + sizeof(m.entry_point) + i * m.row_bytes, m.row_bytes);
              f.write(row.data(), row.size());
          }
        }
        write_padded_file(root / "rotator.bin", std::vector<char>(raw.end() - m.padded_dim * sizeof(float), raw.end()));
        verify_export(original, root, m);
        for (auto offset : {std::size_t(0), m.code_offset * sizeof(float), m.factor_offset * sizeof(float),
                            m.neighbor_offset * sizeof(float), m.row_bytes}) {
            auto path = root / "node_rows.pages";
            char saved;
            { std::fstream f(path, std::ios::binary | std::ios::in | std::ios::out);
              f.seekg(offset); f.read(&saved, 1); char changed = saved ^ 1;
              f.seekp(offset); f.write(&changed, 1); }
            bool rejected = false;
            try { verify_export(original, root, m); } catch (const std::runtime_error&) { rejected = true; }
            if (!rejected) throw std::runtime_error("tampered export accepted");
            { std::fstream f(path, std::ios::binary | std::ios::in | std::ios::out);
              f.seekp(offset); f.write(&saved, 1); }
        }
        { std::ofstream f(root / "rotator.bin", std::ios::binary | std::ios::app); f.put('x'); }
        bool rejected = false;
        try { verify_export(original, root, m); } catch (const std::runtime_error&) { rejected = true; }
        if (!rejected) throw std::runtime_error("tampered rotation accepted");
    }
    auto meta = make_meta(64, 64, 32);
    symqg::FHTRotator rotator(meta.dim);
    symqg::QGScanner scanner(meta.padded_dim, meta.degree);
    std::vector<float> data(meta.n * meta.row_floats, 0.0f);
    // Controlled tied distances, connected and disconnected graphs exercise fallback.
    for (bool connected : {false, true}) {
        for (std::size_t u = 0; u < meta.n; ++u) {
            auto* row = data.data() + u * meta.row_floats;
            std::fill(row, row + meta.dim, static_cast<float>(u % 4));
            auto* neighbors = reinterpret_cast<symqg::PID*>(row + meta.neighbor_offset);
            for (std::size_t j = 0; j < meta.degree; ++j)
                neighbors[j] = connected ? (u + j + 1) % meta.n : 0;
        }
        auto index = directory / "native.index";
        {
            std::ofstream output(index, std::ios::binary);
            output.write(reinterpret_cast<const char*>(&meta.entry_point), sizeof(meta.entry_point));
            output.write(reinterpret_cast<const char*>(data.data()), data.size() * sizeof(float));
            rotator.save(output);
        }
        auto pages = directory / "rows.pages";
        {
            std::ofstream output(pages, std::ios::binary);
            std::vector<char> padding(meta.pages_per_row * kPage - meta.row_bytes, 0);
            for (std::size_t u = 0; u < meta.n; ++u) {
                output.write(reinterpret_cast<const char*>(data.data() + u * meta.row_floats), meta.row_bytes);
                output.write(padding.data(), padding.size());
            }
        }
        symqg::QuantizedGraph original(meta.n, meta.degree, meta.dim);
        original.load_index(index.c_str());
        for (int width : {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 32, 64, 580}) {
            original.set_ef(width);
            for (float value : {0.0f, 0.5f, 2.0f}) {
                std::vector<float> query(meta.dim, value);
                std::vector<symqg::PID> expected(kTopK);
                original.search(query.data(), kTopK, expected.data());
                symqg::QGQuery prepared(query.data(), meta.padded_dim);
                prepared.query_prepare(rotator, scanner);
                QueryStats stats;
                RowReader reader(pages, meta, &stats);
                auto actual = search_one(reader, meta, prepared, scanner, query.data(), width, stats);
                for (int i = 0; i < kTopK; ++i) {
                    if (static_cast<symqg::PID>(actual[i]) != expected[i])
                        throw std::runtime_error("Native/disk ID order mismatch at width " + std::to_string(width));
                }
            }
        }
    }
    // Exercise nonzero codes/factors produced by the actual official builder.
    {
        auto built_meta = make_meta(128, 64, 32);
        std::vector<float> base(built_meta.n * built_meta.dim);
        for (std::size_t i = 0; i < base.size(); ++i)
            base[i] = std::sin(float(i * 17 + 3)) + 0.2f * std::cos(float(i * 7));
        symqg::QuantizedGraph original(built_meta.n, built_meta.degree, built_meta.dim);
        symqg::QGBuilder builder(original, 64, base.data(), 1);
        builder.build(3);
        auto raw = directory / "built.index";
        original.save_index(raw.c_str());
        std::ifstream input(raw, std::ios::binary);
        input.read(reinterpret_cast<char*>(&built_meta.entry_point), sizeof(symqg::PID));
        auto pages = directory / "built.pages";
        {
            std::ofstream output(pages, std::ios::binary);
            std::vector<char> row(built_meta.pages_per_row * kPage, 0);
            for (std::size_t u = 0; u < built_meta.n; ++u) {
                input.read(row.data(), built_meta.row_bytes);
                output.write(row.data(), row.size());
            }
        }
        symqg::FHTRotator built_rotator(built_meta.dim);
        built_rotator.load(input);
        symqg::QGScanner built_scanner(built_meta.padded_dim, built_meta.degree);
        for (int width : {1, 9, 10, 32, 64, 128}) {
            original.set_ef(width);
            for (int q = 0; q < 8; ++q) {
                const float* query = base.data() + q * built_meta.dim;
                std::vector<symqg::PID> expected(kTopK);
                original.search(query, kTopK, expected.data());
                symqg::QGQuery prepared(query, built_meta.padded_dim);
                prepared.query_prepare(built_rotator, built_scanner);
                QueryStats stats;
                RowReader reader(pages, built_meta, &stats);
                auto actual = search_one(reader, built_meta, prepared, built_scanner, query, width, stats);
                for (std::size_t cache_budget : {std::size_t(256*1024), std::size_t(4*1024*1024)}) {
                    qgraph05::SharedPageCache shared(cache_budget);
                    if(shared.allocated_bytes()>cache_budget) throw std::runtime_error("cache budget violation");
                    for(int repeat=0;repeat<2;++repeat) {
                        QueryStats cached;
                        RowReader cached_reader(pages,built_meta,&cached,&shared);
                        auto result=search_one(cached_reader,built_meta,prepared,built_scanner,query,width,cached);
                        if(result!=actual || cached.visited_nodes!=stats.visited_nodes || cached.distance_calls!=stats.distance_calls)
                            throw std::runtime_error("shared-cache traversal parity mismatch");
                        if(repeat && cache_budget==4*1024*1024 && (cached.bytes_read || !cached.shared_cache_hits))
                            throw std::runtime_error("warm cache did not eliminate reads");
                    }
                }
                for (int i = 0; i < kTopK; ++i)
                    if (static_cast<symqg::PID>(actual[i]) != expected[i])
                        throw std::runtime_error("Built-index upstream/disk mismatch");
            }
        }
    }
    // Concurrent overlapping lookups and eviction must never return another page's bytes.
    qgraph05::SharedPageCache concurrent(256*1024);
    std::atomic<int> corrupt{0};
#pragma omp parallel for num_threads(8)
    for(int i=0;i<8192;++i) {
        const std::uint64_t page=i%257;
        std::array<char,kPage> bytes{},read{};
        std::memcpy(bytes.data(),&page,sizeof(page));
        concurrent.put(page,bytes.data());
        if(concurrent.get(page,read.data()) && read!=bytes) ++corrupt;
    }
    if(corrupt) throw std::runtime_error("concurrent cache corruption");
    fs::remove_all(directory);
    std::cout << "PASS: 126 official comparisons + 192 cache traversal comparisons + concurrent eviction stress\n";
}
