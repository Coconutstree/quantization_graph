#include "direct_io.hpp"

#include <libaio.h>

#include <algorithm>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <sys/mman.h>
#include <unistd.h>

namespace qgraph05 {
bool reference_mmap_enabled() {
    const char* value = std::getenv("QG05_REFERENCE_MMAP");
    return value && std::strcmp(value, "1") == 0;
}
namespace {

[[noreturn]] void throw_system(const std::string& operation, int error_number = errno) {
    throw std::runtime_error(
            operation + ": " + std::string(std::strerror(error_number)));
}

void check_alignment(std::size_t bytes) {
    if (bytes == 0 || bytes % kPageSize != 0) {
        throw std::invalid_argument("aligned allocation size must be a positive 4 KiB multiple");
    }
}

}  // namespace

AlignedBytes::AlignedBytes(std::size_t bytes) : size_(bytes) {
    check_alignment(bytes);
    const int result = ::posix_memalign(&data_, kPageSize, bytes);
    if (result != 0) {
        data_ = nullptr;
        size_ = 0;
        throw_system("posix_memalign", result);
    }
}

AlignedBytes::~AlignedBytes() {
    std::free(data_);
}

AlignedBytes::AlignedBytes(AlignedBytes&& other) noexcept
        : data_(other.data_), size_(other.size_) {
    other.data_ = nullptr;
    other.size_ = 0;
}

AlignedBytes& AlignedBytes::operator=(AlignedBytes&& other) noexcept {
    if (this != &other) {
        std::free(data_);
        data_ = other.data_;
        size_ = other.size_;
        other.data_ = nullptr;
        other.size_ = 0;
    }
    return *this;
}

std::byte* AlignedBytes::data() noexcept {
    return static_cast<std::byte*>(data_);
}

const std::byte* AlignedBytes::data() const noexcept {
    return static_cast<const std::byte*>(data_);
}

std::size_t AlignedBytes::size() const noexcept {
    return size_;
}

PageView PageReadBatch::page(std::uint64_t page_id) const {
    const auto it = std::upper_bound(
            blocks_.begin(),
            blocks_.end(),
            page_id,
            [](std::uint64_t id, const PageBlock& block) {
                return id < block.first_page;
            });
    if (it == blocks_.begin()) {
        throw std::out_of_range("page was not requested");
    }
    const PageBlock& block = *std::prev(it);
    const std::uint64_t end_page = block.first_page + block.page_count;
    if (page_id >= end_page) {
        throw std::out_of_range("page was not requested");
    }
    const std::size_t offset =
            static_cast<std::size_t>(page_id - block.first_page) * kPageSize;
    return PageView(block.bytes.data() + offset, kPageSize);
}

const std::vector<PageBlock>& PageReadBatch::blocks() const noexcept {
    return blocks_;
}

const IoStats& PageReadBatch::stats() const noexcept {
    return stats_;
}

DirectAioReader::DirectAioReader(
        const std::filesystem::path& path,
        std::size_t max_inflight,
        std::size_t max_coalesce_pages)
        : max_inflight_(max_inflight), max_coalesce_pages_(max_coalesce_pages) {
    if (max_inflight_ == 0 || max_inflight_ > kMaxInflightIo) {
        throw std::invalid_argument("max_inflight must be in [1, 128]");
    }
    if (max_coalesce_pages_ == 0) {
        throw std::invalid_argument("max_coalesce_pages must be positive");
    }
    fd_ = ::open(path.c_str(), O_RDONLY | O_CLOEXEC |
            (reference_mmap_enabled() ? 0 : O_DIRECT));
    if (fd_ < 0) {
        throw_system("open(O_DIRECT) " + path.string());
    }
    struct stat st {};
    if (::fstat(fd_, &st) != 0) {
        const int saved = errno;
        ::close(fd_);
        fd_ = -1;
        throw_system("fstat " + path.string(), saved);
    }
    if (st.st_size <= 0 || static_cast<std::uint64_t>(st.st_size) % kPageSize != 0) {
        ::close(fd_);
        fd_ = -1;
        throw std::runtime_error("direct-I/O file must be non-empty and 4 KiB padded: " + path.string());
    }
    file_bytes_ = static_cast<std::uint64_t>(st.st_size);
    if (reference_mmap_enabled()) {
        void* mapping = ::mmap(nullptr, file_bytes_, PROT_READ, MAP_PRIVATE, fd_, 0);
        if (mapping == MAP_FAILED) {
            const int saved = errno;
            ::close(fd_);
            fd_ = -1;
            throw_system("reference mmap", saved);
        }
        reference_mapping_ = static_cast<const std::byte*>(mapping);
        return;
    }
    const int setup = ::io_setup(static_cast<int>(max_inflight_), &context_);
    if (setup < 0) {
        const int saved = -setup;
        ::close(fd_);
        fd_ = -1;
        throw_system("io_setup", saved);
    }
}

DirectAioReader::~DirectAioReader() {
    if (reference_mapping_) ::munmap(const_cast<std::byte*>(reference_mapping_), file_bytes_);
    if (context_ != 0) {
        ::io_destroy(context_);
    }
    if (fd_ >= 0) {
        ::close(fd_);
    }
}

PageReadBatch DirectAioReader::read_pages(
        const std::vector<std::uint64_t>& page_ids) {
    PageReadBatch result;
    result.stats_.requested_pages = page_ids.size();
    if (page_ids.empty()) {
        return result;
    }

    std::vector<std::uint64_t> unique = page_ids;
    std::sort(unique.begin(), unique.end());
    unique.erase(std::unique(unique.begin(), unique.end()), unique.end());
    result.stats_.unique_pages = unique.size();
    result.stats_.duplicate_pages_removed = page_ids.size() - unique.size();
    for (const std::uint64_t page_id : unique) {
        if (page_id >= page_count()) {
            throw std::out_of_range("page id exceeds direct-I/O file");
        }
        if (result.blocks_.empty()) {
            result.blocks_.push_back(PageBlock{page_id, 1, AlignedBytes()});
            continue;
        }
        PageBlock& previous = result.blocks_.back();
        const bool adjacent = page_id == previous.first_page + previous.page_count;
        if (adjacent && previous.page_count < max_coalesce_pages_) {
            ++previous.page_count;
        } else {
            result.blocks_.push_back(PageBlock{page_id, 1, AlignedBytes()});
        }
    }
    for (PageBlock& block : result.blocks_) {
        block.bytes = AlignedBytes(
                static_cast<std::size_t>(block.page_count) * kPageSize);
    }
    result.stats_.submitted_requests = result.blocks_.size();
    result.stats_.coalesced_requests = unique.size() - result.blocks_.size();
    result.stats_.bytes_read = unique.size() * kPageSize;

    if (reference_mapping_) {
        for (PageBlock& block : result.blocks_) {
            std::memcpy(block.bytes.data(), reference_mapping_ + block.first_page * kPageSize,
                        block.bytes.size());
        }
        result.stats_.submitted_requests = 0;
        result.stats_.bytes_read = 0;
        return result;
    }

    try {
        for (std::size_t begin = 0; begin < result.blocks_.size(); begin += max_inflight_) {
            const std::size_t count =
                    std::min(max_inflight_, result.blocks_.size() - begin);
            std::vector<struct iocb> controls(count);
            std::vector<struct iocb*> pointers(count);
            std::vector<struct io_event> events(count);
            for (std::size_t index = 0; index < count; ++index) {
                PageBlock& block = result.blocks_[begin + index];
                ::io_prep_pread(
                        &controls[index],
                        fd_,
                        block.bytes.data(),
                        block.bytes.size(),
                        static_cast<long long>(block.first_page * kPageSize));
                controls[index].data = reinterpret_cast<void*>(index + 1);
                pointers[index] = &controls[index];
            }
            std::size_t submitted = 0;
            while (submitted < count) {
                const int value = ::io_submit(
                        context_,
                        static_cast<long>(count - submitted),
                        pointers.data() + submitted);
                if (value == -EINTR) {
                    continue;
                }
                if (value < 0) {
                    throw_system("io_submit", -value);
                }
                if (value == 0) {
                    throw std::runtime_error("io_submit made no progress");
                }
                submitted += static_cast<std::size_t>(value);
            }

            std::size_t completed = 0;
            while (completed < count) {
                const int value = ::io_getevents(
                        context_,
                        1,
                        static_cast<long>(count - completed),
                        events.data() + completed,
                        nullptr);
                if (value == -EINTR) {
                    continue;
                }
                if (value < 0) {
                    throw_system("io_getevents", -value);
                }
                completed += static_cast<std::size_t>(value);
            }
            for (const io_event& event : events) {
                const auto local = reinterpret_cast<std::uintptr_t>(event.data);
                if (local == 0 || local > count) {
                    throw std::runtime_error("native AIO returned an unknown request token");
                }
                const PageBlock& block = result.blocks_[begin + local - 1];
                if (event.res2 != 0 || event.res != block.bytes.size()) {
                    throw std::runtime_error("native AIO direct read was short or failed");
                }
            }
        }
    } catch (...) {
        // io_destroy cancels/reaps requests before their aligned buffers leave
        // scope. Recreate the context so a handled I/O error cannot poison the
        // next query.
        reset_context();
        throw;
    }
    return result;
}

void DirectAioReader::reset_context() {
    if (context_ != 0) {
        ::io_destroy(context_);
        context_ = 0;
    }
    const int setup = ::io_setup(static_cast<int>(max_inflight_), &context_);
    if (setup < 0) {
        context_ = 0;
        throw_system("io_setup while recovering direct-I/O context", -setup);
    }
}

std::uint64_t DirectAioReader::file_bytes() const noexcept {
    return file_bytes_;
}

std::uint64_t DirectAioReader::page_count() const noexcept {
    return file_bytes_ / kPageSize;
}

}  // namespace qgraph05
