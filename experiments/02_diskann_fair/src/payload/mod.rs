use std::fmt;
use std::path::{Path, PathBuf};

use crate::config::RunContext;

pub mod fp32;
pub mod lvq;
pub mod ours;
pub mod pq;
pub mod saq;
pub mod sq;

#[derive(Debug, Clone)]
pub enum AdapterStatus {
    Done,
}

impl fmt::Display for AdapterStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Done => write!(f, "done"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct PreparedPayload {
    pub method: String,
    pub status: AdapterStatus,
    pub has_real_metrics: bool,
    pub payload_path: PathBuf,
    pub payload_json_path: PathBuf,
    pub nominal_bits_per_dim: f32,
    pub actual_bytes_per_vector: Option<f64>,
    pub code_bytes_per_vector: Option<f64>,
    pub metadata_bytes_per_vector: Option<f64>,
    pub codebook_bytes: Option<u64>,
    pub train_time_ms: Option<f64>,
    pub encode_time_ms: Option<f64>,
    pub build_time_ms: Option<f64>,
    pub graph_build_time_ms: Option<f64>,
    pub shared_graph_build_time_ms: Option<f64>,
    pub graph_build_mode: String,
    pub index_size_mb: Option<f64>,
    pub index_bytes: Option<u64>,
    pub auxiliary_bytes: Option<u64>,
    pub residual_bytes: Option<u64>,
    pub fp32_base_bytes: Option<u64>,
    pub graph_build_distance: String,
    pub peak_rss_mb: Option<f64>,
    pub search_results: Vec<SearchResult>,
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct SearchResult {
    pub search_list_size: usize,
    pub recall: f64,
    pub qps: f64,
    pub latency_mean_us: f64,
    pub latency_p95_us: f64,
    pub prepare_us: f64,
    pub traverse_us: f64,
    pub rerank_us: f64,
    pub neighbor_fetch_us: f64,
    pub visited_mark_us: f64,
    pub paper_batch_us: f64,
    pub flush_us: f64,
    pub visited_nodes: f64,
    pub distance_computations: f64,
    pub paper_checked: f64,
    pub paper_would_prune: f64,
    pub paper_msb_kernel_calls: f64,
    pub paper_remaining_kernel_calls: f64,
    pub ffi_calls_per_query: f64,
    pub mean_relative_error: f64,
    pub p95_relative_error: f64,
    pub mean_absolute_error: f64,
    pub top10_overlap: f64,
    pub pairwise_flip_rate_top10: f64,
    pub query_coarse_codec: String,
    pub status: String,
}

pub trait PayloadAdapter {
    fn method(&self) -> &'static str;
    fn prepare(&self, ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String>;
}

pub fn adapter_for(method: &str) -> Result<Box<dyn PayloadAdapter>, String> {
    match method.to_ascii_uppercase().as_str() {
        "PQ" | "PQ-DISKANN" => Ok(Box::new(pq::PqAdapter)),
        "SQ" | "SQ-DISKANN" => Ok(Box::new(sq::SqAdapter)),
        "SAQ" | "SAQ-DISKANN" => Ok(Box::new(saq::SaqAdapter)),
        "LVQ" | "LVQ-DISKANN" => Ok(Box::new(lvq::LvqAdapter)),
        "FP32" => Ok(Box::new(fp32::Fp32Adapter)),
        "OURS" | "OURS-DISKANN" => Ok(Box::new(ours::OursAdapter)),
        other => Err(format!(
            "unknown method {other}; expected PQ,SQ,SAQ,LVQ,Ours"
        )),
    }
}
