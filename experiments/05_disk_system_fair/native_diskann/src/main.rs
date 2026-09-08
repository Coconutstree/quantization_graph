#![recursion_limit = "512"]

use std::collections::{BTreeMap, HashSet};
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use std::time::Instant;

use anyhow::{anyhow, bail, Context, Result};
use diskann_disk::data_model::{AdHoc, CachingStrategy, GraphDataType};
use diskann_disk::disk_index_build_parameter::DISK_SECTOR_LEN;
use diskann_disk::search::provider::aligned_file_reader::traits::{
    AlignedFileReader, AlignedReaderFactory,
};
use diskann_disk::search::provider::aligned_file_reader::{AlignedRead, A1};
use diskann_disk::search::provider::disk_provider::DiskIndexSearcher;
use diskann_disk::search::provider::disk_vertex_provider_factory::DiskVertexProviderFactory;
use diskann_disk::search::search_mode::SearchMode;
use diskann_disk::storage::disk_index_reader::DiskIndexReader;
use diskann_disk::storage::DiskIndexWriter;
use diskann_disk::utils::QueryStatistics;
use diskann_providers::model::{
    GeneratePivotArguments, MAX_PQ_TRAINING_SET_SIZE, NUM_KMEANS_REPS_PQ, NUM_PQ_CENTROIDS,
};
use diskann_providers::storage::{
    get_compressed_pq_file, get_disk_index_file, get_mem_index_file, get_pq_pivot_file,
    FileStorageProvider, PQStorage,
};
use diskann_vector::distance::Metric;
use rayon::prelude::*;
use serde_json::{json, Value};

const LAYER: &str = "05c";
const METHOD: &str = "DiskANN-PQ-Disk";
const SOURCE_SUITE: &str = "03_system_fair";
const SOURCE_KERNEL: &str = "official diskann-disk PQ search";
const PORT_KIND: &str = "official_native_disk";
const PAGE_SIZE: usize = 4096;
const K: u32 = 10;
const R: usize = 64;
const L_BUILD: usize = 400;
const ALPHA: f32 = 1.2;

#[derive(Debug)]
struct Args {
    values: BTreeMap<String, String>,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut values = BTreeMap::new();
        let mut iter = std::env::args().skip(1);
        while let Some(flag) = iter.next() {
            if !flag.starts_with("--") {
                bail!("unexpected positional argument: {flag}");
            }
            let value = iter
                .next()
                .ok_or_else(|| anyhow!("missing value for {flag}"))?;
            if value.starts_with("--") {
                bail!("missing value for {flag}; got flag {value}");
            }
            if values.insert(flag.clone(), value).is_some() {
                bail!("duplicate argument: {flag}");
            }
        }
        Ok(Self { values })
    }

    fn text(&self, flag: &str) -> Result<&str> {
        self.values
            .get(flag)
            .map(String::as_str)
            .ok_or_else(|| anyhow!("missing required argument: {flag}"))
    }

    fn path(&self, flag: &str) -> Result<PathBuf> {
        Ok(PathBuf::from(self.text(flag)?))
    }

    fn number<T: std::str::FromStr>(&self, flag: &str) -> Result<T> {
        self.text(flag)?.parse().map_err(|_| {
            anyhow!(
                "invalid numeric value for {flag}: {}",
                self.text(flag).unwrap()
            )
        })
    }

    fn optional(&self, flag: &str) -> Option<&str> {
        self.values.get(flag).map(String::as_str)
    }

    fn validate(&self) -> Result<()> {
        if self.text("--contract-version")? != "2" {
            bail!("only result contract version 2 is supported");
        }
        if self.text("--layer")? != LAYER || self.text("--method")? != METHOD {
            bail!("this executable implements only {LAYER}:{METHOD}");
        }
        if self.text("--storage-mode")? != "hybrid_disk" {
            bail!("official DiskANN requires storage-mode=hybrid_disk");
        }
        if self.number::<usize>("--page-size")? != PAGE_SIZE {
            bail!("official DiskANN disk sectors must be {PAGE_SIZE} bytes");
        }
        if self.text("--direct-io")? != "required" || self.text("--native-aio")? != "required" {
            bail!("formal DiskANN requires O_DIRECT and native asynchronous I/O");
        }
        if self.number::<usize>("--max-inflight-io")? != 128 {
            bail!("official LinuxAlignedFileReader uses an io_uring depth of 128");
        }
        Ok(())
    }
}

#[derive(Debug)]
struct IndexFiles {
    root: PathBuf,
    prefix: String,
    converted_base: PathBuf,
    disk_index: PathBuf,
    pq_pivots: PathBuf,
    pq_codes: PathBuf,
    metadata: PathBuf,
    manifest: PathBuf,
}

impl IndexFiles {
    fn new(root: PathBuf) -> Result<Self> {
        let prefix_path = root.join("diskann_pq_R64_L400_A1.2");
        let prefix = prefix_path
            .to_str()
            .ok_or_else(|| anyhow!("non-UTF8 index prefix: {}", prefix_path.display()))?
            .to_string();
        Ok(Self {
            converted_base: root.join("source_base.fbin"),
            disk_index: PathBuf::from(get_disk_index_file(&prefix)),
            pq_pivots: PathBuf::from(get_pq_pivot_file(&prefix)),
            pq_codes: PathBuf::from(get_compressed_pq_file(&prefix)),
            metadata: root.join("index.meta.json"),
            manifest: root.join("index.manifest.json"),
            root,
            prefix,
        })
    }

    fn complete(&self) -> bool {
        [
            &self.disk_index,
            &self.pq_pivots,
            &self.pq_codes,
            &self.metadata,
            &self.manifest,
        ]
        .iter()
        .all(|path| path.is_file())
    }

    fn measured_paths(&self) -> [&Path; 3] {
        [&self.disk_index, &self.pq_pivots, &self.pq_codes]
    }

    fn index_bytes(&self) -> Result<u64> {
        self.measured_paths()
            .iter()
            .try_fold(0_u64, |sum, path| Ok(sum + fs::metadata(path)?.len()))
    }
}

#[derive(Debug, Clone)]
struct SharedGraphInfo {
    path: PathBuf,
    file_size: u64,
    max_degree: u32,
    medoid: usize,
    frozen_count: usize,
    node_count: usize,
    start_index: usize,
}

#[derive(Debug, Clone)]
struct IndexMeta {
    base_count: usize,
    source_base_count: usize,
    dimension: usize,
    pq_chunks: usize,
    resident_bytes: usize,
    codebook_bytes: usize,
    cached_node_bytes: usize,
    build_time_ms: f64,
}

impl IndexMeta {
    fn to_json(&self) -> Value {
        json!({
            "schema_version": 1,
            "implementation": "official microsoft DiskANN diskann-disk",
            "metric": "squared_l2",
            "base_count": self.base_count,
            "source_base_count": self.source_base_count,
            "dimension": self.dimension,
            "R": R,
            "L_build": L_BUILD,
            "alpha": ALPHA,
            "pq_chunks": self.pq_chunks,
            "effective_navigation_bits_per_dimension": 8.0 * self.pq_chunks as f64 / self.dimension as f64,
            "resident_bytes": self.resident_bytes,
            "codebook_bytes": self.codebook_bytes,
            "cached_node_bytes": self.cached_node_bytes,
            "disk_sector_bytes": PAGE_SIZE,
            "search_io_backend": "LinuxAlignedFileReader(O_DIRECT+io_uring)",
            "build_time_ms": self.build_time_ms,
        })
    }

    fn from_path(path: &Path) -> Result<Self> {
        let value: Value = serde_json::from_reader(BufReader::new(File::open(path)?))?;
        let number = |name: &str| -> Result<usize> {
            value[name]
                .as_u64()
                .map(|v| v as usize)
                .ok_or_else(|| anyhow!("{} is missing integer {name}", path.display()))
        };
        if value["R"].as_u64() != Some(R as u64)
            || value["L_build"].as_u64() != Some(L_BUILD as u64)
            || value["alpha"].as_f64() != Some(ALPHA as f64)
        {
            bail!(
                "{} does not contain the formal R/L_build/alpha",
                path.display()
            );
        }
        Ok(Self {
            base_count: number("base_count")?,
            source_base_count: value["source_base_count"]
                .as_u64()
                .map(|v| v as usize)
                .unwrap_or_else(|| number("base_count").unwrap_or(0)),
            dimension: number("dimension")?,
            pq_chunks: number("pq_chunks")?,
            resident_bytes: number("resident_bytes")?,
            codebook_bytes: number("codebook_bytes")?,
            cached_node_bytes: number("cached_node_bytes")?,
            build_time_ms: value["build_time_ms"].as_f64().unwrap_or(0.0),
        })
    }
}

fn read_i32(reader: &mut impl Read) -> Result<i32> {
    let mut bytes = [0_u8; 4];
    reader.read_exact(&mut bytes)?;
    Ok(i32::from_le_bytes(bytes))
}

fn inspect_fvecs(path: &Path) -> Result<(usize, usize)> {
    let mut input = BufReader::new(File::open(path)?);
    let dim = read_i32(&mut input)?;
    if dim <= 0 {
        bail!("{} has invalid fvec dimension {dim}", path.display());
    }
    let record_bytes = 4_u64 * (dim as u64 + 1);
    let bytes = fs::metadata(path)?.len();
    if bytes == 0 || !bytes.is_multiple_of(record_bytes) {
        bail!("{} is not a complete fvecs matrix", path.display());
    }
    Ok(((bytes / record_bytes) as usize, dim as usize))
}

fn convert_fvecs_to_fbin(
    source: &Path,
    destination: &Path,
    graph: &SharedGraphInfo,
) -> Result<(usize, usize, usize)> {
    let (source_rows, dim) = inspect_fvecs(source)?;
    if graph.start_index >= source_rows {
        bail!(
            "shared graph start_index {} is outside source base rows {}",
            graph.start_index,
            source_rows
        );
    }
    if graph.node_count < source_rows {
        bail!(
            "shared graph has fewer nodes ({}) than source base rows ({source_rows})",
            graph.node_count
        );
    }
    if graph.medoid >= graph.node_count {
        bail!(
            "shared graph medoid {} is outside graph node count {}",
            graph.medoid,
            graph.node_count
        );
    }

    let mut input = BufReader::with_capacity(1 << 20, File::open(source)?);
    let mut rows = Vec::with_capacity(source_rows);
    let mut output = BufWriter::with_capacity(1 << 20, File::create(destination)?);
    output.write_all(&(graph.node_count as u32).to_le_bytes())?;
    output.write_all(&(dim as u32).to_le_bytes())?;
    let mut row = vec![0_u8; dim * 4];
    for row_id in 0..source_rows {
        let got_dim = read_i32(&mut input)?;
        if got_dim != dim as i32 {
            bail!(
                "{} row {row_id} changed dimension to {got_dim}",
                source.display()
            );
        }
        input.read_exact(&mut row)?;
        output.write_all(&row)?;
        rows.push(row.clone());
    }

    let start_row = rows[graph.start_index].clone();
    for row_id in source_rows..graph.node_count {
        let filler = if row_id == graph.medoid || graph.frozen_count > 0 {
            &start_row
        } else {
            &rows[row_id % source_rows]
        };
        output.write_all(filler)?;
    }
    output.flush()?;
    Ok((source_rows, graph.node_count, dim))
}

fn write_json(path: &Path, value: &Value) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut output = BufWriter::new(File::create(path)?);
    serde_json::to_writer_pretty(&mut output, value)?;
    output.write_all(b"\n")?;
    output.flush()?;
    Ok(())
}

fn sha256(path: &Path) -> Result<String> {
    let output = Command::new("sha256sum").arg(path).output()?;
    if !output.status.success() {
        bail!("sha256sum failed for {}", path.display());
    }
    String::from_utf8(output.stdout)?
        .split_whitespace()
        .next()
        .map(str::to_owned)
        .ok_or_else(|| anyhow!("sha256sum returned no digest for {}", path.display()))
}

fn write_index_manifest(files: &IndexFiles) -> Result<()> {
    let mut entries = Vec::new();
    for path in files.measured_paths() {
        entries.push(json!({
            "path": path.file_name().and_then(|v| v.to_str()).unwrap_or("unknown"),
            "bytes": fs::metadata(path)?.len(),
            "sha256": sha256(path)?,
        }));
    }
    write_json(
        &files.manifest,
        &json!({
            "schema_version": 1,
            "method": METHOD,
            "files": entries,
        }),
    )
}

fn shared_graph_path(args: &Args) -> Result<PathBuf> {
    if let Some(path) = args.optional("--shared-graph") {
        return Ok(PathBuf::from(path));
    }
    let dataset = args.text("--dataset")?;
    Ok(args
        .path("--source-results-root")?
        .join(dataset)
        .join("indexes/02_diskann_fair/shared_graph")
        .join("diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"))
}

fn parse_start_index(meta_path: &Path) -> Result<usize> {
    let text = fs::read_to_string(meta_path)
        .with_context(|| format!("reading shared graph metadata {}", meta_path.display()))?;
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("start_index=") {
            return value
                .parse::<usize>()
                .with_context(|| format!("invalid start_index in {}", meta_path.display()));
        }
    }
    bail!("missing start_index in {}", meta_path.display())
}

fn inspect_shared_graph(args: &Args) -> Result<SharedGraphInfo> {
    let path = shared_graph_path(args)?;
    if !path.is_file() {
        bail!("missing source shared graph: {}", path.display());
    }
    let mut reader = BufReader::new(File::open(&path)?);
    let file_size = {
        let mut bytes = [0_u8; 8];
        reader.read_exact(&mut bytes)?;
        u64::from_le_bytes(bytes)
    };
    let max_degree = {
        let mut bytes = [0_u8; 4];
        reader.read_exact(&mut bytes)?;
        u32::from_le_bytes(bytes)
    };
    let medoid = {
        let mut bytes = [0_u8; 4];
        reader.read_exact(&mut bytes)?;
        u32::from_le_bytes(bytes) as usize
    };
    let frozen_count = {
        let mut bytes = [0_u8; 8];
        reader.read_exact(&mut bytes)?;
        u64::from_le_bytes(bytes) as usize
    };
    let mut position = 24_u64;
    let mut node_count = 0_usize;
    while position < file_size {
        let mut bytes = [0_u8; 4];
        reader.read_exact(&mut bytes)?;
        let degree = u32::from_le_bytes(bytes) as u64;
        let skip = (degree * 4) as i64;
        reader.seek_relative(skip)?;
        position += 4 + degree * 4;
        node_count += 1;
    }
    let start_index = parse_start_index(&path.with_extension("json"))?;
    Ok(SharedGraphInfo {
        path,
        file_size,
        max_degree,
        medoid,
        frozen_count,
        node_count,
        start_index,
    })
}

fn prepare_official_diskann_inputs(
    args: &Args,
    files: &IndexFiles,
    base_count: usize,
    dimension: usize,
    pq_chunks: usize,
) -> Result<()> {
    let mem_index = PathBuf::from(get_mem_index_file(&files.prefix));
    if !mem_index.is_file() {
            let source_graph = inspect_shared_graph(args)?.path;
        fs::copy(&source_graph, &mem_index).with_context(|| {
            format!(
                "copying shared graph {} to {}",
                source_graph.display(),
                mem_index.display()
            )
        })?;
    }

    if files.pq_pivots.is_file() && files.pq_codes.is_file() {
        return Ok(());
    }

    let mut pq_storage = PQStorage::new(
        &files.pq_pivots.to_string_lossy(),
        &files.pq_codes.to_string_lossy(),
        Some(&files.converted_base.to_string_lossy()),
    );
    let mut rng = diskann_providers::utils::create_rnd_provider_from_seed(42).create_rnd();
    let p_val = (MAX_PQ_TRAINING_SET_SIZE / base_count as f64).min(1.0);
    let (mut train_data, num_train, train_dim) = pq_storage
        .get_random_train_data_slice::<f32, _>(p_val, &FileStorageProvider, &mut rng)?;
    let pool = diskann_providers::utils::create_thread_pool(
        std::thread::available_parallelism().map_or(1, usize::from),
    )?;
    let random_provider = diskann_providers::utils::create_rnd_provider_from_seed(42);
    diskann_providers::model::pq::generate_pq_pivots(
        GeneratePivotArguments::new(
            num_train,
            train_dim,
            NUM_PQ_CENTROIDS,
            pq_chunks,
            NUM_KMEANS_REPS_PQ,
        )?,
        true,
        &mut train_data,
        &pq_storage,
        &FileStorageProvider,
        random_provider,
        pool.as_ref(),
    )?;
    diskann_providers::model::pq::generate_pq_data_from_pivots::<f32, _>(
        NUM_PQ_CENTROIDS,
        pq_chunks,
        &mut pq_storage,
        &FileStorageProvider,
        0,
        pool.as_ref(),
    )?;

    if train_dim != dimension {
        bail!("PQ training dimension {train_dim} does not match dataset dimension {dimension}");
    }
    Ok(())
}

fn export_index(args: &Args, files: &IndexFiles) -> Result<IndexMeta> {
    fs::create_dir_all(&files.root)?;
    let dataset = args.text("--dataset")?;
    let source = args
        .path("--data-root")?
        .join(dataset)
        .join(format!("{dataset}_base.fvecs"));
    let graph = inspect_shared_graph(args)?;
    let (source_base_count, base_count, dimension) =
        convert_fvecs_to_fbin(&source, &files.converted_base, &graph)?;
    if graph.max_degree as usize > R + 32 {
        eprintln!(
            "warning: shared graph max_degree={} exceeds formal R={} by more than slack",
            graph.max_degree, R
        );
    }
    if graph.file_size != fs::metadata(&graph.path)?.len() {
        bail!("shared graph header file_size does not match actual length");
    }
    let pq_chunks = dimension.div_ceil(2);
    let started = Instant::now();
    prepare_official_diskann_inputs(args, files, base_count, dimension, pq_chunks)?;
    let writer = DiskIndexWriter::new(
        files.converted_base.to_string_lossy().into_owned(),
        files.prefix.clone(),
        None,
        DISK_SECTOR_LEN,
    )?;
    writer.create_disk_layout::<AdHoc<f32>, _>(&FileStorageProvider)?;
    let resident_bytes = fs::metadata(&files.pq_codes)?.len() as usize;
    let codebook_bytes = fs::metadata(&files.pq_pivots)?.len() as usize;
    let cached_node_bytes =
        (dimension * std::mem::size_of::<f32>() + R * std::mem::size_of::<u32>() + 128)
            .next_multiple_of(64);
    let meta = IndexMeta {
        base_count,
        source_base_count,
        dimension,
        pq_chunks,
        resident_bytes,
        codebook_bytes,
        cached_node_bytes,
        build_time_ms: started.elapsed().as_secs_f64() * 1000.0,
    };
    write_json(&files.metadata, &meta.to_json())?;
    write_index_manifest(files)?;
    Ok(meta)
}

fn read_fvecs(path: &Path) -> Result<Vec<Vec<f32>>> {
    let (rows, dim) = inspect_fvecs(path)?;
    let mut input = BufReader::new(File::open(path)?);
    let mut result = Vec::with_capacity(rows);
    let mut bytes = vec![0_u8; dim * 4];
    for row_id in 0..rows {
        let got_dim = read_i32(&mut input)?;
        if got_dim != dim as i32 {
            bail!("{} row {row_id} has inconsistent dimension", path.display());
        }
        input.read_exact(&mut bytes)?;
        result.push(
            bytes
                .chunks_exact(4)
                .map(|part| f32::from_le_bytes(part.try_into().unwrap()))
                .collect(),
        );
    }
    Ok(result)
}

fn read_ivecs_top10(path: &Path) -> Result<Vec<Vec<u32>>> {
    let mut input = BufReader::new(File::open(path)?);
    let first_dim = read_i32(&mut input)?;
    if first_dim < K as i32 {
        bail!("{} ground truth has fewer than {K} IDs", path.display());
    }
    let record_bytes = 4_u64 * (first_dim as u64 + 1);
    let rows = (fs::metadata(path)?.len() / record_bytes) as usize;
    input = BufReader::new(File::open(path)?);
    let mut result = Vec::with_capacity(rows);
    for row_id in 0..rows {
        let dim = read_i32(&mut input)?;
        if dim != first_dim {
            bail!(
                "{} row {row_id} has inconsistent ground-truth width",
                path.display()
            );
        }
        let mut ids = Vec::with_capacity(K as usize);
        for index in 0..dim as usize {
            let value = read_i32(&mut input)? as u32;
            if index < K as usize {
                ids.push(value);
            }
        }
        result.push(ids);
    }
    Ok(result)
}

fn read_query_order(path: &Path, query_count: usize) -> Result<Vec<usize>> {
    let bytes = fs::read(path)?;
    if bytes.len() != query_count * 4 {
        bail!(
            "query-order length {} does not match {} queries",
            bytes.len(),
            query_count
        );
    }
    let order = bytes
        .chunks_exact(4)
        .map(|part| u32::from_le_bytes(part.try_into().unwrap()) as usize)
        .collect::<Vec<_>>();
    let unique = order.iter().copied().collect::<HashSet<_>>();
    if unique.len() != query_count || order.iter().any(|&id| id >= query_count) {
        bail!("query-order file is not a permutation");
    }
    Ok(order)
}

#[derive(Clone)]
struct MemoryAlignedReader {
    bytes: Arc<Vec<u8>>,
}

impl AlignedFileReader for MemoryAlignedReader {
    type Alignment = A1;

    fn read(&mut self, requests: &mut [AlignedRead<u8, A1>]) -> diskann::ANNResult<()> {
        for request in requests {
            let start = request.offset() as usize;
            let end = start.saturating_add(request.aligned_buf().len());
            let target = request.aligned_buf_mut();
            if start >= self.bytes.len() {
                target.fill(0);
            } else {
                let available_end = end.min(self.bytes.len());
                let copied = available_end - start;
                target[..copied].copy_from_slice(&self.bytes[start..available_end]);
                target[copied..].fill(0);
            }
        }
        Ok(())
    }
}

#[derive(Clone)]
struct MemoryReaderFactory {
    bytes: Arc<Vec<u8>>,
}

impl AlignedReaderFactory for MemoryReaderFactory {
    type AlignedReaderType = MemoryAlignedReader;

    fn build(&self) -> diskann::ANNResult<Self::AlignedReaderType> {
        Ok(MemoryAlignedReader {
            bytes: self.bytes.clone(),
        })
    }
}

#[derive(Clone)]
struct QueryRun {
    query_id: usize,
    ids: Vec<u32>,
    recall: f64,
    latency_us: f64,
    stats: QueryStatistics,
}

struct BatchRun {
    runs: Vec<QueryRun>,
    wall_seconds: f64,
    peak_rss_bytes: u64,
}

fn recall_at_10(ids: &[u32], truth: &[u32]) -> f64 {
    ids.iter()
        .take(K as usize)
        .filter(|id| truth.contains(id))
        .count() as f64
        / K as f64
}

fn execute_batch<Data, Factory>(
    searcher: &DiskIndexSearcher<Data, Factory>,
    queries: &[Vec<f32>],
    groundtruth: &[Vec<u32>],
    order: &[usize],
    workers: usize,
    width: usize,
    beam: usize,
    warmup_queries: usize,
) -> Result<BatchRun>
where
    Data: GraphDataType<VectorDataType = f32, VectorIdType = u32>,
    Factory: diskann_disk::search::traits::VertexProviderFactory<Data> + Sync,
{
    for &query_id in order.iter().take(warmup_queries.min(order.len())) {
        searcher.search(
            &queries[query_id],
            K,
            width as u32,
            Some(beam),
            SearchMode::graph(),
        )?;
    }
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers)
        .build()?;
    let started = Instant::now();
    let runs = pool.install(|| {
        order
            .par_iter()
            .map(|&query_id| -> Result<QueryRun> {
                let begin = Instant::now();
                let result = searcher.search(
                    &queries[query_id],
                    K,
                    width as u32,
                    Some(beam),
                    SearchMode::graph(),
                )?;
                let latency_us = begin.elapsed().as_secs_f64() * 1_000_000.0;
                let ids = result
                    .results
                    .iter()
                    .take(result.stats.result_count as usize)
                    .map(|item| item.vertex_id)
                    .collect::<Vec<_>>();
                Ok(QueryRun {
                    query_id,
                    recall: recall_at_10(&ids, &groundtruth[query_id]),
                    ids,
                    latency_us,
                    stats: result.stats.query_statistics,
                })
            })
            .collect::<Result<Vec<_>>>()
    })?;
    Ok(BatchRun {
        runs,
        wall_seconds: started.elapsed().as_secs_f64(),
        peak_rss_bytes: peak_rss_bytes(),
    })
}

#[derive(Default)]
struct Parity {
    max_recall_delta: f64,
    overlap_sum: f64,
    comparison_delta_sum: f64,
    count: usize,
}

impl Parity {
    fn add(&mut self, direct: &QueryRun, memory: &QueryRun) -> Result<()> {
        if direct.query_id != memory.query_id {
            bail!("memory/direct query order diverged");
        }
        self.max_recall_delta = self
            .max_recall_delta
            .max((direct.recall - memory.recall).abs());
        self.overlap_sum += direct
            .ids
            .iter()
            .filter(|id| memory.ids.contains(id))
            .count() as f64
            / direct.ids.len().max(1) as f64;
        let a = direct.stats.total_comparisons as u64;
        let b = memory.stats.total_comparisons as u64;
        self.comparison_delta_sum += a.abs_diff(b) as f64 / a.max(b).max(1) as f64;
        self.count += 1;
        Ok(())
    }

    fn mean_overlap(&self) -> f64 {
        self.overlap_sum / self.count.max(1) as f64
    }

    fn mean_comparison_delta(&self) -> f64 {
        self.comparison_delta_sum / self.count.max(1) as f64
    }
}

fn percentile(values: &mut [f64], quantile: f64) -> f64 {
    values.sort_by(f64::total_cmp);
    let index = ((values.len().saturating_sub(1)) as f64 * quantile).ceil() as usize;
    values[index.min(values.len().saturating_sub(1))]
}

fn mean(runs: &[QueryRun], field: impl Fn(&QueryRun) -> f64) -> f64 {
    runs.iter().map(field).sum::<f64>() / runs.len().max(1) as f64
}

fn summary_row(
    batch: &BatchRun,
    width: usize,
    beam: usize,
    files: &IndexFiles,
    meta: &IndexMeta,
) -> Result<Value> {
    let mut latencies = batch
        .runs
        .iter()
        .map(|run| run.latency_us)
        .collect::<Vec<_>>();
    let io_requests = mean(&batch.runs, |run| run.stats.total_io_operations as f64);
    Ok(json!({
        "config_id": format!("beam{beam}"),
        "search_param": width,
        "search_width": width,
        "ablation": "",
        "beam_width": beam,
        "recall": mean(&batch.runs, |run| run.recall),
        "qps": batch.runs.len() as f64 / batch.wall_seconds.max(1e-12),
        "latency_mean_us": mean(&batch.runs, |run| run.latency_us),
        "latency_p50_us": percentile(&mut latencies, 0.50),
        "latency_p95_us": percentile(&mut latencies, 0.95),
        "latency_p99_us": percentile(&mut latencies, 0.99),
        "index_size_mb": files.index_bytes()? as f64 / (1024.0 * 1024.0),
        "resident_bytes": meta.resident_bytes,
        "peak_rss_bytes": batch.peak_rss_bytes,
        "io_requests_per_query": io_requests,
        "sectors_4k_per_query": io_requests,
        "bytes_read_per_query": io_requests * PAGE_SIZE as f64,
        "io_wait_us": mean(&batch.runs, |run| run.stats.io_time_us as f64),
        "distance_compute_us": mean(&batch.runs, |run| run.stats.cpu_time_us as f64),
        "query_prep_us": mean(&batch.runs, |run| run.stats.query_pq_preprocess_time_us as f64),
        "queue_compute_us": 0.0,
        "rerank_us": 0.0,
        "visited_nodes": mean(&batch.runs, |run| run.stats.total_comparisons as f64),
        "distance_evaluations": mean(&batch.runs, |run| run.stats.total_comparisons as f64),
        "query_count": batch.runs.len(),
    }))
}

fn parse_positive_list(value: &str, flag: &str) -> Result<Vec<usize>> {
    let values = value
        .split(',')
        .map(|part| {
            part.parse::<usize>()
                .with_context(|| format!("invalid {flag}: {part}"))
        })
        .collect::<Result<Vec<_>>>()?;
    if values.is_empty() || values.iter().any(|&value| value == 0) {
        bail!("{flag} must contain positive integers");
    }
    Ok(values)
}

fn formal_widths(args: &Args) -> Result<Vec<usize>> {
    if std::env::var("QG05_FAST").ok().as_deref() == Some("1") {
        if let Ok(value) = std::env::var("QG05_FAST_WIDTHS") {
            return parse_positive_list(&value, "QG05_FAST_WIDTHS");
        }
        let width = std::env::var("QG05_FAST_WIDTH")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(70);
        if width == 0 {
            bail!("QG05_FAST_WIDTH must be positive");
        }
        return Ok(vec![width]);
    }
    if let Some(value) = args.optional("--integration-widths") {
        if !args.text("--run-id")?.starts_with("native_integration_") {
            bail!("--integration-widths is restricted to native integration runs");
        }
        return parse_positive_list(value, "--integration-widths");
    }
    let mut widths = (1..=30).collect::<Vec<_>>();
    widths.extend((40..=100).step_by(10));
    widths.extend((140..=580).step_by(40));
    Ok(widths)
}

fn validation_beams(args: &Args) -> Result<Vec<usize>> {
    if std::env::var("QG05_FAST").ok().as_deref() == Some("1") {
        return Ok(vec![1]);
    }
    if let Some(value) = args.optional("--integration-beams") {
        if !args.text("--run-id")?.starts_with("native_integration_") {
            bail!("--integration-beams is restricted to native integration runs");
        }
        return parse_positive_list(value, "--integration-beams");
    }
    Ok(vec![1, 2, 4, 8, 16, 32])
}

fn selected_test_beam(args: &Args) -> Result<usize> {
    if args.text("--run-id")?.starts_with("native_integration_") {
        return Ok(validation_beams(args)?[0]);
    }
    let lock: Value =
        serde_json::from_reader(BufReader::new(File::open(args.path("--tuning-lock")?)?))?;
    let key = format!("{METHOD}::hybrid_disk");
    let config = lock["selected"][&key]["config_id"]
        .as_str()
        .ok_or_else(|| anyhow!("tuning lock has no selection for {key}"))?;
    config
        .strip_prefix("beam")
        .ok_or_else(|| anyhow!("unsupported DiskANN config_id: {config}"))?
        .parse()
        .context("invalid DiskANN beam in tuning lock")
}

fn cache_plan(args: &Args, meta: &IndexMeta) -> Result<(usize, usize, usize)> {
    let workers: usize = args.number("--workers")?;
    let worker_scratch_bytes = workers * 8 * 1024 * 1024;
    let budget =
        (args.number::<f64>("--search-dram-budget-gib")? * (1_u64 << 30) as f64).floor() as usize;
    let fixed = meta
        .resident_bytes
        .checked_add(meta.codebook_bytes)
        .and_then(|value| value.checked_add(worker_scratch_bytes))
        .ok_or_else(|| anyhow!("DRAM accounting overflow"))?;
    if fixed > budget {
        bail!("resident PQ and worker scratch require {fixed} bytes, budget is {budget}");
    }
    let cache_nodes = match args.text("--cache-mode")? {
        "c0" => 0,
        "standard" => ((budget - fixed) / meta.cached_node_bytes).min(meta.base_count / 10),
        other => bail!("unsupported cache mode {other}"),
    };
    let cache_bytes = cache_nodes * meta.cached_node_bytes;
    Ok((cache_nodes, cache_bytes, worker_scratch_bytes))
}

fn caching_strategy(cache_nodes: usize) -> CachingStrategy {
    if cache_nodes == 0 {
        CachingStrategy::None
    } else {
        CachingStrategy::StaticCacheWithBfsNodes(cache_nodes)
    }
}

fn peak_rss_bytes() -> u64 {
    fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|text| {
            text.lines().find_map(|line| {
                line.strip_prefix("VmHWM:")?
                    .split_whitespace()
                    .next()?
                    .parse::<u64>()
                    .ok()
                    .map(|kb| kb * 1024)
            })
        })
        .unwrap_or(1)
}

fn reset_peak_rss() -> Result<()> {
    fs::write("/proc/self/clear_refs", b"5\n")
        .context("resetting VmHWM through /proc/self/clear_refs")
}

fn status_value(key: &str) -> String {
    fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|text| {
            text.lines()
                .find_map(|line| line.strip_prefix(key).map(str::trim).map(str::to_owned))
        })
        .unwrap_or_else(|| "unknown".to_string())
}

fn rustc_version() -> String {
    Command::new("rustc")
        .arg("--version")
        .output()
        .ok()
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|value| value.trim().to_string())
        .unwrap_or_else(|| "rustc-unknown".to_string())
}

fn write_trace_rows(
    output: &mut BufWriter<File>,
    args: &Args,
    batch: &BatchRun,
    width: usize,
    beam: usize,
    meta: &IndexMeta,
    cache_nodes: usize,
    cache_bytes: usize,
) -> Result<()> {
    for run in &batch.runs {
        let io_requests = run.stats.total_io_operations as u64;
        let bytes_read = io_requests * PAGE_SIZE as u64;
        let row = json!({
            "layer": LAYER,
            "storage_mode": "hybrid_disk",
            "cache_mode": args.text("--cache-mode")?,
            "dataset": args.text("--dataset")?,
            "method": METHOD,
            "config_id": format!("beam{beam}"),
            "repeat_id": args.number::<usize>("--repeat-id")?,
            "query_id": run.query_id,
            "search_width": width,
            "beam_width": beam,
            "workers": args.number::<usize>("--workers")?,
            "search_dram_budget_gib": args.number::<f64>("--search-dram-budget-gib")?,
            "cache_nodes": cache_nodes,
            "resident_bytes": meta.resident_bytes,
            "cache_bytes": cache_bytes,
            "peak_rss_bytes": batch.peak_rss_bytes,
            "recall_at_10": run.recall,
            "latency_us": run.latency_us,
            "query_prep_us": run.stats.query_pq_preprocess_time_us,
            "queue_compute_us": 0,
            "io_wait_us": run.stats.io_time_us,
            "distance_compute_us": run.stats.cpu_time_us,
            "rerank_us": 0,
            "visited_nodes": run.stats.total_comparisons,
            "distance_evaluations": run.stats.total_comparisons,
            "io_requests": io_requests,
            "sectors_4k": io_requests,
            "bytes_read": bytes_read,
            "average_read_bytes": if io_requests == 0 { 0 } else { PAGE_SIZE },
            "coalesced_requests": 0,
            "duplicate_pages_removed": 0,
            "shared_cache_hits": run.stats.total_vertices_loaded.saturating_sub(run.stats.total_io_operations),
            "shared_cache_misses": run.stats.total_io_operations,
            "query_cache_hits": 0,
            "query_cache_misses": 0,
        });
        serde_json::to_writer(&mut *output, &row)?;
        output.write_all(b"\n")?;
    }
    Ok(())
}

fn write_export_artifact(args: &Args, files: &IndexFiles, meta: &IndexMeta) -> Result<()> {
    write_json(
        &args.path("--result-json")?,
        &json!({
            "schema_version": 2,
            "status": "done",
            "layer": LAYER,
            "dataset": args.text("--dataset")?,
            "method": METHOD,
            "storage_mode": "hybrid_disk",
            "cache_mode": args.text("--cache-mode")?,
            "phase": "export",
            "run_id": args.text("--run-id")?,
            "repeat_id": args.number::<usize>("--repeat-id")?,
            "workers": args.number::<usize>("--workers")?,
            "search_dram_budget_gib": args.number::<f64>("--search-dram-budget-gib")?,
            "source_suite": SOURCE_SUITE,
            "source_kernel": SOURCE_KERNEL,
            "port_kind": PORT_KIND,
            "implementation_fingerprint": args.text("--implementation-fingerprint")?,
            "native_binary_sha256": args.text("--native-binary-sha256")?,
            "input_manifest_sha256": args.text("--input-manifest-sha256")?,
            "source_index_manifest_sha256": sha256(&files.manifest)?,
            "query_split_sha256": args.text("--query-split-sha256")?,
            "query_order_sha256": args.text("--query-order-sha256")?,
            "query_order_seed": args.number::<u64>("--query-order-seed")?,
            "warmup_queries": args.number::<usize>("--warmup-queries")?,
            "index_path": files.root.to_string_lossy(),
            "index_size_mb": files.index_bytes()? as f64 / (1024.0 * 1024.0),
            "base_count": meta.base_count,
            "dimension": meta.dimension,
            "resident_bytes": meta.resident_bytes,
            "codebook_bytes": meta.codebook_bytes,
            "worker_scratch_bytes": 0,
            "cache_bytes": 0,
            "cache_nodes": 0,
            "peak_rss_bytes": 0,
            "whole_graph_in_memory": false,
            "whole_payload_in_memory": false,
            "direct_io": true,
            "native_aio": true,
            "io_backend": "linux_io_uring_odirect",
            "page_size": PAGE_SIZE,
            "formal_ready": true,
            "implementation_parity": "not_run_export",
            "summary_rows": [],
        }),
    )
}

fn run_search(args: &Args, files: &IndexFiles, meta: &IndexMeta) -> Result<()> {
    if sha256(&args.path("--query-order")?)? != args.text("--query-order-sha256")? {
        bail!("query-order SHA-256 mismatch");
    }
    let queries = read_fvecs(&args.path("--query")?)?;
    let groundtruth = read_ivecs_top10(&args.path("--groundtruth")?)?;
    if queries.len() != groundtruth.len() || queries.first().map(Vec::len) != Some(meta.dimension) {
        bail!("query, ground truth and index dimensions do not match");
    }
    let order = read_query_order(&args.path("--query-order")?, queries.len())?;
    let (cache_nodes, cache_bytes, worker_scratch_bytes) = cache_plan(args, meta)?;
    let index_reader = DiskIndexReader::new(
        files.pq_pivots.to_string_lossy().into_owned(),
        files.pq_codes.to_string_lossy().into_owned(),
        &FileStorageProvider,
    )?;
    let widths = formal_widths(args)?;
    let beams = if matches!(args.text("--phase")?, "test") {
        vec![selected_test_beam(args)?]
    } else {
        validation_beams(args)?
    };
    let trace_path = args.path("--query-trace")?;
    if let Some(parent) = trace_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut trace = BufWriter::new(File::create(&trace_path)?);
    let mut summaries = Vec::new();
    let mut parity = Parity::default();
    let mut measured_peak = 0_u64;
    let workers: usize = args.number("--workers")?;
    let warmup: usize = args.number("--warmup-queries")?;

    for &beam in &beams {
        for &width in &widths {
            eprintln!("official DiskANN disk search: beam={beam} L={width}");
            let direct_factory = DiskVertexProviderFactory::<
                AdHoc<f32>,
                diskann_disk::search::provider::aligned_file_reader::AlignedFileReaderFactory,
            >::from_disk_index_path(
                files.disk_index.to_string_lossy().into_owned(),
                caching_strategy(cache_nodes),
            )?;
            let direct_searcher = DiskIndexSearcher::<AdHoc<f32>, _>::new(
                workers,
                128,
                &index_reader,
                direct_factory,
                Metric::L2,
                None,
            )?;
            for &query_id in order.iter().take(warmup.min(order.len())) {
                direct_searcher.search(
                    &queries[query_id],
                    K,
                    width as u32,
                    Some(beam),
                    SearchMode::graph(),
                )?;
            }
            reset_peak_rss()?;
            let direct = execute_batch(
                &direct_searcher,
                &queries,
                &groundtruth,
                &order,
                workers,
                width,
                beam,
                0,
            )?;
            measured_peak = measured_peak.max(direct.peak_rss_bytes);

            let memory_factory = DiskVertexProviderFactory::<AdHoc<f32>, MemoryReaderFactory>::new(
                MemoryReaderFactory {
                    bytes: Arc::new(fs::read(&files.disk_index)?),
                },
                caching_strategy(cache_nodes),
            )?;
            let memory_searcher = DiskIndexSearcher::<AdHoc<f32>, _>::new(
                workers,
                128,
                &index_reader,
                memory_factory,
                Metric::L2,
                None,
            )?;
            let memory = execute_batch(
                &memory_searcher,
                &queries,
                &groundtruth,
                &order,
                workers,
                width,
                beam,
                0,
            )?;
            for (direct_query, memory_query) in direct.runs.iter().zip(&memory.runs) {
                parity.add(direct_query, memory_query)?;
            }
            write_trace_rows(
                &mut trace,
                args,
                &direct,
                width,
                beam,
                meta,
                cache_nodes,
                cache_bytes,
            )?;
            summaries.push(summary_row(&direct, width, beam, files, meta)?);
        }
    }
    trace.flush()?;
    if parity.max_recall_delta > 1e-3
        || parity.mean_overlap() < 0.99
        || parity.mean_comparison_delta() > 0.01
    {
        bail!(
            "official DiskANN memory/direct parity failed: recall_delta={:.6}, overlap={:.6}, comparison_delta={:.6}",
            parity.max_recall_delta,
            parity.mean_overlap(),
            parity.mean_comparison_delta()
        );
    }
    let parity_path = args.path("--result-json")?.with_extension("parity.json");
    write_json(
        &parity_path,
        &json!({
            "schema_version": 1,
            "method": METHOD,
            "memory_backend": "official DiskANN StorageProviderAlignedFileReader-equivalent over identical exported disk-index bytes",
            "direct_backend": "official LinuxAlignedFileReader(O_DIRECT+io_uring)",
            "max_recall_delta": parity.max_recall_delta,
            "mean_top10_overlap": parity.mean_overlap(),
            "mean_visited_count_relative_delta": parity.mean_comparison_delta(),
            "mean_distance_count_relative_delta": parity.mean_comparison_delta(),
            "query_comparisons": parity.count,
        }),
    )?;
    let phase = match args.text("--phase")? {
        "validate" => "validate",
        "validation" => "validation",
        "test" => "test",
        other => bail!("unsupported measured phase {other}"),
    };
    write_json(
        &args.path("--result-json")?,
        &json!({
            "schema_version": 2,
            "status": "done",
            "layer": LAYER,
            "dataset": args.text("--dataset")?,
            "method": METHOD,
            "storage_mode": "hybrid_disk",
            "cache_mode": args.text("--cache-mode")?,
            "phase": phase,
            "run_id": args.text("--run-id")?,
            "repeat_id": args.number::<usize>("--repeat-id")?,
            "workers": workers,
            "search_dram_budget_gib": args.number::<f64>("--search-dram-budget-gib")?,
            "source_suite": SOURCE_SUITE,
            "source_kernel": SOURCE_KERNEL,
            "port_kind": PORT_KIND,
            "implementation_fingerprint": args.text("--implementation-fingerprint")?,
            "native_binary_sha256": args.text("--native-binary-sha256")?,
            "git_commit": args.text("--git-commit")?,
            "compiler": rustc_version(),
            "simd": "official DiskANN FixedChunkPQTable; architecture-selected SIMD",
            "base_count": meta.base_count,
            "dimension": meta.dimension,
            "resident_bytes": meta.resident_bytes,
            "codebook_bytes": meta.codebook_bytes,
            "worker_scratch_bytes": worker_scratch_bytes,
            "cache_bytes": cache_bytes,
            "cache_nodes": cache_nodes,
            "peak_rss_bytes": measured_peak,
            "cpu_affinity": status_value("Cpus_allowed_list:"),
            "numa_node": fs::read_to_string("/sys/devices/system/node/online").unwrap_or_else(|_| "unknown".into()).trim(),
            "input_manifest_sha256": args.text("--input-manifest-sha256")?,
            "source_index_manifest_sha256": sha256(&files.manifest)?,
            "query_split_sha256": args.text("--query-split-sha256")?,
            "query_order_sha256": args.text("--query-order-sha256")?,
            "query_order_seed": args.number::<u64>("--query-order-seed")?,
            "warmup_queries": warmup,
            "index_path": files.root.to_string_lossy(),
            "index_size_mb": files.index_bytes()? as f64 / (1024.0 * 1024.0),
            "whole_graph_in_memory": false,
            "whole_payload_in_memory": false,
            "direct_io": true,
            "native_aio": true,
            "io_backend": "linux_io_uring_odirect",
            "page_size": PAGE_SIZE,
            "formal_ready": true,
            "implementation_parity": "passed",
            "parity": {
                "reference_artifact_sha256": sha256(&parity_path)?,
                "max_recall_delta": parity.max_recall_delta,
                "mean_top10_overlap": parity.mean_overlap(),
                "mean_visited_count_relative_delta": parity.mean_comparison_delta(),
                "mean_distance_count_relative_delta": parity.mean_comparison_delta(),
            },
            "query_trace_path": trace_path.to_string_lossy(),
            "query_trace_sha256": sha256(&trace_path)?,
            "measurement_notes": {
                "visited_nodes": "official DiskANN does not expose visited-set cardinality; total_comparisons is the stable graph-work proxy",
                "distance_compute_us": "official cpu_time_us includes traversal and full-precision rerank CPU work",
                "bytes_read": "official total_io_operations counts requested disk vertices; one 4-KiB sector per vertex in this layout",
            },
            "summary_rows": summaries,
        }),
    )
}

fn run() -> Result<()> {
    let args = Args::parse()?;
    args.validate()?;
    let files = IndexFiles::new(args.path("--disk-index-dir")?)?;
    match args.text("--phase")? {
        "export" => {
            let meta = export_index(&args, &files)?;
            write_export_artifact(&args, &files, &meta)
        }
        "validate" | "validation" | "test" => {
            if !files.complete() {
                bail!(
                    "official DiskANN index is incomplete under {}; run export first",
                    files.root.display()
                );
            }
            let meta = IndexMeta::from_path(&files.metadata)?;
            run_search(&args, &files, &meta)
        }
        phase => bail!("unsupported phase {phase}"),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("ERROR: {error:#}");
        std::process::exit(2);
    }
}
