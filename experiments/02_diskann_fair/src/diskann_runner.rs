use std::fs::{self, OpenOptions};
use std::io::{BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::Instant;

use diskann::graph::{
    self,
    glue::{DefaultSearchStrategy, MultiInsertStrategy, SearchAccessor, SearchStrategy},
    AdjacencyList, IdDistance,
};
use diskann::provider::{DataProvider, DefaultContext, SetElement};
use diskann_providers::index::diskann_async;
use diskann_providers::model::graph::provider::async_::{
    common::{
        CreateVectorStore, FullPrecision, Hybrid, NoDeletes, NoStore, Quantized, SetElementHelper,
    },
    inmem::{self, DefaultProviderParameters, SetStartPoints},
    SimpleNeighborProviderAsync,
};
use diskann_providers::storage::FileStorageProvider;
use diskann_quantization::scalar::train::ScalarQuantizationParameters;
use diskann_quantization::scalar::CompensatedVectorRef;
use diskann_utils::views::Matrix;
use diskann_vector::distance::Metric;
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

use crate::config::RunContext;
use crate::ours_diskann::{
    OursPaperSearchStats, OursPrecursor, RabitqSpace, build_ours_vamana_graph,
    encode_ours_payloads, export_ours_paper_sidecar,
    search_ours_paper_active,
};
use crate::payload::{AdapterStatus, PreparedPayload, SearchResult};

const K: usize = 10;

type DiskAnnQuantProvider<Q> = inmem::DefaultProvider<
    inmem::FullPrecisionStore<f32>,
    <Q as CreateVectorStore>::Target,
    NoDeletes,
>;

type QuantOnlyProvider<Q> =
    inmem::DefaultProvider<NoStore, <Q as CreateVectorStore>::Target, NoDeletes>;

pub fn run_pq4(ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
    let dim = ctx.dataset.dimension as usize;
    if dim % 2 != 0 {
        return Err(format!(
            "PQ 4bpd requires even dimension when DiskANN PQ stores one 8-bit code per chunk; got dim={dim}"
        ));
    }
    let num_pq_chunks = dim / 2;
    let code_bytes_per_vector = num_pq_chunks as f64;
    let codebook_bytes = (256_u64 * dim as u64 * std::mem::size_of::<f32>() as u64) as u64;

    run_with_provider(
        ctx,
        progress_log,
        "PQ",
        "DiskANN FixedChunkPQTable with num_pq_chunks=D/2. Graph build uses float32 FullPrecision; search uses the 4bit-equivalent PQ payload through inmem/product.rs.",
        code_bytes_per_vector,
        code_bytes_per_vector,
        Some(codebook_bytes),
        |data, rng, pool, progress| {
            progress("train_quantizer", "start")?;
            let start = Instant::now();
            let table =
                diskann_async::train_pq(data.as_view(), num_pq_chunks, rng, pool).map_err(err)?;
            progress("train_quantizer", "done")?;
            Ok((
                table,
                start.elapsed().as_secs_f64() * 1000.0,
                Hybrid::new(None),
            ))
        },
    )
}

pub fn run_sq4(ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
    let dim = ctx.dataset.dimension as usize;
    let bytes = CompensatedVectorRef::<4>::canonical_bytes(dim) as f64;
    let code_bytes = ((dim * 4).div_ceil(8)) as f64;
    let metadata_bytes = bytes - code_bytes;

    run_with_provider(
        ctx,
        progress_log,
        "SQ",
        "DiskANN scalar quantization WithBits<4>. Graph build uses float32 FullPrecision; search uses the 4bit scalar payload through inmem/scalar.rs.",
        bytes,
        code_bytes,
        None,
        |data, _rng, _pool, progress| {
            progress("train_quantizer", "start")?;
            let start = Instant::now();
            let quantizer = ScalarQuantizationParameters::default().train(data.as_view());
            progress("train_quantizer", "done")?;
            Ok((
                inmem::WithBits::<4>::new(quantizer),
                start.elapsed().as_secs_f64() * 1000.0,
                Quantized,
            ))
        },
    )
    .map(|mut payload| {
        payload.metadata_bytes_per_vector = Some(metadata_bytes);
        payload
    })
}

pub fn run_saq4(ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
    let dim = ctx.dataset.dimension as usize;
    let code_bytes = ((dim * 4).div_ceil(8)) as f64;

    run_with_provider(
        ctx,
        progress_log,
        "SAQ",
        "DiskANN spherical quantization Impl<4>. Graph build uses float32 FullPrecision; search uses the 4bit spherical payload through inmem/spherical.rs.",
        code_bytes,
        code_bytes,
        None,
        |data, rng, _pool, progress| {
            progress("train_quantizer", "start")?;
            let start = Instant::now();
            let quantizer = diskann_quantization::spherical::SphericalQuantizer::train(
                data.as_view(),
                diskann_quantization::algorithms::transforms::TransformKind::PaddingHadamard {
                    target_dim:
                        diskann_quantization::algorithms::transforms::TargetDim::Natural,
                },
                Metric::L2.try_into().map_err(err)?,
                diskann_quantization::spherical::PreScale::ReciprocalMeanNorm,
                rng,
                diskann_quantization::alloc::GlobalAllocator,
            )
            .map_err(err)?;
            let plan = diskann_quantization::spherical::iface::Impl::<4>::new(quantizer)
                .map_err(err)?;
            progress("train_quantizer", "done")?;
            Ok((
                plan,
                start.elapsed().as_secs_f64() * 1000.0,
                inmem::spherical::Quantized::search(
                    diskann_quantization::spherical::iface::QueryLayout::ScalarQuantized,
                ),
            ))
        },
    )
}

pub fn run_ours_exrabitq4(
    ctx: &RunContext,
    progress_log: &Path,
) -> Result<PreparedPayload, String> {
    const METHOD: &str = "Ours";
    const NOTE: &str = concat!(
        "Ours ExRaBitQ4 block16 provider. Graph build and search run through DiskANN3 ",
        "in-memory provider/prune storage. Encode, ExRaBitQ4 symmetric build distance, ",
        "paper-prune active Float32-to-ExRaBitQ4 query traversal, and residual4 block16 ",
        "mse/fp16 rerank call the existing RaBitQSpace kernels through a native bridge. ",
        "Graph construction uses ExRaBitQ4 symmetric distance on 4-bit payloads; the index ",
        "stores no fp32 base vectors (fp32_base_bytes=0), unlike fp32-graph baselines."
    );

    reset_progress(progress_log, ctx, METHOD)?;
    append_progress(progress_log, "load_data", "start")?;
    let load_start = Instant::now();
    let data = Arc::new(read_fvecs_matrix(&ctx.dataset.base_path)?);
    let queries = read_fvecs_matrix(&ctx.dataset.query_path)?;
    let groundtruth = read_ivecs_topk(&ctx.dataset.gt_path, K)?;
    append_progress(
        progress_log,
        "load_data",
        &format!("done ms={:.3}", load_start.elapsed().as_secs_f64() * 1000.0),
    )?;

    append_progress(progress_log, "train_center", "start residual_block_size=16")?;
    let train_start = Instant::now();
    let centroid_count = ctx.config.centroid_count;
    let sample_count = std::cmp::min(ctx.config.centroid_train_samples, data.nrows());
    let centroids = if centroid_count == 1 {
        train_global_center_matrix(data.as_ref(), sample_count, 100)
    } else {
        train_kmeans_centroids_matrix(data.as_ref(), sample_count, centroid_count, 100, 6)?
    };
    let space = RabitqSpace::new_with_centroids(data.ncols(), 100, centroid_count, &centroids)?;
    if space.residual_bits() != 4 || space.residual_block_size() != 16 {
        return Err(format!(
            "Ours ExRaBitQ config drifted: residual_bits={} residual_block_size={}",
            space.residual_bits(),
            space.residual_block_size()
        ));
    }
    let train_time_ms = train_start.elapsed().as_secs_f64() * 1000.0;
    append_progress(
        progress_log,
        "train_center",
        &format!("done ms={train_time_ms:.3} residual_bits=4 residual_block_size=16 centroid_count={centroid_count}"),
    )?;

    let total_build_start = Instant::now();
    let config = graph::config::Builder::new_with(
        ctx.config.max_degree,
        graph::config::MaxDegree::default_slack(),
        ctx.config.build_beam,
        Metric::L2.into(),
        |builder| {
            builder.alpha(ctx.config.alpha);
            builder.max_minibatch_par(ctx.config.build_threads);
            builder.intra_batch_candidates(
                graph::config::IntraBatchCandidates::new(ctx.config.intra_batch_candidates),
            );
        },
    )
    .build()
    .map_err(err)?;
    let params = DefaultProviderParameters::simple(
        data.nrows(),
        data.ncols(),
        Metric::L2,
        config.max_degree_u32().get(),
    );
    let index = diskann_async::new_quant_index::<f32, _, _>(
        config,
        params,
        OursPrecursor::new(space.clone()),
        NoDeletes,
    )
    .map_err(err)?;

    let start_index = shared_start_index(ctx.config.seed, data.nrows());
    index
        .provider()
        .set_start_points(std::iter::once(data.row(start_index)))
        .map_err(err)?;

    append_progress(progress_log, "payload_encode", "start target=diskann_provider")?;
    let encode_start = Instant::now();
    encode_ours_payloads(index.provider(), data.as_ref(), ctx.config.build_threads).map_err(err)?;
    let payload_encode_time_ms = encode_start.elapsed().as_secs_f64() * 1000.0;
    append_progress(
        progress_log,
        "payload_encode",
        &format!(
            "done ms={payload_encode_time_ms:.3} target=diskann_provider",
        ),
    )?;

    append_progress(
        progress_log,
        "graph_build",
        &format!(
            "start graph_build_distance=ExRaBitQ4_symmetric builder=native_vamana_bulk_grouped_refine refine_passes={} residual_block_size=16",
            ctx.config.refine_passes
        ),
    )?;
    let graph_start = Instant::now();
    let graph_build_mode: String;
    let total_edges: u64;
    if let Some(graph_file) = &ctx.graph_file {
        if !graph_file.exists() {
            return Err(format!(
                "Ours graph file not found: {}",
                graph_file.display()
            ));
        }
        append_progress(
            progress_log,
            "graph_load",
            &format!("path={}", graph_file.display()),
        )?;
        copy_shared_graph(graph_file, index.provider().neighbors(), data.nrows())?;
        export_ours_paper_sidecar(index.provider(), data.nrows()).map_err(err)?;
        graph_build_mode = "reused_graph".to_string();
        total_edges = count_diskann_graph_edges(graph_file)? as u64;
    } else {
        let heartbeat = Heartbeat::start(progress_log.to_path_buf(), "graph_build");
        total_edges = build_ours_vamana_graph(
            index.provider(),
            ctx.config.max_degree,
            ctx.config.build_beam,
            ctx.config.alpha,
            ctx.config.search_beam_width,
            4096,
            ctx.config.refine_passes,
            ctx.config.prune_candidate_cap,
            ctx.config.build_early_stop_hops,
        )
        .map_err(err)?;
        heartbeat.stop()?;
        graph_build_mode = "built_in_run".to_string();
        {
            // Export the built adjacency in canonical DiskANN format so the 03
            // decomposition can run the uniform fp32 reference search on Ours' own
            // graph (graph-quality measurement).
            let graph_dir = ctx
                .out_root
                .join(&ctx.dataset.name)
                .join("indexes/02_diskann_fair")
                .join(METHOD);
            fs::create_dir_all(&graph_dir).map_err(err)?;
            let graph_path = graph_dir.join(format!(
                "{}_{}_R{}_Lbuild{}.graph.bin",
                ctx.dataset.name, METHOD, ctx.config.max_degree, ctx.config.build_beam
            ));
            let bytes = index
                .provider()
                .neighbors()
                .save_direct(
                    &FileStorageProvider,
                    start_index as u32,
                    graph_path.to_string_lossy().as_ref(),
                )
                .map_err(err)? as u64;
            append_progress(
                progress_log,
                "graph_save",
                &format!("path={} bytes={bytes}", graph_path.display()),
            )?;
        }
    }
    let graph_build_time_ms = graph_start.elapsed().as_secs_f64() * 1000.0;
    append_progress(
        progress_log,
        "graph_build",
        &format!("mode={graph_build_mode} done ms={graph_build_time_ms:.3}"),
    )?;

    let build_time_ms = total_build_start.elapsed().as_secs_f64() * 1000.0;
    append_progress(
        progress_log,
        "search",
        &format!(
            "start paper_prune=active residual_rerank=block16 query_coarse_codecs={}",
            ctx.config
                .query_coarse_codecs
                .iter()
                .map(|c| c.as_str())
                .collect::<Vec<_>>()
                .join(",")
        ),
    )?;
    let mut search_results = Vec::with_capacity(
        ctx.config.search_list_sizes.len() * ctx.config.query_coarse_codecs.len(),
    );
    // The legacy replay is a correctness diagnostic, not part of search
    // latency. Keep it opt-in so formal benchmarks do not time a second full
    // search for the first query of every point.
    let verify_batch_legacy =
        std::env::var_os("RABITQ_VERIFY_BATCH_LEGACY").is_some();
    for &codec in &ctx.config.query_coarse_codecs {
        space.set_query_coarse_codec(codec);
        for &search_list_size in &ctx.config.search_list_sizes {
        let mut latencies = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let mut hits = 0_u64;
        let mut total = 0_u64;
        let mut stats = OursPaperSearchStats::default();
        let mut saved_ids = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let mut saved_distances = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let search_start = Instant::now();
        for repeat_idx in 0..ctx.config.repeats {
            for qid in 0..queries.nrows() {
                let query = queries.row(qid);
                let one_start = Instant::now();
                let result = search_ours_paper_active(
                    index.provider(),
                    query,
                    K,
                    search_list_size,
                    ctx.config.search_beam_width,
                    1.9,
                    ctx.config.b1_epsilon,
                    ctx.config.rerank_candidates,
                    ctx.config.search_early_stop_hops,
                    ctx.config.search_kth_stop,
                    codec,
                    verify_batch_legacy && repeat_idx == 0 && qid == 0,
                )
                .map_err(err)?;
                latencies.push(one_start.elapsed().as_secs_f64() * 1_000_000.0);
                stats.visited_nodes += result.stats.visited_nodes;
                stats.distance_computations += result.stats.distance_computations;
                stats.hops += result.stats.hops;
                stats.prefetch_issued += result.stats.prefetch_issued;
                stats.prepare_ns += result.stats.prepare_ns;
                stats.traverse_ns += result.stats.traverse_ns;
                stats.rerank_ns += result.stats.rerank_ns;
                stats.neighbor_fetch_ns += result.stats.neighbor_fetch_ns;
                stats.visited_mark_ns += result.stats.visited_mark_ns;
                stats.paper_batch_ns += result.stats.paper_batch_ns;
                stats.flush_ns += result.stats.flush_ns;
                stats.paper_checked += result.stats.paper_checked;
                stats.paper_would_prune += result.stats.paper_would_prune;
                stats.paper_not_pruned += result.stats.paper_not_pruned;
                stats.paper_full_saved += result.stats.paper_full_saved;
                stats.paper_msb_kernel_calls += result.stats.paper_msb_kernel_calls;
                stats.paper_remaining_kernel_calls += result.stats.paper_remaining_kernel_calls;
                stats.ffi_calls += result.stats.ffi_calls;
                let ids = result.ids;
                let distances = result.distances;
                hits += recall_hits(&ids, &groundtruth[qid]) as u64;
                saved_ids.push(ids);
                saved_distances.push(distances);
                total += K as u64;
            }
        }
        // QPS is a search metric.  Stop its timer before the offline FP32
        // accuracy audit below; latency_mean_us already has the same scope.
        let elapsed = search_start.elapsed().as_secs_f64();
        let mut totals = AccuracyTotals::default();
        for (idx, (ids, distances)) in saved_ids.iter().zip(&saved_distances).enumerate() {
            let qid = idx % queries.nrows();
            accumulate_accuracy(
                ids,
                distances,
                queries.row(qid),
                data.as_ref(),
                &groundtruth[qid],
                &mut totals,
            );
        }
        let (mean_relative_error, p95_relative_error, mean_absolute_error, top10_overlap, pairwise_flip_rate_top10) =
            accuracy_summary(&mut totals);
        let recall = hits as f64 / total as f64;
        let qps = (queries.nrows() * ctx.config.repeats) as f64 / elapsed.max(1e-12);
        let latency_mean_us = latencies.iter().sum::<f64>() / latencies.len() as f64;
        let latency_p95_us = percentile(&mut latencies, 0.95);
        let query_count = (queries.nrows() * ctx.config.repeats) as f64;
        search_results.push(SearchResult {
            search_list_size,
            recall,
            qps,
            latency_mean_us,
            latency_p95_us,
            prepare_us: stats.prepare_ns as f64 / query_count / 1000.0,
            traverse_us: stats.traverse_ns as f64 / query_count / 1000.0,
            rerank_us: stats.rerank_ns as f64 / query_count / 1000.0,
            neighbor_fetch_us: stats.neighbor_fetch_ns as f64 / query_count / 1000.0,
            visited_mark_us: stats.visited_mark_ns as f64 / query_count / 1000.0,
            paper_batch_us: stats.paper_batch_ns as f64 / query_count / 1000.0,
            flush_us: stats.flush_ns as f64 / query_count / 1000.0,
            visited_nodes: stats.visited_nodes as f64 / query_count,
            distance_computations: stats.distance_computations as f64 / query_count,
            paper_checked: stats.paper_checked as f64 / query_count,
            paper_would_prune: stats.paper_would_prune as f64 / query_count,
            paper_msb_kernel_calls: stats.paper_msb_kernel_calls as f64 / query_count,
            paper_remaining_kernel_calls: stats.paper_remaining_kernel_calls as f64 / query_count,
            ffi_calls_per_query: stats.ffi_calls as f64 / query_count,
            mean_relative_error,
            p95_relative_error,
            mean_absolute_error,
            top10_overlap,
            pairwise_flip_rate_top10,
            query_coarse_codec: codec.as_str().to_string(),
            status: "done".to_string(),
        });
        append_progress(
            progress_log,
            "search",
            &format!(
                "search_list_size={search_list_size} query_coarse_codec={} recall={recall:.6} qps={qps:.3} paper_pruned={} paper_checked={} paper_msb_kernels={:.3} remaining_kernels={} ffi_calls={:.3} traverse_us={:.3} paper_batch_us={:.3} flush_us={:.3}",
                codec.as_str(),
                stats.paper_full_saved,
                stats.paper_checked,
                stats.paper_msb_kernel_calls as f64 / query_count,
                stats.paper_remaining_kernel_calls,
                stats.ffi_calls as f64 / query_count,
                stats.traverse_ns as f64 / query_count / 1000.0,
                stats.paper_batch_ns as f64 / query_count / 1000.0,
                stats.flush_ns as f64 / query_count / 1000.0,
            ),
        )?;
        }
    }
    append_progress(progress_log, "search", "done")?;

    let payload_path = write_payload_marker(ctx, METHOD)?;
    let payload_json_path = write_payload_json(
        ctx,
        METHOD,
        &payload_path,
        (space.compact_record_bytes() + space.residual_record_bytes()) as f64,
        space.compact_record_bytes() as f64,
        None,
        NOTE,
    )?;
    let base_count = data.nrows() as u64;
    let primary_bytes = space.compact_record_bytes() as u64 * base_count;
    let residual_bytes = space.residual_record_bytes() as u64 * base_count;
    let sidecar_bytes =
        (space.paper_msb_code_bytes() + space.paper_factor_bytes()) as u64 * base_count;
    let graph_bytes = base_count * 4 + total_edges * 4;
    let index_bytes = graph_bytes + primary_bytes + sidecar_bytes;
    let auxiliary_bytes = (data.ncols() as u64) * 4 * 2;
    let fp32_base_bytes = 0_u64; // ExRaBitQ4-symmetric graph build stores no fp32 base
    let total_bytes = index_bytes + auxiliary_bytes + residual_bytes + fp32_base_bytes;

    Ok(PreparedPayload {
        method: METHOD.to_string(),
        status: AdapterStatus::Done,
        has_real_metrics: true,
        payload_path,
        payload_json_path,
        nominal_bits_per_dim: 4.0,
        actual_bytes_per_vector: Some(
            (space.compact_record_bytes() + space.residual_record_bytes()) as f64,
        ),
        code_bytes_per_vector: Some(space.compact_record_bytes() as f64),
        metadata_bytes_per_vector: Some(space.residual_record_bytes() as f64),
        codebook_bytes: None,
        train_time_ms: Some(train_time_ms),
        encode_time_ms: Some(payload_encode_time_ms),
        build_time_ms: Some(build_time_ms),
        graph_build_time_ms: Some(graph_build_time_ms),
        shared_graph_build_time_ms: None,
        graph_build_mode,
        index_size_mb: Some(total_bytes as f64 / 1_048_576.0),
        index_bytes: Some(index_bytes),
        auxiliary_bytes: Some(auxiliary_bytes),
        residual_bytes: Some(residual_bytes),
        fp32_base_bytes: Some(fp32_base_bytes),
        graph_build_distance: "ExRaBitQ4_symmetric".to_string(),
        peak_rss_mb: peak_rss_mb(),
        search_results,
        note: format!(
            "{NOTE} query_coarse_codecs={}",
            ctx.config
                .query_coarse_codecs
                .iter()
                .map(|c| c.as_str())
                .collect::<Vec<_>>()
                .join(",")
        ),
    })
}

fn run_with_provider<Q, S, F>(
    ctx: &RunContext,
    progress_log: &Path,
    method: &str,
    note: &str,
    actual_bytes_per_vector: f64,
    code_bytes_per_vector: f64,
    codebook_bytes: Option<u64>,
    make_quant: F,
) -> Result<PreparedPayload, String>
where
    Q: CreateVectorStore,
    Q::Target: diskann_utils::future::AsyncFriendly + SetElementHelper<f32>,
    S: Clone + Send + Sync + 'static,
    F: FnOnce(
        Arc<Matrix<f32>>,
        &mut StdRng,
        diskann_providers::utils::RayonThreadPoolRef<'_>,
        &mut dyn FnMut(&str, &str) -> Result<(), String>,
    ) -> Result<(Q, f64, S), String>,
    FullPrecision: MultiInsertStrategy<DiskAnnQuantProvider<Q>, Matrix<f32>>,
    DiskAnnQuantProvider<Q>: for<'a> SetElement<&'a [f32]>,
    for<'a> <DiskAnnQuantProvider<Q> as SetElement<&'a [f32]>>::SetError: std::fmt::Display,
    DiskAnnQuantProvider<Q>:
        DataProvider<Context = DefaultContext, InternalId = u32, ExternalId = u32>,
    S: for<'a> DefaultSearchStrategy<'a, DiskAnnQuantProvider<Q>, &'a [f32]>,
    S: for<'a> SearchStrategy<'a, DiskAnnQuantProvider<Q>, &'a [f32]>,
    for<'a> <S as SearchStrategy<'a, DiskAnnQuantProvider<Q>, &'a [f32]>>::SearchAccessor:
        SearchAccessor,
{
    reset_progress(progress_log, ctx, method)?;
    append_progress(progress_log, "load_data", "start")?;
    let load_start = Instant::now();
    let data = Arc::new(read_fvecs_matrix(&ctx.dataset.base_path)?);
    let queries = read_fvecs_matrix(&ctx.dataset.query_path)?;
    let groundtruth = read_ivecs_topk(&ctx.dataset.gt_path, K)?;
    append_progress(
        progress_log,
        "load_data",
        &format!("done ms={:.3}", load_start.elapsed().as_secs_f64() * 1000.0),
    )?;

    let start_index = shared_start_index(ctx.config.seed, data.nrows());
    let mut rng = StdRng::seed_from_u64(ctx.config.seed ^ method_seed(method));
    let pool =
        diskann_providers::utils::create_thread_pool(ctx.config.build_threads).map_err(err)?;
    let mut progress = |stage: &str, status: &str| append_progress(progress_log, stage, status);
    let total_build_start = Instant::now();
    let (quant, train_time_ms, search_strategy) =
        make_quant(data.clone(), &mut rng, pool.as_ref(), &mut progress)?;

    progress("create_index", "start")?;
    let config = graph::config::Builder::new_with(
        ctx.config.max_degree,
        graph::config::MaxDegree::default_slack(),
        ctx.config.build_beam,
        Metric::L2.into(),
        |builder| {
            builder.alpha(ctx.config.alpha);
            builder.max_minibatch_par(ctx.config.build_threads);
        },
    )
    .build()
    .map_err(err)?;
    let params = DefaultProviderParameters::simple(
        data.nrows(),
        data.ncols(),
        Metric::L2,
        config.max_degree_u32().get(),
    );
    let index = diskann_async::new_quant_index::<f32, _, _>(config, params, quant, NoDeletes)
        .map_err(err)?;
    index
        .provider()
        .set_start_points(std::iter::once(data.row(start_index)))
        .map_err(err)?;
    progress("create_index", "done")?;

    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(ctx.config.build_threads)
        .enable_all()
        .build()
        .map_err(err)?;
    let shared = SharedGraphPaths::new(ctx);
    fs::create_dir_all(&shared.dir).map_err(err)?;
    let expected_meta = shared_graph_key(ctx, data.nrows(), data.ncols(), start_index);
    let graph_bytes: Option<u64>;
    let graph_build_time_ms: f64;
    let shared_graph_build_time_ms: Option<f64>;
    let graph_build_mode: String;
    let mut payload_encode_time_ms = 0.0;

    if shared.is_valid(&expected_meta) {
        progress(
            "shared_graph",
            &format!("load path={}", shared.graph_path.display()),
        )?;
        let meta = fs::read_to_string(&shared.meta_path).map_err(err)?;
        let shared_build_ms = parse_meta_f64(&meta, "graph_build_time_ms").unwrap_or(0.0);
        shared_graph_build_time_ms = Some(shared_build_ms);
        graph_build_mode = "reused_shared".to_string();
        graph_bytes = parse_meta_u64(&meta, "graph_bytes");

        let encode_start = Instant::now();
        {
            let n = data.nrows();
            let workers = ctx.config.build_threads.max(1).min(n);
            let chunk = n.div_ceil(workers);
            let first_error = Mutex::new(None::<String>);
            std::thread::scope(|scope| {
                for worker in 0..workers {
                    let begin = worker * chunk;
                    let end = (begin + chunk).min(n);
                    if begin >= end {
                        continue;
                    }
                    let data = &data;
                    let index = &index;
                    let rt = &rt;
                    let first_error = &first_error;
                    scope.spawn(move || {
                        for id in begin..end {
                            if first_error.lock().unwrap().is_some() {
                                break;
                            }
                            let id32 = id as u32;
                            if let Err(e) = rt.block_on(index.provider().set_element(
                                &DefaultContext,
                                &id32,
                                data.row(id),
                            )) {
                                *first_error.lock().unwrap() = Some(e.to_string());
                                break;
                            }
                        }
                    });
                }
            });
            if let Some(message) = first_error.into_inner().unwrap() {
                return Err(message);
            }
        }
        payload_encode_time_ms = encode_start.elapsed().as_secs_f64() * 1000.0;
        progress(
            "payload_encode",
            &format!("done ms={payload_encode_time_ms:.3} source=shared_graph_load"),
        )?;

        let copy_start = Instant::now();
        copy_shared_graph(
            &shared.graph_path,
            index.provider().neighbors(),
            index.provider().total_points(),
        )?;
        graph_build_time_ms = copy_start.elapsed().as_secs_f64() * 1000.0;
        progress(
            "graph_build",
            &format!(
                "reused_shared original_ms={shared_build_ms:.3} copy_ms={:.3}",
                graph_build_time_ms,
            ),
        )?;
    } else {
        let intra_label = if ctx.config.intra_batch_candidates == 0 {
            "None".to_string()
        } else {
            format!("Max({})", ctx.config.intra_batch_candidates)
        };
        progress(
            "graph_build",
            &format!(
                "start build_distance=fp32_l2 batch_size={} intra_batch_candidates={intra_label}",
                ctx.config.build_batch_size,
            ),
        )?;
        let graph_start = Instant::now();
        let heartbeat = Heartbeat::start(progress_log.to_path_buf(), "graph_build");
        for batch_start in (0..data.nrows()).step_by(ctx.config.build_batch_size) {
            let batch_end = (batch_start + ctx.config.build_batch_size).min(data.nrows());
            let batch = data
                .subview(batch_start..batch_end)
                .ok_or_else(|| format!("subview {batch_start}..{batch_end} out of bounds"))?
                .to_owned();
            let ids: Arc<[u32]> =
                (batch_start as u32..batch_end as u32).collect::<Vec<_>>().into();
            rt.block_on(index.multi_insert::<FullPrecision, Matrix<f32>>(
                FullPrecision,
                &DefaultContext,
                Arc::new(batch),
                ids,
            ))
            .map_err(err)?;
            progress(
                "graph_build",
                &format!("batch_done points_processed={batch_end} points_total={}", data.nrows()),
            )?;
        }
        heartbeat.stop()?;
        let elapsed_ms = graph_start.elapsed().as_secs_f64() * 1000.0;
        graph_build_time_ms = elapsed_ms;
        shared_graph_build_time_ms = Some(elapsed_ms);
        graph_build_mode = "built_in_run".to_string();
        progress("graph_build", &format!("done ms={elapsed_ms:.3}"))?;

        progress(
            "shared_graph",
            &format!("save path={}", shared.graph_path.display()),
        )?;
        let bytes = index
            .provider()
            .neighbors()
            .save_direct(
                &FileStorageProvider,
                data.nrows() as u32,
                shared.graph_path.to_string_lossy().as_ref(),
            )
            .map_err(err)? as u64;
        graph_bytes = Some(bytes);
        fs::write(
            &shared.meta_path,
            format!(
                "{}graph_build_time_ms={:.6}\nshared_graph_build_time_ms={:.6}\ngraph_bytes={}\ngraph_path={}\n",
                expected_meta,
                elapsed_ms,
                elapsed_ms,
                bytes,
                shared.graph_path.display()
            ),
        )
        .map_err(err)?;
        progress("shared_graph", "saved")?;
    }

    let build_time_ms = total_build_start.elapsed().as_secs_f64() * 1000.0;

    append_progress(progress_log, "search", "start")?;
    let mut search_results = Vec::with_capacity(ctx.config.search_list_sizes.len());
    for &search_list_size in &ctx.config.search_list_sizes {
        let mut latencies = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let mut hits = 0_u64;
        let mut total = 0_u64;
        let mut saved_ids = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let mut saved_distances = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let search_start = Instant::now();
        for _ in 0..ctx.config.repeats {
            for qid in 0..queries.nrows() {
                let query = queries.row(qid);
                let mut ids = vec![0_u32; K];
                let mut distances = vec![0.0_f32; K];
                let one_start = Instant::now();
                let mut output = IdDistance::new(&mut ids, &mut distances);
                let search =
                    graph::search::Knn::new(search_list_size, Some(ctx.config.search_beam_width))
                        .map_err(err)?;
                rt.block_on(index.search(
                    search,
                    &search_strategy,
                    &DefaultContext,
                    query,
                    &mut output,
                ))
                .map_err(err)?;
                latencies.push(one_start.elapsed().as_secs_f64() * 1_000_000.0);
                hits += recall_hits(&ids, &groundtruth[qid]) as u64;
                saved_ids.push(ids);
                saved_distances.push(distances);
                total += K as u64;
            }
        }
        let mut totals = AccuracyTotals::default();
        for (idx, (ids, distances)) in saved_ids.iter().zip(&saved_distances).enumerate() {
            let qid = idx % queries.nrows();
            accumulate_accuracy(
                ids,
                distances,
                queries.row(qid),
                data.as_ref(),
                &groundtruth[qid],
                &mut totals,
            );
        }
        let (mean_relative_error, p95_relative_error, mean_absolute_error, top10_overlap, pairwise_flip_rate_top10) =
            accuracy_summary(&mut totals);
        let elapsed = search_start.elapsed().as_secs_f64();
        let recall = hits as f64 / total as f64;
        let qps = (queries.nrows() * ctx.config.repeats) as f64 / elapsed.max(1e-12);
        let latency_mean_us = latencies.iter().sum::<f64>() / latencies.len() as f64;
        let latency_p95_us = percentile(&mut latencies, 0.95);
        search_results.push(SearchResult {
            search_list_size,
            recall,
            qps,
            latency_mean_us,
            latency_p95_us,
            prepare_us: f64::NAN,
            traverse_us: f64::NAN,
            rerank_us: f64::NAN,
            neighbor_fetch_us: f64::NAN,
            visited_mark_us: f64::NAN,
            paper_batch_us: f64::NAN,
            flush_us: f64::NAN,
            visited_nodes: f64::NAN,
            distance_computations: f64::NAN,
            paper_checked: f64::NAN,
            paper_would_prune: f64::NAN,
            paper_msb_kernel_calls: f64::NAN,
            paper_remaining_kernel_calls: f64::NAN,
            ffi_calls_per_query: f64::NAN,
            mean_relative_error,
            p95_relative_error,
            mean_absolute_error,
            top10_overlap,
            pairwise_flip_rate_top10,
            query_coarse_codec: "full".to_string(),
            status: "done".to_string(),
        });
        progress(
            "search",
            &format!("search_list_size={search_list_size} recall={recall:.6} qps={qps:.3}"),
        )?;
    }
    progress("search", "done")?;

    let payload_path = write_payload_marker(ctx, method)?;
    let payload_json_path = write_payload_json(
        ctx,
        method,
        &payload_path,
        actual_bytes_per_vector,
        code_bytes_per_vector,
        codebook_bytes,
        note,
    )?;
    let graph_bytes = graph_bytes
        .unwrap_or_else(|| (ctx.dataset.base_count + 1) * ctx.config.max_degree as u64 * 4);
    let payload_bytes = (actual_bytes_per_vector * ctx.dataset.base_count as f64).round() as u64;
    let codebook = codebook_bytes.unwrap_or(0);
    let index_bytes = graph_bytes + payload_bytes + codebook;
    let auxiliary_bytes = 0_u64;
    let residual_bytes = 0_u64;
    let fp32_base_bytes = ctx.dataset.base_count as u64 * ctx.dataset.dimension as u64 * 4;
    let total_bytes = index_bytes + auxiliary_bytes + residual_bytes + fp32_base_bytes;

    Ok(PreparedPayload {
        method: method.to_string(),
        status: AdapterStatus::Done,
        has_real_metrics: true,
        payload_path,
        payload_json_path,
        nominal_bits_per_dim: 4.0,
        actual_bytes_per_vector: Some(actual_bytes_per_vector),
        code_bytes_per_vector: Some(code_bytes_per_vector),
        metadata_bytes_per_vector: Some(actual_bytes_per_vector - code_bytes_per_vector),
        codebook_bytes,
        train_time_ms: Some(train_time_ms),
        encode_time_ms: Some(payload_encode_time_ms),
        build_time_ms: Some(build_time_ms),
        graph_build_time_ms: Some(graph_build_time_ms),
        shared_graph_build_time_ms,
        graph_build_mode,
        index_size_mb: Some(total_bytes as f64 / 1_048_576.0),
        index_bytes: Some(index_bytes),
        auxiliary_bytes: Some(auxiliary_bytes),
        residual_bytes: Some(residual_bytes),
        fp32_base_bytes: Some(fp32_base_bytes),
        graph_build_distance: "fp32_l2".to_string(),
        peak_rss_mb: peak_rss_mb(),
        search_results,
        note: note.to_string(),
    })
}

fn run_quant_only_graph<Q, S, F>(
    ctx: &RunContext,
    progress_log: &Path,
    method: &str,
    note: &str,
    graph_build_distance: &str,
    actual_bytes_per_vector: f64,
    code_bytes_per_vector: f64,
    codebook_bytes: Option<u64>,
    make_quant: F,
) -> Result<PreparedPayload, String>
where
    Q: CreateVectorStore,
    Q::Target: diskann_utils::future::AsyncFriendly + SetElementHelper<f32>,
    S: Clone + Send + Sync + 'static,
    F: FnOnce(
        Arc<Matrix<f32>>,
        &mut StdRng,
        diskann_providers::utils::RayonThreadPoolRef<'_>,
        &mut dyn FnMut(&str, &str) -> Result<(), String>,
    ) -> Result<(Q, f64, S), String>,
    QuantOnlyProvider<Q>: for<'a> SetElement<&'a [f32]>,
    for<'a> <QuantOnlyProvider<Q> as SetElement<&'a [f32]>>::SetError: std::fmt::Display,
    QuantOnlyProvider<Q>:
        DataProvider<Context = DefaultContext, InternalId = u32, ExternalId = u32>,
    QuantOnlyProvider<Q>: SetStartPoints<[f32]>,
    S: for<'a> DefaultSearchStrategy<'a, QuantOnlyProvider<Q>, &'a [f32]>,
    S: for<'a> SearchStrategy<'a, QuantOnlyProvider<Q>, &'a [f32]>,
    for<'a> <S as SearchStrategy<'a, QuantOnlyProvider<Q>, &'a [f32]>>::SearchAccessor:
        SearchAccessor,
    S: MultiInsertStrategy<QuantOnlyProvider<Q>, Matrix<f32>>,
{
    reset_progress(progress_log, ctx, method)?;
    append_progress(progress_log, "load_data", "start")?;
    let load_start = Instant::now();
    let data = Arc::new(read_fvecs_matrix(&ctx.dataset.base_path)?);
    let queries = read_fvecs_matrix(&ctx.dataset.query_path)?;
    let groundtruth = read_ivecs_topk(&ctx.dataset.gt_path, K)?;
    append_progress(
        progress_log,
        "load_data",
        &format!("done ms={:.3}", load_start.elapsed().as_secs_f64() * 1000.0),
    )?;

    let start_index = shared_start_index(ctx.config.seed, data.nrows());
    let mut rng = StdRng::seed_from_u64(ctx.config.seed ^ method_seed(method));
    let pool =
        diskann_providers::utils::create_thread_pool(ctx.config.build_threads).map_err(err)?;
    let mut progress = |stage: &str, status: &str| append_progress(progress_log, stage, status);
    let total_build_start = Instant::now();
    let (quant, train_time_ms, search_strategy) =
        make_quant(data.clone(), &mut rng, pool.as_ref(), &mut progress)?;

    progress("create_index", "start")?;
    let config = graph::config::Builder::new_with(
        ctx.config.max_degree,
        graph::config::MaxDegree::default_slack(),
        ctx.config.build_beam,
        Metric::L2.into(),
        |builder| {
            builder.alpha(ctx.config.alpha);
            builder.max_minibatch_par(ctx.config.build_threads);
            builder.intra_batch_candidates(
                graph::config::IntraBatchCandidates::new(ctx.config.intra_batch_candidates),
            );
        },
    )
    .build()
    .map_err(err)?;
    let params = DefaultProviderParameters::simple(
        data.nrows(),
        data.ncols(),
        Metric::L2,
        config.max_degree_u32().get(),
    );
    let index = Arc::new(
        diskann_async::new_quant_only_index(config, params, quant, NoDeletes).map_err(err)?,
    );
    index
        .provider()
        .set_start_points(std::iter::once(data.row(start_index)))
        .map_err(err)?;
    progress("create_index", "done")?;

    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(ctx.config.build_threads)
        .enable_all()
        .build()
        .map_err(err)?;

    let intra_label = if ctx.config.intra_batch_candidates == 0 {
        "None".to_string()
    } else {
        format!("Max({})", ctx.config.intra_batch_candidates)
    };
    progress(
        "graph_build",
        &format!(
            "start build_distance={graph_build_distance} batch_size={} intra_batch_candidates={intra_label}",
            ctx.config.build_batch_size
        ),
    )?;
    let graph_start = Instant::now();
    let heartbeat = Heartbeat::start(progress_log.to_path_buf(), "graph_build");
    for batch_start in (0..data.nrows()).step_by(ctx.config.build_batch_size) {
        let batch_end = (batch_start + ctx.config.build_batch_size).min(data.nrows());
        let batch = data
            .subview(batch_start..batch_end)
            .ok_or_else(|| format!("subview {batch_start}..{batch_end} out of bounds"))?
            .to_owned();
        let ids: Arc<[u32]> =
            (batch_start as u32..batch_end as u32).collect::<Vec<_>>().into();
        rt.block_on(index.multi_insert::<S, Matrix<f32>>(
            search_strategy.clone(),
            &DefaultContext,
            Arc::new(batch),
            ids,
        ))
        .map_err(err)?;
        progress(
            "graph_build",
            &format!("batch_done points_processed={batch_end} points_total={}", data.nrows()),
        )?;
    }
    heartbeat.stop()?;
    let graph_build_time_ms = graph_start.elapsed().as_secs_f64() * 1000.0;
    progress("graph_build", &format!("done ms={graph_build_time_ms:.3}"))?;

    let build_time_ms = total_build_start.elapsed().as_secs_f64() * 1000.0;

    append_progress(progress_log, "search", "start")?;
    let mut search_results = Vec::with_capacity(ctx.config.search_list_sizes.len());
    for &search_list_size in &ctx.config.search_list_sizes {
        let mut latencies = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let mut hits = 0_u64;
        let mut total = 0_u64;
        let mut saved_ids = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let mut saved_distances = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let search_start = Instant::now();
        for _ in 0..ctx.config.repeats {
            for qid in 0..queries.nrows() {
                let query = queries.row(qid);
                let mut ids = vec![0_u32; K];
                let mut distances = vec![0.0_f32; K];
                let one_start = Instant::now();
                let mut output = IdDistance::new(&mut ids, &mut distances);
                let search =
                    graph::search::Knn::new(search_list_size, Some(ctx.config.search_beam_width))
                        .map_err(err)?;
                rt.block_on(index.search(
                    search,
                    &search_strategy,
                    &DefaultContext,
                    query,
                    &mut output,
                ))
                .map_err(err)?;
                latencies.push(one_start.elapsed().as_secs_f64() * 1_000_000.0);
                hits += recall_hits(&ids, &groundtruth[qid]) as u64;
                saved_ids.push(ids);
                saved_distances.push(distances);
                total += K as u64;
            }
        }
        let mut totals = AccuracyTotals::default();
        for (idx, (ids, distances)) in saved_ids.iter().zip(&saved_distances).enumerate() {
            let qid = idx % queries.nrows();
            accumulate_accuracy(
                ids,
                distances,
                queries.row(qid),
                data.as_ref(),
                &groundtruth[qid],
                &mut totals,
            );
        }
        let (mean_relative_error, p95_relative_error, mean_absolute_error, top10_overlap, pairwise_flip_rate_top10) =
            accuracy_summary(&mut totals);
        let elapsed = search_start.elapsed().as_secs_f64();
        let recall = hits as f64 / total as f64;
        let qps = (queries.nrows() * ctx.config.repeats) as f64 / elapsed.max(1e-12);
        let latency_mean_us = latencies.iter().sum::<f64>() / latencies.len() as f64;
        let latency_p95_us = percentile(&mut latencies, 0.95);
        search_results.push(SearchResult {
            search_list_size,
            recall,
            qps,
            latency_mean_us,
            latency_p95_us,
            prepare_us: f64::NAN,
            traverse_us: f64::NAN,
            rerank_us: f64::NAN,
            neighbor_fetch_us: f64::NAN,
            visited_mark_us: f64::NAN,
            paper_batch_us: f64::NAN,
            flush_us: f64::NAN,
            visited_nodes: f64::NAN,
            distance_computations: f64::NAN,
            paper_checked: f64::NAN,
            paper_would_prune: f64::NAN,
            paper_msb_kernel_calls: f64::NAN,
            paper_remaining_kernel_calls: f64::NAN,
            ffi_calls_per_query: f64::NAN,
            mean_relative_error,
            p95_relative_error,
            mean_absolute_error,
            top10_overlap,
            pairwise_flip_rate_top10,
            query_coarse_codec: "full".to_string(),
            status: "done".to_string(),
        });
        progress(
            "search",
            &format!("search_list_size={search_list_size} recall={recall:.6} qps={qps:.3}"),
        )?;
    }
    progress("search", "done")?;

    let payload_path = write_payload_marker(ctx, method)?;
    let payload_json_path = write_payload_json(
        ctx,
        method,
        &payload_path,
        actual_bytes_per_vector,
        code_bytes_per_vector,
        codebook_bytes,
        note,
    )?;
    let graph_bytes = (ctx.dataset.base_count + 1) * ctx.config.max_degree as u64 * 4;
    let payload_bytes = (actual_bytes_per_vector * ctx.dataset.base_count as f64).round() as u64;
    let codebook = codebook_bytes.unwrap_or(0);
    let index_bytes = graph_bytes + payload_bytes + codebook;
    let total_bytes = index_bytes;

    Ok(PreparedPayload {
        method: method.to_string(),
        status: AdapterStatus::Done,
        has_real_metrics: true,
        payload_path,
        payload_json_path,
        nominal_bits_per_dim: 4.0,
        actual_bytes_per_vector: Some(actual_bytes_per_vector),
        code_bytes_per_vector: Some(code_bytes_per_vector),
        metadata_bytes_per_vector: Some(actual_bytes_per_vector - code_bytes_per_vector),
        codebook_bytes,
        train_time_ms: Some(train_time_ms),
        encode_time_ms: Some(0.0),
        build_time_ms: Some(build_time_ms),
        graph_build_time_ms: Some(graph_build_time_ms),
        shared_graph_build_time_ms: None,
        graph_build_mode: "built_in_run".to_string(),
        index_size_mb: Some(total_bytes as f64 / 1_048_576.0),
        index_bytes: Some(index_bytes),
        auxiliary_bytes: Some(0),
        residual_bytes: Some(0),
        fp32_base_bytes: Some(0),
        graph_build_distance: graph_build_distance.to_string(),
        peak_rss_mb: peak_rss_mb(),
        search_results,
        note: note.to_string(),
    })
}

pub fn run_pq4_own(ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
    let dim = ctx.dataset.dimension as usize;
    if dim % 2 != 0 {
        return Err(format!(
            "PQ 4bpd requires even dimension when DiskANN PQ stores one 8-bit code per chunk; got dim={dim}"
        ));
    }
    let num_pq_chunks = dim / 2;
    let code_bytes_per_vector = num_pq_chunks as f64;
    let codebook_bytes = (256_u64 * dim as u64 * std::mem::size_of::<f32>() as u64) as u64;

    run_quant_only_graph(
        ctx,
        progress_log,
        "PQ",
        "DiskANN FixedChunkPQTable with num_pq_chunks=D/2: graph build AND search use the 4bit-equivalent PQ payload (Quantized strategy, NoStore primary, per-method graph).",
        "PQ_quantized",
        code_bytes_per_vector,
        code_bytes_per_vector,
        Some(codebook_bytes),
        |data, rng, pool, progress| {
            progress("train_quantizer", "start")?;
            let start = Instant::now();
            let table =
                diskann_async::train_pq(data.as_view(), num_pq_chunks, rng, pool).map_err(err)?;
            progress("train_quantizer", "done")?;
            Ok((
                table,
                start.elapsed().as_secs_f64() * 1000.0,
                Quantized,
            ))
        },
    )
}

pub fn run_sq4_own(ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
    let dim = ctx.dataset.dimension as usize;
    let bytes = CompensatedVectorRef::<4>::canonical_bytes(dim) as f64;
    let code_bytes = ((dim * 4).div_ceil(8)) as f64;
    let metadata_bytes = bytes - code_bytes;

    run_quant_only_graph(
        ctx,
        progress_log,
        "SQ",
        "DiskANN scalar quantization WithBits<4>: graph build AND search use the 4-bit scalar payload (Quantized strategy, NoStore primary, per-method graph).",
        "SQ4_symmetric",
        bytes,
        code_bytes,
        None,
        |data, _rng, _pool, progress| {
            progress("train_quantizer", "start")?;
            let start = Instant::now();
            let quantizer = ScalarQuantizationParameters::default().train(data.as_view());
            progress("train_quantizer", "done")?;
            Ok((
                inmem::WithBits::<4>::new(quantizer),
                start.elapsed().as_secs_f64() * 1000.0,
                Quantized,
            ))
        },
    )
    .map(|mut payload| {
        payload.metadata_bytes_per_vector = Some(metadata_bytes);
        payload
    })
}

pub fn run_saq4_own(ctx: &RunContext, progress_log: &Path) -> Result<PreparedPayload, String> {
    let dim = ctx.dataset.dimension as usize;
    let code_bytes = ((dim * 4).div_ceil(8)) as f64;

    run_quant_only_graph(
        ctx,
        progress_log,
        "SAQ",
        "DiskANN spherical quantization Impl<4>: graph build AND search use the 4-bit spherical payload (Quantized strategy, NoStore primary, per-method graph).",
        "SAQ4_symmetric",
        code_bytes,
        code_bytes,
        None,
        |data, rng, _pool, progress| {
            progress("train_quantizer", "start")?;
            let start = Instant::now();
            let quantizer = diskann_quantization::spherical::SphericalQuantizer::train(
                data.as_view(),
                diskann_quantization::algorithms::transforms::TransformKind::PaddingHadamard {
                    target_dim:
                        diskann_quantization::algorithms::transforms::TargetDim::Natural,
                },
                Metric::L2.try_into().map_err(err)?,
                diskann_quantization::spherical::PreScale::ReciprocalMeanNorm,
                rng,
                diskann_quantization::alloc::GlobalAllocator,
            )
            .map_err(err)?;
            let plan = diskann_quantization::spherical::iface::Impl::<4>::new(quantizer)
                .map_err(err)?;
            progress("train_quantizer", "done")?;
            Ok((
                plan,
                start.elapsed().as_secs_f64() * 1000.0,
                inmem::spherical::Quantized::search(
                    diskann_quantization::spherical::iface::QueryLayout::ScalarQuantized,
                ),
            ))
        },
    )
}

pub fn run_fp32_graph(
    ctx: &RunContext,
    progress_log: &Path,
    graph_path: &Path,
) -> Result<PreparedPayload, String> {
    const METHOD: &str = "FP32";
    const NOTE: &str = concat!(
        "Uniform fp32 reference search over an externally provided DiskANN-format graph. ",
        "Used by the 03 decomposition graph-quality measurement: the same search code, ",
        "queries, GT and ef sweep are applied to every system's own graph."
    );

    reset_progress(progress_log, ctx, METHOD)?;
    append_progress(progress_log, "load_data", "start")?;
    let load_start = Instant::now();
    let data = Arc::new(read_fvecs_matrix(&ctx.dataset.base_path)?);
    let queries = read_fvecs_matrix(&ctx.dataset.query_path)?;
    let groundtruth = read_ivecs_topk(&ctx.dataset.gt_path, K)?;
    append_progress(
        progress_log,
        "load_data",
        &format!("done ms={:.3}", load_start.elapsed().as_secs_f64() * 1000.0),
    )?;

    append_progress(progress_log, "create_index", "start")?;
    let config = graph::config::Builder::new_with(
        ctx.config.max_degree,
        graph::config::MaxDegree::default_slack(),
        ctx.config.build_beam,
        Metric::L2.into(),
        |builder| {
            builder.alpha(ctx.config.alpha);
            builder.max_minibatch_par(ctx.config.build_threads);
        },
    )
    .build()
    .map_err(err)?;
    let params = DefaultProviderParameters::simple(
        data.nrows(),
        data.ncols(),
        Metric::L2,
        config.max_degree_u32().get(),
    );
    let index = diskann_async::new_quant_index::<f32, NoStore, NoDeletes>(
        config,
        params,
        NoStore,
        NoDeletes,
    )
    .map_err(err)?;
    index
        .provider()
        .set_start_points(std::iter::once(data.row(0)))
        .map_err(err)?;
    append_progress(progress_log, "create_index", "done")?;

    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(ctx.config.build_threads)
        .enable_all()
        .build()
        .map_err(err)?;

    append_progress(progress_log, "payload_encode", "start target=full_precision_store")?;
    let encode_start = Instant::now();
    {
        let n = data.nrows();
        let workers = ctx.config.build_threads.max(1).min(n);
        let chunk = n.div_ceil(workers);
        let first_error = Mutex::new(None::<String>);
        std::thread::scope(|scope| {
            for worker in 0..workers {
                let begin = worker * chunk;
                let end = (begin + chunk).min(n);
                if begin >= end {
                    continue;
                }
                let data = &data;
                let index = &index;
                let rt = &rt;
                let first_error = &first_error;
                scope.spawn(move || {
                    for id in begin..end {
                        if first_error.lock().unwrap().is_some() {
                            break;
                        }
                        let id32 = id as u32;
                        if let Err(e) = rt.block_on(index.provider().set_element(
                            &DefaultContext,
                            &id32,
                            data.row(id),
                        )) {
                            *first_error.lock().unwrap() = Some(e.to_string());
                            break;
                        }
                    }
                });
            }
        });
        if let Some(message) = first_error.into_inner().unwrap() {
            return Err(message);
        }
    }
    let payload_encode_time_ms = encode_start.elapsed().as_secs_f64() * 1000.0;
    append_progress(
        progress_log,
        "payload_encode",
        &format!("done ms={payload_encode_time_ms:.3} target=full_precision_store"),
    )?;

    append_progress(
        progress_log,
        "graph_load",
        &format!("start path={}", graph_path.display()),
    )?;
    let copy_start = Instant::now();
    copy_shared_graph(
        graph_path,
        index.provider().neighbors(),
        index.provider().total_points(),
    )?;
    let graph_build_time_ms = copy_start.elapsed().as_secs_f64() * 1000.0;
    append_progress(
        progress_log,
        "graph_load",
        &format!("done copy_ms={graph_build_time_ms:.3}"),
    )?;

    let build_time_ms = graph_build_time_ms + payload_encode_time_ms;

    append_progress(progress_log, "search", "start")?;
    let mut search_results = Vec::with_capacity(ctx.config.search_list_sizes.len());
    for &search_list_size in &ctx.config.search_list_sizes {
        let mut latencies = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let mut hits = 0_u64;
        let mut total = 0_u64;
        let mut saved_ids = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let mut saved_distances = Vec::with_capacity(queries.nrows() * ctx.config.repeats);
        let search_start = Instant::now();
        for _ in 0..ctx.config.repeats {
            for qid in 0..queries.nrows() {
                let query = queries.row(qid);
                let mut ids = vec![0_u32; K];
                let mut distances = vec![0.0_f32; K];
                let one_start = Instant::now();
                let mut output = IdDistance::new(&mut ids, &mut distances);
                let search =
                    graph::search::Knn::new(search_list_size, Some(ctx.config.search_beam_width))
                        .map_err(err)?;
                rt.block_on(index.search(
                    search,
                    &FullPrecision,
                    &DefaultContext,
                    query,
                    &mut output,
                ))
                .map_err(err)?;
                latencies.push(one_start.elapsed().as_secs_f64() * 1_000_000.0);
                hits += recall_hits(&ids, &groundtruth[qid]) as u64;
                saved_ids.push(ids);
                saved_distances.push(distances);
                total += K as u64;
            }
        }
        let mut totals = AccuracyTotals::default();
        for (idx, (ids, distances)) in saved_ids.iter().zip(&saved_distances).enumerate() {
            let qid = idx % queries.nrows();
            accumulate_accuracy(
                ids,
                distances,
                queries.row(qid),
                data.as_ref(),
                &groundtruth[qid],
                &mut totals,
            );
        }
        let (mean_relative_error, p95_relative_error, mean_absolute_error, top10_overlap, pairwise_flip_rate_top10) =
            accuracy_summary(&mut totals);
        let elapsed = search_start.elapsed().as_secs_f64();
        let recall = hits as f64 / total as f64;
        let qps = (queries.nrows() * ctx.config.repeats) as f64 / elapsed.max(1e-12);
        let latency_mean_us = latencies.iter().sum::<f64>() / latencies.len() as f64;
        let latency_p95_us = percentile(&mut latencies, 0.95);
        search_results.push(SearchResult {
            search_list_size,
            recall,
            qps,
            latency_mean_us,
            latency_p95_us,
            prepare_us: f64::NAN,
            traverse_us: f64::NAN,
            rerank_us: f64::NAN,
            neighbor_fetch_us: f64::NAN,
            visited_mark_us: f64::NAN,
            paper_batch_us: f64::NAN,
            flush_us: f64::NAN,
            visited_nodes: f64::NAN,
            distance_computations: f64::NAN,
            paper_checked: f64::NAN,
            paper_would_prune: f64::NAN,
            paper_msb_kernel_calls: f64::NAN,
            paper_remaining_kernel_calls: f64::NAN,
            ffi_calls_per_query: f64::NAN,
            mean_relative_error,
            p95_relative_error,
            mean_absolute_error,
            top10_overlap,
            pairwise_flip_rate_top10,
            query_coarse_codec: "full".to_string(),
            status: "done".to_string(),
        });
        append_progress(
            progress_log,
            "search",
            &format!("search_list_size={search_list_size} recall={recall:.6} qps={qps:.3}"),
        )?;
    }
    append_progress(progress_log, "search", "done")?;

    let payload_path = write_payload_marker(ctx, METHOD)?;
    let payload_json_path = write_payload_json(
        ctx,
        METHOD,
        &payload_path,
        ctx.dataset.dimension as f64 * 4.0,
        ctx.dataset.dimension as f64 * 4.0,
        None,
        NOTE,
    )?;
    let graph_bytes = (ctx.dataset.base_count + 1) * ctx.config.max_degree as u64 * 4;
    let base_bytes = ctx.dataset.base_count as u64 * ctx.dataset.dimension as u64 * 4;
    let index_bytes = graph_bytes + base_bytes;

    Ok(PreparedPayload {
        method: METHOD.to_string(),
        status: AdapterStatus::Done,
        has_real_metrics: true,
        payload_path,
        payload_json_path,
        nominal_bits_per_dim: 32.0,
        actual_bytes_per_vector: Some(ctx.dataset.dimension as f64 * 4.0),
        code_bytes_per_vector: Some(ctx.dataset.dimension as f64 * 4.0),
        metadata_bytes_per_vector: Some(0.0),
        codebook_bytes: None,
        train_time_ms: Some(0.0),
        encode_time_ms: Some(payload_encode_time_ms),
        build_time_ms: Some(build_time_ms),
        graph_build_time_ms: Some(graph_build_time_ms),
        shared_graph_build_time_ms: None,
        graph_build_mode: "loaded_external".to_string(),
        index_size_mb: Some(index_bytes as f64 / 1_048_576.0),
        index_bytes: Some(index_bytes),
        auxiliary_bytes: Some(0),
        residual_bytes: Some(0),
        fp32_base_bytes: Some(base_bytes),
        graph_build_distance: "fp32_l2_external".to_string(),
        peak_rss_mb: peak_rss_mb(),
        search_results,
        note: NOTE.to_string(),
    })
}

struct Heartbeat {
    stop: mpsc::Sender<()>,
    join: thread::JoinHandle<()>,
}

impl Heartbeat {
    fn start(progress_log: PathBuf, stage: &'static str) -> Self {
        let (stop, rx) = mpsc::channel();
        let join = thread::spawn(move || {
            let start = Instant::now();
            loop {
                match rx.recv_timeout(std::time::Duration::from_secs(60)) {
                    Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
                    Err(mpsc::RecvTimeoutError::Timeout) => {
                        let elapsed_s = start.elapsed().as_secs();
                        let _ = append_progress(
                            &progress_log,
                            stage,
                            &format!("still_running elapsed_s={elapsed_s}"),
                        );
                    }
                }
            }
        });
        Self { stop, join }
    }

    fn stop(self) -> Result<(), String> {
        let _ = self.stop.send(());
        self.join
            .join()
            .map_err(|_| "heartbeat thread panicked".to_string())
    }
}

struct SharedGraphPaths {
    dir: PathBuf,
    graph_path: PathBuf,
    meta_path: PathBuf,
}

impl SharedGraphPaths {
    fn new(ctx: &RunContext) -> Self {
        let dir = ctx
            .out_root
            .join(&ctx.dataset.name)
            .join("indexes/02_diskann_fair/shared_graph");
        let stem = format!(
            "diskann_fp32_R{}_Lbuild{}_alpha{}_seed{}",
            ctx.config.max_degree, ctx.config.build_beam, ctx.config.alpha, ctx.config.seed
        );
        Self {
            graph_path: dir.join(format!("{stem}.graph.bin")),
            meta_path: dir.join(format!("{stem}.graph.json")),
            dir,
        }
    }

    fn is_valid(&self, expected_meta: &str) -> bool {
        self.graph_path.exists()
            && fs::read_to_string(&self.meta_path)
                .map(|text| text.starts_with(expected_meta))
                .unwrap_or(false)
    }
}

fn shared_start_index(seed: u64, nrows: usize) -> usize {
    let mut rng = StdRng::seed_from_u64(seed ^ 0xD15C_AAA4_F32B_0001);
    rng.random_range(0..nrows)
}

fn train_global_center_matrix(data: &Matrix<f32>, sample_count: usize, seed: u32) -> Vec<f32> {
    let actual_sample_count = std::cmp::min(sample_count, data.nrows());
    assert!(actual_sample_count != 0);
    let mut sample_ids: Vec<usize> = (0..data.nrows()).collect();
    let mut rng = StdRng::seed_from_u64(seed as u64);
    for i in 0..actual_sample_count {
        let pick = rng.random_range(i..data.nrows());
        sample_ids.swap(i, pick);
    }
    sample_ids.truncate(actual_sample_count);
    sample_ids.sort_unstable();

    let mut center = vec![0.0_f32; data.ncols()];
    for id in sample_ids {
        let row = data.row(id);
        for (dst, src) in center.iter_mut().zip(row.iter()) {
            *dst += *src;
        }
    }
    let inv = 1.0 / actual_sample_count as f32;
    for value in &mut center {
        *value *= inv;
    }
    center
}

fn train_kmeans_centroids_matrix(
    data: &Matrix<f32>,
    sample_count: usize,
    k: usize,
    seed: u32,
    iters: i32,
) -> Result<Vec<f32>, String> {
    let nrows = data.nrows();
    let dim = data.ncols();
    let sample_count = std::cmp::min(sample_count, nrows);
    if sample_count == 0 {
        return Err("empty sample for kmeans centroid training".to_string());
    }
    let mut sample = Vec::with_capacity(sample_count * dim);
    let mut rng = StdRng::seed_from_u64(seed as u64);
    for _ in 0..sample_count {
        let id = rng.random_range(0..nrows);
        sample.extend_from_slice(data.row(id));
    }
    RabitqSpace::train_kmeans(&sample, sample_count, dim, k, seed, iters)
}

fn method_seed(method: &str) -> u64 {
    method.bytes().fold(0x9E37_79B9_7F4A_7C15_u64, |acc, byte| {
        acc.rotate_left(5) ^ byte as u64
    })
}

fn shared_graph_key(ctx: &RunContext, nrows: usize, dim: usize, start_index: usize) -> String {
    let intra_label = if ctx.config.intra_batch_candidates == 0 {
        "None".to_string()
    } else {
        format!("Max({})", ctx.config.intra_batch_candidates)
    };
    format!(
        concat!(
            "suite=02_diskann_fair\n",
            "graph_role=shared_fp32_vamana\n",
            "dataset={}\n",
            "base_path={}\n",
            "base_count={}\n",
            "loaded_base_count={}\n",
            "dimension={}\n",
            "loaded_dimension={}\n",
            "metric=L2\n",
            "max_degree={}\n",
            "build_beam={}\n",
            "alpha={}\n",
            "seed={}\n",
            "start_index={}\n",
            "graph_build_batch_size={}\n",
            "graph_build_intra_batch_candidates={}\n",
            "graph_build_distance=fp32_l2\n"
        ),
        ctx.dataset.name,
        ctx.dataset.base_path.display(),
        ctx.dataset.base_count,
        nrows,
        ctx.dataset.dimension,
        dim,
        ctx.config.max_degree,
        ctx.config.build_beam,
        ctx.config.alpha,
        ctx.config.seed,
        start_index,
        ctx.config.build_batch_size,
        intra_label
    )
}

fn copy_shared_graph(
    graph_path: &Path,
    target: &SimpleNeighborProviderAsync,
    _total_points: usize,
) -> Result<(), String> {
    let source = SimpleNeighborProviderAsync::load_direct(
        &FileStorageProvider,
        graph_path.to_string_lossy().as_ref(),
    )
    .map_err(err)?;
    let total = count_diskann_graph_nodes(graph_path)?;
    for id in 0..total {
        let mut list = AdjacencyList::new();
        source.get_neighbors_sync(id, &mut list).map_err(err)?;
        target.set_neighbors_sync(id, &list).map_err(err)?;
    }
    Ok(())
}

/// Count the nodes in a canonical DiskANN graph file (24-byte header, then
/// per node a u32 length followed by that many u32 neighbor ids).
fn count_diskann_graph_nodes(path: &Path) -> Result<usize, String> {
    let mut reader = BufReader::new(fs::File::open(path).map_err(err)?);
    let mut header = [0_u8; 24];
    reader.read_exact(&mut header).map_err(err)?;
    let file_size = u64::from_le_bytes(header[0..8].try_into().unwrap()) as usize;
    let mut position = 24_usize;
    let mut nodes = 0_usize;
    while position < file_size {
        let mut len_buf = [0_u8; 4];
        reader.read_exact(&mut len_buf).map_err(err)?;
        let len = u32::from_le_bytes(len_buf) as usize;
        position += 4 + len * 4;
        reader.seek(SeekFrom::Current(len as i64 * 4)).map_err(err)?;
        nodes += 1;
    }
    Ok(nodes)
}

fn count_diskann_graph_edges(path: &Path) -> Result<usize, String> {
    let mut reader = BufReader::new(fs::File::open(path).map_err(err)?);
    let mut header = [0_u8; 24];
    reader.read_exact(&mut header).map_err(err)?;
    let file_size = u64::from_le_bytes(header[0..8].try_into().unwrap()) as usize;
    let mut position = 24_usize;
    let mut edges = 0_usize;
    while position < file_size {
        let mut len_buf = [0_u8; 4];
        reader.read_exact(&mut len_buf).map_err(err)?;
        let len = u32::from_le_bytes(len_buf) as usize;
        position += 4 + len * 4;
        reader.seek(SeekFrom::Current(len as i64 * 4)).map_err(err)?;
        edges += len;
    }
    Ok(edges)
}

fn parse_meta_f64(text: &str, key: &str) -> Option<f64> {
    parse_meta_value(text, key)?.parse().ok()
}

fn parse_meta_u64(text: &str, key: &str) -> Option<u64> {
    parse_meta_value(text, key)?.parse().ok()
}

fn parse_meta_value<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    text.lines()
        .find_map(|line| line.strip_prefix(key)?.strip_prefix('='))
}

fn read_fvecs_matrix(path: &Path) -> Result<Matrix<f32>, String> {
    let mut reader = BufReader::new(fs::File::open(path).map_err(err)?);
    let mut all = Vec::new();
    let mut dim: Option<usize> = None;
    loop {
        let mut dim_buf = [0_u8; 4];
        match reader.read_exact(&mut dim_buf) {
            Ok(()) => {}
            Err(err) if err.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(err) => return Err(err.to_string()),
        }
        let row_dim = i32::from_le_bytes(dim_buf);
        if row_dim <= 0 {
            return Err(format!(
                "{} has invalid fvecs dim {row_dim}",
                path.display()
            ));
        }
        let row_dim = row_dim as usize;
        if let Some(expected) = dim {
            if row_dim != expected {
                return Err(format!(
                    "{} fvecs dimension changed from {expected} to {row_dim}",
                    path.display()
                ));
            }
        } else {
            dim = Some(row_dim);
        }
        let offset = all.len();
        all.resize(offset + row_dim, 0.0);
        let bytes = unsafe {
            std::slice::from_raw_parts_mut(
                all[offset..].as_mut_ptr() as *mut u8,
                row_dim * std::mem::size_of::<f32>(),
            )
        };
        reader.read_exact(bytes).map_err(err)?;
    }
    let dim = dim.ok_or_else(|| format!("{} is empty", path.display()))?;
    let nrows = all.len() / dim;
    Matrix::try_from(all.into_boxed_slice(), nrows, dim).map_err(err)
}

fn read_ivecs_topk(path: &Path, k: usize) -> Result<Vec<Vec<u32>>, String> {
    let mut reader = BufReader::new(fs::File::open(path).map_err(err)?);
    let mut rows = Vec::new();
    loop {
        let mut dim_buf = [0_u8; 4];
        match reader.read_exact(&mut dim_buf) {
            Ok(()) => {}
            Err(err) if err.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(err) => return Err(err.to_string()),
        }
        let row_dim = i32::from_le_bytes(dim_buf);
        if row_dim < k as i32 {
            return Err(format!(
                "{} groundtruth k={} is smaller than requested recall@{k}",
                path.display(),
                row_dim
            ));
        }
        let mut row = Vec::with_capacity(k);
        for i in 0..row_dim as usize {
            let mut id_buf = [0_u8; 4];
            reader.read_exact(&mut id_buf).map_err(err)?;
            if i < k {
                row.push(i32::from_le_bytes(id_buf) as u32);
            }
        }
        rows.push(row);
    }
    Ok(rows)
}

fn recall_hits(ids: &[u32], gt: &[u32]) -> usize {
    ids.iter().filter(|id| gt.contains(id)).count()
}

fn percentile(values: &mut [f64], p: f64) -> f64 {
    values.sort_by(|a, b| a.total_cmp(b));
    let index = ((values.len().saturating_sub(1)) as f64 * p).round() as usize;
    values[index]
}

fn reset_progress(path: &Path, ctx: &RunContext, method: &str) -> Result<(), String> {
    let mut file = fs::File::create(path).map_err(err)?;
    writeln!(file, "experiment_profile=02_diskann_payload_fair").map_err(err)?;
    writeln!(file, "dataset={}", ctx.dataset.name).map_err(err)?;
    writeln!(file, "method={method}").map_err(err)?;
    writeln!(file, "status=running").map_err(err)?;
    Ok(())
}

fn append_progress(path: &Path, stage: &str, status: &str) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .append(true)
        .create(true)
        .open(path)
        .map_err(err)?;
    writeln!(file, "progress_stage={stage} status={status}").map_err(err)
}

fn write_payload_marker(ctx: &RunContext, method: &str) -> Result<PathBuf, String> {
    let method_dir = ctx
        .out_root
        .join(&ctx.dataset.name)
        .join("indexes/02_diskann_fair")
        .join(method);
    fs::create_dir_all(&method_dir).map_err(err)?;
    let payload_path = method_dir.join(format!("{method}_bpd4_payload.bin"));
    fs::write(&payload_path, b"in_memory_diskann_quant_provider\n").map_err(err)?;
    Ok(payload_path)
}

fn write_payload_json(
    ctx: &RunContext,
    method: &str,
    payload_path: &Path,
    actual_bytes_per_vector: f64,
    code_bytes_per_vector: f64,
    codebook_bytes: Option<u64>,
    note: &str,
) -> Result<PathBuf, String> {
    let payload_json_path = payload_path.with_extension("json");
    let json = format!(
        concat!(
            "{{\n",
            "  \"suite\": \"02_diskann_fair\",\n",
            "  \"dataset\": \"{}\",\n",
            "  \"method\": \"{}\",\n",
            "  \"status\": \"done\",\n",
            "  \"has_real_metrics\": true,\n",
            "  \"nominal_bits_per_dim\": 4,\n",
            "  \"actual_bytes_per_vector\": {:.6},\n",
            "  \"code_bytes_per_vector\": {:.6},\n",
            "  \"codebook_bytes\": {},\n",
            "  \"payload_path\": \"{}\",\n",
            "  \"note\": \"{}\"\n",
            "}}\n"
        ),
        ctx.dataset.name,
        method,
        actual_bytes_per_vector,
        code_bytes_per_vector,
        codebook_bytes
            .map(|value| value.to_string())
            .unwrap_or_else(|| "null".to_string()),
        payload_path.display(),
        note.replace('\\', "\\\\").replace('"', "\\\""),
    );
    fs::write(&payload_json_path, json).map_err(err)?;
    Ok(payload_json_path)
}

#[derive(Default)]
struct AccuracyTotals {
    rel_errors: Vec<f64>,
    abs_error_sum: f64,
    overlap: u64,
    evaluated: u64,
    flipped_pairs: u64,
    total_pairs: u64,
}

fn exact_l2_sqr(query: &[f32], vector: &[f32]) -> f32 {
    let n = query.len().min(vector.len());
    let mut sum = 0.0_f32;
    for i in 0..n {
        let diff = query[i] - vector[i];
        sum += diff * diff;
    }
    sum
}

fn accumulate_accuracy(
    ids: &[u32],
    distances: &[f32],
    query: &[f32],
    data: &Matrix<f32>,
    groundtruth: &[u32],
    totals: &mut AccuracyTotals,
) {
    let n = ids.len().min(distances.len());
    if n == 0 {
        return;
    }
    let mut exact = Vec::with_capacity(n);
    for (idx, &id) in ids.iter().enumerate().take(n) {
        let exact_distance = exact_l2_sqr(query, data.row(id as usize));
        exact.push((exact_distance, id));
        let reported = distances[idx] as f64;
        let exact_d = exact_distance as f64;
        let relative = if exact_d > 1e-12 {
            (reported - exact_d).abs() / exact_d
        } else {
            0.0
        };
        totals.rel_errors.push(relative);
        totals.abs_error_sum += (reported - exact_d).abs();
        totals.evaluated += 1;
        if groundtruth.contains(&id) {
            totals.overlap += 1;
        }
    }
    for i in 0..n {
        for j in (i + 1)..n {
            totals.total_pairs += 1;
            let reported_ordered = distances[i] <= distances[j];
            let exact_ordered = exact[i].0 <= exact[j].0;
            if reported_ordered != exact_ordered {
                totals.flipped_pairs += 1;
            }
        }
    }
}

fn accuracy_summary(totals: &mut AccuracyTotals) -> (f64, f64, f64, f64, f64) {
    let count = totals.evaluated.max(1) as f64;
    let mean_relative_error = if totals.rel_errors.is_empty() {
        f64::NAN
    } else {
        totals.rel_errors.iter().sum::<f64>() / totals.rel_errors.len() as f64
    };
    let mut sorted = totals.rel_errors.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let p95_relative_error = if sorted.is_empty() {
        f64::NAN
    } else {
        let rank = ((sorted.len() as f64) * 0.95).ceil() as usize;
        sorted[rank.clamp(1, sorted.len()) - 1]
    };
    let mean_absolute_error = totals.abs_error_sum / count;
    let top10_overlap = totals.overlap as f64 / count;
    let pairwise_flip_rate_top10 = if totals.total_pairs > 0 {
        totals.flipped_pairs as f64 / totals.total_pairs as f64
    } else {
        f64::NAN
    };
    (
        mean_relative_error,
        p95_relative_error,
        mean_absolute_error,
        top10_overlap,
        pairwise_flip_rate_top10,
    )
}

fn peak_rss_mb() -> Option<f64> {
    let status = fs::read_to_string("/proc/self/status").ok()?;
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("VmHWM:") {
            let kb = rest.split_whitespace().next()?.parse::<f64>().ok()?;
            return Some(kb / 1024.0);
        }
    }
    None
}

fn err<E: std::fmt::Display>(err: E) -> String {
    err.to_string()
}
