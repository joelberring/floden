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

/// Prioritetsfyllning (8-grannar) och D8-flödesriktning bitidentiskt med
/// `sankgraf.avrinningsnat.fyll_och_riktning`.
pub fn fyll_och_riktning(
    hojd: usize,
    bredd: usize,
    z: &ZData,
    aktiv: &[u8],
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

    let mut ordning: Vec<i32> = Vec::with_capacity(antal_aktiva);
    let mut lagsta: Vec<u32> = Vec::new();
    let mut lagsta_byggd = false;
    let mut pek = 0usize;

    let h_isize = hojd as isize;
    let b_isize = bredd as isize;

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

    drop(klar);
    drop(lagsta);
    drop(ko);

    // 2. D8: brantast nedåt på den fyllda ytan; platta celler följer fyllningens väg.
    // Randceller är alltid utlopp (-1) och inaktiva alltid -2.
    let mut flow_to = vec![-2i32; n];
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
            for &(dr, dc, avst) in &GRANNAR8 {
                let v = ((r_i + dr) as usize) * bredd + ((c_i + dc) as usize);
                if aktiv[v] != 0 {
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
        let res = fyll_och_riktning(hojd, bredd, &ZData::F64(z), &aktiv).expect("fyll");
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
        let res = fyll_och_riktning(h, b, &ZData::F64(z), &aktiv).expect("fyll slutet");
        let antal_aktiva = aktiv.iter().filter(|&&x| x != 0).count();
        assert_eq!(res.ordning.len(), antal_aktiva);
        // Lägsta cellen i det slutna området är utlopp (-1)
        assert_eq!(res.flow_to[3 * b + 3], -1);
        // Granncell i gården rinner till (3, 3)
        assert_eq!(res.flow_to[2 * b + 3], (3 * b + 3) as i32);
        // Byggnadscell är inaktiv (-2)
        assert_eq!(res.flow_to[b + 1], -2);
    }
}
