use std::path::Path;

use crate::config::RunContext;
use crate::diskann_runner;
use crate::payload::{PayloadAdapter, PreparedPayload};

/// Uniform fp32 reference search over an externally provided DiskANN-format
/// graph (used by the 03 decomposition "graph quality" measurement).
pub struct Fp32Adapter;

impl PayloadAdapter for Fp32Adapter {
    fn method(&self) -> &'static str {
        "FP32"
    }

    fn prepare(&self, ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
        let graph_path = ctx
            .graph_file
            .as_ref()
            .ok_or_else(|| "FP32 adapter requires --graph-file <diskann graph.bin>".to_string())?;
        diskann_runner::run_fp32_graph(ctx, progress_log, graph_path)
    }
}
