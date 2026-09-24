//! Formal low-RAM route: resident PCA + 1-bit, no residual statistics.
//! The epsilon gate is empirical against a full4 pool, not a certified hard prune.
use crate::{
    config::QueryCoarseCodec,
    ours_diskann::{QueryComputer, RabitqPaperEstimate, RabitqSpace},
};
use serde_json::{json, Value};
use std::{
    fs::{self, File},
    io::{BufReader, Read, Seek, SeekFrom, Write},
    path::Path,
    sync::Arc,
};
type R<T> = Result<T, String>;
pub const POLICY: &str = "resident_full_else_pca1bit_m32_no_residual_v1";
pub fn field(v: &Value, key: &str) -> R<usize> {
    v[key]
        .as_u64()
        .and_then(|n| usize::try_from(n).ok())
        .ok_or(format!("invalid PCA field {key}"))
}
fn io(e: impl std::fmt::Display) -> String {
    e.to_string()
}
pub fn padded(d: usize) -> usize {
    d.max(64).next_power_of_two()
}
fn sum(values: &[usize]) -> R<usize> {
    values.iter().try_fold(0usize, |a, &b| {
        a.checked_add(b).ok_or("memory plan overflow".into())
    })
}
pub fn extra(d: usize, k: usize, workers: usize) -> usize {
    4 * (d * k + d) + 16 * padded(k).pow(2) + workers * 16 * d
}
pub fn choose(
    n: usize,
    d: usize,
    centroid_bytes: usize,
    workers: usize,
    budget: usize,
    query_bytes: usize,
) -> R<Value> {
    choose_dimension(n,d,centroid_bytes,workers,budget,query_bytes,None)
}

fn choose_dimension(n: usize,d: usize,centroid_bytes: usize,workers: usize,budget: usize,query_bytes: usize,forced: Option<usize>) -> R<Value> {
    if n == 0 || d == 0 || d > 65536 || workers == 0 || workers > 4096 || n > u32::MAX as usize {
        return Err("invalid PCA planner shape/workers".into());
    }
    let worker = (n + 12 * 1024 * 1024)
        .checked_mul(workers)
        .ok_or("worker reservation overflow")?;
    // This is an explicit planning allowance, not an RSS measurement or AS cap.
    let rotation = 16 * padded(d).pow(2);
    let process = 32 * 1024 * 1024;
    let other = sum(&[rotation, process, query_bytes])?;
    let fixed = sum(&[centroid_bytes, worker, other])?;
    let make = |k: usize| -> R<Value> {
        let pca = k != d;
        let codes = n.checked_mul(padded(k) / 8).ok_or("code size overflow")?;
        let factors = n.checked_mul(20).ok_or("factor size overflow")?;
        let projection = if pca { 4 * (d * k + d) } else { 0 };
        let pca_rotation = if pca { 16 * padded(k).pow(2) } else { 0 };
        let pca_scratch = if pca { workers * 16 * d } else { 0 };
        let required = sum(&[fixed, codes, factors, projection, pca_rotation, pca_scratch])?;
        Ok(
            json!({"selection_policy":"full_else_pca512_256_128_v1","policy":POLICY,"mode":if pca {"pca1bit"} else {"full1bit"},
            "dimension":k,"original_dimension":d,"padded_dimension":padded(k),"count":n,"workers":workers,
            "budget_bytes":budget,"codes_bytes":codes,"factors_bytes":factors,"centroid_bytes":centroid_bytes,
            "worker_reservation_bytes":worker,"original_rotation_reservation_bytes":rotation,
            "process_reservation_bytes":process,"query_reservation_bytes":query_bytes,
            "projection_bytes":projection,"pca_rotation_reservation_bytes":pca_rotation,
            "pca_query_reservation_bytes":pca_scratch,"residual_norm_bytes":0,
            "other_reserved_bytes":other,"required_bytes":required,
            "cache_available_bytes":budget.saturating_sub(required),
            "shortlist_m":if pca {json!(32)} else {Value::Null},
            "gate":if pca {"empirical_epsilon_1.9"} else {"original"},"estimate_only":true}),
        )
    };
    if let Some(k) = forced {
        if k != d && ![128,256,512].contains(&k) || k > d {
            return Err("fixed resident dimension must be full or PCA128/256/512".into());
        }
        let mut chosen=make(k)?;
        if field(&chosen,"required_bytes")?>budget {return Err("fixed resident representation exceeds RAM budget".into());}
        chosen["selection_policy"]=json!("fixed_resident_dimension_ablation_v1");
        return Ok(chosen);
    }
    let full = make(d)?;
    if field(&full, "required_bytes")? <= budget {
        return Ok(full);
    }
    // Discrete resident representations; never fall back to paged routing.
    for k in [512, 256, 128].into_iter().filter(|&k| k < d) {
        let mut plan = make(k)?;
        if field(&plan, "required_bytes")? <= budget {
            plan["full1bit_required_bytes"] = full["required_bytes"].clone();
            return Ok(plan);
        }
    }
    Err(format!("no resident PCA candidate (512/256/128) fits RAM budget {budget}; fixed reservation {fixed}; full 1-bit requires {}",full["required_bytes"]))
}
pub fn plan(index: &Path, workers: usize, budget: usize, query_bytes: usize) -> R<Value> {
    plan_dimension(index,workers,budget,query_bytes,None)
}

pub fn plan_dimension(index: &Path, workers: usize, budget: usize, query_bytes: usize, forced: Option<usize>) -> R<Value> {
    let mut f = File::open(index.join("ours_quantizer.bin")).map_err(io)?;
    let mut h = [0u8; 68];
    f.read_exact(&mut h).map_err(io)?;
    if &h[..8] != b"QG05OUR1" {
        return Err("invalid Ours metadata".into());
    }
    let at = |p| usize::try_from(u64::from_le_bytes(h[p..p + 8].try_into().unwrap())).map_err(io);
    let d = at(12)?;
    let n = at(28)?;
    if at(52)? != padded(d) / 8 || at(60)? != 20 {
        return Err("unsupported native 1-bit layout".into());
    }
    let centroids = d
        .checked_mul(at(20)?)
        .and_then(|x| x.checked_mul(4))
        .ok_or("centroid overflow")?;
    choose_dimension(n, d, centroids, workers, budget, query_bytes, forced)
}
fn floats(path: &Path, count: usize) -> R<Vec<f32>> {
    let mut f = BufReader::new(File::open(path).map_err(io)?);
    if f.get_ref().metadata().map_err(io)?.len() != count as u64 * 4 {
        return Err(format!("invalid PCA length: {}", path.display()));
    }
    let mut v = Vec::with_capacity(count);
    for _ in 0..count {
        let mut b = [0; 4];
        f.read_exact(&mut b).map_err(io)?;
        v.push(f32::from_le_bytes(b));
    }
    if v.iter().any(|x| !x.is_finite()) {
        return Err("nonfinite PCA asset".into());
    }
    Ok(v)
}
pub struct PcaRoute {
    pub space: Arc<RabitqSpace>,
    pub k: usize,
    pub d: usize,
    mean: Vec<f32>,
    basis: Vec<f32>,
    codes: Vec<u8>,
    factors: Vec<u8>,
}
pub struct Prepared {
    pub computer: QueryComputer,
    _projected: Vec<f32>,
}
impl PcaRoute {
    pub fn load(dir: &Path, n: usize, d: usize) -> R<Self> {
        let m: Value =
            serde_json::from_slice(&fs::read(dir.join("pca.json")).map_err(io)?).map_err(io)?;
        let k = field(&m, "dim")?;
        if k == 0 || k >= d || field(&m, "original_dim")? != d || field(&m, "count")? != n {
            return Err("PCA dimension/count mismatch".into());
        }
        let mean = floats(&dir.join("mean.bin"), d)?;
        let basis = floats(&dir.join("basis.bin"), d * k)?;
        let space = RabitqSpace::new_with_centroids(k, 17, 1, &vec![0.; k])?;
        let cs = space.paper_msb_code_bytes();
        let fs = space.paper_factor_bytes();
        if field(&m, "code_stride")? != cs || field(&m, "factor_stride")? != fs {
            return Err("PCA native stride mismatch".into());
        }
        let mut f = BufReader::new(File::open(dir.join("sidecar.bin")).map_err(io)?);
        let mut h = [0; 24];
        f.read_exact(&mut h).map_err(io)?;
        if &h[..8] != b"QG05OSC1"
            || u64::from_le_bytes(h[8..16].try_into().unwrap()) != (n * cs) as u64
            || u64::from_le_bytes(h[16..24].try_into().unwrap()) != (n * fs) as u64
            || f.get_ref().metadata().map_err(io)?.len() != (24 + n * (cs + fs)) as u64
        {
            return Err("PCA sidecar mismatch".into());
        }
        let mut codes = vec![0; n * cs];
        let mut factors = vec![0; n * fs];
        f.read_exact(&mut codes).map_err(io)?;
        f.read_exact(&mut factors).map_err(io)?;
        Ok(Self {
            space,
            k,
            d,
            mean,
            basis,
            codes,
            factors,
        })
    }
    pub fn resident_bytes(&self) -> usize {
        self.codes.len() + self.factors.len() + extra(self.d, self.k, 0)
    }
    pub fn configure(&self, c: QueryCoarseCodec) {
        self.space.set_query_coarse_codec(c);
    }
    pub fn prepare(&self, q: &[f32]) -> R<Prepared> {
        if q.len() != self.d {
            return Err("PCA query dimension mismatch".into());
        }
        let x = q
            .iter()
            .zip(&self.mean)
            .map(|(a, b)| a - b)
            .collect::<Vec<_>>();
        let y = self
            .basis
            .chunks_exact(self.d)
            .map(|r| {
                r.iter()
                    .zip(&x)
                    .map(|(a, b)| (*a as f64) * (*b as f64))
                    .sum::<f64>() as f32
            })
            .collect::<Vec<_>>();
        Ok(Prepared {
            computer: self.space.prepare_query(&y).map_err(io)?,
            _projected: y,
        })
    }
    pub fn estimates(
        &self,
        q: &Prepared,
        ids: &[u32],
        epsilon: f32,
    ) -> R<Vec<RabitqPaperEstimate>> {
        let mut out = vec![RabitqPaperEstimate::default(); ids.len()];
        q.computer
            .paper_estimate_batch_sidecar(
                ids,
                self.codes.as_ptr(),
                self.space.paper_msb_code_bytes(),
                self.factors.as_ptr().cast(),
                self.space.paper_factor_bytes(),
                epsilon,
                &mut out,
            )
            .map_err(io)?;
        if epsilon > 0. {
            for e in &mut out {
                e.lower_bound = e.lower_bound.max(0.);
            }
        }
        Ok(out)
    }
}
/// Offline only; projected rows are streamed and no full payload is retained.
pub fn export(dir: &Path) -> R<()> {
    let mut m: Value =
        serde_json::from_slice(&fs::read(dir.join("pca.json")).map_err(io)?).map_err(io)?;
    let k = field(&m, "dim")?;
    let n = field(&m, "count")?;
    let space = RabitqSpace::new_with_centroids(k, 17, 1, &vec![0.; k])?;
    let cs = space.paper_msb_code_bytes();
    let fs = space.paper_factor_bytes();
    let mut out = File::options()
        .write(true)
        .create_new(true)
        .open(dir.join("sidecar.bin"))
        .map_err(io)?;
    out.write_all(b"QG05OSC1").map_err(io)?;
    out.write_all(&((n * cs) as u64).to_le_bytes())
        .map_err(io)?;
    out.write_all(&((n * fs) as u64).to_le_bytes())
        .map_err(io)?;
    out.set_len((24 + n * (cs + fs)) as u64).map_err(io)?;
    let mut source = BufReader::new(File::open(dir.join("projected.bin")).map_err(io)?);
    if source.get_ref().metadata().map_err(io)?.len() != (n * k * 4) as u64 {
        return Err("projected length mismatch".into());
    }
    for start in (0..n).step_by(2048) {
        let count = (n - start).min(2048);
        let mut compact = Vec::with_capacity(count * space.compact_record_bytes());
        for _ in 0..count {
            let mut row = vec![0.; k];
            for x in &mut row {
                let mut b = [0; 4];
                source.read_exact(&mut b).map_err(io)?;
                *x = f32::from_le_bytes(b);
            }
            compact.extend(space.encode_parts(&row).map_err(io)?.0);
        }
        let (c, f) = space.export_sidecar(&compact, count).map_err(io)?;
        out.seek(SeekFrom::Start((24 + start * cs) as u64))
            .map_err(io)?;
        out.write_all(&c).map_err(io)?;
        out.seek(SeekFrom::Start((24 + n * cs + start * fs) as u64))
            .map_err(io)?;
        out.write_all(&f).map_err(io)?;
    }
    m["code_stride"] = cs.into();
    m["factor_stride"] = fs.into();
    fs::write(
        dir.join("pca.json"),
        serde_json::to_vec_pretty(&m).map_err(io)?,
    )
    .map_err(io)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_resident_dimension_budget() {
        for k in [128,256,512,960] {
            let p=choose_dimension(1_000_000,960,3840,32,4usize<<30,8<<20,Some(k)).unwrap();
            assert_eq!(p["dimension"],k);
            assert_eq!(p["budget_bytes"],4usize<<30);
            assert_eq!(p["selection_policy"],"fixed_resident_dimension_ablation_v1");
            let needed=field(&p,"required_bytes").unwrap();
            assert!(choose_dimension(1_000_000,960,3840,32,needed-1,8<<20,Some(k)).is_err());
        }
        assert!(choose_dimension(1_000_000,960,3840,32,4usize<<30,8<<20,Some(64)).is_err());
    }
    #[test]
    fn full_first_and_maximum_prefix() {
        let full = choose(1_000_000, 960, 3840, 32, 2 << 30, 8 << 20).unwrap();
        assert_eq!(full["mode"], "full1bit");
        let low = choose(1_000_000, 960, 3840, 32, 512 << 20, 8 << 20).unwrap();
        assert_eq!(low["mode"], "pca1bit");
        assert_eq!(low["dimension"], 128);
        assert_eq!(low["residual_norm_bytes"], 0);
        assert_eq!(low["shortlist_m"], 32);
        let exact = field(&low, "required_bytes").unwrap();
        assert_eq!(
            choose(1_000_000, 960, 3840, 32, exact, 8 << 20).unwrap()["dimension"],
            128
        );
        assert!(choose(1_000_000, 960, 3840, 32, exact - 1, 8 << 20).is_err());
        assert!(choose(1_000_000, 960, 3840, 32, 32 << 20, 8 << 20).is_err());
    }
    #[test]
    fn discrete_boundaries() {
        let full = choose(1_000_000, 960, 3840, 32, 2 << 30, 8 << 20).unwrap();
        let fixed = 3840 + (1_000_000 + 12*1024*1024)*32 + 16*padded(960).pow(2) + 32*1024*1024 + (8<<20);
        for (k, smaller) in [(512,Some(256)), (256,Some(128)), (128,None)] {
            let required = fixed + 1_000_000*(padded(k)/8+20) + extra(960,k,32);
            assert_eq!(choose(1_000_000,960,3840,32,required,8<<20).unwrap()["dimension"],k);
            let below = choose(1_000_000,960,3840,32,required-1,8<<20);
            if let Some(next) = smaller { assert_eq!(below.unwrap()["dimension"],next); }
            else { assert!(below.is_err()); }
        }
        let exact=field(&full,"required_bytes").unwrap();
        assert_eq!(choose(1_000_000,960,3840,32,exact,8<<20).unwrap()["mode"],"full1bit");
        assert_eq!(choose(1_000_000,960,3840,32,exact-1,8<<20).unwrap()["dimension"],512);
    }
    #[test]
    fn preparing_lowdim_query_preserves_full4_context() {
        const DIST_MODE_RECOMPUTE_FULL: u8 = 0;
        let full = RabitqSpace::new_with_centroids(256, 100, 1, &vec![0.; 256]).unwrap();
        let low = RabitqSpace::new_with_centroids(64, 17, 1, &vec![0.; 64]).unwrap();
        full.set_query_coarse_codec(QueryCoarseCodec::Int8);
        low.set_query_coarse_codec(QueryCoarseCodec::Int8);
        let x = (0..256)
            .map(|i| (i as f32 * 0.17).sin())
            .collect::<Vec<_>>();
        let q = (0..256)
            .map(|i| (i as f32 * 0.11).cos())
            .collect::<Vec<_>>();
        let compact = full.encode_parts(&x).unwrap().0;
        let prepared = full.prepare_query(&q).unwrap();
        let score = || {
            let mut out = [0.];
            prepared
                .distance_batch(
                    &[0],
                    &[DIST_MODE_RECOMPUTE_FULL],
                    compact.as_ptr().cast(),
                    full.compact_record_bytes(),
                    &[0.],
                    &mut out,
                )
                .unwrap();
            out[0].to_bits()
        };
        let expected = score();
        let _pca_prepared = low.prepare_query(&q[..64]).unwrap();
        assert_eq!(score(), expected);
    }
}
