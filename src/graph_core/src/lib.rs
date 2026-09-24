//! Reusable implementation of experiment 02.
//!
//! The formal 05B disk port depends on this library so the resident navigation
//! codecs and the ExRaBitQ bridge are compiled from the exact same sources as
//! the in-memory experiment.  The `run_diskann_fair` binary remains unchanged.

pub mod args;
pub mod config;
pub mod dataset;
#[path = "../../../experiments/02_disk_shared_graph/native/src/lib.rs"]
pub mod disk_port;
pub mod diskann_runner;
pub mod logging;
pub mod ours_diskann;
pub mod payload;
