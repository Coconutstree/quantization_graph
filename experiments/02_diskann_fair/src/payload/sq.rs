use std::path::Path;

use crate::config::RunContext;
use crate::diskann_runner;
use crate::payload::{PayloadAdapter, PreparedPayload};

pub struct SqAdapter;

impl PayloadAdapter for SqAdapter {
    fn method(&self) -> &'static str {
        "SQ"
    }

    fn prepare(&self, ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
        diskann_runner::run_sq4_own(ctx, progress_log)
    }
}
