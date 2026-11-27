// Copyright (C) Floden-projektet. Licens: GPL-3.0-or-later.
//! Deterministisk prioritetsfyllning (Barnes Priority-Flood), D8-flödesriktning och
//! nedströms tillrinningsackumulering med bitidentisk paritet mot `src/sankgraf/avrinningsnat.py`.

use crate::binar_io::ZData;
use std::cmp::Ordering;
use std::collections::BinaryHeap;

const GRANNAR8: [(isize, isize, f64); 8] = [
    (-1, 0, 1.0),
    (1, 0, 1.0),
    (0, -1, 1.0),
    (0, 1, 1.0),
    (-1, -1, std::f64::consts::SQRT_2),
    (-1, 1, std::f64::consts::SQRT_2),
    (1, -1, std::f64::consts::SQRT_2),
    (1, 1, std::f64::consts::SQRT_2),
];

const FALL_EPS: f64 = 1e-12;

pub struct AvrinningsnatResultat {
    pub flow_to: Vec<i32>,
    pub ordning: Vec<i32>,
}

#[inline(always)]
fn cmp_f64_ieee(a: f64, b: f64) -> Ordering {
    // IEEE-ordning där -0.0 == +0.0 (som Python/NumPy) med total fallback för NaN.
    a.partial_cmp(&b).unwrap_or_else(|| a.total_cmp(&b))
}

#[derive(Clone, Copy)]
struct HeapPost {
    z: f64,
    rakn: u64,
    cell: u32,
}

impl PartialEq for HeapPost {
    fn eq(&self, other: &Self) -> bool {
        cmp_f64_ieee(self.z, other.z) == Ordering::Equal && self.rakn == other.rakn
    }
}

impl Eq for HeapPost {}

impl PartialOrd for HeapPost {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for HeapPost {
    fn cmp(&self, other: &Self) -> Ordering {
        // Min-heap på (z, rakn): omvänd jämförelse mot BinaryHeaps max-heap.
        cmp_f64_ieee(other.z, self.z).then_with(|| other.rakn.cmp(&self.rakn))
    }
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
fn diag_barriar_hojd(
    barriarer: &[(u64, f64)],
    aktiv: &[u8],
    z_ref: &[f64],
    bredd: usize,
    cr: isize,
    cc: isize,
    dr: isize,
    dc: isize,
) -> Option<f64> {
    if barriarer.is_empty() {
        return None;
    }
    let u = (cr as usize) * bredd + (cc as usize);
    let v = ((cr + dr) as usize) * bredd + ((cc + dc) as usize);
    let m1 = ((cr + dr) as usize) * bredd + (cc as usize);
    let m2 = (cr as usize) * bredd + ((cc + dc) as usize);

    let h1a = barriar_hojd(barriarer, u, m1);
    let h1b = barriar_hojd(barriarer, m1, v);
    let h2a = barriar_hojd(barriarer, u, m2);
    let h2b = barriar_hojd(barriarer, m2, v);

    if h1a.is_none() && h1b.is_none() && h2a.is_none() && h2b.is_none() {
        return None;
    }

    let b1 = if aktiv[m1] == 0 {
        f64::INFINITY
    } else {
        let mut h = z_ref[m1];
        if let Some(a) = h1a {
            if a > h {
                h = a;
            }
        }
        if let Some(b) = h1b {
            if b > h {
                h = b;
            }
        }
        h
    };
    let b2 = if aktiv[m2] == 0 {
        f64::INFINITY
    } else {
        let mut h = z_ref[m2];
        if let Some(a) = h2a {
            if a > h {
                h = a;
            }
        }
        if let Some(b) = h2b {
            if b > h {
                h = b;
            }
        }
        h
    };

    Some(b1.min(b2))
}

fn bygg_nar_barriar(hojd: usize, bredd: usize, n: usize, barriarer: &[(u64, f64)]) -> Vec<u8> {
    let mut nar = vec![0u8; n];
    for &(nyckel, _) in barriarer {
        let bu = (nyckel >> 32) as usize;
        let bv = (nyckel & 0xFFFF_FFFF) as usize;
        if bu >= n || bv >= n {
            continue;
        }
        if bv == bu + bredd {
            // Vertikal 4-grannekant (r, c) <-> (r + 1, c): markera de två angränsande 2x2-kvadraterna.
            let r = bu / bredd;
            let c = bu % bredd;
            let r1 = r + 1;
            nar[r * bredd + c] = 1;
            nar[r1 * bredd + c] = 1;
            if c > 0 {
                nar[r * bredd + (c - 1)] = 1;
                nar[r1 * bredd + (c - 1)] = 1;
            }
            if c + 1 < bredd {
                nar[r * bredd + (c + 1)] = 1;
                nar[r1 * bredd + (c + 1)] = 1;
            }
        } else if bv == bu + 1 {
            // Horisontell 4-grannekant (r, c) <-> (r, c + 1): markera de två angränsande 2x2-kvadraterna.
            let r = bu / bredd;
            let c = bu % bredd;
            let c1 = c + 1;
            nar[r * bredd + c] = 1;
            nar[r * bredd + c1] = 1;
            if r > 0 {
                nar[(r - 1) * bredd + c] = 1;
                nar[(r - 1) * bredd + c1] = 1;
            }
            if r + 1 < hojd {
                nar[(r + 1) * bredd + c] = 1;
                nar[(r + 1) * bredd + c1] = 1;
            }
        } else {
            nar[bu] = 1;
            nar[bv] = 1;
        }
    }
    nar
}

/// Prioritetsfyllning (8-grannar) och D8-flödesriktning bitidentiskt med
/// `sankgraf.avrinningsnat.fyll_och_riktning`.
///
/// När `barriarer` är tom körs den exakta ursprungliga snabbvägen (100 % bitidentisk).
/// När `barriarer` anges körs minimax-Priority-Flood med kanttrösklar och blockering av
/// diagonala steg över barriärer så att 20°- och 45°-kantstenar aldrig läcker diagonalt.
pub fn fyll_och_riktning(
    hojd: usize,
    bredd: usize,
    z: &ZData,
    aktiv: &[u8],
    barriarer: &[(u64, f64)],
) -> Result<AvrinningsnatResultat, String> {
    let n = hojd
        .checked_mul(bredd)
        .ok_or_else(|| "Rutnätsstorlek svämmade över usize.".to_string())?;
    if n >= i32::MAX as usize {
        return Err("Rutnätet överskrider i32::MAX celler.".to_string());
    }
    if aktiv.len() != n {
        return Err(format!(
            "aktiv har längd {}, förväntade {n}.",
            aktiv.len()
        ));
    }
    if hojd == 0 || bredd == 0 {
        return Ok(AvrinningsnatResultat {
            flow_to: Vec::new(),
            ordning: Vec::new(),
        });
    }

    let mut zf = vec![f64::INFINITY; n];
    let mut klar = vec![1u8; n];
    let mut parent = vec![-2i32; n];
    let mut antal_aktiva = 0usize;

    for u in 0..n {
        if aktiv[u] != 0 {
            zf[u] = z.get_f64(u);
            klar[u] = 0;
            antal_aktiva += 1;
        }
    }

    let mut ko: BinaryHeap<HeapPost> = BinaryHeap::new();
    let mut rakn: u64 = 0;
    let mut ordning: Vec<i32> = Vec::with_capacity(antal_aktiva);
    let mut lagsta: Vec<u32> = Vec::new();
    let mut lagsta_byggd = false;
    let mut pek = 0usize;

    let h_isize = hojd as isize;
    let b_isize = bredd as isize;

    let nar_barriar = if barriarer.is_empty() {
        Vec::new()
    } else {
        bygg_nar_barriar(hojd, bredd, n, barriarer)
    };

    if barriarer.is_empty() {
        // 1. Lägg in aktiva randceller i exakt radordning (identiskt med `idx[kant & aktiv]`).
        for r in 0..hojd {
            let rad_bas = r * bredd;
            let hel_rad = r == 0 || r + 1 == hojd;
            if hel_rad {
                for c in 0..bredd {
                    let u = rad_bas + c;
                    if aktiv[u] != 0 {
                        klar[u] = 1;
                        parent[u] = -1;
                        ko.push(HeapPost {
                            z: zf[u],
                            rakn,
                            cell: u as u32,
                        });
                        rakn += 1;
                    }
                }
            } else {
                let u0 = rad_bas;
                if aktiv[u0] != 0 {
                    klar[u0] = 1;
                    parent[u0] = -1;
                    ko.push(HeapPost {
                        z: zf[u0],
                        rakn,
                        cell: u0 as u32,
                    });
                    rakn += 1;
                }
                if bredd > 1 {
                    let u1 = rad_bas + bredd - 1;
                    if aktiv[u1] != 0 {
                        klar[u1] = 1;
                        parent[u1] = -1;
                        ko.push(HeapPost {
                            z: zf[u1],
                            rakn,
                            cell: u1 as u32,
                        });
                        rakn += 1;
                    }
                }
            }
        }

        loop {
            while let Some(post) = ko.pop() {
                let zc = post.z;
                let c = post.cell as usize;
                ordning.push(c as i32);
                let cr = (c / bredd) as isize;
                let cc = (c % bredd) as isize;
                for &(dr, dc, _) in &GRANNAR8 {
                    let nr = cr + dr;
                    let nc = cc + dc;
                    if nr >= 0 && nr < h_isize && nc >= 0 && nc < b_isize {
                        let v = (nr as usize) * bredd + (nc as usize);
                        if klar[v] == 0 {
                            klar[v] = 1;
                            parent[v] = c as i32;
                            if zf[v] < zc {
                                zf[v] = zc;
                            }
                            ko.push(HeapPost {
                                z: zf[v],
                                rakn,
                                cell: v as u32,
                            });
                            rakn += 1;
                        }
                    }
                }
            }

            if ordning.len() >= antal_aktiva {
                break;
            }

            // Slutet område (omgivet av byggnader/inaktiva): sortera kvarvarande aktiva celler
            // stabilt efter (zf[u], u), exakt som `np.argsort(..., kind="stable")`.
            if !lagsta_byggd {
                lagsta_byggd = true;
                lagsta.reserve(antal_aktiva - ordning.len());
                for (u, &k) in klar.iter().enumerate() {
                    if k == 0 {
                        lagsta.push(u as u32);
                    }
                }
                lagsta.sort_by(|&a, &b| {
                    cmp_f64_ieee(zf[a as usize], zf[b as usize]).then_with(|| a.cmp(&b))
                });
            }

            while pek < lagsta.len() && klar[lagsta[pek] as usize] != 0 {
                pek += 1;
            }
            if pek >= lagsta.len() {
                break;
            }
            let u = lagsta[pek] as usize;
            klar[u] = 1;
            parent[u] = -1;
            ko.push(HeapPost {
                z: zf[u],
                rakn,
                cell: u as u32,
            });
            rakn += 1;
        }
    } else {
        // Barriärmedveten minimax-Priority-Flood: en cell låses först när den poppas med sin
        // lägsta möjliga tröskelnivå, så en låg baksida aldrig förgiftar en kantstensskyddad sida.
        let z_raw = zf.clone();
        let mut best_z = vec![f64::INFINITY; n];

        for r in 0..hojd {
            let rad_bas = r * bredd;
            let hel_rad = r == 0 || r + 1 == hojd;
            if hel_rad {
                for c in 0..bredd {
                    let u = rad_bas + c;
                    if aktiv[u] != 0 {
                        best_z[u] = z_raw[u];
                        parent[u] = -1;
                        ko.push(HeapPost {
                            z: z_raw[u],
                            rakn,
                            cell: u as u32,
                        });
                        rakn += 1;
                    }
                }
            } else {
                let u0 = rad_bas;
                if aktiv[u0] != 0 {
                    best_z[u0] = z_raw[u0];
                    parent[u0] = -1;
                    ko.push(HeapPost {
                        z: z_raw[u0],
                        rakn,
                        cell: u0 as u32,
                    });
                    rakn += 1;
                }
                if bredd > 1 {
                    let u1 = rad_bas + bredd - 1;
                    if aktiv[u1] != 0 {
                        best_z[u1] = z_raw[u1];
                        parent[u1] = -1;
                        ko.push(HeapPost {
                            z: z_raw[u1],
                            rakn,
                            cell: u1 as u32,
                        });
                        rakn += 1;
                    }
                }
            }
        }

        loop {
            while let Some(post) = ko.pop() {
                let c = post.cell as usize;
                if klar[c] != 0 {
                    continue;
                }
                klar[c] = 1;
                let zc = post.z;
                zf[c] = zc;
                ordning.push(c as i32);

                let cr = (c / bredd) as isize;
                let cc = (c % bredd) as isize;
                let c_nar = nar_barriar[c] != 0;
                for &(dr, dc, _) in &GRANNAR8 {
                    let nr = cr + dr;
                    let nc = cc + dc;
                    if nr >= 0 && nr < h_isize && nc >= 0 && nc < b_isize {
                        let v = (nr as usize) * bredd + (nc as usize);
                        if klar[v] != 0 {
                            continue;
                        }
                        let zb_opt = if !c_nar {
                            None
                        } else if dr == 0 || dc == 0 {
                            barriar_hojd(barriarer, c, v)
                        } else {
                            let d_opt =
                                diag_barriar_hojd(barriarer, aktiv, &z_raw, bredd, cr, cc, dr, dc);
                            if let Some(zb) = d_opt {
                                let max_end = z_raw[c].max(z_raw[v]);
                                if !zb.is_finite() || zb > max_end + FALL_EPS {
                                    continue;
                                }
                            }
                            d_opt
                        };

                        let mut cand = z_raw[v];
                        if zc > cand {
                            cand = zc;
                        }
                        if let Some(zb) = zb_opt {
                            if zb > cand {
                                cand = zb;
                            }
                        }
                        if cand < best_z[v] {
                            best_z[v] = cand;
                            parent[v] = c as i32;
                            ko.push(HeapPost {
                                z: cand,
                                rakn,
                                cell: v as u32,
                            });
                            rakn += 1;
                        }
                    }
                }
            }

            if ordning.len() >= antal_aktiva {
                break;
            }

            if !lagsta_byggd {
                lagsta_byggd = true;
                lagsta.reserve(antal_aktiva - ordning.len());
                for (u, &k) in klar.iter().enumerate() {
                    if k == 0 {
                        lagsta.push(u as u32);
                    }
                }
                lagsta.sort_by(|&a, &b| {
                    cmp_f64_ieee(z_raw[a as usize], z_raw[b as usize]).then_with(|| a.cmp(&b))
                });
            }

            while pek < lagsta.len() && klar[lagsta[pek] as usize] != 0 {
                pek += 1;
            }
            if pek >= lagsta.len() {
                break;
            }
            let u = lagsta[pek] as usize;
            best_z[u] = z_raw[u];
            parent[u] = -1;
            ko.push(HeapPost {
                z: z_raw[u],
                rakn,
                cell: u as u32,
            });
            rakn += 1;
        }
    }

    drop(klar);
    drop(lagsta);
    drop(ko);

    // 2. D8: brantast nedåt på den fyllda ytan; platta celler följer fyllningens väg.
    // Barriärkanter med z_barriar > zf[u] + FALL_EPS blockeras så flödet följer rännstenen.
    // Randceller är alltid utlopp (-1) och inaktiva alltid -2.
    let mut flow_to = vec![-2i32; n];
    let har_barriarer = !barriarer.is_empty();
    for r in 0..hojd {
        let rad_bas = r * bredd;
        let ar_kant_rad = r == 0 || r + 1 == hojd;
        for c in 0..bredd {
            let u = rad_bas + c;
            if aktiv[u] == 0 {
                continue;
            }
            if ar_kant_rad || c == 0 || c + 1 == bredd {
                flow_to[u] = -1;
                continue;
            }
            let zu = zf[u];
            let mut basta = 0.0f64;
            let mut mal = -1i32;
            let r_i = r as isize;
            let c_i = c as isize;
            let u_nar = har_barriarer && nar_barriar[u] != 0;
            for &(dr, dc, avst) in &GRANNAR8 {
                let v = ((r_i + dr) as usize) * bredd + ((c_i + dc) as usize);
                if aktiv[v] != 0 {
                    if u_nar {
                        let zb_opt = if dr == 0 || dc == 0 {
                            barriar_hojd(barriarer, u, v)
                        } else {
                            diag_barriar_hojd(barriarer, aktiv, &zf, bredd, r_i, c_i, dr, dc)
                        };
                        if let Some(zb) = zb_opt {
                            if !zb.is_finite() || zb > zu + FALL_EPS {
                                continue;
                            }
                        }
                    }
                    let fall = (zu - zf[v]) / avst;
                    if fall > basta + FALL_EPS {
                        basta = fall;
                        mal = v as i32;
                    }
                }
            }
            flow_to[u] = if mal >= 0 { mal } else { parent[u] };
        }
    }

    Ok(AvrinningsnatResultat { flow_to, ordning })
}

/// Summerar tillrinningsytan nedströms i exakt omvänd `ordning` (bitidentiskt med
/// `sankgraf.avrinningsnat.tillrinning`).
pub fn berakna_tillrinning(
    flow_to: &[i32],
    ordning: &[i32],
    egen: &[f64],
) -> Result<Vec<f64>, String> {
    if flow_to.len() != egen.len() {
        return Err(format!(
            "flow_to ({}) och egen ({}) har olika längd.",
            flow_to.len(),
            egen.len()
        ));
    }
    let mut a = egen.to_vec();
    for &u_i32 in ordning.iter().rev() {
        if u_i32 < 0 {
            continue;
        }
        let u = u_i32 as usize;
        let v = flow_to[u];
        if v >= 0 {
            a[v as usize] += a[u];
        }
    }
    Ok(a)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kant_nyckel(u: usize, v: usize) -> u64 {
        let (a, b) = if u < v { (u as u64, v as u64) } else { (v as u64, u as u64) };
        (a << 32) | b
    }

    #[test]
    fn v_dal_fyll_och_tillrinning() {
        let hojd = 10usize;
        let bredd = 7usize;
        let mut z = Vec::with_capacity(hojd * bredd);
        for r in 0..hojd {
            for c in 0..bredd {
                let zv = 0.05 * ((c as f64) - 3.0).abs() + 0.02 * ((hojd - r) as f64);
                z.push(zv);
            }
        }
        let aktiv = vec![1u8; hojd * bredd];
        let res = fyll_och_riktning(hojd, bredd, &ZData::F64(z), &aktiv, &[]).expect("fyll");
        assert_eq!(res.flow_to.len(), hojd * bredd);
        assert_eq!(res.ordning.len(), hojd * bredd);
        // Nedre mitten (rad 9, kol 3) är utlopp (-1)
        assert_eq!(res.flow_to[9 * bredd + 3], -1);
        // Rad 5, kol 3 ska rinna söderut till rad 6, kol 3
        assert_eq!(res.flow_to[5 * bredd + 3], (6 * bredd + 3) as i32);

        let egen = vec![1.0f64; hojd * bredd];
        let a = berakna_tillrinning(&res.flow_to, &res.ordning, &egen).expect("tillrinning");
        // Utloppet vid dalbotten tar emot merparten av dalen
        assert!(a[9 * bredd + 3] > 30.0);
    }

    #[test]
    fn slutet_omrade_med_byggnadsmur_far_lagsta_cell_som_utlopp() {
        // 7x7 med byggnadsring vid r=1..=5, c=1..=5 utom inre 3x3 (r=2..=4, c=2..=4)
        let h = 7usize;
        let b = 7usize;
        let mut z = vec![10.0f64; h * b];
        let mut aktiv = vec![1u8; h * b];
        for r in 1..=5 {
            for c in 1..=5 {
                let ar_ring = r == 1 || r == 5 || c == 1 || c == 5;
                if ar_ring {
                    aktiv[r * b + c] = 0;
                }
            }
        }
        // Inre gården sluttar mot (3, 3) med z=8.0
        z[3 * b + 3] = 8.0;
        let res = fyll_och_riktning(h, b, &ZData::F64(z), &aktiv, &[]).expect("fyll slutet");
        let antal_aktiva = aktiv.iter().filter(|&&x| x != 0).count();
        assert_eq!(res.ordning.len(), antal_aktiva);
        // Lägsta cellen i det slutna området är utlopp (-1)
        assert_eq!(res.flow_to[3 * b + 3], -1);
        // Granncell i gården rinner till (3, 3)
        assert_eq!(res.flow_to[2 * b + 3], (3 * b + 3) as i32);
        // Byggnadscell är inaktiv (-2)
        assert_eq!(res.flow_to[b + 1], -2);
    }

    #[test]
    fn kantsten_stoppar_lackage_till_lagre_gard_och_styr_langs_rannsten() {
        // 8x12: gata (r=0..=3) sluttar mot öster (c=11) längs r=3, lägre gård (r=4..=7).
        // 12 cm kantsten mellan rad 3 och rad 4 över alla kolumner.
        let h = 8usize;
        let b = 12usize;
        let mut z = vec![0.0f64; h * b];
        for r in 0..h {
            for c in 0..b {
                if r <= 3 {
                    z[r * b + c] = 10.0 + 0.02 * ((3 - r) as f64) + 0.005 * ((b - 1 - c) as f64);
                } else {
                    z[r * b + c] = 9.5 - 0.02 * ((r - 4) as f64);
                }
            }
        }
        let aktiv = vec![1u8; h * b];
        let mut barriarer: Vec<(u64, f64)> = (0..b)
            .map(|c| {
                let u = 3 * b + c;
                let v = 4 * b + c;
                (kant_nyckel(u, v), z[u] + 0.12)
            })
            .collect();
        barriarer.sort_unstable_by_key(|&(k, _)| k);

        let res = fyll_och_riktning(h, b, &ZData::F64(z), &aktiv, &barriarer).expect("fyll kantsten");
        for c in 1..(b - 1) {
            let u = 3 * b + c;
            let mal = res.flow_to[u] as usize;
            let mal_r = mal / b;
            let mal_c = mal % b;
            assert_eq!(mal_r, 3, "kol {c} läckte över kantsten till rad {mal_r}");
            assert_eq!(mal_c, c + 1, "kol {c} borde rinna österut längs rännstenen");
        }
    }

    #[test]
    fn sankt_infart_slapper_ut_flode_exakt_genom_oppningen() {
        // Sluten gata (r=2..=4, c=1..=10, höga sidokanter c=0 & c=11 vid 10.5) mot lägre gård (r>=5).
        // Kantsten 10.12 mellan r=4 och r=5 överallt utom infart vid c=5..=6 (10.02).
        let h = 8usize;
        let b = 12usize;
        let mut z = vec![10.5f64; h * b];
        for r in 2..=4 {
            for c in 1..=(b - 2) {
                z[r * b + c] = 10.0 + 0.01 * ((4 - r) as f64);
            }
        }
        for r in 5..h {
            for c in 0..b {
                z[r * b + c] = 9.6 - 0.02 * ((r - 5) as f64);
            }
        }
        let aktiv = vec![1u8; h * b];
        let mut barriarer: Vec<(u64, f64)> = (0..b)
            .map(|c| {
                let u = 4 * b + c;
                let v = 5 * b + c;
                let hojd_b = if c == 5 || c == 6 { 10.02 } else { 10.12 };
                (kant_nyckel(u, v), hojd_b)
            })
            .collect();
        barriarer.sort_unstable_by_key(|&(k, _)| k);

        let res = fyll_och_riktning(h, b, &ZData::F64(z), &aktiv, &barriarer).expect("fyll infart");
        // Följ varje inre rännstenscell tills den passerar till rad 5: den MÅSTE passera via c=5 eller c=6!
        for start_c in 1..(b - 1) {
            let mut curr = 4 * b + start_c;
            let mut korsade_c: Option<usize> = None;
            for _ in 0..30 {
                let nxt = res.flow_to[curr];
                assert!(nxt >= 0, "start_c={start_c} fastnade vid {curr}");
                let nr = (nxt as usize) / b;
                let nc = (nxt as usize) % b;
                if nr == 5 {
                    korsade_c = Some(nc);
                    break;
                }
                curr = nxt as usize;
            }
            let kc = korsade_c.expect("borde nå rad 5 via infarten");
            assert!(
                kc == 5 || kc == 6,
                "start_c={start_c} bröt ut vid kol {kc} utanför infarten (5..=6)"
            );
        }
    }

    #[test]
    fn diagonal_45_graders_kantsten_lacker_aldrig_diagonalt() {
        // 12x12: övre högra halvan (c > r) är gata som sluttar mot sydost längs diagonalen c = r + 1.
        // Nedre vänstra halvan (c <= r) är lägre gård (z = 9.0).
        // Spärra alla 4-grannekanter mellan {c > r} och {c <= r} vid 10.5.
        let h = 12usize;
        let b = 12usize;
        let mut z = vec![9.0f64; h * b];
        for r in 0..h {
            for c in 0..b {
                if c > r {
                    z[r * b + c] = 10.0 + 0.05 * ((c - r) as f64) + 0.01 * ((h - 1 - r) as f64);
                } else {
                    z[r * b + c] = 9.0 - 0.01 * ((r - c) as f64);
                }
            }
        }
        let aktiv = vec![1u8; h * b];
        let mut barriarer: Vec<(u64, f64)> = Vec::new();
        for r in 0..h {
            for c in 0..b {
                let u = r * b + c;
                if c + 1 < b && (c > r) != (c + 1 > r) {
                    barriarer.push((kant_nyckel(u, u + 1), 10.5));
                }
                if r + 1 < h && (c > r) != (c > r + 1) {
                    barriarer.push((kant_nyckel(u, u + b), 10.5));
                }
            }
        }
        barriarer.sort_unstable_by_key(|&(k, _)| k);

        let res = fyll_och_riktning(h, b, &ZData::F64(z), &aktiv, &barriarer).expect("fyll 45 deg");
        // Ingen inre cell på gatusidan (c > r) får rinna in på gårdssidan (mc <= mr)
        for r in 1..(h - 1) {
            for c in 1..(b - 1) {
                if c > r {
                    let mal = res.flow_to[r * b + c];
                    assert!(mal >= 0);
                    let mr = (mal as usize) / b;
                    let mc = (mal as usize) % b;
                    assert!(
                        mc > mr,
                        "cell ({r},{c}) läckte diagonalt/ortogonalt över 45°-kantsten till ({mr},{mc})"
                    );
                }
            }
        }
    }
}
