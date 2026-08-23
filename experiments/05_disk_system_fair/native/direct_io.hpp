#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <vector>

#include <libaio.h>

namespace qgraph05 {

inline constexpr std::size_t kPageSize = 4096;
inline constexpr std::size_t kMaxInflightIo = 128;

class AlignedBytes {
  public:
    AlignedBytes() = default;
    explicit AlignedBytes(std::size_t bytes);
    ~AlignedBytes();

    AlignedBytes(const AlignedBytes&) = delete;
    AlignedBytes& operator=(const AlignedBytes&) = delete;
    AlignedBytes(AlignedBytes&& other) noexcept;
    AlignedBytes& operator=(AlignedBytes&& other) noexcept;

    [[nodiscard]] std::byte* data() noexcept;
    [[nodiscard]] const std::byte* data() const noexcept;
    [[nodiscard]] std::size_t size() const noexcept;

  private:
    void* data_ = nullptr;
    std::size_t size_ = 0;
};

struct IoStats {
    std::uint64_t requested_pages = 0;
    std::uint64_t unique_pages = 0;
    std::uint64_t submitted_requests = 0;
    std::uint64_t duplicate_pages_removed = 0;
    std::uint64_t coalesced_requests = 0;
    std::uint64_t bytes_read = 0;
};

struct PageBlock {
    std::uint64_t first_page = 0;
    std::uint32_t page_count = 0;
    AlignedBytes bytes;
};

class PageView {
  public:
    PageView(const std::byte* data, std::size_t size) : data_(data), size_(size) {}

    [[nodiscard]] const std::byte* data() const noexcept { return data_; }
    [[nodiscard]] std::size_t size() const noexcept { return size_; }
    [[nodiscard]] const std::byte& front() const { return data_[0]; }
    [[nodiscard]] const std::byte& back() const { return data_[size_ - 1]; }

  private:
    const std::byte* data_;
    std::size_t size_;
};

class PageReadBatch {
  public:
    [[nodiscard]] PageView page(std::uint64_t page_id) const;
    [[nodiscard]] const std::vector<PageBlock>& blocks() const noexcept;
    [[nodiscard]] const IoStats& stats() const noexcept;

  private:
    friend class DirectAioReader;
    std::vector<PageBlock> blocks_;
    IoStats stats_;
};

class DirectAioReader {
  public:
    explicit DirectAioReader(
            const std::filesystem::path& path,
            std::size_t max_inflight = kMaxInflightIo,
            std::size_t max_coalesce_pages = 32);
    ~DirectAioReader();

    DirectAioReader(const DirectAioReader&) = delete;
    DirectAioReader& operator=(const DirectAioReader&) = delete;

    // A reader owns one persistent native-AIO context and is intentionally
    // single-worker. Construct one reader per query worker.
    [[nodiscard]] PageReadBatch read_pages(
            const std::vector<std::uint64_t>& page_ids);
    [[nodiscard]] std::uint64_t file_bytes() const noexcept;
    [[nodiscard]] std::uint64_t page_count() const noexcept;

  private:
    int fd_ = -1;
    std::uint64_t file_bytes_ = 0;
    std::size_t max_inflight_ = kMaxInflightIo;
    std::size_t max_coalesce_pages_ = 32;
    io_context_t context_ = 0;

    void reset_context();
};

}  // namespace qgraph05
