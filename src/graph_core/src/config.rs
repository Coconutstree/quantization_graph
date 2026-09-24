use std::path::PathBuf;

use crate::args::Args;
use crate::dataset::DatasetInfo;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueryCoarseCodec {
    Full,
    /// Diagnostic twin of `Full`: identical query/DB representation and
    /// pruning math, but forces the scalar per-candidate remaining-bit path.
    FullScalar,
    /// Diagnostic twin of `Full`: keeps the old staged 1-bit gate while the
    /// remaining-3-bit path stays batched.
    FullStaged,
    /// Diagnostic twin of `Full`: keeps the optimized gate and remaining-bit
    /// path, but forces the old per-candidate residual rerank loop.
    FullRerankScalar,
    B1,
    Int4,
    Int8,
    B1Main,
}

impl QueryCoarseCodec {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value.to_ascii_lowercase().as_str() {
            "full" => Ok(Self::Full),
            "full-scalar" | "fullscalar" => Ok(Self::FullScalar),
            "full-staged" | "fullstaged" => Ok(Self::FullStaged),
            "full-rerank-scalar" | "fullrerankscalar" => Ok(Self::FullRerankScalar),
            "b1" => Ok(Self::B1),
            "int4" => Ok(Self::Int4),
            "int8" => Ok(Self::Int8),
            "b1main" => Ok(Self::B1Main),
            other => Err(format!(
                "bad --query-coarse-codec: {other}; expected full|full-scalar|full-staged|full-rerank-scalar|b1|int4|int8|b1main"
            )),
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::FullScalar => "full-scalar",
            Self::FullStaged => "full-staged",
            Self::FullRerankScalar => "full-rerank-scalar",
            Self::B1 => "b1",
            Self::Int4 => "int4",
            Self::Int8 => "int8",
            Self::B1Main => "b1main",
        }
    }
}

#[derive(Debug, Clone)]
pub struct DiskAnnConfig {
    pub max_degree: usize,
    pub build_beam: usize,
    pub alpha: f32,
    pub search_beam_width: usize,
    pub search_list_sizes: Vec<usize>,
    pub b1_epsilon: f32,
    pub rerank_candidates: usize,
    pub threads: usize,
    pub build_threads: usize,
    pub repeats: usize,
    pub seed: u64,
    pub build_batch_size: usize,
    pub intra_batch_candidates: u32,
    pub refine_passes: usize,
    pub prune_candidate_cap: usize,
    pub build_early_stop_hops: usize,
    pub search_early_stop_hops: usize,
    pub search_kth_stop: bool,
    pub shared_graph: bool,
    pub centroid_count: usize,
    pub centroid_train_samples: usize,
    pub query_coarse_codecs: Vec<QueryCoarseCodec>,
    pub adaptive_route_dir: Option<PathBuf>,
    pub adaptive_route_dim: usize,
}

#[derive(Debug, Clone)]
pub struct RunContext {
    pub dataset: DatasetInfo,
    pub config: DiskAnnConfig,
    pub out_root: PathBuf,
    pub graph_file: Option<PathBuf>,
}

impl RunContext {
    pub fn from_args(args: &Args, dataset: DatasetInfo) -> Self {
        Self {
            dataset,
            config: DiskAnnConfig {
                max_degree: args.max_degree,
                build_beam: args.build_beam,
                alpha: args.alpha,
                search_beam_width: args.search_beam_width,
                search_list_sizes: args.search_list_sizes.clone(),
                b1_epsilon: args.b1_epsilon,
                rerank_candidates: args.rerank_candidates,
                threads: args.threads,
                build_threads: args.build_threads,
                repeats: args.repeats,
                seed: args.seed,
                build_batch_size: args.build_batch_size,
                intra_batch_candidates: args.intra_batch_candidates,
                refine_passes: args.refine_passes,
                prune_candidate_cap: args.prune_candidate_cap,
                build_early_stop_hops: args.build_early_stop_hops,
                search_early_stop_hops: args.search_early_stop_hops,
                search_kth_stop: args.search_kth_stop,
                shared_graph: args.shared_graph,
                centroid_count: args.centroid_count,
                centroid_train_samples: args.centroid_train_samples,
                query_coarse_codecs: args.query_coarse_codecs.clone(),
                adaptive_route_dir: args.adaptive_route_dir.clone(),
                adaptive_route_dim: args.adaptive_route_dim,
            },
            out_root: args.out_root.clone(),
            graph_file: args.graph_file.clone(),
        }
    }
}
