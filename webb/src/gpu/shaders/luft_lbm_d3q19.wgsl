// Floden – WebGPU compute shader för 3D-LBM (D3Q19, fuserad pull-stream + BGK/Smagorinsky).
// Speglar `src/luft/lbm.py`:
// - Lagrar f_bar = f_i - w_i i f32 (hög precision vid låga Machtal)
// - Exakt implicit kvadratisk vegetationsdämpning: u = 2*u* / (1 + sqrt(1 + 2*alfa*|u*|))
// - Halvvägs bounce-back på fasta byggnadsceller och mark (k = 0)
//
// Prestanda: ~300–1000 MLUPS på laptop-GPU (Mac M2/M3 m.fl.) -> 2–8 s i webbläsaren.

struct LbmParams {
    nx: u32,
    ny: u32,
    nz: u32,
    _pad0: u32,
    tau0: f32,
    cs_smag2: f32,   // (C_s * Delta)^2
    cd_dx: f32,      // C_d * dx (för vegetation)
    _pad1: f32,
};

@group(0) @binding(0) var<uniform> p: LbmParams;
@group(0) @binding(1) var<storage, read> fast: array<u32>;         // (nx*ny*nz), 1 = byggnad
@group(0) @binding(2) var<storage, read> lad: array<f32>;          // (nx*ny*nz), bladytetäthet
@group(0) @binding(3) var<storage, read> f_in: array<f32>;         // (19 * nx*ny*nz)
@group(0) @binding(4) var<storage, read_write> f_out: array<f32>;  // (19 * nx*ny*nz)
@group(0) @binding(5) var<storage, read_write> u_ut: array<vec4<f32>>; // (ux, uy, uz, nu_t)

// Exakt samma ordning som C_D3Q19 och W_D3Q19 i `src/luft/lbm.py`
const CX = array<i32, 19>(0, 1, -1, 0, 0, 0, 0, 1, -1, 1, -1, 1, -1, 1, -1, 0, 0, 0, 0);
const CY = array<i32, 19>(0, 0, 0, 1, -1, 0, 0, 1, -1, -1, 1, 0, 0, 0, 0, 1, -1, 1, -1);
const CZ = array<i32, 19>(0, 0, 0, 0, 0, 1, -1, 0, 0, 0, 0, 1, -1, -1, 1, 1, -1, -1, 1);
const OPP = array<u32, 19>(0u, 2u, 1u, 4u, 3u, 6u, 5u, 8u, 7u, 10u, 9u, 12u, 11u, 14u, 13u, 16u, 15u, 18u, 17u);
const W = array<f32, 19>(
    0.33333334,
    0.055555556, 0.055555556, 0.055555556, 0.055555556, 0.055555556, 0.055555556,
    0.027777778, 0.027777778, 0.027777778, 0.027777778,
    0.027777778, 0.027777778, 0.027777778, 0.027777778,
    0.027777778, 0.027777778, 0.027777778, 0.027777778
);

fn cell_idx(x: u32, y: u32, z: u32) -> u32 {
    return (x * p.ny + y) * p.nz + z;
}

@compute @workgroup_size(4, 4, 4)
fn steg_pull_kollidera(@builtin(global_invocation_id) gid: vec3<u32>) {
    if (gid.x >= p.nx || gid.y >= p.ny || gid.z >= p.nz) {
        return;
    }
    let n_tot = p.nx * p.ny * p.nz;
    let idx = cell_idx(gid.x, gid.y, gid.z);
    if (fast[idx] != 0u) {
        u_ut[idx] = vec4<f32>(0.0, 0.0, 0.0, 0.0);
        return;
    }

    // 1. Fuserad pull-stream med periodiskt y och halvvägs bounce-back på vägg/mark
    var f_loc: array<f32, 19>;
    var drho: f32 = 0.0;
    var jx: f32 = 0.0;
    var jy: f32 = 0.0;
    var jz: f32 = 0.0;

    for (var q: u32 = 0u; q < 19u; q = q + 1u) {
        let sx = i32(gid.x) - CX[q];
        let sy_raw = i32(gid.y) - CY[q];
        let sy = (sy_raw + i32(p.ny)) % i32(p.ny);
        let sz = i32(gid.z) - CZ[q];

        var val: f32;
        if (sx < 0 || sx >= i32(p.nx) || sz < 0 || sz >= i32(p.nz)) {
            // Domänrand (mark k=0 eller tak): halvvägs bounce-back som skydd
            val = f_in[OPP[q] * n_tot + idx];
        } else {
            let s_idx = cell_idx(u32(sx), u32(sy), u32(sz));
            if (fast[s_idx] != 0u) {
                val = f_in[OPP[q] * n_tot + idx];
            } else {
                val = f_in[q * n_tot + s_idx];
            }
        }
        f_loc[q] = val;
        drho = drho + val;
        jx = jx + f32(CX[q]) * val;
        jy = jy + f32(CY[q]) * val;
        jz = jz + f32(CZ[q]) * val;
    }

    let rho = 1.0 + drho;
    let inv_rho = 1.0 / max(rho, 1e-4);
    var ux = jx * inv_rho;
    var uy = jy * inv_rho;
    var uz = jz * inv_rho;

    // 2. Exakt implicit kvadratisk vegetationsdämpning (matchar `lbm.py:55-56`)
    let alfa = p.cd_dx * lad[idx];
    var fx: f32 = 0.0;
    var fy: f32 = 0.0;
    var fz: f32 = 0.0;
    if (alfa > 0.0) {
        let u_mag = sqrt(ux * ux + uy * uy + uz * uz);
        let skala = 2.0 / (1.0 + sqrt(1.0 + 2.0 * alfa * u_mag));
        let ux_ny = ux * skala;
        let uy_ny = uy * skala;
        let uz_ny = uz * skala;
        fx = 2.0 * rho * (ux_ny - ux);
        fy = 2.0 * rho * (uy_ny - uy);
        fz = 2.0 * rho * (uz_ny - uz);
        ux = ux_ny;
        uy = uy_ny;
        uz = uz_ny;
    }

    // 3. Smagorinsky-LES lokalt ur icke-jämviktsmomenten Π^neq
    let u2 = ux * ux + uy * uy + uz * uz;
    var pxx: f32 = 0.0; var pyy: f32 = 0.0; var pzz: f32 = 0.0;
    var pxy: f32 = 0.0; var pxz: f32 = 0.0; var pyz: f32 = 0.0;
    for (var q: u32 = 0u; q < 19u; q = q + 1u) {
        let cx = f32(CX[q]); let cy = f32(CY[q]); let cz = f32(CZ[q]);
        let cu = cx * ux + cy * uy + cz * uz;
        let feq_bar = W[q] * (drho + rho * (3.0 * cu + 4.5 * cu * cu - 1.5 * u2));
        let fneq = f_loc[q] - feq_bar;
        pxx = pxx + cx * cx * fneq;
        pyy = pyy + cy * cy * fneq;
        pzz = pzz + cz * cz * fneq;
        pxy = pxy + cx * cy * fneq;
        pxz = pxz + cx * cz * fneq;
        pyz = pyz + cy * cz * fneq;
    }
    let pi_norm = sqrt(pxx * pxx + pyy * pyy + pzz * pzz + 2.0 * (pxy * pxy + pxz * pxz + pyz * pyz));
    // Hou (1996) / Malaspinas & Sagaut (2012), matchar `lbm.py:29`
    let tau = 0.5 * (p.tau0 + sqrt(p.tau0 * p.tau0 + 25.455844 * p.cs_smag2 * pi_norm * inv_rho));
    let omega = 1.0 / tau;
    let guo_fak = 1.0 - 0.5 * omega;

    // 4. Kollision + Guo-kraftterm, skriv till f_out
    for (var q: u32 = 0u; q < 19u; q = q + 1u) {
        let cx = f32(CX[q]); let cy = f32(CY[q]); let cz = f32(CZ[q]);
        let cu = cx * ux + cy * uy + cz * uz;
        let feq_bar = W[q] * (drho + rho * (3.0 * cu + 4.5 * cu * cu - 1.5 * u2));
        let cf = cx * fx + cy * fy + cz * fz;
        let uf = ux * fx + uy * fy + uz * fz;
        let si = W[q] * guo_fak * (3.0 * (cf - uf) + 9.0 * cu * cf);
        f_out[q * n_tot + idx] = f_loc[q] + omega * (feq_bar - f_loc[q]) + si;
    }

    let nu_t = max(0.0, (tau - p.tau0) / 3.0);
    u_ut[idx] = vec4<f32>(ux, uy, uz, nu_t);
}
