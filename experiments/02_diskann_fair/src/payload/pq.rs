use std::path::Path;

use crate::config::RunContext;
use crate::diskann_runner;
use crate::payload::{PayloadAdapter, PreparedPayload};

pub struct PqAdapter;

impl PayloadAdapter for PqAdapter {
    fn method(&self) -> &'static str {
        "PQ"
    }

    fn prepare(&self, ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
        diskann_runner::run_pq4_own(ctx, progress_log)
    }
}
