// Bounded asynchronous routing pages; pinned data is immutable outside mu.
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <libaio.h>
#include <limits>
#include <list>
#include <mutex>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/eventfd.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <unordered_map>
#include <vector>

namespace {
thread_local std::string error;
constexpr size_t PAGE = 4096;
constexpr uint64_t DONE = UINT64_MAX;
struct Stats {
  uint64_t requests = 0, hits = 0, merges = 0, reads = 0, bytes = 0,
           peak_active = 0, peak_pages = 0, evictions = 0, wait_ns = 0;
  uint64_t lock_calls = 0, lock_wait_ns = 0, batch_steps = 0,
           release_batches = 0, notifications = 0, wait_calls = 0,
           submit_calls = 0, submitted_pages = 0;
};
struct Request {
  uint64_t page = 0, token = 0;
  const uint8_t *bytes = nullptr;
  uint64_t submitted_bytes = 0;
};
struct Client {
  std::condition_variable cv;
  Request *requests = nullptr;
  size_t count = 0;
  Client *next = nullptr;
  bool notified = false;
};
struct Slot {
  uint64_t page = 0;
  size_t pins = 0;
  int state = 0;
  bool referenced = false, hot = false;
  std::list<size_t>::iterator lru;
};
class Routing {
  int fd = -1, submit_fd = -1, completion_fd = -1;
  io_context_t ctx = 0;
  uint64_t length;
  size_t limit, active = 0, eligible = 0, hand = 0;
  bool clock_policy;
  char *data = nullptr;
  std::vector<Slot> slots;
  std::vector<uint64_t> hot_pages, profile;
  std::vector<size_t> free;
  std::unordered_map<uint64_t, size_t> map;
  std::list<size_t> lru;
  std::deque<size_t> queue;
  std::mutex mu;
  std::condition_variable legacy_cv;
  Client *waiters = nullptr;
  size_t legacy_waiters = 0;
  uint64_t generation = 0;
  bool stop = false;
  std::string failure;
  std::thread worker;
  void wake_service() noexcept {
    uint64_t one = 1;
    while (write(submit_fd, &one, sizeof(one)) < 0 && errno == EINTR) {
    }
  }
  void poll_service() {
    pollfd fds[2] = {{submit_fd, POLLIN, 0}, {completion_fd, POLLIN, 0}};
    int result;
    do {
      result = poll(fds, 2, -1);
    } while (result < 0 && errno == EINTR);
    if (result < 0)
      throw std::runtime_error("routing poll failed");
    for (auto &f : fds) {
      if (f.revents & (POLLERR | POLLNVAL))
        throw std::runtime_error("routing eventfd failed");
      if (f.revents & POLLIN) {
        uint64_t value;
        while (read(f.fd, &value, sizeof(value)) > 0) {
        }
      }
    }
  }
  void check() {
    if (!failure.empty())
      throw std::runtime_error(failure);
  }
  auto query_lock() {
    auto start = std::chrono::steady_clock::now();
    std::unique_lock<std::mutex> lock(mu);
    ++stats.lock_calls;
    stats.lock_wait_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(
                              std::chrono::steady_clock::now() - start)
                              .count();
    return lock;
  }
  bool can_admit() const {
    return active < limit && (!free.empty() || eligible);
  }
  void legacy_change() {
    ++generation;
    if (legacy_waiters)
      legacy_cv.notify_all();
  }
  // Called with mu held. Only actual dependents are woken; capacity grants wake
  // one additional waiter. Predicate rechecks under mu prevent lost wakeups.
  bool ready(Client *c, bool &capacity) {
    for (size_t i = 0; i < c->count; ++i) {
      auto &r = c->requests[i];
      if (r.token == DONE)
        continue;
      if (r.token) {
        if (slots[r.token - 1].state == 3)
          return true;
      } else {
        if (map.find(r.page) != map.end())
          return true;
        if (can_admit())
          capacity = true;
      }
    }
    return false;
  }
  void notify_waiters() {
    bool capacity_given = false;
    for (Client *c = waiters; c; c = c->next) {
      if (c->notified)
        continue;
      bool capacity = false;
      bool dependent = ready(c, capacity);
      if (!failure.empty() || stop || dependent ||
          (capacity && !capacity_given)) {
        if (!dependent && capacity)
          capacity_given = true;
        c->notified = true;
        ++stats.notifications;
        c->cv.notify_one();
      }
    }
    legacy_change();
  }
  void unlist(Client *client) {
    Client **c = &waiters;
    while (*c && *c != client)
      c = &(*c)->next;
    if (*c)
      *c = client->next;
    client->next = nullptr;
    client->requests = nullptr;
    client->count = 0;
  }
  size_t victim() {
    if (!free.empty()) {
      size_t id = free.back();
      free.pop_back();
      return id;
    }
    if (!eligible)
      throw std::runtime_error("no evictable routing slot");
    size_t id = 0;
    if (!clock_policy) {
      id = lru.back();
      lru.pop_back();
    } else {
      bool found = false;
      for (size_t scanned = 0; scanned < slots.size() * 2; ++scanned) {
        id = hand;
        hand = (hand + 1) % slots.size();
        auto &s = slots[id];
        if (s.state != 3 || s.pins || s.hot)
          continue;
        if (s.referenced) {
          s.referenced = false;
          continue;
        }
        found = true;
        break;
      }
      if (!found)
        throw std::runtime_error("CLOCK eligibility invariant failed");
    }
    --eligible;
    map.erase(slots[id].page);
    ++stats.evictions;
    return id;
  }
  int64_t acquire_locked(uint64_t page, bool *submitted) {
    *submitted = false;
    if (page >= (length + PAGE - 1) / PAGE)
      throw std::runtime_error("routing page out of range");
    auto found = map.find(page);
    if (found != map.end()) {
      auto id = found->second;
      auto &s = slots[id];
      if (s.state == 3) {
        ++stats.hits;
        if (!s.pins && !s.hot) {
          --eligible;
          if (!clock_policy)
            lru.erase(s.lru);
        }
      } else
        ++stats.merges;
      ++s.pins;
      s.referenced = true;
      ++stats.requests;
      if (page < profile.size())
        ++profile[page];
      return id + 1;
    }
    if (!can_admit())
      return 0;
    size_t id = victim();
    auto &s = slots[id];
    s.page = page;
    s.pins = 1;
    s.state = 1;
    s.referenced = true;
    s.hot = std::binary_search(hot_pages.begin(), hot_pages.end(), page);
    if (page < profile.size())
      ++profile[page];
    map.emplace(page, id);
    queue.push_back(id);
    ++active;
    ++stats.requests;
    stats.peak_active = std::max<uint64_t>(stats.peak_active, active);
    stats.peak_pages = std::max<uint64_t>(stats.peak_pages, map.size());
    *submitted = true;
    return id + 1;
  }
  void release_locked(size_t token) {
    if (!token || token > slots.size() || !slots[token - 1].pins)
      return;
    size_t id = token - 1;
    auto &s = slots[id];
    if (!--s.pins && s.state == 3 && !s.hot) {
      ++eligible;
      if (!clock_policy) {
        lru.push_front(id);
        s.lru = lru.begin();
      }
    }
  }
  // Control blocks have independent reusable IDs: never reuse an iocb before
  // its completion is reaped, even while other requests from the batch remain.
  void complete() noexcept {
    std::vector<iocb> controls;
    std::vector<iocb *> pointers;
    std::vector<io_event> events;
    std::vector<size_t> control_slots, available;
    try {
      controls.resize(limit);
      pointers.reserve(limit);
      events.resize(limit);
      control_slots.resize(limit);
      available.reserve(limit);
      for (size_t i = 0; i < limit; ++i)
        available.push_back(i);
      size_t inflight = 0;
      for (;;) {
        pointers.clear();
        {
          std::unique_lock<std::mutex> lock(mu);
          if (stop && queue.empty() && !inflight)
            break;
          while (!queue.empty() && !available.empty()) {
            size_t slot = queue.front();
            queue.pop_front();
            size_t control = available.back();
            available.pop_back();
            slots[slot].state = 2;
            control_slots[control] = slot;
            io_prep_pread(&controls[control], fd, data + slot * PAGE, PAGE,
                          slots[slot].page * PAGE);
            io_set_eventfd(&controls[control], completion_fd);
            controls[control].data = reinterpret_cast<void *>(control + 1);
            pointers.push_back(&controls[control]);
          }
        }
        size_t sent = 0;
        while (sent < pointers.size()) {
          int n =
              io_submit(ctx, pointers.size() - sent, pointers.data() + sent);
          if (n == -EINTR)
            continue;
          if (n <= 0)
            throw std::runtime_error("routing io_submit failed: " +
                                     std::to_string(n));
          sent += n;
          inflight += n;
          {
            std::lock_guard<std::mutex> lock(mu);
            ++stats.submit_calls;
            stats.submitted_pages += n;
          }
        }
        if (!inflight) {
          poll_service();
          continue;
        }
        int n = io_getevents(ctx, 0, inflight, events.data(), nullptr);
        if (n == -EINTR)
          continue;
        if (n < 0)
          throw std::runtime_error("routing io_getevents failed");
        if (n == 0) {
          poll_service();
          continue;
        }
        {
          std::lock_guard<std::mutex> lock(mu);
          for (int i = 0; i < n; ++i) {
            size_t control = reinterpret_cast<uintptr_t>(events[i].data) - 1;
            if (control >= limit)
              throw std::runtime_error("routing completion token invalid");
            size_t id = control_slots[control];
            auto &s = slots[id];
            if (s.state != 2)
              throw std::runtime_error("routing completion state invalid");
            size_t expected = std::min<uint64_t>(PAGE, length - s.page * PAGE);
            if (events[i].res2 || events[i].res != expected)
              throw std::runtime_error("routing read failed or file truncated");
            if (expected < PAGE)
              std::memset(data + id * PAGE + expected, 0, PAGE - expected);
            s.state = 3;
            --active;
            --inflight;
            ++stats.reads;
            stats.bytes += expected;
            available.push_back(control);
            if (!s.pins && !s.hot) {
              ++eligible;
              if (!clock_policy) {
                lru.push_front(id);
                s.lru = lru.begin();
              }
            }
          }
          notify_waiters();
        }
        // Refill immediately after any completion instead of draining a batch.
      }
    } catch (const std::exception &e) {
      if (ctx) {
        io_destroy(ctx);
        ctx = 0;
      }
      std::lock_guard<std::mutex> lock(mu);
      failure = e.what();
      notify_waiters();
    }
  }

public:
  Stats stats;
  Routing(const char *path, size_t capacity, size_t inflight, uint64_t expected,
          bool use_clock = false)
      : length(expected), limit(inflight), clock_policy(use_clock),
        slots(capacity) {
    if (!capacity || !inflight || inflight > capacity ||
        capacity > SIZE_MAX / PAGE)
      throw std::runtime_error("invalid routing capacity");
    try {
      fd = open(path, O_RDONLY | O_DIRECT | O_CLOEXEC);
      if (fd < 0)
        throw std::runtime_error("routing O_DIRECT open failed");
      struct stat st {};
      if (fstat(fd, &st) || uint64_t(st.st_size) != length || !length)
        throw std::runtime_error("routing file length changed");
      if (posix_memalign(reinterpret_cast<void **>(&data), PAGE,
                         capacity * PAGE))
        throw std::bad_alloc();
      map.reserve(capacity);
      free.reserve(capacity);
      for (size_t i = 0; i < capacity; ++i)
        free.push_back(capacity - i - 1);
      submit_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
      completion_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
      if (submit_fd < 0 || completion_fd < 0)
        throw std::runtime_error("routing eventfd allocation failed");
      if (io_setup(limit, &ctx))
        throw std::runtime_error("routing io_setup failed");
      worker = std::thread([this] { complete(); });
    } catch (...) {
      if (ctx)
        io_destroy(ctx);
      if (fd >= 0)
        close(fd);
      if (submit_fd >= 0)
        close(submit_fd);
      if (completion_fd >= 0)
        close(completion_fd);
      std::free(data);
      throw;
    }
  }
  ~Routing() {
    {
      std::lock_guard<std::mutex> lock(mu);
      stop = true;
      notify_waiters();
      wake_service();
    }
    if (worker.joinable())
      worker.join();
    if (ctx)
      io_destroy(ctx);
    if (fd >= 0)
      close(fd);
    if (submit_fd >= 0)
      close(submit_fd);
    if (completion_fd >= 0)
      close(completion_fd);
    std::free(data);
  }
  void batch_step(Request *requests, size_t count) {
    auto lock = query_lock();
    check();
    ++stats.batch_steps;
    bool any_submitted = false;
    for (size_t i = 0; i < count; ++i) {
      auto &r = requests[i];
      r.submitted_bytes = 0;
      if (r.token == DONE)
        continue;
      if (!r.token) {
        bool submitted = false;
        r.token = acquire_locked(r.page, &submitted);
        if (submitted) {
          any_submitted = true;
          r.submitted_bytes = std::min<uint64_t>(PAGE, length - r.page * PAGE);
        }
      }
      if (r.token) {
        if (r.token > slots.size() || !slots[r.token - 1].pins)
          throw std::runtime_error("invalid batch token");
        if (slots[r.token - 1].state == 3)
          r.bytes = reinterpret_cast<uint8_t *>(data + (r.token - 1) * PAGE);
      }
    }
    if (any_submitted) {
      legacy_change();
      wake_service();
    }
  }
  void batch_release(Request *requests, size_t count, bool all) {
    auto lock = query_lock();
    ++stats.release_batches;
    bool released = false;
    for (size_t i = 0; i < count; ++i) {
      auto &r = requests[i];
      if (r.token && r.token != DONE && (all || r.bytes)) {
        release_locked(r.token);
        r.token = DONE;
        r.bytes = nullptr;
        released = true;
      }
    }
    if (released)
      notify_waiters();
  }
  void wait(Client *client, Request *requests, size_t count) {
    auto lock = query_lock();
    check();
    ++stats.wait_calls;
    client->requests = requests;
    client->count = count;
    client->notified = false;
    bool capacity = false;
    if (ready(client, capacity) || capacity) {
      client->requests = nullptr;
      return;
    }
    client->next = waiters;
    waiters = client;
    auto start = std::chrono::steady_clock::now();
    try {
      client->cv.wait(
          lock, [&] { return client->notified || !failure.empty() || stop; });
    } catch (...) {
      unlist(client);
      throw;
    }
    unlist(client);
    stats.wait_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(
                         std::chrono::steady_clock::now() - start)
                         .count();
    check();
  }
  // Compatibility entrypoints for fault-injection tests, not the production
  // path.
  int64_t acquire(uint64_t page, bool *submitted) {
    auto lock = query_lock();
    check();
    auto token = acquire_locked(page, submitted);
    if (*submitted) {
      legacy_change();
      wake_service();
    }
    return token;
  }
  int copy(size_t token, void *output) {
    auto lock = query_lock();
    check();
    if (!token || token > slots.size() || !slots[token - 1].pins)
      throw std::runtime_error("invalid routing token");
    if (slots[token - 1].state != 3)
      return 0;
    std::memcpy(output, data + (token - 1) * PAGE, PAGE);
    return 1;
  }
  void release(size_t token) {
    auto lock = query_lock();
    release_locked(token);
    notify_waiters();
  }
  uint64_t epoch() {
    std::lock_guard<std::mutex> lock(mu);
    return generation;
  }
  void legacy_wait(uint64_t observed) {
    auto lock = query_lock();
    check();
    ++legacy_waiters;
    legacy_cv.wait(lock, [&] {
      return generation != observed || !failure.empty() || stop;
    });
    --legacy_waiters;
    check();
  }
  void clear() {
    std::lock_guard<std::mutex> lock(mu);
    check();
    if (active)
      throw std::runtime_error("routing clear with active I/O");
    for (auto &s : slots)
      if (s.pins)
        throw std::runtime_error("routing clear with pinned pages");
    map.clear();
    lru.clear();
    free.clear();
    eligible = 0;
    hand = 0;
    for (size_t i = 0; i < slots.size(); ++i) {
      slots[i].state = 0;
      free.push_back(slots.size() - i - 1);
    }
    notify_waiters();
  }
  void configure(const uint64_t *pages, size_t n, size_t profile_pages) {
    std::lock_guard<std::mutex> lock(mu);
    if (!map.empty() || active || !hot_pages.empty() || !profile.empty())
      throw std::runtime_error("routing configuration must precede requests");
    if (n >= slots.size() || profile_pages > (length + PAGE - 1) / PAGE)
      throw std::runtime_error("invalid routing hot/profile capacity");
    for (size_t i = 0; i < n; ++i)
      if (pages[i] >= (length + PAGE - 1) / PAGE ||
          (i && pages[i] <= pages[i - 1]))
        throw std::runtime_error(
            "hot routing pages must be sorted unique valid IDs");
    if (n)
      hot_pages.assign(pages, pages + n);
    profile.resize(profile_pages, 0);
  }
  void profile_copy(uint64_t *out, size_t n) {
    std::lock_guard<std::mutex> lock(mu);
    if (n != profile.size() || active)
      throw std::runtime_error("invalid routing profile snapshot");
    std::copy(profile.begin(), profile.end(), out);
  }
  Stats snapshot() {
    std::lock_guard<std::mutex> lock(mu);
    return stats;
  }
};
} // namespace
extern "C" {
void *qgraph_routing_new_policy(const char *path, size_t capacity,
                                size_t inflight, uint64_t length,
                                bool clock) noexcept {
  try {
    return new Routing(path, capacity, inflight, length, clock);
  } catch (const std::exception &e) {
    error = e.what();
    return nullptr;
  }
}
void *qgraph_routing_new(const char *path, size_t capacity, size_t inflight,
                         uint64_t length) noexcept {
  return qgraph_routing_new_policy(path, capacity, inflight, length, false);
}
void qgraph_routing_delete(void *p) noexcept {
  delete static_cast<Routing *>(p);
}
void *qgraph_routing_client_new() noexcept {
  try {
    return new Client();
  } catch (const std::exception &e) {
    error = e.what();
    return nullptr;
  }
}
void qgraph_routing_client_delete(void *p) noexcept {
  delete static_cast<Client *>(p);
}
bool qgraph_routing_batch_step(void *p, Request *r, size_t n) noexcept {
  try {
    static_cast<Routing *>(p)->batch_step(r, n);
    return true;
  } catch (const std::exception &e) {
    error = e.what();
    return false;
  }
}
void qgraph_routing_batch_release(void *p, Request *r, size_t n,
                                  bool all) noexcept {
  static_cast<Routing *>(p)->batch_release(r, n, all);
}
bool qgraph_routing_batch_wait(void *p, void *c, Request *r,
                               size_t n) noexcept {
  try {
    static_cast<Routing *>(p)->wait(static_cast<Client *>(c), r, n);
    return true;
  } catch (const std::exception &e) {
    error = e.what();
    return false;
  }
}
int64_t qgraph_routing_acquire(void *p, uint64_t page,
                               bool *submitted) noexcept {
  try {
    return static_cast<Routing *>(p)->acquire(page, submitted);
  } catch (const std::exception &e) {
    error = e.what();
    return -1;
  }
}
int qgraph_routing_copy(void *p, size_t token, void *out) noexcept {
  try {
    return static_cast<Routing *>(p)->copy(token, out);
  } catch (const std::exception &e) {
    error = e.what();
    return -1;
  }
}
void qgraph_routing_release(void *p, size_t token) noexcept {
  static_cast<Routing *>(p)->release(token);
}
uint64_t qgraph_routing_epoch(void *p) noexcept {
  return static_cast<Routing *>(p)->epoch();
}
bool qgraph_routing_wait(void *p, uint64_t observed) noexcept {
  try {
    static_cast<Routing *>(p)->legacy_wait(observed);
    return true;
  } catch (const std::exception &e) {
    error = e.what();
    return false;
  }
}
bool qgraph_routing_clear(void *p) noexcept {
  try {
    static_cast<Routing *>(p)->clear();
    return true;
  } catch (const std::exception &e) {
    error = e.what();
    return false;
  }
}
bool qgraph_routing_configure(void *p, const uint64_t *pages, size_t n,
                              size_t profile_pages) noexcept {
  try {
    static_cast<Routing *>(p)->configure(pages, n, profile_pages);
    return true;
  } catch (const std::exception &e) {
    error = e.what();
    return false;
  }
}
bool qgraph_routing_profile(void *p, uint64_t *out, size_t n) noexcept {
  try {
    static_cast<Routing *>(p)->profile_copy(out, n);
    return true;
  } catch (const std::exception &e) {
    error = e.what();
    return false;
  }
}
void qgraph_routing_stats(void *p, Stats *out) noexcept {
  *out = static_cast<Routing *>(p)->snapshot();
}
const char *qgraph_routing_error() noexcept { return error.c_str(); }
}
