/*
 * Rena delar av vattenmotorn (typer, bindningar, uniformpackning, arbetsgrupper) utan
 * WGSL-importen, så att de går att testa med `node --test`. Se `vattenMotor.ts`.
 */
export interface VattenSteg {
  antal: number;
  regn_m: number;
}

export interface VattenGpuIndata {
  nx: number;
  ny: number;
  dx: number;
  dy: number;
  dt: number;
  theta: number;
  min_djup: number;
  z_bed: Float32Array;
  manning: Float32Array;
  aktiv: Uint32Array;
  steg: readonly VattenSteg[];
}

export interface VattenGpuResultat {
  max_djup_m: Float32Array;
  slutdjup_m: Float32Array;
  kortid_ms: number;
  gpu_buffrar_mb: number;
  berakningsplats: "klient_webgpu_f32";
}

/** Bindningar per entry point (binding-numren i vatten_2d.wgsl). */
export const BINDNINGAR = {
  uppdatera_qx: [0, 1, 2, 3, 4, 5],
  uppdatera_qy: [0, 1, 2, 3, 4, 6],
  begransa_och_volym: [0, 3, 4, 5, 6, 7],
} as const;
export type VattenEntry = keyof typeof BINDNINGAR;
export const ENTRIES: readonly VattenEntry[] = ["uppdatera_qx", "uppdatera_qy", "begransa_och_volym"];

/** Den 32 byte stora uniform-bufferten som matchar `struct VattenParams` i vatten_2d.wgsl. */
export function packaVattenParams(ind: VattenGpuIndata, regn_m: number): ArrayBuffer {
  const buf = new ArrayBuffer(32);
  const u32 = new Uint32Array(buf, 0, 2);
  const f32 = new Float32Array(buf, 8, 6);
  u32[0] = ind.nx >>> 0;
  u32[1] = ind.ny >>> 0;
  f32[0] = ind.dx;
  f32[1] = ind.dy;
  f32[2] = ind.dt;
  f32[3] = ind.theta;
  f32[4] = ind.min_djup;
  f32[5] = regn_m;
  return buf;
}

/** Antal arbetsgrupper (8 x 8) per entry point för en nx x ny-yta. */
export function arbetsgrupper(entry: VattenEntry, nx: number, ny: number): [number, number] {
  const bredd = entry === "uppdatera_qx" ? nx - 1 : nx;
  const hojd = entry === "uppdatera_qy" ? ny - 1 : ny;
  return [Math.ceil(bredd / 8), Math.ceil(hojd / 8)];
}

export function kontrolleraVattenIndata(ind: VattenGpuIndata): void {
  const n = ind.nx * ind.ny;
  if (ind.nx < 2 || ind.ny < 2) throw new Error("Vattenfältet måste vara minst 2 x 2 celler.");
  for (const [namn, a] of [["z_bed", ind.z_bed], ["manning", ind.manning], ["aktiv", ind.aktiv]] as const) {
    if (a.length !== n) throw new Error(`${namn} har längd ${a.length}, förväntade ${n}.`);
  }
}

