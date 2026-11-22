// Floden – WebGPU compute shaders för 2D lokal tröghet (Bates 2010 / de Almeida 2012).
// Speglar `rust/tvad_karna/src/karna.rs` & `src/tvad/lokal_troghet.py`:
// - Punkt-implicit kvadratisk Manning-friktion: q = 2*q_rhs / (1 + sqrt(1 + 4*c*|q_rhs|))
// - Exakt h^(7/3) via h² * pow(h, 1/3)
// - Massbevarande och positivitetsbevarande flödesbegränsare
//
// Avsett för snabbt skissläge i webbläsaren (f32); formell rapport verifieras i f64.

struct VattenParams {
    nx: u32,
    ny: u32,
    dx: f32,
    dy: f32,
    dt: f32,
    theta: f32,
    min_djup: f32,
    regn_m: f32,
};

@group(0) @binding(0) var<uniform> p: VattenParams;
@group(0) @binding(1) var<storage, read> z_bed: array<f32>;        // (ny * nx)
@group(0) @binding(2) var<storage, read> manning: array<f32>;      // (ny * nx)
@group(0) @binding(3) var<storage, read> aktiv: array<u32>;        // (ny * nx), 0 eller 1
@group(0) @binding(4) var<storage, read_write> volym: array<f32>;  // (ny * nx)
@group(0) @binding(5) var<storage, read_write> qx: array<f32>;     // (ny * (nx - 1))
@group(0) @binding(6) var<storage, read_write> qy: array<f32>;     // ((ny - 1) * nx)
@group(0) @binding(7) var<storage, read_write> max_djup: array<f32>; // (ny * nx)

const G: f32 = 9.81;
const FROUDE_MAX: f32 = 2.0;

fn bates_punkt_implicit(q_bar: f32, h_flode: f32, lutning: f32, n_manning: f32) -> f32 {
    if (!(h_flode > p.min_djup)) {
        return 0.0;
    }
    let h_73 = (h_flode * h_flode) * pow(h_flode, 0.33333334);
    let q_rhs = q_bar - G * h_flode * p.dt * lutning;
    let c_frikt = G * p.dt * (n_manning * n_manning) / h_73;
    let q_ny = (2.0 * q_rhs) / (1.0 + sqrt(1.0 + 4.0 * c_frikt * abs(q_rhs)));
    let q_max = FROUDE_MAX * h_flode * sqrt(G * h_flode);
    return clamp(q_ny, -q_max, q_max);
}

@compute @workgroup_size(8, 8, 1)
fn uppdatera_qx(@builtin(global_invocation_id) gid: vec3<u32>) {
    let wu = p.nx - 1u;
    if (gid.x >= wu || gid.y >= p.ny) {
        return;
    }
    let r = gid.y;
    let c = gid.x;
    let a = r * p.nx + c;
    let b = a + 1u;
    let e = r * wu + c;
    if (aktiv[a] == 0u || aktiv[b] == 0u) {
        qx[e] = 0.0;
        return;
    }
    let area = p.dx * p.dy;
    let zs_a = z_bed[a] + volym[a] / area;
    let zs_b = z_bed[b] + volym[b] / area;
    let h_u = max(0.0, max(zs_a, zs_b) - max(z_bed[a], z_bed[b]));
    let sx = (zs_b - zs_a) / p.dx;
    let n_u = 0.5 * (manning[a] + manning[b]);
    let q_nu = qx[e];
    qx[e] = bates_punkt_implicit(q_nu, h_u, sx, n_u);
}

@compute @workgroup_size(8, 8, 1)
fn uppdatera_qy(@builtin(global_invocation_id) gid: vec3<u32>) {
    let hv = p.ny - 1u;
    if (gid.x >= p.nx || gid.y >= hv) {
        return;
    }
    let r = gid.y;
    let c = gid.x;
    let a = r * p.nx + c;
    let b = a + p.nx;
    let e = r * p.nx + c;
    if (aktiv[a] == 0u || aktiv[b] == 0u) {
        qy[e] = 0.0;
        return;
    }
    let area = p.dx * p.dy;
    let zs_a = z_bed[a] + volym[a] / area;
    let zs_b = z_bed[b] + volym[b] / area;
    let h_v = max(0.0, max(zs_a, zs_b) - max(z_bed[a], z_bed[b]));
    let sy = (zs_b - zs_a) / p.dy;
    let n_v = 0.5 * (manning[a] + manning[b]);
    let q_nu = qy[e];
    qy[e] = bates_punkt_implicit(q_nu, h_v, sy, n_v);
}

@compute @workgroup_size(8, 8, 1)
fn begransa_och_volym(@builtin(global_invocation_id) gid: vec3<u32>) {
    if (gid.x >= p.nx || gid.y >= p.ny) {
        return;
    }
    let r = gid.y;
    let c = gid.x;
    let i = r * p.nx + c;
    if (aktiv[i] == 0u) {
        volym[i] = 0.0;
        return;
    }
    let area = p.dx * p.dy;
    let v_start = volym[i] + area * p.regn_m;
    let wu = p.nx - 1u;
    let dydt = p.dy * p.dt;
    let dxdt = p.dx * p.dt;

    // Nettoflöde ur cell i (skalad lokalt för att bevara v >= 0)
    var dv: f32 = 0.0;
    if (c < wu) { dv = dv - qx[r * wu + c] * dydt; }
    if (c > 0u) { dv = dv + qx[r * wu + (c - 1u)] * dydt; }
    if (r < p.ny - 1u) { dv = dv - qy[r * p.nx + c] * dxdt; }
    if (r > 0u) { dv = dv + qy[(r - 1u) * p.nx + c] * dxdt; }

    let v_ny = max(0.0, v_start + dv);
    volym[i] = v_ny;
    max_djup[i] = max(max_djup[i], v_ny / area);
}
