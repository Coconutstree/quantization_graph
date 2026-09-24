use std::path::Path;

use crate::config::RunContext;
use crate::payload::{PayloadAdapter, PreparedPayload};

pub struct LvqAdapter;

impl PayloadAdapter for LvqAdapter {
    fn method(&self) -> &'static str {
        "LVQ"
    }

    fn prepare(&self, _ctx: &RunContext, _progress_log: &Path) -> Result<PreparedPayload, String> {
        Err("LVQ-DiskANN is not wired yet: Microsoft DiskANN has no LVQ provider in baselines/diskann. Add an SVS LVQ4-backed provider instead of mapping LVQ to another quantizer.".to_string())
    }
}
