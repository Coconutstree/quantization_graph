#pragma once
#include "direct_io.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <sys/resource.h>
#include <vector>

namespace qgraph05 {
inline void check_query_cache_budget(std::size_t workers, double budget_gib) {
    constexpr std::size_t per_query=4*1024*1024;
    if (!(budget_gib>0) || workers>budget_gib*(1ULL<<30)/per_query)
        throw std::runtime_error("query caches alone exceed memory budget");
}
inline std::uint64_t measured_peak_rss() {
    rusage usage{};
    if (getrusage(RUSAGE_SELF, &usage) != 0) throw std::runtime_error("getrusage failed");
    return static_cast<std::uint64_t>(usage.ru_maxrss) * 1024;
}

// Fixed storage for both pages and hash/LRU metadata. No allocations on insertion.
class QueryPageCache {
    static constexpr std::uint32_t none = UINT32_MAX;
    struct Slot {
        std::array<char, kPageSize> bytes;
        std::uint64_t page = 0;
        std::uint32_t file = 0, prev = none, next = none;
    };
    std::vector<Slot> slots_;
    std::vector<std::uint32_t> table_;
    std::uint32_t used_ = 0, head_ = none, tail_ = none;
    std::size_t bucket(std::uint32_t file, std::uint64_t page) const {
        auto x = page ^ (std::uint64_t(file) * 0x9e3779b97f4a7c15ULL);
        x ^= x >> 30; x *= 0xbf58476d1ce4e5b9ULL;
        x ^= x >> 27; x *= 0x94d049bb133111ebULL;
        return (x ^ (x >> 31)) & (table_.size()-1);
    }
    std::size_t find(std::uint32_t file, std::uint64_t page) const {
        auto b = bucket(file,page);
        while (table_[b] != none) {
            const auto& s = slots_[table_[b]];
            if (s.file == file && s.page == page) break;
            b = (b+1)&(table_.size()-1);
        }
        return b;
    }
    void touch(std::uint32_t id) {
        auto& s=slots_[id];
        if (head_ == id) return;
        if (s.prev != none) slots_[s.prev].next=s.next;
        if (s.next != none) slots_[s.next].prev=s.prev;
        if (tail_ == id) tail_=s.prev;
        s.prev=none; s.next=head_;
        if (head_ != none) slots_[head_].prev=id;
        head_=id;
        if (tail_ == none) tail_=id;
    }
    void erase(std::uint32_t id) {
        auto b=find(slots_[id].file,slots_[id].page);
        table_[b]=none;
        b=(b+1)&(table_.size()-1);
        while (table_[b] != none) {
            auto move=table_[b]; table_[b]=none;
            table_[find(slots_[move].file,slots_[move].page)]=move;
            b=(b+1)&(table_.size()-1);
        }
    }
  public:
    std::uint64_t hits=0, misses=0, evictions=0;
    static constexpr std::size_t default_budget = 4 * 1024 * 1024;
    explicit QueryPageCache(std::size_t budget=default_budget) {
        std::size_t count=budget/(sizeof(Slot)+16);
        std::size_t buckets=1;
        while (buckets < count*2) buckets*=2;
        while (count && sizeof(*this)+count*sizeof(Slot)+buckets*sizeof(std::uint32_t)>budget) --count;
        if (!count) throw std::invalid_argument("query cache budget too small");
        slots_.resize(count); table_.assign(buckets,none);
        if (allocated_bytes()>budget) throw std::runtime_error("query cache allocation exceeds budget");
    }
    std::size_t allocated_bytes() const {
        return sizeof(*this)+slots_.capacity()*sizeof(Slot)+table_.capacity()*sizeof(std::uint32_t);
    }
    std::size_t capacity_pages() const { return slots_.size(); }
    bool get(std::uint32_t file, std::uint64_t page, char* out) {
        auto id=table_[find(file,page)];
        if (id==none) { ++misses; return false; }
        ++hits; touch(id); std::memcpy(out,slots_[id].bytes.data(),kPageSize); return true;
    }
    void put(std::uint32_t file, std::uint64_t page, const char* data) {
        auto id=table_[find(file,page)];
        if (id==none) {
            if (used_ < slots_.size()) id=used_++;
            else { id=tail_; erase(id); ++evictions; }
            slots_[id].file=file; slots_[id].page=page;
            table_[find(file,page)]=id;
        }
        std::memcpy(slots_[id].bytes.data(),data,kPageSize); touch(id);
    }
};

template<class Stats>
void cached_pages(DirectAioReader& reader, QueryPageCache& cache, std::uint32_t file,
                  std::uint64_t first, std::size_t count, std::vector<char>& out, Stats& stats) {
    out.resize(count*kPageSize);
    std::vector<std::uint64_t> missing;
    for (std::size_t i=0;i<count;++i)
        if (!cache.get(file,first+i,out.data()+i*kPageSize)) missing.push_back(first+i);
    stats.query_cache_hits=cache.hits; stats.query_cache_misses=cache.misses;
    stats.query_cache_allocated_bytes=cache.allocated_bytes();
    if (missing.empty()) return;
    const auto start=std::chrono::steady_clock::now();
    auto batch=reader.read_pages(missing);
    stats.io_wait_us+=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count();
    const auto& io=batch.stats();
    stats.bytes_read+=io.bytes_read; stats.io_requests+=io.submitted_requests;
    stats.coalesced+=io.coalesced_requests; stats.duplicates+=io.duplicate_pages_removed;
    for (auto page:missing) {
        auto target=out.data()+(page-first)*kPageSize;
        std::memcpy(target,batch.page(page).data(),kPageSize);
        cache.put(file,page,target);
    }
    stats.query_cache_evictions=cache.evictions;
}
}
