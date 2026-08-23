#include "direct_io.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

namespace fs = std::filesystem;

int main() {
    const fs::path path = fs::temp_directory_path() /
            ("qgraph05_direct_io_" + std::to_string(::getpid()) + ".pages");
    try {
        {
            std::ofstream stream(path, std::ios::binary | std::ios::trunc);
            if (!stream) {
                throw std::runtime_error("cannot create direct-I/O test file");
            }
            std::array<char, qgraph05::kPageSize> page{};
            for (std::uint64_t page_id = 0; page_id < 8; ++page_id) {
                page.fill(static_cast<char>(page_id + 1));
                stream.write(page.data(), page.size());
            }
        }
        qgraph05::DirectAioReader reader(path, 4, 3);
        const qgraph05::PageReadBatch batch = reader.read_pages({5, 2, 3, 2, 4, 7});
        if (batch.stats().requested_pages != 6 || batch.stats().unique_pages != 5 ||
            batch.stats().duplicate_pages_removed != 1 ||
            batch.stats().submitted_requests != 3 || batch.stats().coalesced_requests != 2 ||
            batch.stats().bytes_read != 5 * qgraph05::kPageSize) {
            throw std::runtime_error("direct-I/O accounting mismatch");
        }
        for (const std::uint64_t page_id : {2ULL, 3ULL, 4ULL, 5ULL, 7ULL}) {
            const auto page = batch.page(page_id);
            if (page.size() != qgraph05::kPageSize ||
                std::to_integer<unsigned char>(page.front()) != page_id + 1 ||
                std::to_integer<unsigned char>(page.back()) != page_id + 1) {
                throw std::runtime_error("direct-I/O page contents mismatch");
            }
        }
        bool missing_rejected = false;
        try {
            static_cast<void>(batch.page(6));
        } catch (const std::out_of_range&) {
            missing_rejected = true;
        }
        if (!missing_rejected) {
            throw std::runtime_error("unrequested page was exposed");
        }
        fs::remove(path);
        std::cout << "PASS qgraph05_direct_io_test\n";
        return 0;
    } catch (const std::exception& error) {
        std::error_code ignored;
        fs::remove(path, ignored);
        std::cerr << "FAIL qgraph05_direct_io_test: " << error.what() << "\n";
        return 1;
    }
}
