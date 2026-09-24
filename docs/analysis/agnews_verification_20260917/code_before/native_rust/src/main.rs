use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use diskann::graph::search::Knn;
use diskann::graph::IdDistance;
use diskann::provider::DefaultContext;
use diskann_fair::disk_port::baseline_disk::{
    self, BaselineCodec, BaselineKind, BaselinePayloadFactory, BaselinePayloadLayout,
};
use diskann_fair::disk_port::ours_port::{
    search_ours_disk_graph, OursAblation, OursDiskStats, OursIndexBuilder, OursPayloadFactory,
    OursResidentCodec,
};
use diskann_fair::disk_port::{
    export_shared_graph, new_search_index, select_bfs_cache_pages, BackendFactory,
    DirectGraphReader, DiskStrategy, GraphLayout, IoStats, PqCodec, ResidentCodec, SaqCodec,
    SharedPageCache, SqCodec, PAGE_SIZE,
};
use diskann_fair::diskann_runner::{percentile, read_fvecs_matrix, read_ivecs_topk, recall_hits};
use diskann_fair::ours_diskann::RabitqSpace;
use diskann_providers::index::diskann_async;
use diskann_providers::utils::create_thread_pool;
use diskann_quantization::CompressInto;
use diskann_quantization::scalar::train::ScalarQuantizationParameters;
use diskann_quantization::{
    algorithms::transforms::{TargetDim, TransformKind},
    alloc::GlobalAllocator,
    spherical,
};
use diskann_utils::views::Matrix;
use diskann_vector::distance::Metric;
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

type Result<T> = std::result::Result<T, String>;

const PORT_KIND: &str = "algorithm_preserving_disk_port";
const PQ_METHOD: &str = "PQ-DiskANN-Disk";
const SQ_METHOD: &str = "SQ-DiskANN-Disk";
const SAQ_METHOD: &str = "SAQ-DiskANN-Disk";
const OURS_METHOD: &str = "Ours-Disk";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CodecKind {
    Pq,
    Sq,
    Saq,
    Ours,
}

impl CodecKind {
    fn from_method(method: &str) -> Result<Self> {
        match method {
            PQ_METHOD => Ok(Self::Pq),
            SQ_METHOD => Ok(Self::Sq),
            SAQ_METHOD => Ok(Self::Saq),
            OURS_METHOD => Ok(Self::Ours),
            other => Err(format!(
                "unknown native 05B method: {other}; ready: {PQ_METHOD}, {SQ_METHOD}, {SAQ_METHOD}, {OURS_METHOD}"
            )),
        }
    }

    fn method(self) -> &'static str {
        match self {
            Self::Pq => PQ_METHOD,
            Self::Sq => SQ_METHOD,
            Self::Saq => SAQ_METHOD,
            Self::Ours => OURS_METHOD,
        }
    }

    fn source_kernel(self) -> &'static str {
        match self {
            Self::Pq => "DiskANN FixedChunkPQTable 4-bit",
            Self::Sq => "DiskANN ScalarQuantizer<4>",
            Self::Saq => "DiskANN spherical::Impl<4>",
            Self::Ours => "ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search",
        }
    }

    fn simd_description(self) -> String {
        let cpuinfo = fs::read_to_string("/proc/cpuinfo").unwrap_or_default();
        let architecture = if cpuinfo.contains("avx512f") {
            "AVX-512 available"
        } else if cpuinfo.contains(" avx2") {
            "AVX2 available"
        } else {
            "native SIMD"
        };
        format!(
            "{architecture}; DiskANN current-architecture {} kernel",
            self.method()
        )
    }
}

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
                return Err(format!("unexpected positional argument: {flag}"));
            }
            let value = iter
                .next()
                .ok_or_else(|| format!("missing value for {flag}"))?;
            if value.starts_with("--") {
                return Err(format!("missing value for {flag}; got flag {value}"));
            }
            if values.insert(flag.clone(), value).is_some() {
                return Err(format!("duplicate argument: {flag}"));
            }
        }
        Ok(Self { values })
    }

    fn text(&self, flag: &str) -> Result<&str> {
        self.values
            .get(flag)
            .map(String::as_str)
            .ok_or_else(|| format!("missing required argument: {flag}"))
    }

    fn path(&self, flag: &str) -> Result<PathBuf> {
        Ok(PathBuf::from(self.text(flag)?))
    }

    fn number<T: std::str::FromStr>(&self, flag: &str) -> Result<T> {
        self.text(flag)?.parse().map_err(|_| {
            format!(
                "invalid numeric value for {flag}: {}",
                self.text(flag).unwrap()
            )
        })
    }

    fn optional(&self, flag: &str) -> Option<&str> {
        self.values.get(flag).map(String::as_str)
    }

    fn validate_contract(&self) -> Result<()> {
        if self.text("--contract-version")? != "2" {
            return Err("only contract version 2 is supported".into());
        }
        let layer = self.text("--layer")?;
        let kind = CodecKind::from_method(self.text("--method")?)?;
        if layer != "05b" && !(layer == "05c" && kind == CodecKind::Ours) {
            return Err("this binary implements 05B PQ/SQ/SAQ/Ours and 05C Ours only".into());
        }
        let storage_mode = self.text("--storage-mode")?;
        let disk_payload_ok = kind != CodecKind::Ours && storage_mode == "disk_payload";
        if storage_mode != "hybrid_disk" && !disk_payload_ok {
            return Err(
                "05B native codecs require storage-mode=hybrid_disk (disk_payload is available for PQ/SQ/SAQ)"
                    .into(),
            );
        }
        if self.number::<usize>("--page-size")? != PAGE_SIZE {
            return Err(format!("page size must be {PAGE_SIZE}"));
        }
        if self.text("--direct-io")? != "required" || self.text("--native-aio")? != "required" {
            return Err("formal 05B requires O_DIRECT and native AIO".into());
        }
        if layer == "05b" && kind == CodecKind::Ours {
            let requested = self.text("--ablations")?;
            let formal = "full4-resident/no-gate,db1-resident/full4-on-ssd,db1+coalescing,db1+coalescing+reuse";
            if requested != formal && std::env::var("QG05_FAST").ok().as_deref() != Some("1") {
                return Err(
                    "Ours 05B requires the exact four registered ablations outside QG05_FAST"
                        .into(),
                );
            }
            for item in requested.split(',').filter(|item| !item.is_empty()) {
                if OursAblation::parse(item).is_none() {
                    return Err(format!("unknown Ours 05B ablation: {item}"));
                }
            }
        }
        Ok(())
    }
}

fn source_suite(args: &Args) -> Result<&'static str> {
    match args.text("--layer")? {
        "05b" => Ok("02_diskann_fair"),
        "05c" => Ok("03_system_fair"),
        other => Err(format!("unsupported layer {other}")),
    }
}

#[derive(Clone)]
struct QueryRun {
    query_id: usize,
    ids: Vec<u32>,
    recall: f64,
    latency_us: f64,
    stats: IoStats,
    ablation: &'static str,
    queue_compute_us: f64,
    rerank_us: f64,
    db1_checks: u64,
    db1_survivors: u64,
    full4_candidates: u64,
    full4_page_reads: u64,
    rerank_candidates: u64,
    rerank_page_reads: u64,
}

struct BatchRun {
    runs: Vec<QueryRun>,
    wall_seconds: f64,
    peak_rss_bytes: u64,
}

#[derive(Default)]
struct ParityTotals {
    max_recall_delta: f64,
    overlap_sum: f64,
    visited_relative_sum: f64,
    distance_relative_sum: f64,
    count: usize,
}

impl ParityTotals {
    fn add(&mut self, direct: &QueryRun, memory: &QueryRun) -> Result<()> {
        if direct.query_id != memory.query_id {
            return Err("parity query order changed between backends".into());
        }
        self.max_recall_delta = self
            .max_recall_delta
            .max((direct.recall - memory.recall).abs());
        let overlap = direct
            .ids
            .iter()
            .filter(|id| memory.ids.contains(id))
            .count() as f64
            / direct.ids.len().max(1) as f64;
        self.overlap_sum += overlap;
        self.visited_relative_sum +=
            relative_delta(direct.stats.visited_nodes, memory.stats.visited_nodes);
        self.distance_relative_sum += relative_delta(
            direct.stats.distance_evaluations,
            memory.stats.distance_evaluations,
        );
        self.count += 1;
        Ok(())
    }

    fn mean_overlap(&self) -> f64 {
        self.overlap_sum / self.count.max(1) as f64
    }

    fn mean_visited_delta(&self) -> f64 {
        self.visited_relative_sum / self.count.max(1) as f64
    }

    fn mean_distance_delta(&self) -> f64 {
        self.distance_relative_sum / self.count.max(1) as f64
    }
}

fn relative_delta(a: u64, b: u64) -> f64 {
    (a.abs_diff(b) as f64) / a.max(b).max(1) as f64
}

#[derive(Debug)]
struct IndexFiles {
    root: PathBuf,
    graph_pages: PathBuf,
    pivots: PathBuf,
    codes: PathBuf,
    pq_payload_pages: PathBuf,
    pq_codebook: PathBuf,
    sq_prefix: PathBuf,
    sq_codes: PathBuf,
    sq_quantizer: PathBuf,
    sq_payload_pages: PathBuf,
    sq_codebook: PathBuf,
    saq_metadata: PathBuf,
    saq_codes: PathBuf,
    saq_payload_pages: PathBuf,
    ours_metadata: PathBuf,
    ours_sidecar: PathBuf,
    ours_payload: PathBuf,
    metadata: PathBuf,
    resident_marker: PathBuf,
}

impl IndexFiles {
    fn new(root: PathBuf) -> Self {
        Self {
            graph_pages: root.join("shared_graph.pages"),
            pivots: root.join("pq_pivots.bin"),
            codes: root.join("pq_codes.bin"),
            pq_payload_pages: root.join("pq_payload.pages"),
            pq_codebook: root.join("pq_codebook.bin"),
            sq_prefix: root.join("sq"),
            sq_codes: root.join("sq_sq_compressed.bin"),
            sq_quantizer: root.join("sq_scalar_quantizer_proto.bin"),
            sq_payload_pages: root.join("sq_payload.pages"),
            sq_codebook: root.join("sq_codebook.bin"),
            saq_metadata: root.join("saq_quantizer.bin"),
            saq_codes: root.join("saq_codes.bin"),
            saq_payload_pages: root.join("saq_payload.pages"),
            ours_metadata: root.join("ours_quantizer.bin"),
            ours_sidecar: root.join("ours_db1_sidecar.bin"),
            ours_payload: root.join("ours_full4_residual.pages"),
            metadata: root.join("index.meta"),
            resident_marker: root.join("resident_codes.marker"),
            root,
        }
    }

    fn complete(&self, kind: CodecKind, storage_mode: &str) -> bool {
        let payload_required = storage_mode == "disk_payload";
        let codec_files_exist = match kind {
            CodecKind::Pq => {
                self.pivots.exists()
                    && self.codes.exists()
                    && (!payload_required
                        || (self.pq_payload_pages.exists() && self.pq_codebook.exists()))
            }
            CodecKind::Sq => {
                self.sq_codes.exists()
                    && self.sq_quantizer.exists()
                    && (!payload_required
                        || (self.sq_payload_pages.exists() && self.sq_codebook.exists()))
            }
            CodecKind::Saq => {
                self.saq_metadata.exists()
                    && self.saq_codes.exists()
                    && (!payload_required || self.saq_payload_pages.exists())
            }
            CodecKind::Ours => {
                self.ours_metadata.exists()
                    && self.ours_sidecar.exists()
                    && self.ours_payload.exists()
            }
        };
        self.graph_pages.exists()
            && codec_files_exist
            && self.metadata.exists()
            && self.resident_marker.exists()
    }

    fn measured_paths(&self, kind: CodecKind) -> Vec<&Path> {
        let mut paths = vec![
            self.graph_pages.as_path(),
            self.metadata.as_path(),
            self.resident_marker.as_path(),
        ];
        match kind {
            CodecKind::Pq => paths.extend([
                self.pivots.as_path(),
                self.codes.as_path(),
                self.pq_payload_pages.as_path(),
                self.pq_codebook.as_path(),
            ]),
            CodecKind::Sq => paths.extend([
                self.sq_codes.as_path(),
                self.sq_quantizer.as_path(),
                self.sq_payload_pages.as_path(),
                self.sq_codebook.as_path(),
            ]),
            CodecKind::Saq => paths.extend([
                self.saq_metadata.as_path(),
                self.saq_codes.as_path(),
                self.saq_payload_pages.as_path(),
            ]),
            CodecKind::Ours => paths.extend([
                self.ours_metadata.as_path(),
                self.ours_sidecar.as_path(),
                self.ours_payload.as_path(),
            ]),
        }
        paths
    }
}

#[derive(Debug, Clone)]
struct IndexMeta {
    kind: CodecKind,
    layout: GraphLayout,
    base_count: usize,
    dimension: usize,
    source_start_index: usize,
    source_graph_sha256: String,
    graph_role: String,
    resident_bytes: usize,
    codebook_bytes: usize,
    export_time_ms: f64,
    ours_compact_record_bytes: usize,
    ours_residual_record_bytes: usize,
    ours_record_count: usize,
}

fn write_meta(path: &Path, meta: &IndexMeta) -> Result<()> {
    let text = format!(
        concat!(
            "schema_version=1\nmethod={}\nbase_count={}\ndimension={}\n",
            "source_start_index={}\nsource_graph_sha256={}\ngraph_role={}\nnode_count={}\n",
            "max_degree={}\nstart_point={}\nadditional_points={}\nrecord_bytes={}\n",
            "nodes_per_page={}\npage_count={}\nresident_bytes={}\ncodebook_bytes={}\n",
            "export_time_ms={:.6}\nours_compact_record_bytes={}\n",
            "ours_residual_record_bytes={}\nours_record_count={}\n"
        ),
        meta.kind.method(),
        meta.base_count,
        meta.dimension,
        meta.source_start_index,
        meta.source_graph_sha256,
        meta.graph_role,
        meta.layout.node_count,
        meta.layout.max_degree,
        meta.layout.start_point,
        meta.layout.additional_points,
        meta.layout.record_bytes,
        meta.layout.nodes_per_page,
        meta.layout.page_count,
        meta.resident_bytes,
        meta.codebook_bytes,
        meta.export_time_ms,
        meta.ours_compact_record_bytes,
        meta.ours_residual_record_bytes,
        meta.ours_record_count,
    );
    fs::write(path, text).map_err(err)
}

fn parse_key_values(path: &Path) -> Result<BTreeMap<String, String>> {
    let text = fs::read_to_string(path).map_err(err)?;
    let mut values = BTreeMap::new();
    for line in text.lines() {
        if let Some((key, value)) = line.split_once('=') {
            values.insert(key.to_string(), value.to_string());
        }
    }
    Ok(values)
}

fn map_number<T: std::str::FromStr>(map: &BTreeMap<String, String>, key: &str) -> Result<T> {
    map.get(key)
        .ok_or_else(|| format!("metadata is missing {key}"))?
        .parse()
        .map_err(|_| format!("metadata has invalid {key}"))
}

fn load_meta(path: &Path) -> Result<IndexMeta> {
    let map = parse_key_values(path)?;
    let kind = CodecKind::from_method(
        map.get("method")
            .ok_or_else(|| format!("{} is missing method", path.display()))?,
    )?;
    Ok(IndexMeta {
        kind,
        layout: GraphLayout {
            node_count: map_number(&map, "node_count")?,
            max_degree: map_number(&map, "max_degree")?,
            start_point: map_number(&map, "start_point")?,
            additional_points: map_number(&map, "additional_points")?,
            record_bytes: map_number(&map, "record_bytes")?,
            nodes_per_page: map_number(&map, "nodes_per_page")?,
            page_count: map_number(&map, "page_count")?,
        },
        base_count: map_number(&map, "base_count")?,
        dimension: map_number(&map, "dimension")?,
        source_start_index: map_number(&map, "source_start_index")?,
        source_graph_sha256: map
            .get("source_graph_sha256")
            .ok_or_else(|| "metadata is missing source_graph_sha256".to_string())?
            .clone(),
        graph_role: map
            .get("graph_role")
            .ok_or_else(|| "metadata is missing graph_role".to_string())?
            .clone(),
        resident_bytes: map_number(&map, "resident_bytes")?,
        codebook_bytes: map_number(&map, "codebook_bytes")?,
        export_time_ms: map_number(&map, "export_time_ms")?,
        ours_compact_record_bytes: map
            .get("ours_compact_record_bytes")
            .and_then(|v| v.parse().ok())
            .unwrap_or(0),
        ours_residual_record_bytes: map
            .get("ours_residual_record_bytes")
            .and_then(|v| v.parse().ok())
            .unwrap_or(0),
        ours_record_count: map
            .get("ours_record_count")
            .and_then(|v| v.parse().ok())
            .unwrap_or(0),
    })
}

fn sha256(path: &Path) -> Result<String> {
    let output = Command::new("sha256sum").arg(path).output().map_err(err)?;
    if !output.status.success() {
        return Err(format!("sha256sum failed for {}", path.display()));
    }
    String::from_utf8(output.stdout)
        .map_err(err)?
        .split_whitespace()
        .next()
        .map(str::to_string)
        .ok_or_else(|| format!("sha256sum returned no digest for {}", path.display()))
}

fn companion_graph_meta(graph: &Path) -> PathBuf {
    graph.with_extension("json")
}

/// Exact K=1 center training used by experiment 02: sample without
/// replacement with seed 100, sort sampled row IDs, then accumulate f32.
fn train_ours_k1_center(data: &Matrix<f32>) -> Result<Vec<f32>> {
    let sample_count = data.nrows().min(100_000);
    if sample_count == 0 {
        return Err("Ours center training received an empty base matrix".into());
    }
    let mut ids = (0..data.nrows()).collect::<Vec<_>>();
    let mut rng = StdRng::seed_from_u64(100);
    for index in 0..sample_count {
        let picked = rng.random_range(index..data.nrows());
        ids.swap(index, picked);
    }
    ids.truncate(sample_count);
    ids.sort_unstable();
    let mut center = vec![0.0_f32; data.ncols()];
    for id in ids {
        for (output, input) in center.iter_mut().zip(data.row(id)) {
            *output += *input;
        }
    }
    let inverse = 1.0_f32 / sample_count as f32;
    for value in &mut center {
        *value *= inverse;
    }
    Ok(center)
}

fn export_index(args: &Args, files: &IndexFiles) -> Result<(IndexMeta, u64)> {
    let kind = CodecKind::from_method(args.text("--method")?)?;
    let (source_graph, expected_graph_hash, graph_role) = if kind == CodecKind::Ours {
        (
            args.path("--ours-graph")?,
            args.text("--ours-graph-sha256")?,
            "ours_native",
        )
    } else {
        (
            args.path("--shared-graph")?,
            args.text("--shared-graph-sha256")?,
            "shared_baseline",
        )
    };
    let actual_graph_hash = sha256(&source_graph)?;
    if actual_graph_hash != expected_graph_hash {
        return Err(format!(
            "source graph SHA-256 mismatch: expected {expected_graph_hash}, got {actual_graph_hash}"
        ));
    }
    fs::create_dir_all(&files.root).map_err(err)?;
    let started = Instant::now();
    eprintln!(
        "05B/05C export {}: converting graph pages from {}",
        kind.method(),
        source_graph.display()
    );
    let layout = export_shared_graph(&source_graph, &files.graph_pages).map_err(err)?;
    eprintln!(
        "05B/05C export {}: graph nodes={} max_degree={} pages={}",
        kind.method(),
        layout.node_count,
        layout.max_degree,
        layout.page_count
    );

    let data_root = args.path("--data-root")?;
    let dataset = args.text("--dataset")?;
    let base_path = data_root
        .join(dataset)
        .join(format!("{dataset}_base.fvecs"));
    eprintln!(
        "05B/05C export {}: loading base vectors from {}",
        kind.method(),
        base_path.display()
    );
    let base = read_fvecs_matrix(&base_path)?;
    eprintln!(
        "05B/05C export {}: base rows={} dim={}",
        kind.method(),
        base.nrows(),
        base.ncols()
    );
    let source_start_index = if kind == CodecKind::Ours {
        if layout.node_count < base.nrows() {
            return Err(format!(
                "Ours native graph must contain at least N base nodes: graph={}, base={}",
                layout.node_count,
                base.nrows(),
            ));
        }
        0
    } else {
        if layout.additional_points < 1
            || layout.node_count < base.nrows() + layout.additional_points
        {
            return Err(format!(
                "baseline shared graph must contain at least N+additional nodes: graph={}, base={}, additional={}",
                layout.node_count,
                base.nrows(),
                layout.additional_points
            ));
        }
        let source_meta = companion_graph_meta(&source_graph);
        let source_values = parse_key_values(&source_meta)?;
        let start_index: usize = map_number(&source_values, "start_index")?;
        if start_index >= base.nrows() {
            return Err(format!(
                "source start_index {start_index} exceeds base count"
            ));
        }
        start_index
    };

    let mut build_distance_computations = 0_u64;
    let mut ours_compact_record_bytes = 0_usize;
    let mut ours_residual_record_bytes = 0_usize;
    let mut ours_record_count = 0_usize;
    let (resident_bytes, codebook_bytes) = match kind {
        CodecKind::Pq => {
            if base.ncols() % 2 != 0 {
                return Err(format!(
                    "PQ 4 bit/dim requires an even dimension, got {}",
                    base.ncols()
                ));
            }
            let threads = std::thread::available_parallelism().map_or(1, usize::from);
            let pool = create_thread_pool(threads).map_err(err)?;
            let seed: u64 = args.number("--seed")?;
            let mut rng = StdRng::seed_from_u64(seed);
            eprintln!(
                "05B export PQ: training 4-bit PQ with chunks={} threads={threads}",
                base.ncols() / 2
            );
            let table =
                diskann_async::train_pq(base.as_view(), base.ncols() / 2, &mut rng, pool.as_ref())
                    .map_err(err)?;
            build_distance_computations =
                diskann_quantization::algorithms::kmeans::take_kmeans_distance_count();
            eprintln!("05B export PQ: training done, encoding payload");
            let codec = PqCodec::new(table, layout.node_count);
            for id in 0..base.nrows() {
                codec.encode(id as u32, base.row(id)).map_err(err)?;
                if id > 0 && id % 262_144 == 0 {
                    eprintln!("05B export PQ: encoded {id}/{}", base.nrows());
                }
            }
            for id in base.nrows()..layout.node_count {
                codec
                    .encode(id as u32, base.row(source_start_index))
                    .map_err(err)?;
            }
            eprintln!("05B export PQ: saving payload");
            codec.save(&files.pivots, &files.codes).map_err(err)?;
            let _ = baseline_disk::export_pq_disk_payload(
                &codec,
                &files.pq_payload_pages,
                &files.pq_codebook,
            )
            .map_err(err)?;
            (
                codec.resident_bytes(),
                256 * base.ncols() * std::mem::size_of::<f32>(),
            )
        }
        CodecKind::Sq => {
            eprintln!("05B export SQ: training scalar quantizer");
            let quantizer = ScalarQuantizationParameters::default().train(base.as_view());
            eprintln!("05B export SQ: training done, encoding payload");
            let codec = SqCodec::new(quantizer, layout.node_count);
            for id in 0..base.nrows() {
                codec.encode(id as u32, base.row(id)).map_err(err)?;
                if id > 0 && id % 262_144 == 0 {
                    eprintln!("05B export SQ: encoded {id}/{}", base.nrows());
                }
            }
            for id in base.nrows()..layout.node_count {
                codec
                    .encode(id as u32, base.row(source_start_index))
                    .map_err(err)?;
            }
            codec.save(&files.sq_prefix).map_err(err)?;
            let sq_code_bytes = codec.code_bytes_per_vector();
            let mut sq_codes = Vec::with_capacity(layout.node_count);
            for id in 0..layout.node_count {
                let vector = if id < base.nrows() {
                    base.row(id)
                } else {
                    base.row(source_start_index)
                };
                let mut buf = vec![0_u8; sq_code_bytes];
                let cv =
                    diskann_quantization::scalar::MutCompensatedVectorRef::<4>::from_canonical_front_mut(
                        &mut buf,
                        base.ncols(),
                    )
                    .map_err(err)?;
                codec.quantizer().compress_into(vector, cv).map_err(err)?;
                sq_codes.push(buf);
            }
            let _ = baseline_disk::export_sq_disk_payload(
                &codec,
                &sq_codes,
                &files.sq_payload_pages,
                &files.sq_codebook,
            )
            .map_err(err)?;
            (codec.resident_bytes(), 0)
        }
        CodecKind::Saq => {
            let seed: u64 = args.number("--seed")?;
            let mut rng = StdRng::seed_from_u64(seed);
            eprintln!("05B export SAQ: training spherical quantizer");
            let quantizer = spherical::SphericalQuantizer::train(
                base.as_view(),
                TransformKind::PaddingHadamard {
                    target_dim: TargetDim::Natural,
                },
                Metric::L2.try_into().map_err(err)?,
                spherical::PreScale::ReciprocalMeanNorm,
                &mut rng,
                GlobalAllocator,
            )
            .map_err(err)?;
            let plan = spherical::iface::Impl::<4>::new(quantizer).map_err(err)?;
            eprintln!("05B export SAQ: training done, encoding payload");
            let codec = SaqCodec::new(plan, layout.node_count, seed);
            for id in 0..base.nrows() {
                codec.encode(id as u32, base.row(id)).map_err(err)?;
                if id > 0 && id % 262_144 == 0 {
                    eprintln!("05B export SAQ: encoded {id}/{}", base.nrows());
                }
            }
            for id in base.nrows()..layout.node_count {
                codec
                    .encode(id as u32, base.row(source_start_index))
                    .map_err(err)?;
            }
            codec
                .save(&files.saq_metadata, &files.saq_codes)
                .map_err(err)?;
            let _ = baseline_disk::export_saq_disk_payload(&codec, &files.saq_payload_pages)
                .map_err(err)?;
            (codec.resident_bytes(), 0)
        }
        CodecKind::Ours => {
            let center = train_ours_k1_center(&base)?;
            let space = RabitqSpace::new_with_centroids(base.ncols(), 100, 1, &center)?;
            if space.residual_bits() != 4 || space.residual_block_size() != 16 {
                return Err(format!(
                    "Ours native configuration drifted: residual_bits={} residual_block_size={}",
                    space.residual_bits(),
                    space.residual_block_size()
                ));
            }
            let mut builder = OursIndexBuilder::new(space, base.nrows()).map_err(err)?;
            for id in 0..base.nrows() {
                builder.encode(id, base.row(id)).map_err(err)?;
                if id > 0 && id % 262_144 == 0 {
                    eprintln!("05B/05C export Ours: encoded {id}/{}", base.nrows());
                }
            }
            let (payload_layout, resident_bytes) = builder
                .save(
                    100,
                    &center,
                    &files.ours_metadata,
                    &files.ours_sidecar,
                    &files.ours_payload,
                )
                .map_err(err)?;
            if payload_layout.record_count != base.nrows() {
                return Err("Ours exported payload count changed unexpectedly".into());
            }
            ours_compact_record_bytes = payload_layout.compact_bytes;
            ours_residual_record_bytes = payload_layout.residual_bytes;
            ours_record_count = payload_layout.record_count;
            // The artifact contains all four ablations.  Account the maximum
            // simultaneous resident footprint (sidecar + full4/residual) and
            // use one identical BFS cache for every ablation, so the measured
            // differences cannot come from silently reallocating DRAM.
            (
                resident_bytes + payload_layout.record_count * payload_layout.record_bytes,
                0,
            )
        }
    };
    fs::write(
        &files.resident_marker,
        format!(
            "{} navigation codes are deliberately loaded into DRAM during graph search.\n",
            kind.method()
        ),
    )
    .map_err(err)?;

    let meta = IndexMeta {
        kind,
        layout,
        base_count: base.nrows(),
        dimension: base.ncols(),
        source_start_index,
        source_graph_sha256: actual_graph_hash,
        graph_role: graph_role.to_string(),
        resident_bytes,
        codebook_bytes,
        export_time_ms: started.elapsed().as_secs_f64() * 1_000.0,
        ours_compact_record_bytes,
        ours_residual_record_bytes,
        ours_record_count,
    };
    write_meta(&files.metadata, &meta)?;
    Ok((meta, build_distance_computations))
}

fn read_query_order(path: &Path, query_count: usize) -> Result<Vec<usize>> {
    let bytes = fs::read(path).map_err(err)?;
    if bytes.len() != query_count * 4 {
        return Err(format!(
            "query order has {} bytes, expected {} for {query_count} queries",
            bytes.len(),
            query_count * 4
        ));
    }
    let mut seen = vec![false; query_count];
    let mut order = Vec::with_capacity(query_count);
    for chunk in bytes.chunks_exact(4) {
        let id = u32::from_le_bytes(chunk.try_into().unwrap()) as usize;
        if id >= query_count || seen[id] {
            return Err(format!("query order contains invalid or duplicate id {id}"));
        }
        seen[id] = true;
        order.push(id);
    }
    Ok(order)
}

fn search_one<C: ResidentCodec>(
    codec: &C,
    backend: BackendFactory,
    index: &diskann::graph::DiskANNIndex<diskann_fair::disk_port::PortProvider>,
    runtime: &tokio::runtime::Runtime,
    query_id: usize,
    query: &[f32],
    truth: &[u32],
    width: usize,
    beam: usize,
) -> Result<QueryRun> {
    let stats = Arc::new(Mutex::new(IoStats::default()));
    let strategy = DiskStrategy::new(codec, backend, stats.clone());
    let mut ids = vec![0_u32; 10];
    let mut distances = vec![0.0_f32; 10];
    let mut output = IdDistance::new(&mut ids, &mut distances);
    let started = Instant::now();
    runtime
        .block_on(index.search(
            Knn::new(width, Some(beam)).map_err(err)?,
            &strategy,
            &DefaultContext,
            query,
            &mut output,
        ))
        .map_err(err)?;
    let latency_us = started.elapsed().as_secs_f64() * 1_000_000.0;
    let stats = *stats
        .lock()
        .map_err(|_| "query stats mutex was poisoned".to_string())?;
    Ok(QueryRun {
        query_id,
        recall: recall_hits(&ids, truth) as f64 / 10.0,
        ids,
        latency_us,
        stats,
        ablation: "",
        queue_compute_us: 0.0,
        rerank_us: 0.0,
        db1_checks: 0,
        db1_survivors: 0,
        full4_candidates: 0,
        full4_page_reads: 0,
        rerank_candidates: 0,
        rerank_page_reads: 0,
    })
}

#[allow(clippy::too_many_arguments)]
fn execute_config<C: ResidentCodec + 'static>(
    codec: Arc<C>,
    files: &IndexFiles,
    meta: &IndexMeta,
    queries: Arc<Matrix<f32>>,
    groundtruth: Arc<Vec<Vec<u32>>>,
    order: Arc<Vec<usize>>,
    workers: usize,
    warmup_queries: usize,
    width: usize,
    beam: usize,
    direct: bool,
    shared_cache: Option<SharedPageCache>,
) -> Result<BatchRun> {
    if workers == 0 {
        return Err("workers must be positive".into());
    }
    let memory_factory = if direct {
        None
    } else {
        Some(BackendFactory::memory(&files.graph_pages, meta.layout.clone()).map_err(err)?)
    };
    let next = AtomicUsize::new(0);
    let results = Mutex::new(vec![None::<QueryRun>; order.len()]);
    let finished_at = Mutex::new(None::<Instant>);
    let mut measured_start = None;
    let (ready_tx, ready_rx) = std::sync::mpsc::channel();
    std::thread::scope(|scope| -> Result<()> {
        let mut handles = Vec::with_capacity(workers);
        let mut starts = Vec::with_capacity(workers);
        for worker_id in 0..workers {
            let codec = codec.clone();
            let queries = queries.clone();
            let groundtruth = groundtruth.clone();
            let order = order.clone();
            let factory = memory_factory.clone();
            let cache = shared_cache.clone();
            let next = &next;
            let results = &results;
            let finished_at = &finished_at;
            let ready = ready_tx.clone();
            let (start_tx, start_rx) = std::sync::mpsc::channel();
            starts.push(start_tx);
            handles.push(scope.spawn(move || -> Result<()> {
                let factory = if direct {
                    BackendFactory::direct_with_loaded_cache(
                        &files.graph_pages,
                        meta.layout.clone(),
                        cache.ok_or_else(|| {
                            "direct search is missing the shared cache".to_string()
                        })?,
                    )
                    .map_err(err)?
                } else {
                    factory.ok_or_else(|| "memory search is missing its backend".to_string())?
                };
                let index = new_search_index(meta.layout.max_degree, 400).map_err(err)?;
                let runtime = tokio::runtime::Builder::new_current_thread()
                    .build()
                    .map_err(err)?;

                for warm_index in (worker_id..warmup_queries.min(order.len())).step_by(workers) {
                    let query_id = order[warm_index];
                    let _ = search_one(
                        codec.as_ref(),
                        factory.clone(),
                        &index,
                        &runtime,
                        query_id,
                        queries.row(query_id),
                        &groundtruth[query_id],
                        width,
                        beam,
                    )?;
                }

                ready.send(()).map_err(|_| "warmup coordinator exited")?;
                drop(ready);
                start_rx.recv().map_err(|_| "measurement cancelled")?;
                loop {
                    let position = next.fetch_add(1, Ordering::Relaxed);
                    if position >= order.len() {
                        break;
                    }
                    let query_id = order[position];
                    let run = search_one(
                        codec.as_ref(),
                        factory.clone(),
                        &index,
                        &runtime,
                        query_id,
                        queries.row(query_id),
                        &groundtruth[query_id],
                        width,
                        beam,
                    )?;
                    results
                        .lock()
                        .map_err(|_| "query results mutex was poisoned".to_string())?[position] =
                        Some(run);
                }
                *finished_at.lock().map_err(|_| "timing lock poisoned")? = Some(Instant::now());
                Ok(())
            }));
        }
        drop(ready_tx);
        for _ in 0..workers {
            ready_rx.recv().map_err(|_| "worker failed during setup/warmup")?;
        }
        measured_start = Some(Instant::now());
        for start in starts {
            start.send(()).map_err(|_| "worker exited before measurement")?;
        }
        for handle in handles {
            handle
                .join()
                .map_err(|_| "query worker panicked".to_string())??;
        }
        Ok(())
    })?;
    let wall_seconds = finished_at.into_inner().map_err(|_| "timing lock poisoned")?
        .ok_or("measurement has no end time")?
        .duration_since(measured_start.ok_or("measurement has no start time")?)
        .as_secs_f64();
    let runs = results
        .into_inner()
        .map_err(|_| "query results mutex was poisoned".to_string())?
        .into_iter()
        .map(|run| run.ok_or_else(|| "a query worker did not produce a result".to_string()))
        .collect::<Result<Vec<_>>>()?;
    Ok(BatchRun {
        runs,
        wall_seconds,
        peak_rss_bytes: peak_rss_bytes(),
    })
}

#[allow(clippy::too_many_arguments)]
fn search_one_ours(
    codec: &OursResidentCodec,
    graph_factory: &BackendFactory,
    payload_reader: &mut diskann_fair::disk_port::ours_port::OursPayloadReader,
    query_id: usize,
    trace_enabled: bool,
    query: &[f32],
    truth: &[u32],
    width: usize,
    beam: usize,
    ablation: OursAblation,
) -> Result<QueryRun> {
    let started = Instant::now();
    let result = search_ours_disk_graph(
        codec,
        graph_factory,
        payload_reader,
        query,
        query_id,
        trace_enabled,
        10,
        width,
        beam,
        1.9,
        100,
        ablation,
    )
    .map_err(err)?;
    let latency_us = started.elapsed().as_secs_f64() * 1_000_000.0;
    let OursDiskStats {
        io,
        queue_compute_us,
        rerank_us,
        db1_checks,
        db1_survivors,
        full4_candidates,
        full4_page_reads,
        rerank_candidates,
        rerank_page_reads,
    } = result.stats;
    Ok(QueryRun {
        query_id,
        recall: recall_hits(&result.ids, truth) as f64 / 10.0,
        ids: result.ids,
        latency_us,
        stats: io,
        ablation: ablation.as_str(),
        queue_compute_us,
        rerank_us,
        db1_checks,
        db1_survivors,
        full4_candidates,
        full4_page_reads,
        rerank_candidates,
        rerank_page_reads,
    })
}

#[allow(clippy::too_many_arguments)]
fn execute_config_ours(
    codec: Arc<OursResidentCodec>,
    files: &IndexFiles,
    meta: &IndexMeta,
    queries: Arc<Matrix<f32>>,
    groundtruth: Arc<Vec<Vec<u32>>>,
    order: Arc<Vec<usize>>,
    workers: usize,
    warmup_queries: usize,
    width: usize,
    beam: usize,
    ablation: OursAblation,
    direct: bool,
    shared_cache: Option<SharedPageCache>,
) -> Result<BatchRun> {
    if workers == 0 {
        return Err("workers must be positive".into());
    }
    let memory_graph = if direct {
        None
    } else {
        Some(BackendFactory::memory(&files.graph_pages, meta.layout.clone()).map_err(err)?)
    };
    let memory_payload = if !direct || ablation == OursAblation::Full4ResidentNoGate {
        Some(OursPayloadFactory::memory(&files.ours_payload, codec.layout.clone()).map_err(err)?)
    } else {
        None
    };
    let direct_payload = if direct && ablation != OursAblation::Full4ResidentNoGate {
        Some(OursPayloadFactory::direct(&files.ours_payload, codec.layout.clone()).map_err(err)?)
    } else {
        None
    };
    let next = AtomicUsize::new(0);
    let results = Mutex::new(vec![None::<QueryRun>; order.len()]);
    let finished_at = Mutex::new(None::<Instant>);
    let mut measured_start = None;
    let (ready_tx, ready_rx) = std::sync::mpsc::channel();
    std::thread::scope(|scope| -> Result<()> {
        let mut handles = Vec::with_capacity(workers);
        let mut starts = Vec::with_capacity(workers);
        for worker_id in 0..workers {
            let codec = codec.clone();
            let queries = queries.clone();
            let groundtruth = groundtruth.clone();
            let order = order.clone();
            let memory_graph = memory_graph.clone();
            let memory_payload = memory_payload.clone();
            let direct_payload = direct_payload.clone();
            let cache = shared_cache.clone();
            let next = &next;
            let results = &results;
            let finished_at = &finished_at;
            let ready = ready_tx.clone();
            let (start_tx, start_rx) = std::sync::mpsc::channel();
            starts.push(start_tx);
            handles.push(scope.spawn(move || -> Result<()> {
                let graph_factory = if direct {
                    BackendFactory::direct_with_loaded_cache(
                        &files.graph_pages,
                        meta.layout.clone(),
                        cache.ok_or_else(|| {
                            "Ours direct search is missing shared cache".to_string()
                        })?,
                    )
                    .map_err(err)?
                } else {
                    memory_graph
                        .ok_or_else(|| "Ours memory search is missing graph backend".to_string())?
                };
                let payload_factory = if direct {
                    direct_payload.or(memory_payload).ok_or_else(|| {
                        "Ours direct search is missing payload backend".to_string()
                    })?
                } else {
                    memory_payload.ok_or_else(|| {
                        "Ours memory search is missing payload backend".to_string()
                    })?
                };
                let mut payload_reader = payload_factory.create().map_err(err)?;
                if direct {
                    if let Some(layout) = &codec.locality {
                        payload_reader.locality = Some(diskann_fair::disk_port::locality::LocalityReader::new(layout.clone()).map_err(err)?);
                    }
                }

                for warm_index in (worker_id..warmup_queries.min(order.len())).step_by(workers) {
                    let query_id = order[warm_index];
                    let _ = search_one_ours(
                        codec.as_ref(),
                        &graph_factory,
                        &mut payload_reader,
                        query_id,
                        false,
                        queries.row(query_id),
                        &groundtruth[query_id],
                        width,
                        beam,
                        ablation,
                    )?;
                }
                // Dropped channels release peers if setup/warmup fails.
                ready.send(()).map_err(|_| "Ours warmup coordinator exited")?;
                drop(ready);
                start_rx.recv().map_err(|_| "Ours measurement cancelled")?;
                loop {
                    let position = next.fetch_add(1, Ordering::Relaxed);
                    if position >= order.len() {
                        break;
                    }
                    let query_id = order[position];
                    let run = search_one_ours(
                        codec.as_ref(),
                        &graph_factory,
                        &mut payload_reader,
                        query_id,
                        direct,
                        queries.row(query_id),
                        &groundtruth[query_id],
                        width,
                        beam,
                        ablation,
                    )?;
                    results
                        .lock()
                        .map_err(|_| "Ours query results mutex was poisoned".to_string())?
                        [position] = Some(run);
                }
                let mut end = finished_at.lock().map_err(|_| "Ours timing lock poisoned")?;
                *end = Some(Instant::now());
                Ok(())
            }));
        }
        drop(ready_tx);
        for _ in 0..workers {
            ready_rx.recv().map_err(|_| "Ours worker failed during setup/warmup")?;
        }
        measured_start = Some(Instant::now());
        for start in starts {
            start.send(()).map_err(|_| "Ours worker exited before measurement")?;
        }
        for handle in handles {
            handle
                .join()
                .map_err(|_| "Ours query worker panicked".to_string())??;
        }
        Ok(())
    })?;
    let wall_seconds = finished_at.into_inner()
        .map_err(|_| "Ours timing lock poisoned")?
        .ok_or("Ours measurement has no end time")?
        .duration_since(measured_start.ok_or("Ours measurement has no start time")?)
        .as_secs_f64();
    let runs = results
        .into_inner()
        .map_err(|_| "Ours query results mutex was poisoned".to_string())?
        .into_iter()
        .map(|run| run.ok_or_else(|| "an Ours query did not produce a result".to_string()))
        .collect::<Result<Vec<_>>>()?;
    Ok(BatchRun {
        runs,
        wall_seconds,
        peak_rss_bytes: peak_rss_bytes(),
    })
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
            return Err("QG05_FAST_WIDTH must be positive".into());
        }
        return Ok(vec![width]);
    }
    if let Some(value) = args.optional("--integration-widths") {
        if !args.text("--run-id")?.starts_with("native_integration_") {
            return Err("--integration-widths is restricted to native_integration_* runs".into());
        }
        return parse_positive_list(value, "--integration-widths");
    }
    let mut values = (1..=30).collect::<Vec<_>>();
    values.extend((40..=100).step_by(10));
    values.extend((140..=580).step_by(40));
    Ok(values)
}

fn validation_beams(args: &Args) -> Result<Vec<usize>> {
    if std::env::var("QG05_FAST").ok().as_deref() == Some("1") {
        return Ok(vec![1]);
    }
    if let Some(value) = args.optional("--integration-beams") {
        if !args.text("--run-id")?.starts_with("native_integration_") {
            return Err("--integration-beams is restricted to native_integration_* runs".into());
        }
        return parse_positive_list(value, "--integration-beams");
    }
    Ok(vec![1, 2, 4, 8, 16, 32])
}

fn parse_positive_list(value: &str, flag: &str) -> Result<Vec<usize>> {
    let values = value
        .split(',')
        .map(|item| {
            item.parse::<usize>()
                .map_err(|_| format!("invalid {flag} item: {item}"))
        })
        .collect::<Result<Vec<_>>>()?;
    if values.is_empty() || values.iter().any(|&value| value == 0) {
        return Err(format!("{flag} must contain positive integers"));
    }
    Ok(values)
}

fn selected_test_beam(args: &Args) -> Result<usize> {
    if args.text("--run-id")?.starts_with("native_integration_") {
        return Ok(validation_beams(args)?[0]);
    }
    let path = args.path("--tuning-lock")?;
    let text = fs::read_to_string(&path).map_err(err)?;
    let method = args.text("--method")?;
    let storage_mode = args.text("--storage-mode")?;
    let marker = format!("\"{method}::{storage_mode}\"");
    let section = text
        .split_once(&marker)
        .map(|(_, rest)| rest)
        .ok_or_else(|| format!("{} has no selection for {method}", path.display()))?;
    let config = section
        .split_once("\"config_id\"")
        .and_then(|(_, rest)| rest.split_once(':').map(|(_, value)| value))
        .and_then(|value| value.split('"').nth(1))
        .ok_or_else(|| format!("{} has no config_id for {method}", path.display()))?;
    config
        .strip_prefix("beam")
        .ok_or_else(|| format!("unsupported 05B config_id in tuning lock: {config}"))?
        .parse()
        .map_err(|_| format!("invalid 05B config_id in tuning lock: {config}"))
}

fn cached_base_nodes(pages: &[u64], layout: &GraphLayout, base_count: usize) -> usize {
    pages
        .iter()
        .map(|&page| {
            let first = page as usize * layout.nodes_per_page;
            let last = (first + layout.nodes_per_page).min(base_count);
            last.saturating_sub(first)
        })
        .sum()
}

struct SummaryRow {
    width: usize,
    beam: usize,
    ablation: &'static str,
    recall: f64,
    qps: f64,
    latency_mean_us: f64,
    latency_p50_us: f64,
    latency_p95_us: f64,
    latency_p99_us: f64,
    io_requests_per_query: f64,
    sectors_4k_per_query: f64,
    bytes_read_per_query: f64,
    io_wait_us: f64,
    distance_compute_us: f64,
    query_prep_us: f64,
    queue_compute_us: f64,
    rerank_us: f64,
    visited_nodes: f64,
    distance_evaluations: f64,
    db1_checks: f64,
    db1_survivors: f64,
    full4_candidates: f64,
    full4_page_reads: f64,
    rerank_candidates: f64,
    rerank_page_reads: f64,
    query_count: usize,
    peak_rss_bytes: u64,
}

impl SummaryRow {
    fn from_batch(batch: &BatchRun, width: usize, beam: usize) -> Self {
        let n = batch.runs.len().max(1) as f64;
        let mut latencies = batch
            .runs
            .iter()
            .map(|run| run.latency_us)
            .collect::<Vec<_>>();
        let latency_mean_us = latencies.iter().sum::<f64>() / n;
        let latency_p50_us = percentile(&mut latencies, 0.50);
        let latency_p95_us = percentile(&mut latencies, 0.95);
        let latency_p99_us = percentile(&mut latencies, 0.99);
        let sum = |value: fn(&QueryRun) -> f64| batch.runs.iter().map(value).sum::<f64>() / n;
        Self {
            width,
            beam,
            ablation: batch.runs.first().map_or("", |run| run.ablation),
            recall: sum(|run| run.recall),
            qps: batch.runs.len() as f64 / batch.wall_seconds.max(1e-12),
            latency_mean_us,
            latency_p50_us,
            latency_p95_us,
            latency_p99_us,
            io_requests_per_query: sum(|run| run.stats.requests as f64),
            sectors_4k_per_query: sum(|run| run.stats.sectors_4k as f64),
            bytes_read_per_query: sum(|run| run.stats.bytes_read as f64),
            io_wait_us: sum(|run| run.stats.io_wait_us),
            distance_compute_us: sum(|run| run.stats.distance_compute_us),
            query_prep_us: sum(|run| run.stats.query_prep_us),
            queue_compute_us: sum(|run| {
                if run.ablation.is_empty() {
                    (run.latency_us
                        - run.stats.io_wait_us
                        - run.stats.distance_compute_us
                        - run.stats.query_prep_us)
                        .max(0.0)
                } else {
                    run.queue_compute_us
                }
            }),
            rerank_us: sum(|run| run.rerank_us),
            visited_nodes: sum(|run| run.stats.visited_nodes as f64),
            distance_evaluations: sum(|run| run.stats.distance_evaluations as f64),
            db1_checks: sum(|run| run.db1_checks as f64),
            db1_survivors: sum(|run| run.db1_survivors as f64),
            full4_candidates: sum(|run| run.full4_candidates as f64),
            full4_page_reads: sum(|run| run.full4_page_reads as f64),
            rerank_candidates: sum(|run| run.rerank_candidates as f64),
            rerank_page_reads: sum(|run| run.rerank_page_reads as f64),
            query_count: batch.runs.len(),
            peak_rss_bytes: batch.peak_rss_bytes,
        }
    }

    fn to_json(&self, index_size_mb: f64, resident_bytes: usize) -> String {
        format!(
            concat!(
                "{{\"config_id\":\"beam{}\",\"search_param\":{},\"search_width\":{},",
                "\"effective_search_width\":{},",
                "\"ablation\":{},\"beam_width\":{},\"recall\":{:.12},\"qps\":{:.9},",
                "\"latency_mean_us\":{:.9},\"latency_p50_us\":{:.9},",
                "\"latency_p95_us\":{:.9},\"latency_p99_us\":{:.9},",
                "\"index_size_mb\":{:.9},\"resident_bytes\":{},\"peak_rss_bytes\":{},",
                "\"io_requests_per_query\":{:.9},\"sectors_4k_per_query\":{:.9},",
                "\"bytes_read_per_query\":{:.9},\"io_wait_us\":{:.9},",
                "\"distance_compute_us\":{:.9},\"query_prep_us\":{:.9},",
                "\"queue_compute_us\":{:.9},\"rerank_us\":{:.9},",
                "\"visited_nodes\":{:.9},\"distance_evaluations\":{:.9},",
                "\"db1_checks\":{:.9},\"db1_survivors\":{:.9},",
                "\"full4_candidates\":{:.9},\"full4_page_reads\":{:.9},",
                "\"rerank_candidates\":{:.9},\"rerank_page_reads\":{:.9},",
                "\"query_count\":{}}}"
            ),
            self.beam,
            self.width,
            self.width,
            if self.ablation.is_empty() { self.width } else { self.width.max(10) },
            json_escape(self.ablation),
            self.beam,
            self.recall,
            self.qps,
            self.latency_mean_us,
            self.latency_p50_us,
            self.latency_p95_us,
            self.latency_p99_us,
            index_size_mb,
            resident_bytes,
            self.peak_rss_bytes,
            self.io_requests_per_query,
            self.sectors_4k_per_query,
            self.bytes_read_per_query,
            self.io_wait_us,
            self.distance_compute_us,
            self.query_prep_us,
            self.queue_compute_us,
            self.rerank_us,
            self.visited_nodes,
            self.distance_evaluations,
            self.db1_checks,
            self.db1_survivors,
            self.full4_candidates,
            self.full4_page_reads,
            self.rerank_candidates,
            self.rerank_page_reads,
            self.query_count,
        )
    }
}

fn peak_rss_bytes() -> u64 {
    fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|text| {
            text.lines().find_map(|line| {
                let value = line.strip_prefix("VmHWM:")?.split_whitespace().next()?;
                value.parse::<u64>().ok().map(|kb| kb * 1024)
            })
        })
        .unwrap_or(1)
}

/// Reset Linux's per-process high-water RSS immediately before a measured
/// direct-I/O configuration.  Memory parity runs happen afterward and must
/// not inflate the paper-facing peak-RSS number.
fn reset_peak_rss() -> Result<()> {
    if std::env::var("QG05_MEASURE_WHOLE_PROCESS").ok().as_deref() == Some("1") {
        return Ok(());
    }
    fs::write("/proc/self/clear_refs", b"5\n")
        .map_err(|error| format!("failed to reset /proc/self peak RSS: {error}"))
}

fn status_value(key: &str) -> String {
    fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|text| {
            text.lines()
                .find_map(|line| line.strip_prefix(key).map(str::trim).map(str::to_string))
        })
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".to_string())
}

fn rustc_version() -> String {
    Command::new("rustc")
        .arg("--version")
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|value| value.trim().to_string())
        .unwrap_or_else(|| "rustc-unknown".to_string())
}

#[allow(clippy::too_many_arguments)]
fn write_trace_rows(
    output: &mut File,
    args: &Args,
    batch: &BatchRun,
    width: usize,
    beam: usize,
    storage_mode: &str,
    cache_nodes: usize,
    cache_bytes: usize,
    resident_bytes: usize,
) -> Result<()> {
    let peak = batch.peak_rss_bytes;
    for run in &batch.runs {
        let stats = run.stats;
        let average_read_bytes = stats.bytes_read as f64 / stats.requests.max(1) as f64;
        let queue_compute_us =
            (run.latency_us - stats.query_prep_us - stats.io_wait_us - stats.distance_compute_us)
                .max(0.0);
        let queue_compute_us = if run.ablation.is_empty() {
            queue_compute_us
        } else {
            run.queue_compute_us
        };
        let line = format!(
            concat!(
                "{{\"layer\":{},\"storage_mode\":{},\"cache_mode\":{},\"dataset\":{},\"method\":{},",
                "\"config_id\":\"beam{}\",\"ablation\":{},\"repeat_id\":{},\"query_id\":{},",
                "\"result_ids\":{:?},",
                "\"search_width\":{},\"beam_width\":{},\"workers\":{},",
                "\"search_dram_budget_gib\":{},\"cache_nodes\":{},",
                "\"resident_bytes\":{},\"cache_bytes\":{},\"peak_rss_bytes\":{},",
                "\"recall_at_10\":{:.12},\"latency_us\":{:.9},",
                "\"query_prep_us\":{:.9},\"queue_compute_us\":{:.9},",
                "\"io_wait_us\":{:.9},\"distance_compute_us\":{:.9},\"rerank_us\":{:.9},",
                "\"visited_nodes\":{},\"distance_evaluations\":{},",
                "\"io_requests\":{},\"sectors_4k\":{},\"bytes_read\":{},",
                "\"average_read_bytes\":{:.9},\"coalesced_requests\":{},",
                "\"duplicate_pages_removed\":{},\"shared_cache_hits\":{},",
                "\"shared_cache_misses\":{},\"query_cache_hits\":{},",
                "\"query_cache_misses\":{},\"query_cache_allocated_bytes\":{},",
                "\"query_cache_evictions\":{},\"db1_checks\":{},",
                "\"db1_survivors\":{},\"full4_candidates\":{},",
                "\"full4_page_reads\":{},\"rerank_candidates\":{},",
                "\"rerank_page_reads\":{}}}\n"
            ),
            json_escape(args.text("--layer")?),
            json_escape(storage_mode),
            json_escape(args.text("--cache-mode")?),
            json_escape(args.text("--dataset")?),
            json_escape(args.text("--method")?),
            beam,
            json_escape(run.ablation),
            args.number::<usize>("--repeat-id")?,
            run.query_id,
            run.ids,
            width,
            beam,
            args.number::<usize>("--workers")?,
            args.number::<f64>("--search-dram-budget-gib")?,
            cache_nodes,
            resident_bytes,
            cache_bytes,
            peak,
            run.recall,
            run.latency_us,
            stats.query_prep_us,
            queue_compute_us,
            stats.io_wait_us,
            stats.distance_compute_us,
            run.rerank_us,
            stats.visited_nodes,
            stats.distance_evaluations,
            stats.requests,
            stats.sectors_4k,
            stats.bytes_read,
            average_read_bytes,
            stats.coalesced_requests,
            stats.duplicate_pages_removed,
            stats.shared_cache_hits,
            stats.shared_cache_misses,
            stats.query_cache_hits,
            stats.query_cache_misses,
            stats.query_cache_allocated_bytes,
            stats.query_cache_evictions,
            run.db1_checks,
            run.db1_survivors,
            run.full4_candidates,
            run.full4_page_reads,
            run.rerank_candidates,
            run.rerank_page_reads,
        );
        output.write_all(line.as_bytes()).map_err(err)?;
    }
    Ok(())
}

fn json_object(fields: Vec<(&str, String)>) -> String {
    let body = fields
        .into_iter()
        .map(|(key, value)| format!("  {}: {}", json_escape(key), value))
        .collect::<Vec<_>>()
        .join(",\n");
    format!("{{\n{body}\n}}\n")
}

#[allow(clippy::too_many_arguments)]
fn write_search_artifact(
    args: &Args,
    files: &IndexFiles,
    meta: &IndexMeta,
    storage_mode: &str,
    resident_bytes: usize,
    summaries: &[SummaryRow],
    parity: &ParityTotals,
    parity_hash: &str,
    trace_path: &Path,
    cache_nodes: usize,
    cache_bytes: usize,
    worker_scratch_bytes: usize,
    measured_peak_rss_bytes: u64,
) -> Result<()> {
    let result_path = args.path("--result-json")?;
    let phase = match args.text("--phase")? {
        "validate" => "validate",
        "validation" => "validation",
        "test" => "test",
        other => return Err(format!("invalid measured phase: {other}")),
    };
    let peak = measured_peak_rss_bytes;
    let mut measured_index_bytes = index_bytes(files)?;
    if let Some(dir) = args.optional("--locality-layout-dir") {
        measured_index_bytes -= fs::metadata(&files.graph_pages).map_err(err)?.len();
        measured_index_bytes -= fs::metadata(&files.ours_payload).map_err(err)?.len();
        for name in ["graph_compact.pages", "residual.pages", "id_to_slot.u32"] {
            measured_index_bytes += fs::metadata(Path::new(dir).join(name)).map_err(err)?.len();
        }
    }
    let index_size_mb = measured_index_bytes as f64 / (1024.0 * 1024.0);
    let summary_rows = summaries
        .iter()
        .map(|row| row.to_json(index_size_mb, meta.resident_bytes))
        .collect::<Vec<_>>()
        .join(",\n    ");
    let parity_json = json_object(vec![
        ("reference_artifact_sha256", json_escape(parity_hash)),
        (
            "max_recall_delta",
            format!("{:.12}", parity.max_recall_delta),
        ),
        (
            "mean_top10_overlap",
            format!("{:.12}", parity.mean_overlap()),
        ),
        (
            "mean_visited_count_relative_delta",
            format!("{:.12}", parity.mean_visited_delta()),
        ),
        (
            "mean_distance_count_relative_delta",
            format!("{:.12}", parity.mean_distance_delta()),
        ),
    ]);
    let mut fields = vec![
        ("measurement_scope", json_escape("test_only_excludes_warmup")),
        ("storage_cache_protocol", json_escape("uncontrolled")),
        ("locality_layout_dir", json_escape(args.optional("--locality-layout-dir").unwrap_or(""))),
        ("locality_combined_sha256", json_escape(args.optional("--locality-combined-sha256").unwrap_or(""))),
        ("locality_residual_sha256", json_escape(args.optional("--locality-residual-sha256").unwrap_or(""))),
        ("locality_mapping_sha256", json_escape(args.optional("--locality-mapping-sha256").unwrap_or(""))),
        ("adaptive_route_dim", args.optional("--adaptive-route-dim").unwrap_or("0").into()),
        ("adaptive_route_keep", args.optional("--adaptive-route-keep").unwrap_or("0").into()),
        ("adaptive_route_ratio", args.optional("--adaptive-route-ratio").unwrap_or("0").into()),
        ("adaptive_route_revisit", json_escape(args.optional("--adaptive-route-revisit").unwrap_or("0"))),
        ("diagnostic_neighbor_trace", args.optional("--adaptive-route-trace-dir").is_some().to_string()),
        ("throughput_comparable", args.optional("--adaptive-route-trace-dir").is_none().to_string()),
        ("adaptive_route_norms", json_escape(args.optional("--adaptive-route-norms").unwrap_or(""))),
        ("adaptive_route_norms_sha256", json_escape(args.optional("--adaptive-route-norms-sha256").unwrap_or(""))),
        ("adaptive_route_calibrate", json_escape(args.optional("--adaptive-route-calibrate").unwrap_or("0"))),
        ("adaptive_route_mode", json_escape(if args.optional("--adaptive-route-dir").is_some() {
            "resident-replacement-full4-verification-pilot"
        } else { "disabled" })),
        ("schema_version", "2".into()),
        ("status", json_escape("done")),
        ("layer", json_escape(args.text("--layer")?)),
        ("dataset", json_escape(args.text("--dataset")?)),
        ("method", json_escape(meta.kind.method())),
        ("storage_mode", json_escape(storage_mode)),
        ("cache_mode", json_escape(args.text("--cache-mode")?)),
        ("phase", json_escape(phase)),
        ("run_id", json_escape(args.text("--run-id")?)),
        ("repeat_id", args.text("--repeat-id")?.into()),
        ("workers", args.text("--workers")?.into()),
        (
            "search_dram_budget_gib",
            args.text("--search-dram-budget-gib")?.into(),
        ),
        ("source_suite", json_escape(source_suite(args)?)),
        ("source_kernel", json_escape(if args.optional("--adaptive-route-dir").is_some() {
            "projected asymmetric sign ordering + full4 disk verification + residual rerank"
        } else { meta.kind.source_kernel() })),
        ("port_kind", json_escape(if args.optional("--adaptive-route-dir").is_some() {
            "adaptive_resident_replacement_pilot"
        } else { PORT_KIND })),
        (
            "implementation_fingerprint",
            json_escape(args.text("--implementation-fingerprint")?),
        ),
        (
            "native_binary_sha256",
            json_escape(args.text("--native-binary-sha256")?),
        ),
        ("git_commit", json_escape(args.text("--git-commit")?)),
        ("compiler", json_escape(&rustc_version())),
        ("simd", json_escape(&meta.kind.simd_description())),
        ("base_count", meta.base_count.to_string()),
        ("dimension", meta.dimension.to_string()),
        ("resident_bytes", resident_bytes.to_string()),
        ("codebook_bytes", meta.codebook_bytes.to_string()),
        (
            "ours_4bit_payload_bytes",
            (if meta.kind == CodecKind::Ours {
                meta.ours_compact_record_bytes.saturating_mul(meta.ours_record_count)
            } else {
                0
            })
            .to_string(),
        ),
        (
            "ours_8bit_payload_bytes",
            (if meta.kind == CodecKind::Ours {
                meta.ours_compact_record_bytes
                    .saturating_add(meta.ours_residual_record_bytes)
                    .saturating_mul(meta.ours_record_count)
            } else {
                0
            })
            .to_string(),
        ),
        (
            "ours_adjacency_bytes",
            fs::metadata(&files.graph_pages)
                .map(|metadata| metadata.len())
                .unwrap_or(0)
                .to_string(),
        ),
        ("ours_fp32_base_bytes", "0".to_string()),
        ("worker_scratch_bytes", "null".to_string()),
        ("worker_scratch_reservation_bytes", worker_scratch_bytes.to_string()),
        ("query_cache_policy", json_escape(if meta.kind == CodecKind::Ours {
            "4k_lru_per_query_v1"
        } else { "4k_lru_graph_only_v1" })),
        ("query_cache_budget_per_worker_bytes", if meta.kind == CodecKind::Ours {
            (4 * 1024 * 1024).to_string()
        } else { "null".to_string() }),
        ("memory_accounting_complete", "false".to_string()),
        ("cache_bytes", cache_bytes.to_string()),
        ("cache_nodes", cache_nodes.to_string()),
        ("peak_rss_bytes", peak.to_string()),
        (
            "cpu_affinity",
            json_escape(&status_value("Cpus_allowed_list:")),
        ),
        (
            "numa_node",
            json_escape(
                &fs::read_to_string("/sys/devices/system/node/online")
                    .unwrap_or_else(|_| "unknown".into())
                    .trim()
                    .to_string(),
            ),
        ),
        (
            "input_manifest_sha256",
            json_escape(args.text("--input-manifest-sha256")?),
        ),
        (
            "source_index_manifest_sha256",
            json_escape(&sha256(&files.metadata)?),
        ),
        (
            "query_split_sha256",
            json_escape(args.text("--query-split-sha256")?),
        ),
        (
            "query_order_sha256",
            json_escape(args.text("--query-order-sha256")?),
        ),
        ("query_order_seed", args.text("--query-order-seed")?.into()),
        ("warmup_queries", args.text("--warmup-queries")?.into()),
        (
            "shared_graph_sha256",
            json_escape(if meta.graph_role == "shared_baseline" {
                &meta.source_graph_sha256
            } else {
                ""
            }),
        ),
        (
            "source_graph_sha256",
            json_escape(&meta.source_graph_sha256),
        ),
        ("graph_role", json_escape(&meta.graph_role)),
        ("index_path", json_escape(&files.root.to_string_lossy())),
        ("index_size_mb", format!("{index_size_mb:.9}")),
        ("whole_graph_in_memory", "false".into()),
        ("whole_payload_in_memory", "false".into()),
        ("direct_io", "true".into()),
        ("native_aio", "true".into()),
        ("io_backend", json_escape("linux_native_aio_odirect")),
        ("page_size", PAGE_SIZE.to_string()),
        ("formal_ready", "false".to_string()),
        ("implementation_parity", json_escape(if parity.count > 0 { "passed" } else { "not_verified" })),
        ("parity", parity_json.trim().to_string()),
        (
            "query_trace_path",
            json_escape(&trace_path.to_string_lossy()),
        ),
        ("query_trace_sha256", json_escape(&sha256(trace_path)?)),
        ("summary_rows", format!("[\n    {summary_rows}\n  ]")),
    ];
    if meta.kind == CodecKind::Ours && args.text("--layer")? == "05b" {
        fields.push((
            "ablations",
            "[\"full4-resident/no-gate\",\"db1-resident/full4-on-ssd\",\"db1+coalescing\",\"db1+coalescing+reuse\"]".into(),
        ));
    }
    fs::write(result_path, json_object(fields)).map_err(err)
}

fn run_search_with_codec_factory<C: ResidentCodec + 'static>(
    args: &Args,
    files: &IndexFiles,
    meta: &IndexMeta,
    make_codec: impl Fn(bool) -> Arc<C>,
    resident_payload_bytes: usize,
    storage_mode: &str,
    queries: Arc<Matrix<f32>>,
    groundtruth: Arc<Vec<Vec<u32>>>,
    order: Arc<Vec<usize>>,
) -> Result<()> {
    let workers: usize = args.number("--workers")?;
    let warmup_queries: usize = args.number("--warmup-queries")?;
    let worker_scratch_bytes = workers * 8 * 1024 * 1024;
    let budget_bytes =
        (args.number::<f64>("--search-dram-budget-gib")? * (1_u64 << 30) as f64).floor() as usize;
    let fixed_bytes = meta
        .codebook_bytes
        .checked_add(resident_payload_bytes)
        .and_then(|value| value.checked_add(worker_scratch_bytes))
        .ok_or_else(|| "DRAM accounting overflow".to_string())?;
    if fixed_bytes > budget_bytes {
        return Err(format!(
            "resident codec/codebook/worker scratch requires {fixed_bytes} bytes, budget is {budget_bytes}"
        ));
    }
    let cache_mode = args.text("--cache-mode")?;
    let mut cache_pages = if cache_mode == "c0" {
        Vec::new()
    } else if cache_mode == "standard" {
        let max_pages_by_budget = (budget_bytes - fixed_bytes) / PAGE_SIZE;
        let max_base_nodes = meta.base_count / 10;
        let max_pages_by_nodes = max_base_nodes / meta.layout.nodes_per_page + 1;
        select_bfs_cache_pages(
            &files.graph_pages,
            &meta.layout,
            max_pages_by_budget.min(max_pages_by_nodes),
        )
        .map_err(err)?
    } else {
        return Err(format!("unsupported cache mode: {cache_mode}"));
    };
    while cached_base_nodes(&cache_pages, &meta.layout, meta.base_count) > meta.base_count / 10 {
        cache_pages.pop();
    }
    let cache_nodes = cached_base_nodes(&cache_pages, &meta.layout, meta.base_count);
    let cache_bytes = cache_pages.len() * PAGE_SIZE;
    if fixed_bytes + cache_bytes > budget_bytes {
        return Err("BFS cache exceeds the declared DRAM budget".into());
    }
    let shared_cache =
        DirectGraphReader::load_shared_cache(&files.graph_pages, &meta.layout, &cache_pages)
            .map_err(err)?;

    let widths = formal_widths(args)?;
    let beams = if args.text("--phase")? == "test" {
        vec![selected_test_beam(args)?]
    } else {
        validation_beams(args)?
    };
    let trace_path = args.path("--query-trace")?;
    if let Some(parent) = trace_path.parent() {
        fs::create_dir_all(parent).map_err(err)?;
    }
    let mut trace = File::create(&trace_path).map_err(err)?;
    let mut summaries = Vec::new();
    let mut parity = ParityTotals::default();
    let mut measured_peak_rss_bytes = 0_u64;
    for &beam in &beams {
        for &width in &widths {
            eprintln!(
                "{} {} search: beam={beam} width={width}",
                args.text("--layer")?.to_uppercase(),
                meta.kind.method()
            );
            reset_peak_rss()?;
            let direct = execute_config(
                make_codec(true),
                files,
                meta,
                queries.clone(),
                groundtruth.clone(),
                order.clone(),
                workers,
                warmup_queries,
                width,
                beam,
                true,
                Some(shared_cache.clone()),
            )?;
            measured_peak_rss_bytes = measured_peak_rss_bytes.max(direct.peak_rss_bytes);
            let memory = execute_config(
                make_codec(false),
                files,
                meta,
                queries.clone(),
                groundtruth.clone(),
                order.clone(),
                workers,
                0,
                width,
                beam,
                false,
                None,
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
                storage_mode,
                cache_nodes,
                cache_bytes,
                resident_payload_bytes,
            )?;
            summaries.push(SummaryRow::from_batch(&direct, width, beam));
        }
    }
    trace.flush().map_err(err)?;
    if parity.count > 0
        && (parity.max_recall_delta > 1e-3
            || parity.mean_overlap() < 0.99
            || parity.mean_visited_delta() > 0.01
            || parity.mean_distance_delta() > 0.01)
    {
        return Err(format!(
            "memory/direct parity failed: recall_delta={:.6}, overlap={:.6}, visited_delta={:.6}, distance_delta={:.6}",
            parity.max_recall_delta,
            parity.mean_overlap(),
            parity.mean_visited_delta(),
            parity.mean_distance_delta()
        ));
    }
    let result_path = args.path("--result-json")?;
    let parity_path = result_path.with_extension("parity.json");
    let parity_document = json_object(vec![
        ("schema_version", "1".into()),
        ("method", json_escape(meta.kind.method())),
        ("memory_backend", json_escape("same exported pages in DRAM")),
        (
            "direct_backend",
            json_escape("O_DIRECT + Linux native AIO/libaio"),
        ),
        (
            "max_recall_delta",
            format!("{:.12}", parity.max_recall_delta),
        ),
        (
            "mean_top10_overlap",
            format!("{:.12}", parity.mean_overlap()),
        ),
        (
            "mean_visited_count_relative_delta",
            format!("{:.12}", parity.mean_visited_delta()),
        ),
        (
            "mean_distance_count_relative_delta",
            format!("{:.12}", parity.mean_distance_delta()),
        ),
        ("query_comparisons", parity.count.to_string()),
    ]);
    fs::write(&parity_path, parity_document).map_err(err)?;
    write_search_artifact(
        args,
        files,
        meta,
        storage_mode,
        resident_payload_bytes,
        &summaries,
        &parity,
        &sha256(&parity_path)?,
        &trace_path,
        cache_nodes,
        cache_bytes,
        worker_scratch_bytes,
        measured_peak_rss_bytes,
    )
}

fn run_search_with_codec<C: ResidentCodec + 'static>(
    args: &Args,
    files: &IndexFiles,
    meta: &IndexMeta,
    codec: Arc<C>,
    queries: Arc<Matrix<f32>>,
    groundtruth: Arc<Vec<Vec<u32>>>,
    order: Arc<Vec<usize>>,
) -> Result<()> {
    run_search_with_codec_factory(
        args,
        files,
        meta,
        move |_| codec.clone(),
        meta.resident_bytes,
        "hybrid_disk",
        queries,
        groundtruth,
        order,
    )
}

fn run_search_with_ours(
    args: &Args,
    files: &IndexFiles,
    meta: &IndexMeta,
    codec: Arc<OursResidentCodec>,
    queries: Arc<Matrix<f32>>,
    groundtruth: Arc<Vec<Vec<u32>>>,
    order: Arc<Vec<usize>>,
) -> Result<()> {
    if codec.layout.record_count != meta.base_count || codec.space.dim() != meta.dimension {
        return Err("loaded Ours records do not match shared-graph metadata".into());
    }
    let workers: usize = args.number("--workers")?;
    let warmup_queries: usize = args.number("--warmup-queries")?;
    // This is an admission reservation, not a measured workspace high-water mark.
    // Rust Vec<bool> uses one byte per element; the visited array alone can exceed 8 MiB.
    let worker_scratch_bytes = codec.layout.record_count
        .checked_mul(std::mem::size_of::<bool>())
        .and_then(|bytes| bytes.checked_add(4 * 1024 * 1024))
        .and_then(|bytes| bytes.checked_add(8 * 1024 * 1024))
        .and_then(|bytes| bytes.checked_mul(workers))
        .ok_or_else(|| "Ours workspace reservation overflow".to_string())?;
    let budget_bytes =
        (args.number::<f64>("--search-dram-budget-gib")? * (1_u64 << 30) as f64).floor() as usize;
    let fixed_bytes = meta
        .resident_bytes
        .checked_add(meta.codebook_bytes)
        .and_then(|value| value.checked_add(worker_scratch_bytes))
        .ok_or_else(|| "Ours DRAM accounting overflow".to_string())?;
    if fixed_bytes > budget_bytes {
        return Err(format!(
            "Ours max-resident ablation and worker scratch require {fixed_bytes} bytes, budget is {budget_bytes}"
        ));
    }
    let cache_mode = args.text("--cache-mode")?;
    let mut cache_pages = if cache_mode == "c0" {
        Vec::new()
    } else if cache_mode == "standard" {
        let max_pages_by_budget = (budget_bytes - fixed_bytes) / PAGE_SIZE;
        let max_base_nodes = meta.base_count / 10;
        let max_pages_by_nodes = max_base_nodes / meta.layout.nodes_per_page + 1;
        select_bfs_cache_pages(
            &files.graph_pages,
            &meta.layout,
            max_pages_by_budget.min(max_pages_by_nodes),
        )
        .map_err(err)?
    } else {
        return Err(format!("unsupported cache mode: {cache_mode}"));
    };
    while cached_base_nodes(&cache_pages, &meta.layout, meta.base_count) > meta.base_count / 10 {
        cache_pages.pop();
    }
    let cache_nodes = cached_base_nodes(&cache_pages, &meta.layout, meta.base_count);
    let cache_bytes = cache_pages.len() * PAGE_SIZE;
    if fixed_bytes + cache_bytes > budget_bytes {
        return Err("Ours BFS cache exceeds the declared DRAM budget".into());
    }
    let shared_cache =
        DirectGraphReader::load_shared_cache(&files.graph_pages, &meta.layout, &cache_pages)
            .map_err(err)?;

    let widths = formal_widths(args)?;
    let beams = if args.text("--phase")? == "test" {
        vec![selected_test_beam(args)?]
    } else {
        validation_beams(args)?
    };
    let trace_path = args.path("--query-trace")?;
    if let Some(parent) = trace_path.parent() {
        fs::create_dir_all(parent).map_err(err)?;
    }
    let mut trace = File::create(&trace_path).map_err(err)?;
    let mut summaries = Vec::new();
    let mut parity = ParityTotals::default();
    let mut measured_peak_rss_bytes = 0_u64;
    let ablations = if args.text("--layer")? == "05b" {
        args.text("--ablations")?
            .split(',')
            .filter(|item| !item.is_empty())
            .map(|item| {
                OursAblation::parse(item)
                    .ok_or_else(|| format!("unknown Ours 05B ablation: {item}"))
            })
            .collect::<Result<Vec<_>>>()?
    } else {
        vec![OursAblation::Db1CoalescingReuse]
    };
    for ablation in ablations {
        codec.configure_query_codec(ablation.query_codec());
        for &beam in &beams {
            for &width in &widths {
                eprintln!(
                    "{} {} search: ablation={} beam={beam} width={width}",
                    args.text("--layer")?.to_uppercase(),
                    meta.kind.method(),
                    ablation.as_str()
                );
                reset_peak_rss()?;
                let direct = execute_config_ours(
                    codec.clone(),
                    files,
                    meta,
                    queries.clone(),
                    groundtruth.clone(),
                    order.clone(),
                    workers,
                    warmup_queries,
                    width,
                    beam,
                    ablation,
                    true,
                    Some(shared_cache.clone()),
                )?;
                measured_peak_rss_bytes = measured_peak_rss_bytes.max(direct.peak_rss_bytes);
                if std::env::var("QG05_FAST").ok().as_deref() != Some("1")
                    && args.optional("--parity-mode") != Some("external") {
                    let memory = execute_config_ours(
                        codec.clone(),
                        files,
                        meta,
                        queries.clone(),
                        groundtruth.clone(),
                        order.clone(),
                        workers,
                        0,
                        width,
                        beam,
                        ablation,
                        false,
                        None,
                    )?;
                    for (direct_query, memory_query) in direct.runs.iter().zip(&memory.runs) {
                        parity.add(direct_query, memory_query)?;
                    }
                }
                write_trace_rows(
                    &mut trace,
                    args,
                    &direct,
                    width,
                    beam,
                    "hybrid_disk",
                    cache_nodes,
                    cache_bytes,
                    meta.resident_bytes,
                )?;
                summaries.push(SummaryRow::from_batch(&direct, width, beam));
            }
        }
    }
    trace.flush().map_err(err)?;
    if parity.count > 0
        && (parity.max_recall_delta > 1e-3
            || parity.mean_overlap() < 0.99
            || parity.mean_visited_delta() > 0.01
            || parity.mean_distance_delta() > 0.01)
    {
        return Err(format!(
            "Ours memory/direct parity failed: recall_delta={:.6}, overlap={:.6}, visited_delta={:.6}, distance_delta={:.6}",
            parity.max_recall_delta,
            parity.mean_overlap(),
            parity.mean_visited_delta(),
            parity.mean_distance_delta()
        ));
    }
    let result_path = args.path("--result-json")?;
    let parity_path = result_path.with_extension("parity.json");
    let parity_document = json_object(vec![
        ("schema_version", "1".into()),
        ("method", json_escape(meta.kind.method())),
        (
            "memory_backend",
            json_escape("same exported graph and payload pages in DRAM"),
        ),
        (
            "direct_backend",
            json_escape("O_DIRECT + Linux native AIO/libaio"),
        ),
        (
            "max_recall_delta",
            format!("{:.12}", parity.max_recall_delta),
        ),
        (
            "mean_top10_overlap",
            format!("{:.12}", parity.mean_overlap()),
        ),
        (
            "mean_visited_count_relative_delta",
            format!("{:.12}", parity.mean_visited_delta()),
        ),
        (
            "mean_distance_count_relative_delta",
            format!("{:.12}", parity.mean_distance_delta()),
        ),
        ("query_comparisons", parity.count.to_string()),
    ]);
    fs::write(&parity_path, parity_document).map_err(err)?;
    write_search_artifact(
        args,
        files,
        meta,
        "hybrid_disk",
        meta.resident_bytes,
        &summaries,
        &parity,
        &sha256(&parity_path)?,
        &trace_path,
        cache_nodes,
        cache_bytes,
        worker_scratch_bytes,
        measured_peak_rss_bytes,
    )
}

fn run_search_phase(args: &Args, files: &IndexFiles, meta: &IndexMeta) -> Result<()> {
    let requested_kind = CodecKind::from_method(args.text("--method")?)?;
    if requested_kind != meta.kind {
        return Err(format!(
            "index contains {}, but invocation requests {}",
            meta.kind.method(),
            requested_kind.method()
        ));
    }
    let (source_path, expected_hash) = if requested_kind == CodecKind::Ours {
        (
            args.path("--ours-graph")?,
            args.text("--ours-graph-sha256")?,
        )
    } else {
        (
            args.path("--shared-graph")?,
            args.text("--shared-graph-sha256")?,
        )
    };
    let source_hash = sha256(&source_path)?;
    if source_hash != meta.source_graph_sha256 || source_hash != expected_hash {
        return Err("source graph changed after disk-index export".into());
    }
    let query_order_path = args.path("--query-order")?;
    if sha256(&query_order_path)? != args.text("--query-order-sha256")? {
        return Err("query-order SHA-256 mismatch".into());
    }
    let queries = Arc::new(read_fvecs_matrix(&args.path("--query")?)?);
    let groundtruth = Arc::new(read_ivecs_topk(&args.path("--groundtruth")?, 10)?);
    if queries.nrows() != groundtruth.len() || queries.ncols() != meta.dimension {
        return Err(format!(
            "query/groundtruth/index shape mismatch: queries={}x{}, gt={}, index_dim={}",
            queries.nrows(),
            queries.ncols(),
            groundtruth.len(),
            meta.dimension
        ));
    }
    let order = Arc::new(read_query_order(&query_order_path, queries.nrows())?);
    let storage_mode = args.text("--storage-mode")?;
    match (meta.kind, storage_mode) {
        (CodecKind::Pq, "disk_payload") => {
            run_search_baseline_disk_payload(
                args,
                files,
                meta,
                BaselineKind::Pq,
                queries,
                groundtruth,
                order,
            )
        }
        (CodecKind::Sq, "disk_payload") => {
            run_search_baseline_disk_payload(
                args,
                files,
                meta,
                BaselineKind::Sq,
                queries,
                groundtruth,
                order,
            )
        }
        (CodecKind::Saq, "disk_payload") => {
            run_search_baseline_disk_payload(
                args,
                files,
                meta,
                BaselineKind::Saq,
                queries,
                groundtruth,
                order,
            )
        }
        (CodecKind::Pq, "hybrid_disk") => {
            let codec = Arc::new(PqCodec::load(&files.pivots, &files.codes).map_err(err)?);
            if codec.total() != meta.layout.node_count || codec.full_dim() != meta.dimension {
                return Err("loaded PQ codes do not match graph metadata".into());
            }
            run_search_with_codec(args, files, meta, codec, queries, groundtruth, order)
        }
        (CodecKind::Sq, "hybrid_disk") => {
            let codec = Arc::new(SqCodec::load(&files.sq_prefix).map_err(err)?);
            if codec.total() != meta.layout.node_count || codec.full_dim() != meta.dimension {
                return Err("loaded SQ codes do not match graph metadata".into());
            }
            run_search_with_codec(args, files, meta, codec, queries, groundtruth, order)
        }
        (CodecKind::Saq, "hybrid_disk") => {
            let codec =
                Arc::new(SaqCodec::load(&files.saq_metadata, &files.saq_codes).map_err(err)?);
            if codec.total() != meta.layout.node_count || codec.full_dim() != meta.dimension {
                return Err("loaded SAQ codes do not match graph metadata".into());
            }
            run_search_with_codec(args, files, meta, codec, queries, groundtruth, order)
        }
        (CodecKind::Ours, "hybrid_disk") => {
            let route = args.values.get("--adaptive-route-dir").map(|dir| {
                args.number::<usize>("--adaptive-route-dim").map(|dim| (Path::new(dir), dim))
            }).transpose()?;
            if route.is_some() && args.text("--layer")? != "05c" {
                return Err("adaptive route pilot currently requires --layer 05c".into());
            }
            if route.is_some() && !args.text("--run-id")?.starts_with("native_integration_") {
                return Err("adaptive replacement requires an isolated native_integration_ pilot run".into());
            }
            let keep = args.optional("--adaptive-route-keep").unwrap_or("0")
                .parse::<usize>().map_err(|e| format!("invalid route keep: {e}"))?;
            if keep > 0 && route.is_none() {
                return Err("adaptive-route-keep requires adaptive-route-dir".into());
            }
            let mut codec = OursResidentCodec::load_with_route(
                &files.ours_metadata, &files.ours_sidecar, route).map_err(err)?;
            if let Some(dir) = args.optional("--locality-layout-dir") {
                if route.is_some() || args.text("--layer")? != "05c"
                    || args.text("--cache-mode")? != "c0"
                    || !args.text("--run-id")?.starts_with("native_integration_") {
                    return Err("locality layout requires isolated 05c C0, without adaptive route".into());
                }
                let dir = Path::new(dir);
                for (name, flag) in [("graph_compact.pages", "--locality-combined-sha256"),
                                     ("residual.pages", "--locality-residual-sha256"),
                                     ("id_to_slot.u32", "--locality-mapping-sha256")] {
                    if sha256(&dir.join(name))? != args.text(flag)? {
                        return Err(format!("locality file hash mismatch: {name}"));
                    }
                }
                codec.locality = Some(Arc::new(diskann_fair::disk_port::locality::LocalityLayout::load(
                    dir, codec.layout.record_count, meta.layout.record_bytes,
                    codec.layout.compact_bytes, codec.layout.residual_bytes, meta.layout.max_degree).map_err(err)?));
            }
            codec.route_keep = keep;
            if let Some(dir) = args.optional("--adaptive-route-trace-dir") {
                if route.is_none() || !args.text("--run-id")?.starts_with("native_integration_") {
                    return Err("route tracing requires isolated adaptive diagnostic run".into());
                }
                std::fs::create_dir_all(dir).map_err(err)?;
                codec.route_trace_dir = Some(std::path::PathBuf::from(dir));
                eprintln!("DIAGNOSTIC TRACE ENABLED: throughput is not a benchmark result");
            }
            codec.route_revisit = match args.optional("--adaptive-route-revisit").unwrap_or("0") {
                "0" => false, "1" if route.is_some() => true,
                _ => return Err("route revisit requires adaptive route and value 0 or 1".into()),
            };
            if let Some(path) = args.optional("--adaptive-route-norms") {
                codec.load_route_norms(Path::new(path), args.optional("--adaptive-route-calibrate") == Some("1"))
                    .map_err(err)?;
            }
            codec.route_ratio = args.optional("--adaptive-route-ratio").unwrap_or("0")
                .parse::<f32>().map_err(|e| format!("invalid route ratio: {e}"))?;
            if !codec.route_ratio.is_finite() || codec.route_ratio < 0.0
                || (codec.route_ratio > 0.0 && route.is_none()) {
                return Err("route ratio must be finite, nonnegative and require a route".into());
            }
            let codec = Arc::new(codec);
            let mut measured_meta = meta.clone();
            if args.text("--layer")? == "05c" {
                // 05C keeps no resident full4 ablation image. Count the codec
                // actually loaded, for both the original and adaptive path.
                measured_meta.resident_bytes = codec.resident_bytes();
            }
            if let Some((_, dim)) = route {
                measured_meta.resident_bytes = codec.resident_bytes();
                eprintln!("adaptive_route_mode=resident-replacement-full4-verification route_dim={dim} resident_bytes={} db1_resident_bytes=0 scale_storage=fp32", measured_meta.resident_bytes);
            }
            run_search_with_ours(args, files, &measured_meta, codec, queries, groundtruth, order)
        }
        _ => Err(format!(
            "unsupported storage mode {storage_mode:?} for {}",
            meta.kind.method()
        )),
    }
}

#[allow(clippy::too_many_arguments)]
fn run_search_baseline_disk_payload(
    args: &Args,
    files: &IndexFiles,
    meta: &IndexMeta,
    kind: BaselineKind,
    queries: Arc<Matrix<f32>>,
    groundtruth: Arc<Vec<Vec<u32>>>,
    order: Arc<Vec<usize>>,
) -> Result<()> {
    let (pq_table, sq_quantizer, saq_codec) = match kind {
        BaselineKind::Pq => (
            Some(baseline_disk::load_pq_codebook(&files.pq_codebook).map_err(err)?),
            None,
            None,
        ),
        BaselineKind::Sq => (
            None,
            Some(baseline_disk::load_sq_codebook(&files.sq_codebook).map_err(err)?),
            None,
        ),
        BaselineKind::Saq => (
            None,
            None,
            Some(Arc::new(
                SaqCodec::load_computer_only(&files.saq_metadata).map_err(err)?,
            )),
        ),
    };
    let code_bytes = match kind {
        BaselineKind::Pq => pq_table.as_ref().unwrap().get_num_chunks(),
        BaselineKind::Sq => {
            sq_quantizer.as_ref().unwrap().shift().len().div_ceil(2) + 4
        }
        BaselineKind::Saq => saq_codec.as_ref().unwrap().code_bytes_per_vector(),
    };
    let layout = BaselinePayloadLayout::new(meta.layout.node_count, code_bytes).map_err(err)?;
    let payload_path = match kind {
        BaselineKind::Pq => &files.pq_payload_pages,
        BaselineKind::Sq => &files.sq_payload_pages,
        BaselineKind::Saq => &files.saq_payload_pages,
    };
    let memory_factory =
        BaselinePayloadFactory::memory(payload_path, layout.clone()).map_err(err)?;
    let direct_factory =
        BaselinePayloadFactory::direct(payload_path, layout.clone()).map_err(err)?;
    let resident_payload_bytes = match kind {
        BaselineKind::Pq => pq_table.as_ref().unwrap().get_pq_table().len() * 4,
        BaselineKind::Sq => sq_quantizer.as_ref().unwrap().shift().len() * 4 + 8,
        BaselineKind::Saq => 0,
    };
    let direct_codec = Arc::new(
        BaselineCodec::new(
            kind,
            meta.dimension,
            layout.clone(),
            pq_table.clone(),
            sq_quantizer.clone(),
            saq_codec.clone(),
            direct_factory,
        )
        .map_err(err)?,
    );
    let memory_codec = Arc::new(
        BaselineCodec::new(
            kind,
            meta.dimension,
            layout,
            pq_table,
            sq_quantizer,
            saq_codec,
            memory_factory,
        )
        .map_err(err)?,
    );
    run_search_with_codec_factory(
        args,
        files,
        meta,
        move |direct| {
            if direct {
                direct_codec.clone()
            } else {
                memory_codec.clone()
            }
        },
        resident_payload_bytes,
        "disk_payload",
        queries,
        groundtruth,
        order,
    )
}

fn json_escape(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c.is_control() => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn index_bytes(files: &IndexFiles) -> Result<u64> {
    let kind = load_meta(&files.metadata)?.kind;
    files
        .measured_paths(kind)
        .into_iter()
        .try_fold(0_u64, |sum, path| {
            fs::metadata(path)
                .map(|metadata| sum + metadata.len())
                .map_err(err)
        })
}

fn write_export_artifact(
    args: &Args,
    files: &IndexFiles,
    meta: &IndexMeta,
    build_distance_computations: u64,
) -> Result<()> {
    let result = args.path("--result-json")?;
    if let Some(parent) = result.parent() {
        fs::create_dir_all(parent).map_err(err)?;
    }
    let ours_4bit_payload_bytes = if meta.kind == CodecKind::Ours {
        meta.ours_compact_record_bytes.saturating_mul(meta.ours_record_count)
    } else {
        0
    };
    let ours_8bit_payload_bytes = if meta.kind == CodecKind::Ours {
        meta.ours_compact_record_bytes
            .saturating_add(meta.ours_residual_record_bytes)
            .saturating_mul(meta.ours_record_count)
    } else {
        0
    };
    let ours_adjacency_bytes = fs::metadata(&files.graph_pages)
        .map(|metadata| metadata.len())
        .unwrap_or(0);
    // Ours stores no fp32 payload: rerank uses residual codes, not base vectors.
    let ours_fp32_base_bytes = 0_u64;
    let document = format!(
        concat!(
            "{{\n  \"schema_version\": 2,\n  \"status\": \"done\",\n",
            "  \"layer\": {},\n  \"dataset\": {},\n  \"method\": {},\n",
            "  \"storage_mode\": {},\n  \"phase\": \"export\",\n",
            "  \"run_id\": {},\n  \"repeat_id\": {},\n  \"workers\": {},\n",
            "  \"cache_mode\": {},\n  \"search_dram_budget_gib\": {},\n",
            "  \"source_suite\": {},\n  \"source_kernel\": {},\n",
            "  \"port_kind\": {},\n  \"implementation_fingerprint\": {},\n",
            "  \"native_binary_sha256\": {},\n  \"input_manifest_sha256\": {},\n",
            "  \"query_split_sha256\": {},\n  \"query_order_sha256\": {},\n",
            "  \"query_order_seed\": {},\n  \"warmup_queries\": {},\n",
            "  \"shared_graph_sha256\": {},\n  \"source_graph_sha256\": {},\n",
            "  \"graph_role\": {},\n  \"source_index_manifest_sha256\": {},\n",
            "  \"index_path\": {},\n  \"index_size_mb\": {:.9},\n",
            "  \"base_count\": {},\n  \"dimension\": {},\n  \"resident_bytes\": {},\n",
            "  \"codebook_bytes\": {},\n  \"worker_scratch_bytes\": 0,\n",
            "  \"ours_4bit_payload_bytes\": {},\n  \"ours_8bit_payload_bytes\": {},\n",
            "  \"ours_adjacency_bytes\": {},\n  \"ours_fp32_base_bytes\": {},\n",
            "  \"cache_bytes\": 0,\n  \"cache_nodes\": 0,\n  \"peak_rss_bytes\": 0,\n",
            "  \"whole_graph_in_memory\": false,\n  \"whole_payload_in_memory\": false,\n",
            "  \"direct_io\": true,\n  \"native_aio\": true,\n",
            "  \"io_backend\": \"linux_native_aio_odirect\",\n  \"page_size\": {},\n",
            "  \"formal_ready\": true,\n  \"implementation_parity\": \"not_run_export\",\n",
            "  \"build_distance_computations\": {},\n",
            "  \"ablations\": {},\n",
            "  \"summary_rows\": []\n}}\n"
        ),
        json_escape(args.text("--layer")?),
        json_escape(args.text("--dataset")?),
        json_escape(meta.kind.method()),
        json_escape(args.text("--storage-mode")?),
        json_escape(args.text("--run-id")?),
        args.number::<usize>("--repeat-id")?,
        args.number::<usize>("--workers")?,
        json_escape(args.text("--cache-mode")?),
        args.number::<f64>("--search-dram-budget-gib")?,
        json_escape(source_suite(args)?),
        json_escape(meta.kind.source_kernel()),
        json_escape(PORT_KIND),
        json_escape(args.text("--implementation-fingerprint")?),
        json_escape(args.text("--native-binary-sha256")?),
        json_escape(args.text("--input-manifest-sha256")?),
        json_escape(args.text("--query-split-sha256")?),
        json_escape(args.text("--query-order-sha256")?),
        args.number::<u64>("--query-order-seed")?,
        args.number::<usize>("--warmup-queries")?,
        json_escape(if meta.graph_role == "shared_baseline" {
            &meta.source_graph_sha256
        } else {
            ""
        }),
        json_escape(&meta.source_graph_sha256),
        json_escape(&meta.graph_role),
        json_escape(&sha256(&files.metadata)?),
        json_escape(&files.root.to_string_lossy()),
        index_bytes(files)? as f64 / (1024.0 * 1024.0),
        meta.base_count,
        meta.dimension,
        meta.resident_bytes,
        meta.codebook_bytes,
        ours_4bit_payload_bytes,
        ours_8bit_payload_bytes,
        ours_adjacency_bytes,
        ours_fp32_base_bytes,
        PAGE_SIZE,
        build_distance_computations,
        if meta.kind == CodecKind::Ours && args.text("--layer")? == "05b" {
            "[\"full4-resident/no-gate\",\"db1-resident/full4-on-ssd\",\"db1+coalescing\",\"db1+coalescing+reuse\"]"
        } else {
            "[]"
        },
    );
    fs::write(result, document).map_err(err)
}

fn err(value: impl std::fmt::Display) -> String {
    value.to_string()
}

fn run() -> Result<()> {
    let args = Args::parse()?;
    args.validate_contract()?;
    let kind = CodecKind::from_method(args.text("--method")?)?;
    let files = IndexFiles::new(args.path("--disk-index-dir")?);
    match args.text("--phase")? {
        "export" => {
            let (meta, build_distance_computations) = export_index(&args, &files)?;
            write_export_artifact(&args, &files, &meta, build_distance_computations)
        }
        "validate" | "validation" | "test" => {
            if !files.complete(kind, args.text("--storage-mode")?) {
                return Err(format!(
                    "{} {} index is incomplete under {}; run --phase export first",
                    args.text("--layer")?.to_uppercase(),
                    kind.method(),
                    files.root.display()
                ));
            }
            let meta = load_meta(&files.metadata)?;
            run_search_phase(&args, &files, &meta)
        }
        phase => Err(format!("unsupported phase: {phase}")),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("ERROR: {error}");
        std::process::exit(2);
    }
}
