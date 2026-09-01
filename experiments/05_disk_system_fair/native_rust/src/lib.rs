//! Native storage substrate shared by the formal 05B disk ports.
//!
//! For PQ/SQ/SAQ, graph search is DiskANN's `Knn` over the exact shared
//! baseline graph. Ours keeps its own ExRaBitQ-symmetric graph and paper-prune
//! search. In both cases this module converts the source adjacency without
//! reordering edges and reads it either from the same bytes in memory (parity
//! reference) or through the common Linux `O_DIRECT`/libaio reader.

use std::collections::{HashMap, HashSet, VecDeque};
use std::ffi::{c_char, c_void, CString};
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::num::NonZeroUsize;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use diskann::graph::glue::{
    self, DefaultPostProcessor, FilterStartPoints, Pipeline, SearchAccessor, SearchStrategy,
};
use diskann::graph::{self, DiskANNIndex};
use diskann::provider::{DataProvider, DefaultContext, HasId, NoopGuard};
use diskann::{default_post_processor, ANNError, ANNResult};
use diskann_providers::model::graph::provider::async_::{
    common::{CreateVectorStore, SetElementHelper, VectorStore},
    inmem::{SQStore, WithBits},
    FastMemoryQuantVectorProviderAsync,
};
use diskann_providers::model::pq::FixedChunkPQTable;
use diskann_providers::storage::{
    AsyncIndexMetadata, AsyncQuantLoadContext, FileStorageProvider, LoadWith, SaveWith,
};
use diskann_quantization::scalar::{CompensatedVectorRef, ScalarQuantizer};
use diskann_quantization::{
    algorithms::transforms::{TargetDim, TransformKind},
    alloc::{GlobalAllocator, Poly},
    spherical,
};
use diskann_vector::{distance::Metric, PreprocessedDistanceFunction};
use rand::rngs::StdRng;
use rand::SeedableRng;

pub mod ours_port;
pub mod baseline_disk;

pub const PAGE_SIZE: usize = 4096;
pub const MAX_INFLIGHT_IO: usize = 128;
pub type SharedPageCache = Arc<HashMap<u64, Box<[u8; PAGE_SIZE]>>>;

#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
struct BridgeIoStats {
    requested_pages: u64,
    unique_pages: u64,
    submitted_requests: u64,
    duplicate_pages_removed: u64,
    coalesced_requests: u64,
    bytes_read: u64,
}

unsafe extern "C" {
    fn qgraph05_direct_reader_new(
        path: *const c_char,
        max_inflight: usize,
        max_coalesce_pages: usize,
    ) -> *mut c_void;
    fn qgraph05_direct_reader_delete(reader: *mut c_void);
    fn qgraph05_direct_reader_read_pages(
        reader: *mut c_void,
        page_ids: *const u64,
        count: usize,
        output: *mut u8,
        stats: *mut BridgeIoStats,
    ) -> bool;
    fn qgraph05_direct_reader_error(output: *mut c_char, capacity: usize) -> usize;
}

fn direct_io_error() -> ANNError {
    let required = unsafe { qgraph05_direct_reader_error(std::ptr::null_mut(), 0) };
    let mut buffer = vec![0_u8; required + 1];
    unsafe {
        qgraph05_direct_reader_error(buffer.as_mut_ptr().cast(), buffer.len());
    }
    buffer.truncate(required);
    ann_error(format!(
        "native direct-I/O: {}",
        String::from_utf8_lossy(&buffer)
    ))
}

struct DirectAioHandle(*mut c_void);

unsafe impl Send for DirectAioHandle {}
unsafe impl Sync for DirectAioHandle {}

impl DirectAioHandle {
    fn new(path: &Path) -> ANNResult<Self> {
        let path = CString::new(path.to_string_lossy().as_bytes())
            .map_err(|_| ann_error("direct-I/O path contains a NUL byte"))?;
        let pointer = unsafe { qgraph05_direct_reader_new(path.as_ptr(), MAX_INFLIGHT_IO, 32) };
        if pointer.is_null() {
            Err(direct_io_error())
        } else {
            Ok(Self(pointer))
        }
    }

    fn read_pages(&mut self, pages: &[u64]) -> ANNResult<(Vec<u8>, BridgeIoStats)> {
        let mut output = vec![0_u8; pages.len() * PAGE_SIZE];
        let mut stats = BridgeIoStats::default();
        let ok = unsafe {
            qgraph05_direct_reader_read_pages(
                self.0,
                pages.as_ptr(),
                pages.len(),
                output.as_mut_ptr(),
                &mut stats,
            )
        };
        if ok {
            Ok((output, stats))
        } else {
            Err(direct_io_error())
        }
    }
}

impl Drop for DirectAioHandle {
    fn drop(&mut self) {
        unsafe { qgraph05_direct_reader_delete(self.0) };
    }
}

fn ann_error(message: impl Into<String>) -> ANNError {
    ANNError::message(message.into())
}

fn read_u32(reader: &mut impl Read) -> std::io::Result<u32> {
    let mut bytes = [0_u8; 4];
    reader.read_exact(&mut bytes)?;
    Ok(u32::from_le_bytes(bytes))
}

fn read_u64(reader: &mut impl Read) -> std::io::Result<u64> {
    let mut bytes = [0_u8; 8];
    reader.read_exact(&mut bytes)?;
    Ok(u64::from_le_bytes(bytes))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GraphLayout {
    pub node_count: usize,
    pub max_degree: usize,
    pub start_point: u32,
    pub additional_points: usize,
    pub record_bytes: usize,
    pub nodes_per_page: usize,
    pub page_count: usize,
}

impl GraphLayout {
    pub fn page_for(&self, node: u32) -> ANNResult<u64> {
        if node as usize >= self.node_count {
            return Err(ann_error(format!(
                "graph node {node} is out of range 0..{}",
                self.node_count
            )));
        }
        Ok((node as usize / self.nodes_per_page) as u64)
    }

    fn offset_in_page(&self, node: u32) -> usize {
        node as usize % self.nodes_per_page * self.record_bytes
    }

    fn decode_node(&self, page: &[u8], node: u32) -> ANNResult<Vec<u32>> {
        let begin = self.offset_in_page(node);
        let end = begin + self.record_bytes;
        if end > page.len() {
            return Err(ann_error("short graph page while decoding a node"));
        }
        let record = &page[begin..end];
        let degree = u32::from_le_bytes(record[0..4].try_into().unwrap()) as usize;
        if degree > self.max_degree {
            return Err(ann_error(format!(
                "stored degree {degree} exceeds max_degree {}",
                self.max_degree
            )));
        }
        let mut result = Vec::with_capacity(degree);
        for chunk in record[4..4 + degree * 4].chunks_exact(4) {
            let id = u32::from_le_bytes(chunk.try_into().unwrap());
            if id as usize >= self.node_count {
                return Err(ann_error(format!(
                    "adjacency id {id} exceeds node_count {}",
                    self.node_count
                )));
            }
            result.push(id);
        }
        Ok(result)
    }
}

/// Convert a canonical DiskANN adjacency file into fixed-size 4-KiB graph pages.
/// No edge is reordered or removed.
pub fn export_shared_graph(source: &Path, destination: &Path) -> ANNResult<GraphLayout> {
    let source_size = fs::metadata(source)?.len();
    let mut reader = BufReader::new(File::open(source)?);
    let declared_size = read_u64(&mut reader)?;
    let max_degree = read_u32(&mut reader)? as usize;
    let start_point = read_u32(&mut reader)?;
    let additional_points = read_u64(&mut reader)? as usize;
    if declared_size != source_size {
        return Err(ann_error(format!(
            "canonical graph size mismatch: header={declared_size} file={source_size}"
        )));
    }
    if max_degree == 0 {
        return Err(ann_error("canonical graph max_degree is zero"));
    }
    let record_bytes = 4 * (max_degree + 1);
    let nodes_per_page = PAGE_SIZE / record_bytes;
    if nodes_per_page == 0 {
        return Err(ann_error(format!(
            "graph record {record_bytes} does not fit in a {PAGE_SIZE}-byte page"
        )));
    }
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut writer = BufWriter::new(File::create(destination)?);
    let mut page = vec![0_u8; PAGE_SIZE];
    let mut slot = 0_usize;
    let mut node_count = 0_usize;
    let mut position = 24_u64;
    while position < declared_size {
        let degree = read_u32(&mut reader)? as usize;
        position += 4;
        if degree > max_degree {
            return Err(ann_error(format!(
                "node {node_count} degree {degree} exceeds header max_degree {max_degree}"
            )));
        }
        let mut neighbors = vec![0_u8; degree * 4];
        reader.read_exact(&mut neighbors)?;
        position += neighbors.len() as u64;

        let begin = slot * record_bytes;
        page[begin..begin + 4].copy_from_slice(&(degree as u32).to_le_bytes());
        page[begin + 4..begin + 4 + neighbors.len()].copy_from_slice(&neighbors);
        slot += 1;
        node_count += 1;
        if slot == nodes_per_page {
            writer.write_all(&page)?;
            page.fill(0);
            slot = 0;
        }
    }
    if position != declared_size {
        return Err(ann_error(format!(
            "canonical graph ended at {position}, expected {declared_size}"
        )));
    }
    if slot != 0 {
        writer.write_all(&page)?;
    }
    writer.flush()?;

    if start_point as usize >= node_count {
        return Err(ann_error(format!(
            "start point {start_point} exceeds exported node count {node_count}"
        )));
    }
    let page_count = node_count.div_ceil(nodes_per_page);
    let actual_bytes = fs::metadata(destination)?.len() as usize;
    if actual_bytes != page_count * PAGE_SIZE {
        return Err(ann_error(format!(
            "page file is not exact: got {actual_bytes}, expected {}",
            page_count * PAGE_SIZE
        )));
    }
    Ok(GraphLayout {
        node_count,
        max_degree,
        start_point,
        additional_points,
        record_bytes,
        nodes_per_page,
        page_count,
    })
}

#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct IoStats {
    pub requests: u64,
    pub sectors_4k: u64,
    pub bytes_read: u64,
    pub coalesced_requests: u64,
    pub duplicate_pages_removed: u64,
    pub query_cache_hits: u64,
    pub query_cache_misses: u64,
    pub shared_cache_hits: u64,
    pub shared_cache_misses: u64,
    pub io_wait_us: f64,
    pub query_prep_us: f64,
    pub distance_compute_us: f64,
    pub visited_nodes: u64,
    pub distance_evaluations: u64,
}

pub struct MemoryGraphReader {
    layout: GraphLayout,
    bytes: Arc<Vec<u8>>,
}

impl MemoryGraphReader {
    pub fn new(path: &Path, layout: GraphLayout) -> ANNResult<Self> {
        let bytes = fs::read(path)?;
        if bytes.len() != layout.page_count * PAGE_SIZE {
            return Err(ann_error("memory graph page file size mismatch"));
        }
        Ok(Self {
            layout,
            bytes: Arc::new(bytes),
        })
    }

    fn from_shared(bytes: Arc<Vec<u8>>, layout: GraphLayout) -> ANNResult<Self> {
        if bytes.len() != layout.page_count * PAGE_SIZE {
            return Err(ann_error("shared memory graph page file size mismatch"));
        }
        Ok(Self { layout, bytes })
    }

    fn read_nodes(&mut self, ids: &[u32]) -> ANNResult<(Vec<Vec<u32>>, IoStats)> {
        let mut output = Vec::with_capacity(ids.len());
        for &id in ids {
            let page_id = self.layout.page_for(id)? as usize;
            let page = &self.bytes[page_id * PAGE_SIZE..(page_id + 1) * PAGE_SIZE];
            output.push(self.layout.decode_node(page, id)?);
        }
        Ok((output, IoStats::default()))
    }
}

pub struct DirectGraphReader {
    layout: GraphLayout,
    reader: DirectAioHandle,
    shared_cache: Arc<HashMap<u64, Box<[u8; PAGE_SIZE]>>>,
    query_cache: HashMap<u64, Box<[u8; PAGE_SIZE]>>,
}

impl DirectGraphReader {
    pub fn new(path: &Path, layout: GraphLayout) -> ANNResult<Self> {
        Ok(Self {
            layout,
            reader: DirectAioHandle::new(path)?,
            shared_cache: Arc::new(HashMap::new()),
            query_cache: HashMap::new(),
        })
    }

    pub fn with_shared_cache(path: &Path, layout: GraphLayout, pages: &[u64]) -> ANNResult<Self> {
        let mut reader = DirectAioHandle::new(path)?;
        let shared_cache = load_shared_page_cache_with_reader(&mut reader, &layout, pages)?;
        Ok(Self {
            layout,
            reader,
            shared_cache,
            query_cache: HashMap::new(),
        })
    }

    pub fn with_loaded_shared_cache(
        path: &Path,
        layout: GraphLayout,
        shared_cache: SharedPageCache,
    ) -> ANNResult<Self> {
        Ok(Self {
            layout,
            reader: DirectAioHandle::new(path)?,
            shared_cache,
            query_cache: HashMap::new(),
        })
    }

    pub fn load_shared_cache(
        path: &Path,
        layout: &GraphLayout,
        pages: &[u64],
    ) -> ANNResult<SharedPageCache> {
        let mut reader = DirectAioHandle::new(path)?;
        load_shared_page_cache_with_reader(&mut reader, layout, pages)
    }

    pub fn clear_query_cache(&mut self) {
        self.query_cache.clear();
    }

    fn read_nodes(&mut self, ids: &[u32]) -> ANNResult<(Vec<Vec<u32>>, IoStats)> {
        let start = Instant::now();
        let requested_pages = ids
            .iter()
            .map(|&id| self.layout.page_for(id))
            .collect::<ANNResult<Vec<_>>>()?;
        let mut unique_pages = requested_pages.clone();
        unique_pages.sort_unstable();
        unique_pages.dedup();

        let mut stats = IoStats {
            duplicate_pages_removed: (requested_pages.len() - unique_pages.len()) as u64,
            ..IoStats::default()
        };
        let missing = unique_pages
            .iter()
            .copied()
            .filter(|page| {
                if self.query_cache.contains_key(page) {
                    stats.query_cache_hits += 1;
                    false
                } else if self.shared_cache.contains_key(page) {
                    stats.shared_cache_hits += 1;
                    false
                } else {
                    stats.query_cache_misses += 1;
                    stats.shared_cache_misses += 1;
                    true
                }
            })
            .collect::<Vec<_>>();

        if !missing.is_empty() {
            let (buffer, bridge_stats) = self.reader.read_pages(&missing)?;

            for (index, page_id) in missing.iter().copied().enumerate() {
                let begin = index * PAGE_SIZE;
                let mut page = Box::new([0_u8; PAGE_SIZE]);
                page.copy_from_slice(&buffer[begin..begin + PAGE_SIZE]);
                self.query_cache.insert(page_id, page);
            }
            stats.requests = bridge_stats.submitted_requests;
            stats.sectors_4k = bridge_stats.unique_pages;
            stats.bytes_read = bridge_stats.bytes_read;
            stats.coalesced_requests = bridge_stats.coalesced_requests;
            stats.duplicate_pages_removed += bridge_stats.duplicate_pages_removed;
        }

        let mut output = Vec::with_capacity(ids.len());
        for (&id, page_id) in ids.iter().zip(requested_pages) {
            let page = self
                .query_cache
                .get(&page_id)
                .or_else(|| self.shared_cache.get(&page_id))
                .ok_or_else(|| ann_error("direct graph read did not populate requested page"))?;
            output.push(self.layout.decode_node(page.as_slice(), id)?);
        }
        stats.io_wait_us = start.elapsed().as_secs_f64() * 1_000_000.0;
        Ok((output, stats))
    }
}

fn load_shared_page_cache_with_reader(
    reader: &mut DirectAioHandle,
    layout: &GraphLayout,
    pages: &[u64],
) -> ANNResult<SharedPageCache> {
    let mut unique = pages.to_vec();
    unique.sort_unstable();
    unique.dedup();
    if unique.is_empty() {
        return Ok(Arc::new(HashMap::new()));
    }
    if unique
        .iter()
        .any(|&page| page as usize >= layout.page_count)
    {
        return Err(ann_error(
            "shared-cache page is outside the graph page file",
        ));
    }
    let (bytes, _) = reader.read_pages(&unique)?;
    let mut shared_cache = HashMap::with_capacity(unique.len());
    for (index, page_id) in unique.into_iter().enumerate() {
        let begin = index * PAGE_SIZE;
        let mut page = Box::new([0_u8; PAGE_SIZE]);
        page.copy_from_slice(&bytes[begin..begin + PAGE_SIZE]);
        shared_cache.insert(page_id, page);
    }
    Ok(Arc::new(shared_cache))
}

pub enum GraphBackend {
    Memory(MemoryGraphReader),
    Direct(Arc<Mutex<DirectGraphReader>>),
}

impl GraphBackend {
    fn read_nodes(&mut self, ids: &[u32]) -> ANNResult<(Vec<Vec<u32>>, IoStats)> {
        match self {
            Self::Memory(reader) => reader.read_nodes(ids),
            Self::Direct(reader) => reader
                .lock()
                .map_err(|_| ann_error("direct graph reader lock poisoned"))?
                .read_nodes(ids),
        }
    }
}

pub trait QueryDistance: Send + Sync {
    fn distance(&self, id: u32) -> ANNResult<f32>;
}

pub trait ResidentCodec: Send + Sync {
    type Query<'a>: QueryDistance + 'a
    where
        Self: 'a;

    fn prepare<'a>(
        &'a self,
        query: &[f32],
        stats: Arc<Mutex<IoStats>>,
    ) -> ANNResult<Self::Query<'a>>;
}

/// Exact resident PQ navigation payload used by experiment 02.
///
/// Four bits per original dimension are represented by one 8-bit centroid ID
/// for every two dimensions (`num_pq_chunks = D / 2`).  Both encoding and
/// query distance call DiskANN's existing `FastMemoryQuantVectorProviderAsync`
/// implementation; this type only adapts it to the disk-backed graph search.
pub struct PqCodec {
    store: FastMemoryQuantVectorProviderAsync,
}

impl PqCodec {
    pub fn new(table: FixedChunkPQTable, count: usize) -> Self {
        Self {
            store: FastMemoryQuantVectorProviderAsync::new(Metric::L2, count, table),
        }
    }

    pub fn load(pivots: &Path, codes: &Path) -> ANNResult<Self> {
        let store = FastMemoryQuantVectorProviderAsync::load_direct(
            &FileStorageProvider,
            path_text(pivots)?,
            path_text(codes)?,
            Metric::L2,
        )?;
        Ok(Self { store })
    }

    pub fn save(&self, pivots: &Path, codes: &Path) -> ANNResult<usize> {
        if let Some(parent) = pivots.parent() {
            fs::create_dir_all(parent)?;
        }
        if let Some(parent) = codes.parent() {
            fs::create_dir_all(parent)?;
        }
        self.store
            .save_direct(&FileStorageProvider, path_text(pivots)?, path_text(codes)?)
    }

    pub fn encode(&self, id: u32, vector: &[f32]) -> ANNResult<()> {
        self.store.set_element(&id, vector)
    }

    pub fn total(&self) -> usize {
        self.store.total()
    }

    pub fn full_dim(&self) -> usize {
        self.store.full_dim()
    }

    pub fn code_bytes_per_vector(&self) -> usize {
        self.store.pq_chunks()
    }

    pub fn resident_bytes(&self) -> usize {
        self.total() * self.code_bytes_per_vector()
    }

    /// Return the immutable 4-bit PQ code for one node (export-time dump).
    pub fn code_bytes(&self, id: u32) -> &[u8] {
        // SAFETY: the payload is immutable after export; no writer exists.
        unsafe { self.store.get_vector_sync(id as usize) }
    }

    /// Return the shared PQ codebook table.
    pub fn table(&self) -> &FixedChunkPQTable {
        &self.store.pq_chunk_table
    }
}

fn path_text(path: &Path) -> ANNResult<&str> {
    path.to_str()
        .ok_or_else(|| ann_error(format!("path is not valid UTF-8: {}", path.display())))
}

pub struct PqQuery<'a> {
    store: &'a FastMemoryQuantVectorProviderAsync,
    computer: diskann_providers::model::pq::distance::QueryComputer<'a>,
}

impl QueryDistance for PqQuery<'_> {
    #[inline(always)]
    fn distance(&self, id: u32) -> ANNResult<f32> {
        if id as usize >= self.store.total() {
            return Err(ann_error(format!(
                "PQ vector id {id} is out of range 0..{}",
                self.store.total()
            )));
        }
        // SAFETY: the search payload is immutable after export/load.  No
        // writer exists while a query is running.
        let code = unsafe { self.store.get_vector_sync(id as usize) };
        Ok(self.computer.evaluate_similarity(code))
    }
}

impl ResidentCodec for PqCodec {
    type Query<'a> = PqQuery<'a>;

    fn prepare<'a>(
        &'a self,
        query: &[f32],
        _stats: Arc<Mutex<IoStats>>,
    ) -> ANNResult<Self::Query<'a>> {
        Ok(PqQuery {
            store: &self.store,
            computer: self.store.query_computer(query)?,
        })
    }
}

/// Exact DiskANN scalar-quantized navigation payload used by experiment 02.
pub struct SqCodec {
    store: SQStore<4>,
}

impl SqCodec {
    pub fn new(quantizer: ScalarQuantizer, count: usize) -> Self {
        Self {
            store: WithBits::<4>::new(quantizer).create(count, Metric::L2, None),
        }
    }

    pub fn load(prefix: &Path) -> ANNResult<Self> {
        let metadata = AsyncIndexMetadata::new(path_text(prefix)?.to_string());
        let context = AsyncQuantLoadContext {
            metadata,
            num_frozen_points: NonZeroUsize::new(1).unwrap(),
            metric: Metric::L2,
            prefetch_lookahead: None,
            is_disk_index: false,
            prefetch_cache_line_level: None,
        };
        let runtime = tokio::runtime::Builder::new_current_thread().build()?;
        let store = runtime.block_on(SQStore::<4>::load_with(&FileStorageProvider, &context))?;
        Ok(Self { store })
    }

    pub fn save(&self, prefix: &Path) -> ANNResult<usize> {
        if let Some(parent) = prefix.parent() {
            fs::create_dir_all(parent)?;
        }
        let metadata = AsyncIndexMetadata::new(path_text(prefix)?.to_string());
        let runtime = tokio::runtime::Builder::new_current_thread().build()?;
        runtime.block_on(self.store.save_with(&FileStorageProvider, &metadata))
    }

    pub fn encode(&self, id: u32, vector: &[f32]) -> ANNResult<()> {
        self.store.set_element(&id, vector)
    }

    pub fn total(&self) -> usize {
        VectorStore::total(&self.store)
    }

    pub fn full_dim(&self) -> usize {
        self.store.dim()
    }

    pub fn code_bytes_per_vector(&self) -> usize {
        CompensatedVectorRef::<4>::canonical_bytes(self.full_dim())
    }

    pub fn resident_bytes(&self) -> usize {
        self.total() * self.code_bytes_per_vector()
    }

    /// Return the quantizer used by this store (used by the disk-payload
    /// variant, which keeps only the quantizer resident).
    pub fn quantizer(&self) -> &ScalarQuantizer {
        self.store.quantizer()
    }
}

pub struct SqQuery<'a> {
    store: &'a SQStore<4>,
    computer: diskann_providers::model::graph::provider::async_::inmem::SQQueryComputer<4>,
}

impl QueryDistance for SqQuery<'_> {
    #[inline(always)]
    fn distance(&self, id: u32) -> ANNResult<f32> {
        if id as usize >= VectorStore::total(self.store) {
            return Err(ann_error(format!(
                "SQ vector id {id} is out of range 0..{}",
                VectorStore::total(self.store)
            )));
        }
        let code = self.store.get_vector(id as usize)?;
        Ok(self.computer.evaluate_similarity(code))
    }
}

impl ResidentCodec for SqCodec {
    type Query<'a> = SqQuery<'a>;

    fn prepare<'a>(
        &'a self,
        query: &[f32],
        _stats: Arc<Mutex<IoStats>>,
    ) -> ANNResult<Self::Query<'a>> {
        Ok(SqQuery {
            store: &self.store,
            computer: self.store.query_computer(query, true)?,
        })
    }
}

/// Exact DiskANN spherical 4-bit navigation payload used by experiment 02.
/// The final quantizer shift and the RNG seed that generated the Hadamard
/// signs are persisted, so loading never retrains or touches the base vectors.
pub struct SaqCodec {
    store: diskann_providers::model::graph::provider::async_::inmem::spherical::SphericalStore,
    transform_seed: u64,
    scaled_shift: Vec<f32>,
    mean_norm: f32,
    pre_scale: f32,
}

impl SaqCodec {
    pub fn new(plan: spherical::iface::Impl<4>, count: usize, transform_seed: u64) -> Self {
        let quantizer = plan.quantizer();
        let scaled_shift = quantizer.shift().to_vec();
        let mean_norm = quantizer.mean_norm().into_inner();
        let pre_scale = quantizer.pre_scale().into_inner();
        let store = plan.create(count, Metric::L2, None);
        Self {
            store,
            transform_seed,
            scaled_shift,
            mean_norm,
            pre_scale,
        }
    }

    pub fn save(&self, metadata: &Path, codes: &Path) -> ANNResult<()> {
        if let Some(parent) = metadata.parent() {
            fs::create_dir_all(parent)?;
        }
        if let Some(parent) = codes.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut meta = BufWriter::new(File::create(metadata)?);
        meta.write_all(b"QG05SAQ1")?;
        meta.write_all(&self.transform_seed.to_le_bytes())?;
        meta.write_all(&self.mean_norm.to_le_bytes())?;
        meta.write_all(&self.pre_scale.to_le_bytes())?;
        meta.write_all(&(self.scaled_shift.len() as u64).to_le_bytes())?;
        for value in &self.scaled_shift {
            meta.write_all(&value.to_le_bytes())?;
        }
        meta.flush()?;

        let mut output = BufWriter::new(File::create(codes)?);
        output.write_all(b"QG05SAC1")?;
        output.write_all(&(self.total() as u64).to_le_bytes())?;
        output.write_all(&(self.code_bytes_per_vector() as u64).to_le_bytes())?;
        for id in 0..self.total() {
            output.write_all(self.store.compressed_vector(id)?)?;
        }
        output.flush()?;
        Ok(())
    }

    pub fn load(metadata: &Path, codes: &Path) -> ANNResult<Self> {
        let mut meta = BufReader::new(File::open(metadata)?);
        let mut magic = [0_u8; 8];
        meta.read_exact(&mut magic)?;
        if &magic != b"QG05SAQ1" {
            return Err(ann_error("invalid SAQ metadata magic"));
        }
        let transform_seed = read_u64(&mut meta)?;
        let mean_norm = read_f32(&mut meta)?;
        let pre_scale = read_f32(&mut meta)?;
        let dim = read_u64(&mut meta)? as usize;
        if dim == 0 {
            return Err(ann_error("SAQ metadata dimension is zero"));
        }
        let mut scaled_shift = Vec::with_capacity(dim);
        for _ in 0..dim {
            scaled_shift.push(read_f32(&mut meta)?);
        }

        let shift = Poly::from_iter(scaled_shift.iter().copied(), GlobalAllocator)
            .map_err(|error| ann_error(error.to_string()))?;
        let mut rng = StdRng::seed_from_u64(transform_seed);
        let metric = Metric::L2
            .try_into()
            .map_err(|error: spherical::UnsupportedMetric| ann_error(error.to_string()))?;
        let quantizer = spherical::SphericalQuantizer::restore_from_scaled_shift(
            shift,
            mean_norm,
            TransformKind::PaddingHadamard {
                target_dim: TargetDim::Natural,
            },
            metric,
            pre_scale,
            &mut rng,
            GlobalAllocator,
        )
        .map_err(|error| ann_error(error.to_string()))?;
        let plan = spherical::iface::Impl::<4>::new(quantizer)
            .map_err(|error| ann_error(error.to_string()))?;

        let mut input = BufReader::new(File::open(codes)?);
        input.read_exact(&mut magic)?;
        if &magic != b"QG05SAC1" {
            return Err(ann_error("invalid SAQ code magic"));
        }
        let count = read_u64(&mut input)? as usize;
        let stored_bytes = read_u64(&mut input)? as usize;
        let codec = Self::new(plan, count, transform_seed);
        if stored_bytes != codec.code_bytes_per_vector() {
            return Err(ann_error(format!(
                "SAQ code size mismatch: file={stored_bytes}, quantizer={}",
                codec.code_bytes_per_vector()
            )));
        }
        let mut buffer = vec![0_u8; stored_bytes];
        for id in 0..count {
            input.read_exact(&mut buffer)?;
            codec.store.set_compressed_vector(id, &buffer)?;
        }
        let mut trailing = [0_u8; 1];
        if input.read(&mut trailing)? != 0 {
            return Err(ann_error("SAQ code file contains trailing bytes"));
        }
        Ok(codec)
    }

    pub fn encode(&self, id: u32, vector: &[f32]) -> ANNResult<()> {
        self.store.set_element(&id, vector)
    }

    pub fn total(&self) -> usize {
        VectorStore::total(&self.store)
    }

    pub fn full_dim(&self) -> usize {
        self.store.input_dim()
    }

    pub fn code_bytes_per_vector(&self) -> usize {
        self.store.bytes()
    }

    pub fn resident_bytes(&self) -> usize {
        self.total() * self.code_bytes_per_vector()
    }

    /// Return the raw compressed code for one node (export-time dump).
    pub fn code_bytes(&self, id: u32) -> ANNResult<&[u8]> {
        self.store.compressed_vector(id as usize)
    }

    /// Rebuild only the quantizer plan from metadata, without loading the
    /// per-node codes (disk-payload variant).
    pub fn load_computer_only(metadata: &Path) -> ANNResult<Self> {
        let mut meta = BufReader::new(File::open(metadata)?);
        let mut magic = [0_u8; 8];
        meta.read_exact(&mut magic)?;
        if &magic != b"QG05SAQ1" {
            return Err(ann_error("invalid SAQ metadata magic"));
        }
        let transform_seed = read_u64(&mut meta)?;
        let mean_norm = read_f32(&mut meta)?;
        let pre_scale = read_f32(&mut meta)?;
        let dim = read_u64(&mut meta)? as usize;
        if dim == 0 {
            return Err(ann_error("SAQ metadata dimension is zero"));
        }
        let mut scaled_shift = Vec::with_capacity(dim);
        for _ in 0..dim {
            scaled_shift.push(read_f32(&mut meta)?);
        }

        let shift = Poly::from_iter(scaled_shift.iter().copied(), GlobalAllocator)
            .map_err(|error| ann_error(error.to_string()))?;
        let mut rng = StdRng::seed_from_u64(transform_seed);
        let metric = Metric::L2
            .try_into()
            .map_err(|error: spherical::UnsupportedMetric| ann_error(error.to_string()))?;
        let quantizer = spherical::SphericalQuantizer::restore_from_scaled_shift(
            shift,
            mean_norm,
            TransformKind::PaddingHadamard {
                target_dim: TargetDim::Natural,
            },
            metric,
            pre_scale,
            &mut rng,
            GlobalAllocator,
        )
        .map_err(|error| ann_error(error.to_string()))?;
        let plan = spherical::iface::Impl::<4>::new(quantizer)
            .map_err(|error| ann_error(error.to_string()))?;
        let store = plan.create(0, Metric::L2, None);
        let mut trailing = [0_u8; 1];
        if meta.read(&mut trailing)? != 0 {
            return Err(ann_error("SAQ metadata contains trailing bytes"));
        }
        Ok(Self {
            store,
            transform_seed,
            scaled_shift,
            mean_norm,
            pre_scale,
        })
    }

    /// The empty store keeps the query computer and exposes the plan-derived
    /// `query_computer` without any per-node codes.
    pub fn computer_store(
        &self,
    ) -> &diskann_providers::model::graph::provider::async_::inmem::spherical::SphericalStore {
        &self.store
    }
}

fn read_f32(reader: &mut impl Read) -> std::io::Result<f32> {
    let mut bytes = [0_u8; 4];
    reader.read_exact(&mut bytes)?;
    Ok(f32::from_le_bytes(bytes))
}

pub struct SaqQuery<'a> {
    store: &'a diskann_providers::model::graph::provider::async_::inmem::spherical::SphericalStore,
    computer: spherical::iface::QueryComputer,
}

impl QueryDistance for SaqQuery<'_> {
    #[inline(always)]
    fn distance(&self, id: u32) -> ANNResult<f32> {
        if id as usize >= VectorStore::total(self.store) {
            return Err(ann_error(format!(
                "SAQ vector id {id} is out of range 0..{}",
                VectorStore::total(self.store)
            )));
        }
        self.computer
            .evaluate_similarity(self.store.get_vector(id as usize)?)
            .map_err(|error| ann_error(error.to_string()))
    }
}

impl ResidentCodec for SaqCodec {
    type Query<'a> = SaqQuery<'a>;

    fn prepare<'a>(
        &'a self,
        query: &[f32],
        _stats: Arc<Mutex<IoStats>>,
    ) -> ANNResult<Self::Query<'a>> {
        Ok(SaqQuery {
            store: &self.store,
            computer: self
                .store
                .query_computer(query, spherical::iface::QueryLayout::ScalarQuantized, true)
                .map_err(|error| ann_error(error.to_string()))?,
        })
    }
}

pub struct PortProvider;

impl DataProvider for PortProvider {
    type Context = DefaultContext;
    type InternalId = u32;
    type ExternalId = u32;
    type Error = ANNError;
    type Guard = NoopGuard<u32>;

    fn to_internal_id(
        &self,
        _context: &Self::Context,
        gid: &Self::ExternalId,
    ) -> Result<Self::InternalId, Self::Error> {
        Ok(*gid)
    }

    fn to_external_id(
        &self,
        _context: &Self::Context,
        id: Self::InternalId,
    ) -> Result<Self::ExternalId, Self::Error> {
        Ok(id)
    }
}

pub struct DiskSearchAccessor<Q> {
    start_point: u32,
    graph: GraphBackend,
    query: Q,
    stats: Arc<Mutex<IoStats>>,
}

impl<Q> HasId for DiskSearchAccessor<Q> {
    type Id = u32;
}

impl<Q: QueryDistance> SearchAccessor for DiskSearchAccessor<Q> {
    fn starting_points(&self) -> impl std::future::Future<Output = ANNResult<Vec<u32>>> + Send {
        std::future::ready(Ok(vec![self.start_point]))
    }

    fn num_starting_points(&self) -> impl std::future::Future<Output = ANNResult<usize>> + Send {
        std::future::ready(Ok(1))
    }

    fn start_point_distances<F>(
        &mut self,
        mut f: F,
    ) -> impl std::future::Future<Output = ANNResult<()>> + Send
    where
        F: FnMut(Self::Id, f32) + Send,
    {
        let distance_start = Instant::now();
        let result = self.query.distance(self.start_point).and_then(|distance| {
            self.stats
                .lock()
                .map_err(|_| ann_error("stats lock poisoned"))?
                .distance_compute_us += distance_start.elapsed().as_secs_f64() * 1_000_000.0;
            self.stats
                .lock()
                .map_err(|_| ann_error("stats lock poisoned"))?
                .distance_evaluations += 1;
            f(self.start_point, distance);
            Ok(())
        });
        std::future::ready(result)
    }

    fn expand_beam<Itr, P, F>(
        &mut self,
        ids: Itr,
        mut pred: P,
        mut on_neighbors: F,
    ) -> impl std::future::Future<Output = ANNResult<()>> + Send
    where
        Itr: Iterator<Item = Self::Id> + Send,
        P: glue::HybridPredicate<Self::Id> + Send + Sync,
        F: FnMut(Self::Id, f32) + Send,
    {
        let ids = ids.collect::<Vec<_>>();
        let result = (|| {
            self.stats
                .lock()
                .map_err(|_| ann_error("stats lock poisoned"))?
                .visited_nodes += ids.len() as u64;
            let (lists, delta) = self.graph.read_nodes(&ids)?;
            {
                let mut total = self
                    .stats
                    .lock()
                    .map_err(|_| ann_error("stats lock poisoned"))?;
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
            for neighbors in lists {
                for id in neighbors {
                    if pred.eval_mut(&id) {
                        let distance_start = Instant::now();
                        let distance = self.query.distance(id)?;
                        let distance_us = distance_start.elapsed().as_secs_f64() * 1_000_000.0;
                        self.stats
                            .lock()
                            .map_err(|_| ann_error("stats lock poisoned"))?
                            .distance_compute_us += distance_us;
                        self.stats
                            .lock()
                            .map_err(|_| ann_error("stats lock poisoned"))?
                            .distance_evaluations += 1;
                        on_neighbors(id, distance);
                    }
                }
            }
            Ok(())
        })();
        std::future::ready(result)
    }
}

#[derive(Clone)]
pub enum BackendFactory {
    Memory {
        bytes: Arc<Vec<u8>>,
        layout: GraphLayout,
    },
    Direct {
        reader: Arc<Mutex<DirectGraphReader>>,
        layout: GraphLayout,
    },
}

impl BackendFactory {
    pub fn memory(path: &Path, layout: GraphLayout) -> ANNResult<Self> {
        let bytes = fs::read(path)?;
        if bytes.len() != layout.page_count * PAGE_SIZE {
            return Err(ann_error("memory backend page file size mismatch"));
        }
        Ok(Self::Memory {
            bytes: Arc::new(bytes),
            layout,
        })
    }

    pub fn direct(path: &Path, layout: GraphLayout) -> ANNResult<Self> {
        Ok(Self::Direct {
            reader: Arc::new(Mutex::new(DirectGraphReader::new(path, layout.clone())?)),
            layout,
        })
    }

    pub fn direct_with_cache(path: &Path, layout: GraphLayout, pages: &[u64]) -> ANNResult<Self> {
        Ok(Self::Direct {
            reader: Arc::new(Mutex::new(DirectGraphReader::with_shared_cache(
                path,
                layout.clone(),
                pages,
            )?)),
            layout,
        })
    }

    pub fn direct_with_loaded_cache(
        path: &Path,
        layout: GraphLayout,
        cache: SharedPageCache,
    ) -> ANNResult<Self> {
        Ok(Self::Direct {
            reader: Arc::new(Mutex::new(DirectGraphReader::with_loaded_shared_cache(
                path,
                layout.clone(),
                cache,
            )?)),
            layout,
        })
    }

    fn create(&self) -> ANNResult<GraphBackend> {
        match self {
            Self::Memory { bytes, layout } => Ok(GraphBackend::Memory(
                MemoryGraphReader::from_shared(bytes.clone(), layout.clone())?,
            )),
            Self::Direct { reader, .. } => {
                reader
                    .lock()
                    .map_err(|_| ann_error("direct graph reader lock poisoned"))?
                    .clear_query_cache();
                Ok(GraphBackend::Direct(reader.clone()))
            }
        }
    }

    fn start_point(&self) -> u32 {
        match self {
            Self::Memory { layout, .. } | Self::Direct { layout, .. } => layout.start_point,
        }
    }
}

pub struct DiskStrategy<'codec, C> {
    codec: &'codec C,
    backend: BackendFactory,
    stats: Arc<Mutex<IoStats>>,
}

impl<'codec, C> DiskStrategy<'codec, C> {
    pub fn new(codec: &'codec C, backend: BackendFactory, stats: Arc<Mutex<IoStats>>) -> Self {
        Self {
            codec,
            backend,
            stats,
        }
    }
}

impl<'a, 'codec: 'a, C> SearchStrategy<'a, PortProvider, &'a [f32]> for DiskStrategy<'codec, C>
where
    C: ResidentCodec,
{
    type SearchAccessor = DiskSearchAccessor<C::Query<'a>>;
    type SearchAccessorError = ANNError;

    fn search_accessor(
        &'a self,
        _provider: &'a PortProvider,
        _context: &'a DefaultContext,
        query: &'a [f32],
    ) -> Result<Self::SearchAccessor, Self::SearchAccessorError> {
        let prepare_start = Instant::now();
        let prepared = self.codec.prepare(query, self.stats.clone())?;
        self.stats
            .lock()
            .map_err(|_| ann_error("stats lock poisoned"))?
            .query_prep_us += prepare_start.elapsed().as_secs_f64() * 1_000_000.0;
        Ok(DiskSearchAccessor {
            start_point: self.backend.start_point(),
            graph: self.backend.create()?,
            query: prepared,
            stats: self.stats.clone(),
        })
    }
}

impl<'a, 'codec: 'a, C> DefaultPostProcessor<'a, PortProvider, &'a [f32]>
    for DiskStrategy<'codec, C>
where
    C: ResidentCodec,
{
    default_post_processor!(Pipeline<FilterStartPoints, glue::CopyIds>);
}

/// Select resident graph pages by breadth-first traversal from the exact
/// entry point.  The page count is the hard budget: caching one node always
/// retains its complete 4-KiB physical page.
pub fn select_bfs_cache_pages(
    page_path: &Path,
    layout: &GraphLayout,
    max_pages: usize,
) -> ANNResult<Vec<u64>> {
    if max_pages == 0 {
        return Ok(Vec::new());
    }
    let mut reader = MemoryGraphReader::new(page_path, layout.clone())?;
    let mut queue = VecDeque::from([layout.start_point]);
    let mut visited_nodes = HashSet::from([layout.start_point]);
    let mut selected_pages = Vec::new();
    let mut selected_set = HashSet::new();
    while let Some(node) = queue.pop_front() {
        let page = layout.page_for(node)?;
        if selected_set.insert(page) {
            selected_pages.push(page);
            if selected_pages.len() == max_pages {
                break;
            }
        }
        let (neighbors, _) = reader.read_nodes(&[node])?;
        for neighbor in &neighbors[0] {
            if visited_nodes.insert(*neighbor) {
                queue.push_back(*neighbor);
            }
        }
    }
    Ok(selected_pages)
}

pub fn new_search_index(
    max_degree: usize,
    build_beam: usize,
) -> ANNResult<DiskANNIndex<PortProvider>> {
    let config = graph::config::Builder::new_with(
        max_degree,
        graph::config::MaxDegree::default_slack(),
        build_beam,
        diskann_vector::distance::Metric::L2.into(),
        |_| {},
    )
    .build()?;
    Ok(DiskANNIndex::new(
        config,
        PortProvider,
        NonZeroUsize::new(1),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use diskann::graph::search::Knn;
    use diskann::graph::{IdDistance, Search};
    use diskann_quantization::scalar::train::ScalarQuantizationParameters;
    use diskann_utils::views::Matrix;
    use std::path::PathBuf;

    struct TestDir(PathBuf);

    impl TestDir {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "qgraph05_disk_port_{}_{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }

        fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    struct RawCodec(Vec<[f32; 2]>);
    struct RawQuery<'a> {
        codec: &'a RawCodec,
        query: [f32; 2],
    }

    impl QueryDistance for RawQuery<'_> {
        fn distance(&self, id: u32) -> ANNResult<f32> {
            let point = self
                .codec
                .0
                .get(id as usize)
                .ok_or_else(|| ann_error("raw test codec id out of range"))?;
            Ok((point[0] - self.query[0]).powi(2) + (point[1] - self.query[1]).powi(2))
        }
    }

    impl ResidentCodec for RawCodec {
        type Query<'a> = RawQuery<'a>;

        fn prepare<'a>(
            &'a self,
            query: &[f32],
            _stats: Arc<Mutex<IoStats>>,
        ) -> ANNResult<Self::Query<'a>> {
            Ok(RawQuery {
                codec: self,
                query: [query[0], query[1]],
            })
        }
    }

    fn write_canonical(path: &Path) {
        let lists: [&[u32]; 5] = [&[1, 2], &[0, 2, 3], &[0, 1, 3], &[1, 2], &[0, 3]];
        let size = 24 + lists.iter().map(|x| 4 + x.len() * 4).sum::<usize>();
        let mut file = File::create(path).unwrap();
        file.write_all(&(size as u64).to_le_bytes()).unwrap();
        file.write_all(&3_u32.to_le_bytes()).unwrap();
        file.write_all(&4_u32.to_le_bytes()).unwrap();
        file.write_all(&1_u64.to_le_bytes()).unwrap();
        for list in lists {
            file.write_all(&(list.len() as u32).to_le_bytes()).unwrap();
            for id in list {
                file.write_all(&id.to_le_bytes()).unwrap();
            }
        }
    }

    #[test]
    fn pq_codec_uses_official_kernel_and_roundtrips() {
        let temp = TestDir::new();
        let table = FixedChunkPQTable::new(
            2,
            vec![0.0, 0.0, 1.0, 1.0, 3.0, 3.0, 8.0, 8.0].into_boxed_slice(),
            vec![0_usize, 2].into_boxed_slice(),
        )
        .unwrap();
        let codec = PqCodec::new(table, 3);
        codec.encode(0, &[0.1, 0.2]).unwrap();
        codec.encode(1, &[3.1, 2.9]).unwrap();
        codec.encode(2, &[7.9, 8.1]).unwrap();

        let before = codec.prepare(&[2.9, 3.2]).unwrap();
        let expected = (0..3)
            .map(|id| before.distance(id).unwrap())
            .collect::<Vec<_>>();
        assert!((expected[1] - 0.05_f32).abs() < 1e-6);

        let pivots = temp.path().join("pq_pivots.bin");
        let codes = temp.path().join("pq_codes.bin");
        codec.save(&pivots, &codes).unwrap();
        let loaded = PqCodec::load(&pivots, &codes).unwrap();
        let after = loaded.prepare(&[2.9, 3.2]).unwrap();
        let actual = (0..3)
            .map(|id| after.distance(id).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(actual, expected);
        assert_eq!(loaded.total(), 3);
        assert_eq!(loaded.full_dim(), 2);
        assert_eq!(loaded.code_bytes_per_vector(), 1);
    }

    #[test]
    fn sq_codec_uses_official_kernel_and_roundtrips() {
        let temp = TestDir::new();
        let matrix = Matrix::try_from(
            vec![0.0_f32, 0.5, 1.0, 1.5, 3.0, 3.5].into_boxed_slice(),
            3,
            2,
        )
        .unwrap();
        let quantizer = ScalarQuantizationParameters::default().train(matrix.as_view());
        let codec = SqCodec::new(quantizer, 3);
        for id in 0..3 {
            codec.encode(id as u32, matrix.row(id)).unwrap();
        }
        let before = codec.prepare(&[1.1, 1.4]).unwrap();
        let expected = (0..3)
            .map(|id| before.distance(id).unwrap())
            .collect::<Vec<_>>();

        let prefix = temp.path().join("sq");
        codec.save(&prefix).unwrap();
        let loaded = SqCodec::load(&prefix).unwrap();
        let after = loaded.prepare(&[1.1, 1.4]).unwrap();
        let actual = (0..3)
            .map(|id| after.distance(id).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(actual, expected);
        assert_eq!(loaded.total(), 3);
        assert_eq!(loaded.full_dim(), 2);
        assert!(loaded.code_bytes_per_vector() >= 1);
    }

    #[test]
    fn saq_codec_reconstructs_transform_and_roundtrips_codes() {
        let temp = TestDir::new();
        let matrix = Matrix::try_from(
            vec![
                0.2_f32, 0.5, 0.7, 0.9, 1.0, 1.5, 1.7, 1.9, 3.0, 3.5, 3.7, 3.9,
            ]
            .into_boxed_slice(),
            3,
            4,
        )
        .unwrap();
        let seed = 20260813;
        let mut rng = StdRng::seed_from_u64(seed);
        let quantizer = spherical::SphericalQuantizer::train(
            matrix.as_view(),
            TransformKind::PaddingHadamard {
                target_dim: TargetDim::Natural,
            },
            Metric::L2.try_into().unwrap(),
            spherical::PreScale::ReciprocalMeanNorm,
            &mut rng,
            GlobalAllocator,
        )
        .unwrap();
        let plan = spherical::iface::Impl::<4>::new(quantizer).unwrap();
        let codec = SaqCodec::new(plan, 3, seed);
        for id in 0..3 {
            codec.encode(id as u32, matrix.row(id)).unwrap();
        }
        let before = codec.prepare(&[1.1, 1.4, 1.6, 1.8]).unwrap();
        let expected = (0..3)
            .map(|id| before.distance(id).unwrap())
            .collect::<Vec<_>>();

        let metadata = temp.path().join("saq.meta");
        let codes = temp.path().join("saq.codes");
        codec.save(&metadata, &codes).unwrap();
        let loaded = SaqCodec::load(&metadata, &codes).unwrap();
        let after = loaded.prepare(&[1.1, 1.4, 1.6, 1.8]).unwrap();
        let actual = (0..3)
            .map(|id| after.distance(id).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(actual, expected);
        assert_eq!(loaded.total(), 3);
        assert_eq!(loaded.full_dim(), 4);
    }

    fn search<C: ResidentCodec>(
        codec: &C,
        backend: BackendFactory,
    ) -> (Vec<u32>, Vec<f32>, IoStats) {
        let stats = Arc::new(Mutex::new(IoStats::default()));
        let strategy = DiskStrategy::new(codec, backend, stats.clone());
        let index = new_search_index(3, 8).unwrap();
        let query = [0.0_f32, 0.0];
        let mut ids = vec![0_u32; 4];
        let mut distances = vec![0_f32; 4];
        let mut output = IdDistance::new(&mut ids, &mut distances);
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        runtime
            .block_on(Knn::new(4, Some(2)).unwrap().search(
                &index,
                &strategy,
                Pipeline::new(FilterStartPoints, glue::CopyIds),
                &DefaultContext,
                query.as_slice(),
                &mut output,
            ))
            .unwrap();
        let final_stats = *stats.lock().unwrap();
        (ids, distances, final_stats)
    }

    #[test]
    fn graph_pages_memory_and_official_direct_reader_match() {
        let tmp = TestDir::new();
        let source = tmp.path().join("graph.bin");
        let pages = tmp.path().join("index.pages");
        write_canonical(&source);
        let layout = export_shared_graph(&source, &pages).unwrap();
        assert_eq!(layout.node_count, 5);
        assert_eq!(fs::metadata(&pages).unwrap().len() as usize % PAGE_SIZE, 0);

        let codec = RawCodec(vec![
            [1.0, 0.0],
            [0.5, 0.0],
            [0.0, 0.5],
            [2.0, 0.0],
            [3.0, 0.0],
        ]);
        let memory = search(
            &codec,
            BackendFactory::memory(&pages, layout.clone()).unwrap(),
        );
        let direct = search(&codec, BackendFactory::direct(&pages, layout).unwrap());
        assert_eq!(memory.0, direct.0);
        assert_eq!(memory.1, direct.1);
        assert!(direct.2.requests > 0);
        assert!(direct.2.sectors_4k > 0);
        assert_eq!(direct.2.bytes_read, direct.2.sectors_4k * PAGE_SIZE as u64);
    }
}
