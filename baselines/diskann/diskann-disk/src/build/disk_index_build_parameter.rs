use serde::{Deserialize, Serialize};

pub const BYTES_IN_GB: f64 = 1024.0 * 1024.0 * 1024.0;
pub const DISK_SECTOR_LEN: usize = 4096;

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub enum QuantizationType {
    FP,
    PQ { num_chunks: usize },
    SQ { num_bits: usize },
}

#[derive(Debug, Clone, Copy)]
pub struct MemoryBudget {
    gb: f64,
}

impl MemoryBudget {
    pub fn try_from_gb(gb: f64) -> anyhow::Result<Self> {
        if gb.is_finite() && gb > 0.0 {
            Ok(Self { gb })
        } else {
            anyhow::bail!("memory budget must be a positive finite value in GiB")
        }
    }

    pub fn as_bytes(self) -> f64 {
        self.gb * BYTES_IN_GB
    }

    pub fn as_gb(self) -> f64 {
        self.gb
    }
}

#[derive(Debug, Clone, Copy)]
pub struct NumPQChunks {
    chunks: usize,
}

impl NumPQChunks {
    pub fn new_with(chunks: usize, dimensions: usize) -> anyhow::Result<Self> {
        if chunks == 0 {
            anyhow::bail!("number of PQ chunks must be non-zero")
        }
        if dimensions == 0 {
            anyhow::bail!("dimensions must be non-zero")
        }
        Ok(Self { chunks })
    }

    pub fn get(self) -> usize {
        self.chunks
    }
}

#[derive(Debug, Clone, Copy)]
pub struct DiskIndexBuildParameters {
    pub memory_budget: MemoryBudget,
    pub quantization_type: QuantizationType,
    pub num_pq_chunks: NumPQChunks,
}

impl DiskIndexBuildParameters {
    pub fn new(
        memory_budget: MemoryBudget,
        quantization_type: QuantizationType,
        num_pq_chunks: NumPQChunks,
    ) -> Self {
        Self {
            memory_budget,
            quantization_type,
            num_pq_chunks,
        }
    }
}
