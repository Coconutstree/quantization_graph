use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use crate::config::RunContext;
use crate::payload::PreparedPayload;

pub struct OutputPaths {
    pub raw_log: PathBuf,
    pub raw_csv: PathBuf,
    pub manifest_csv: PathBuf,
}

pub fn create_output_dirs(ctx: &RunContext, method: &str) -> Result<OutputPaths, String> {
    let dataset_root = ctx.out_root.join("02_diskann_fair").join(&ctx.dataset.name);
    let raw_dir = dataset_root.join("logs").join(method);
    let csv_dir = dataset_root.join("csv");
    let manifest_dir = dataset_root.join("manifests");
    let shared_graph_dir = dataset_root.join("indexes/shared_graph");

    for dir in [&raw_dir, &csv_dir, &manifest_dir, &shared_graph_dir] {
        fs::create_dir_all(dir).map_err(|err| format!("mkdir {}: {err}", dir.display()))?;
    }

    Ok(OutputPaths {
        raw_log: raw_dir.join(format!(
            "{}_{}_R{}_Lbuild{}.log",
            ctx.dataset.name, method, ctx.config.max_degree, ctx.config.build_beam
        )),
        raw_csv: csv_dir.join("diskann_fair_raw.csv"),
        manifest_csv: manifest_dir.join("02_diskann_fair_manifest.csv"),
    })
}

pub fn write_raw_log(
    ctx: &RunContext,
    payload: &PreparedPayload,
    paths: &OutputPaths,
) -> Result<(), String> {
    let mut file = fs::File::create(&paths.raw_log)
        .map_err(|err| format!("create {}: {err}", paths.raw_log.display()))?;
    writeln!(file, "experiment_profile=02_diskann_payload_fair").map_err(to_string)?;
    writeln!(file, "suite=02_diskann_fair").map_err(to_string)?;
    writeln!(file, "dataset={}", ctx.dataset.name).map_err(to_string)?;
    writeln!(file, "method={}", payload.method).map_err(to_string)?;
    writeln!(file, "status={}", payload.status).map_err(to_string)?;
    writeln!(file, "has_real_metrics={}", payload.has_real_metrics).map_err(to_string)?;
    writeln!(file, "metric=L2").map_err(to_string)?;
    writeln!(file, "base_count={}", ctx.dataset.base_count).map_err(to_string)?;
    writeln!(file, "query_count={}", ctx.dataset.query_count).map_err(to_string)?;
    writeln!(file, "dimension={}", ctx.dataset.dimension).map_err(to_string)?;
    writeln!(file, "groundtruth_k={}", ctx.dataset.gt_k).map_err(to_string)?;
    let is_ours = payload.method.eq_ignore_ascii_case("Ours");
    writeln!(
        file,
        "graph_type={}",
        if is_ours { "DiskANN3/Vamana" } else { "DiskANN/Vamana" }
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "graph_build_distance={}",
        payload.graph_build_distance
    )
    .map_err(to_string)?;
    writeln!(file, "graph_build_mode={}", payload.graph_build_mode).map_err(to_string)?;
    writeln!(
        file,
        "shared_graph_build_time_ms={}",
        opt_f64(payload.shared_graph_build_time_ms)
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "search_distance={}",
        if is_ours {
            "Float32_to_ExRaBitQ4_plus_residual4_block16"
        } else {
            "quantized_4bit_payload"
        }
    )
    .map_err(to_string)?;
    if is_ours {
        writeln!(file, "graph_build_builder=native_vamana_bulk_grouped_refine")
            .map_err(to_string)?;
        writeln!(file, "graph_refine_passes={}", ctx.config.refine_passes).map_err(to_string)?;
        writeln!(file, "framework=diskann3_provider_search_prune").map_err(to_string)?;
        writeln!(file, "provider=OursExRaBitQ4").map_err(to_string)?;
        writeln!(file, "paper_prune=active").map_err(to_string)?;
        writeln!(file, "paper_epsilon0=1.9").map_err(to_string)?;
        writeln!(file, "rerank_candidates={}", ctx.config.rerank_candidates)
            .map_err(to_string)?;
        writeln!(file, "residual_bits=4").map_err(to_string)?;
        writeln!(file, "residual_block_size=16").map_err(to_string)?;
        writeln!(file, "residual_scale_mode=mse").map_err(to_string)?;
        writeln!(file, "residual_scale_storage=fp16").map_err(to_string)?;
        writeln!(file, "legacy_hnswlib_runtime=0").map_err(to_string)?;
    }
    writeln!(file, "max_degree={}", ctx.config.max_degree).map_err(to_string)?;
    writeln!(file, "build_beam={}", ctx.config.build_beam).map_err(to_string)?;
    writeln!(file, "M={}", ctx.config.max_degree).map_err(to_string)?;
    writeln!(file, "ef={}", ctx.config.build_beam).map_err(to_string)?;
    writeln!(file, "efConstruction={}", ctx.config.build_beam).map_err(to_string)?;
    writeln!(file, "search_param_name=efSearch").map_err(to_string)?;
    writeln!(
        file,
        "efSearch_values={}",
        ctx.config
            .search_list_sizes
            .iter()
            .map(|value| value.to_string())
            .collect::<Vec<_>>()
            .join(",")
    )
    .map_err(to_string)?;
    writeln!(file, "alpha={}", ctx.config.alpha).map_err(to_string)?;
    writeln!(
        file,
        "legacy_equivalent=M{}_ef{}",
        ctx.config.max_degree, ctx.config.build_beam
    )
    .map_err(to_string)?;
    writeln!(file, "search_beam_width={}", ctx.config.search_beam_width).map_err(to_string)?;
    writeln!(file, "threads={}", ctx.config.threads).map_err(to_string)?;
    writeln!(file, "build_threads={}", ctx.config.build_threads).map_err(to_string)?;
    writeln!(file, "repeats={}", ctx.config.repeats).map_err(to_string)?;
    writeln!(file, "seed={}", ctx.config.seed).map_err(to_string)?;
    writeln!(
        file,
        "accuracy_metrics=mean_relative_error,p95_relative_error,mean_absolute_error,top10_overlap,pairwise_flip_rate_top10"
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "nominal_bits_per_dim={}",
        payload.nominal_bits_per_dim
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "actual_bytes_per_vector={}",
        opt_f64(payload.actual_bytes_per_vector)
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "code_bytes_per_vector={}",
        opt_f64(payload.code_bytes_per_vector)
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "metadata_bytes_per_vector={}",
        opt_f64(payload.metadata_bytes_per_vector)
    )
    .map_err(to_string)?;
    writeln!(file, "fp32_base_bytes={}", opt_u64(payload.fp32_base_bytes))
        .map_err(to_string)?;
    writeln!(file, "codebook_bytes={}", opt_u64(payload.codebook_bytes)).map_err(to_string)?;
    writeln!(file, "base_path={}", ctx.dataset.base_path.display()).map_err(to_string)?;
    writeln!(file, "query_path={}", ctx.dataset.query_path.display()).map_err(to_string)?;
    writeln!(file, "gt_path={}", ctx.dataset.gt_path.display()).map_err(to_string)?;
    writeln!(file, "payload_path={}", payload.payload_path.display()).map_err(to_string)?;
    writeln!(
        file,
        "payload_json_path={}",
        payload.payload_json_path.display()
    )
    .map_err(to_string)?;
    writeln!(file, "note={}", payload.note).map_err(to_string)?;
    if !payload.has_real_metrics {
        writeln!(file, "result_status=no_real_metrics").map_err(to_string)?;
        writeln!(
            file,
            "reason=adapter is only wired as a quantization slot; it has not run DiskANN graph build or search"
        )
        .map_err(to_string)?;
        writeln!(
            file,
            "next_action=replace experiments/02_diskann_fair/src/payload/{}.rs with a real DiskANN adapter before using this file as an experiment result",
            payload.method.to_ascii_lowercase()
        )
        .map_err(to_string)?;
        return Ok(());
    }
    writeln!(
        file,
        "build_stage={} us={} status=done",
        if is_ours { "train_center" } else { "train_quantizer" },
        ms_to_us(payload.train_time_ms)
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "build_stage=payload_encode us={} count={} status=done",
        ms_to_us(payload.encode_time_ms),
        ctx.dataset.base_count
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "build_stage=payload_import us=0 status=in_memory_provider"
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "build_stage=graph_build us={} status=done",
        ms_to_us(payload.graph_build_time_ms)
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "build_total_us={} status=done",
        ms_to_us(payload.build_time_ms)
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "Graph construction time: {:.6} seconds status=done",
        payload.graph_build_time_ms.unwrap_or(0.0) / 1000.0
    )
    .map_err(to_string)?;
    if !is_ours {
        writeln!(
            file,
            "Shared graph construction time: {:.6} seconds status=shared_fp32_vamana",
            payload.shared_graph_build_time_ms.unwrap_or(0.0) / 1000.0
        )
        .map_err(to_string)?;
    }
    writeln!(
        file,
        "Build time: {:.6} seconds status=done",
        payload.build_time_ms.unwrap_or(0.0) / 1000.0
    )
    .map_err(to_string)?;
    let index_bytes = payload.index_bytes.unwrap_or(0);
    let auxiliary_bytes = payload.auxiliary_bytes.unwrap_or(0);
    let residual_bytes = payload.residual_bytes.unwrap_or(0);
    let fp32_base_bytes = payload.fp32_base_bytes.unwrap_or(0);
    let total_bytes = index_bytes + auxiliary_bytes + residual_bytes + fp32_base_bytes;
    writeln!(
        file,
        "Index storage size: {:.2} MB (index={:.2} MB, auxiliary={:.2} MB, residual={:.2} MB, fp32_base={:.2} MB, total_bytes={}) status=computed",
        total_bytes as f64 / 1_000_000.0,
        index_bytes as f64 / 1_000_000.0,
        auxiliary_bytes as f64 / 1_000_000.0,
        residual_bytes as f64 / 1_000_000.0,
        fp32_base_bytes as f64 / 1_000_000.0,
        total_bytes
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "storage_breakdown index_bytes={index_bytes} auxiliary_bytes={auxiliary_bytes} residual_bytes={residual_bytes} fp32_base_bytes={fp32_base_bytes} total_bytes={total_bytes}"
    )
    .map_err(to_string)?;
    writeln!(
        file,
        "Peak RSS: {} MB status=measured",
        opt_f64(payload.peak_rss_mb)
    )
    .map_err(to_string)?;
    writeln!(file, "search_list_size recall latency_mean_us qps latency_p95_us prepare_us traverse_us rerank_us neighbor_fetch_us visited_mark_us paper_batch_us flush_us visited_nodes distance_computations mean_relative_error p95_relative_error mean_absolute_error top10_overlap pairwise_flip_rate_top10 payload_bytes_read avg_bits_read refine_calls paper_checked paper_would_prune paper_msb_kernel_calls paper_remaining_kernel_calls ffi_calls_per_query status").map_err(to_string)?;
    for result in &payload.search_results {
        writeln!(
            file,
            "{} {:.6} {:.6} us search_list_size={} efSearch={} search_beam_width={} total_us_per_query={:.6} qps={:.6} p95_us={:.6} prepare_us={} traverse_us={} rerank_us={} neighbor_fetch_us={} visited_mark_us={} paper_batch_us={} flush_us={} visited_nodes={} distance_computations={} mean_relative_error={} p95_relative_error={} mean_absolute_error={} top10_overlap={} pairwise_flip_rate_top10={} payload_bytes_read=NaN avg_bits_read=NaN refine_calls=0 paper_checked={} paper_would_prune={} paper_msb_kernel_calls={} paper_remaining_kernel_calls={} ffi_calls_per_query={} status={}",
            result.search_list_size,
            result.recall,
            result.latency_mean_us,
            result.search_list_size,
            result.search_list_size,
            ctx.config.search_beam_width,
            result.latency_mean_us,
            result.qps,
            result.latency_p95_us,
            fmt_metric(result.prepare_us),
            fmt_metric(result.traverse_us),
            fmt_metric(result.rerank_us),
            fmt_metric(result.neighbor_fetch_us),
            fmt_metric(result.visited_mark_us),
            fmt_metric(result.paper_batch_us),
            fmt_metric(result.flush_us),
            fmt_metric(result.visited_nodes),
            fmt_metric(result.distance_computations),
            fmt_metric(result.mean_relative_error),
            fmt_metric(result.p95_relative_error),
            fmt_metric(result.mean_absolute_error),
            fmt_metric(result.top10_overlap),
            fmt_metric(result.pairwise_flip_rate_top10),
            fmt_metric(result.paper_checked),
            fmt_metric(result.paper_would_prune),
            fmt_metric(result.paper_msb_kernel_calls),
            fmt_metric(result.paper_remaining_kernel_calls),
            fmt_metric(result.ffi_calls_per_query),
            result.status
        )
        .map_err(to_string)?;
    }
    Ok(())
}

pub fn append_raw_csv(
    ctx: &RunContext,
    payload: &PreparedPayload,
    paths: &OutputPaths,
) -> Result<(), String> {
    if !payload.has_real_metrics {
        return Ok(());
    }

    let needs_header = !paths.raw_csv.exists();
    let expected_header = "suite,dataset,method,status,graph_build_distance,graph_build_mode,shared_graph_build_time_ms,metric,k,nominal_bpd,actual_bytes_per_vector,codebook_bytes,index_size_mb,index_bytes,auxiliary_bytes,residual_bytes,fp32_base_bytes,peak_rss_mb,build_time_ms,graph_build_time_ms,train_time_ms,encode_time_ms,max_degree,build_beam,alpha,search_param_name,search_param_value,search_beam_width,recall,qps,latency_mean_us,latency_p95_us,prepare_us,traverse_us,rerank_us,neighbor_fetch_us,visited_mark_us,paper_batch_us,flush_us,visited_nodes,distance_calls,mean_relative_error,p95_relative_error,mean_absolute_error,top10_overlap,pairwise_flip_rate_top10,payload_bytes_read,avg_bits_read,refine_calls,threads,repeat_id,seed,log_path,note";
    let header_matches = if needs_header {
        true
    } else {
        fs::read_to_string(&paths.raw_csv)
            .ok()
            .and_then(|text| text.lines().next().map(str::to_string))
            .map(|header| header == expected_header)
            .unwrap_or(false)
    };
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&paths.raw_csv)
        .map_err(|err| format!("open {}: {err}", paths.raw_csv.display()))?;
    if needs_header || !header_matches {
        if !needs_header {
            writeln!(file, "# schema_reset=02_diskann_fair_real_metrics").map_err(to_string)?;
        }
        writeln!(file, "{expected_header}").map_err(to_string)?;
    }
    for result in &payload.search_results {
        let row = vec![
            "02_diskann_fair".to_string(),
            ctx.dataset.name.clone(),
            payload.method.clone(),
            payload.status.to_string(),
            payload.graph_build_distance.clone(),
            payload.graph_build_mode.clone(),
            opt_f64(payload.shared_graph_build_time_ms),
            "L2".to_string(),
            "10".to_string(),
            "4".to_string(),
            opt_f64(payload.actual_bytes_per_vector),
            opt_u64(payload.codebook_bytes),
            opt_f64(payload.index_size_mb),
            opt_u64(payload.index_bytes),
            opt_u64(payload.auxiliary_bytes),
            opt_u64(payload.residual_bytes),
            opt_u64(payload.fp32_base_bytes),
            opt_f64(payload.peak_rss_mb),
            opt_f64(payload.build_time_ms),
            opt_f64(payload.graph_build_time_ms),
            opt_f64(payload.train_time_ms),
            opt_f64(payload.encode_time_ms),
            ctx.config.max_degree.to_string(),
            ctx.config.build_beam.to_string(),
            ctx.config.alpha.to_string(),
            "efSearch".to_string(),
            result.search_list_size.to_string(),
            ctx.config.search_beam_width.to_string(),
            format!("{:.6}", result.recall),
            format!("{:.6}", result.qps),
            format!("{:.6}", result.latency_mean_us),
            format!("{:.6}", result.latency_p95_us),
            fmt_metric(result.prepare_us),
            fmt_metric(result.traverse_us),
            fmt_metric(result.rerank_us),
            fmt_metric(result.neighbor_fetch_us),
            fmt_metric(result.visited_mark_us),
            fmt_metric(result.paper_batch_us),
            fmt_metric(result.flush_us),
            fmt_metric(result.visited_nodes),
            fmt_metric(result.distance_computations),
            fmt_metric(result.mean_relative_error),
            fmt_metric(result.p95_relative_error),
            fmt_metric(result.mean_absolute_error),
            fmt_metric(result.top10_overlap),
            fmt_metric(result.pairwise_flip_rate_top10),
            "NaN".to_string(),
            "NaN".to_string(),
            "0".to_string(),
            ctx.config.threads.to_string(),
            "aggregate".to_string(),
            ctx.config.seed.to_string(),
            paths.raw_log.display().to_string(),
            csv_quote(&payload.note),
        ];
        writeln!(file, "{}", row.join(",")).map_err(to_string)?;
    }
    Ok(())
}

pub fn append_manifest(
    ctx: &RunContext,
    payload: &PreparedPayload,
    paths: &OutputPaths,
) -> Result<(), String> {
    let needs_header = !paths.manifest_csv.exists();
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&paths.manifest_csv)
        .map_err(|err| format!("open {}: {err}", paths.manifest_csv.display()))?;
    if needs_header {
        writeln!(file, "suite,dataset,method,status,implementation,source_path,max_degree,build_beam,alpha,nominal_bpd,payload_path,raw_log,note").map_err(to_string)?;
    }
    writeln!(
        file,
        "02_diskann_fair,{},{},{},adapter_slot,experiments/02_diskann_fair,{},{},{},4,{},{},\"{}\"",
        ctx.dataset.name,
        payload.method,
        payload.status,
        ctx.config.max_degree,
        ctx.config.build_beam,
        ctx.config.alpha,
        payload.payload_path.display(),
        paths.raw_log.display(),
        payload.note.replace('"', "'"),
    )
    .map_err(to_string)?;
    Ok(())
}

pub fn ensure_parent(path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| format!("mkdir {}: {err}", parent.display()))?;
    }
    Ok(())
}

fn opt_f64(value: Option<f64>) -> String {
    value
        .map(|item| format!("{item:.6}"))
        .unwrap_or_else(|| "".to_string())
}

fn fmt_metric(value: f64) -> String {
    if value.is_nan() {
        "NaN".to_string()
    } else {
        format!("{value:.6}")
    }
}

fn opt_u64(value: Option<u64>) -> String {
    value
        .map(|item| item.to_string())
        .unwrap_or_else(|| "".to_string())
}

fn ms_to_us(value: Option<f64>) -> u64 {
    value.map(|ms| (ms * 1000.0).round() as u64).unwrap_or(0)
}

fn csv_quote(value: &str) -> String {
    format!("\"{}\"", value.replace('"', "\"\""))
}

fn to_string(err: std::io::Error) -> String {
    err.to_string()
}
