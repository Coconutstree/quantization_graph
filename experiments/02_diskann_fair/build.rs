use std::env;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let repo_root = manifest_dir.join("../..").canonicalize().unwrap();
    let out_dir = PathBuf::from(env::var("OUT_DIR").unwrap());
    let source = manifest_dir.join("native/rabitq_bridge.cpp");
    let object = out_dir.join("rabitq_bridge.o");
    let archive = out_dir.join("librabitq_bridge.a");

    let status = Command::new("g++")
        .arg("-std=c++17")
        .arg("-O3")
        .arg("-march=native")
        .arg("-fopenmp")
        .arg("-fPIC")
        .arg("-I")
        .arg(&repo_root)
        .arg("-c")
        .arg(&source)
        .arg("-o")
        .arg(&object)
        .status()
        .expect("failed to invoke g++ for rabitq bridge");
    if !status.success() {
        panic!("g++ failed while compiling {}", source.display());
    }

    let status = Command::new("ar")
        .arg("crus")
        .arg(&archive)
        .arg(&object)
        .status()
        .expect("failed to invoke ar for rabitq bridge");
    if !status.success() {
        panic!("ar failed while archiving {}", archive.display());
    }

    println!("cargo:rerun-if-changed={}", source.display());
    println!("cargo:rerun-if-changed={}", repo_root.join("Ours/core/hnswlib/space_rabitq.h").display());
    println!("cargo:rustc-link-search=native={}", out_dir.display());
    println!("cargo:rustc-link-lib=static=rabitq_bridge");
    println!("cargo:rustc-link-lib=dylib=gomp");
    println!("cargo:rustc-link-lib=dylib=stdc++");
}
