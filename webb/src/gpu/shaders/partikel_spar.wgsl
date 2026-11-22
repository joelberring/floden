// Floden – WebGPU compute shader för 3D vind- och partikelspår (zero-copy ur LBM/snabbvind).
// Läser direkt från `u_ut` (ux, uy, uz, nu_t) och `lad` på GPU utan CPU-kopiering:
// - Advekterar partikelposition (x, y, z) med vindfältet + Stokes-sedimentation (-w_s)
// - Dämpar partikelns vikt (w) i vegetation via exp(-v_d_veg * lad * dt)
// - Återföder partiklar som lämnar domänen, träffar mark/byggnad eller dör ut
//
// 50 000 partiklar = ~1,6 MB VRAM, 60 fps i 1 enda InstancedMesh/storage draw call.

struct SparParams {
    nx: u32,
    ny: u32,
    nz: u32,
    n_partiklar: u32,
    dx_m: f32,
    dt_s: f32,
    w_s_m_s: f32,        // Stokes–Cunningham sedimentationshastighet (m/s)
    v_d_veg_m_s: f32,    // Vegetationsdeposition (m/s)
    max_alder_s: f32,
    _pad0: f32,
    _pad1: f32,
    _pad2: f32,
};

struct Partikel {
    pos_alder: vec4<f32>,   // (x_cell, y_cell, z_cell, alder_s)
    vikt_kalla: vec4<f32>,  // (vikt_0_1, kalla_x, kalla_y, kalla_z)
};

@group(0) @binding(0) var<uniform> p: SparParams;
@group(0) @binding(1) var<storage, read> fast: array<u32>;         // (nx*ny*nz)
@group(0) @binding(2) var<storage, read> lad: array<f32>;          // (nx*ny*nz)
@group(0) @binding(3) var<storage, read> u_falt: array<vec4<f32>>; // (ux, uy, uz, nu_t)
@group(0) @binding(4) var<storage, read_write> partiklar: array<Partikel>;

fn cell_idx(x: u32, y: u32, z: u32) -> u32 {
    return (x * p.ny + y) * p.nz + z;
}

@compute @workgroup_size(64, 1, 1)
fn advektera_partiklar(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = gid.x;
    if (i >= p.n_partiklar) {
        return;
    }
    var pt = partiklar[i];
    var x = pt.pos_alder.x;
    var y = pt.pos_alder.y;
    var z = pt.pos_alder.z;
    var alder = pt.pos_alder.w;
    var vikt = pt.vikt_kalla.x;

    let ix = i32(floor(x));
    let iy = i32(floor(y));
    let iz = i32(floor(z));
    let utanfor = ix < 0 || ix >= i32(p.nx) || iy < 0 || iy >= i32(p.ny) || iz < 0 || iz >= i32(p.nz);

    var aterfod = utanfor || alder >= p.max_alder_s || vikt < 0.02;
    if (!aterfod) {
        let idx = cell_idx(u32(ix), u32(iy), u32(iz));
        if (fast[idx] != 0u) {
            aterfod = true;
        } else {
            let u = u_falt[idx];
            let inv_dx = 1.0 / max(p.dx_m, 1e-4);
            x = x + u.x * p.dt_s * inv_dx;
            y = y + u.y * p.dt_s * inv_dx;
            z = z + (u.z - p.w_s_m_s) * p.dt_s * inv_dx;
            alder = alder + p.dt_s;

            // Vegetationsfiltrering i häckar och trädkronor
            let lad_lok = lad[idx];
            if (lad_lok > 0.0 && p.v_d_veg_m_s > 0.0) {
                vikt = vikt * exp(-p.v_d_veg_m_s * lad_lok * p.dt_s);
            }
            if (z < 0.0) {
                aterfod = true;
            }
        }
    }

    if (aterfod) {
        pt.pos_alder = vec4<f32>(pt.vikt_kalla.y, pt.vikt_kalla.z, pt.vikt_kalla.w, 0.0);
        pt.vikt_kalla.x = 1.0;
    } else {
        pt.pos_alder = vec4<f32>(x, y, z, alder);
        pt.vikt_kalla.x = vikt;
    }
    partiklar[i] = pt;
}
