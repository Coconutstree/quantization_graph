#include <cassert>
#include <fstream>
#include <string>
#include <thread>
#include <vector>
#include <atomic>
#include <unistd.h>
#include "../../02_disk_shared_graph/native/routing_io.cpp"
int main(){
 for(bool clock:{false,true}){
  std::string path="/tmp/routing-batch-error-"+std::to_string(getpid());
  {std::ofstream f(path,std::ios::binary);std::string b(3*4096,'x');f.write(b.data(),b.size());}
  void* r=qgraph_routing_new_policy(path.c_str(),3,2,3*4096,clock);assert(r);assert(truncate(path.c_str(),0)==0);
  std::atomic<int> failures{0};std::vector<std::thread> threads;
  for(int i=0;i<8;++i)threads.emplace_back([&,i]{
   Request req;req.page=i%3;void* client=qgraph_routing_client_new();assert(client);
   for(;;){
    if(!qgraph_routing_batch_step(r,&req,1)){++failures;break;}
    assert(!req.bytes);
    if(!qgraph_routing_batch_wait(r,client,&req,1)){++failures;break;}
   }
   qgraph_routing_batch_release(r,&req,1,true);qgraph_routing_client_delete(client);
  });
  for(auto& t:threads)t.join();assert(failures==8);qgraph_routing_delete(r);std::remove(path.c_str());
 }
}
