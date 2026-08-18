use std::cell::UnsafeCell;
use std::future::Future;
use std::sync::{Arc, Mutex};

use diskann::error::IntoANNResult;
use diskann::graph::glue::{
    self, DefaultPostProcessor, InsertStrategy, PruneStrategy, SearchPostProcess, SearchStrategy,
};
use diskann::graph::{AdjacencyList, SearchOutputBuffer, workingset};
use diskann::neighbor::{self, Neighbor};
use diskann::provider::{ExecutionContext, HasId};
use diskann::utils::{IntoUsize, VectorRepr};
use diskann::{ANNError, ANNResult, default_post_processor};
use diskann_providers::model::graph::provider::async_::SimpleNeighborProviderAsync;
use diskann_providers::model::graph::provider::async_::common::{
    CreateVectorStore, SetElementHelper, VectorStore,
};
use diskann_providers::model::graph::provider::async_::inmem::{
    FullPrecisionProvider,
};
use diskann_utils::views::Matrix;
use diskann_vector::DistanceFunction;
use diskann_vector::distance::Metric;

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct RabitqDistanceInterval {
    estimate: f32,
    lower_bound: f32,
    upper_bound: f32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct RabitqPaperEstimate {
    lower_bound: f32,
    short_ip: f32,
    alpha: f32,
    ip_hat: f32,
    error_bound: f32,
    valid: u8,
}

unsafe extern "C" {
    fn rabitq_space_new(
        dim: usize,
        seed: u32,
        centroid_count: usize,
        residual_bits: usize,
        residual_block_size: usize,
        residual_mse: bool,
        residual_fp16: bool,
    ) -> *mut std::ffi::c_void;
    fn rabitq_space_delete(space: *mut std::ffi::c_void);
    fn rabitq_space_set_center(space: *mut std::ffi::c_void, center: *const f32) -> bool;
    fn rabitq_space_set_centroids(
        space: *mut std::ffi::c_void,
        centroids: *const f32,
        centroid_count: usize,
    ) -> bool;
    fn rabitq_train_centroids(
        data: *const f32,
        count: usize,
        dim: usize,
        k: usize,
        sample_count: usize,
        seed: u32,
        iters: i32,
        out: *mut f32,
    ) -> bool;
    fn rabitq_full_record_bytes(space: *const std::ffi::c_void) -> usize;
    fn rabitq_compact_record_bytes(space: *const std::ffi::c_void) -> usize;
    fn rabitq_residual_record_bytes(space: *const std::ffi::c_void) -> usize;
    fn rabitq_residual_block_size(space: *const std::ffi::c_void) -> usize;
    fn rabitq_residual_bits(space: *const std::ffi::c_void) -> usize;
    fn rabitq_encode_full(
        space: *const std::ffi::c_void,
        raw: *const f32,
        encoded_out: *mut std::ffi::c_void,
    ) -> bool;
    fn rabitq_copy_compact(
        space: *const std::ffi::c_void,
        full_encoded: *const std::ffi::c_void,
        compact_out: *mut std::ffi::c_void,
    ) -> bool;
    fn rabitq_copy_residual_record(
        space: *const std::ffi::c_void,
        full_encoded: *const std::ffi::c_void,
        residual_out: *mut std::ffi::c_void,
    ) -> bool;
    fn rabitq_symmetric_distance(
        space: *const std::ffi::c_void,
        lhs: *const std::ffi::c_void,
        rhs: *const std::ffi::c_void,
    ) -> f32;
    fn rabitq_build_vamana_graph(
        space: *mut std::ffi::c_void,
        full_records: *const std::ffi::c_void,
        record_count: usize,
        full_stride: usize,
        r: usize,
        l_build: usize,
        alpha: f32,
        beam_width: usize,
        batch_size: usize,
        refine_passes: usize,
        prune_candidate_cap: usize,
        build_early_stop_hops: usize,
        degrees_out: *mut u32,
        edges_out: *mut u32,
        edge_stride: usize,
        paper_msb_out: *mut u8,
        paper_factors_out: *mut std::ffi::c_void,
        paper_msb_stride_out: *mut usize,
        paper_factor_bytes_out: *mut usize,
    ) -> bool;
    fn rabitq_paper_msb_code_bytes(space: *const std::ffi::c_void) -> usize;
    fn rabitq_paper_factor_bytes() -> usize;
    fn rabitq_prepare_query(
        space: *const std::ffi::c_void,
        query: *const f32,
    ) -> *const std::ffi::c_void;
    fn rabitq_release_query(space: *const std::ffi::c_void, prepared: *const std::ffi::c_void);
    fn rabitq_query_distance(
        space: *const std::ffi::c_void,
        prepared: *const std::ffi::c_void,
        encoded: *const std::ffi::c_void,
    ) -> f32;
    fn rabitq_paper_estimate(
        space: *const std::ffi::c_void,
        prepared: *const std::ffi::c_void,
        encoded: *const std::ffi::c_void,
        epsilon0: f32,
    ) -> RabitqPaperEstimate;
    fn rabitq_paper_estimate_sidecar(
        space: *const std::ffi::c_void,
        prepared: *const std::ffi::c_void,
        msb_code: *const u8,
        factors: *const std::ffi::c_void,
        epsilon0: f32,
    ) -> RabitqPaperEstimate;
    fn rabitq_query_distance_with_paper(
        space: *const std::ffi::c_void,
        prepared: *const std::ffi::c_void,
        encoded: *const std::ffi::c_void,
        short_ip: f32,
    ) -> f32;
    fn rabitq_residual_distance_from_record(
        space: *const std::ffi::c_void,
        prepared: *const std::ffi::c_void,
        encoded: *const std::ffi::c_void,
        residual_record: *const std::ffi::c_void,
        long_distance: f32,
    ) -> RabitqDistanceInterval;
    fn rabitq_paper_estimate_batch(
        space: *const std::ffi::c_void,
        prepared: *const std::ffi::c_void,
        ids: *const u32,
        count: usize,
        encoded_base: *const std::ffi::c_void,
        encoded_stride: usize,
        epsilon0: f32,
        out: *mut RabitqPaperEstimate,
    ) -> bool;
    fn rabitq_paper_estimate_batch_sidecar(
        space: *const std::ffi::c_void,
        prepared: *const std::ffi::c_void,
        ids: *const u32,
        count: usize,
        msb_base: *const u8,
        msb_stride: usize,
        factors_base: *const std::ffi::c_void,
        factors_stride: usize,
        epsilon0: f32,
        out: *mut RabitqPaperEstimate,
    ) -> bool;
    fn rabitq_query_distance_batch(
        space: *const std::ffi::c_void,
        prepared: *const std::ffi::c_void,
        ids: *const u32,
        modes: *const u8,
        count: usize,
        encoded_base: *const std::ffi::c_void,
        encoded_stride: usize,
        short_ips: *const f32,
        out_distances: *mut f32,
    ) -> bool;
    fn rabitq_residual_distance_batch(
        space: *const std::ffi::c_void,
        prepared: *const std::ffi::c_void,
        ids: *const u32,
        count: usize,
        encoded_base: *const std::ffi::c_void,
        encoded_stride: usize,
        residual_base: *const std::ffi::c_void,
        residual_stride: usize,
        long_distances: *const f32,
        out: *mut RabitqDistanceInterval,
    ) -> bool;
}

pub struct RabitqSpace {
    ptr: *mut std::ffi::c_void,
    dim: usize,
    centroid_count: usize,
}

unsafe impl Send for RabitqSpace {}
unsafe impl Sync for RabitqSpace {}

impl Drop for RabitqSpace {
    fn drop(&mut self) {
        unsafe { rabitq_space_delete(self.ptr) }
    }
}

impl RabitqSpace {
    pub fn new_dbpedia_default(dim: usize, seed: u32, center: &[f32]) -> Result<Arc<Self>, String> {
        Self::new_with_centroids(dim, seed, 1, center)
    }

    pub fn new_with_centroids(
        dim: usize,
        seed: u32,
        centroid_count: usize,
        centroids: &[f32],
    ) -> Result<Arc<Self>, String> {
        if centroids.len() != dim * centroid_count {
            return Err(format!(
                "ExRaBitQ centroids dimension mismatch: centroids={} dim*K={}",
                centroids.len(),
                dim * centroid_count
            ));
        }
        let ptr = unsafe { rabitq_space_new(dim, seed, centroid_count, 4, 16, true, true) };
        if ptr.is_null() {
            return Err("failed to create ExRaBitQ space".to_string());
        }
        let ok = unsafe {
            if centroid_count == 1 {
                rabitq_space_set_center(ptr, centroids.as_ptr())
            } else {
                rabitq_space_set_centroids(ptr, centroids.as_ptr(), centroid_count)
            }
        };
        if !ok {
            unsafe { rabitq_space_delete(ptr) };
            return Err("failed to set ExRaBitQ centroids".to_string());
        }
        Ok(Arc::new(Self { ptr, dim, centroid_count }))
    }

    pub fn train_kmeans(
        sample: &[f32],
        sample_count: usize,
        dim: usize,
        k: usize,
        seed: u32,
        iters: i32,
    ) -> Result<Vec<f32>, String> {
        let mut out = vec![0.0_f32; k * dim];
        let ok = unsafe {
            rabitq_train_centroids(
                sample.as_ptr(),
                sample_count,
                dim,
                k,
                sample_count,
                seed,
                iters,
                out.as_mut_ptr(),
            )
        };
        if ok {
            Ok(out)
        } else {
            Err("kmeans centroid training failed".to_string())
        }
    }

    pub fn full_record_bytes(&self) -> usize {
        unsafe { rabitq_full_record_bytes(self.ptr) }
    }

    pub fn compact_record_bytes(&self) -> usize {
        unsafe { rabitq_compact_record_bytes(self.ptr) }
    }

    pub fn residual_record_bytes(&self) -> usize {
        unsafe { rabitq_residual_record_bytes(self.ptr) }
    }

    pub fn residual_block_size(&self) -> usize {
        unsafe { rabitq_residual_block_size(self.ptr) }
    }

    pub fn residual_bits(&self) -> usize {
        unsafe { rabitq_residual_bits(self.ptr) }
    }

    pub fn paper_msb_code_bytes(&self) -> usize {
        unsafe { rabitq_paper_msb_code_bytes(self.ptr) }
    }

    pub fn paper_factor_bytes(&self) -> usize {
        unsafe { rabitq_paper_factor_bytes() }
    }

    fn encode_full(&self, raw: &[f32], encoded_out: &mut [u8]) -> ANNResult<()> {
        if raw.len() != self.dim {
            return Err(ANNError::message(format!(
                "ExRaBitQ encode dimension mismatch: raw={} dim={}",
                raw.len(),
                self.dim
            )));
        }
        if encoded_out.len() != self.full_record_bytes() {
            return Err(ANNError::message("ExRaBitQ output record size mismatch"));
        }
        let ok = unsafe {
            rabitq_encode_full(
                self.ptr,
                raw.as_ptr(),
                encoded_out.as_mut_ptr().cast::<std::ffi::c_void>(),
            )
        };
        if ok {
            Ok(())
        } else {
            Err(ANNError::message("ExRaBitQ encode failed"))
        }
    }

    fn copy_compact(&self, full: &[u8], compact_out: &mut [u8]) -> ANNResult<()> {
        if full.len() != self.full_record_bytes() || compact_out.len() != self.compact_record_bytes() {
            return Err(ANNError::message("ExRaBitQ compact copy size mismatch"));
        }
        let ok = unsafe {
            rabitq_copy_compact(
                self.ptr,
                full.as_ptr().cast::<std::ffi::c_void>(),
                compact_out.as_mut_ptr().cast::<std::ffi::c_void>(),
            )
        };
        if ok {
            Ok(())
        } else {
            Err(ANNError::message("ExRaBitQ compact copy failed"))
        }
    }

    fn copy_residual_record(&self, full: &[u8], residual_out: &mut [u8]) -> ANNResult<()> {
        if full.len() != self.full_record_bytes()
            || residual_out.len() != self.residual_record_bytes()
        {
            return Err(ANNError::message("ExRaBitQ residual copy size mismatch"));
        }
        let ok = unsafe {
            rabitq_copy_residual_record(
                self.ptr,
                full.as_ptr().cast::<std::ffi::c_void>(),
                residual_out.as_mut_ptr().cast::<std::ffi::c_void>(),
            )
        };
        if ok {
            Ok(())
        } else {
            Err(ANNError::message("ExRaBitQ residual copy failed"))
        }
    }
}

#[derive(Clone)]
pub struct OursPrecursor {
    space: Arc<RabitqSpace>,
}

impl OursPrecursor {
    pub fn new(space: Arc<RabitqSpace>) -> Self {
        Self { space }
    }
}

impl CreateVectorStore for OursPrecursor {
    type Target = OursStore;

    fn create(
        self,
        max_points: usize,
        _metric: Metric,
        prefetch_lookahead: Option<usize>,
    ) -> Self::Target {
        OursStore::new(self.space, max_points, prefetch_lookahead.unwrap_or(8))
    }
}

pub struct OursStore {
    space: Arc<RabitqSpace>,
    data: UnsafeCell<Vec<u8>>,
    record_bytes: usize,
    residual: UnsafeCell<Vec<u8>>,
    residual_record_bytes: usize,
    full_record_bytes: usize,
    msb: UnsafeCell<Vec<u8>>,
    factors: UnsafeCell<Vec<u8>>,
    msb_bytes: usize,
    factor_bytes: usize,
    total: usize,
    write_locks: Vec<Mutex<()>>,
    prefetch_lookahead: usize,
}

unsafe impl Send for OursStore {}
unsafe impl Sync for OursStore {}

impl OursStore {
    fn new(space: Arc<RabitqSpace>, total: usize, prefetch_lookahead: usize) -> Self {
        let record_bytes = space.compact_record_bytes();
        let residual_record_bytes = space.residual_record_bytes();
        let full_record_bytes = space.full_record_bytes();
        let msb_bytes = space.paper_msb_code_bytes();
        let factor_bytes = space.paper_factor_bytes();
        let write_locks = (0..total.div_ceil(16)).map(|_| Mutex::new(())).collect();
        Self {
            space,
            data: UnsafeCell::new(vec![0; total * record_bytes]),
            record_bytes,
            residual: UnsafeCell::new(vec![0; total * residual_record_bytes]),
            residual_record_bytes,
            full_record_bytes,
            msb: UnsafeCell::new(vec![0; total * msb_bytes]),
            factors: UnsafeCell::new(vec![0; total * factor_bytes]),
            msb_bytes,
            factor_bytes,
            total,
            write_locks,
            prefetch_lookahead,
        }
    }

    fn get_vector(&self, id: usize) -> &[u8] {
        assert!(id < self.total);
        let begin = id * self.record_bytes;
        let end = begin + self.record_bytes;
        unsafe { &(&*self.data.get())[begin..end] }
    }

    fn get_vector_mut(&self, id: usize) -> &mut [u8] {
        assert!(id < self.total);
        let begin = id * self.record_bytes;
        let end = begin + self.record_bytes;
        unsafe { &mut (&mut *self.data.get())[begin..end] }
    }

    fn records_ptr(&self) -> *const u8 {
        unsafe { (&*self.data.get()).as_ptr() }
    }

    fn msb_ptr(&self) -> *const u8 {
        unsafe { (&*self.msb.get()).as_ptr() }
    }

    fn factors_ptr(&self) -> *const u8 {
        unsafe { (&*self.factors.get()).as_ptr() }
    }

    fn residual_ptr(&self) -> *const u8 {
        unsafe { (&*self.residual.get()).as_ptr() }
    }

    fn get_residual_record(&self, id: usize) -> &[u8] {
        assert!(id < self.total);
        let begin = id * self.residual_record_bytes;
        let end = begin + self.residual_record_bytes;
        unsafe { &(&*self.residual.get())[begin..end] }
    }

    fn get_residual_record_mut(&self, id: usize) -> &mut [u8] {
        assert!(id < self.total);
        let begin = id * self.residual_record_bytes;
        let end = begin + self.residual_record_bytes;
        unsafe { &mut (&mut *self.residual.get())[begin..end] }
    }

    fn get_msb_code(&self, id: usize) -> &[u8] {
        assert!(id < self.total);
        let begin = id * self.msb_bytes;
        let end = begin + self.msb_bytes;
        unsafe { &(&*self.msb.get())[begin..end] }
    }

    fn get_factors(&self, id: usize) -> &[u8] {
        assert!(id < self.total);
        let begin = id * self.factor_bytes;
        let end = begin + self.factor_bytes;
        unsafe { &(&*self.factors.get())[begin..end] }
    }

    fn install_paper_sidecar(&self, msb: Vec<u8>, factors: Vec<u8>, record_count: usize) {
        assert!(record_count <= self.total);
        assert!(msb.len() == record_count * self.msb_bytes);
        assert!(factors.len() == record_count * self.factor_bytes);
        unsafe {
            (&mut *self.msb.get())[..msb.len()].copy_from_slice(&msb);
            (&mut *self.factors.get())[..factors.len()].copy_from_slice(&factors);
        }
    }

    fn set_vector<T>(&self, id: usize, raw: &[T]) -> ANNResult<()>
    where
        T: VectorRepr,
    {
        let mut full = vec![0_u8; self.full_record_bytes];
        self.set_vector_with_scratch(id, raw, &mut full)
    }

    fn set_vector_with_scratch<T>(&self, id: usize, raw: &[T], full: &mut Vec<u8>) -> ANNResult<()>
    where
        T: VectorRepr,
    {
        let raw = T::as_f32(raw).into_ann_result()?;
        let _guard = self.write_locks[id / 16].lock().unwrap();
        full.resize(self.full_record_bytes, 0);
        self.space.encode_full(raw.as_ref(), full)?;
        self.space.copy_compact(full, self.get_vector_mut(id))?;
        self.space
            .copy_residual_record(full, self.get_residual_record_mut(id))
    }

    fn prefetch_hint(&self, id: usize) {
        let data = self.get_vector(id);
        unsafe {
            std::arch::x86_64::_mm_prefetch(
                data.as_ptr().cast::<i8>(),
                std::arch::x86_64::_MM_HINT_T0,
            );
        }
    }
}

impl VectorStore for OursStore {
    fn total(&self) -> usize {
        self.total
    }

    fn count_for_get_vector(&self) -> usize {
        0
    }
}

impl<T> SetElementHelper<T> for OursStore
where
    T: VectorRepr,
{
    fn set_element(&self, id: &u32, element: &[T]) -> ANNResult<()> {
        self.set_vector(id.into_usize(), element)
    }
}

pub struct SymmetricComputer {
    space: Arc<RabitqSpace>,
}

impl DistanceFunction<&[u8], &[u8], f32> for SymmetricComputer {
    fn evaluate_similarity(&self, left: &[u8], right: &[u8]) -> f32 {
        unsafe {
            rabitq_symmetric_distance(
                self.space.ptr,
                left.as_ptr().cast::<std::ffi::c_void>(),
                right.as_ptr().cast::<std::ffi::c_void>(),
            )
        }
    }
}

pub struct QueryComputer {
    space: Arc<RabitqSpace>,
    prepared: *const std::ffi::c_void,
}

unsafe impl Send for QueryComputer {}
unsafe impl Sync for QueryComputer {}

impl QueryComputer {
    fn new(space: Arc<RabitqSpace>, query: &[f32]) -> ANNResult<Self> {
        let prepared = unsafe { rabitq_prepare_query(space.ptr, query.as_ptr()) };
        if prepared.is_null() {
            return Err(ANNError::message("ExRaBitQ query preparation failed"));
        }
        Ok(Self { space, prepared })
    }

    fn distance(&self, encoded: &[u8]) -> f32 {
        unsafe {
            rabitq_query_distance(
                self.space.ptr,
                self.prepared,
                encoded.as_ptr().cast::<std::ffi::c_void>(),
            )
        }
    }

    fn paper_estimate(&self, encoded: &[u8], epsilon0: f32) -> RabitqPaperEstimate {
        unsafe {
            rabitq_paper_estimate(
                self.space.ptr,
                self.prepared,
                encoded.as_ptr().cast::<std::ffi::c_void>(),
                epsilon0,
            )
        }
    }

    fn paper_estimate_sidecar(
        &self,
        msb_code: &[u8],
        factors: &[u8],
        epsilon0: f32,
    ) -> RabitqPaperEstimate {
        unsafe {
            rabitq_paper_estimate_sidecar(
                self.space.ptr,
                self.prepared,
                msb_code.as_ptr(),
                factors.as_ptr().cast::<std::ffi::c_void>(),
                epsilon0,
            )
        }
    }

    fn distance_with_paper(&self, encoded: &[u8], short_ip: f32) -> f32 {
        unsafe {
            rabitq_query_distance_with_paper(
                self.space.ptr,
                self.prepared,
                encoded.as_ptr().cast::<std::ffi::c_void>(),
                short_ip,
            )
        }
    }

    fn residual_distance(&self, encoded: &[u8], residual_record: &[u8], long_distance: f32) -> f32 {
        unsafe {
            rabitq_residual_distance_from_record(
                self.space.ptr,
                self.prepared,
                encoded.as_ptr().cast::<std::ffi::c_void>(),
                residual_record.as_ptr().cast::<std::ffi::c_void>(),
                long_distance,
            )
            .estimate
        }
    }

    fn paper_estimate_batch(
        &self,
        ids: &[u32],
        encoded_base: *const std::ffi::c_void,
        encoded_stride: usize,
        epsilon0: f32,
        out: &mut [RabitqPaperEstimate],
    ) -> ANNResult<()> {
        if ids.len() != out.len() {
            return Err(ANNError::message("ExRaBitQ paper estimate batch size mismatch"));
        }
        if ids.is_empty() {
            return Ok(());
        }
        let ok = unsafe {
            rabitq_paper_estimate_batch(
                self.space.ptr,
                self.prepared,
                ids.as_ptr(),
                ids.len(),
                encoded_base,
                encoded_stride,
                epsilon0,
                out.as_mut_ptr(),
            )
        };
        if ok {
            Ok(())
        } else {
            Err(ANNError::message("ExRaBitQ paper estimate batch failed"))
        }
    }

    fn paper_estimate_batch_sidecar(
        &self,
        ids: &[u32],
        msb_base: *const u8,
        msb_stride: usize,
        factors_base: *const std::ffi::c_void,
        factors_stride: usize,
        epsilon0: f32,
        out: &mut [RabitqPaperEstimate],
    ) -> ANNResult<()> {
        if ids.len() != out.len() {
            return Err(ANNError::message("ExRaBitQ paper sidecar estimate batch size mismatch"));
        }
        if ids.is_empty() {
            return Ok(());
        }
        let ok = unsafe {
            rabitq_paper_estimate_batch_sidecar(
                self.space.ptr,
                self.prepared,
                ids.as_ptr(),
                ids.len(),
                msb_base,
                msb_stride,
                factors_base,
                factors_stride,
                epsilon0,
                out.as_mut_ptr(),
            )
        };
        if ok {
            Ok(())
        } else {
            Err(ANNError::message("ExRaBitQ paper sidecar estimate batch failed"))
        }
    }

    fn distance_batch(
        &self,
        ids: &[u32],
        modes: &[u8],
        encoded_base: *const std::ffi::c_void,
        encoded_stride: usize,
        short_ips: &[f32],
        out_distances: &mut [f32],
    ) -> ANNResult<()> {
        if ids.len() != modes.len()
            || ids.len() != short_ips.len()
            || ids.len() != out_distances.len()
        {
            return Err(ANNError::message("ExRaBitQ distance batch size mismatch"));
        }
        if ids.is_empty() {
            return Ok(());
        }
        let ok = unsafe {
            rabitq_query_distance_batch(
                self.space.ptr,
                self.prepared,
                ids.as_ptr(),
                modes.as_ptr(),
                ids.len(),
                encoded_base,
                encoded_stride,
                short_ips.as_ptr(),
                out_distances.as_mut_ptr(),
            )
        };
        if ok {
            Ok(())
        } else {
            Err(ANNError::message("ExRaBitQ distance batch failed"))
        }
    }

    fn residual_distance_batch(
        &self,
        ids: &[u32],
        encoded_base: *const std::ffi::c_void,
        encoded_stride: usize,
        residual_base: *const std::ffi::c_void,
        residual_stride: usize,
        long_distances: &[f32],
        out: &mut [RabitqDistanceInterval],
    ) -> ANNResult<()> {
        if ids.len() != long_distances.len() || ids.len() != out.len() {
            return Err(ANNError::message("ExRaBitQ residual distance batch size mismatch"));
        }
        if ids.is_empty() {
            return Ok(());
        }
        let ok = unsafe {
            rabitq_residual_distance_batch(
                self.space.ptr,
                self.prepared,
                ids.as_ptr(),
                ids.len(),
                encoded_base,
                encoded_stride,
                residual_base,
                residual_stride,
                long_distances.as_ptr(),
                out.as_mut_ptr(),
            )
        };
        if ok {
            Ok(())
        } else {
            Err(ANNError::message("ExRaBitQ residual distance batch failed"))
        }
    }
}

impl Drop for QueryComputer {
    fn drop(&mut self) {
        unsafe { rabitq_release_query(self.space.ptr, self.prepared) }
    }
}

#[derive(Debug, Default, Clone, Copy)]
pub struct OursPaperSearchStats {
    pub visited_nodes: u64,
    pub distance_computations: u64,
    pub hops: u64,
    pub prefetch_issued: u64,
    pub paper_checked: u64,
    pub paper_would_prune: u64,
    pub paper_not_pruned: u64,
    pub paper_full_saved: u64,
    pub paper_msb_kernel_calls: u64,
    pub paper_remaining_kernel_calls: u64,
    pub ffi_calls: u64,
}

#[derive(Debug, Clone)]
pub struct OursPaperSearchResult {
    pub ids: Vec<u32>,
    pub distances: Vec<f32>,
    pub stats: OursPaperSearchStats,
}

#[derive(Debug, Clone, Copy)]
struct SearchCandidate {
    distance: f32,
    id: u32,
    expanded: bool,
    // Paper lower bound on the true distance (f32::MAX when unknown). Used by
    // the k-th convergence early stop: once the k-th pool distance is <= every
    // unexpanded candidate's lower bound, the top-k cannot change and the
    // search terminates without losing recall.
    lower_bound: f32,
}

fn candidate_less(lhs: SearchCandidate, rhs: SearchCandidate) -> bool {
    lhs.distance < rhs.distance || (lhs.distance == rhs.distance && lhs.id < rhs.id)
}

fn insert_candidate_sorted(pool: &mut Vec<SearchCandidate>, candidate: SearchCandidate, l_value: usize) {
    if l_value == 0 {
        return;
    }
    if pool.len() >= l_value && !candidate_less(candidate, *pool.last().unwrap()) {
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
    if pool.len() > l_value {
        pool.truncate(l_value);
    }
}

#[derive(Default)]
struct VisitedScratch {
    tags: Vec<u32>,
    current_tag: u32,
}

impl VisitedScratch {
    fn reset_for(&mut self, total: usize) {
        if self.tags.len() < total {
            self.tags.resize(total, 0);
        }
        if self.current_tag == u32::MAX {
            self.tags.fill(0);
            self.current_tag = 0;
        }
        self.current_tag += 1;
    }

    fn try_mark(&mut self, id: usize) -> bool {
        if self.tags[id] == self.current_tag {
            return false;
        }
        self.tags[id] = self.current_tag;
        true
    }
}

thread_local! {
    static VISITED_SCRATCH: std::cell::RefCell<VisitedScratch> =
        std::cell::RefCell::new(VisitedScratch::default());
}

const DIST_BATCH_SIZE: usize = 64;

pub fn search_ours_paper_active<D, Ctx>(
    provider: &FullPrecisionProvider<f32, OursStore, D, Ctx>,
    query: &[f32],
    k: usize,
    l_value: usize,
    beam_width: usize,
    paper_epsilon0: f32,
    rerank_candidates: usize,
    early_stop_hops: usize,
    kth_stop: bool,
    verify: bool,
) -> ANNResult<OursPaperSearchResult>
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    VISITED_SCRATCH.with(|cell| {
        let mut scratch = cell.borrow_mut();
        let batched = search_ours_paper_active_inner(
            provider,
            query,
            k,
            l_value,
            beam_width,
            paper_epsilon0,
            rerank_candidates,
            early_stop_hops,
            kth_stop,
            &mut scratch,
        )?;
        if verify {
            let legacy = search_ours_paper_active_legacy(
                provider,
                query,
                k,
                l_value,
                beam_width,
                paper_epsilon0,
                rerank_candidates,
                early_stop_hops,
                kth_stop,
                &mut scratch,
            )?;
            verify_search_parity(&batched, &legacy)?;
        }
        Ok(batched)
    })
}

fn search_ours_paper_active_legacy<D, Ctx>(
    provider: &FullPrecisionProvider<f32, OursStore, D, Ctx>,
    query: &[f32],
    k: usize,
    mut l_value: usize,
    beam_width: usize,
    paper_epsilon0: f32,
    rerank_candidates: usize,
    early_stop_hops: usize,
    kth_stop: bool,
    visited_scratch: &mut VisitedScratch,
) -> ANNResult<OursPaperSearchResult>
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    if k == 0 {
        return Ok(OursPaperSearchResult {
            ids: Vec::new(),
            distances: Vec::new(),
            stats: OursPaperSearchStats::default(),
        });
    }
    l_value = l_value.max(k);
    if beam_width == 0 {
        return Err(ANNError::message("Ours paper-active search requires beam_width > 0"));
    }

    let total_points = provider.total_points();
    let base_points = provider.capacity();
    if total_points == 0 {
        return Ok(OursPaperSearchResult {
            ids: Vec::new(),
            distances: Vec::new(),
            stats: OursPaperSearchStats::default(),
        });
    }

    let computer = QueryComputer::new(provider.aux_vectors.space.clone(), query)?;
    let mut stats = OursPaperSearchStats::default();
    visited_scratch.reset_for(total_points);
    let mut pool = Vec::with_capacity(l_value + provider.num_start_points() + 1);

    if base_points != 0 {
        let start = 0_u32;
        visited_scratch.try_mark(0);
        let distance = computer.distance(provider.aux_vectors.get_vector(0));
        insert_candidate_sorted(
            &mut pool,
            SearchCandidate {
                distance,
                id: start,
                expanded: false,
                lower_bound: f32::MAX,
            },
            l_value,
        );
        stats.visited_nodes += 1;
        stats.distance_computations += 1;
        stats.paper_remaining_kernel_calls += 1;
    }

    let mut frontier = Vec::with_capacity(beam_width);
    let mut fresh = Vec::new();
    let mut paper_estimates = Vec::new();
    let mut id_buffer = AdjacencyList::new();

    let mut stall_hops = 0usize;
    loop {
        let back_before = pool.last().map(|candidate| candidate.distance).unwrap_or(0.0f32);
        frontier.clear();
        for candidate in &mut pool {
            if !candidate.expanded {
                candidate.expanded = true;
                frontier.push(candidate.id);
                if frontier.len() == beam_width {
                    break;
                }
            }
        }
        if frontier.is_empty() {
            break;
        }

        for node in frontier.iter().copied() {
            fresh.clear();
            provider
                .neighbors()
                .get_neighbors_sync(node.into_usize(), &mut id_buffer)?;
            for neighbor in id_buffer.iter().copied() {
                let neighbor_usize = neighbor.into_usize();
                if neighbor_usize >= total_points {
                    return Err(ANNError::message(format!(
                        "Ours DiskANN graph contains out-of-range neighbor {neighbor}"
                    )));
                }
                if visited_scratch.try_mark(neighbor_usize) {
                    fresh.push(neighbor);
                }
            }
            if fresh.is_empty() {
                continue;
            }
            stats.visited_nodes += fresh.len() as u64;

            paper_estimates.clear();
            paper_estimates.reserve(fresh.len());
            for candidate in fresh.iter().copied() {
                let id = candidate.into_usize();
                paper_estimates.push(computer.paper_estimate_sidecar(
                    provider.aux_vectors.get_msb_code(id),
                    provider.aux_vectors.get_factors(id),
                    paper_epsilon0,
                ));
            }
            stats.paper_msb_kernel_calls += fresh.len() as u64;

            let lookahead = provider.aux_vectors.prefetch_lookahead;
            for candidate in fresh.iter().take(lookahead) {
                provider.aux_vectors.prefetch_hint(candidate.into_usize());
                stats.prefetch_issued += 1;
            }

            for (slot, candidate_id) in fresh.iter().copied().enumerate() {
                if lookahead > 0 && slot + lookahead < fresh.len() {
                    provider
                        .aux_vectors
                        .prefetch_hint(fresh[slot + lookahead].into_usize());
                    stats.prefetch_issued += 1;
                }
                let pool_full = pool.len() >= l_value;
                let lower_bound = if pool_full {
                    pool.last().map(|candidate| candidate.distance).unwrap_or(f32::MAX)
                } else {
                    f32::MAX
                };
                let paper = paper_estimates[slot];
                if pool_full && paper.valid != 0 {
                    stats.paper_checked += 1;
                    if paper.lower_bound > lower_bound {
                        stats.paper_would_prune += 1;
                        stats.paper_full_saved += 1;
                        continue;
                    }
                    stats.paper_not_pruned += 1;
                }

                let encoded = provider.aux_vectors.get_vector(candidate_id.into_usize());
                let distance = if paper.valid != 0 {
                    computer.distance_with_paper(encoded, paper.short_ip)
                } else {
                    computer.distance(encoded)
                };
                stats.paper_remaining_kernel_calls += 1;
                stats.distance_computations += 1;
                insert_candidate_sorted(
                    &mut pool,
                    SearchCandidate {
                        distance,
                        id: candidate_id,
                        expanded: false,
                        lower_bound: if paper.valid != 0 {
                            paper.lower_bound
                        } else {
                            f32::MAX
                        },
                    },
                    l_value,
                );
            }
        }
        stats.hops += frontier.len() as u64;
        if kth_stop && pool.len() >= k {
            let kth = pool[k - 1].distance;
            let mut min_unexpanded_lb = f32::MAX;
            for candidate in pool.iter().filter(|candidate| !candidate.expanded) {
                if candidate.lower_bound < min_unexpanded_lb {
                    min_unexpanded_lb = candidate.lower_bound;
                }
            }
            if kth <= min_unexpanded_lb {
                break;
            }
        }
        if early_stop_hops != 0 && pool.len() >= l_value {
            let back_after = pool.last().map(|candidate| candidate.distance).unwrap_or(0.0f32);
            if back_after >= back_before {
                stall_hops += 1;
            } else {
                stall_hops = 0;
            }
            if stall_hops >= early_stop_hops {
                break;
            }
        }
    }

    let rerank_count = pool
        .iter()
        .filter(|candidate| candidate.id.into_usize() < base_points)
        .count()
        .min(rerank_candidates.max(k))
        .max(k);
    let mut reranked: Vec<_> = pool
        .iter()
        .filter(|candidate| candidate.id.into_usize() < base_points)
        .take(rerank_count)
        .map(|candidate| {
            let encoded = provider.aux_vectors.get_vector(candidate.id.into_usize());
            let residual_record = provider
                .aux_vectors
                .get_residual_record(candidate.id.into_usize());
            (
                computer.residual_distance(encoded, residual_record, candidate.distance),
                candidate.id,
            )
        })
        .collect();
    reranked.sort_unstable_by(|lhs, rhs| {
        lhs.0
            .partial_cmp(&rhs.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| lhs.1.cmp(&rhs.1))
    });
    let mut ids = Vec::with_capacity(k);
    let mut distances = Vec::with_capacity(k);
    for (distance, id) in reranked.into_iter().take(k) {
        ids.push(id);
        distances.push(distance);
    }

    Ok(OursPaperSearchResult {
        ids,
        distances,
        stats,
    })
}

fn search_ours_paper_active_inner<D, Ctx>(
    provider: &FullPrecisionProvider<f32, OursStore, D, Ctx>,
    query: &[f32],
    k: usize,
    mut l_value: usize,
    beam_width: usize,
    paper_epsilon0: f32,
    rerank_candidates: usize,
    early_stop_hops: usize,
    kth_stop: bool,
    visited_scratch: &mut VisitedScratch,
) -> ANNResult<OursPaperSearchResult>
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    if k == 0 {
        return Ok(OursPaperSearchResult {
            ids: Vec::new(),
            distances: Vec::new(),
            stats: OursPaperSearchStats::default(),
        });
    }
    l_value = l_value.max(k);
    if beam_width == 0 {
        return Err(ANNError::message("Ours paper-active search requires beam_width > 0"));
    }

    let total_points = provider.total_points();
    let base_points = provider.capacity();
    if total_points == 0 {
        return Ok(OursPaperSearchResult {
            ids: Vec::new(),
            distances: Vec::new(),
            stats: OursPaperSearchStats::default(),
        });
    }

    let computer = QueryComputer::new(provider.aux_vectors.space.clone(), query)?;
    let mut stats = OursPaperSearchStats::default();
    stats.ffi_calls += 1; // rabitq_prepare_query
    visited_scratch.reset_for(total_points);
    let mut pool = Vec::with_capacity(l_value + provider.num_start_points() + 1);

    if base_points != 0 {
        let start = 0_u32;
        visited_scratch.try_mark(0);
        let distance = computer.distance(provider.aux_vectors.get_vector(0));
        stats.ffi_calls += 1;
        insert_candidate_sorted(
            &mut pool,
            SearchCandidate {
                distance,
                id: start,
                expanded: false,
                lower_bound: f32::MAX,
            },
            l_value,
        );
        stats.visited_nodes += 1;
        stats.distance_computations += 1;
        stats.paper_remaining_kernel_calls += 1;
    }

    let mut frontier = Vec::with_capacity(beam_width);
    let mut fresh = Vec::new();
    let mut paper_estimates = Vec::new();
    let mut id_buffer = AdjacencyList::new();
    let mut batch_ids: Vec<u32> = Vec::with_capacity(DIST_BATCH_SIZE);
    let mut batch_slots: Vec<usize> = Vec::with_capacity(DIST_BATCH_SIZE);
    let mut batch_modes: Vec<u8> = Vec::with_capacity(DIST_BATCH_SIZE);
    let mut batch_short_ips: Vec<f32> = Vec::with_capacity(DIST_BATCH_SIZE);
    let mut batch_distances: Vec<f32> = Vec::with_capacity(DIST_BATCH_SIZE);

    let mut stall_hops = 0usize;
    loop {
        let back_before = pool.last().map(|candidate| candidate.distance).unwrap_or(0.0f32);
        frontier.clear();
        for candidate in &mut pool {
            if !candidate.expanded {
                candidate.expanded = true;
                frontier.push(candidate.id);
                if frontier.len() == beam_width {
                    break;
                }
            }
        }
        if frontier.is_empty() {
            break;
        }

        for node in frontier.iter().copied() {
            fresh.clear();
            provider
                .neighbors()
                .get_neighbors_sync(node.into_usize(), &mut id_buffer)?;
            for neighbor in id_buffer.iter().copied() {
                let neighbor_usize = neighbor.into_usize();
                if neighbor_usize >= total_points {
                    return Err(ANNError::message(format!(
                        "Ours DiskANN graph contains out-of-range neighbor {neighbor}"
                    )));
                }
                if visited_scratch.try_mark(neighbor_usize) {
                    fresh.push(neighbor);
                }
            }
            if fresh.is_empty() {
                continue;
            }
            stats.visited_nodes += fresh.len() as u64;

            paper_estimates.clear();
            paper_estimates.resize(fresh.len(), RabitqPaperEstimate::default());
            computer.paper_estimate_batch_sidecar(
                &fresh,
                provider.aux_vectors.msb_ptr(),
                provider.aux_vectors.msb_bytes,
                provider
                    .aux_vectors
                    .factors_ptr()
                    .cast::<std::ffi::c_void>(),
                provider.aux_vectors.factor_bytes,
                paper_epsilon0,
                &mut paper_estimates,
            )?;
            stats.paper_msb_kernel_calls += fresh.len() as u64;
            stats.ffi_calls += 1;

            let lookahead = provider.aux_vectors.prefetch_lookahead;
            for candidate in fresh.iter().take(lookahead) {
                provider.aux_vectors.prefetch_hint(candidate.into_usize());
                stats.prefetch_issued += 1;
            }

            for (slot, candidate_id) in fresh.iter().copied().enumerate() {
                if lookahead > 0 && slot + lookahead < fresh.len() {
                    provider
                        .aux_vectors
                        .prefetch_hint(fresh[slot + lookahead].into_usize());
                    stats.prefetch_issued += 1;
                }
                let paper = paper_estimates[slot];
                let pool_full = pool.len() >= l_value;
                let lower_bound = if pool_full {
                    pool.last()
                        .map(|candidate| candidate.distance)
                        .unwrap_or(f32::MAX)
                } else {
                    f32::MAX
                };
                if pool_full && paper.valid != 0 {
                    if paper.lower_bound > lower_bound {
                        // The pool can only get stricter within this frontier, so this
                        // candidate is definitely pruned and never needs a full distance.
                        stats.paper_checked += 1;
                        stats.paper_would_prune += 1;
                        stats.paper_full_saved += 1;
                        continue;
                    }
                    // Not definitely pruned: the final decision is made in
                    // flush_distance_batch against the exact evolving pool.
                }
                batch_ids.push(candidate_id);
                batch_slots.push(slot);
                batch_modes.push(if paper.valid != 0 { 1 } else { 0 });
                batch_short_ips.push(paper.short_ip);
                if batch_ids.len() >= DIST_BATCH_SIZE {
                    flush_distance_batch(
                        &computer,
                        provider,
                        &mut pool,
                        l_value,
                        &paper_estimates,
                        &mut batch_ids,
                        &mut batch_slots,
                        &mut batch_modes,
                        &mut batch_short_ips,
                        &mut batch_distances,
                        &mut stats,
                    )?;
                }
            }
            flush_distance_batch(
                &computer,
                provider,
                &mut pool,
                l_value,
                &paper_estimates,
                &mut batch_ids,
                &mut batch_slots,
                &mut batch_modes,
                &mut batch_short_ips,
                &mut batch_distances,
                &mut stats,
            )?;
        }
        stats.hops += frontier.len() as u64;
        if kth_stop && pool.len() >= k {
            let kth = pool[k - 1].distance;
            let mut min_unexpanded_lb = f32::MAX;
            for candidate in pool.iter().filter(|candidate| !candidate.expanded) {
                if candidate.lower_bound < min_unexpanded_lb {
                    min_unexpanded_lb = candidate.lower_bound;
                }
            }
            if kth <= min_unexpanded_lb {
                break;
            }
        }
        if early_stop_hops != 0 && pool.len() >= l_value {
            let back_after = pool.last().map(|candidate| candidate.distance).unwrap_or(0.0f32);
            if back_after >= back_before {
                stall_hops += 1;
            } else {
                stall_hops = 0;
            }
            if stall_hops >= early_stop_hops {
                break;
            }
        }
    }

    let rerank_count = pool
        .iter()
        .filter(|candidate| candidate.id.into_usize() < base_points)
        .count()
        .min(rerank_candidates.max(k))
        .max(k);
    let mut rerank_ids = Vec::with_capacity(rerank_count);
    let mut rerank_long_distances = Vec::with_capacity(rerank_count);
    for candidate in pool
        .iter()
        .filter(|candidate| candidate.id.into_usize() < base_points)
        .take(rerank_count)
    {
        rerank_ids.push(candidate.id);
        rerank_long_distances.push(candidate.distance);
    }
    let mut rerank_intervals = vec![RabitqDistanceInterval::default(); rerank_ids.len()];
    if !rerank_ids.is_empty() {
        computer.residual_distance_batch(
            &rerank_ids,
            provider
                .aux_vectors
                .records_ptr()
                .cast::<std::ffi::c_void>(),
            provider.aux_vectors.record_bytes,
            provider
                .aux_vectors
                .residual_ptr()
                .cast::<std::ffi::c_void>(),
            provider.aux_vectors.residual_record_bytes,
            &rerank_long_distances,
            &mut rerank_intervals,
        )?;
        stats.ffi_calls += 1;
    }
    let mut reranked: Vec<_> = rerank_intervals
        .iter()
        .map(|interval| interval.estimate)
        .zip(rerank_ids.iter().copied())
        .collect();
    reranked.sort_unstable_by(|lhs, rhs| {
        lhs.0
            .partial_cmp(&rhs.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| lhs.1.cmp(&rhs.1))
    });
    let mut ids = Vec::with_capacity(k);
    let mut distances = Vec::with_capacity(k);
    for (distance, id) in reranked.into_iter().take(k) {
        ids.push(id);
        distances.push(distance);
    }
    stats.ffi_calls += 1; // rabitq_release_query

    Ok(OursPaperSearchResult {
        ids,
        distances,
        stats,
    })
}

#[allow(clippy::too_many_arguments)]
fn flush_distance_batch<D, Ctx>(
    computer: &QueryComputer,
    provider: &FullPrecisionProvider<f32, OursStore, D, Ctx>,
    pool: &mut Vec<SearchCandidate>,
    l_value: usize,
    paper_estimates: &[RabitqPaperEstimate],
    batch_ids: &mut Vec<u32>,
    batch_slots: &mut Vec<usize>,
    batch_modes: &mut Vec<u8>,
    batch_short_ips: &mut Vec<f32>,
    batch_distances: &mut Vec<f32>,
    stats: &mut OursPaperSearchStats,
) -> ANNResult<()> {
    if batch_ids.is_empty() {
        return Ok(());
    }
    batch_distances.clear();
    batch_distances.resize(batch_ids.len(), 0.0);
    computer.distance_batch(
        batch_ids,
        batch_modes,
        provider
            .aux_vectors
            .records_ptr()
            .cast::<std::ffi::c_void>(),
        provider.aux_vectors.record_bytes,
        batch_short_ips,
        batch_distances,
    )?;
    stats.ffi_calls += 1;
    for index in 0..batch_ids.len() {
        let slot = batch_slots[index];
        let paper = paper_estimates[slot];
        let pool_full = pool.len() >= l_value;
        let lower_bound = if pool_full {
            pool.last()
                .map(|candidate| candidate.distance)
                .unwrap_or(f32::MAX)
        } else {
            f32::MAX
        };
        if pool_full && paper.valid != 0 {
            stats.paper_checked += 1;
            if paper.lower_bound > lower_bound {
                stats.paper_would_prune += 1;
                stats.paper_full_saved += 1;
                continue;
            }
            stats.paper_not_pruned += 1;
        }
        stats.paper_remaining_kernel_calls += 1;
        stats.distance_computations += 1;
        insert_candidate_sorted(
            pool,
            SearchCandidate {
                distance: batch_distances[index],
                id: batch_ids[index],
                expanded: false,
                lower_bound: if paper.valid != 0 {
                    paper.lower_bound
                } else {
                    f32::MAX
                },
            },
            l_value,
        );
    }
    batch_ids.clear();
    batch_slots.clear();
    batch_modes.clear();
    batch_short_ips.clear();
    Ok(())
}

fn verify_search_parity(
    batched: &OursPaperSearchResult,
    legacy: &OursPaperSearchResult,
) -> ANNResult<()> {
    if batched.ids != legacy.ids {
        return Err(ANNError::message(format!(
            "batch/legacy id mismatch: {:?} vs {:?}",
            batched.ids, legacy.ids
        )));
    }
    if batched.distances.len() != legacy.distances.len() {
        return Err(ANNError::message("batch/legacy distance count mismatch"));
    }
    for (index, (left, right)) in batched
        .distances
        .iter()
        .zip(legacy.distances.iter())
        .enumerate()
    {
        if left.to_bits() != right.to_bits() {
            return Err(ANNError::message(format!(
                "batch/legacy distance mismatch at {index}: {left} vs {right}"
            )));
        }
    }
    let batched_stats = &batched.stats;
    let legacy_stats = &legacy.stats;
    let fields = [
        ("visited_nodes", batched_stats.visited_nodes, legacy_stats.visited_nodes),
        (
            "distance_computations",
            batched_stats.distance_computations,
            legacy_stats.distance_computations,
        ),
        ("hops", batched_stats.hops, legacy_stats.hops),
        ("paper_checked", batched_stats.paper_checked, legacy_stats.paper_checked),
        (
            "paper_would_prune",
            batched_stats.paper_would_prune,
            legacy_stats.paper_would_prune,
        ),
        (
            "paper_not_pruned",
            batched_stats.paper_not_pruned,
            legacy_stats.paper_not_pruned,
        ),
        (
            "paper_full_saved",
            batched_stats.paper_full_saved,
            legacy_stats.paper_full_saved,
        ),
        (
            "paper_msb_kernel_calls",
            batched_stats.paper_msb_kernel_calls,
            legacy_stats.paper_msb_kernel_calls,
        ),
        (
            "paper_remaining_kernel_calls",
            batched_stats.paper_remaining_kernel_calls,
            legacy_stats.paper_remaining_kernel_calls,
        ),
    ];
    for (name, left, right) in fields {
        if left != right {
            return Err(ANNError::message(format!(
                "batch/legacy stat mismatch {name}: {left} vs {right}"
            )));
        }
    }
    Ok(())
}

pub fn build_ours_vamana_graph<D, Ctx>(
    provider: &FullPrecisionProvider<f32, OursStore, D, Ctx>,
    r: usize,
    l_build: usize,
    alpha: f32,
    beam_width: usize,
    batch_size: usize,
    refine_passes: usize,
    prune_candidate_cap: usize,
    build_early_stop_hops: usize,
) -> ANNResult<u64>
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    let record_count = provider.capacity();
    if record_count == 0 {
        return Ok(0);
    }
    let mut degrees = vec![0_u32; record_count];
    let mut edges = vec![0_u32; record_count * r];
    let msb_bytes = provider.aux_vectors.msb_bytes;
    let factor_bytes = provider.aux_vectors.factor_bytes;
    let mut msb_sidecar = vec![0_u8; record_count * msb_bytes];
    let mut factor_sidecar = vec![0_u8; record_count * factor_bytes];
    let mut msb_stride_out = 0_usize;
    let mut factor_bytes_out = 0_usize;
    let ok = unsafe {
        rabitq_build_vamana_graph(
            provider.aux_vectors.space.ptr,
            provider
                .aux_vectors
                .records_ptr()
                .cast::<std::ffi::c_void>(),
            record_count,
            provider.aux_vectors.record_bytes,
            r,
            l_build,
            alpha,
            beam_width,
            batch_size,
            refine_passes,
            prune_candidate_cap,
            build_early_stop_hops,
            degrees.as_mut_ptr(),
            edges.as_mut_ptr(),
            r,
            msb_sidecar.as_mut_ptr(),
            factor_sidecar.as_mut_ptr().cast::<std::ffi::c_void>(),
            &mut msb_stride_out,
            &mut factor_bytes_out,
        )
    };
    if !ok {
        return Err(ANNError::message("native ExRaBitQ Vamana graph build failed"));
    }
    if msb_stride_out != msb_bytes || factor_bytes_out != factor_bytes {
        return Err(ANNError::message(format!(
            "ExRaBitQ paper sidecar layout mismatch: msb {msb_stride_out} != {msb_bytes}, factors {factor_bytes_out} != {factor_bytes}"
        )));
    }
    provider
        .aux_vectors
        .install_paper_sidecar(msb_sidecar, factor_sidecar, record_count);

    let total_edges = degrees.iter().map(|&degree| degree as u64).sum();
    for id in 0..record_count {
        let degree = degrees[id] as usize;
        provider
            .neighbors()
            .set_neighbors_sync(id, &edges[id * r..id * r + degree])?;
    }
    for id in record_count..provider.total_points() {
        provider.neighbors().set_neighbors_sync(id, &[0])?;
    }
    Ok(total_edges)
}

pub fn encode_ours_payloads<D, Ctx>(
    provider: &FullPrecisionProvider<f32, OursStore, D, Ctx>,
    data: &Matrix<f32>,
    threads: usize,
) -> ANNResult<()>
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    let rows = data.nrows();
    if rows > provider.capacity() {
        return Err(ANNError::message(format!(
            "Ours encode rows {} exceed provider capacity {}",
            rows,
            provider.capacity()
        )));
    }
    if rows == 0 {
        return Ok(());
    }
    let workers = threads.max(1).min(rows);
    let chunk = rows.div_ceil(workers);
    let first_error = Mutex::new(None::<String>);
    std::thread::scope(|scope| {
        for worker in 0..workers {
            let begin = worker * chunk;
            let end = (begin + chunk).min(rows);
            if begin >= end {
                continue;
            }
            let first_error = &first_error;
            scope.spawn(move || {
                let mut full = vec![0_u8; provider.aux_vectors.full_record_bytes];
                for id in begin..end {
                    if first_error.lock().unwrap().is_some() {
                        break;
                    }
                    if let Err(err) = provider
                        .aux_vectors
                        .set_vector_with_scratch(id, data.row(id), &mut full)
                    {
                        *first_error.lock().unwrap() = Some(err.to_string());
                        break;
                    }
                }
            });
        }
    });
    if let Some(err) = first_error.into_inner().unwrap() {
        Err(ANNError::message(err))
    } else {
        Ok(())
    }
}

pub struct PruneAccessor<'a> {
    store: &'a OursStore,
    neighbors: &'a SimpleNeighborProviderAsync,
    distance: SymmetricComputer,
}

impl HasId for PruneAccessor<'_> {
    type Id = u32;
}

impl glue::PruneAccessor for PruneAccessor<'_> {
    type ElementRef<'a> = &'a [u8];
    type View<'a>
        = &'a Self
    where
        Self: 'a;
    type Distance<'a>
        = &'a SymmetricComputer
    where
        Self: 'a;
    type Neighbors<'a>
        = &'a SimpleNeighborProviderAsync
    where
        Self: 'a;

    async fn fill<Itr>(&mut self, _itr: Itr) -> ANNResult<(Self::View<'_>, Self::Distance<'_>)>
    where
        Itr: ExactSizeIterator<Item = Self::Id> + Clone + Send + Sync,
    {
        Ok((self, &self.distance))
    }

    fn neighbors(&mut self) -> Self::Neighbors<'_> {
        self.neighbors
    }
}

impl workingset::View<u32> for &PruneAccessor<'_> {
    type ElementRef<'a> = &'a [u8];
    type Element<'a>
        = &'a [u8]
    where
        Self: 'a;

    fn get(&self, id: u32) -> Option<Self::Element<'_>> {
        Some(self.store.get_vector(id.into_usize()))
    }
}

#[allow(dead_code)]
pub struct OursAccessor<'a, D, Ctx>
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    provider: &'a FullPrecisionProvider<f32, OursStore, D, Ctx>,
    computer: QueryComputer,
    id_buffer: AdjacencyList<u32>,
}

impl<'a, D, Ctx> OursAccessor<'a, D, Ctx>
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    #[allow(dead_code)]
    fn new(
        provider: &'a FullPrecisionProvider<f32, OursStore, D, Ctx>,
        query: &[f32],
    ) -> ANNResult<Self> {
        Ok(Self {
            provider,
            computer: QueryComputer::new(provider.aux_vectors.space.clone(), query)?,
            id_buffer: AdjacencyList::new(),
        })
    }
}

impl<D, Ctx> HasId for OursAccessor<'_, D, Ctx>
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    type Id = u32;
}

impl<D, Ctx> glue::SearchAccessor for OursAccessor<'_, D, Ctx>
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    fn starting_points(&self) -> impl Future<Output = ANNResult<Vec<u32>>> {
        std::future::ready(self.provider.starting_points())
    }

    fn num_starting_points(&self) -> impl Future<Output = ANNResult<usize>> {
        std::future::ready(Ok(self.provider.num_start_points()))
    }

    fn start_point_distances<F>(
        &mut self,
        mut f: F,
    ) -> impl Future<Output = ANNResult<()>> + Send
    where
        F: FnMut(Self::Id, f32) + Send,
    {
        let mut run = move || -> ANNResult<()> {
            for id in self.provider.starting_points()? {
                let distance = self.computer.distance(self.provider.aux_vectors.get_vector(id.into_usize()));
                f(id, distance);
            }
            Ok(())
        };
        std::future::ready(run())
    }

    fn expand_beam<Itr, P, F>(
        &mut self,
        ids: Itr,
        mut pred: P,
        mut on_neighbors: F,
    ) -> impl Future<Output = ANNResult<()>> + Send
    where
        Itr: Iterator<Item = Self::Id> + Send,
        P: glue::HybridPredicate<Self::Id> + Send + Sync,
        F: FnMut(Self::Id, f32) + Send,
    {
        let run = move || -> ANNResult<()> {
            let id_buffer = &mut self.id_buffer;
            for id in ids {
                self.provider
                    .neighbors()
                    .get_neighbors_sync(id.into_usize(), id_buffer)?;
                id_buffer.retain(|candidate| pred.eval_mut(candidate));
                let lookahead = self.provider.aux_vectors.prefetch_lookahead;
                for candidate in id_buffer.iter().take(lookahead) {
                    self.provider.aux_vectors.prefetch_hint(candidate.into_usize());
                }
                for (offset, candidate) in id_buffer.iter().enumerate() {
                    if lookahead > 0 && offset + lookahead < id_buffer.len() {
                        self.provider
                            .aux_vectors
                            .prefetch_hint(id_buffer[offset + lookahead].into_usize());
                    }
                    let encoded = self.provider.aux_vectors.get_vector(candidate.into_usize());
                    on_neighbors(*candidate, self.computer.distance(encoded));
                }
            }
            Ok(())
        };
        std::future::ready(run())
    }
}

#[derive(Debug, Clone, Copy)]
#[allow(dead_code)]
pub struct OursStrategy;

impl<'a, D, Ctx> SearchStrategy<'a, FullPrecisionProvider<f32, OursStore, D, Ctx>, &'a [f32]>
    for OursStrategy
where
    D: Send + Sync + 'static,
    Ctx: ExecutionContext,
{
    type SearchAccessor = OursAccessor<'a, D, Ctx>;
    type SearchAccessorError = ANNError;

    fn search_accessor(
        &'a self,
        provider: &'a FullPrecisionProvider<f32, OursStore, D, Ctx>,
        _context: &'a Ctx,
        query: &'a [f32],
    ) -> Result<Self::SearchAccessor, Self::SearchAccessorError> {
        OursAccessor::new(provider, query)
    }
}

#[derive(Debug, Default, Clone, Copy)]
#[allow(dead_code)]
pub struct ResidualRerank;

impl<'a, D, Ctx> SearchPostProcess<OursAccessor<'_, D, Ctx>, &'a [f32]> for ResidualRerank
where
    D: Send + Sync,
    Ctx: ExecutionContext,
{
    type Error = ANNError;

    fn post_process<I, B>(
        &self,
        accessor: &mut OursAccessor<'_, D, Ctx>,
        _query: &'a [f32],
        candidates: I,
        output: &mut B,
    ) -> impl Future<Output = Result<usize, Self::Error>> + Send
    where
        I: Iterator<Item = Neighbor<u32>> + Send,
        B: SearchOutputBuffer<u32> + Send + ?Sized,
    {
        let mut reranked: Vec<_> = candidates
            .map(|candidate| {
                let encoded = accessor
                    .provider
                    .aux_vectors
                    .get_vector(candidate.id().into_usize());
                let residual_record = accessor
                    .provider
                    .aux_vectors
                    .get_residual_record(candidate.id().into_usize());
                Neighbor::new(
                    *candidate.id(),
                    accessor
                        .computer
                        .residual_distance(encoded, residual_record, *candidate.distance()),
                )
            })
            .collect();
        reranked.sort_unstable_by(neighbor::ord::fast_distance);
        std::future::ready(Ok(output.extend(reranked)))
    }
}

impl<'a, D, Ctx> DefaultPostProcessor<'a, FullPrecisionProvider<f32, OursStore, D, Ctx>, &'a [f32]>
    for OursStrategy
where
    D: Send + Sync + 'static,
    Ctx: ExecutionContext,
{
    default_post_processor!(ResidualRerank);
}

impl<D, Ctx> PruneStrategy<FullPrecisionProvider<f32, OursStore, D, Ctx>> for OursStrategy
where
    D: Send + Sync + 'static,
    Ctx: ExecutionContext,
{
    type PruneAccessor<'a> = PruneAccessor<'a>;
    type PruneAccessorError = diskann::error::Infallible;

    fn prune_accessor<'a>(
        &'a self,
        provider: &'a FullPrecisionProvider<f32, OursStore, D, Ctx>,
        _context: &'a Ctx,
        _capacity: usize,
    ) -> Result<Self::PruneAccessor<'a>, Self::PruneAccessorError> {
        Ok(PruneAccessor {
            store: &provider.aux_vectors,
            neighbors: provider.neighbors(),
            distance: SymmetricComputer {
                space: provider.aux_vectors.space.clone(),
            },
        })
    }
}

impl<'a, D, Ctx> InsertStrategy<'a, FullPrecisionProvider<f32, OursStore, D, Ctx>, &'a [f32]>
    for OursStrategy
where
    D: Send + Sync + 'static,
    Ctx: ExecutionContext,
{
    type PruneStrategy = Self;

    fn prune_strategy(&self) -> Self::PruneStrategy {
        *self
    }
}

impl<D, Ctx, B> glue::MultiInsertStrategy<FullPrecisionProvider<f32, OursStore, D, Ctx>, B>
    for OursStrategy
where
    D: Send + Sync + 'static,
    Ctx: ExecutionContext,
    B: glue::Batch,
    Self: for<'a> InsertStrategy<
            'a,
            FullPrecisionProvider<f32, OursStore, D, Ctx>,
            B::Element<'a>,
            PruneStrategy = Self,
        >,
{
    type Seed = ();
    type FinishError = diskann::error::Infallible;
    type PruneStrategy = Self;
    type InsertStrategy = Self;

    fn insert_strategy(&self) -> Self::InsertStrategy {
        *self
    }

    fn finish<Itr>(
        &self,
        _provider: &FullPrecisionProvider<f32, OursStore, D, Ctx>,
        _ctx: &Ctx,
        _batch: &Arc<B>,
        _ids: Itr,
    ) -> impl Future<Output = Result<Self::Seed, Self::FinishError>> + Send
    where
        Itr: ExactSizeIterator<Item = u32> + Send,
    {
        std::future::ready(Ok(()))
    }

    fn seeded_prune_accessor<'a>(
        &'a self,
        provider: &'a FullPrecisionProvider<f32, OursStore, D, Ctx>,
        context: &'a Ctx,
        _seed: &'a (),
        capacity: usize,
    ) -> ANNResult<
        <Self as PruneStrategy<FullPrecisionProvider<f32, OursStore, D, Ctx>>>::PruneAccessor<'a>,
    > {
        Ok(self.prune_accessor(provider, context, capacity)?)
    }
}
