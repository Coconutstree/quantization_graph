mod args;
mod config;
mod dataset;
mod diskann_runner;
mod logging;
mod ours_diskann;
mod payload;

use args::Args;
use config::RunContext;
use dataset::DatasetInfo;
use logging::{append_manifest, append_raw_csv, create_output_dirs, write_raw_log};
use payload::adapter_for;

fn main() {
    if let Err(err) = run() {
        eprintln!("error: {err}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let args = Args::parse()?;
    let dataset = DatasetInfo::load(
        &args.dataset,
        &args.data_root,
        args.query_path.as_deref(),
        args.gt_path.as_deref(),
    )?;
    let ctx = RunContext::from_args(&args, dataset);

    println!("suite=02_diskann_fair");
    println!("dataset={}", ctx.dataset.name);
    println!("base_path={}", ctx.dataset.base_path.display());
    println!("query_path={}", ctx.dataset.query_path.display());
    println!("gt_path={}", ctx.dataset.gt_path.display());
    println!("max_degree={}", ctx.config.max_degree);
    println!("build_beam={}", ctx.config.build_beam);
    println!("methods={}", args.methods.join(","));
    println!(
        "query_coarse_codecs={}",
        ctx.config
            .query_coarse_codecs
            .iter()
            .map(|c| c.as_str())
            .collect::<Vec<_>>()
            .join(",")
    );

    for method in &args.methods {
        let adapter = adapter_for(method)?;
        let paths = create_output_dirs(&ctx, adapter.method())?;
        logging::ensure_parent(&paths.raw_log)?;
        let prepared = adapter.prepare(&ctx, &paths.raw_log)?;
        write_raw_log(&ctx, &prepared, &paths)?;
        append_raw_csv(&ctx, &prepared, &paths)?;
        append_manifest(&ctx, &prepared, &paths)?;
        println!(
            "method={} status={} metrics={} raw_log={}",
            prepared.method,
            prepared.status,
            if prepared.has_real_metrics {
                "real"
            } else {
                "not_run"
            },
            paths.raw_log.display()
        );
    }

    Ok(())
}
