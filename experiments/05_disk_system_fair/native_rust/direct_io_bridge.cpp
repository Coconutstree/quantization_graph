#include "direct_io.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <exception>
#include <memory>
#include <string>
#include <vector>

namespace {

thread_local std::string last_error;

struct BridgeStats {
    std::uint64_t requested_pages;
    std::uint64_t unique_pages;
    std::uint64_t submitted_requests;
    std::uint64_t duplicate_pages_removed;
    std::uint64_t coalesced_requests;
    std::uint64_t bytes_read;
};

void remember_exception() noexcept {
    try {
        throw;
    } catch (const std::exception& error) {
        last_error = error.what();
    } catch (...) {
        last_error = "unknown native direct-I/O exception";
    }
}

}  // namespace

extern "C" {

void* qgraph05_direct_reader_new(
        const char* path,
        std::size_t max_inflight,
        std::size_t max_coalesce_pages) noexcept {
    try {
        last_error.clear();
        return new qgraph05::DirectAioReader(path, max_inflight, max_coalesce_pages);
    } catch (...) {
        remember_exception();
        return nullptr;
    }
}

void qgraph05_direct_reader_delete(void* opaque) noexcept {
    delete static_cast<qgraph05::DirectAioReader*>(opaque);
}

bool qgraph05_direct_reader_read_pages(
        void* opaque,
        const std::uint64_t* page_ids,
        std::size_t count,
        std::uint8_t* output,
        BridgeStats* stats) noexcept {
    try {
        last_error.clear();
        if (opaque == nullptr || (count != 0 && (page_ids == nullptr || output == nullptr)) ||
            stats == nullptr) {
            throw std::invalid_argument("invalid direct-I/O bridge argument");
        }
        std::vector<std::uint64_t> pages(page_ids, page_ids + count);
        const qgraph05::PageReadBatch batch =
                static_cast<qgraph05::DirectAioReader*>(opaque)->read_pages(pages);
        for (std::size_t index = 0; index < count; ++index) {
            const qgraph05::PageView page = batch.page(page_ids[index]);
            std::memcpy(output + index * qgraph05::kPageSize, page.data(), qgraph05::kPageSize);
        }
        const qgraph05::IoStats& source = batch.stats();
        *stats = BridgeStats{
                source.requested_pages,
                source.unique_pages,
                source.submitted_requests,
                source.duplicate_pages_removed,
                source.coalesced_requests,
                source.bytes_read};
        return true;
    } catch (...) {
        remember_exception();
        return false;
    }
}

std::size_t qgraph05_direct_reader_error(char* output, std::size_t capacity) noexcept {
    const std::size_t required = last_error.size();
    if (output != nullptr && capacity != 0) {
        const std::size_t copied = std::min(required, capacity - 1);
        std::memcpy(output, last_error.data(), copied);
        output[copied] = '\0';
    }
    return required;
}

}  // extern "C"
