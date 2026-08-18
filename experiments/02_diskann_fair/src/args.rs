use std::env;
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct Args {
    pub dataset: String,
    pub methods: Vec<String>,
    pub data_root: PathBuf,
    pub out_root: PathBuf,
    pub query_path: Option<PathBuf>,
    pub gt_path: Option<PathBuf>,
    pub graph_file: Option<PathBuf>,
    pub max_degree: usize,
    pub build_beam: usize,
    pub alpha: f32,
    pub search_list_sizes: Vec<usize>,
    pub search_beam_width: usize,
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

impl Default for Args {
    fn default() -> Self {
        Self {
            dataset: "dbpedia".to_string(),
            methods: vec!["PQ".to_string()],
            data_root: PathBuf::from("data"),
            out_root: PathBuf::from("results"),
            query_path: None,
            gt_path: None,
            graph_file: None,
            max_degree: 32,
            build_beam: 400,
            alpha: 1.2,
            search_list_sizes: vec![
                10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
                40, 50, 60, 70, 80, 90, 100, 140, 180, 220, 260, 300, 340, 380, 420, 460,
            ],
            search_beam_width: 1,
            rerank_candidates: 100,
            threads: 1,
            build_threads: 64,
            repeats: 5,
            seed: 20260813,
            build_batch_size: 32768,
            intra_batch_candidates: 32,
            refine_passes: 0,
            prune_candidate_cap: 0,
            build_early_stop_hops: 0,
            search_early_stop_hops: 0,
            search_kth_stop: false,
            centroid_count: 1,
            centroid_train_samples: 100000,
        }
    }
}

impl Args {
    pub fn parse() -> Result<Self, String> {
        let mut args = Args::default();
        let mut iter = env::args().skip(1);
        while let Some(flag) = iter.next() {
            let value = match flag.as_str() {
                "--dataset"
                | "--methods"
                | "--data-root"
                | "--out-root"
                | "--query-path"
                | "--gt-path"
                | "--graph-file"
                | "--max-degree"
                | "--build-beam"
                | "--alpha"
                | "--search-list-sizes"
                | "--search-beam-width"
                | "--rerank-candidates"
                | "--threads"
                | "--build-threads"
                | "--repeats"
                | "--seed"
                | "--build-batch-size"
                | "--intra-batch-candidates"
                | "--refine-passes"
                | "--build-prune-cap"
                | "--build-early-stop-hops"
                | "--search-early-stop-hops"
                | "--search-kth-stop"
                | "--centroid-count"
                | "--centroid-train-samples" => iter
                    .next()
                    .ok_or_else(|| format!("missing value for {flag}"))?,
                "--help" | "-h" => return Err(Self::usage()),
                other => return Err(format!("unknown argument: {other}\n\n{}", Self::usage())),
            };

            match flag.as_str() {
                "--dataset" => args.dataset = value,
                "--methods" => {
                    args.methods = value
                        .split(',')
                        .map(|part| part.trim().to_ascii_uppercase())
                        .filter(|part| !part.is_empty())
                        .collect();
                }
                "--data-root" => args.data_root = PathBuf::from(value),
                "--out-root" => args.out_root = PathBuf::from(value),
                "--query-path" => args.query_path = Some(PathBuf::from(value)),
                "--gt-path" => args.gt_path = Some(PathBuf::from(value)),
                "--graph-file" => args.graph_file = Some(PathBuf::from(value)),
                "--max-degree" => args.max_degree = parse_num(&flag, &value)?,
                "--build-beam" => args.build_beam = parse_num(&flag, &value)?,
                "--alpha" => {
                    args.alpha = value.parse().map_err(|_| format!("bad {flag}: {value}"))?
                }
                "--search-list-sizes" => args.search_list_sizes = parse_list(&value)?,
                "--search-beam-width" => args.search_beam_width = parse_num(&flag, &value)?,
                "--rerank-candidates" => args.rerank_candidates = parse_num(&flag, &value)?,
                "--threads" => args.threads = parse_num(&flag, &value)?,
                "--build-threads" => args.build_threads = parse_num(&flag, &value)?,
                "--repeats" => args.repeats = parse_num(&flag, &value)?,
                "--seed" => {
                    args.seed = value.parse().map_err(|_| format!("bad {flag}: {value}"))?
                }
                "--build-batch-size" => args.build_batch_size = parse_num(&flag, &value)?,
                "--intra-batch-candidates" => {
                    args.intra_batch_candidates = parse_num(&flag, &value)?
                }
                "--refine-passes" => args.refine_passes = parse_num(&flag, &value)?,
                "--build-prune-cap" => args.prune_candidate_cap = parse_num(&flag, &value)?,
                "--build-early-stop-hops" => args.build_early_stop_hops = parse_num(&flag, &value)?,
                "--search-early-stop-hops" => args.search_early_stop_hops = parse_num(&flag, &value)?,
                "--search-kth-stop" => {
                    args.search_kth_stop = parse_num::<usize>(&flag, &value)? != 0
                }
                "--centroid-count" => args.centroid_count = parse_num(&flag, &value)?,
                "--centroid-train-samples" => {
                    args.centroid_train_samples = parse_num(&flag, &value)?
                }
                _ => unreachable!(),
            }
        }

        if args.methods.is_empty() {
            return Err("--methods resolved to an empty method list".to_string());
        }
        Ok(args)
    }

    fn usage() -> String {
        "usage: run_diskann_fair --dataset dbpedia --methods PQ,SQ\nformal defaults already set data-root/out-root/max-degree/build-beam/search-list-sizes/search-beam-width/threads/repeats/seed/centroid-count/centroid-train-samples".to_string()
    }
}

fn parse_num<T>(flag: &str, value: &str) -> Result<T, String>
where
    T: std::str::FromStr,
{
    value.parse().map_err(|_| format!("bad {flag}: {value}"))
}

fn parse_list(value: &str) -> Result<Vec<usize>, String> {
    let mut out = Vec::new();
    for part in value.split(',') {
        let item = part.trim();
        if item.is_empty() {
            continue;
        }
        out.push(
            item.parse()
                .map_err(|_| format!("bad --search-list-sizes item: {item}"))?,
        );
    }
    if out.is_empty() {
        return Err("--search-list-sizes cannot be empty".to_string());
    }
    Ok(out)
}
