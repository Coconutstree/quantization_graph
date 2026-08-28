//! Exact experiment-02 ExRaBitQ search port for the 05B shared disk graph.
//!
//! Only storage is changed: the resident DB1/factor sidecar is evaluated by
//! the original INT8-query C++ kernels, while compact 4-bit and residual
//! records are either resident or fetched from fixed 4-KiB payload pages.

use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

use diskann::{ANNError, ANNResult};

use crate::config::QueryCoarseCodec;
use crate::ours_diskann::{
    QueryComputer, RabitqDistanceInterval, RabitqPaperEstimate, RabitqSpace,
};

use super::{BackendFactory, DirectAioHandle, IoStats, PAGE_SIZE};

const META_MAGIC: &[u8; 8] = b"QG05OUR1";
const SIDECAR_MAGIC: &[u8; 8] = b"QG05OSC1";
const DIST_MODE_RECOMPUTE_FULL: u8 = 0;
const DIST_MODE_REUSE_QUANTIZED_MSB: u8 = 2;
const DIST_BATCH_SIZE: usize = 64;

fn ann_error(message: impl Into<String>) -> ANNError {
    ANNError::message(message.into())
}

fn read_u32(reader: &mut impl Read) -> std::io::Result<u32> {
    let mut value = [0_u8; 4];
    reader.read_exact(&mut value)?;
    Ok(u32::from_le_bytes(value))
}

fn read_u64(reader: &mut impl Read) -> std::io::Result<u64> {
    let mut value = [0_u8; 8];
    reader.read_exact(&mut value)?;
    Ok(u64::from_le_bytes(value))
}

fn read_f32(reader: &mut impl Read) -> std::io::Result<f32> {
    let mut value = [0_u8; 4];
    reader.read_exact(&mut value)?;
    Ok(f32::from_le_bytes(value))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OursAblation {
    Full4ResidentNoGate,
    Db1ResidentFull4Ssd,
    Db1Coalescing,
    Db1CoalescingReuse,
}

impl OursAblation {
    pub const ALL: [Self; 4] = [
        Self::Full4ResidentNoGate,
        Self::Db1ResidentFull4Ssd,
        Self::Db1Coalescing,
        Self::Db1CoalescingReuse,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Full4ResidentNoGate => "full4-resident/no-gate",
            Self::Db1ResidentFull4Ssd => "db1-resident/full4-on-ssd",
            Self::Db1Coalescing => "db1+coalescing",
            Self::Db1CoalescingReuse => "db1+coalescing+reuse",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "full4-resident/no-gate" => Some(Self::Full4ResidentNoGate),
            "db1-resident/full4-on-ssd" => Some(Self::Db1ResidentFull4Ssd),
            "db1+coalescing" => Some(Self::Db1Coalescing),
            "db1+coalescing+reuse" => Some(Self::Db1CoalescingReuse),
            _ => None,
        }
    }

    pub fn uses_gate(self) -> bool {
        self != Self::Full4ResidentNoGate
    }

    pub fn payload_coalescing(self) -> bool {
        matches!(self, Self::Db1Coalescing | Self::Db1CoalescingReuse)
    }

    pub fn payload_reuse(self) -> bool {
        self == Self::Db1CoalescingReuse
    }

    pub fn query_codec(self) -> QueryCoarseCodec {
        if self.uses_gate() {
            QueryCoarseCodec::Int8
        } else {
            QueryCoarseCodec::Full
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OursPayloadLayout {
    pub record_count: usize,
    pub compact_bytes: usize,
    pub residual_bytes: usize,
    pub record_bytes: usize,
    pub page_count: usize,
}

impl OursPayloadLayout {
    fn pages_for(&self, id: u32) -> ANNResult<std::ops::RangeInclusive<u64>> {
        if id as usize >= self.record_count {
            return Err(ann_error(format!(
                "Ours payload id {id} exceeds record_count {}",
                self.record_count
            )));
        }
        let begin = id as usize * self.record_bytes;
        let end = begin + self.record_bytes - 1;
        Ok((begin / PAGE_SIZE) as u64..=(end / PAGE_SIZE) as u64)
    }

    fn extract(&self, pages: &HashMap<u64, Box<[u8; PAGE_SIZE]>>, id: u32) -> ANNResult<Vec<u8>> {
        let begin = id as usize * self.record_bytes;
        let mut output = vec![0_u8; self.record_bytes];
        let mut copied = 0_usize;
        while copied < self.record_bytes {
            let absolute = begin + copied;
            let page_id = (absolute / PAGE_SIZE) as u64;
            let offset = absolute % PAGE_SIZE;
            let take = (PAGE_SIZE - offset).min(self.record_bytes - copied);
            let page = pages
                .get(&page_id)
                .ok_or_else(|| ann_error(format!("Ours payload page {page_id} was not read")))?;
            output[copied..copied + take].copy_from_slice(&page[offset..offset + take]);
            copied += take;
        }
        Ok(output)
    }

    /// Read one record directly from the resident payload image.
    ///
    /// The payload file stores records back-to-back in page-aligned space, so
    /// a record occupies one contiguous byte range regardless of page
    /// boundaries. This avoids rebuilding a full page map on every read.
    fn extract_from_bytes(&self, bytes: &[u8], id: u32) -> ANNResult<Vec<u8>> {
        if id as usize >= self.record_count {
            return Err(ann_error(format!(
                "Ours payload id {id} exceeds record_count {}",
                self.record_count
            )));
        }
        let begin = id as usize * self.record_bytes;
        let end = begin + self.record_bytes;
        if end > bytes.len() {
            return Err(ann_error("Ours memory payload is truncated"));
        }
        Ok(bytes[begin..end].to_vec())
    }
}

pub struct OursIndexBuilder {
    space: Arc<RabitqSpace>,
    count: usize,
    compact: Vec<u8>,
    residual: Vec<u8>,
    encoded: Vec<bool>,
}

impl OursIndexBuilder {
    pub fn new(space: Arc<RabitqSpace>, count: usize) -> ANNResult<Self> {
        if count == 0 {
            return Err(ann_error("Ours export requires at least one vector"));
        }
        Ok(Self {
            compact: vec![0_u8; count * space.compact_record_bytes()],
            residual: vec![0_u8; count * space.residual_record_bytes()],
            encoded: vec![false; count],
            space,
            count,
        })
    }

    pub fn encode(&mut self, id: usize, vector: &[f32]) -> ANNResult<()> {
        if id >= self.count {
            return Err(ann_error(format!("Ours export id {id} is out of range")));
        }
        let (compact, residual) = self.space.encode_parts(vector)?;
        let cb = self.space.compact_record_bytes();
        let rb = self.space.residual_record_bytes();
        self.compact[id * cb..(id + 1) * cb].copy_from_slice(&compact);
        self.residual[id * rb..(id + 1) * rb].copy_from_slice(&residual);
        self.encoded[id] = true;
        Ok(())
    }

    pub fn save(
        self,
        seed: u32,
        centroids: &[f32],
        metadata_path: &Path,
        sidecar_path: &Path,
        payload_path: &Path,
    ) -> ANNResult<(OursPayloadLayout, usize)> {
        if self.encoded.iter().any(|encoded| !encoded) {
            return Err(ann_error("Ours export has unencoded vector slots"));
        }
        if centroids.len() != self.space.dim() * self.space.centroid_count() {
            return Err(ann_error(
                "Ours centroid metadata does not match the native space",
            ));
        }
        for path in [metadata_path, sidecar_path, payload_path] {
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
        }

        let (msb, factors) = self.space.export_sidecar(&self.compact, self.count)?;
        let compact_bytes = self.space.compact_record_bytes();
        let residual_bytes = self.space.residual_record_bytes();
        let record_bytes = compact_bytes + residual_bytes;
        let page_count = (self.count * record_bytes).div_ceil(PAGE_SIZE);
        let layout = OursPayloadLayout {
            record_count: self.count,
            compact_bytes,
            residual_bytes,
            record_bytes,
            page_count,
        };

        let mut meta = BufWriter::new(File::create(metadata_path)?);
        meta.write_all(META_MAGIC)?;
        meta.write_all(&seed.to_le_bytes())?;
        meta.write_all(&(self.space.dim() as u64).to_le_bytes())?;
        meta.write_all(&(self.space.centroid_count() as u64).to_le_bytes())?;
        meta.write_all(&(self.count as u64).to_le_bytes())?;
        meta.write_all(&(compact_bytes as u64).to_le_bytes())?;
        meta.write_all(&(residual_bytes as u64).to_le_bytes())?;
        meta.write_all(&(self.space.paper_msb_code_bytes() as u64).to_le_bytes())?;
        meta.write_all(&(self.space.paper_factor_bytes() as u64).to_le_bytes())?;
        for value in centroids {
            meta.write_all(&value.to_le_bytes())?;
        }
        meta.flush()?;

        let mut sidecar = BufWriter::new(File::create(sidecar_path)?);
        sidecar.write_all(SIDECAR_MAGIC)?;
        sidecar.write_all(&(msb.len() as u64).to_le_bytes())?;
        sidecar.write_all(&(factors.len() as u64).to_le_bytes())?;
        sidecar.write_all(&msb)?;
        sidecar.write_all(&factors)?;
        sidecar.flush()?;

        let mut payload = BufWriter::new(File::create(payload_path)?);
        for id in 0..self.count {
            payload.write_all(&self.compact[id * compact_bytes..(id + 1) * compact_bytes])?;
            payload.write_all(&self.residual[id * residual_bytes..(id + 1) * residual_bytes])?;
        }
        let padding = page_count * PAGE_SIZE - self.count * record_bytes;
        if padding != 0 {
            payload.write_all(&vec![0_u8; padding])?;
        }
        payload.flush()?;

        let resident_bytes = msb.len() + factors.len() + centroids.len() * 4;
        Ok((layout, resident_bytes))
    }
}

pub struct OursResidentCodec {
    pub space: Arc<RabitqSpace>,
    pub layout: OursPayloadLayout,
    msb: Vec<u8>,
    factors: Vec<u8>,
    pub centroid_bytes: usize,
}

impl OursResidentCodec {
    pub fn load(metadata_path: &Path, sidecar_path: &Path) -> ANNResult<Self> {
        let mut meta = BufReader::new(File::open(metadata_path)?);
        let mut magic = [0_u8; 8];
        meta.read_exact(&mut magic)?;
        if &magic != META_MAGIC {
            return Err(ann_error("invalid Ours metadata magic"));
        }
        let seed = read_u32(&mut meta)?;
        let dim = read_u64(&mut meta)? as usize;
        let centroid_count = read_u64(&mut meta)? as usize;
        let record_count = read_u64(&mut meta)? as usize;
        let compact_bytes = read_u64(&mut meta)? as usize;
        let residual_bytes = read_u64(&mut meta)? as usize;
        let msb_bytes = read_u64(&mut meta)? as usize;
        let factor_bytes = read_u64(&mut meta)? as usize;
        if dim == 0 || centroid_count == 0 || record_count == 0 {
            return Err(ann_error("Ours metadata contains a zero dimension/count"));
        }
        let mut centroids = Vec::with_capacity(dim * centroid_count);
        for _ in 0..dim * centroid_count {
            centroids.push(read_f32(&mut meta)?);
        }
        let mut trailing = [0_u8; 1];
        if meta.read(&mut trailing)? != 0 {
            return Err(ann_error("Ours metadata contains trailing bytes"));
        }
        let space = RabitqSpace::new_with_centroids(dim, seed, centroid_count, &centroids)
            .map_err(ann_error)?;
        if compact_bytes != space.compact_record_bytes()
            || residual_bytes != space.residual_record_bytes()
            || msb_bytes != space.paper_msb_code_bytes()
            || factor_bytes != space.paper_factor_bytes()
        {
            return Err(ann_error("Ours persisted/native record layout mismatch"));
        }

        let mut sidecar = BufReader::new(File::open(sidecar_path)?);
        sidecar.read_exact(&mut magic)?;
        if &magic != SIDECAR_MAGIC {
            return Err(ann_error("invalid Ours sidecar magic"));
        }
        let stored_msb = read_u64(&mut sidecar)? as usize;
        let stored_factors = read_u64(&mut sidecar)? as usize;
        if stored_msb != record_count * msb_bytes || stored_factors != record_count * factor_bytes {
            return Err(ann_error("Ours sidecar size does not match metadata"));
        }
        let mut msb = vec![0_u8; stored_msb];
        let mut factors = vec![0_u8; stored_factors];
        sidecar.read_exact(&mut msb)?;
        sidecar.read_exact(&mut factors)?;
        if sidecar.read(&mut trailing)? != 0 {
            return Err(ann_error("Ours sidecar contains trailing bytes"));
        }
        let record_bytes = compact_bytes + residual_bytes;
        Ok(Self {
            space,
            layout: OursPayloadLayout {
                record_count,
                compact_bytes,
                residual_bytes,
                record_bytes,
                page_count: (record_count * record_bytes).div_ceil(PAGE_SIZE),
            },
            msb,
            factors,
            centroid_bytes: centroids.len() * 4,
        })
    }

    pub fn resident_bytes(&self) -> usize {
        self.msb.len() + self.factors.len() + self.centroid_bytes
    }

    /// Configure one immutable codec instance before its worker threads start.
    /// Every concurrent query then observes the same C++ query layout.
    pub fn configure_query_codec(&self, codec: QueryCoarseCodec) {
        self.space.set_query_coarse_codec(codec);
    }

    pub fn prepare(&self, query: &[f32]) -> ANNResult<OursPreparedQuery<'_>> {
        Ok(OursPreparedQuery {
            owner: self,
            computer: self.space.prepare_query(query)?,
        })
    }
}

pub struct OursPreparedQuery<'a> {
    owner: &'a OursResidentCodec,
    computer: QueryComputer,
}

impl OursPreparedQuery<'_> {
    fn paper_estimates(&self, ids: &[u32], epsilon: f32) -> ANNResult<Vec<RabitqPaperEstimate>> {
        let mut output = vec![RabitqPaperEstimate::default(); ids.len()];
        self.computer.paper_estimate_batch_sidecar(
            ids,
            self.owner.msb.as_ptr(),
            self.owner.space.paper_msb_code_bytes(),
            self.owner.factors.as_ptr().cast(),
            self.owner.space.paper_factor_bytes(),
            epsilon,
            &mut output,
        )?;
        Ok(output)
    }

    fn distances(
        &self,
        batch: &OursPayloadBatch,
        modes: &[u8],
        short_ips: &[f32],
    ) -> ANNResult<Vec<f32>> {
        let count = batch.count(self.owner.layout.compact_bytes)?;
        if count != modes.len() || count != short_ips.len() {
            return Err(ann_error("Ours gathered distance batch length mismatch"));
        }
        let local_ids = (0..count as u32).collect::<Vec<_>>();
        let mut output = vec![0.0_f32; count];
        self.computer.distance_batch(
            &local_ids,
            modes,
            batch.compact.as_ptr().cast(),
            self.owner.layout.compact_bytes,
            short_ips,
            &mut output,
        )?;
        Ok(output)
    }

    fn rerank(&self, batch: &OursPayloadBatch) -> ANNResult<Vec<f32>> {
        let count = batch.count(self.owner.layout.compact_bytes)?;
        let modes = vec![DIST_MODE_RECOMPUTE_FULL; count];
        let short_ips = vec![0.0_f32; count];
        let long_distances = self.distances(batch, &modes, &short_ips)?;
        let local_ids = (0..count as u32).collect::<Vec<_>>();
        let mut intervals = vec![RabitqDistanceInterval::default(); count];
        self.computer.residual_distance_batch(
            &local_ids,
            batch.compact.as_ptr().cast(),
            self.owner.layout.compact_bytes,
            batch.residual.as_ptr().cast(),
            self.owner.layout.residual_bytes,
            &long_distances,
            &mut intervals,
        )?;
        Ok(intervals.into_iter().map(|value| value.estimate).collect())
    }
}

#[derive(Default)]
struct OursPayloadBatch {
    compact: Vec<u8>,
    residual: Vec<u8>,
}

impl OursPayloadBatch {
    fn count(&self, compact_bytes: usize) -> ANNResult<usize> {
        if compact_bytes == 0 || self.compact.len() % compact_bytes != 0 {
            return Err(ann_error("invalid gathered Ours compact batch"));
        }
        Ok(self.compact.len() / compact_bytes)
    }
}

#[derive(Clone)]
pub enum OursPayloadFactory {
    Memory {
        bytes: Arc<Vec<u8>>,
        layout: OursPayloadLayout,
    },
    Direct {
        path: Arc<std::path::PathBuf>,
        layout: OursPayloadLayout,
    },
}

impl OursPayloadFactory {
    pub fn memory(path: &Path, layout: OursPayloadLayout) -> ANNResult<Self> {
        let bytes = fs::read(path)?;
        if bytes.len() != layout.page_count * PAGE_SIZE {
            return Err(ann_error("Ours memory payload file size mismatch"));
        }
        Ok(Self::Memory {
            bytes: Arc::new(bytes),
            layout,
        })
    }

    pub fn direct(path: &Path, layout: OursPayloadLayout) -> ANNResult<Self> {
        let size = fs::metadata(path)?.len() as usize;
        if size != layout.page_count * PAGE_SIZE {
            return Err(ann_error("Ours direct payload file size mismatch"));
        }
        Ok(Self::Direct {
            path: Arc::new(path.to_path_buf()),
            layout,
        })
    }

    pub fn create(&self) -> ANNResult<OursPayloadReader> {
        match self {
            Self::Memory { bytes, layout } => Ok(OursPayloadReader {
                inner: OursPayloadReaderInner::Memory {
                    bytes: bytes.clone(),
                    layout: layout.clone(),
                },
            }),
            Self::Direct { path, layout } => Ok(OursPayloadReader {
                inner: OursPayloadReaderInner::Direct {
                    reader: DirectAioHandle::new(path)?,
                    layout: layout.clone(),
                    query_cache: HashMap::new(),
                },
            }),
        }
    }
}

pub struct OursPayloadReader {
    inner: OursPayloadReaderInner,
}

enum OursPayloadReaderInner {
    Memory {
        bytes: Arc<Vec<u8>>,
        layout: OursPayloadLayout,
    },
    Direct {
        reader: DirectAioHandle,
        layout: OursPayloadLayout,
        query_cache: HashMap<u64, Box<[u8; PAGE_SIZE]>>,
    },
}

impl OursPayloadReader {
    pub fn begin_query(&mut self) {
        if let OursPayloadReaderInner::Direct { query_cache, .. } = &mut self.inner {
            query_cache.clear();
        }
    }

    fn read_records(
        &mut self,
        ids: &[u32],
        coalesce: bool,
        reuse: bool,
    ) -> ANNResult<(OursPayloadBatch, IoStats)> {
        match &mut self.inner {
            OursPayloadReaderInner::Memory { bytes, layout } => {
                let mut batch = OursPayloadBatch {
                    compact: Vec::with_capacity(ids.len() * layout.compact_bytes),
                    residual: Vec::with_capacity(ids.len() * layout.residual_bytes),
                };
                for &id in ids {
                    let record = layout.extract_from_bytes(bytes.as_slice(), id)?;
                    batch
                        .compact
                        .extend_from_slice(&record[..layout.compact_bytes]);
                    batch
                        .residual
                        .extend_from_slice(&record[layout.compact_bytes..]);
                }
                Ok((batch, IoStats::default()))
            }
            OursPayloadReaderInner::Direct {
                reader,
                layout,
                query_cache,
            } => {
                if !reuse {
                    query_cache.clear();
                }
                let started = Instant::now();
                let requested = ids
                    .iter()
                    .map(|&id| layout.pages_for(id))
                    .collect::<ANNResult<Vec<_>>>()?;
                let requested_count = requested
                    .iter()
                    .map(|range| range.clone().count())
                    .sum::<usize>();
                let mut stats = IoStats::default();

                if !coalesce {
                    let mut batch = OursPayloadBatch {
                        compact: Vec::with_capacity(ids.len() * layout.compact_bytes),
                        residual: Vec::with_capacity(ids.len() * layout.residual_bytes),
                    };
                    for (&id, pages) in ids.iter().zip(requested) {
                        let mut local = HashMap::new();
                        for page_id in pages {
                            if reuse {
                                if let Some(page) = query_cache.get(&page_id) {
                                    stats.query_cache_hits += 1;
                                    local.insert(page_id, page.clone());
                                    continue;
                                }
                            }
                            stats.query_cache_misses += 1;
                            let (bytes, delta) = reader.read_pages(&[page_id])?;
                            merge_bridge_stats(&mut stats, delta);
                            let mut page = Box::new([0_u8; PAGE_SIZE]);
                            page.copy_from_slice(&bytes[..PAGE_SIZE]);
                            if reuse {
                                query_cache.insert(page_id, page.clone());
                            }
                            local.insert(page_id, page);
                        }
                        append_record(layout, &local, id, &mut batch)?;
                    }
                    stats.io_wait_us = started.elapsed().as_secs_f64() * 1_000_000.0;
                    return Ok((batch, stats));
                }

                let mut unique_pages = requested
                    .into_iter()
                    .flat_map(|range| range)
                    .collect::<Vec<_>>();
                unique_pages.sort_unstable();
                unique_pages.dedup();
                stats.duplicate_pages_removed = (requested_count - unique_pages.len()) as u64;
                let missing = unique_pages
                    .iter()
                    .copied()
                    .filter(|page| {
                        if reuse && query_cache.contains_key(page) {
                            stats.query_cache_hits += 1;
                            false
                        } else {
                            stats.query_cache_misses += 1;
                            true
                        }
                    })
                    .collect::<Vec<_>>();
                let mut temporary = HashMap::new();
                if !missing.is_empty() {
                    let (bytes, delta) = reader.read_pages(&missing)?;
                    merge_bridge_stats(&mut stats, delta);
                    for (slot, page_id) in missing.iter().copied().enumerate() {
                        let mut page = Box::new([0_u8; PAGE_SIZE]);
                        page.copy_from_slice(&bytes[slot * PAGE_SIZE..(slot + 1) * PAGE_SIZE]);
                        if reuse {
                            query_cache.insert(page_id, page);
                        } else {
                            temporary.insert(page_id, page);
                        }
                    }
                }
                let source = if reuse { query_cache } else { &temporary };
                let batch = gather_records(layout, source, ids)?;
                stats.io_wait_us = started.elapsed().as_secs_f64() * 1_000_000.0;
                Ok((batch, stats))
            }
        }
    }
}

fn merge_bridge_stats(stats: &mut IoStats, delta: super::BridgeIoStats) {
    stats.requests += delta.submitted_requests;
    stats.sectors_4k += delta.unique_pages;
    stats.bytes_read += delta.bytes_read;
    stats.coalesced_requests += delta.coalesced_requests;
    stats.duplicate_pages_removed += delta.duplicate_pages_removed;
}

fn gather_records(
    layout: &OursPayloadLayout,
    pages: &HashMap<u64, Box<[u8; PAGE_SIZE]>>,
    ids: &[u32],
) -> ANNResult<OursPayloadBatch> {
    let mut batch = OursPayloadBatch {
        compact: Vec::with_capacity(ids.len() * layout.compact_bytes),
        residual: Vec::with_capacity(ids.len() * layout.residual_bytes),
    };
    for &id in ids {
        append_record(layout, pages, id, &mut batch)?;
    }
    Ok(batch)
}

fn append_record(
    layout: &OursPayloadLayout,
    pages: &HashMap<u64, Box<[u8; PAGE_SIZE]>>,
    id: u32,
    batch: &mut OursPayloadBatch,
) -> ANNResult<()> {
    let record = layout.extract(pages, id)?;
    batch
        .compact
        .extend_from_slice(&record[..layout.compact_bytes]);
    batch
        .residual
        .extend_from_slice(&record[layout.compact_bytes..]);
    Ok(())
}

fn add_io(total: &mut IoStats, delta: IoStats) {
    total.requests += delta.requests;
    total.sectors_4k += delta.sectors_4k;
    total.bytes_read += delta.bytes_read;
    total.coalesced_requests += delta.coalesced_requests;
    total.duplicate_pages_removed += delta.duplicate_pages_removed;
    total.query_cache_hits += delta.query_cache_hits;
    total.query_cache_misses += delta.query_cache_misses;
    total.shared_cache_hits += delta.shared_cache_hits;
    total.shared_cache_misses += delta.shared_cache_misses;
    total.io_wait_us += delta.io_wait_us;
}

#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct OursDiskStats {
    pub io: IoStats,
    pub queue_compute_us: f64,
    pub rerank_us: f64,
    pub db1_checks: u64,
    pub db1_survivors: u64,
    pub full4_candidates: u64,
    pub full4_page_reads: u64,
    pub rerank_candidates: u64,
    pub rerank_page_reads: u64,
}

#[derive(Debug, Clone)]
pub struct OursDiskSearchResult {
    pub ids: Vec<u32>,
    pub distances: Vec<f32>,
    pub stats: OursDiskStats,
}

#[derive(Debug, Clone, Copy)]
struct Candidate {
    distance: f32,
    id: u32,
    expanded: bool,
}

fn candidate_less(left: Candidate, right: Candidate) -> bool {
    left.distance < right.distance || (left.distance == right.distance && left.id < right.id)
}

fn insert_candidate(pool: &mut Vec<Candidate>, candidate: Candidate, width: usize) {
    if width == 0 || (pool.len() >= width && !candidate_less(candidate, *pool.last().unwrap())) {
        return;
    }
    let position = pool
        .binary_search_by(|probe| {
            if candidate_less(*probe, candidate) {
                std::cmp::Ordering::Less
            } else if candidate_less(candidate, *probe) {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .unwrap_or_else(|position| position);
    pool.insert(position, candidate);
    pool.truncate(width);
}

/// Run the exact experiment-02/03 Ours search loop on its own page-backed
/// ExRaBitQ-symmetric Vamana graph.
#[allow(clippy::too_many_arguments)]
pub fn search_ours_disk_graph(
    codec: &OursResidentCodec,
    graph_factory: &BackendFactory,
    payload_reader: &mut OursPayloadReader,
    query: &[f32],
    k: usize,
    width: usize,
    beam: usize,
    epsilon: f32,
    rerank_candidates: usize,
    ablation: OursAblation,
) -> ANNResult<OursDiskSearchResult> {
    if k == 0 {
        return Ok(OursDiskSearchResult {
            ids: Vec::new(),
            distances: Vec::new(),
            stats: OursDiskStats::default(),
        });
    }
    let width = width.max(k);
    if beam == 0 {
        return Err(ann_error("Ours disk search beam must be positive"));
    }
    let mut graph = graph_factory.create()?;
    payload_reader.begin_query();
    let mut stats = OursDiskStats::default();
    let prepare_started = Instant::now();
    let prepared = codec.prepare(query)?;
    stats.io.query_prep_us = prepare_started.elapsed().as_secs_f64() * 1_000_000.0;
    let mut visited = vec![false; codec.layout.record_count];
    let mut pool = Vec::with_capacity(width + 1);

    // Experiment 02's Ours loop starts from base node 0, rather than the
    // extra frozen-point payload used by the stock DiskANN strategies.
    visited[0] = true;
    let (start_batch, delta) = payload_reader.read_records(
        &[0],
        ablation.payload_coalescing(),
        ablation.payload_reuse(),
    )?;
    if ablation != OursAblation::Full4ResidentNoGate {
        stats.full4_candidates += 1;
        stats.full4_page_reads += delta.sectors_4k;
    }
    add_io(&mut stats.io, delta);
    let distance_started = Instant::now();
    let start_distance = prepared.distances(&start_batch, &[DIST_MODE_RECOMPUTE_FULL], &[0.0])?[0];
    stats.io.distance_compute_us += distance_started.elapsed().as_secs_f64() * 1_000_000.0;
    stats.io.visited_nodes = 1;
    stats.io.distance_evaluations = 1;
    insert_candidate(
        &mut pool,
        Candidate {
            distance: start_distance,
            id: 0,
            expanded: false,
        },
        width,
    );

    let mut frontier = Vec::with_capacity(beam);
    loop {
        frontier.clear();
        for candidate in &mut pool {
            if !candidate.expanded {
                candidate.expanded = true;
                frontier.push(candidate.id);
                if frontier.len() == beam {
                    break;
                }
            }
        }
        if frontier.is_empty() {
            break;
        }

        // Preserve the source Ours semantics: each frontier node is expanded
        // in order, while candidates from that node are processed as one SIMD
        // gate/distance batch.
        for node in frontier.iter().copied() {
            let (lists, graph_delta) = graph.read_nodes(&[node])?;
            add_io(&mut stats.io, graph_delta);
            let mut fresh = Vec::new();
            for neighbor in &lists[0] {
                let id = *neighbor as usize;
                if id >= codec.layout.record_count {
                    // Defensive guard for malformed or auxiliary graph IDs;
                    // the formal Ours graph contains exactly the base IDs.
                    continue;
                }
                if !visited[id] {
                    visited[id] = true;
                    fresh.push(*neighbor);
                }
            }
            if fresh.is_empty() {
                continue;
            }
            stats.io.visited_nodes += fresh.len() as u64;

            let estimates = if ablation.uses_gate() {
                let gate_started = Instant::now();
                let values = prepared.paper_estimates(&fresh, epsilon)?;
                stats.queue_compute_us += gate_started.elapsed().as_secs_f64() * 1_000_000.0;
                stats.db1_checks += fresh.len() as u64;
                values
            } else {
                vec![RabitqPaperEstimate::default(); fresh.len()]
            };

            let mut survivor_ids = Vec::with_capacity(fresh.len());
            let mut survivor_estimates = Vec::with_capacity(fresh.len());
            for (&id, estimate) in fresh.iter().zip(&estimates) {
                if ablation.uses_gate()
                    && pool.len() >= width
                    && estimate.valid != 0
                    && estimate.lower_bound > pool.last().unwrap().distance
                {
                    continue;
                }
                survivor_ids.push(id);
                survivor_estimates.push(*estimate);
            }
            if ablation.uses_gate() {
                stats.db1_survivors += survivor_ids.len() as u64;
            }
            if survivor_ids.is_empty() {
                continue;
            }

            for (ids, estimates) in survivor_ids
                .chunks(DIST_BATCH_SIZE)
                .zip(survivor_estimates.chunks(DIST_BATCH_SIZE))
            {
                let (payload, payload_delta) = payload_reader.read_records(
                    ids,
                    ablation.payload_coalescing(),
                    ablation.payload_reuse(),
                )?;
                stats.full4_candidates += ids.len() as u64;
                stats.full4_page_reads += payload_delta.sectors_4k;
                add_io(&mut stats.io, payload_delta);
                let modes = estimates
                    .iter()
                    .map(|estimate| {
                        if ablation.uses_gate() && estimate.valid != 0 {
                            DIST_MODE_REUSE_QUANTIZED_MSB
                        } else {
                            DIST_MODE_RECOMPUTE_FULL
                        }
                    })
                    .collect::<Vec<_>>();
                let short_ips = estimates
                    .iter()
                    .map(|value| value.short_ip)
                    .collect::<Vec<_>>();
                let distance_started = Instant::now();
                let distances = prepared.distances(&payload, &modes, &short_ips)?;
                stats.io.distance_compute_us +=
                    distance_started.elapsed().as_secs_f64() * 1_000_000.0;

                let queue_started = Instant::now();
                for ((&id, estimate), distance) in ids.iter().zip(estimates).zip(distances) {
                    if ablation.uses_gate()
                        && pool.len() >= width
                        && estimate.valid != 0
                        && estimate.lower_bound > pool.last().unwrap().distance
                    {
                        continue;
                    }
                    stats.io.distance_evaluations += 1;
                    insert_candidate(
                        &mut pool,
                        Candidate {
                            distance,
                            id,
                            expanded: false,
                        },
                        width,
                    );
                }
                stats.queue_compute_us += queue_started.elapsed().as_secs_f64() * 1_000_000.0;
            }
        }
    }

    let rerank_ids = pool
        .iter()
        .take(rerank_candidates.max(k).min(pool.len()))
        .map(|candidate| candidate.id)
        .collect::<Vec<_>>();
    stats.rerank_candidates = rerank_ids.len() as u64;
    let rerank_started = Instant::now();
    let (rerank_payload, rerank_delta) = payload_reader.read_records(
        &rerank_ids,
        ablation.payload_coalescing(),
        ablation.payload_reuse(),
    )?;
    stats.rerank_page_reads = rerank_delta.sectors_4k;
    add_io(&mut stats.io, rerank_delta);
    let rerank_distances = prepared.rerank(&rerank_payload)?;
    stats.rerank_us = rerank_started.elapsed().as_secs_f64() * 1_000_000.0;
    let mut reranked = rerank_ids
        .into_iter()
        .zip(rerank_distances)
        .map(|(id, distance)| (distance, id))
        .collect::<Vec<_>>();
    reranked.sort_unstable_by(|left, right| {
        left.0
            .partial_cmp(&right.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.1.cmp(&right.1))
    });
    let (distances, ids): (Vec<_>, Vec<_>) = reranked.into_iter().take(k).unzip();
    Ok(OursDiskSearchResult {
        ids,
        distances,
        stats,
    })
}
