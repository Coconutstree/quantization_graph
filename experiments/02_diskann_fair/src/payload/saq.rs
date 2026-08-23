use std::path::Path;

use crate::config::RunContext;
use crate::diskann_runner;
use crate::payload::{PayloadAdapter, PreparedPayload};

pub struct SaqAdapter;

impl PayloadAdapter for SaqAdapter {
    fn method(&self) -> &'static str {
        "SAQ"
    }

    fn prepare(&self, ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
        if ctx.config.shared_graph {
            diskann_runner::run_saq4(ctx, progress_log)
        } else {
            diskann_runner::run_saq4_own(ctx, progress_log)
        }
    }
}
