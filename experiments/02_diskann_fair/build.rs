use std::env;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let repo_root = manifest_dir.join("../..").canonicalize().unwrap();
    let out_dir = PathBuf::from(env::var("OUT_DIR").unwrap());
    let sources = [
        manifest_dir.join("native/rabitq_bridge.cpp"),
        repo_root.join("experiments/05_disk_system_fair/native/direct_io.cpp"),
        repo_root.join("experiments/05_disk_system_fair/native_rust/direct_io_bridge.cpp"),
    ];
    let archive = out_dir.join("librabitq_bridge.a");

    let cxx = env::var("CXX").unwrap_or_else(|_| "g++".to_string());
    let ar = env::var("AR").unwrap_or_else(|_| "ar".to_string());

    let mut objects = Vec::new();
    for (index, source) in sources.iter().enumerate() {
        let object = out_dir.join(format!("qgraph_native_{index}.o"));
        let status = Command::new(&cxx)
            .arg("-std=c++20")
            .arg("-O3")
            .arg("-march=native")
            .arg("-fopenmp")
            .arg("-fPIC")
            .arg("-I")
            .arg(&repo_root)
            .arg("-I")
            .arg(repo_root.join("experiments/05_disk_system_fair/native"))
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
    println!(
        "cargo:rerun-if-changed={}",
        repo_root.join("Ours/core/hnswlib/space_rabitq.h").display()
    );
    println!("cargo:rustc-link-search=native={}", out_dir.display());
    println!("cargo:rustc-link-search=native=/usr/lib/x86_64-linux-gnu");
    println!("cargo:rustc-link-lib=static=rabitq_bridge");
    println!("cargo:rustc-link-lib=dylib=aio");
    println!("cargo:rustc-link-lib=dylib=gomp");
    println!("cargo:rustc-link-lib=dylib=stdc++");
}
