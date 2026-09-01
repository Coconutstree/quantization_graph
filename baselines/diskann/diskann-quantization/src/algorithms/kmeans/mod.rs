/*
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

pub(crate) mod common;
pub(crate) use common::square_norm;

pub mod lloyds;
pub mod plusplus;

use std::sync::atomic::{AtomicU64, Ordering};

/// Process-wide counter of distance computations performed by the k-means
/// kernels (k-means++ initialization + Lloyd iterations). The port reads and
/// resets this after PQ training so the export artifact can report build-time
/// distance counts without changing any training signature.
static KMEANS_DISTANCE_COUNT: AtomicU64 = AtomicU64::new(0);

#[inline]
pub fn add_kmeans_distance_count(n: u64) {
    KMEANS_DISTANCE_COUNT.fetch_add(n, Ordering::Relaxed);
}

#[inline]
pub fn take_kmeans_distance_count() -> u64 {
    KMEANS_DISTANCE_COUNT.swap(0, Ordering::Relaxed)
}
