// Copyright (C) Floden-projektet. Licens: GPL-3.0-or-later.
//! Deterministiska rasterkärnor för SankGraf med bitidentisk paritet mot `src/sankgraf/hierarki.py`:
//! - `berakna_avrinningsomraden`: 4-granne brantaste lutning, kantbarriärer, platå-BFS och
//!   O(N) vägkompression utan HashMap över celler.
//! - `bygg_granspar`: extraktion och sortering av unika gränspar och likvärdiga sadelkanter
//!   inom `SADEL_TIE_TOL_M = 1e-9 m`.

use crate::binar_io::ZData;
use std::cmp::Ordering;
use std::collections::VecDeque;

pub const HAV_NOD_ID: i32 = -1;
pub const BYGGNAD_NOD_ID: i32 = -2;
pub const BOTTEN_MARKOR: i32 = -3;
pub const SADEL_TIE_TOL_M: f64 = 1e-9;

const FLAG_HAR_LAGRE: u8 = 0x01;
const FLAG_BESOKT_PLATT: u8 = 0x02;
const FLAG_I_KOMPONENT: u8 = 0x04;
const FLAG_TILLDELAD: u8 = 0x08;

pub struct AvrinningResultat {
    pub flow_to: Vec<i32>,
    pub cell_till_nod: Vec<i32>,
    pub antal_lov: i32,
}

pub struct GransparResultat {
    pub par_w: Vec<f64>,
    pub par_c_lo: Vec<i32>,
    pub par_c_hi: Vec<i32>,
    pub par_start: Vec<i64>,
    pub par_slut: Vec<i64>,
    pub f_u: Vec<i32>,
    pub f_v: Vec<i32>,
    pub f_cu: Vec<i32>,
    pub f_cv: Vec<i32>,
}

impl GransparResultat {
    pub fn tom() -> Self {
        Self {
            par_w: Vec::new(),
            par_c_lo: Vec::new(),
            par_c_hi: Vec::new(),
            par_start: Vec::new(),
            par_slut: Vec::new(),
            f_u: Vec::new(),
            f_v: Vec::new(),
            f_cu: Vec::new(),
            f_cv: Vec::new(),
        }
    }
}

#[inline(always)]
fn cmp_f64(a: f64, b: f64) -> Ordering {
    // IEEE-ordning där -0.0 == +0.0 (som NumPys lexsort) och NaN får total ordning utan panik.
    a.partial_cmp(&b).unwrap_or_else(|| a.total_cmp(&b))
}

#[inline(always)]
fn max_f64_numpy(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else if b > a {
        b
    } else if a > b {
        a
    } else if a.is_sign_positive() {
        a
    } else {
        b
    }
}

#[inline(always)]
fn ar_rand(u: usize, hojd: usize, bredd: usize) -> bool {
    let r = u / bredd;
    let c = u % bredd;
    r == 0 || r + 1 == hojd || c == 0 || c + 1 == bredd
}

/// Returnerar giltiga 4-grannar till cell `u` i exakt deterministisk ordning: N, S, V, Ö.
#[inline(always)]
fn grannar4(u: usize, hojd: usize, bredd: usize) -> ([usize; 4], usize) {
    let r = u / bredd;
    let c = u % bredd;
    let mut ut = [0usize; 4];
    let mut n = 0usize;
    if r > 0 {
        ut[n] = u - bredd;
        n += 1;
    }
    if r + 1 < hojd {
        ut[n] = u + bredd;
        n += 1;
    }
    if c > 0 {
        ut[n] = u - 1;
        n += 1;
    }
    if c + 1 < bredd {
        ut[n] = u + 1;
        n += 1;
    }
    (ut, n)
}

#[inline]
fn barriar_hojd(barriarer: &[(u64, f64)], u: usize, v: usize) -> Option<f64> {
    if barriarer.is_empty() {
        return None;
    }
    let (a, b) = if u < v {
        (u as u64, v as u64)
    } else {
        (v as u64, u as u64)
    };
    let nyckel = (a << 32) | b;
    barriarer
        .binary_search_by_key(&nyckel, |&(k, _)| k)
        .ok()
        .map(|idx| barriarer[idx].1)
}

#[inline]
fn tillganglig_platt_kant(barriarer: &[(u64, f64)], u: usize, v: usize, z_niv: f64) -> bool {
    match barriar_hojd(barriarer, u, v) {
        Some(bh) => bh <= z_niv,
        None => true,
    }
}

/// Beräknar 4-granne brantaste lutning, lövsänkor och `cell_till_nod` bitidentiskt med
/// `sankgraf.hierarki._berakna_avrinningsomraden`.
pub fn berakna_avrinningsomraden(
    hojd: usize,
    bredd: usize,
    z: &ZData,
    aktiv: &[u8],
    barriarer: &[(u64, f64)],
) -> Result<AvrinningResultat, String> {
    let n_celler = hojd
        .checked_mul(bredd)
        .ok_or_else(|| "Rutnätsstorlek svämmade över usize.".to_string())?;
    if n_celler >= i32::MAX as usize {
        return Err("Rutnätet överskrider i32::MAX celler.".to_string());
    }
    if aktiv.len() != n_celler {
        return Err(format!(
            "aktiv har längd {}, förväntade {n_celler}.",
            aktiv.len()
        ));
    }
    if hojd == 0 || bredd == 0 {
        return Ok(AvrinningResultat {
            flow_to: Vec::new(),
            cell_till_nod: Vec::new(),
            antal_lov: 0,
        });
    }

    let mut flow_to = vec![BYGGNAD_NOD_ID; n_celler];

    // 1. Brantaste nedförsriktning i 4-grannskap (N, S, V, Ö i strikt ordning)
    for r in 0..hojd {
        let rad_bas = r * bredd;
        let har_n = r > 0;
        let har_s = r + 1 < hojd;
        for c in 0..bredd {
            let u = rad_bas + c;
            if aktiv[u] == 0 {
                continue;
            }
            let mut basta_z = z.get_f64(u);
            let mut mal = BYGGNAD_NOD_ID;

            if har_n {
                let v = u - bredd;
                if aktiv[v] != 0 {
                    let zv = z.get_f64(v);
                    if zv < basta_z {
                        basta_z = zv;
                        mal = v as i32;
                    }
                }
            }
            if har_s {
                let v = u + bredd;
                if aktiv[v] != 0 {
                    let zv = z.get_f64(v);
                    if zv < basta_z {
                        basta_z = zv;
                        mal = v as i32;
                    }
                }
            }
            if c > 0 {
                let v = u - 1;
                if aktiv[v] != 0 {
                    let zv = z.get_f64(v);
                    if zv < basta_z {
                        basta_z = zv;
                        mal = v as i32;
                    }
                }
            }
            if c + 1 < bredd {
                let v = u + 1;
                if aktiv[v] != 0 {
                    let zv = z.get_f64(v);
                    if zv < basta_z {
                        mal = v as i32;
                    }
                }
            }
            flow_to[u] = mal;
        }
    }

    // 2. Korrigera celler som gränsar till minst en 2D/3D-kantbarriär
    if !barriarer.is_empty() {
        let mut berorda: Vec<usize> = Vec::with_capacity(barriarer.len() * 2);
        for &(nyckel, _) in barriarer {
            berorda.push((nyckel >> 32) as usize);
            berorda.push((nyckel & 0xFFFF_FFFF) as usize);
        }
        berorda.sort_unstable();
        berorda.dedup();

        for u in berorda {
            if aktiv[u] == 0 {
                continue;
            }
            let zu = z.get_f64(u);
            let mut min_z = zu;
            let mut mal = BYGGNAD_NOD_ID;
            let (gr, n_gr) = grannar4(u, hojd, bredd);
            for &v in &gr[..n_gr] {
                if aktiv[v] == 0 {
                    continue;
                }
                if let Some(bh) = barriar_hojd(barriarer, u, v) {
                    if bh > zu {
                        continue;
                    }
                }
                let zv = z.get_f64(v);
                if zv < min_z {
                    min_z = zv;
                    mal = v as i32;
                }
            }
            flow_to[u] = mal;
        }
    }

    // 3. Platå-BFS för aktiva celler utan strikt lägre granne (plan, randutlopp och lövsänkor)
    let mut flaggor = vec![0u8; n_celler];
    for u in 0..n_celler {
        if flow_to[u] >= 0 {
            flaggor[u] = FLAG_HAR_LAGRE;
        }
    }

    let mut cell_till_nod = vec![BYGGNAD_NOD_ID; n_celler];
    let mut antal_lov: i32 = 0;

    let mut komponent: Vec<usize> = Vec::new();
    let mut utlopp: Vec<usize> = Vec::new();
    let mut ko: VecDeque<usize> = VecDeque::new();

    for start_u in 0..n_celler {
        if aktiv[start_u] == 0 || (flaggor[start_u] & (FLAG_HAR_LAGRE | FLAG_BESOKT_PLATT)) != 0 {
            continue;
        }

        let z_f = z.get_f64(start_u);
        komponent.clear();
        utlopp.clear();
        ko.clear();

        flaggor[start_u] |= FLAG_BESOKT_PLATT | FLAG_I_KOMPONENT;
        ko.push_back(start_u);

        while let Some(curr) = ko.pop_front() {
            komponent.push(curr);
            if (flaggor[curr] & FLAG_HAR_LAGRE) != 0 || ar_rand(curr, hojd, bredd) {
                utlopp.push(curr);
            }

            let (gr, n_gr) = grannar4(curr, hojd, bredd);
            for &nxt in &gr[..n_gr] {
                if aktiv[nxt] != 0
                    && (flaggor[nxt] & FLAG_BESOKT_PLATT) == 0
                    && z.get_f64(nxt) == z_f
                    && tillganglig_platt_kant(barriarer, curr, nxt, z_f)
                {
                    flaggor[nxt] |= FLAG_BESOKT_PLATT | FLAG_I_KOMPONENT;
                    ko.push_back(nxt);
                }
            }
        }

        if utlopp.is_empty() {
            // Sann lövsänka: ingen lägre granne och ingen randcell
            let lov_id = antal_lov;
            antal_lov = antal_lov
                .checked_add(1)
                .ok_or_else(|| "Antal lövsänkor överskred i32::MAX.".to_string())?;
            for &cell in &komponent {
                cell_till_nod[cell] = lov_id;
                flow_to[cell] = BOTTEN_MARKOR;
            }
        } else {
            // Platån dränerar ut via `utlopp`: deterministisk multi-source BFS inåt
            utlopp.sort_unstable();
            ko.clear();
            for &u_ut in &utlopp {
                flaggor[u_ut] |= FLAG_TILLDELAD;
                if (flaggor[u_ut] & FLAG_HAR_LAGRE) == 0 {
                    flow_to[u_ut] = HAV_NOD_ID;
                    cell_till_nod[u_ut] = HAV_NOD_ID;
                }
                ko.push_back(u_ut);
            }

            while let Some(curr) = ko.pop_front() {
                let (gr, n_gr) = grannar4(curr, hojd, bredd);
                for &nxt in &gr[..n_gr] {
                    if (flaggor[nxt] & FLAG_I_KOMPONENT) != 0
                        && (flaggor[nxt] & FLAG_TILLDELAD) == 0
                        && tillganglig_platt_kant(barriarer, curr, nxt, z_f)
                    {
                        flaggor[nxt] |= FLAG_TILLDELAD;
                        flow_to[nxt] = curr as i32;
                        ko.push_back(nxt);
                    }
                }
            }
        }

        // Återställ temporära platåflaggor utan extra allokeringar
        for &cell in &komponent {
            flaggor[cell] &= !(FLAG_I_KOMPONENT | FLAG_TILLDELAD);
        }
    }

    drop(flaggor);
    drop(komponent);
    drop(utlopp);
    drop(ko);

    // 4. Tilldela alla kvarvarande aktiva celler sin mottagande nod via O(N) vägkompression
    for u in 0..n_celler {
        if aktiv[u] != 0 && cell_till_nod[u] == BYGGNAD_NOD_ID && flow_to[u] < 0 {
            cell_till_nod[u] = HAV_NOD_ID;
        }
    }

    let mut kedja: Vec<usize> = Vec::new();
    for start in 0..n_celler {
        if aktiv[start] == 0 || cell_till_nod[start] != BYGGNAD_NOD_ID {
            continue;
        }
        kedja.clear();
        let mut curr = start;
        let mal_nod = loop {
            let nod = cell_till_nod[curr];
            if nod != BYGGNAD_NOD_ID {
                break nod;
            }
            let nxt = flow_to[curr];
            if nxt < 0 {
                break HAV_NOD_ID;
            }
            kedja.push(curr);
            if kedja.len() > n_celler {
                return Err("Cykel upptäckt i flow_to vid vägkompression.".to_string());
            }
            curr = nxt as usize;
        };
        for &c in &kedja {
            cell_till_nod[c] = mal_nod;
        }
    }

    Ok(AvrinningResultat {
        flow_to,
        cell_till_nod,
        antal_lov,
    })
}

struct GransKant {
    w: f64,
    u: i32,
    v: i32,
    cu: i32,
    cv: i32,
}

struct ParPost {
    w: f64,
    c_lo: i32,
    c_hi: i32,
    start: i64,
    slut: i64,
}

/// Extraherar unika gränspar mellan distinkta avrinningsområden och mot ytterranden (`HAV_NOD_ID`)
/// bitidentiskt med `sankgraf.hierarki._bygg_granspar`.
pub fn bygg_granspar(
    hojd: usize,
    bredd: usize,
    catch: &[i32],
    aktiv: &[u8],
    z: &ZData,
    barriarer: &[(u64, f64)],
) -> Result<GransparResultat, String> {
    let n_celler = hojd
        .checked_mul(bredd)
        .ok_or_else(|| "Rutnätsstorlek svämmade över usize.".to_string())?;
    if n_celler >= i32::MAX as usize {
        return Err("Rutnätet överskrider i32::MAX celler.".to_string());
    }
    if catch.len() != n_celler || aktiv.len() != n_celler {
        return Err("Felaktig längd på catch eller aktiv i bygg_granspar.".to_string());
    }
    if hojd == 0 || bredd == 0 {
        return Ok(GransparResultat::tom());
    }

    let mut kanter: Vec<GransKant> = Vec::new();

    // 1. Vertikala grannkanter: (r, c) <-> (r + 1, c)
    if hojd > 1 {
        for r in 0..(hojd - 1) {
            let rad_u = r * bredd;
            let rad_v = rad_u + bredd;
            for c in 0..bredd {
                let u = rad_u + c;
                let v = rad_v + c;
                if aktiv[u] != 0 && aktiv[v] != 0 {
                    let cu = catch[u];
                    let cv = catch[v];
                    if cu != cv {
                        let w = barriar_hojd(barriarer, u, v)
                            .unwrap_or_else(|| max_f64_numpy(z.get_f64(u), z.get_f64(v)));
                        kanter.push(GransKant {
                            w,
                            u: u as i32,
                            v: v as i32,
                            cu,
                            cv,
                        });
                    }
                }
            }
        }
    }

    // 2. Horisontella grannkanter: (r, c) <-> (r, c + 1)
    if bredd > 1 {
        for r in 0..hojd {
            let rad_bas = r * bredd;
            for c in 0..(bredd - 1) {
                let u = rad_bas + c;
                let v = u + 1;
                if aktiv[u] != 0 && aktiv[v] != 0 {
                    let cu = catch[u];
                    let cv = catch[v];
                    if cu != cv {
                        let w = barriar_hojd(barriarer, u, v)
                            .unwrap_or_else(|| max_f64_numpy(z.get_f64(u), z.get_f64(v)));
                        kanter.push(GransKant {
                            w,
                            u: u as i32,
                            v: v as i32,
                            cu,
                            cv,
                        });
                    }
                }
            }
        }
    }

    // 3. Ytterrandskanter till HAV för aktiva randceller med catch >= 0 (varje hörn exakt en gång)
    // Översta raden (r = 0)
    for c in 0..bredd {
        let u = c;
        if aktiv[u] != 0 && catch[u] >= 0 {
            kanter.push(GransKant {
                w: z.get_f64(u),
                u: u as i32,
                v: HAV_NOD_ID,
                cu: catch[u],
                cv: HAV_NOD_ID,
            });
        }
    }
    // Nedersta raden (r = hojd - 1)
    if hojd > 1 {
        let rad_bas = (hojd - 1) * bredd;
        for c in 0..bredd {
            let u = rad_bas + c;
            if aktiv[u] != 0 && catch[u] >= 0 {
                kanter.push(GransKant {
                    w: z.get_f64(u),
                    u: u as i32,
                    v: HAV_NOD_ID,
                    cu: catch[u],
                    cv: HAV_NOD_ID,
                });
            }
        }
    }
    // Vänster och höger kolumn (r = 1 .. hojd - 1)
    if hojd > 2 {
        for r in 1..(hojd - 1) {
            let u_v = r * bredd;
            if aktiv[u_v] != 0 && catch[u_v] >= 0 {
                kanter.push(GransKant {
                    w: z.get_f64(u_v),
                    u: u_v as i32,
                    v: HAV_NOD_ID,
                    cu: catch[u_v],
                    cv: HAV_NOD_ID,
                });
            }
            if bredd > 1 {
                let u_h = u_v + (bredd - 1);
                if aktiv[u_h] != 0 && catch[u_h] >= 0 {
                    kanter.push(GransKant {
                        w: z.get_f64(u_h),
                        u: u_h as i32,
                        v: HAV_NOD_ID,
                        cu: catch[u_h],
                        cv: HAV_NOD_ID,
                    });
                }
            }
        }
    }

    if kanter.is_empty() {
        return Ok(GransparResultat::tom());
    }

    // 4. Sortera på (c_lo, c_hi, w, u, v): exakt total ordning eftersom varje (u, v) är unik
    kanter.sort_unstable_by(|a, b| {
        let a_lo = a.cu.min(a.cv);
        let b_lo = b.cu.min(b.cv);
        a_lo.cmp(&b_lo)
            .then_with(|| a.cu.max(a.cv).cmp(&b.cu.max(b.cv)))
            .then_with(|| cmp_f64(a.w, b.w))
            .then_with(|| a.u.cmp(&b.u))
            .then_with(|| a.v.cmp(&b.v))
    });

    // 5. Gruppera per unikt (c_lo, c_hi) och behåll alla sadelkanter inom SADEL_TIE_TOL_M
    let mut par_lista: Vec<ParPost> = Vec::new();
    let mut f_u: Vec<i32> = Vec::new();
    let mut f_v: Vec<i32> = Vec::new();
    let mut f_cu: Vec<i32> = Vec::new();
    let mut f_cv: Vec<i32> = Vec::new();

    let n_kanter = kanter.len();
    let mut idx = 0usize;
    while idx < n_kanter {
        let c_lo = kanter[idx].cu.min(kanter[idx].cv);
        let c_hi = kanter[idx].cu.max(kanter[idx].cv);
        let w_min = kanter[idx].w;
        let start = f_u.len() as i64;

        while idx < n_kanter {
            let e = &kanter[idx];
            if e.cu.min(e.cv) != c_lo || e.cu.max(e.cv) != c_hi {
                break;
            }
            if (e.w - w_min) <= SADEL_TIE_TOL_M {
                f_u.push(e.u);
                f_v.push(e.v);
                f_cu.push(e.cu);
                f_cv.push(e.cv);
            }
            idx += 1;
        }

        let slut = f_u.len() as i64;
        par_lista.push(ParPost {
            w: w_min,
            c_lo,
            c_hi,
            start,
            slut,
        });
    }
    drop(kanter);

    // 6. Sortera unika par i stigande (w_min, c_lo, c_hi) för Kruskal / Barnes (2020)
    par_lista.sort_unstable_by(|a, b| {
        cmp_f64(a.w, b.w)
            .then_with(|| a.c_lo.cmp(&b.c_lo))
            .then_with(|| a.c_hi.cmp(&b.c_hi))
    });

    let n_par = par_lista.len();
    let mut par_w = Vec::with_capacity(n_par);
    let mut par_c_lo = Vec::with_capacity(n_par);
    let mut par_c_hi = Vec::with_capacity(n_par);
    let mut par_start = Vec::with_capacity(n_par);
    let mut par_slut = Vec::with_capacity(n_par);

    for p in par_lista {
        par_w.push(p.w);
        par_c_lo.push(p.c_lo);
        par_c_hi.push(p.c_hi);
        par_start.push(p.start);
        par_slut.push(p.slut);
    }

    Ok(GransparResultat {
        par_w,
        par_c_lo,
        par_c_hi,
        par_start,
        par_slut,
        f_u,
        f_v,
        f_cu,
        f_cv,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kantfall_1x1_och_1x5_och_5x1_och_tom() {
        // 0x0
        let r0 = berakna_avrinningsomraden(0, 0, &ZData::F32(vec![]), &[], &[]).expect("0x0");
        assert_eq!(r0.antal_lov, 0);
        assert!(r0.flow_to.is_empty());

        // 1x1 aktiv -> randutlopp mot HAV
        let r1 =
            berakna_avrinningsomraden(1, 1, &ZData::F32(vec![10.0]), &[1], &[]).expect("1x1");
        assert_eq!(r1.antal_lov, 0);
        assert_eq!(r1.flow_to, vec![HAV_NOD_ID]);
        assert_eq!(r1.cell_till_nod, vec![HAV_NOD_ID]);

        // 1x1 inaktiv (byggnad / NaN)
        let r1_in = berakna_avrinningsomraden(1, 1, &ZData::F32(vec![f32::NAN]), &[0], &[])
            .expect("1x1 inaktiv");
        assert_eq!(r1_in.antal_lov, 0);
        assert_eq!(r1_in.flow_to, vec![BYGGNAD_NOD_ID]);
        assert_eq!(r1_in.cell_till_nod, vec![BYGGNAD_NOD_ID]);

        // 1x5 [10, 11, 10, 11, 10]: cell 1 har V=0 och Ö=2 på 10 -> V vinner
        let r1x5 = berakna_avrinningsomraden(
            1,
            5,
            &ZData::F32(vec![10.0, 11.0, 10.0, 11.0, 10.0]),
            &[1; 5],
            &[],
        )
        .expect("1x5");
        assert_eq!(r1x5.antal_lov, 0);
        assert_eq!(r1x5.flow_to, vec![-1, 0, -1, 2, -1]);
        assert_eq!(r1x5.cell_till_nod, vec![-1; 5]);

        // 5x1 [10, 11, 10, 11, 10]: cell 1 har N=0 och S=2 på 10 -> N vinner
        let r5x1 = berakna_avrinningsomraden(
            5,
            1,
            &ZData::F32(vec![10.0, 11.0, 10.0, 11.0, 10.0]),
            &[1; 5],
            &[],
        )
        .expect("5x1");
        assert_eq!(r5x1.antal_lov, 0);
        assert_eq!(r5x1.flow_to, vec![-1, 0, -1, 2, -1]);
    }

    #[test]
    fn tie_breaking_ordning_n_s_v_o() {
        // 3x3 med topp i mitten (1,1)=12.0 och lika låga grannar i N, S, V, Ö (=10.0)
        // N (index 1) måste vinna!
        let mut z = vec![11.0f32; 9];
        z[4] = 12.0;
        z[1] = 10.0; // N
        z[7] = 10.0; // S
        z[3] = 10.0; // V
        z[5] = 10.0; // Ö
        let res =
            berakna_avrinningsomraden(3, 3, &ZData::F32(z.clone()), &[1; 9], &[]).expect("NSVO");
        assert_eq!(res.flow_to[4], 1);

        // Höj N -> S (index 7) vinner
        z[1] = 11.0;
        let res_s =
            berakna_avrinningsomraden(3, 3, &ZData::F32(z.clone()), &[1; 9], &[]).expect("SVO");
        assert_eq!(res_s.flow_to[4], 7);

        // Höj S -> V (index 3) vinner
        z[7] = 11.0;
        let res_v =
            berakna_avrinningsomraden(3, 3, &ZData::F32(z.clone()), &[1; 9], &[]).expect("VO");
        assert_eq!(res_v.flow_to[4], 3);

        // Höj V -> Ö (index 5) vinner
        z[3] = 11.0;
        let res_o = berakna_avrinningsomraden(3, 3, &ZData::F32(z), &[1; 9], &[]).expect("O");
        assert_eq!(res_o.flow_to[4], 5);
    }

    #[test]
    fn instangd_sanka_och_platt_plata_med_utlopp() {
        // 5x5: kant = 12.0, inre 3x3 = 10.0 -> 1 lövsänka (9 celler)
        let mut z = vec![12.0f32; 25];
        for r in 1..4 {
            for c in 1..4 {
                z[r * 5 + c] = 10.0;
            }
        }
        let r_sanka =
            berakna_avrinningsomraden(5, 5, &ZData::F32(z.clone()), &[1; 25], &[]).expect("sanka");
        assert_eq!(r_sanka.antal_lov, 1);
        for r in 1..4 {
            for c in 1..4 {
                let u = r * 5 + c;
                assert_eq!(r_sanka.cell_till_nod[u], 0);
                assert_eq!(r_sanka.flow_to[u], BOTTEN_MARKOR);
            }
        }
        // Sadelhöjden till HAV ska vara 12.0 (randcellerna rinner in i sänkan -> ytterrandskanter!)
        let gp = bygg_granspar(5, 5, &r_sanka.cell_till_nod, &[1; 25], &ZData::F32(z.clone()), &[])
            .expect("granspar");
        assert_eq!(gp.par_w.len(), 1);
        assert_eq!(gp.par_c_lo, vec![HAV_NOD_ID]);
        assert_eq!(gp.par_c_hi, vec![0]);
        assert_eq!(gp.par_w[0], 12.0);

        // Öppna en cell i västra randen (2, 0) till 10.0 -> hela platån dränerar till HAV!
        z[2 * 5] = 10.0;
        let r_oppen =
            berakna_avrinningsomraden(5, 5, &ZData::F32(z), &[1; 25], &[]).expect("oppen");
        assert_eq!(r_oppen.antal_lov, 0);
        assert_eq!(r_oppen.flow_to[2 * 5], HAV_NOD_ID);
        assert!(r_oppen.cell_till_nod.iter().all(|&nid| nid == HAV_NOD_ID));
    }

    #[test]
    fn barriar_blockerar_och_tie_tolerans_i_granspar() {
        // Två kanter mellan (0, 1) med viktskillnad 5e-10 <= 1e-9: båda ska behållas
        let catch = vec![0i32, 1, 0, 1];
        let aktiv = vec![1u8; 4];
        let z = ZData::F32(vec![10.0; 4]);
        let barriarer = vec![((0u64 << 32) | 1u64, 10.5 + 5e-10), ((2u64 << 32) | 3u64, 10.5)];
        let gp = bygg_granspar(2, 2, &catch, &aktiv, &z, &barriarer).expect("tie");

        let pos = gp
            .par_c_lo
            .iter()
            .zip(gp.par_c_hi.iter())
            .position(|(&lo, &hi)| lo == 0 && hi == 1)
            .expect("paret (0, 1) måste finnas");
        assert!((gp.par_w[pos] - 10.5).abs() < 1e-12);
        let s0 = gp.par_start[pos] as usize;
        let s1 = gp.par_slut[pos] as usize;
        let mut kanter: Vec<(i32, i32)> = (s0..s1).map(|i| (gp.f_u[i], gp.f_v[i])).collect();
        kanter.sort_unstable();
        assert_eq!(kanter, vec![(0, 1), (2, 3)]);
    }

    #[test]
    fn kantbarriar_skapar_sanka_bakom_mur() {
        // 3x3 med mittcell (1,1)=10.0 och övriga 10.0 (helt platt), men 4 barriärer runt (1,1)
        // med höjd 11.0 -> (1,1) blir isolerad lövsänka medan övriga dränerar till HAV!
        let z = ZData::F32(vec![10.0; 9]);
        let aktiv = vec![1u8; 9];
        let mut barriarer = vec![
            ((1u64 << 32) | 4u64, 11.0), // N: (1, 4)
            ((4u64 << 32) | 7u64, 11.0), // S: (4, 7)
            ((3u64 << 32) | 4u64, 11.0), // V: (3, 4)
            ((4u64 << 32) | 5u64, 11.0), // Ö: (4, 5)
        ];
        barriarer.sort_unstable_by_key(|&(k, _)| k);
        let res = berakna_avrinningsomraden(3, 3, &z, &aktiv, &barriarer).expect("mur");
        assert_eq!(res.antal_lov, 1);
        assert_eq!(res.cell_till_nod[4], 0);
        assert_eq!(res.flow_to[4], BOTTEN_MARKOR);
        for (i, &nid) in res.cell_till_nod.iter().enumerate() {
            if i != 4 {
                assert_eq!(nid, HAV_NOD_ID);
            }
        }
        let gp = bygg_granspar(3, 3, &res.cell_till_nod, &aktiv, &z, &barriarer).expect("gp");
        assert_eq!(gp.par_w, vec![11.0]);
        assert_eq!(gp.par_c_lo, vec![HAV_NOD_ID]);
        assert_eq!(gp.par_c_hi, vec![0]);
        assert_eq!(gp.f_u.len(), 4);
    }
}
