// Hold one completed event back until a replacement request has been submitted.
// A drain-the-whole-batch loop cannot make progress in this test.
#include <libaio.h>
#include <atomic>
#include <cassert>
#include <fstream>
#include <string>
#include <unistd.h>
#include <algorithm>
static std::atomic<int> submitted{0};
static bool holding=false,first=true,refilled=false;
static io_event held;
static int submit_counted(io_context_t c,long n,iocb** p){int r=io_submit(c,n,p);if(r>0)submitted+=r;return r;}
static int delayed_events(io_context_t c,long minimum,long maximum,io_event* out,timespec* timeout){
 if(holding){if(submitted>=3){refilled=true;out[0]=held;holding=false;return 1;}return 0;}
 if(first){int n=io_getevents(c,maximum,maximum,out,timeout);assert(n==2);held=out[1];holding=true;first=false;return 1;}
 return io_getevents(c,minimum,maximum,out,timeout);
}
#define io_submit submit_counted
#define io_getevents delayed_events
#include "../../02_disk_shared_graph/native/routing_io.cpp"
#undef io_submit
#undef io_getevents
int main(){
 std::string path="/tmp/routing-rolling-"+std::to_string(getpid());
 {std::ofstream f(path,std::ios::binary);for(int i=0;i<3;++i){std::string b(4096,char(10+i));f.write(b.data(),b.size());}}
 void* r=qgraph_routing_new_policy(path.c_str(),3,2,3*4096,true);assert(r);
 void* client=qgraph_routing_client_new();assert(client);
 Request req[3];for(int i=0;i<3;++i)req[i].page=i;
 size_t done=0;
 while(done<3){
  assert(qgraph_routing_batch_step(r,req,3));bool progress=false;
  for(int i=0;i<3;++i)if(req[i].bytes){for(int k=0;k<4096;++k)assert(req[i].bytes[k]==10+i);++done;progress=true;}
  qgraph_routing_batch_release(r,req,3,false);
  if(!progress && done<3)assert(qgraph_routing_batch_wait(r,client,req,3));
 }
 assert(refilled);Stats s;qgraph_routing_stats(r,&s);assert(s.reads==3 && s.peak_active==2);
 qgraph_routing_client_delete(client);qgraph_routing_delete(r);std::remove(path.c_str());
}
