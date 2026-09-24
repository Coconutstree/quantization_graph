//! Experimental physical layout; logical IDs and search order stay unchanged.
use super::{ann_error, DirectAioHandle, IoStats, QueryPageCache, PAGE_SIZE};
use diskann::ANNResult;
use std::{collections::HashMap, fs, path::{Path, PathBuf}, sync::{Arc, Mutex}, time::Instant};

pub struct LocalityLayout {
    pub dir: PathBuf,
    pub slots: Vec<u32>,
    pub graph_bytes: usize,
    pub compact_bytes: usize,
    pub residual_bytes: usize,
    pub max_degree: usize,
}

impl LocalityLayout {
    pub fn load(dir: &Path, count: usize, graph_bytes: usize, compact_bytes: usize,
                residual_bytes: usize, max_degree: usize) -> ANNResult<Self> {
        let bytes = fs::read(dir.join("id_to_slot.u32"))?;
        if bytes.len() != count.checked_mul(4).ok_or_else(|| ann_error("mapping size overflow"))? {
            return Err(ann_error("locality mapping length mismatch"));
        }
        let slots: Vec<u32> = bytes.chunks_exact(4).map(|b| u32::from_le_bytes(b.try_into().unwrap())).collect();
        let mut seen = vec![false; count];
        for &slot in &slots {
            if slot as usize >= count || std::mem::replace(&mut seen[slot as usize], true) {
                return Err(ann_error("locality mapping is not a permutation"));
            }
        }
        for (name, size) in [("graph_compact.pages", graph_bytes + compact_bytes), ("residual.pages", residual_bytes)] {
            if size == 0 || size > PAGE_SIZE { return Err(ann_error("locality v1 requires records <= 4 KiB")); }
            let expected = count.div_ceil(PAGE_SIZE / size) * PAGE_SIZE;
            if fs::metadata(dir.join(name))?.len() != expected as u64 {
                return Err(ann_error("locality page file length mismatch"));
            }
        }
        Ok(Self { dir: dir.into(), slots, graph_bytes, compact_bytes, residual_bytes, max_degree })
    }
    pub fn resident_bytes(&self) -> usize { self.slots.capacity() * 4 }
}

pub struct LocalityReader {
    layout: Arc<LocalityLayout>,
    combined: DirectAioHandle,
    residual: DirectAioHandle,
    pub cache: Arc<Mutex<QueryPageCache>>,
}

impl LocalityReader {
    pub fn new(layout: Arc<LocalityLayout>) -> ANNResult<Self> {
        Ok(Self { combined: DirectAioHandle::new(&layout.dir.join("graph_compact.pages"))?,
            residual: DirectAioHandle::new(&layout.dir.join("residual.pages"))?,
            cache: Arc::new(Mutex::new(QueryPageCache::new()?)), layout })
    }
    fn records(&mut self, ids: &[u32], residual: bool) -> ANNResult<(Vec<Vec<u8>>, IoStats)> {
        let started = Instant::now();
        let size = if residual { self.layout.residual_bytes } else { self.layout.graph_bytes + self.layout.compact_bytes };
        let file = if residual { 2 } else { 0 };
        let per_page = PAGE_SIZE / size;
        let slots = ids.iter().map(|&id| self.layout.slots.get(id as usize).copied()
            .ok_or_else(|| ann_error("locality logical ID out of range"))).collect::<ANNResult<Vec<_>>>()?;
        let mut pages: Vec<u64> = slots.iter().map(|&s| (s as usize / per_page) as u64).collect();
        pages.sort_unstable();
        pages.dedup();
        let mut stats = IoStats::default();
        stats.duplicate_pages_removed = (ids.len() - pages.len()) as u64;
        let mut cache = self.cache.lock().map_err(|_| ann_error("locality cache poisoned"))?;
        let evicted = cache.evictions();
        let mut found = HashMap::new();
        let mut missing = Vec::new();
        for page in pages {
            if let Some(bytes) = cache.get(file, page) {
                stats.query_cache_hits += 1;
                found.insert(page, bytes);
            } else {
                stats.query_cache_misses += 1;
                missing.push(page);
            }
        }
        if !missing.is_empty() {
            let reader = if residual { &mut self.residual } else { &mut self.combined };
            let (bytes, io) = reader.read_pages(&missing)?;
            stats.requests = io.submitted_requests;
            stats.sectors_4k = io.unique_pages;
            stats.bytes_read = io.bytes_read;
            stats.coalesced_requests = io.coalesced_requests;
            for (i, page) in missing.into_iter().enumerate() {
                let mut data = Box::new([0; PAGE_SIZE]);
                data.copy_from_slice(&bytes[i * PAGE_SIZE..(i + 1) * PAGE_SIZE]);
                cache.put(file, page, &data);
                found.insert(page, data);
            }
        }
        let records = slots.iter().map(|&slot| {
            let page = &found[&((slot as usize / per_page) as u64)];
            let offset = slot as usize % per_page * size;
            page[offset..offset + size].to_vec()
        }).collect();
        stats.query_cache_allocated_bytes = cache.allocated_bytes();
        stats.query_cache_evictions = cache.evictions() - evicted;
        stats.io_wait_us = started.elapsed().as_secs_f64() * 1e6;
        Ok((records, stats))
    }
    pub fn neighbors(&mut self, id: u32) -> ANNResult<(Vec<u32>, IoStats)> {
        let (rows, stats) = self.records(&[id], false)?;
        let row = &rows[0];
        let degree = u32::from_le_bytes(row[..4].try_into().unwrap()) as usize;
        if degree > self.layout.max_degree || 4 + degree * 4 > self.layout.graph_bytes {
            return Err(ann_error("invalid locality graph degree"));
        }
        Ok((row[4..4 + degree * 4].chunks_exact(4).map(|b| u32::from_le_bytes(b.try_into().unwrap())).collect(), stats))
    }
    pub fn compact(&mut self, ids: &[u32]) -> ANNResult<(Vec<u8>, IoStats)> {
        let (rows, stats) = self.records(ids, false)?;
        Ok((rows.iter().flat_map(|row| row[self.layout.graph_bytes..].iter().copied()).collect(), stats))
    }
    pub fn residual(&mut self, ids: &[u32]) -> ANNResult<(Vec<u8>, IoStats)> {
        let (rows, stats) = self.records(ids, true)?;
        Ok((rows.into_iter().flatten().collect(), stats))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shared_page_reuse_and_residual_namespace() {
        let dir = std::env::temp_dir().join(format!("locality_reader_{}", std::process::id()));
        fs::create_dir(&dir).unwrap();
        let mapping: Vec<u8> = [1u32, 0].iter().flat_map(|x| x.to_le_bytes()).collect();
        fs::write(dir.join("id_to_slot.u32"), mapping).unwrap();
        let mut combined = vec![0u8; PAGE_SIZE];
        for (slot, id) in [1u32, 0].into_iter().enumerate() {
            let offset = slot * 12;
            combined[offset..offset + 4].copy_from_slice(&1u32.to_le_bytes());
            combined[offset + 4..offset + 8].copy_from_slice(&(1 - id).to_le_bytes());
            combined[offset + 8..offset + 12].fill(id as u8 + 10);
        }
        fs::write(dir.join("graph_compact.pages"), combined).unwrap();
        let mut residual = vec![0u8; PAGE_SIZE];
        residual[..4].fill(21);
        residual[4..8].fill(20);
        fs::write(dir.join("residual.pages"), residual).unwrap();
        let layout = Arc::new(LocalityLayout::load(&dir, 2, 8, 4, 4, 1).unwrap());
        let mut reader = LocalityReader::new(layout).unwrap();
        let (codes, first) = reader.compact(&[0, 1]).unwrap();
        assert_eq!(codes, [10, 10, 10, 10, 11, 11, 11, 11]);
        assert_eq!(first.requests, 1);
        let (neighbors, reused) = reader.neighbors(0).unwrap();
        assert_eq!(neighbors, [1]);
        assert_eq!(reused.requests, 0);
        let (rest, separate) = reader.residual(&[0, 1]).unwrap();
        assert_eq!(rest, [20, 20, 20, 20, 21, 21, 21, 21]);
        assert_eq!(separate.requests, 1);
        assert!(reader.compact(&[2]).is_err());
        fs::write(dir.join("id_to_slot.u32"), [0u8; 8]).unwrap();
        assert!(LocalityLayout::load(&dir, 2, 8, 4, 4, 1).is_err());
        drop(reader);
        fs::remove_dir_all(dir).unwrap();
    }
}
