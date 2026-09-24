// Reverse each harvested completion batch before the production cache sees it.
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdio>
#include <fstream>
#include <libaio.h>
#include <string>
#include <unistd.h>
static std::atomic<size_t> reversed{0};
static int reversed_getevents(io_context_t ctx, long minimum, long maximum,
                              io_event *events, timespec *timeout) {
  (void)minimum;
  int n = io_getevents(ctx, maximum, maximum, events, timeout);
  if (n > 1) {
    std::reverse(events, events + n);
    ++reversed;
  }
  return n;
}
#define io_getevents reversed_getevents
#include "../../02_disk_shared_graph/native/routing_io.cpp"
#undef io_getevents
int main() {
  std::string path = "/tmp/routing-reverse-" + std::to_string(getpid());
  {
    std::ofstream file(path, std::ios::binary);
    for (int i = 0; i < 64; ++i) {
      std::string page(4096, char(i));
      file.write(page.data(), page.size());
    }
  }
  auto *reader = qgraph_routing_new(path.c_str(), 64, 64, 64 * 4096);
  assert(reader);
  size_t tokens[64];
  bool submitted;
  for (size_t i = 0; i < 64; ++i) {
    auto token = qgraph_routing_acquire(reader, 63 - i, &submitted);
    assert(token > 0 && submitted);
    tokens[i] = token;
  }
  char page[4096];
  for (size_t i = 0; i < 64; ++i) {
    for (;;) {
      auto epoch = qgraph_routing_epoch(reader);
      int ready = qgraph_routing_copy(reader, tokens[i], page);
      assert(ready >= 0);
      if (ready)
        break;
      assert(qgraph_routing_wait(reader, epoch));
    }
    for (char value : page)
      assert(value == char(63 - i));
    qgraph_routing_release(reader, tokens[i]);
  }
  assert(reversed > 0);
  Stats stats;
  qgraph_routing_stats(reader, &stats);
  assert(stats.reads == 64 && stats.peak_active <= 64);
  qgraph_routing_delete(reader);
  std::remove(path.c_str());
}
