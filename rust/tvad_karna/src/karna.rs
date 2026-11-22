//! Rena funktioner per kant och per cell.
//!
//! Varje funktion läser bara sina grannar och skriver ett enda värde, utan allokering och utan
//! delat tillstånd. Det gör att de kan köras parallellt i valfri ordning (rayon nu) och lyftas
//! rakt till en compute shader (wgpu/WGSL) senare: en arbetstråd per kant eller cell.
//!
//! Räkneordningen i varje uttryck följer Python-lösaren (`tvad.lokal_troghet`) operation för
//! operation, så att resultaten blir bitidentiska.

pub const G: f64 = 9.81;
pub const FROUDE_MAX: f64 = 2.0;

/// NumPys `np.maximum(a, b)`: `a` om `a >= b` eller `a` är NaN, annars `b`.
#[inline]
pub fn np_max(a: f64, b: f64) -> f64 {
    if a >= b || a.is_nan() {
        a
    } else {
        b
    }
}

/// NumPys `np.minimum(a, b)`.
#[inline]
pub fn np_min(a: f64, b: f64) -> f64 {
    if a <= b || a.is_nan() {
        a
    } else {
        b
    }
}

/// de Almeida (2012) / SFINCS riktad theta-viktning för en kant.
/// `q_fore`/`q_efter` = grannkanternas flöde, eller kantens eget när grannen saknas/är ogiltig.
#[inline]
pub fn utjamnat_flode(q: f64, q_fore: f64, q_efter: f64, theta: f64) -> f64 {
    let w = 0.5 * (1.0 - theta);
    theta * q + w * (q_fore + q_efter)
}

/// Bates (2010) / de Almeida (2012): nytt enhetsflöde (m²/s) över en våt kant.
/// Returnerar 0 när kanten är torr (`h_flode <= min_djup`, eller NaN).
#[inline]
#[allow(clippy::neg_cmp_op_on_partial_ord)] // NaN ska räknas som torrt, som i NumPy-masken
pub fn bates_flode(
    _q_nu: f64,
    q_bar: f64,
    h_flode: f64,
    lutning: f64,
    manning_n: f64,
    dt: f64,
    min_djup: f64,
) -> f64 {
    if !(h_flode > min_djup) {
        return 0.0;
    }
    let h_73 = (h_flode * h_flode) * h_flode.cbrt();
    let q_rhs = q_bar - G * h_flode * dt * lutning;
    let c_frikt = G * dt * (manning_n * manning_n) / h_73;
    let q_ny = (2.0 * q_rhs) / (1.0 + (1.0 + 4.0 * c_frikt * q_rhs.abs()).sqrt());
    let q_max = FROUDE_MAX * h_flode * (G * h_flode).sqrt();
    // np.clip(x, lo, hi) = min(max(x, lo), hi)
    np_min(np_max(q_ny, -q_max), q_max)
}

/// Flödesdjup över en kant: max(0, max(zs_a, zs_b) - max(z_a, z_b)).
#[inline]
pub fn kant_djup(zs_a: f64, zs_b: f64, z_kant: f64) -> f64 {
    np_max(0.0, np_max(zs_a, zs_b) - z_kant)
}

/// Utåtriktat enhetsflöde över en öppen randkant (Manning, begränsat till kritiskt flöde).
#[inline]
#[allow(clippy::neg_cmp_op_on_partial_ord)]
pub fn rand_flode(h: f64, manning_n: f64, oppen: bool, lutning: f64, bredd_andel: f64, min_djup: f64) -> f64 {
    if !(oppen && h > min_djup) {
        return 0.0;
    }
    let q_man = (1.0 / manning_n) * (h * (h * h).cbrt()) * lutning.sqrt();
    let q_krit = h * (G * h).sqrt();
    bredd_andel * np_min(q_man, q_krit)
}

/// NumPys parvisa summa (`pairwise_sum`, 8-vägs utrullning, block om 128) för en följd.
fn parvis(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = 0.0;
        for v in a {
            res += *v;
        }
        res
    } else if n <= 128 {
        let mut r = [a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]];
        let mut i = 8;
        while i < n - (n % 8) {
            for j in 0..8 {
                r[j] += a[i + j];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += a[i];
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        parvis(&a[..n2]) + parvis(&a[n2..])
    }
}

/// `np.sum` för en sammanhängande float64-vektor: parvis summa i buffertblock om 8192.
pub fn np_sum(a: &[f64]) -> f64 {
    let mut tot = 0.0;
    for block in a.chunks(8192) {
        tot += parvis(block);
    }
    tot
}

/// `np.sum` av `n` kopior av samma värde (utan att bygga vektorn): samma struktur som `parvis`.
fn parvis_konst(n: usize, v: f64) -> f64 {
    if n < 8 {
        let mut res = 0.0;
        for _ in 0..n {
            res += v;
        }
        res
    } else if n <= 128 {
        let varv = (n - n % 8) / 8;
        let mut r = v;
        for _ in 1..varv {
            r += v;
        }
        let mut res = ((r + r) + (r + r)) + ((r + r) + (r + r));
        for _ in 0..(n % 8) {
            res += v;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        parvis_konst(n2, v) + parvis_konst(n - n2, v)
    }
}

pub fn np_sum_konst(n: usize, v: f64) -> f64 {
    let mut tot = 0.0;
    let mut kvar = n;
    while kvar > 0 {
        let b = kvar.min(8192);
        tot += parvis_konst(b, v);
        kvar -= b;
    }
    tot
}

#[cfg(test)]
mod tester {
    use super::*;

    #[test]
    fn konstsumma_som_vektor() {
        for n in [0usize, 1, 7, 8, 9, 63, 128, 129, 1000, 8192, 8193, 20000] {
            let v = 0.0625 * 0.3;
            let vek = vec![v; n];
            assert_eq!(np_sum(&vek).to_bits(), np_sum_konst(n, v).to_bits(), "n={n}");
        }
    }
}
