use std::path::Path;

use crate::config::RunContext;
use crate::diskann_runner;
use crate::payload::{PayloadAdapter, PreparedPayload};

pub struct OursAdapter;

impl PayloadAdapter for OursAdapter {
    fn method(&self) -> &'static str {
        "Ours"
    }

    fn prepare(&self, ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
        diskann_runner::run_ours_exrabitq4(ctx, progress_log)
    }
}
