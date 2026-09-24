//! Bounded DB1 paging. Page bytes are copied into a 64-record scratch window,
//! then unpinned immediately; even a one-page cache can make progress.
use diskann::{ANNError, ANNResult};
use std::ffi::{c_char, c_void, CStr, CString};
use std::path::Path;
use std::time::Instant;

pub const PAGE: usize = 4096;
pub const PAGE_CHARGE: usize = PAGE + 256;
// Completion thread stack, allocator/context bookkeeping and request controls.
pub const SERVICE_RESERVE: usize = 10 * 1024 * 1024;
#[derive(Clone, Copy, Debug)]
pub struct PagingPlan {
    pub clock: bool,
    pub resident_factors: bool,
    pub capacity: usize,
    pub inflight: usize,
    pub reserved_bytes: usize,
}
#[derive(Clone, Copy, Debug)]
pub enum RoutingPlan {
    Resident,
    Paged(PagingPlan),
}
impl RoutingPlan {
    pub fn choose(
        available: usize,
        routing: usize,
        cache_cap: Option<usize>,
        inflight: usize,
    ) -> Result<Self, String> {
        if inflight == 0 {
            return Err("routing inflight pages must be positive".into());
        }
        if routing <= available {
            return Ok(Self::Resident);
        }
        let page_budget = available.checked_sub(SERVICE_RESERVE).ok_or_else(|| {
            format!(
                "insufficient memory for routing service: deficit {} bytes",
                SERVICE_RESERVE - available
            )
        })?;
        let capacity = (page_budget.min(cache_cap.unwrap_or(usize::MAX)) / PAGE_CHARGE).min(
            routing
                .checked_add(24)
                .ok_or("routing size overflow")?
                .div_ceil(PAGE),
        );
        if capacity == 0 {
            return Err(format!(
                "insufficient memory for one routing page: need at least {} bytes",
                SERVICE_RESERVE + PAGE_CHARGE
            ));
        }
        // Bound request-control allocation even if the caller requests an enormous queue.
        let inflight = inflight.min(capacity).min(4096);
        Ok(Self::Paged(PagingPlan {
            clock: true,
            resident_factors: false,
            capacity,
            inflight,
            reserved_bytes: SERVICE_RESERVE + capacity * PAGE_CHARGE,
        }))
    }
}
#[repr(C)]
#[derive(Default, Debug, Clone, Copy)]
pub struct RoutingStats {
    pub requests: u64,
    pub hits: u64,
    pub merges: u64,
    pub reads: u64,
    pub bytes: u64,
    pub peak_active: u64,
    pub peak_pages: u64,
    pub evictions: u64,
    pub wait_ns: u64,
    pub lock_calls: u64,
    pub lock_wait_ns: u64,
    pub batch_steps: u64,
    pub release_batches: u64,
    pub notifications: u64,
    pub wait_calls: u64,
    pub submit_calls: u64,
    pub submitted_pages: u64,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Request {
    page: u64,
    token: u64,
    bytes: *const u8,
    submitted_bytes: u64,
}
unsafe extern "C" {
    fn qgraph_routing_configure(
        p: *mut c_void,
        pages: *const u64,
        n: usize,
        profile_pages: usize,
    ) -> bool;
    fn qgraph_routing_profile(p: *mut c_void, out: *mut u64, n: usize) -> bool;
    fn qgraph_routing_new_policy(
        path: *const c_char,
        capacity: usize,
        inflight: usize,
        length: u64,
        clock: bool,
    ) -> *mut c_void;
    fn qgraph_routing_client_new() -> *mut c_void;
    fn qgraph_routing_client_delete(p: *mut c_void);
    fn qgraph_routing_batch_step(p: *mut c_void, requests: *mut Request, count: usize) -> bool;
    fn qgraph_routing_batch_release(
        p: *mut c_void,
        requests: *mut Request,
        count: usize,
        all: bool,
    );
    fn qgraph_routing_batch_wait(
        p: *mut c_void,
        client: *mut c_void,
        requests: *mut Request,
        count: usize,
    ) -> bool;

    fn qgraph_routing_delete(p: *mut c_void);
    #[cfg(test)]
    fn qgraph_routing_acquire(p: *mut c_void, page: u64, submitted: *mut bool) -> i64;
    #[cfg(test)]
    fn qgraph_routing_copy(p: *mut c_void, token: usize, out: *mut u8) -> i32;
    #[cfg(test)]
    fn qgraph_routing_release(p: *mut c_void, token: usize);
    #[cfg(test)]
    fn qgraph_routing_epoch(p: *mut c_void) -> u64;
    #[cfg(test)]
    fn qgraph_routing_wait(p: *mut c_void, observed: u64) -> bool;
    fn qgraph_routing_clear(p: *mut c_void) -> bool;
    fn qgraph_routing_stats(p: *mut c_void, out: *mut RoutingStats);
    fn qgraph_routing_error() -> *const c_char;
}
fn failure() -> ANNError {
    ANNError::message(unsafe { CStr::from_ptr(qgraph_routing_error()) }.to_string_lossy())
}
thread_local! {static DELTA: std::cell::Cell<(u64,u64,f64)> = const {std::cell::Cell::new((0,0,0.))};}
pub fn take_delta() -> (u64, u64, f64) {
    DELTA.with(|d| d.replace((0, 0, 0.)))
}
pub struct RoutingReader {
    profile_pages: usize,
    raw: *mut c_void,
    pub plan: PagingPlan,
    length: usize,
}
unsafe impl Send for RoutingReader {}
unsafe impl Sync for RoutingReader {}
impl Drop for RoutingReader {
    fn drop(&mut self) {
        unsafe { qgraph_routing_delete(self.raw) }
    }
}
#[cfg(test)]
struct Ticket<'a> {
    reader: &'a RoutingReader,
    token: usize,
}
#[cfg(test)]
impl Drop for Ticket<'_> {
    fn drop(&mut self) {
        unsafe { qgraph_routing_release(self.reader.raw, self.token) }
    }
}
#[derive(Clone, Copy)]
struct Part {
    page: u64,
    pos: usize,
    is_factor: bool,
    dst: usize,
    src: usize,
    len: usize,
}
struct Workspace {
    codes: Vec<u8>,
    scales: Vec<u8>,
    parts: Vec<Part>,
    requests: Vec<Request>,
    ranges: Vec<(usize, usize)>,
    pending: [usize; 64],
    scored: [bool; 64],
    ready: Vec<u32>,
    client: *mut c_void,
}
impl Default for Workspace {
    fn default() -> Self {
        Self {
            codes: Vec::new(),
            scales: Vec::new(),
            parts: Vec::new(),
            requests: Vec::new(),
            ranges: Vec::new(),
            pending: [0; 64],
            scored: [false; 64],
            ready: Vec::with_capacity(64),
            client: std::ptr::null_mut(),
        }
    }
}
impl Drop for Workspace {
    fn drop(&mut self) {
        if !self.client.is_null() {
            unsafe { qgraph_routing_client_delete(self.client) }
        }
    }
}
thread_local! {static WORKSPACE:std::cell::RefCell<Workspace>=std::cell::RefCell::new(Workspace::default());}
struct BatchGuard<'a> {
    reader: &'a RoutingReader,
    requests: *mut Request,
    count: usize,
}
impl Drop for BatchGuard<'_> {
    fn drop(&mut self) {
        unsafe { qgraph_routing_batch_release(self.reader.raw, self.requests, self.count, true) }
    }
}
impl RoutingReader {
    pub fn new(path: &Path, plan: PagingPlan, length: usize) -> ANNResult<Self> {
        let name = CString::new(path.as_os_str().as_encoded_bytes())
            .map_err(|e| ANNError::message(e.to_string()))?;
        let raw = unsafe {
            qgraph_routing_new_policy(
                name.as_ptr(),
                plan.capacity,
                plan.inflight,
                length as u64,
                plan.clock,
            )
        };
        if raw.is_null() {
            return Err(failure());
        }
        Ok(Self {
            raw,
            plan,
            length,
            profile_pages: 0,
        })
    }
    pub fn configure(
        &mut self,
        hot_path: Option<&Path>,
        profile_pages: usize,
        code_pages: usize,
    ) -> ANNResult<()> {
        use std::io::Read;
        let mut pages = Vec::new();
        if let Some(path) = hot_path {
            let mut file = std::fs::File::open(path)?;
            let length = usize::try_from(file.metadata()?.len())
                .map_err(|_| ANNError::message("hot page file overflow"))?;
            if length % 8 != 0 || length / 8 >= self.plan.capacity {
                return Err(ANNError::message(
                    "hot pages must leave at least one dynamic cache slot",
                ));
            }
            pages.reserve_exact(length / 8);
            for _ in 0..length / 8 {
                let mut bytes = [0; 8];
                file.read_exact(&mut bytes)?;
                let page = u64::from_le_bytes(bytes);
                if page >= code_pages as u64 || pages.last().is_some_and(|&old| old >= page) {
                    return Err(ANNError::message("invalid sorted hot code page IDs"));
                }
                pages.push(page);
            }
        }
        if !unsafe {
            qgraph_routing_configure(self.raw, pages.as_ptr(), pages.len(), profile_pages)
        } {
            return Err(failure());
        }
        self.profile_pages = profile_pages;
        eprintln!(
            "routing_code_policy hot_pages={} profile_pages={} factors_resident={}",
            pages.len(),
            profile_pages,
            self.plan.resident_factors
        );
        Ok(())
    }
    pub fn save_profile(&self, path: &Path) -> ANNResult<()> {
        use std::io::Write;
        if self.profile_pages == 0 {
            return Err(ANNError::message("routing profiling is not enabled"));
        }
        let mut counts = vec![0u64; self.profile_pages];
        if !unsafe { qgraph_routing_profile(self.raw, counts.as_mut_ptr(), counts.len()) } {
            return Err(failure());
        }
        let mut out = std::io::BufWriter::new(std::fs::File::create(path)?);
        for count in counts {
            out.write_all(&count.to_le_bytes())?;
        }
        out.flush()?;
        Ok(())
    }
    pub fn clear(&self) -> ANNResult<()> {
        if unsafe { qgraph_routing_clear(self.raw) } {
            Ok(())
        } else {
            Err(failure())
        }
    }
    pub fn log_stats(&self, phase: &str, width: usize) {
        let s = self.stats();
        eprintln!("routing_stats phase={phase} L={width} requests={} hits={} merges={} reads={} bytes={} peak_active={} peak_pages={} evictions={} capacity={} inflight={} reserved_bytes={} wait_ns={} lock_calls={} lock_wait_ns={} batch_steps={} release_batches={} notifications={} wait_calls={} submit_calls={} submitted_pages={}",s.requests,s.hits,s.merges,s.reads,s.bytes,s.peak_active,s.peak_pages,s.evictions,self.plan.capacity,self.plan.inflight,self.plan.reserved_bytes,s.wait_ns,s.lock_calls,s.lock_wait_ns,s.batch_steps,s.release_batches,s.notifications,s.wait_calls,s.submit_calls,s.submitted_pages);
    }
    pub fn stats(&self) -> RoutingStats {
        let mut out = RoutingStats::default();
        unsafe { qgraph_routing_stats(self.raw, &mut out) };
        out
    }
    /// Calls score as soon as record data is complete, with original window positions.
    /// Returns actual blocked time; physical reads are counted only by the service.
    pub fn gather<F>(
        &self,
        ids: &[u32],
        count: usize,
        msb: usize,
        factors: usize,
        score: F,
    ) -> ANNResult<f64>
    where
        F: FnMut(&[u32], &[u8], &[u8]) -> ANNResult<()>,
    {
        self.gather_with_factors(ids, count, msb, factors, &[], score)
    }
    pub fn gather_with_factors<F>(
        &self,
        ids: &[u32],
        count: usize,
        msb: usize,
        factors: usize,
        resident_factors: &[u8],
        mut score: F,
    ) -> ANNResult<f64>
    where
        F: FnMut(&[u32], &[u8], &[u8]) -> ANNResult<()>,
    {
        if !resident_factors.is_empty()
            && count.checked_mul(factors) != Some(resident_factors.len())
        {
            return Err(ANNError::message(
                "resident routing factors length mismatch",
            ));
        }
        if ids.len() > 64 || msb == 0 || factors == 0 {
            return Err(ANNError::message("invalid routing scratch window"));
        }
        let factor_base = count
            .checked_mul(msb)
            .and_then(|x| x.checked_add(24))
            .ok_or_else(|| ANNError::message("routing offset overflow"))?;
        WORKSPACE.with(|cell| {
            let mut workspace = cell
                .try_borrow_mut()
                .map_err(|_| ANNError::message("routing gather is not reentrant"))?;
            let w = &mut *workspace;
            if w.client.is_null() {
                w.client = unsafe { qgraph_routing_client_new() };
                if w.client.is_null() {
                    return Err(failure());
                }
            }
            w.codes.resize(ids.len() * msb, 0);
            w.scales.resize(ids.len() * factors, 0);
            w.parts.clear();
            w.requests.clear();
            w.ranges.clear();
            w.pending.fill(0);
            w.scored.fill(false);
            for (pos, &id) in ids.iter().enumerate() {
                if id as usize >= count {
                    return Err(ANNError::message("routing candidate out of range"));
                }
                for (is_factor, base, stride) in [(false, 24, msb), (true, factor_base, factors)] {
                    if is_factor && !resident_factors.is_empty() {
                        let begin = id as usize * factors;
                        w.scales[pos * factors..(pos + 1) * factors]
                            .copy_from_slice(&resident_factors[begin..begin + factors]);
                        continue;
                    }
                    let start = (id as usize)
                        .checked_mul(stride)
                        .and_then(|n| n.checked_add(base))
                        .ok_or_else(|| ANNError::message("routing offset overflow"))?;
                    if start
                        .checked_add(stride)
                        .filter(|&end| end <= self.length)
                        .is_none()
                    {
                        return Err(ANNError::message("routing record exceeds file"));
                    }
                    let mut copied = 0;
                    while copied < stride {
                        let offset = start + copied;
                        let len = (PAGE - offset % PAGE).min(stride - copied);
                        w.parts.push(Part {
                            page: (offset / PAGE) as u64,
                            pos,
                            is_factor,
                            dst: pos * stride + copied,
                            src: offset % PAGE,
                            len,
                        });
                        w.pending[pos] += 1;
                        copied += len;
                    }
                }
            }
            w.parts.sort_unstable_by_key(|p| p.page);
            for i in 0..w.parts.len() {
                if i == 0 || w.parts[i].page != w.parts[i - 1].page {
                    if let Some(range) = w.ranges.last_mut() {
                        range.1 = i;
                    }
                    w.requests.push(Request {
                        page: w.parts[i].page,
                        ..Request::default()
                    });
                    w.ranges.push((i, w.parts.len()));
                }
            }
            let _guard = BatchGuard {
                reader: self,
                requests: w.requests.as_mut_ptr(),
                count: w.requests.len(),
            };
            let mut remaining = w.requests.len();
            let mut blocked = 0.;
            while remaining > 0 {
                if !unsafe {
                    qgraph_routing_batch_step(self.raw, w.requests.as_mut_ptr(), w.requests.len())
                } {
                    return Err(failure());
                }
                let mut reads = 0;
                let mut bytes = 0;
                let mut progress = false;
                for (i, request) in w.requests.iter().enumerate() {
                    if request.submitted_bytes > 0 {
                        reads += 1;
                        bytes += request.submitted_bytes;
                    }
                    if request.bytes.is_null() {
                        continue;
                    }
                    // The batch owns a pin. The service cannot evict/write this page
                    // until batch_release, so only the required bytes are copied without mu.
                    let page = unsafe { std::slice::from_raw_parts(request.bytes, PAGE) };
                    let (begin, end) = w.ranges[i];
                    for part in &w.parts[begin..end] {
                        let output = if part.is_factor {
                            &mut w.scales
                        } else {
                            &mut w.codes
                        };
                        output[part.dst..part.dst + part.len]
                            .copy_from_slice(&page[part.src..part.src + part.len]);
                        w.pending[part.pos] -= 1;
                    }
                    remaining -= 1;
                    progress = true;
                }
                if reads > 0 {
                    DELTA.with(|d| {
                        let (r, b, t) = d.get();
                        d.set((r + reads, b + bytes, t));
                    });
                }
                if progress {
                    unsafe {
                        qgraph_routing_batch_release(
                            self.raw,
                            w.requests.as_mut_ptr(),
                            w.requests.len(),
                            false,
                        )
                    };
                }
                w.ready.clear();
                for pos in 0..ids.len() {
                    if w.pending[pos] == 0 && !w.scored[pos] {
                        w.ready.push(pos as u32);
                    }
                }
                // Coalesce tiny completion fragments, but never wait for more data
                // after the last page. Results are scattered to original positions.
                if w.ready.len() >= 8 || (remaining == 0 && !w.ready.is_empty()) {
                    score(&w.ready, &w.codes, &w.scales)?;
                    for &pos in &w.ready {
                        w.scored[pos as usize] = true;
                    }
                }
                if !progress && remaining > 0 {
                    let start = Instant::now();
                    if !unsafe {
                        qgraph_routing_batch_wait(
                            self.raw,
                            w.client,
                            w.requests.as_mut_ptr(),
                            w.requests.len(),
                        )
                    } {
                        return Err(failure());
                    }
                    blocked += start.elapsed().as_secs_f64() * 1e6;
                }
            }
            DELTA.with(|d| {
                let (r, b, t) = d.get();
                d.set((r, b, t + blocked));
            });
            Ok(blocked)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn admission_boundaries() {
        assert!(matches!(
            RoutingPlan::choose(100, 100, None, 64).unwrap(),
            RoutingPlan::Resident
        ));
        assert!(matches!(
            RoutingPlan::choose(101, 100, None, 64).unwrap(),
            RoutingPlan::Resident
        ));
        assert!(RoutingPlan::choose(99, 100, None, 64).is_err());
        let RoutingPlan::Paged(p) =
            RoutingPlan::choose(SERVICE_RESERVE + PAGE_CHARGE, 100_000_000, None, 64).unwrap()
        else {
            panic!()
        };
        assert_eq!((p.capacity, p.inflight), (1, 1));
    }
    #[test]
    fn protected_hot_page_survives_churn_and_profile_counts_hits() {
        for clock in [false, true] {
            let path =
                std::env::temp_dir().join(format!("routing-hot-{}-{clock}", std::process::id()));
            let hot = path.with_extension("hot");
            let output = path.with_extension("counts");
            std::fs::write(&path, vec![7u8; PAGE * 4]).unwrap();
            std::fs::write(&hot, 0u64.to_le_bytes()).unwrap();
            let mut reader = RoutingReader::new(
                &path,
                PagingPlan {
                    clock,
                    resident_factors: true,
                    capacity: 2,
                    inflight: 2,
                    reserved_bytes: 0,
                },
                PAGE * 4,
            )
            .unwrap();
            reader.configure(Some(&hot), 4, 4).unwrap();
            for page in [0, 1, 2, 0, 3, 0] {
                let mut submitted = false;
                let token = unsafe { qgraph_routing_acquire(reader.raw, page, &mut submitted) };
                assert!(token > 0);
                let ticket = Ticket {
                    reader: &reader,
                    token: token as usize,
                };
                let mut bytes = [0u8; PAGE];
                loop {
                    let epoch = unsafe { qgraph_routing_epoch(reader.raw) };
                    let status = unsafe {
                        qgraph_routing_copy(reader.raw, ticket.token, bytes.as_mut_ptr())
                    };
                    assert!(status >= 0);
                    if status == 1 {
                        break;
                    }
                    assert!(unsafe { qgraph_routing_wait(reader.raw, epoch) });
                }
                assert_eq!(bytes, [7u8; PAGE]);
                drop(ticket);
            }
            assert_eq!(reader.stats().reads, 4);
            reader.save_profile(&output).unwrap();
            let data = std::fs::read(&output).unwrap();
            let counts = data
                .chunks_exact(8)
                .map(|b| u64::from_le_bytes(b.try_into().unwrap()))
                .collect::<Vec<_>>();
            assert_eq!(counts, [3, 1, 1, 1]);
            reader.clear().unwrap();
            drop(reader);
            std::fs::remove_file(path).unwrap();
            std::fs::remove_file(hot).unwrap();
            std::fs::remove_file(output).unwrap();
        }
    }
    #[test]
    fn merged_pins_backpressure_and_failure() {
        let path = std::env::temp_dir().join(format!("routing-merge-{}.bin", std::process::id()));
        std::fs::write(&path, vec![7u8; PAGE * 3]).unwrap();
        let reader = RoutingReader::new(
            &path,
            PagingPlan {
                clock: true,
                resident_factors: false,
                capacity: 1,
                inflight: 1,
                reserved_bytes: 0,
            },
            PAGE * 3,
        )
        .unwrap();
        let mut submitted = false;
        let a = unsafe { qgraph_routing_acquire(reader.raw, 0, &mut submitted) };
        assert!(a > 0 && submitted);
        let b = unsafe { qgraph_routing_acquire(reader.raw, 0, &mut submitted) };
        assert_eq!(a, b);
        assert!(!submitted);
        let first = Ticket {
            reader: &reader,
            token: a as usize,
        };
        let second = Ticket {
            reader: &reader,
            token: b as usize,
        };
        let mut bytes = [0u8; PAGE];
        loop {
            let epoch = unsafe { qgraph_routing_epoch(reader.raw) };
            if unsafe { qgraph_routing_copy(reader.raw, first.token, bytes.as_mut_ptr()) } == 1 {
                break;
            }
            assert!(unsafe { qgraph_routing_wait(reader.raw, epoch) });
        }
        assert_eq!(
            unsafe { qgraph_routing_acquire(reader.raw, 1, &mut submitted) },
            0
        );
        drop(first);
        assert_eq!(
            unsafe { qgraph_routing_acquire(reader.raw, 1, &mut submitted) },
            0
        );
        drop(second);
        assert_eq!(reader.stats().reads, 1);
        std::fs::OpenOptions::new()
            .write(true)
            .open(&path)
            .unwrap()
            .set_len(PAGE as u64)
            .unwrap();
        let token = unsafe { qgraph_routing_acquire(reader.raw, 2, &mut submitted) };
        assert!(token > 0);
        let ticket = Ticket {
            reader: &reader,
            token: token as usize,
        };
        loop {
            let epoch = unsafe { qgraph_routing_epoch(reader.raw) };
            let status =
                unsafe { qgraph_routing_copy(reader.raw, ticket.token, bytes.as_mut_ptr()) };
            if status < 0 {
                break;
            }
            assert_eq!(status, 0);
            if !unsafe { qgraph_routing_wait(reader.raw, epoch) } {
                break;
            }
        }
        assert!(unsafe { qgraph_routing_acquire(reader.raw, 0, &mut submitted) } < 0);
        drop(ticket);
        drop(reader);
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn one_page_crossing_tail_and_parallel() {
        let path = std::env::temp_dir().join(format!("routing-test-{}.bin", std::process::id()));
        let count = 100;
        let msb = 129;
        let factors = 20;
        let bytes = (0..24 + count * (msb + factors))
            .map(|i| (i % 251) as u8)
            .collect::<Vec<_>>();
        std::fs::write(&path, &bytes).unwrap();
        let reader = RoutingReader::new(
            &path,
            PagingPlan {
                clock: true,
                resident_factors: false,
                capacity: 1,
                inflight: 1,
                reserved_bytes: SERVICE_RESERVE + PAGE_CHARGE,
            },
            bytes.len(),
        )
        .unwrap();
        std::thread::scope(|s| {
            for _ in 0..8 {
                let r = &reader;
                let b = &bytes;
                s.spawn(move || {
                    let ids = [99, 0, 31, 63, 31];
                    let mut checked = 0;
                    r.gather(&ids, count, msb, factors, |ready, c, f| {
                        for &pos in ready {
                            let p = pos as usize;
                            let id = ids[p] as usize;
                            assert_eq!(
                                &c[p * msb..(p + 1) * msb],
                                &b[24 + id * msb..24 + (id + 1) * msb]
                            );
                            let off = 24 + count * msb + id * factors;
                            assert_eq!(&f[p * factors..(p + 1) * factors], &b[off..off + factors]);
                            checked += 1;
                        }
                        Ok(())
                    })
                    .unwrap();
                    assert_eq!(checked, ids.len());
                });
            }
        });
        assert_eq!(reader.stats().peak_active, 1);
        assert_eq!(reader.stats().peak_pages, 1);
        drop(reader);
        std::fs::remove_file(path).unwrap();
    }
}
