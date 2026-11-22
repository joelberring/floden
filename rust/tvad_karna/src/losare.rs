//! Tidsslingan för den fina 2D-lösaren (ingen subgrid), samma semantik som
//! `tvad.lokal_troghet.kor_2d_lokal_troghet(subgrid_faktor=1)`.
//!
//! Allt arbete per steg är per kant eller per cell (`karna`), parallelliserat radvis med rayon.
//! Reduktioner är max (ordningsoberoende), heltal eller NumPy-likadana summor i fast ordning,
//! så resultatet är detsamma oavsett antal trådar.

use crate::karna::{bates_flode, kant_djup, np_max, np_sum, np_sum_konst, rand_flode, utjamnat_flode, G};
use rayon::prelude::*;

const DT_MIN_S: f64 = 1e-4;

pub struct Rand {
    pub mask: Vec<bool>,
    pub lutning: Vec<f64>,
    pub bredd: Vec<f64>,
}

pub struct Indata {
    pub hojd: usize,
    pub bredd: usize,
    pub z_bed: Vec<f64>,
    pub manning: Vec<f64>,
    pub aktiv: Vec<bool>,
    pub vast: Rand,
    pub ost: Rand,
    pub norr: Rand,
    pub soder: Rand,
    pub tider: Vec<f64>,
    pub intens_m_s: Vec<f64>,
    pub dx: f64,
    pub dy: f64,
    pub cfl: f64,
    pub min_djup: f64,
    pub dt_max: f64,
    pub theta: f64,
    pub t_start: f64,
    pub t_slut: f64,
    pub total_aktiv_area: f64,
    pub total_doman_area: f64,
    pub gles_front: bool,
    /// Regnyta per cell (m²) med takvatten (`regn_area.npy`); None = cellens aktiva yta.
    pub regn_area: Option<Vec<f64>>,
    /// Takyta vars vatten lämnar rutan (m², `tak_ut.npy`); räknas som in- och utflöde.
    pub tak_ut_area: f64,
}

pub struct Utdata {
    pub max_djup: Vec<f32>,
    pub slut_djup: Vec<f32>,
    pub max_hastighet: Vec<f32>,
    pub fara: Vec<f32>,
    pub tid_01: Vec<f32>,
    pub tid_02: Vec<f32>,
    pub volym: Vec<f64>,
    pub in_m3: f64,
    pub ut_m3: f64,
    pub antal_steg: u64,
    pub sum_aktiv_andel: f64,
}

fn integrera_regn(t0: f64, t1: f64, tider: &[f64], intens: &[f64]) -> f64 {
    if t1 <= t0 {
        return 0.0;
    }
    let mut regn = 0.0;
    for i in 0..tider.len().saturating_sub(1) {
        let a = tider[i];
        let b = tider[i + 1];
        if b <= a || b <= t0 || a >= t1 {
            continue;
        }
        let lo = if t0 > a { t0 } else { a };
        let hi = if t1 < b { t1 } else { b };
        let dt = hi - lo;
        if dt <= 0.0 {
            continue;
        }
        let ia = intens[i];
        let ib = intens[i + 1];
        let span = b - a;
        let i_lo = ia + ((lo - a) / span) * (ib - ia);
        let i_hi = ia + ((hi - a) / span) * (ib - ia);
        regn += 0.5 * (i_lo + i_hi) * dt;
    }
    regn
}

fn nasta_brytpunkt(t_nu: f64, tider: &[f64], t_slut: f64) -> f64 {
    for &t in tider {
        if t > t_nu + 1e-12 {
            return if t < t_slut { t } else { t_slut };
        }
    }
    t_slut
}

/// Pythons `min(a, b, c)` (första minsta vinner; inga NaN här).
fn py_min3(a: f64, b: f64, c: f64) -> f64 {
    let mut m = a;
    if b < m {
        m = b;
    }
    if c < m {
        m = c;
    }
    m
}

// Slingorna indexerar flera matriser och grannar med samma index; iteratorer gör det oläsligt.
#[allow(clippy::needless_range_loop)]
pub fn kor(ind: &Indata) -> Utdata {
    let (h, w) = (ind.hojd, ind.bredd);
    let n = h * w;
    let (dx, dy) = (ind.dx, ind.dy);
    let cellarea = dx * dy;
    let min_d = ind.min_djup;

    let aktiv_area: Vec<f64> = match &ind.regn_area {
        Some(r) => r.clone(),
        None => ind.aktiv.iter().map(|&a| if a { cellarea } else { 0.0 }).collect(),
    };
    let safe_area: Vec<f64> = ind.aktiv.iter().map(|&a| if a { cellarea } else { 1.0 }).collect();

    // Kantgeometri (u = x-kanter (h × (w-1)), v = y-kanter ((h-1) × w)).
    let wu = w.saturating_sub(1);
    let hv = h.saturating_sub(1);
    let mut manning_u = vec![0.0; h * wu];
    let mut z_kant_u = vec![0.0; h * wu];
    let mut u_giltig = vec![false; h * wu];
    for r in 0..h {
        for c in 0..wu {
            let a = r * w + c;
            manning_u[r * wu + c] = 0.5 * (ind.manning[a] + ind.manning[a + 1]);
            z_kant_u[r * wu + c] = np_max(ind.z_bed[a], ind.z_bed[a + 1]);
            u_giltig[r * wu + c] = ind.aktiv[a] && ind.aktiv[a + 1];
        }
    }
    let mut manning_v = vec![0.0; hv * w];
    let mut z_kant_v = vec![0.0; hv * w];
    let mut v_giltig = vec![false; hv * w];
    for r in 0..hv {
        for c in 0..w {
            let a = r * w + c;
            manning_v[r * w + c] = 0.5 * (ind.manning[a] + ind.manning[a + w]);
            z_kant_v[r * w + c] = np_max(ind.z_bed[a], ind.z_bed[a + w]);
            v_giltig[r * w + c] = ind.aktiv[a] && ind.aktiv[a + w];
        }
    }

    let mut volym = vec![0.0f64; n];
    let mut h_eff = vec![0.0f64; n];
    let mut h_fore = vec![0.0f64; n];
    let mut zs = ind.z_bed.clone();
    let mut qx = vec![0.0f64; h * wu];
    let mut qy = vec![0.0f64; hv * w];
    let mut qx_ny = vec![0.0f64; h * wu];
    let mut qy_ny = vec![0.0f64; hv * w];
    let mut vat = vec![false; n];
    let mut skala = vec![1.0f64; n];

    let mut max_djup = vec![0.0f64; n];
    let mut max_hast = vec![0.0f64; n];
    let mut max_fara = vec![0.0f64; n];
    let mut tid_01 = vec![f32::NAN; n];
    let mut tid_02 = vec![f32::NAN; n];

    let mut in_m3 = 0.0f64;
    let mut ut_m3 = 0.0f64;
    let mut antal_steg: u64 = 0;
    let mut sum_aktiv_andel = 0.0f64;
    let mut t_nu = ind.t_start;
    let min_dx = if dx < dy { dx } else { dy };
    let theta = ind.theta;
    let utjamna_x = theta < 1.0 && wu >= 2;
    let utjamna_y = theta < 1.0 && hv >= 2;

    let mut q_vast = vec![0.0; h];
    let mut q_ost = vec![0.0; h];
    let mut q_norr = vec![0.0; w];
    let mut q_soder = vec![0.0; w];
    let mut dv_v = vec![0.0; h];
    let mut dv_o = vec![0.0; h];
    let mut dv_n = vec![0.0; w];
    let mut dv_s = vec![0.0; w];

    let antal_aktiv = ind.aktiv.iter().filter(|&&a| a).count();
    // Regn på aktiva celler, sedan djup, vattenyta och våta celler i samma pass.
    let regn_och_djup = |regn_m: f64, volym: &mut [f64], h_eff: &mut [f64], zs: &mut [f64], vat: &mut [bool]| {
        volym
            .par_chunks_mut(w)
            .zip(h_eff.par_chunks_mut(w))
            .zip(zs.par_chunks_mut(w).zip(vat.par_chunks_mut(w)))
            .enumerate()
            .for_each(|(r, ((vr, hr), (zr, war)))| {
                for c in 0..w {
                    let i = r * w + c;
                    vr[c] += aktiv_area[i] * regn_m;
                    let akt = ind.aktiv[i];
                    hr[c] = if akt { vr[c] / safe_area[i] } else { 0.0 };
                    zr[c] = ind.z_bed[i] + hr[c];
                    war[c] = akt && hr[c] > min_d;
                }
            });
    };

    let mut h_max = 0.0f64;
    while t_nu < ind.t_slut - 1e-12 {
        // h_max kommer från passet som senast uppdaterade djupet (h_eff innehåller aldrig NaN).
        let vaghast = (G * np_max(h_max, min_d)).sqrt();
        let dt_cfl = ind.cfl * min_dx / vaghast;
        let nasta_t = nasta_brytpunkt(t_nu, &ind.tider, ind.t_slut);
        let mut dt = py_min3(dt_cfl, ind.dt_max, nasta_t - t_nu);
        if dt < DT_MIN_S && (ind.t_slut - t_nu) > DT_MIN_S {
            let b = nasta_t - t_nu;
            dt = if b < DT_MIN_S { b } else { DT_MIN_S };
        }
        if dt <= 0.0 {
            break;
        }

        // 1. Regn
        let regn_m = integrera_regn(t_nu, t_nu + dt, &ind.tider, &ind.intens_m_s);
        if regn_m > 0.0 && ind.total_aktiv_area > 0.0 {
            in_m3 += ind.total_aktiv_area * regn_m;
            if ind.tak_ut_area > 0.0 {
                ut_m3 += ind.tak_ut_area * regn_m;
            }
            regn_och_djup(regn_m, &mut volym, &mut h_eff, &mut zs, &mut vat);
        }

        // 2. Våta celler och aktiv front (fronten används bara för statistiken)
        // `vat` är satt av regnpasset eller av förra stegets djuppass.
        if ind.total_doman_area > 0.0 {
            let k: usize = if ind.gles_front {
                let v = &vat;
                (0..h)
                    .into_par_iter()
                    .map(|r| {
                        let o = r * w;
                        let mut k = 0usize;
                        for c in 0..w {
                            let i = o + c;
                            if ind.aktiv[i]
                                && (v[i]
                                    || (r > 0 && v[i - w])
                                    || (r + 1 < h && v[i + w])
                                    || (c > 0 && v[i - 1])
                                    || (c + 1 < w && v[i + 1]))
                            {
                                k += 1;
                            }
                        }
                        k
                    })
                    .sum()
            } else {
                antal_aktiv
            };
            sum_aktiv_andel += np_sum_konst(k, cellarea) / ind.total_doman_area;
        }
        // 3. Flöden på x-kanter
        if w > 1 {
            let qx_g = &qx;
            let (vat_r, zs_r) = (&vat, &zs);
            qx_ny.par_chunks_mut(wu).enumerate().for_each(|(r, rad)| {
                for c in 0..wu {
                    let e = r * wu + c;
                    let a = r * w + c;
                    let akt = if ind.gles_front {
                        (vat_r[a] || vat_r[a + 1]) && u_giltig[e]
                    } else {
                        u_giltig[e]
                    };
                    if !akt {
                        rad[c] = 0.0;
                        continue;
                    }
                    let q = qx_g[e];
                    let q_bar = if utjamna_x {
                        let fore = if c == 0 { q } else if u_giltig[e - 1] { qx_g[e - 1] } else { q };
                        let efter = if c == wu - 1 { q } else if u_giltig[e + 1] { qx_g[e + 1] } else { q };
                        utjamnat_flode(q, fore, efter, theta)
                    } else {
                        q
                    };
                    let h_u = kant_djup(zs_r[a], zs_r[a + 1], z_kant_u[e]);
                    let sx = (zs_r[a + 1] - zs_r[a]) / dx;
                    rad[c] = bates_flode(q, q_bar, h_u, sx, manning_u[e], dt, min_d);
                }
            });
            std::mem::swap(&mut qx, &mut qx_ny);
        }

        // 3b. Flöden på y-kanter
        if h > 1 {
            let qy_g = &qy;
            let (vat_r, zs_r) = (&vat, &zs);
            qy_ny.par_chunks_mut(w).enumerate().for_each(|(r, rad)| {
                for c in 0..w {
                    let e = r * w + c;
                    let a = r * w + c;
                    let akt = if ind.gles_front {
                        (vat_r[a] || vat_r[a + w]) && v_giltig[e]
                    } else {
                        v_giltig[e]
                    };
                    if !akt {
                        rad[c] = 0.0;
                        continue;
                    }
                    let q = qy_g[e];
                    let q_bar = if utjamna_y {
                        let fore = if r == 0 { q } else if v_giltig[e - w] { qy_g[e - w] } else { q };
                        let efter = if r == hv - 1 { q } else if v_giltig[e + w] { qy_g[e + w] } else { q };
                        utjamnat_flode(q, fore, efter, theta)
                    } else {
                        q
                    };
                    let h_v = kant_djup(zs_r[a], zs_r[a + w], z_kant_v[e]);
                    let sy = (zs_r[a + w] - zs_r[a]) / dy;
                    rad[c] = bates_flode(q, q_bar, h_v, sy, manning_v[e], dt, min_d);
                }
            });
            std::mem::swap(&mut qy, &mut qy_ny);
        }

        // 4. Öppna ränder
        for r in 0..h {
            let a0 = r * w;
            let a1 = r * w + w - 1;
            q_vast[r] = rand_flode(h_eff[a0], ind.manning[a0], ind.vast.mask[r], ind.vast.lutning[r], ind.vast.bredd[r], min_d);
            q_ost[r] = rand_flode(h_eff[a1], ind.manning[a1], ind.ost.mask[r], ind.ost.lutning[r], ind.ost.bredd[r], min_d);
        }
        for c in 0..w {
            let a0 = c;
            let a1 = (h - 1) * w + c;
            q_norr[c] = rand_flode(h_eff[a0], ind.manning[a0], ind.norr.mask[c], ind.norr.lutning[c], ind.norr.bredd[c], min_d);
            q_soder[c] = rand_flode(h_eff[a1], ind.manning[a1], ind.soder.mask[c], ind.soder.lutning[c], ind.soder.bredd[c], min_d);
        }

        // 5. Flödesbegränsare: utgående volym per cell, i Python-lösarens summeringsordning
        let dydt = dy * dt;
        let dxdt = dx * dt;
        let nagon_begransad: bool;
        {
            let (qx_r, qy_r, vol_r) = (&qx, &qy, &volym);
            let (qv, qo, qn, qs) = (&q_vast, &q_ost, &q_norr, &q_soder);
            nagon_begransad = skala.par_chunks_mut(w).enumerate().map(|(r, rad)| {
                let mut b = false;
                for (c, sk) in rad.iter_mut().enumerate() {
                    let i = r * w + c;
                    let mut u = 0.0f64;
                    if w > 1 {
                        if c < w - 1 {
                            u += np_max(0.0, qx_r[r * wu + c]) * dydt;
                        }
                        if c > 0 {
                            u += np_max(0.0, -qx_r[r * wu + c - 1]) * dydt;
                        }
                    }
                    if h > 1 {
                        if r < h - 1 {
                            u += np_max(0.0, qy_r[r * w + c]) * dxdt;
                        }
                        if r > 0 {
                            u += np_max(0.0, -qy_r[(r - 1) * w + c]) * dxdt;
                        }
                    }
                    if c == 0 {
                        u += qv[r] * dydt;
                    }
                    if c == w - 1 {
                        u += qo[r] * dydt;
                    }
                    if r == 0 {
                        u += qn[c] * dxdt;
                    }
                    if r == h - 1 {
                        u += qs[c] * dxdt;
                    }
                    *sk = if u > vol_r[i] {
                        b = true;
                        vol_r[i] / u
                    } else {
                        1.0
                    };
                }
                b
            }).reduce(|| false, |a, b| a || b);
        }
        // Skalning (multiplikation med 1,0 är exakt, så den görs överallt)
        if nagon_begransad && w > 1 {
            let sk = &skala;
            qx.par_chunks_mut(wu).enumerate().for_each(|(r, rad)| {
                for (c, q) in rad.iter_mut().enumerate() {
                    let a = r * w + c;
                    *q = if *q >= 0.0 { *q * sk[a] } else { *q * sk[a + 1] };
                }
            });
        }
        if nagon_begransad && h > 1 {
            let sk = &skala;
            qy.par_chunks_mut(w).enumerate().for_each(|(r, rad)| {
                for (c, q) in rad.iter_mut().enumerate() {
                    let a = r * w + c;
                    *q = if *q >= 0.0 { *q * sk[a] } else { *q * sk[a + w] };
                }
            });
        }
        for r in 0..h {
            q_vast[r] *= skala[r * w];
            q_ost[r] *= skala[r * w + w - 1];
        }
        for c in 0..w {
            q_norr[c] *= skala[c];
            q_soder[c] *= skala[(h - 1) * w + c];
        }

        // 6–8. Volym (samma summeringsordning som Python), djup, vattenyta, maxdjup och
        // ankomsttider i ett pass per rad; svarar om någon cell är våt.
        let t32 = (t_nu + dt) as f32;
        let (nagon_vat, ny_h_max) = {
            let (qx_r, qy_r) = (&qx, &qy);
            let (qv, qo, qn, qs) = (&q_vast, &q_ost, &q_norr, &q_soder);
            volym
                .par_chunks_mut(w)
                .zip(h_eff.par_chunks_mut(w).zip(h_fore.par_chunks_mut(w)))
                .zip(zs.par_chunks_mut(w))
                .zip(max_djup.par_chunks_mut(w))
                .zip(tid_01.par_chunks_mut(w).zip(tid_02.par_chunks_mut(w)))
                .zip(vat.par_chunks_mut(w))
                .enumerate()
                .map(|(r, (((((vr, (hr, hfr)), zr), mr), (t1r, t2r)), war))| {
                    let mut vat_rad = false;
                    let mut hmax_rad = 0.0f64;
                    for c in 0..w {
                        let i = r * w + c;
                        let mut x = vr[c];
                        if w > 1 {
                            if c < w - 1 {
                                x -= qx_r[r * wu + c] * dydt;
                            }
                            if c > 0 {
                                x += qx_r[r * wu + c - 1] * dydt;
                            }
                        }
                        if h > 1 {
                            if r < h - 1 {
                                x -= qy_r[r * w + c] * dxdt;
                            }
                            if r > 0 {
                                x += qy_r[(r - 1) * w + c] * dxdt;
                            }
                        }
                        if c == 0 {
                            x -= qv[r] * dydt;
                        }
                        if c == w - 1 {
                            x -= qo[r] * dydt;
                        }
                        if r == 0 {
                            x -= qn[c] * dxdt;
                        }
                        if r == h - 1 {
                            x -= qs[c] * dxdt;
                        }
                        // np.maximum(volym, 0.0); volym[~aktiv] = 0
                        x = np_max(x, 0.0);
                        let akt = ind.aktiv[i];
                        let v = if akt { x } else { 0.0 };
                        vr[c] = v;
                        let hf = hr[c];
                        hfr[c] = hf;
                        let he = if akt { v / safe_area[i] } else { 0.0 };
                        hr[c] = he;
                        zr[c] = ind.z_bed[i] + he;
                        mr[c] = np_max(mr[c], he);
                        hmax_rad = hmax_rad.max(he);
                        war[c] = akt && he > min_d;
                        if akt {
                            if np_max(hf, he) > min_d {
                                vat_rad = true;
                            }
                            if t1r[c].is_nan() && he >= 0.1 {
                                t1r[c] = t32;
                            }
                            if t2r[c].is_nan() && he >= 0.2 {
                                t2r[c] = t32;
                            }
                        }
                    }
                    (vat_rad, hmax_rad)
                })
                .reduce(|| (false, 0.0f64), |a, b| (a.0 || b.0, a.1.max(b.1)))
        };
        h_max = ny_h_max;
        for r in 0..h {
            dv_v[r] = q_vast[r] * dydt;
            dv_o[r] = q_ost[r] * dydt;
        }
        for c in 0..w {
            dv_n[c] = q_norr[c] * dxdt;
            dv_s[c] = q_soder[c] * dxdt;
        }
        ut_m3 += ((np_sum(&dv_v) + np_sum(&dv_o)) + np_sum(&dv_n)) + np_sum(&dv_s);
        t_nu += dt;
        antal_steg += 1;

        // Hastighet och fara uppdateras bara när någon cell är våt (som i Python). Celler där
        // båda cellflödena är noll hoppas över: max med 0 ändrar inget.
        if nagon_vat {
            let (qx_r, qy_r, he, hf) = (&qx, &qy, &h_eff, &h_fore);
            let (qv, qo, qn, qs) = (&q_vast, &q_ost, &q_norr, &q_soder);
            max_hast
                .par_chunks_mut(w)
                .zip(max_fara.par_chunks_mut(w))
                .enumerate()
                .for_each(|(r, (mh, mf))| {
                    for c in 0..w {
                        let i = r * w + c;
                        let mut qcx = 0.0f64;
                        if w > 1 {
                            if c < w - 1 {
                                qcx += 0.5 * qx_r[r * wu + c];
                            }
                            if c > 0 {
                                qcx += 0.5 * qx_r[r * wu + c - 1];
                            }
                        }
                        if c == 0 {
                            qcx -= 0.5 * qv[r];
                        }
                        if c == w - 1 {
                            qcx += 0.5 * qo[r];
                        }
                        let mut qcy = 0.0f64;
                        if h > 1 {
                            if r < h - 1 {
                                qcy += 0.5 * qy_r[r * w + c];
                            }
                            if r > 0 {
                                qcy += 0.5 * qy_r[(r - 1) * w + c];
                            }
                        }
                        if r == 0 {
                            qcy -= 0.5 * qn[c];
                        }
                        if r == h - 1 {
                            qcy += 0.5 * qs[c];
                        }
                        if qcx == 0.0 && qcy == 0.0 {
                            continue;
                        }
                        let q_mag = qcx.hypot(qcy);
                        let hv = np_max(hf[i], he[i]);
                        if ind.aktiv[i] && hv > min_d {
                            mh[c] = np_max(mh[c], q_mag / hv);
                            mf[c] = np_max(mf[c], q_mag);
                        }
                    }
                });
        }
    }

    Utdata {
        max_djup: max_djup.iter().map(|&v| v as f32).collect(),
        slut_djup: h_eff.iter().map(|&v| v as f32).collect(),
        max_hastighet: max_hast.iter().map(|&v| v as f32).collect(),
        fara: max_fara.iter().map(|&v| v as f32).collect(),
        tid_01,
        tid_02,
        volym,
        in_m3,
        ut_m3,
        antal_steg,
        sum_aktiv_andel,
    }
}
