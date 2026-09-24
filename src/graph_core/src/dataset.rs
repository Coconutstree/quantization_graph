use std::fs;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct DatasetInfo {
    pub name: String,
    pub base_path: PathBuf,
    pub query_path: PathBuf,
    pub gt_path: PathBuf,
    pub base_count: u64,
    pub query_count: u64,
    pub dimension: u32,
    pub gt_k: u32,
}

impl DatasetInfo {
    pub fn load(
        dataset: &str,
        data_root: &Path,
        query_override: Option<&Path>,
        gt_override: Option<&Path>,
    ) -> Result<Self, String> {
        let dir = data_root.join(dataset);
        let base_path = dir.join(format!("{dataset}_base.fvecs"));
        let query_path = query_override
            .map(Path::to_path_buf)
            .unwrap_or_else(|| dir.join(format!("{dataset}_query.fvecs")));
        let gt_path = gt_override
            .map(Path::to_path_buf)
            .unwrap_or_else(|| dir.join(format!("{dataset}_groundtruth.ivecs")));

        let (base_count, dimension) = inspect_fvecs(&base_path)?;
        let (query_count, query_dim) = inspect_fvecs(&query_path)?;
        if query_dim != dimension {
            return Err(format!(
                "query dimension mismatch: base={dimension}, query={query_dim}"
            ));
        }
        let (_, gt_k) = inspect_ivecs(&gt_path)?;

        Ok(Self {
            name: dataset.to_string(),
            base_path,
            query_path,
            gt_path,
            base_count,
            query_count,
            dimension,
            gt_k,
        })
    }
}

fn inspect_fvecs(path: &Path) -> Result<(u64, u32), String> {
    let dim = read_first_i32(path)? as u32;
    let bytes = fs::metadata(path)
        .map_err(|err| format!("stat {}: {err}", path.display()))?
        .len();
    let row_bytes = 4_u64 + 4_u64 * dim as u64;
    if row_bytes == 0 || bytes % row_bytes != 0 {
        return Err(format!(
            "{} has size {bytes}, not divisible by fvecs row size {row_bytes}",
            path.display()
        ));
    }
    Ok((bytes / row_bytes, dim))
}

fn inspect_ivecs(path: &Path) -> Result<(u64, u32), String> {
    let dim = read_first_i32(path)? as u32;
    let bytes = fs::metadata(path)
        .map_err(|err| format!("stat {}: {err}", path.display()))?
        .len();
    let row_bytes = 4_u64 + 4_u64 * dim as u64;
    if row_bytes == 0 || bytes % row_bytes != 0 {
        return Err(format!(
            "{} has size {bytes}, not divisible by ivecs row size {row_bytes}",
            path.display()
        ));
    }
    Ok((bytes / row_bytes, dim))
}

fn read_first_i32(path: &Path) -> Result<i32, String> {
    let mut file = fs::File::open(path).map_err(|err| format!("open {}: {err}", path.display()))?;
    file.seek(SeekFrom::Start(0))
        .map_err(|err| format!("seek {}: {err}", path.display()))?;
    let mut buf = [0_u8; 4];
    file.read_exact(&mut buf)
        .map_err(|err| format!("read {}: {err}", path.display()))?;
    let dim = i32::from_le_bytes(buf);
    if dim <= 0 {
        return Err(format!(
            "{} has invalid first dimension {dim}",
            path.display()
        ));
    }
    Ok(dim)
}
