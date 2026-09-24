#include "query_page_cache.hpp"
#include <cassert>
#include <iostream>
#include <list>
#include <random>
#include <map>

int main() {
    qgraph05::QueryPageCache cache(32768);
    assert(cache.allocated_bytes()<=32768);
    using Key=std::pair<uint32_t,uint64_t>;
    std::list<Key> lru;
    std::map<Key,std::array<char,4096>> reference;
    std::mt19937 random(42);
    for (int i=0;i<10000;++i) {
        Key key{random()%2, random()%16};
        std::array<char,4096> actual{}, expected{};
        bool hit=cache.get(key.first,key.second,actual.data());
        assert(hit==(reference.count(key)!=0));
        if (hit) {
            assert(actual==reference.at(key));
            lru.remove(key); lru.push_front(key);
        } else {
            if (reference.size()==cache.capacity_pages()) {
                reference.erase(lru.back()); lru.pop_back();
            }
            expected.fill(static_cast<char>(i));
            reference[key]=expected; lru.push_front(key);
            cache.put(key.first,key.second,expected.data());
        }
    }
    assert(cache.hits+cache.misses==10000);
    assert(cache.evictions>0);
    std::cout << "PASS capacity, file isolation, collisions, LRU, byte integrity\n";
}
