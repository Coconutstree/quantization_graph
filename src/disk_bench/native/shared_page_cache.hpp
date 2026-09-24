#pragma once
#include "query_page_cache.hpp"
#include <memory>
#include <mutex>

namespace qgraph05 {
// Index-local cache: one owner per search configuration, shared by all workers.
// Fixed allocations include the LRU/hash metadata; no allocation on insertion.
class SharedPageCache {
    static constexpr std::size_t shards = 16;
    struct Shard { std::mutex mutex; std::unique_ptr<QueryPageCache> cache; };
    std::array<Shard, shards> data_;
public:
    explicit SharedPageCache(std::size_t budget) {
        if (budget < sizeof(*this) + shards * 8192) return;
        const auto each = (budget - sizeof(*this)) / shards;
        for (auto& s : data_) s.cache = std::make_unique<QueryPageCache>(each);
        if (allocated_bytes() > budget) throw std::runtime_error("shared cache exceeds budget");
    }
    std::size_t allocated_bytes() const {
        std::size_t n=sizeof(*this);
        for (auto& s:data_) if(s.cache) n+=s.cache->allocated_bytes();
        return n;
    }
    bool enabled() const { return bool(data_[0].cache); }
    bool get(std::uint64_t page, char* out) {
        auto& s=data_[page%shards];
        if(!s.cache) return false;
        std::lock_guard<std::mutex> lock(s.mutex);
        return s.cache->get(0,page,out);
    }
    void put(std::uint64_t page,const char* bytes) {
        auto& s=data_[page%shards];
        if(!s.cache) return;
        std::lock_guard<std::mutex> lock(s.mutex);
        s.cache->put(0,page,bytes);
    }
};
}
