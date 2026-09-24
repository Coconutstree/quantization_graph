use std::env;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let repo_root = manifest_dir.join("../..").canonicalize().unwrap();
    let out_dir = PathBuf::from(env::var("OUT_DIR").unwrap());
    let sources = [
        repo_root.join("experiments/02_disk_shared_graph/native/routing_io.cpp"),
        manifest_dir.join("native/rabitq_bridge.cpp"),
        repo_root.join("src/disk_bench/native/direct_io.cpp"),
        repo_root.join("experiments/02_disk_shared_graph/native/direct_io_bridge.cpp"),
    ];
    let archive = out_dir.join("librabitq_bridge.a");

    let cxx = env::var("CXX").unwrap_or_else(|_| "g++".to_string());
    let ar = env::var("AR").unwrap_or_else(|_| "ar".to_string());

    let mut objects = Vec::new();
    for (index, source) in sources.iter().enumerate() {
        let object = out_dir.join(format!("qgraph_native_{index}.o"));
        // Ubuntu 22.04's default GCC 9 accepts the C++20 dialect as
        // `c++2a`; newer compilers accept both spellings.  Keep the build
        // portable so the adaptive route bridge can be compiled locally.
        let cxx_std = env::var("QGRAPH_CXX_STD").unwrap_or_else(|_| "c++2a".to_string());
        let status = Command::new(&cxx)
            .arg(format!("-std={cxx_std}"))
            .arg("-O3")
            .arg("-march=native")
            .arg("-fopenmp")
            .arg("-fPIC")
            .arg("-I")
            .arg(&repo_root)
            .arg("-I")
            .arg(repo_root.join("src/disk_bench/native"))
            .arg("-c")
            .arg(source)
            .arg("-o")
            .arg(&object)
            .status()
            .expect("failed to invoke C++ compiler for a native bridge");
        if !status.success() {
            panic!("C++ compiler failed while compiling {}", source.display());
        }
        objects.push(object);
    }

    let mut archive_command = Command::new(&ar);
    archive_command.arg("crus").arg(&archive);
    archive_command.args(&objects);
    let status = archive_command
        .status()
        .expect("failed to invoke ar for native bridges");
    if !status.success() {
        panic!("ar failed while archiving {}", archive.display());
    }

    for source in &sources {
        println!("cargo:rerun-if-changed={}", source.display());
    }
    println!("cargo:rerun-if-changed={}", repo_root.join("src/disk_bench/native/query_page_cache.hpp").display());
    println!(
        "cargo:rerun-if-changed={}",
        repo_root.join("Ours/core/hnswlib/space_rabitq.h").display()
    );
    println!("cargo:rustc-link-search=native={}", out_dir.display());
    println!("cargo:rustc-link-search=native=/usr/lib/x86_64-linux-gnu");
    println!("cargo:rustc-link-lib=static:+whole-archive=rabitq_bridge");
    println!("cargo:rustc-link-arg-bins={}", archive.display());
    println!("cargo:rustc-link-lib=dylib=aio");
    println!("cargo:rustc-link-lib=dylib=gomp");
    println!("cargo:rustc-link-lib=dylib=stdc++");
    println!("cargo:rustc-link-arg-bins=-laio");
    println!("cargo:rustc-link-arg-bins=-lgomp");
    println!("cargo:rustc-link-arg-bins=-lstdc++");
}
