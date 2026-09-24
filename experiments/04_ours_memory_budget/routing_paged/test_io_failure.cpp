// Inject failure after a real successful submission; destructor must reap it.
#include <atomic>
#include <cassert>
#include <cstdio>
#include <fstream>
#include <libaio.h>
#include <string>
#include <unistd.h>
static std::atomic<int> calls{0};
static int failing_submit(io_context_t ctx, long count, iocb **requests) {
  if (calls.fetch_add(1) > 0)
    return -EIO;
  return io_submit(ctx, std::min<long>(1, count), requests);
}
#define io_submit failing_submit
#include "../../02_disk_shared_graph/native/routing_io.cpp"
#undef io_submit
int main() {
  std::string path = "/tmp/routing-failure-" + std::to_string(getpid());
  {
    std::ofstream f(path, std::ios::binary);
    std::string data(4096 * 64, 'x');
    f.write(data.data(), data.size());
  }
  auto *reader = qgraph_routing_new(path.c_str(), 64, 64, 64 * 4096);
  assert(reader);
  std::vector<size_t> tokens;
  bool submitted;
  for (size_t i = 0; i < 64; ++i) {
    auto token = qgraph_routing_acquire(reader, i, &submitted);
    if (token < 0)
      break;
    assert(token > 0);
    tokens.push_back(token);
  }
  char page[4096];
  bool failed = false;
  for (auto token : tokens) {
    for (;;) {
      auto epoch = qgraph_routing_epoch(reader);
      int result = qgraph_routing_copy(reader, token, page);
      if (result < 0) {
        failed = true;
        break;
      }
      if (result > 0)
        break;
      if (!qgraph_routing_wait(reader, epoch)) {
        failed = true;
        break;
      }
    }
    qgraph_routing_release(reader, token);
  }
  assert(failed);
  qgraph_routing_delete(reader);
  std::remove(path.c_str());
}
