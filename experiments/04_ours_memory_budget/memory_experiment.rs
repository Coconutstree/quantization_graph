//! Experimental caches only. One dataset/configuration per process.
use crate::disk_port::{locality::LocalityLayout, ours_port::OursPreparedQuery, PAGE_SIZE};
use std::{
    collections::{HashMap, VecDeque},
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex, OnceLock,
    },
};
struct Pages {
    map: HashMap<(u32, u64), Box<[u8; PAGE_SIZE]>>,
    fifo: VecDeque<(u32, u64)>,
    cap: usize,
}
// Counts are reset after warmup; scores weight every L equally.
struct Profile {
    counts: [Vec<u64>; 3],
    scores: [Vec<f64>; 3],
    widths: Vec<usize>,
}
impl Profile {
    fn new(n: usize) -> Self {
        Self {
            counts: std::array::from_fn(|_| vec![0; n]),
            scores: std::array::from_fn(|_| vec![0.0; n]),
            widths: vec![],
        }
    }
    fn reset(&mut self) {
        for v in &mut self.counts {
            v.fill(0);
        }
    }
    fn accumulate(&mut self, width: usize) {
        assert!(!self.widths.contains(&width), "duplicate profile width");
        for k in 0..3 {
            let total: u64 = self.counts[k].iter().sum();
            if total > 0 {
                for (score, &count) in self.scores[k].iter_mut().zip(&self.counts[k]) {
                    *score += count as f64 / total as f64;
                }
            }
        }
        self.widths.push(width);
    }
}
// Operation attribution: these are submitted I/O counters, not causal savings.
static REQUESTS: [AtomicU64; 3] = [const { AtomicU64::new(0) }; 3];
static IO_REQUESTS: [AtomicU64; 3] = [const { AtomicU64::new(0) }; 3];
static IO_PAGES: [AtomicU64; 3] = [const { AtomicU64::new(0) }; 3];
static IO_BYTES: [AtomicU64; 3] = [const { AtomicU64::new(0) }; 3];
fn page_allowance(mode: &str, available: usize, static_bytes: usize) -> usize {
    match mode {
        "pages" | "nav_pages" | "hybrid" | "nav_hybrid" => {
            available.checked_sub(static_bytes).unwrap()
        }
        _ => 0,
    }
}
pub struct State {
    pages: Vec<Mutex<Pages>>,
    graph_map: Vec<u32>,
    graph: Vec<u32>,
    degree: usize,
    record_map: Vec<u32>,
    records: Vec<u8>,
    cb: usize,
    rb: usize,
    nav_ids: Vec<u32>,
    nav_edges: Vec<Vec<usize>>,
    profile: Option<Mutex<Profile>>,
    pub allocated: usize,
    pub graph_count: usize,
    pub record_count: usize,
    mode: String,
    stats_dir: std::path::PathBuf,
    profile_dir: std::path::PathBuf,
    setup_seconds: f64,
}
static STATE: OnceLock<State> = OnceLock::new();
pub static PAGE_HITS: AtomicU64 = AtomicU64::new(0);
pub static GRAPH_HITS: AtomicU64 = AtomicU64::new(0);
pub static RECORD_HITS: AtomicU64 = AtomicU64::new(0);
pub static NAV_CHECKS: AtomicU64 = AtomicU64::new(0);
fn u32read(f: &mut File) -> u32 {
    let mut b = [0; 4];
    f.read_exact(&mut b).unwrap();
    u32::from_le_bytes(b)
}
fn ranks(path: &Path, n: usize) -> Vec<usize> {
    let mut f = File::open(path).unwrap();
    assert_eq!(f.metadata().unwrap().len(), (n * 8) as u64);
    let counts = (0..n)
        .map(|_| {
            let mut b = [0; 8];
            f.read_exact(&mut b).unwrap();
            let v = f64::from_le_bytes(b);
            assert!(v.is_finite() && v >= 0.0);
            v
        })
        .collect::<Vec<_>>();
    let mut ids = (0..n).filter(|i| counts[*i] > 0.0).collect::<Vec<_>>();
    ids.sort_unstable_by(|a, b| counts[*b].total_cmp(&counts[*a]).then_with(|| a.cmp(b)));
    ids
}
pub fn setup(
    mode: &str,
    budget: usize,
    profile: &Path,
    stats_dir: &Path,
    layout: &LocalityLayout,
) -> &'static State {
    let started = std::time::Instant::now();
    let n = layout.slots.len();
    let record_bytes = layout.compact_bytes + layout.residual_bytes;
    let root = &layout.dir;
    let mut s = State {
        pages: vec![],
        graph_map: vec![],
        graph: vec![],
        degree: layout.max_degree,
        record_map: vec![],
        records: vec![],
        cb: layout.compact_bytes,
        rb: layout.residual_bytes,
        nav_ids: vec![],
        nav_edges: vec![],
        profile: None,
        allocated: 0,
        graph_count: 0,
        record_count: 0,
        mode: mode.into(),
        stats_dir: stats_dir.into(),
        profile_dir: profile.into(),
        setup_seconds: 0.0,
    };
    if mode == "profile" {
        s.profile = Some(Mutex::new(Profile::new(n)));
    }
    // Reserve navigation metadata inside the same optional-memory allowance.
    let nav_bytes = if mode.starts_with("nav") {
        let mut f = File::open(profile.join("nav.bin")).unwrap();
        let count = u32read(&mut f) as usize;
        let degree = u32read(&mut f) as usize;
        count * (4 + 24 + degree * 8)
    } else {
        0
    };
    assert!(budget >= nav_bytes, "navigation exceeds available memory");
    let available = budget - nav_bytes;
    let graph_budget = match mode {
        "hot_graph" => available,
        "hybrid" | "nav_hybrid" => available / 4,
        _ => 0,
    };
    let record_budget = match mode {
        "hot_payload" => available,
        "hybrid" | "nav_hybrid" => available / 4,
        "full_payload" => n * record_bytes + n * 4,
        _ => 0,
    };
    if mode == "full_payload" {
        assert!(
            record_budget <= available,
            "full payload does not fit remaining budget"
        );
    }
    if graph_budget > n * 4 {
        let ids = ranks(&profile.join("graph_scores.f64"), n);
        let count = ((graph_budget - n * 4) / ((layout.max_degree + 1) * 4)).min(ids.len());
        s.graph_map = if count > 0 { vec![u32::MAX; n] } else { vec![] };
        s.graph = vec![0; count * (layout.max_degree + 1)];
        let mut f = File::open(root.join("graph_compact.pages")).unwrap();
        let mut b = vec![0; layout.graph_bytes];
        for (slot, &id) in ids.iter().take(count).enumerate() {
            let size = layout.graph_bytes + layout.compact_bytes;
            let physical = layout.slots[id] as usize;
            let per_page = PAGE_SIZE / size;
            f.seek(SeekFrom::Start(
                (physical / per_page * PAGE_SIZE + physical % per_page * size) as u64,
            ))
            .unwrap();
            f.read_exact(&mut b).unwrap();
            for (j, v) in b.chunks_exact(4).enumerate() {
                s.graph[slot * (layout.max_degree + 1) + j] =
                    u32::from_le_bytes(v.try_into().unwrap());
            }
            s.graph_map[id] = slot as u32;
        }
        s.graph_count = count;
        s.allocated += s.graph_map.len() * 4 + s.graph.len() * 4;
    }
    if record_budget > n * 4 {
        let ids = if mode == "full_payload" {
            (0..n).collect()
        } else {
            ranks(&profile.join("payload_scores.f64"), n)
        };
        let count = ((record_budget - n * 4) / record_bytes).min(ids.len());
        s.record_map = if count > 0 { vec![u32::MAX; n] } else { vec![] };
        s.records = vec![0; count * record_bytes];
        let mut f = File::open(root.join("graph_compact.pages")).unwrap();
        let mut residual = File::open(root.join("residual.pages")).unwrap();
        for (slot, &id) in ids.iter().take(count).enumerate() {
            let physical = layout.slots[id] as usize;
            let size = layout.graph_bytes + layout.compact_bytes;
            let per_page = PAGE_SIZE / size;
            f.seek(SeekFrom::Start(
                (physical / per_page * PAGE_SIZE + physical % per_page * size + layout.graph_bytes)
                    as u64,
            ))
            .unwrap();
            f.read_exact(&mut s.records[slot * record_bytes..slot * record_bytes + s.cb])
                .unwrap();
            let per_page = PAGE_SIZE / s.rb;
            residual
                .seek(SeekFrom::Start(
                    (physical / per_page * PAGE_SIZE + physical % per_page * s.rb) as u64,
                ))
                .unwrap();
            residual
                .read_exact(&mut s.records[slot * record_bytes + s.cb..(slot + 1) * record_bytes])
                .unwrap();
            s.record_map[id] = slot as u32;
        }
        s.record_count = count;
        s.allocated += s.record_map.len() * 4 + s.records.len();
    }
    let page_budget = page_allowance(mode, available, s.allocated);
    if page_budget > 0 {
        // FIFO, 16 shards; reserve 128 bytes/page for table/queue metadata.
        let cap = page_budget / (PAGE_SIZE + 128) / 16;
        for _ in 0..16 {
            s.pages.push(Mutex::new(Pages {
                map: HashMap::with_capacity(cap),
                fifo: VecDeque::with_capacity(cap),
                cap,
            }));
        }
        s.allocated += cap * 16 * (PAGE_SIZE + 128);
    }
    if mode.starts_with("nav") {
        let mut f = File::open(profile.join("nav.bin")).unwrap();
        let count = u32read(&mut f) as usize;
        let degree = u32read(&mut f) as usize;
        s.nav_ids = (0..count).map(|_| u32read(&mut f)).collect();
        s.nav_edges = (0..count)
            .map(|_| (0..degree).map(|_| u32read(&mut f) as usize).collect())
            .collect();
        s.allocated += count * (4 + 24 + degree * 8);
    }
    assert!(s.allocated <= budget, "cache accounting exceeds allowance");
    s.setup_seconds = started.elapsed().as_secs_f64();
    STATE.set(s).ok().expect("one configuration per process");
    STATE.get().unwrap()
}
pub fn page_get(file: u32, page: u64) -> Option<Box<[u8; PAGE_SIZE]>> {
    let s = STATE.get()?;
    if s.pages.is_empty() {
        return None;
    }
    let p = s.pages[((page.wrapping_mul(31) + file as u64) % 16) as usize]
        .lock()
        .unwrap();
    p.map.get(&(file, page)).map(|b| {
        PAGE_HITS.fetch_add(1, Ordering::Relaxed);
        b.clone()
    })
}
pub fn page_put(file: u32, page: u64, bytes: &[u8; PAGE_SIZE]) {
    let Some(s) = STATE.get() else { return };
    if s.pages.is_empty() {
        return;
    }
    let mut p = s.pages[((page.wrapping_mul(31) + file as u64) % 16) as usize]
        .lock()
        .unwrap();
    let key = (file, page);
    if p.cap == 0 || p.map.contains_key(&key) {
        return;
    }
    if p.map.len() == p.cap {
        let old = p.fifo.pop_front().unwrap();
        p.map.remove(&old);
    }
    p.map.insert(key, Box::new(*bytes));
    p.fifo.push_back(key);
}
pub fn record_access(kind: usize, ids: &[u32]) {
    REQUESTS[kind].fetch_add(ids.len() as u64, Ordering::Relaxed);
    if let Some(p) = STATE.get().and_then(|s| s.profile.as_ref()) {
        let mut p = p.lock().unwrap();
        for &id in ids {
            if let Some(v) = p.counts[kind].get_mut(id as usize) {
                *v += 1;
            }
        }
    }
}
pub fn record_io(kind: usize, stats: &crate::disk_port::IoStats) {
    IO_REQUESTS[kind].fetch_add(stats.requests, Ordering::Relaxed);
    IO_PAGES[kind].fetch_add(stats.sectors_4k, Ordering::Relaxed);
    IO_BYTES[kind].fetch_add(stats.bytes_read, Ordering::Relaxed);
}
pub fn save_profile(dir: &Path) {
    let p = STATE
        .get()
        .unwrap()
        .profile
        .as_ref()
        .unwrap()
        .lock()
        .unwrap();
    assert!(!p.widths.is_empty());
    for (name, scores) in [
        ("graph", &p.scores[0]),
        ("compact", &p.scores[1]),
        ("residual", &p.scores[2]),
    ] {
        let mut f = File::create(dir.join(format!("{name}_scores.f64"))).unwrap();
        for &v in scores {
            f.write_all(&(v / p.widths.len() as f64).to_le_bytes())
                .unwrap();
        }
    }
    let mut f = File::create(dir.join("payload_scores.f64")).unwrap();
    for (&c, &r) in p.scores[1].iter().zip(&p.scores[2]) {
        f.write_all(&((c + r) / (2.0 * p.widths.len() as f64)).to_le_bytes())
            .unwrap();
    }
    std::fs::write(
        dir.join("profile_policy.json"),
        serde_json::to_vec_pretty(&serde_json::json!({
            "schema": 2, "widths": p.widths, "exclude_warmup": true,
            "normalization": "per_kind_per_L_request_total_then_equal_L_mean",
            "payload_score": "0.5*compact_score+0.5*residual_score",
            "ranking": "descending_score_then_ascending_node_id",
            "scope": "validation_only; request_frequency_proxy_not_IO_savings"
        }))
        .unwrap(),
    )
    .unwrap();
}
pub fn neighbors(id: u32) -> Option<Vec<u32>> {
    let s = STATE.get()?;
    let slot = *s.graph_map.get(id as usize)?;
    if slot == u32::MAX {
        return None;
    }
    GRAPH_HITS.fetch_add(1, Ordering::Relaxed);
    let begin = slot as usize * (s.degree + 1);
    let count = s.graph[begin] as usize;
    Some(s.graph[begin + 1..begin + 1 + count].to_vec())
}
pub fn has_records() -> bool {
    STATE.get().is_some_and(|s| !s.record_map.is_empty())
}
pub fn contains_record(id: u32) -> bool {
    STATE
        .get()
        .and_then(|s| s.record_map.get(id as usize))
        .is_some_and(|v| *v != u32::MAX)
}
pub fn record_sizes() -> (usize, usize) {
    let s = STATE.get().unwrap();
    (s.cb, s.rb)
}
pub fn record(id: u32) -> Option<(&'static [u8], &'static [u8])> {
    let s = STATE.get()?;
    let slot = *s.record_map.get(id as usize)?;
    if slot == u32::MAX {
        return None;
    }
    RECORD_HITS.fetch_add(1, Ordering::Relaxed);
    let start = slot as usize * (s.cb + s.rb);
    Some((
        &s.records[start..start + s.cb],
        &s.records[start + s.cb..start + s.cb + s.rb],
    ))
}
pub fn entry(q: &OursPreparedQuery<'_>) -> diskann::ANNResult<u32> {
    let Some(s) = STATE.get() else { return Ok(0) };
    if s.nav_ids.is_empty() {
        return Ok(0);
    }
    // Best-first beam on representative kNN graph, using existing DB1 estimates.
    let mut seen = vec![false; s.nav_ids.len()];
    let mut pool = vec![(f32::INFINITY, 0usize, false)];
    seen[0] = true;
    for _ in 0..32 {
        let Some(pos) = pool.iter().position(|x| !x.2) else {
            break;
        };
        pool[pos].2 = true;
        let node = pool[pos].1;
        let fresh = s.nav_edges[node]
            .iter()
            .copied()
            .filter(|&i| {
                if seen[i] {
                    false
                } else {
                    seen[i] = true;
                    true
                }
            })
            .collect::<Vec<_>>();
        let ids = fresh.iter().map(|&i| s.nav_ids[i]).collect::<Vec<_>>();
        let scores = q.paper_estimates(&ids, 0.0)?;
        NAV_CHECKS.fetch_add(ids.len() as u64, Ordering::Relaxed);
        for (i, v) in fresh.into_iter().zip(scores) {
            pool.push((v.lower_bound, i, false));
        }
        pool.sort_unstable_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        pool.truncate(16);
    }
    Ok(s.nav_ids[pool[0].1])
}

pub fn begin_config() {
    if let Some(s) = STATE.get() {
        for p in &s.pages {
            let mut p = p.lock().unwrap();
            p.map.clear();
            p.fifo.clear();
        }
    }
    reset_counters();
}
fn reset_counters() {
    for group in [&REQUESTS, &IO_REQUESTS, &IO_PAGES, &IO_BYTES] {
        for c in group {
            c.store(0, Ordering::Relaxed);
        }
    }
    for c in [&PAGE_HITS, &GRAPH_HITS, &RECORD_HITS, &NAV_CHECKS] {
        c.store(0, Ordering::Relaxed);
    }
}
pub fn measurement_begin() {
    reset_counters();
    if let Some(p) = STATE.get().and_then(|s| s.profile.as_ref()) {
        p.lock().unwrap().reset();
    }
}
pub fn finish_config(width: usize) {
    if let Some(s) = STATE.get() {
        let mut operations = serde_json::Map::new();
        for (k, name) in ["graph", "compact", "residual"].iter().enumerate() {
            operations.insert(
                name.to_string(),
                serde_json::json!({
                    "logical_requests": REQUESTS[k].load(Ordering::Relaxed),
                    "io_requests": IO_REQUESTS[k].load(Ordering::Relaxed),
                    "read_pages": IO_PAGES[k].load(Ordering::Relaxed),
                    "read_bytes": IO_BYTES[k].load(Ordering::Relaxed)
                }),
            );
        }
        let text = serde_json::json!({"mode":s.mode,"width":width,"cache_reserved_bytes":s.allocated,
            "graph_nodes":s.graph_count,"payload_nodes":s.record_count,"setup_seconds":s.setup_seconds,
            "page_hits":PAGE_HITS.load(Ordering::Relaxed),"graph_hits":GRAPH_HITS.load(Ordering::Relaxed),
            "record_hits":RECORD_HITS.load(Ordering::Relaxed),"nav_checks":NAV_CHECKS.load(Ordering::Relaxed),
            "page_capacity":s.pages.iter().map(|p|p.lock().unwrap().cap).sum::<usize>(),
            "operations":operations});
        std::fs::write(
            s.stats_dir.join(format!("L{width}.json")),
            serde_json::to_vec_pretty(&text).unwrap(),
        )
        .unwrap();
        if let Some(p) = &s.profile {
            let mut p = p.lock().unwrap();
            for (k, name) in ["graph", "compact", "residual"].iter().enumerate() {
                let mut f = File::create(s.profile_dir.join(format!("L{width}_{name}_counts.u64")))
                    .unwrap();
                for v in &p.counts[k] {
                    f.write_all(&v.to_le_bytes()).unwrap();
                }
            }
            p.accumulate(width);
        }
    }
}
pub fn finish() {
    if let Some(s) = STATE.get() {
        if s.profile.is_some() {
            save_profile(&s.profile_dir);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn profile_normalizes_each_width_and_separates_record_kinds() {
        let mut p = Profile::new(2);
        p.counts[0] = vec![1000, 0]; // Simulated warmup must contribute nothing.
        p.reset();
        p.counts[0] = vec![1, 0];
        p.counts[1] = vec![0, 2];
        p.counts[2] = vec![3, 0];
        p.accumulate(10);
        p.reset();
        p.counts[0] = vec![0, 100];
        p.accumulate(580);
        assert_eq!(p.scores[0], vec![1.0, 1.0]);
        assert_eq!(p.scores[1], vec![0.0, 1.0]);
        assert_eq!(p.scores[2], vec![1.0, 0.0]);
        assert_eq!(page_allowance("hybrid", 1000, 150), 850);
        assert_eq!(page_allowance("nav_hybrid", 900, 0), 900);
        assert_eq!(page_allowance("hot_graph", 1000, 150), 0);
    }
    #[test]
    fn locality_cache_preserves_mapping_mixed_misses_and_namespaces() {
        let dir = std::env::temp_dir().join(format!(
            "ours_memory_v2_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&dir).unwrap();
        std::fs::write(
            dir.join("id_to_slot.u32"),
            [1u32, 0]
                .iter()
                .flat_map(|x| x.to_le_bytes())
                .collect::<Vec<_>>(),
        )
        .unwrap();
        let mut combined = vec![0; PAGE_SIZE];
        for (slot, id) in [1u32, 0].into_iter().enumerate() {
            let b = slot * 12;
            combined[b..b + 4].copy_from_slice(&1u32.to_le_bytes());
            combined[b + 4..b + 8].copy_from_slice(&(1 - id).to_le_bytes());
            combined[b + 8..b + 12].fill(id as u8 + 10);
        }
        std::fs::write(dir.join("graph_compact.pages"), combined).unwrap();
        let mut rest = vec![0; PAGE_SIZE];
        rest[..4].fill(21);
        rest[4..8].fill(20);
        std::fs::write(dir.join("residual.pages"), rest).unwrap();
        let s = State {
            pages: (0..16)
                .map(|_| {
                    Mutex::new(Pages {
                        map: HashMap::new(),
                        fifo: VecDeque::new(),
                        cap: 1,
                    })
                })
                .collect(),
            graph_map: vec![0, u32::MAX],
            graph: vec![1, 1],
            degree: 1,
            record_map: vec![0, u32::MAX],
            records: vec![10, 10, 10, 10, 20, 20, 20, 20],
            cb: 4,
            rb: 4,
            nav_ids: vec![],
            nav_edges: vec![],
            profile: Some(Mutex::new(Profile::new(2))),
            allocated: 0,
            graph_count: 1,
            record_count: 1,
            mode: "test".into(),
            stats_dir: dir.clone(),
            profile_dir: dir.clone(),
            setup_seconds: 0.0,
        };
        assert!(STATE.set(s).is_ok());
        page_put(0, 0, &[7; PAGE_SIZE]);
        assert!(page_get(2, 0).is_none());
        assert_eq!(page_get(0, 0).unwrap()[0], 7);
        page_put(0, 16, &[8; PAGE_SIZE]);
        assert!(page_get(0, 0).is_none());
        assert_eq!(page_get(0, 16).unwrap()[0], 8);
        begin_config();
        record_access(0, &[0, 0, 1]);
        measurement_begin();
        assert_eq!(REQUESTS[0].load(Ordering::Relaxed), 0);
        assert_eq!(
            STATE
                .get()
                .unwrap()
                .profile
                .as_ref()
                .unwrap()
                .lock()
                .unwrap()
                .counts[0],
            vec![0, 0]
        );
        let layout = std::sync::Arc::new(LocalityLayout::load(&dir, 2, 8, 4, 4, 1).unwrap());
        let mut reader = crate::disk_port::locality::LocalityReader::new(layout).unwrap();
        assert_eq!(
            reader.compact(&[0, 1, 0]).unwrap().0,
            vec![10, 10, 10, 10, 11, 11, 11, 11, 10, 10, 10, 10]
        );
        assert_eq!(
            reader.residual(&[0, 1, 0]).unwrap().0,
            vec![20, 20, 20, 20, 21, 21, 21, 21, 20, 20, 20, 20]
        );
        assert_eq!(reader.neighbors(0).unwrap().0, vec![1]);
        assert_eq!(reader.neighbors(1).unwrap().0, vec![0]);
        assert_eq!(reader.compact(&[0]).unwrap().1.requests, 0);
        assert!(reader.compact(&[2]).is_err());
        assert_eq!(REQUESTS[0].load(Ordering::Relaxed), 2);
        assert_eq!(IO_PAGES[1].load(Ordering::Relaxed), 1);
        assert_eq!(IO_PAGES[2].load(Ordering::Relaxed), 1);
        assert_eq!(IO_PAGES[0].load(Ordering::Relaxed), 0);
        finish_config(30);
        save_profile(&dir);
        assert_eq!(ranks(&dir.join("graph_scores.f64"), 2), vec![0, 1]);
        let mut bytes = vec![];
        for v in [0.0f64, 0.25, 0.25, 0.5] {
            bytes.extend(v.to_le_bytes());
        }
        std::fs::write(dir.join("rank_test.f64"), bytes).unwrap();
        assert_eq!(ranks(&dir.join("rank_test.f64"), 4), vec![3, 1, 2]);
        let result = std::fs::read_to_string(dir.join("L30.json")).unwrap();
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&result).unwrap()["width"],
            30
        );
        drop(reader);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
