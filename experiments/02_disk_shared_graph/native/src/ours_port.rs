//! Exact experiment-02 ExRaBitQ search port for the 05B shared disk graph.
//!
//! Only storage is changed: the resident DB1/factor sidecar is evaluated by
//! the original INT8-query C++ kernels, while compact 4-bit and residual
//! records are either resident or fetched from fixed 4-KiB payload pages.

use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use diskann::{ANNError, ANNResult};

use crate::config::QueryCoarseCodec;
use crate::ours_diskann::{
    QueryComputer, RabitqDistanceInterval, RabitqPaperEstimate, RabitqSpace,
    AdaptiveRoute, load_adaptive_route,
};

use super::{BackendFactory, DirectAioHandle, GraphBackend, IoStats, QueryPageCache, PAGE_SIZE};

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
    pub records_per_page: usize,
    pub page_count: usize,
}

impl OursPayloadLayout {
    fn new(record_count: usize, compact_bytes: usize, residual_bytes: usize) -> ANNResult<Self> {
        let record_bytes = compact_bytes + residual_bytes;
        if record_bytes == 0 {
            return Err(ann_error("Ours payload record size is zero"));
        }
        let records_per_page = PAGE_SIZE / record_bytes;
        let page_count = if records_per_page > 0 {
            record_count.div_ceil(records_per_page)
        } else {
            record_count * record_bytes.div_ceil(PAGE_SIZE)
        };
        Ok(Self {
            record_count,
            compact_bytes,
            residual_bytes,
            record_bytes,
            records_per_page,
            page_count,
        })
    }

    fn pages_per_record(&self) -> usize {
        if self.records_per_page > 0 {
            1
        } else {
            self.record_bytes.div_ceil(PAGE_SIZE)
        }
    }

    fn record_offset(&self, id: u32) -> ANNResult<usize> {
        if id as usize >= self.record_count {
            return Err(ann_error(format!(
                "Ours payload id {id} exceeds record_count {}",
                self.record_count
            )));
        }
        if self.records_per_page > 0 {
            let page = id as usize / self.records_per_page;
            let slot = id as usize % self.records_per_page;
            Ok(page * PAGE_SIZE + slot * self.record_bytes)
        } else {
            Ok(id as usize * self.pages_per_record() * PAGE_SIZE)
        }
    }

    fn pages_for(&self, id: u32) -> ANNResult<std::ops::RangeInclusive<u64>> {
        let begin = self.record_offset(id)?;
        let end = begin + self.record_bytes - 1;
        Ok((begin / PAGE_SIZE) as u64..=(end / PAGE_SIZE) as u64)
    }

    fn extract(&self, pages: &HashMap<u64, Box<[u8; PAGE_SIZE]>>, id: u32) -> ANNResult<Vec<u8>> {
        let begin = self.record_offset(id)?;
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
    /// The payload file stores records in DiskANN-style 4 KiB sectors, so small
    /// records never straddle sector boundaries.
    fn extract_from_bytes(&self, bytes: &[u8], id: u32) -> ANNResult<Vec<u8>> {
        let begin = self.record_offset(id)?;
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
        let layout = OursPayloadLayout::new(self.count, compact_bytes, residual_bytes)?;

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
        if layout.records_per_page > 0 {
            let mut page = vec![0_u8; PAGE_SIZE];
            let mut slot = 0_usize;
            for id in 0..self.count {
                let begin = slot * layout.record_bytes;
                let compact_begin = id * compact_bytes;
                let residual_begin = id * residual_bytes;
                page[begin..begin + compact_bytes]
                    .copy_from_slice(&self.compact[compact_begin..compact_begin + compact_bytes]);
                page[begin + compact_bytes..begin + layout.record_bytes].copy_from_slice(
                    &self.residual[residual_begin..residual_begin + residual_bytes],
                );
                slot += 1;
                if slot == layout.records_per_page {
                    payload.write_all(&page)?;
                    page.fill(0);
                    slot = 0;
                }
            }
            if slot != 0 {
                payload.write_all(&page)?;
            }
        } else {
            let mut record = vec![0_u8; layout.pages_per_record() * PAGE_SIZE];
            for id in 0..self.count {
                let compact_begin = id * compact_bytes;
                let residual_begin = id * residual_bytes;
                record[..compact_bytes]
                    .copy_from_slice(&self.compact[compact_begin..compact_begin + compact_bytes]);
                record[compact_bytes..layout.record_bytes].copy_from_slice(
                    &self.residual[residual_begin..residual_begin + residual_bytes],
                );
                payload.write_all(&record)?;
                record.fill(0);
            }
        }
        payload.flush()?;

        let resident_bytes = msb.len() + factors.len() + centroids.len() * 4;
        Ok((layout, resident_bytes))
    }
}

pub struct OursResidentCodec {
    pub pca: Option<super::pca::PcaRoute>,
    pub locality: Option<Arc<super::locality::LocalityLayout>>,
    pub route_keep: usize,
    pub route_ratio: f32,
    pub route_revisit: bool,
    pub route_trace_dir: Option<std::path::PathBuf>,
    adaptive: Option<AdaptiveRoute>,
    pub space: Arc<RabitqSpace>,
    pub layout: OursPayloadLayout,
    msb: Vec<u8>,
    factors: Vec<u8>,
    pub centroid_bytes: usize,
    pub routing: Option<super::routing::RoutingReader>,
}

impl OursResidentCodec {
    pub fn load_route_norms(&mut self, path: &Path, calibrate: bool) -> ANNResult<()> {
        self.adaptive.as_mut().ok_or_else(|| ann_error("route norms require adaptive route"))?
            .load_norms(path, calibrate)
    }
    pub fn load(metadata_path: &Path, sidecar_path: &Path) -> ANNResult<Self> {
        Self::load_with_route(metadata_path, sidecar_path, None)
    }

    pub fn load_with_route(metadata_path: &Path, sidecar_path: &Path,
        route: Option<(&Path, usize)>) -> ANNResult<Self> {
        Self::load_planned(metadata_path, sidecar_path, route, super::routing::RoutingPlan::Resident)
    }

    pub fn load_planned(metadata_path: &Path, sidecar_path: &Path,
        route: Option<(&Path, usize)>, plan: super::routing::RoutingPlan) -> ANNResult<Self> {
        Self::load_planned_pca(metadata_path, sidecar_path, route, plan, None)
    }

    pub fn load_planned_pca(metadata_path: &Path, sidecar_path: &Path,
        route: Option<(&Path, usize)>, plan: super::routing::RoutingPlan,
        pca_dir: Option<&Path>) -> ANNResult<Self> {
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
        let centroid_values=dim.checked_mul(centroid_count).ok_or_else(||ann_error("centroid size overflow"))?;
        let mut centroids = Vec::with_capacity(centroid_values);
        for _ in 0..centroid_values {
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

        if let Some((dir, route_dim)) = route {
            if pca_dir.is_some() {return Err(ann_error("PCA and legacy adaptive route are exclusive"));}
            let adaptive = load_adaptive_route(dir, record_count, route_dim, dim)?;
            return Ok(Self {
                pca: None,
                locality: None,
                space, layout: OursPayloadLayout::new(record_count, compact_bytes, residual_bytes)?,
                msb: Vec::new(), factors: Vec::new(), centroid_bytes: centroids.len() * 4,
                adaptive: Some(adaptive),
                routing: None,
                route_keep: 0,
                route_ratio: 0.0,
                route_revisit: false,
                route_trace_dir: None,
            });
        }
        if let Some(dir) = pca_dir {
            if !matches!(plan, super::routing::RoutingPlan::Resident) {
                return Err(ann_error("formal PCA route must be resident"));
            }
            return Ok(Self {
                pca: Some(super::pca::PcaRoute::load(dir,record_count,dim).map_err(ann_error)?),
                adaptive: None, routing: None, locality: None,
                route_keep: 0, route_ratio: 0., route_revisit: false, route_trace_dir: None,
                space, layout: OursPayloadLayout::new(record_count,compact_bytes,residual_bytes)?,
                msb: Vec::new(), factors: Vec::new(), centroid_bytes: centroids.len()*4,
            });
        }
        let mut sidecar = BufReader::new(File::open(sidecar_path)?);
        sidecar.read_exact(&mut magic)?;
        if &magic != SIDECAR_MAGIC {
            return Err(ann_error("invalid Ours sidecar magic"));
        }
        let stored_msb = read_u64(&mut sidecar)? as usize;
        let stored_factors = read_u64(&mut sidecar)? as usize;
        if record_count.checked_mul(msb_bytes) != Some(stored_msb) || record_count.checked_mul(factor_bytes) != Some(stored_factors) {
            return Err(ann_error("Ours sidecar size does not match metadata"));
        }
        let length = stored_msb.checked_add(stored_factors).and_then(|n|n.checked_add(24))
            .ok_or_else(||ann_error("routing length overflow"))?;
        if sidecar.get_ref().metadata()?.len() != length as u64 {return Err(ann_error("routing sidecar length mismatch"));}
        let (msb, factors, routing) = match plan {
            super::routing::RoutingPlan::Resident => {
                let mut msb=vec![0u8;stored_msb];let mut factors=vec![0u8;stored_factors];
                sidecar.read_exact(&mut msb)?;sidecar.read_exact(&mut factors)?;
                (msb,factors,None)
            },
            super::routing::RoutingPlan::Paged(plan) => {
                if route.is_some(){return Err(ann_error("paged routing is incompatible with adaptive route"));}
                let factors=if plan.resident_factors {
                    use std::io::{Seek, SeekFrom};
                    sidecar.seek(SeekFrom::Start((24+stored_msb) as u64))?;
                    let mut values=vec![0u8;stored_factors];sidecar.read_exact(&mut values)?;values
                }else{Vec::new()};
                (Vec::new(),factors,Some(super::routing::RoutingReader::new(sidecar_path,plan,length)?))
            }
        };
        let layout = OursPayloadLayout::new(record_count, compact_bytes, residual_bytes)?;
        Ok(Self {
            pca: None,
            adaptive: None,
            routing,
            locality: None,
            route_keep: 0,
            route_ratio: 0.0,
            route_revisit: false,
            route_trace_dir: None,
            space,
            layout,
            msb,
            factors,
            centroid_bytes: centroids.len() * 4,
        })
    }

    pub fn resident_bytes(&self) -> usize {
        self.msb.len() + self.factors.len() + self.centroid_bytes
            + self.routing.as_ref().map_or(0, |r| r.plan.reserved_bytes)
            + self.locality.as_ref().map_or(0, |layout| layout.resident_bytes())
            + self.adaptive.as_ref().map_or(0, |route| route.resident_bytes())
            + self.pca.as_ref().map_or(0, |route| route.resident_bytes())
    }

    /// Configure one immutable codec instance before its worker threads start.
    /// Every concurrent query then observes the same C++ query layout.
    pub fn configure_query_codec(&self, codec: QueryCoarseCodec) {
        self.space.set_query_coarse_codec(codec);
        if let Some(pca) = &self.pca {pca.configure(codec);}
    }

    pub fn prepare(&self, query: &[f32]) -> ANNResult<OursPreparedQuery<'_>> {
        Ok(OursPreparedQuery {
            pca: self.pca.as_ref().map(|p|p.prepare(query).map_err(ann_error)).transpose()?,
            routing_estimates: std::cell::RefCell::new(if self.routing.is_some(){Vec::with_capacity(64)}else{Vec::new()}),
            projected: self.adaptive.as_ref().map(|route| route.project(query)),
            owner: self,
            computer: self.space.prepare_query(query)?,
        })
    }
}

pub struct OursPreparedQuery<'a> {
    pca: Option<super::pca::Prepared>,
    routing_estimates: std::cell::RefCell<Vec<RabitqPaperEstimate>>,
    projected: Option<Vec<f32>>,
    owner: &'a OursResidentCodec,
    computer: QueryComputer,
}

impl OursPreparedQuery<'_> {
    fn paper_estimates(&self, ids: &[u32], epsilon: f32) -> ANNResult<Vec<RabitqPaperEstimate>> {
        if let (Some(pca),Some(query))=(&self.owner.pca,&self.pca) {
            return pca.estimates(query,ids,epsilon).map_err(ann_error);
        }
        if self.owner.adaptive.is_some() {
            return Ok(vec![RabitqPaperEstimate::default(); ids.len()]);
        }
        let mut output = vec![RabitqPaperEstimate::default(); ids.len()];
        if let Some(reader) = self.owner.routing.as_ref() {
            let msb=self.owner.space.paper_msb_code_bytes();
            let factors=self.owner.space.paper_factor_bytes();
            let mut estimates=self.routing_estimates.borrow_mut();
            for (window, target) in ids.chunks(64).zip(output.chunks_mut(64)) {
                reader.gather_with_factors(window,self.owner.layout.record_count,msb,factors,&self.owner.factors,|positions,codes,scales|{
                    estimates.resize(positions.len(),RabitqPaperEstimate::default());
                    self.computer.paper_estimate_batch_sidecar(positions,codes.as_ptr(),msb,
                        scales.as_ptr().cast(),factors,epsilon,&mut estimates)?;
                    for (&position,value) in positions.iter().zip(estimates.iter().copied()){target[position as usize]=value;}
                    Ok(())
                })?;
            }
            return Ok(output);
        }
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
                locality: None,
                record_cache: None,
                record_profile: None,
                inner: OursPayloadReaderInner::Memory {
                    bytes: bytes.clone(),
                    layout: layout.clone(),
                },
            }),
            Self::Direct { path, layout } => Ok(OursPayloadReader {
                locality: None,
                record_cache: None,
                record_profile: None,
                inner: OursPayloadReaderInner::Direct {
                    reader: DirectAioHandle::new(path)?,
                    layout: layout.clone(),
                    query_cache: None,
                },
            }),
        }
    }
}

pub struct OursPayloadReader {
    pub locality: Option<super::locality::LocalityReader>,
    pub record_cache: Option<Arc<super::record_cache::RecordCache>>,
    pub record_profile: Option<Arc<super::record_cache::RecordProfile>>,
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
        query_cache: Option<Arc<Mutex<QueryPageCache>>>,
    },
}

impl OursPayloadReader {
    pub fn preload_record(&mut self, id:u32) -> ANNResult<(Vec<u8>,Vec<u8>)> {
        let (batch, _) = self.read_records_uncached(&[id], true, true)?;
        Ok((batch.compact, batch.residual))
    }
    pub fn begin_query(&mut self) {
        if let OursPayloadReaderInner::Direct { query_cache, .. } = &mut self.inner {
            *query_cache = None;
        }
    }

    fn read_records(
        &mut self,
        ids: &[u32],
        coalesce: bool,
        reuse: bool,
    ) -> ANNResult<(OursPayloadBatch, IoStats)> {
        if self.record_cache.is_none() && self.record_profile.is_none() {
            return self.read_records_uncached(ids, coalesce, reuse);
        }
        let layout = match &self.inner {
            OursPayloadReaderInner::Memory{layout,..}|OursPayloadReaderInner::Direct{layout,..}=>layout,
        };
        if ids.iter().any(|&id|id as usize>=layout.record_count) {
            return Err(ann_error("record cache ID out of range"));
        }
        if let Some(profile)=&self.record_profile {profile.record(ids);}
        let Some(cache)=self.record_cache.clone() else {return self.read_records_uncached(ids,coalesce,reuse)};
        if self.locality.is_some() || !coalesce || !reuse {
            return Err(ann_error("formal record cache requires standard packed coalescing+reuse"));
        }
        let (cb,rb)=(layout.compact_bytes,layout.residual_bytes);
        let mut batch=OursPayloadBatch{compact:vec![0;ids.len()*cb],residual:vec![0;ids.len()*rb]};
        let mut missing=Vec::new();let mut positions=Vec::new();
        for (pos,&id) in ids.iter().enumerate() {
            // Copy hits under the shard lock. Never re-check them after I/O.
            let c=cache.get(id,0,&mut batch.compact[pos*cb..(pos+1)*cb]);
            let r=cache.get(id,1,&mut batch.residual[pos*rb..(pos+1)*rb]);
            if !c || !r {missing.push(id);positions.push(pos);}
        }
        let (loaded,stats)=if missing.is_empty() {(OursPayloadBatch::default(),IoStats::default())}
            else {self.read_records_uncached(&missing,coalesce,reuse)?};
        for (i,(&id,&pos)) in missing.iter().zip(&positions).enumerate() {
            let c=&loaded.compact[i*cb..(i+1)*cb];let r=&loaded.residual[i*rb..(i+1)*rb];
            batch.compact[pos*cb..(pos+1)*cb].copy_from_slice(c);
            batch.residual[pos*rb..(pos+1)*rb].copy_from_slice(r);
            // Packed payload already contains both parts; no extra read to fill residual.
            cache.put(id,0,c);cache.put(id,1,r);
        }
        Ok((batch,stats))
    }

    fn read_records_uncached(
        &mut self, ids:&[u32], coalesce:bool, reuse:bool,
    )->ANNResult<(OursPayloadBatch,IoStats)> {
        if let Some(reader) = &mut self.locality {
            if !coalesce || !reuse { return Err(ann_error("locality requires coalescing+reuse")); }
            let (compact, stats) = reader.compact(ids)?;
            return Ok((OursPayloadBatch { compact, residual: Vec::new() }, stats));
        }
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
                if query_cache.is_none() {
                    *query_cache = Some(Arc::new(Mutex::new(QueryPageCache::new()?)));
                }
                let mut query_cache = query_cache.as_ref().unwrap().lock()
                    .map_err(|_| ann_error("query cache lock poisoned"))?;
                let evictions_before = query_cache.evictions();
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
                                if let Some(page) = query_cache.get(1, page_id) {
                                    stats.query_cache_hits += 1;
                                    local.insert(page_id, page);
                                    continue;
                                }
                            }
                            stats.query_cache_misses += 1;
                            let (bytes, delta) = reader.read_pages(&[page_id])?;
                            merge_bridge_stats(&mut stats, delta);
                            let mut page = Box::new([0_u8; PAGE_SIZE]);
                            page.copy_from_slice(&bytes[..PAGE_SIZE]);
                            if reuse {
                                query_cache.put(1, page_id, &page);
                            }
                            local.insert(page_id, page);
                        }
                        append_record(layout, &local, id, &mut batch)?;
                    }
                    stats.io_wait_us = started.elapsed().as_secs_f64() * 1_000_000.0;
                    stats.query_cache_allocated_bytes = query_cache.allocated_bytes();
                    stats.query_cache_evictions = query_cache.evictions() - evictions_before;
                    return Ok((batch, stats));
                }

                let mut unique_pages = requested
                    .into_iter()
                    .flat_map(|range| range)
                    .collect::<Vec<_>>();
                unique_pages.sort_unstable();
                unique_pages.dedup();
                stats.duplicate_pages_removed = (requested_count - unique_pages.len()) as u64;
                let mut temporary = HashMap::new();
                let missing = unique_pages
                    .iter()
                    .copied()
                    .filter(|page| {
                        if reuse {
                            if let Some(bytes) = query_cache.get(1, *page) {
                                temporary.insert(*page, bytes);
                                stats.query_cache_hits += 1;
                                return false;
                            }
                        }
                        stats.query_cache_misses += 1;
                        true
                    })
                    .collect::<Vec<_>>();
                if !missing.is_empty() {
                    let (bytes, delta) = reader.read_pages(&missing)?;
                    merge_bridge_stats(&mut stats, delta);
                    for (slot, page_id) in missing.iter().copied().enumerate() {
                        let mut page = Box::new([0_u8; PAGE_SIZE]);
                        page.copy_from_slice(&bytes[slot * PAGE_SIZE..(slot + 1) * PAGE_SIZE]);
                        if reuse {
                            query_cache.put(1, page_id, &page);
                        }
                        temporary.insert(page_id, page);
                    }
                }
                let batch = gather_records(layout, &temporary, ids)?;
                stats.io_wait_us = started.elapsed().as_secs_f64() * 1_000_000.0;
                stats.query_cache_allocated_bytes = query_cache.allocated_bytes();
                stats.query_cache_evictions = query_cache.evictions() - evictions_before;
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
    total.query_cache_allocated_bytes = total.query_cache_allocated_bytes.max(delta.query_cache_allocated_bytes);
    total.query_cache_evictions += delta.query_cache_evictions;
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
    pub graph_io: IoStats,
    pub compact_io: IoStats,
    pub residual_io: IoStats,
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
    query_id: usize,
    trace_enabled: bool,
    k: usize,
    width: usize,
    beam: usize,
    epsilon: f32,
    rerank_candidates: usize,
    ablation: OursAblation,
) -> ANNResult<OursDiskSearchResult> {
    if codec.routing.is_some() {super::routing::take_delta();}
    // Diagnostic-only files: exclusive creation prevents silently mixing runs.
    let mut trace = codec.route_trace_dir.as_ref().filter(|_| trace_enabled).map(|dir| {
        File::options().write(true).create_new(true)
            .open(dir.join(format!("q{query_id}_w{width}.tsv")))
            .map(BufWriter::new).map_err(|e| ann_error(e.to_string()))
    }).transpose()?;
    if k == 0 {
        return Ok(OursDiskSearchResult {
            ids: Vec::new(),
            distances: Vec::new(),
            stats: OursDiskStats::default(),
        });
    }
    if width < k {
        return Err(ann_error("Ours search width must be >= top-k; implicit clamping is not allowed"));
    }
    if beam == 0 {
        return Err(ann_error("Ours disk search beam must be positive"));
    }
    payload_reader.begin_query();
    let mut graph = graph_factory.create()?;
    if let OursPayloadReaderInner::Direct { query_cache, .. } = &mut payload_reader.inner {
        *query_cache = Some(match &graph {
            GraphBackend::Direct(reader) => reader.lock()
                .map_err(|_| ann_error("graph reader lock poisoned"))?.query_cache.clone(),
            GraphBackend::Memory(_) => Arc::new(Mutex::new(QueryPageCache::new()?)),
        });
        if let Some(locality) = &mut payload_reader.locality {
            locality.cache = query_cache.as_ref().unwrap().clone();
        }
    }
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
    add_io(&mut stats.compact_io, delta);
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

        // Prefetch only the already selected frontier. Process its adjacency lists
        // in the original order so gate thresholds and candidate insertion stay unchanged.
        let frontier_lists = if payload_reader.locality.is_none() {
            let (lists, delta) = graph.read_nodes(&frontier)?;
            add_io(&mut stats.graph_io, delta);
            add_io(&mut stats.io, delta);
            Some(lists)
        } else {
            None
        };
        for (frontier_index, node) in frontier.iter().copied().enumerate() {
            let (lists, graph_delta) = if let Some(locality) = &mut payload_reader.locality {
                let (neighbors, stats) = locality.neighbors(node)?;
                (vec![neighbors], stats)
            } else { (vec![frontier_lists.as_ref().unwrap()[frontier_index].clone()], IoStats::default()) };
            add_io(&mut stats.graph_io, graph_delta);
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
                if let Some(writer) = &mut trace { writeln!(writer, "{}\t\t", node).map_err(|e| ann_error(e.to_string()))?; }
                continue;
            }
            stats.io.visited_nodes += fresh.len() as u64;
            let trace_fresh = trace.as_ref().map(|_| fresh.clone());

            if let (Some(route), Some(projected)) = (&codec.adaptive, &prepared.projected) {
                let started = Instant::now();
                // Diagnostic policy: rejected fresh neighbors may be reconsidered
                // from another expansion. Already-read nodes remain visited.
                if codec.route_revisit {
                    for id in &fresh { visited[*id as usize] = false; }
                }
                // Compare only in projected space. This is an empirical gate,
                // not a bound on original-space distance. Use the worst projected
                // score of ALL current pool members, not the original-space tail.
                if codec.route_ratio > 0.0 && pool.len() >= width {
                    let norm: f32 = projected.iter().map(|x| x*x).sum();
                    let worst = pool.iter().map(|c| route.score(c.id as usize, projected))
                        .fold(f32::NEG_INFINITY, f32::max);
                    let threshold = codec.route_ratio * (norm + worst).max(0.0);
                    fresh.retain(|id| norm + route.score(*id as usize, projected) <= threshold);
                }
                fresh.sort_by_cached_key(|id| {
                    let bits = route.score(*id as usize, projected).to_bits();
                    if bits >> 31 != 0 { !bits } else { bits ^ 0x80000000 }
                });
                // Approximate per-expansion shortlist, NOT a distance lower bound.
                // Dropped IDs remain visited, matching the gate's one-shot policy.
                // Zero preserves the full-read replacement control.
                if codec.route_keep > 0 {
                    fresh.truncate(codec.route_keep);
                }
                if codec.route_revisit {
                    for id in &fresh { visited[*id as usize] = true; }
                }
                stats.queue_compute_us += started.elapsed().as_secs_f64() * 1_000_000.0;
            }

            if let (Some(writer), Some(before)) = (&mut trace, trace_fresh) {
                writeln!(writer, "{}\t{}\t{}", node,
                    before.iter().map(u32::to_string).collect::<Vec<_>>().join(","),
                    fresh.iter().map(u32::to_string).collect::<Vec<_>>().join(","))
                    .map_err(|e| ann_error(e.to_string()))?;
                writer.flush().map_err(|e| ann_error(e.to_string()))?;
            }
            if codec.pca.is_some() {
                let started=Instant::now();
                let ranking=prepared.paper_estimates(&fresh,0.)?;
                stats.db1_checks+=fresh.len() as u64;
                let mut ranked=fresh.iter().copied().zip(ranking).collect::<Vec<_>>();
                ranked.sort_by(|a,b|a.1.lower_bound.total_cmp(&b.1.lower_bound).then(a.0.cmp(&b.0)));
                // Match the accepted M=32 experiment: unselected IDs can be revisited.
                for &(id,_) in ranked.iter().skip(32) {visited[id as usize]=false;}
                fresh=ranked.into_iter().take(32).map(|(id,_)|id).collect();
                stats.queue_compute_us+=started.elapsed().as_secs_f64()*1_000_000.;
            }
            let estimates = if ablation.uses_gate() && codec.adaptive.is_none() {
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
            if ablation.uses_gate() && codec.adaptive.is_none() {
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
                add_io(&mut stats.compact_io, payload_delta);
                add_io(&mut stats.io, payload_delta);
                let modes = estimates
                    .iter()
                    .map(|estimate| {
                        if codec.pca.is_none() && ablation.uses_gate() && estimate.valid != 0 {
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
                stats.io.distance_evaluations += ids.len() as u64;
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
    let (mut rerank_payload, rerank_delta) = payload_reader.read_records(
        &rerank_ids,
        ablation.payload_coalescing(),
        ablation.payload_reuse(),
    )?;
    stats.rerank_page_reads = rerank_delta.sectors_4k;
    add_io(&mut stats.residual_io, rerank_delta);
    add_io(&mut stats.io, rerank_delta);
    if let Some(locality) = &mut payload_reader.locality {
        let (residual, delta) = locality.residual(&rerank_ids)?;
        rerank_payload.residual = residual;
        stats.rerank_page_reads += delta.sectors_4k;
        add_io(&mut stats.residual_io, delta);
        add_io(&mut stats.io, delta);
    }
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
    if codec.routing.is_some() {
        let (reads,bytes,wait)=super::routing::take_delta();
        stats.io.requests+=reads;stats.io.sectors_4k+=reads;stats.io.bytes_read+=bytes;
        stats.io.io_wait_us+=wait;stats.queue_compute_us=(stats.queue_compute_us-wait).max(0.);
    }
    Ok(OursDiskSearchResult {
        ids,
        distances,
        stats,
    })
}

#[cfg(test)]
mod cache_tests {
    use super::*;

    #[test]
    fn paged_scores_bitwise_match_resident() {
        let root=std::env::temp_dir().join(format!("routing-score-{}",std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let dim=128;let count=137;
        let center=vec![0f32;dim];
        let space=RabitqSpace::new_with_centroids(dim,100,1,&center).unwrap();
        let mut builder=OursIndexBuilder::new(space,count).unwrap();
        for id in 0..count {let v=(0..dim).map(|j|((id*31+j*17)%103) as f32/51.-1.).collect::<Vec<_>>();builder.encode(id,&v).unwrap();}
        let meta=root.join("meta");let side=root.join("side");
        builder.save(100,&center,&meta,&side,&root.join("payload")).unwrap();
        let resident=OursResidentCodec::load(&meta,&side).unwrap();
        for (capacity,clock) in [(1,true),(8,true),(1,false),(8,false)] {
          for resident_factors in [false,true] {
            let paged=OursResidentCodec::load_planned(&meta,&side,None,super::super::routing::RoutingPlan::Paged(super::super::routing::PagingPlan{clock,resident_factors,capacity,inflight:capacity,reserved_bytes:0})).unwrap();
            assert!(paged.msb.is_empty());
            assert_eq!(paged.factors.is_empty(),!resident_factors);
            for codec in [QueryCoarseCodec::Int8,QueryCoarseCodec::Int4,QueryCoarseCodec::B1,QueryCoarseCodec::Full] {
                resident.configure_query_codec(codec);paged.configure_query_codec(codec);
                let query=(0..dim).map(|j|(j%19) as f32/19.).collect::<Vec<_>>();
                let a=resident.prepare(&query).unwrap();let b=paged.prepare(&query).unwrap();
                for n in [1,31,32,63,64,65,137] {
                    let ids=(0..n).map(|j|((j*17+11)%count) as u32).collect::<Vec<_>>();
                    let expected=a.paper_estimates(&ids,0.1).unwrap();let actual=b.paper_estimates(&ids,0.1).unwrap();
                    for (x,y) in expected.iter().zip(actual.iter()) {
                        assert_eq!(x.valid,y.valid);
                        for (u,v) in [(x.lower_bound,y.lower_bound),(x.short_ip,y.short_ip),(x.alpha,y.alpha),(x.ip_hat,y.ip_hat),(x.error_bound,y.error_bound)]{assert_eq!(u.to_bits(),v.to_bits(),"codec={codec:?}, count={n}");}
                    }
                }
            }
        }
          }
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn payload_batches_larger_than_cache_match_memory() {
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let path = std::env::temp_dir().join(format!("qgraph05_payload_{}_{}.pages", std::process::id(), stamp));
        let count = 1300;
        let layout = OursPayloadLayout::new(count, 2048, 2048).unwrap();
        let mut file = File::options().write(true).create_new(true).open(&path).unwrap();
        for id in 0..count {
            file.write_all(&[(id % 251) as u8; PAGE_SIZE]).unwrap();
        }
        drop(file);
        let ids = (0..count as u32).collect::<Vec<_>>();
        let mut memory = OursPayloadFactory::memory(&path, layout.clone()).unwrap().create().unwrap();
        let (expected, _) = memory.read_records(&ids, true, true).unwrap();
        for coalesce in [false, true] {
            for reuse in [false, true] {
                let mut direct = OursPayloadFactory::direct(&path, layout.clone()).unwrap().create().unwrap();
                let (actual, stats) = direct.read_records(&ids, coalesce, reuse).unwrap();
                assert_eq!(actual.compact, expected.compact);
                assert_eq!(actual.residual, expected.residual);
                assert!(stats.query_cache_allocated_bytes <= 4 * 1024 * 1024);
                if reuse { assert!(stats.query_cache_evictions > 0); }
                direct.begin_query();
                let (_, reset_stats) = direct.read_records(&[1299], true, true).unwrap();
                assert_eq!(reset_stats.query_cache_hits, 0);
                assert_eq!(reset_stats.query_cache_misses, 1);
            }
        }
        fs::remove_file(path).unwrap();
    }
}
