//! Disk-payload variants of the 05B PQ/SQ/SAQ baselines.
//!
//! The resident baselines keep the whole 4-bit navigation payload in DRAM.
//! These disk variants keep only the (small) codebook/quantizer in memory and
//! store the per-node codes in a page-aligned file that is read on demand
//! through the same O_DIRECT/libaio reader and per-query page cache that the
//! Ours payload uses.  The graph traversal itself is unchanged (DiskANN
//! `Knn` over the shared graph), so the only difference from the resident
//! variant is where the codes live.

use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use diskann::ANNResult;
use diskann_providers::model::graph::provider::async_::common::CreateVectorStore;
use diskann_providers::model::graph::provider::async_::inmem::{
    SQQueryComputer, WithBits,
};
use diskann_providers::model::pq::distance::QueryComputer as PqQueryComputer;
use diskann_providers::model::pq::FixedChunkPQTable;
use diskann_quantization::scalar::{CompensatedVectorRef, ScalarQuantizer};
use diskann_quantization::spherical;
use diskann_vector::{distance::Metric, PreprocessedDistanceFunction};

use super::{DirectAioHandle, IoStats, PAGE_SIZE};

fn ann_error(message: impl Into<String>) -> diskann::ANNError {
    diskann::ANNError::message(message.into())
}

const PQ_TABLE_MAGIC: &[u8; 8] = b"QG05PQT1";
const SQ_TABLE_MAGIC: &[u8; 8] = b"QG05SQT1";

/// Page layout for one fixed-size code record per node.
#[derive(Debug, Clone)]
pub struct BaselinePayloadLayout {
    pub code_bytes: usize,
    pub record_count: usize,
    pub records_per_page: usize,
    pub page_count: usize,
}

impl BaselinePayloadLayout {
    pub fn new(record_count: usize, code_bytes: usize) -> ANNResult<Self> {
        if record_count == 0 || code_bytes == 0 {
            return Err(ann_error(
                "baseline disk payload requires a positive record count and code size",
            ));
        }
        let records_per_page = PAGE_SIZE / code_bytes;
        let page_count = if records_per_page > 0 {
            record_count.div_ceil(records_per_page)
        } else {
            record_count * code_bytes.div_ceil(PAGE_SIZE)
        };
        Ok(Self {
            code_bytes,
            record_count,
            records_per_page,
            page_count,
        })
    }

    fn pages_per_record(&self) -> usize {
        if self.records_per_page > 0 {
            1
        } else {
            self.code_bytes.div_ceil(PAGE_SIZE)
        }
    }

    fn pages_for(&self, id: u32) -> ANNResult<std::ops::RangeInclusive<u64>> {
        let id = id as usize;
        if id >= self.record_count {
            return Err(ann_error(format!(
                "baseline payload id {id} is out of range 0..{}",
                self.record_count
            )));
        }
        if self.records_per_page > 0 {
            let page = (id / self.records_per_page) as u64;
            Ok(page..=page)
        } else {
            let begin = (id * self.pages_per_record()) as u64;
            Ok(begin..=begin + self.pages_per_record() as u64 - 1)
        }
    }

    fn extract(&self, pages: &HashMap<u64, Box<[u8; PAGE_SIZE]>>, id: u32) -> ANNResult<Vec<u8>> {
        let id = id as usize;
        let mut output = vec![0_u8; self.code_bytes];
        if self.records_per_page > 0 {
            let page = (id / self.records_per_page) as u64;
            let slot = id % self.records_per_page;
            let begin = slot * self.code_bytes;
            let page_bytes = pages
                .get(&page)
                .ok_or_else(|| ann_error(format!("baseline payload page {page} was not read")))?;
            output.copy_from_slice(&page_bytes[begin..begin + self.code_bytes]);
            Ok(output)
        } else {
            let absolute = id * self.code_bytes;
            let mut copied = 0;
            while copied < self.code_bytes {
                let offset = absolute + copied;
                let page_id = (offset / PAGE_SIZE) as u64;
                let in_page = offset % PAGE_SIZE;
                let take = (PAGE_SIZE - in_page).min(self.code_bytes - copied);
                let page_bytes = pages.get(&page_id).ok_or_else(|| {
                    ann_error(format!("baseline payload page {page_id} was not read"))
                })?;
                output[copied..copied + take]
                    .copy_from_slice(&page_bytes[in_page..in_page + take]);
                copied += take;
            }
            Ok(output)
        }
    }
}

/// Serialize `codes` (one `code_bytes`-wide record per node, in node order)
/// into a page-aligned file, padding the tail with zeros.
pub fn write_paged_payload(
    path: &Path,
    layout: &BaselinePayloadLayout,
    codes: &[Vec<u8>],
) -> ANNResult<()> {
    if codes.len() != layout.record_count {
        return Err(ann_error(format!(
            "baseline payload record count {} does not match layout {}",
            codes.len(),
            layout.record_count
        )));
    }
    let mut output = BufWriter::new(File::create(path)?);
    let mut page = vec![0_u8; PAGE_SIZE];
    if layout.records_per_page > 0 {
        for (index, code) in codes.iter().enumerate() {
            if code.len() != layout.code_bytes {
                return Err(ann_error("baseline payload record size mismatch"));
            }
            let slot = index % layout.records_per_page;
            page[slot * layout.code_bytes..(slot + 1) * layout.code_bytes]
                .copy_from_slice(code);
            if slot == layout.records_per_page - 1 {
                output.write_all(&page)?;
                page.fill(0);
            }
        }
        if layout.record_count % layout.records_per_page != 0 {
            output.write_all(&page)?;
        }
    } else {
        for code in codes {
            output.write_all(code)?;
            let remainder = code.len() % PAGE_SIZE;
            if remainder != 0 {
                output.write_all(&vec![0_u8; PAGE_SIZE - remainder])?;
            }
        }
    }
    output.flush()?;
    Ok(())
}

#[derive(Clone)]
pub enum BaselinePayloadFactory {
    Memory {
        bytes: Arc<Vec<u8>>,
        layout: BaselinePayloadLayout,
    },
    Direct {
        path: Arc<PathBuf>,
        layout: BaselinePayloadLayout,
    },
}

impl BaselinePayloadFactory {
    pub fn memory(path: &Path, layout: BaselinePayloadLayout) -> ANNResult<Self> {
        let bytes = fs::read(path)?;
        if bytes.len() != layout.page_count * PAGE_SIZE {
            return Err(ann_error("baseline memory payload file size mismatch"));
        }
        Ok(Self::Memory {
            bytes: Arc::new(bytes),
            layout,
        })
    }

    pub fn direct(path: &Path, layout: BaselinePayloadLayout) -> ANNResult<Self> {
        let size = fs::metadata(path)?.len() as usize;
        if size != layout.page_count * PAGE_SIZE {
            return Err(ann_error("baseline direct payload file size mismatch"));
        }
        Ok(Self::Direct {
            path: Arc::new(path.to_path_buf()),
            layout,
        })
    }

    pub fn create(&self) -> ANNResult<BaselinePayloadReader> {
        match self {
            Self::Memory { bytes, layout } => Ok(BaselinePayloadReader {
                inner: BaselinePayloadReaderInner::Memory {
                    bytes: bytes.clone(),
                    layout: layout.clone(),
                },
            }),
            Self::Direct { path, layout } => Ok(BaselinePayloadReader {
                inner: BaselinePayloadReaderInner::Direct {
                    reader: DirectAioHandle::new(path)?,
                    layout: layout.clone(),
                    query_cache: HashMap::new(),
                },
            }),
        }
    }
}

pub struct BaselinePayloadReader {
    inner: BaselinePayloadReaderInner,
}

enum BaselinePayloadReaderInner {
    Memory {
        bytes: Arc<Vec<u8>>,
        layout: BaselinePayloadLayout,
    },
    Direct {
        reader: DirectAioHandle,
        layout: BaselinePayloadLayout,
        query_cache: HashMap<u64, Box<[u8; PAGE_SIZE]>>,
    },
}

impl BaselinePayloadReader {
    pub fn begin_query(&mut self) {
        if let BaselinePayloadReaderInner::Direct { query_cache, .. } = &mut self.inner {
            query_cache.clear();
        }
    }

    /// Fetch the code for one node; returns the code bytes and the IO delta.
    pub fn read_code(&mut self, id: u32) -> ANNResult<(Vec<u8>, IoStats)> {
        match &mut self.inner {
            BaselinePayloadReaderInner::Memory { bytes, layout } => {
                let page_id = if layout.records_per_page > 0 {
                    (id as usize / layout.records_per_page) as u64
                } else {
                    (id as usize * layout.pages_per_record()) as u64
                };
                let begin = page_id as usize * PAGE_SIZE;
                let end = begin + PAGE_SIZE;
                let mut pages = HashMap::new();
                let mut boxed = Box::new([0_u8; PAGE_SIZE]);
                boxed.copy_from_slice(&bytes[begin..end]);
                pages.insert(page_id, boxed);
                let code = layout.extract(&pages, id)?;
                Ok((code, IoStats::default()))
            }
            BaselinePayloadReaderInner::Direct {
                reader,
                layout,
                query_cache,
            } => {
                let started = Instant::now();
                let pages = layout.pages_for(id)?;
                let mut local = HashMap::new();
                let mut stats = IoStats::default();
                for page_id in pages {
                    if let Some(page) = query_cache.get(&page_id) {
                        stats.query_cache_hits += 1;
                        local.insert(page_id, page.clone());
                        continue;
                    }
                    stats.query_cache_misses += 1;
                    let (bytes, delta) = reader.read_pages(&[page_id])?;
                    stats.requests += delta.submitted_requests;
                    stats.sectors_4k += delta.unique_pages;
                    stats.bytes_read += delta.bytes_read;
                    stats.coalesced_requests += delta.coalesced_requests;
                    stats.duplicate_pages_removed += delta.duplicate_pages_removed;
                    let mut page = Box::new([0_u8; PAGE_SIZE]);
                    page.copy_from_slice(&bytes[..PAGE_SIZE]);
                    query_cache.insert(page_id, page.clone());
                    local.insert(page_id, page);
                }
                stats.io_wait_us = started.elapsed().as_secs_f64() * 1_000_000.0;
                let code = layout.extract(&local, id)?;
                Ok((code, stats))
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BaselineKind {
    Pq,
    Sq,
    Saq,
}

/// Small in-memory codec state (codebook/quantizer/plan).  The per-node codes
/// are not resident; they are fetched through `BaselinePayloadFactory`.
pub struct BaselineCodec {
    kind: BaselineKind,
    dim: usize,
    pq_table: Option<FixedChunkPQTable>,
    sq_quantizer: Option<ScalarQuantizer>,
    sq_store: Option<diskann_providers::model::graph::provider::async_::inmem::SQStore<4>>,
    saq_codec: Option<Arc<super::SaqCodec>>,
    payload: BaselinePayloadFactory,
}

impl BaselineCodec {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        kind: BaselineKind,
        dim: usize,
        _layout: BaselinePayloadLayout,
        pq_table: Option<FixedChunkPQTable>,
        sq_quantizer: Option<ScalarQuantizer>,
        saq_codec: Option<Arc<super::SaqCodec>>,
        payload: BaselinePayloadFactory,
    ) -> ANNResult<Self> {
        let sq_store = match sq_quantizer.as_ref() {
            Some(quantizer) => Some(
                WithBits::<4>::new(quantizer.clone()).create(0, Metric::L2, None),
            ),
            None => None,
        };
        Ok(Self {
            kind,
            dim,
            pq_table,
            sq_quantizer,
            sq_store,
            saq_codec,
            payload,
        })
    }

    pub fn resident_bytes(&self) -> usize {
        match self.kind {
            BaselineKind::Pq => self
                .pq_table
                .as_ref()
                .map(|table| table.get_pq_table().len() * 4)
                .unwrap_or(0),
            BaselineKind::Sq => self
                .sq_quantizer
                .as_ref()
                .map(|quantizer| quantizer.shift().len() * 4 + 8)
                .unwrap_or(0),
            BaselineKind::Saq => 0,
        }
    }

    pub fn prepare(
        &self,
        query: &[f32],
        stats: Arc<Mutex<IoStats>>,
    ) -> ANNResult<BaselinePreparedQuery<'_>> {
        let mut reader = self.payload.create()?;
        reader.begin_query();
        let computer = match self.kind {
            BaselineKind::Pq => {
                let table = self
                    .pq_table
                    .as_ref()
                    .ok_or_else(|| ann_error("PQ table missing"))?;
                BaselineComputer::Pq(PqQueryComputer::new(table, Metric::L2, query, None)?)
            }
            BaselineKind::Sq => {
                let store = self
                    .sq_store
                    .as_ref()
                    .ok_or_else(|| ann_error("SQ store missing"))?;
                BaselineComputer::Sq {
                    computer: store
                        .query_computer(query, true)
                        .map_err(|error| ann_error(error.to_string()))?,
                    dim: self.dim,
                }
            }
            BaselineKind::Saq => {
                let store = self
                    .saq_codec
                    .as_ref()
                    .map(|codec| codec.computer_store())
                    .ok_or_else(|| ann_error("SAQ store missing"))?;
                BaselineComputer::Saq(
                    store
                        .query_computer(
                            query,
                            spherical::iface::QueryLayout::ScalarQuantized,
                            true,
                        )
                        .map_err(|error| ann_error(error.to_string()))?,
                )
            }
        };
        Ok(BaselinePreparedQuery {
            computer,
            payload: Mutex::new(reader),
            stats,
        })
    }
}

impl super::ResidentCodec for BaselineCodec {
    type Query<'a> = BaselinePreparedQuery<'a>
    where
        Self: 'a;

    fn prepare<'a>(
        &'a self,
        query: &[f32],
        stats: Arc<Mutex<IoStats>>,
    ) -> ANNResult<Self::Query<'a>> {
        self.prepare(query, stats)
    }
}

pub enum BaselineComputer<'a> {
    Pq(PqQueryComputer<'a>),
    Sq {
        computer: SQQueryComputer<4>,
        dim: usize,
    },
    Saq(spherical::iface::QueryComputer),
}

impl BaselineComputer<'_> {
    fn distance(&self, code: &[u8]) -> ANNResult<f32> {
        match self {
            Self::Pq(computer) => Ok(computer.evaluate_similarity(code)),
            Self::Sq { computer, dim } => {
                let reference = CompensatedVectorRef::<4>::from_canonical_front(code, *dim)
                    .map_err(|error| ann_error(error.to_string()))?;
                Ok(computer.evaluate_similarity(reference))
            }
            Self::Saq(computer) => computer
                .evaluate_similarity(spherical::iface::Opaque::new(code))
                .map_err(|error| ann_error(error.to_string())),
        }
    }
}

pub struct BaselinePreparedQuery<'a> {
    computer: BaselineComputer<'a>,
    payload: Mutex<BaselinePayloadReader>,
    stats: Arc<Mutex<IoStats>>,
}

impl super::QueryDistance for BaselinePreparedQuery<'_> {
    fn distance(&self, id: u32) -> ANNResult<f32> {
        let mut payload = self
            .payload
            .lock()
            .map_err(|_| ann_error("baseline payload mutex poisoned"))?;
        let (code, delta) = payload.read_code(id)?;
        {
            let mut total = self
                .stats
                .lock()
                .map_err(|_| ann_error("baseline stats mutex poisoned"))?;
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
        self.computer.distance(&code)
    }
}

// ---------------------------------------------------------------------------
// Export helpers: write page-aligned payload files and small codebook files.
// ---------------------------------------------------------------------------

pub fn export_pq_disk_payload(
    codec: &super::PqCodec,
    payload_path: &Path,
    codebook_path: &Path,
) -> ANNResult<BaselinePayloadLayout> {
    let table = codec.table();
    let dim = table.get_dim();
    let num_chunks = table.get_num_chunks();
    let num_centers = table.get_num_centers();
    let pivots = table.get_pq_table();
    let offsets = table.get_chunk_offsets();
    let mut out = BufWriter::new(File::create(codebook_path)?);
    out.write_all(PQ_TABLE_MAGIC)?;
    out.write_all(&(dim as u64).to_le_bytes())?;
    out.write_all(&(num_chunks as u64).to_le_bytes())?;
    out.write_all(&(num_centers as u64).to_le_bytes())?;
    out.write_all(&(offsets.len() as u64).to_le_bytes())?;
    for offset in offsets {
        out.write_all(&(*offset as u64).to_le_bytes())?;
    }
    for value in pivots {
        out.write_all(&value.to_le_bytes())?;
    }
    out.flush()?;

    let code_bytes = table.get_num_chunks();
    let layout = BaselinePayloadLayout::new(codec.total(), code_bytes)?;
    let codes = (0..codec.total())
        .map(|id| codec.code_bytes(id as u32).to_vec())
        .collect::<Vec<_>>();
    write_paged_payload(payload_path, &layout, &codes)?;
    Ok(layout)
}

pub fn load_pq_codebook(path: &Path) -> ANNResult<FixedChunkPQTable> {
    let mut input = BufReader::new(File::open(path)?);
    let mut magic = [0_u8; 8];
    input.read_exact(&mut magic)?;
    if &magic != PQ_TABLE_MAGIC {
        return Err(ann_error("invalid PQ codebook magic"));
    }
    let dim = read_u64(&mut input)? as usize;
    let num_chunks = read_u64(&mut input)? as usize;
    let num_centers = read_u64(&mut input)? as usize;
    let offset_len = read_u64(&mut input)? as usize;
    if dim == 0 || num_chunks == 0 || num_centers == 0 || offset_len < 2 {
        return Err(ann_error("PQ codebook header is malformed"));
    }
    let mut offsets = Vec::with_capacity(offset_len);
    for _ in 0..offset_len {
        offsets.push(read_u64(&mut input)? as usize);
    }
    if offsets[0] != 0
        || *offsets.last().unwrap() != dim
        || offsets.windows(2).any(|w| w[0] >= w[1])
    {
        return Err(ann_error("PQ codebook chunk offsets are invalid"));
    }
    let pivot_count = num_centers * dim;
    let mut pivots = Vec::with_capacity(pivot_count);
    for _ in 0..pivot_count {
        pivots.push(read_f32(&mut input)?);
    }
    let mut trailing = [0_u8; 1];
    if input.read(&mut trailing)? != 0 {
        return Err(ann_error("PQ codebook contains trailing bytes"));
    }
    FixedChunkPQTable::new(dim, pivots.into_boxed_slice(), offsets.into_boxed_slice())
}

pub fn export_sq_disk_payload(
    codec: &super::SqCodec,
    codes: &[Vec<u8>],
    payload_path: &Path,
    codebook_path: &Path,
) -> ANNResult<BaselinePayloadLayout> {
    let quantizer = codec.quantizer();
    let mut out = BufWriter::new(File::create(codebook_path)?);
    out.write_all(SQ_TABLE_MAGIC)?;
    out.write_all(&quantizer.scale().to_le_bytes())?;
    let mean = quantizer.mean_norm().unwrap_or(f32::NAN);
    out.write_all(&mean.to_le_bytes())?;
    out.write_all(&(quantizer.dim() as u64).to_le_bytes())?;
    for value in quantizer.shift() {
        out.write_all(&value.to_le_bytes())?;
    }
    out.flush()?;

    let code_bytes = codec.code_bytes_per_vector();
    let layout = BaselinePayloadLayout::new(codec.total(), code_bytes)?;
    write_paged_payload(payload_path, &layout, codes)?;
    Ok(layout)
}

pub fn load_sq_codebook(path: &Path) -> ANNResult<ScalarQuantizer> {
    let mut input = BufReader::new(File::open(path)?);
    let mut magic = [0_u8; 8];
    input.read_exact(&mut magic)?;
    if &magic != SQ_TABLE_MAGIC {
        return Err(ann_error("invalid SQ codebook magic"));
    }
    let scale = read_f32(&mut input)?;
    let mean = read_f32(&mut input)?;
    let dim = read_u64(&mut input)? as usize;
    if dim == 0 {
        return Err(ann_error("SQ codebook dimension is zero"));
    }
    let mut shift = Vec::with_capacity(dim);
    for _ in 0..dim {
        shift.push(read_f32(&mut input)?);
    }
    let mut trailing = [0_u8; 1];
    if input.read(&mut trailing)? != 0 {
        return Err(ann_error("SQ codebook contains trailing bytes"));
    }
    let mean_norm = if mean.is_nan() { None } else { Some(mean) };
    Ok(ScalarQuantizer::new(scale, shift, mean_norm))
}

pub fn export_saq_disk_payload(
    codec: &super::SaqCodec,
    payload_path: &Path,
) -> ANNResult<BaselinePayloadLayout> {
    let code_bytes = codec.code_bytes_per_vector();
    let layout = BaselinePayloadLayout::new(codec.total(), code_bytes)?;
    let mut codes = Vec::with_capacity(codec.total());
    for id in 0..codec.total() {
        codes.push(codec.code_bytes(id as u32)?.to_vec());
    }
    write_paged_payload(payload_path, &layout, &codes)?;
    Ok(layout)
}

fn read_u64(reader: &mut impl Read) -> std::io::Result<u64> {
    let mut bytes = [0_u8; 8];
    reader.read_exact(&mut bytes)?;
    Ok(u64::from_le_bytes(bytes))
}

fn read_f32(reader: &mut impl Read) -> std::io::Result<f32> {
    let mut bytes = [0_u8; 4];
    reader.read_exact(&mut bytes)?;
    Ok(f32::from_le_bytes(bytes))
}
