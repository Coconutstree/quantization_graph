//! Fixed-storage per-query page LRU. Reader instances may outlive a query;
//! the query scope, not the reader pool, owns cached data and I/O counters.
use std::cell::RefCell;
use std::mem::size_of;

pub const BUDGET: usize = 4 * 1024 * 1024;
pub const PAGE: usize = 4096;
const NONE: u32 = u32::MAX;

#[derive(Clone)]
struct Slot {
    bytes: [u8; PAGE],
    page: u64,
    prev: u32,
    next: u32,
}

#[derive(Clone, Copy, Default)]
pub struct Stats {
    pub hits: u64,
    pub misses: u64,
    pub evictions: u64,
    pub allocated_bytes: u64,
    pub requests: u64,
    pub bytes_read: u64,
}

pub struct Cache {
    slots: Vec<Slot>,
    table: Vec<u32>,
    used: u32,
    head: u32,
    tail: u32,
    pub stats: Stats,
}

impl Cache {
    pub fn new(budget: usize) -> Self {
        let mut count = budget / (size_of::<Slot>() + 16);
        let buckets = (count * 2).next_power_of_two();
        while count > 0 && size_of::<Self>() + count * size_of::<Slot>() + buckets * 4 > budget {
            count -= 1;
        }
        assert!(count > 0, "query cache budget too small");
        let slots = vec![Slot { bytes: [0; PAGE], page: 0, prev: NONE, next: NONE }; count];
        let table = vec![NONE; buckets];
        let allocated = size_of::<Self>() + slots.capacity() * size_of::<Slot>() + table.capacity() * 4;
        assert!(allocated <= budget);
        Self { slots, table, used: 0, head: NONE, tail: NONE,
            stats: Stats { allocated_bytes: allocated as u64, ..Stats::default() } }
    }

    fn find(&self, page: u64) -> usize {
        let mut x = page;
        x ^= x >> 30;
        x = x.wrapping_mul(0xbf58476d1ce4e5b9);
        x ^= x >> 27;
        x = x.wrapping_mul(0x94d049bb133111eb);
        let mut b = ((x ^ (x >> 31)) as usize) & (self.table.len() - 1);
        while self.table[b] != NONE && self.slots[self.table[b] as usize].page != page {
            b = (b + 1) & (self.table.len() - 1);
        }
        b
    }

    fn touch(&mut self, id: u32) {
        if self.head == id { return; }
        let prev = self.slots[id as usize].prev;
        let next = self.slots[id as usize].next;
        if prev != NONE { self.slots[prev as usize].next = next; }
        if next != NONE { self.slots[next as usize].prev = prev; }
        if self.tail == id { self.tail = prev; }
        self.slots[id as usize].prev = NONE;
        self.slots[id as usize].next = self.head;
        if self.head != NONE { self.slots[self.head as usize].prev = id; }
        self.head = id;
        if self.tail == NONE { self.tail = id; }
    }

    pub fn get(&mut self, page: u64, out: &mut [u8]) -> bool {
        let id = self.table[self.find(page)];
        if id == NONE { self.stats.misses += 1; return false; }
        self.stats.hits += 1;
        self.touch(id);
        out.copy_from_slice(&self.slots[id as usize].bytes);
        true
    }

    pub fn put(&mut self, page: u64, bytes: &[u8]) {
        let mut id = self.table[self.find(page)];
        if id == NONE {
            if self.used < self.slots.len() as u32 {
                id = self.used;
                self.used += 1;
            } else {
                id = self.tail;
                let mut b = self.find(self.slots[id as usize].page);
                self.table[b] = NONE;
                b = (b + 1) & (self.table.len() - 1);
                while self.table[b] != NONE {
                    let moved = self.table[b];
                    self.table[b] = NONE;
                    let target = self.find(self.slots[moved as usize].page);
                    self.table[target] = moved;
                    b = (b + 1) & (self.table.len() - 1);
                }
                self.stats.evictions += 1;
            }
            self.slots[id as usize].page = page;
            let b = self.find(page);
            self.table[b] = id;
        }
        self.slots[id as usize].bytes.copy_from_slice(bytes);
        self.touch(id);
    }
}

thread_local! { pub static ACTIVE: RefCell<Option<Cache>> = const { RefCell::new(None) }; }

pub struct QueryScope;
impl QueryScope {
    pub fn new() -> Self {
        ACTIVE.with(|active| {
            let mut cache = active.borrow_mut();
            assert!(cache.is_none(), "nested query cache scope");
            *cache = Some(Cache::new(BUDGET));
        });
        Self
    }
    pub fn stats(&self) -> Stats {
        ACTIVE.with(|cache| cache.borrow().as_ref().unwrap().stats)
    }
}
impl Drop for QueryScope {
    fn drop(&mut self) { ACTIVE.with(|cache| *cache.borrow_mut() = None); }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::{HashMap, VecDeque};
    #[test]
    fn bounded_lru_matches_reference() {
        let mut cache = Cache::new(32 * 1024);
        let mut reference = HashMap::new();
        let mut order = VecDeque::new();
        let mut rng = 12345_u64;
        for step in 0..10000 {
            rng = rng.wrapping_mul(6364136223846793005).wrapping_add(1);
            let page = (rng >> 32) % 32;
            let mut out = [0; PAGE];
            let hit = cache.get(page, &mut out);
            assert_eq!(hit, reference.contains_key(&page));
            if hit {
                assert_eq!(out[0], reference[&page]);
                order.retain(|p| *p != page);
            } else if order.len() == cache.slots.len() {
                reference.remove(&order.pop_front().unwrap());
            }
            order.push_back(page);
            let bytes = [step as u8; PAGE];
            cache.put(page, &bytes);
            reference.insert(page, bytes[0]);
        }
        assert!(cache.stats.allocated_bytes <= 32 * 1024);
        assert!(cache.stats.evictions > 0);
    }
    #[test]
    fn scope_does_not_retain_prior_query() {
        { let _scope = QueryScope::new(); ACTIVE.with(|c| c.borrow_mut().as_mut().unwrap().put(0, &[1; PAGE])); }
        let scope = QueryScope::new();
        ACTIVE.with(|c| assert!(!c.borrow_mut().as_mut().unwrap().get(0, &mut [0; PAGE])));
        assert_eq!(scope.stats().misses, 1);
    }
}
