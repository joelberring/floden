// Floden – WebGPU compute shader för dagsljus: horisontprofil, SVF och soltimmar.
// Speglar `src/dagsljus/horisont.py` (Amanatides–Woo i f32, en tråd per cell).
//
// Minnesbudget: 512×512 @ 1 m = 1 MB för z_m + 1 MB för utdata (körs på alla laptop-GPU:er).

struct Params {
    nx: u32,
    ny: u32,
    n_riktningar: u32,
    n_solsteg: u32,
    cell_m: f32,
    rmax_celler: f32,
    steg_h: f32,
    _pad: f32,
};

struct SolPunkt {
    hojd_rad: f32,
    azimut_rad: f32,
};

@group(0) @binding(0) var<uniform> params: Params;
@group(0) @binding(1) var<storage, read> z_m: array<f32>;             // (ny * nx), rad 0 = norr
@group(0) @binding(2) var<storage, read> solbanor: array<SolPunkt>;   // (n_solsteg)
@group(0) @binding(3) var<storage, read_write> svf_ut: array<f32>;    // (ny * nx)
@group(0) @binding(4) var<storage, read_write> soltimmar_ut: array<f32>; // (ny * nx)

const PI: f32 = 3.14159265358979323846;
const TWO_PI: f32 = 6.28318530717958647692;
const AXEL_NOLL: f32 = 1e-6;
const T_MIN: f32 = 1e-5;

// Horisontvinkel (rad) i en azimutriktning phi (0 = norr, medurs) via Amanatides–Woo.
fn horisont_riktning(r0: i32, c0: i32, z0: f32, phi: f32) -> f32 {
    let nx_i = i32(params.nx);
    let ny_i = i32(params.ny);
    var ex = sin(phi);
    var ey = -cos(phi); // rad 0 = norr -> norr är -y i radindex
    if (abs(ex) < AXEL_NOLL) { ex = 0.0; }
    if (abs(ey) < AXEL_NOLL) { ey = 0.0; }

    let aex = abs(ex);
    let aey = abs(ey);
    let sx: i32 = select(-1, 1, ex >= 0.0);
    let sy: i32 = select(-1, 1, ey >= 0.0);
    let tdx: f32 = select(1e30, 1.0 / aex, ex != 0.0);
    let tdy: f32 = select(1e30, 1.0 / aey, ey != 0.0);

    var ix: i32 = 0;
    var iy: i32 = 0;
    var cj: i32 = c0;
    var ci: i32 = r0;
    var max_lutning: f32 = 0.0;

    // Heltalsräknare (ix, iy) + korsprodukt eliminerar kumulativ f32-drift och ULP-fel i GPU sin/cos.
    let max_steg = u32(2.0 * params.rmax_celler) + 4u;
    for (var steg: u32 = 0u; steg < max_steg; steg = steg + 1u) {
        let fx = f32(ix) + 0.5;
        let fy = f32(iy) + 0.5;
        let tmx: f32 = select(1e30, fx * tdx, ex != 0.0);
        let tmy: f32 = select(1e30, fy * tdy, ey != 0.0);
        let t = min(tmx, tmy);
        if (t > params.rmax_celler) {
            break;
        }
        let lhs: f32 = select(1e30, fx * aey, ex != 0.0);
        let rhs: f32 = select(1e30, fy * aex, ey != 0.0);
        let tie_tol = 1e-5 * max(lhs, rhs);
        let tie = (ex != 0.0) && (ey != 0.0) && (abs(lhs - rhs) <= tie_tol);
        let steg_x = (ex != 0.0) && (tie || lhs < rhs);
        let steg_y = (ey != 0.0) && (tie || rhs < lhs);
        if (steg_x) {
            cj = cj + sx;
            ix = ix + 1;
        }
        if (steg_y) {
            ci = ci + sy;
            iy = iy + 1;
        }
        if (cj < 0 || cj >= nx_i || ci < 0 || ci >= ny_i) {
            break;
        }
        let z_hinder = z_m[u32(ci) * params.nx + u32(cj)];
        let dz = z_hinder - z0;
        if (dz > 0.0) {
            let lutning = dz / (max(t, T_MIN) * params.cell_m);
            max_lutning = max(max_lutning, lutning);
        }
    }
    return atan(max_lutning);
}

@compute @workgroup_size(8, 8, 1)
fn berakna_svf_och_soltimmar(@builtin(global_invocation_id) gid: vec3<u32>) {
    if (gid.x >= params.nx || gid.y >= params.ny) {
        return;
    }
    let idx = gid.y * params.nx + gid.x;
    let z0 = z_m[idx];
    let r0 = i32(gid.y);
    let c0 = i32(gid.x);

    // 1. SVF = (1/K) * sum(cos^2(h_k))
    var svf_sum: f32 = 0.0;
    let k_f = f32(params.n_riktningar);
    for (var k: u32 = 0u; k < params.n_riktningar; k = k + 1u) {
        let phi = TWO_PI * f32(k) / k_f;
        let h = horisont_riktning(r0, c0, z0, phi);
        let ch = cos(h);
        svf_sum = svf_sum + ch * ch;
    }
    svf_ut[idx] = svf_sum / k_f;

    // 2. Direkta soltimmar över enbart solpositioner över horisonten (hoppar över norr-sektorn)
    var sol_antal: u32 = 0u;
    for (var s: u32 = 0u; s < params.n_solsteg; s = s + 1u) {
        let sp = solbanor[s];
        if (sp.hojd_rad > 0.0) {
            let h_sol = horisont_riktning(r0, c0, z0, sp.azimut_rad);
            if (sp.hojd_rad > h_sol) {
                sol_antal = sol_antal + 1u;
            }
        }
    }
    soltimmar_ut[idx] = f32(sol_antal) * params.steg_h;
}
