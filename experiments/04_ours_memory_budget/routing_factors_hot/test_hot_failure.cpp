#include <cassert>
#include <fstream>
#include <string>
#include <unistd.h>
#include "../../02_disk_shared_graph/native/routing_io.cpp"
int main() {
  std::string path="/tmp/routing-hot-failure-"+std::to_string(getpid());
  {std::ofstream f(path); std::string bytes(4096*4,'x');f.write(bytes.data(),bytes.size());}
  for(bool clock:{false,true}) {
    void *r=qgraph_routing_new_policy(path.c_str(),3,2,4096*4,clock);assert(r);
    uint64_t all[]={0,1,2}, duplicate[]={0,0}, outside[]={4}, hot[]={0};
    assert(!qgraph_routing_configure(r,all,3,0));
    assert(!qgraph_routing_configure(r,duplicate,2,0));
    assert(!qgraph_routing_configure(r,outside,1,0));
    assert(!qgraph_routing_configure(r,hot,1,5));
    assert(qgraph_routing_configure(r,hot,1,4));
    uint64_t counts[4];assert(!qgraph_routing_profile(r,counts,3));
    assert(qgraph_routing_profile(r,counts,4));
    for(auto count:counts)assert(count==0);
    qgraph_routing_delete(r);
  }
  unlink(path.c_str());
}
