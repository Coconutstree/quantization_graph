use std::path::PathBuf;

use crate::args::Args;
use crate::dataset::DatasetInfo;

#[derive(Debug, Clone)]
pub struct DiskAnnConfig {
    pub max_degree: usize,
    pub build_beam: usize,
    pub alpha: f32,
    pub search_beam_width: usize,
    pub search_list_sizes: Vec<usize>,
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
    pub centroid_count: usize,
    pub centroid_train_samples: usize,
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
                centroid_count: args.centroid_count,
                centroid_train_samples: args.centroid_train_samples,
            },
            out_root: args.out_root.clone(),
            graph_file: args.graph_file.clone(),
        }
    }
}
