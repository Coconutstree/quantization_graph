/*
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

pub mod search_output_buffer;
pub use search_output_buffer::{
    BufferState, IdDistance, IdDistanceAssociatedData, SearchOutputBuffer,
};

pub mod adjacencylist;
pub use adjacencylist::AdjacencyList;

pub mod config;
pub use config::Config;

use std::sync::atomic::{AtomicU64, Ordering};

/// Total distance evaluations (search + prune) performed by graph construction
/// since the last [`reset_graph_build_distance_evaluations`].
static GRAPH_BUILD_DISTANCE_EVALUATIONS: AtomicU64 = AtomicU64::new(0);

/// Reset the graph-construction distance counter before a build pass.
pub fn reset_graph_build_distance_evaluations() {
    GRAPH_BUILD_DISTANCE_EVALUATIONS.store(0, Ordering::Relaxed);
}

/// Return the number of distance evaluations recorded during graph construction.
pub fn graph_build_distance_evaluations() -> u64 {
    GRAPH_BUILD_DISTANCE_EVALUATIONS.load(Ordering::Relaxed)
}

pub(crate) fn add_graph_build_distance_evaluations(count: u64) {
    GRAPH_BUILD_DISTANCE_EVALUATIONS.fetch_add(count, Ordering::Relaxed);
}

pub mod index;
pub use index::DiskANNIndex;

mod start_point;
pub use start_point::{SampleableForStart, StartPointStrategy};

mod misc;
pub use misc::{ConsolidateKind, InplaceDeleteMethod};

pub mod glue;
pub mod search;
pub mod workingset;

pub mod ext;

// Re-export the Search trait and error/output types only.
// Search parameter types (Knn, Range, Diverse, etc.) should be accessed via `graph::search::`.
pub use search::{KnnSearchError, RangeSearchError, Search};

mod internal;

pub mod strategy;

// Integration tests and test providers.
#[cfg(any(test, feature = "testing"))]
pub mod test;
