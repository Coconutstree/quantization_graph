// Diagnostic LD_PRELOAD probe. Does not change request arguments or results.
#include <libaio.h>
#include <dlfcn.h>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cerrno>
#include <string_view>

namespace {
using Clock = std::chrono::steady_clock;
struct Counter {
    std::atomic<unsigned long long> calls{0}, ns{0}, errors{0};
};
Counter counts[4];
template<class F> F resolve(const char* name) {
    const char* version = std::string_view(name) == "io_submit" ? "LIBAIO_0.1" : "LIBAIO_0.4";
    auto f = reinterpret_cast<F>(dlvsym(RTLD_NEXT, name, version));
    if (!f) std::abort();
    return f;
}
template<class F> int measure(int slot, F call) {
    auto start = Clock::now();
    int result = call();
    int saved_errno = errno;
    auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start).count();
    counts[slot].calls.fetch_add(1, std::memory_order_relaxed);
    counts[slot].ns.fetch_add(elapsed, std::memory_order_relaxed);
    if (result < 0) counts[slot].errors.fetch_add(1, std::memory_order_relaxed);
    errno = saved_errno;
    return result;
}
__attribute__((destructor)) void report() {
    if (counts[2].calls.load() == 0) return;
    const char* path = std::getenv("QG_AIO_TIMING_OUTPUT");
    if (!path) return;
    FILE* file = std::fopen(path, "wx");
    if (!file) return;
    std::fprintf(file, "operation,calls,thread_wall_ns,errors\n");
    const char* names[] = {"io_submit", "io_getevents", "io_setup", "io_destroy"};
    for (int i = 0; i < 4; ++i)
        std::fprintf(file, "%s,%llu,%llu,%llu\n", names[i], counts[i].calls.load(), counts[i].ns.load(), counts[i].errors.load());
    std::fclose(file);
}
}
extern "C" int io_submit(io_context_t ctx, long n, iocb** cb) {
    static auto real = resolve<decltype(&io_submit)>("io_submit");
    return measure(0, [&] { return real(ctx, n, cb); });
}
extern "C" int io_getevents(io_context_t ctx, long min, long max, io_event* events, timespec* timeout) {
    static auto real = resolve<decltype(&io_getevents)>("io_getevents");
    return measure(1, [&] { return real(ctx, min, max, events, timeout); });
}
extern "C" int io_setup(int n, io_context_t* ctx) {
    static auto real = resolve<decltype(&io_setup)>("io_setup");
    return measure(2, [&] { return real(n, ctx); });
}
extern "C" int io_destroy(io_context_t ctx) {
    static auto real = resolve<decltype(&io_destroy)>("io_destroy");
    return measure(3, [&] { return real(ctx); });
}
