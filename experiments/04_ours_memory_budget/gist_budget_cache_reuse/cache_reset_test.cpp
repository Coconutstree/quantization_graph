#include "query_page_cache.hpp"
#include <cassert>
#include <cstdlib>
#include <new>
#include <iostream>
static size_t allocations=0;
void* operator new(size_t n) { ++allocations; if(auto p=std::malloc(n))return p; throw std::bad_alloc(); }
void operator delete(void* p) noexcept {std::free(p);}
void operator delete(void* p,size_t) noexcept {std::free(p);}
int main(){
 qgraph05::QueryPageCache cache(32768);
 const auto bytes=cache.allocated_bytes(), pages=cache.capacity_pages();
 std::array<char,4096> in{},out{};
 for(int round=0;round<100;++round){
  for(size_t p=0;p<pages*3;++p){in.fill(char(p));cache.put(p%2,p,in.data());}
  assert(cache.evictions>0);
  auto before=allocations;
  cache.clear();
  assert(allocations==before);
  assert(cache.allocated_bytes()==bytes && cache.capacity_pages()==pages);
  assert(cache.hits==0 && cache.misses==0 && cache.evictions==0);
  for(size_t p=0;p<pages*3;++p)assert(!cache.get(p%2,p,out.data()));
  for(size_t p=0;p<pages;++p){in.fill(char(p+round));cache.put(1,p,in.data());}
  for(size_t p=0;p<pages;++p){assert(cache.get(1,p,out.data()));in.fill(char(p+round));assert(out==in);}
 }
 std::cout<<"PASS 100 reset cycles: no allocation, no stale hits, reuse after eviction, byte integrity\n";
}
