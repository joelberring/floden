/*
 * Geometri till 3D-vyn ur serverns buffertar. Rena funktioner som ger typade matriser: ingen
 * WebGL behövs, så logiken går att testa i Node. Scenkoordinater: x = öster, y = upp, z = söder.
 */
import { ShapeUtils, Vector2 } from "three";
import { avkvantisera, CELLER, INGET_VARDE, rutHorn, type Rut } from "./rutnat.ts";
import { type Origo } from "./koord.ts";
import type { Behallare } from "./format.ts";
import { delaKant, takFor, takTrianglar, type Takform } from "./tak.ts";

// ---------------------------------------------------------------------------------------------
// Terräng

export interface TerrangRuta {
  rut: Rut;
  x0: number;
  y0: number;
  cell: number;
  /** (CELLER + 1)^2 höjder (RH 2000, m), rad 0 = norr; NaN där terräng saknas. */
  z: Float32Array;
}

export function terrangRutaFran(b: Behallare): TerrangRuta {
  const h = b.huvud as unknown as { lod: number; tx: number; ty: number; z_lo: number; z_hi: number };
  const q = b.delar.z as Uint16Array;
  const z = new Float32Array(q.length);
  for (let i = 0; i < q.length; i++) z[i] = avkvantisera(q[i] as number, h.z_lo, h.z_hi);
  const rut = { lod: h.lod, tx: h.tx, ty: h.ty };
  const { x0, y0, cell } = rutHorn(rut);
  return { rut, x0, y0, cell, z };
}

export interface MaskGeometri {
  position: Float32Array;
  uv: Float32Array;
  index: Uint32Array;
}

/**
 * Nät för en terrängruta: (129 × 129) punkter och två trianglar per cell. Celler vars hörn saknar
 * höjd hoppas över. En kjol (kanten sänkt `kjol` m) döljer sprickor mot grannrutor med annan
 * detaljnivå.
 */
export function terrangNat(t: TerrangRuta, o: Origo, kjol = Math.max(1, t.cell * 0.75)): MaskGeometri {
  const N = CELLER + 1;
  const kant: number[] = [];
  for (let i = 0; i < N; i++) kant.push(i); // norr
  for (let r = 1; r < N; r++) kant.push(r * N + (N - 1)); // öster
  for (let i = N - 2; i >= 0; i--) kant.push((N - 1) * N + i); // söder
  for (let r = N - 2; r >= 1; r--) kant.push(r * N); // väster
  const antalPunkter = N * N + kant.length;
  const position = new Float32Array(antalPunkter * 3);
  const uv = new Float32Array(antalPunkter * 2);
  const punkt = (i: number, r: number, k: number, hojd: number): void => {
    position[k * 3] = t.x0 + i * t.cell - o.e;
    position[k * 3 + 1] = hojd - o.z;
    position[k * 3 + 2] = -(t.y0 + (CELLER - r) * t.cell - o.n);
    uv[k * 2] = i / CELLER;
    uv[k * 2 + 1] = r / CELLER;
  };
  for (let r = 0; r < N; r++) {
    for (let i = 0; i < N; i++) {
      const z = t.z[r * N + i] as number;
      punkt(i, r, r * N + i, Number.isFinite(z) ? z : 0);
    }
  }
  kant.forEach((idx, n) => {
    const r = Math.floor(idx / N);
    const i = idx % N;
    const z = t.z[idx] as number;
    punkt(i, r, N * N + n, (Number.isFinite(z) ? z : 0) - kjol);
  });
  const ind: number[] = [];
  for (let r = 0; r < CELLER; r++) {
    for (let i = 0; i < CELLER; i++) {
      const a = r * N + i;
      const b = a + 1;
      const c = a + N;
      const d = c + 1;
      if (![a, b, c, d].every((k) => Number.isFinite(t.z[k] as number))) continue;
      ind.push(a, c, b, b, c, d);
    }
  }
  for (let n = 0; n < kant.length; n++) {
    const m = (n + 1) % kant.length;
    const a = kant[n] as number;
    const b = kant[m] as number;
    if (!Number.isFinite(t.z[a] as number) || !Number.isFinite(t.z[b] as number)) continue;
    ind.push(a, N * N + n, b, b, N * N + n, N * N + m);
  }
  return { position, uv, index: Uint32Array.from(ind) };
}

/** Höjdfältet över alla inlästa terrängrutor: finaste rutan som täcker punkten, bilinjärt. */
export class Hojdfalt {
  private rutor: TerrangRuta[] = [];

  lagg(t: TerrangRuta): void {
    this.rutor = this.rutor.filter((r) => r.rut.lod !== t.rut.lod || r.rut.tx !== t.rut.tx || r.rut.ty !== t.rut.ty);
    this.rutor.push(t);
    this.rutor.sort((a, b) => a.rut.lod - b.rut.lod);
  }

  tomma(): void {
    this.rutor = [];
  }

  /** Terränghöjd (RH 2000) i (e, n), eller NaN om ingen ruta täcker punkten. */
  hojd(e: number, n: number): number {
    for (const t of this.rutor) {
      const span = t.cell * CELLER;
      if (e < t.x0 || e > t.x0 + span || n < t.y0 || n > t.y0 + span) continue;
      const fx = (e - t.x0) / t.cell;
      const fy = CELLER - (n - t.y0) / t.cell;
      const i0 = Math.min(Math.floor(fx), CELLER - 1);
      const r0 = Math.min(Math.floor(fy), CELLER - 1);
      const tx = fx - i0;
      const ty = fy - r0;
      const N = CELLER + 1;
      const v00 = t.z[r0 * N + i0] as number;
      const v10 = t.z[r0 * N + i0 + 1] as number;
      const v01 = t.z[(r0 + 1) * N + i0] as number;
      const v11 = t.z[(r0 + 1) * N + i0 + 1] as number;
      const v = (v00 * (1 - tx) + v10 * tx) * (1 - ty) + (v01 * (1 - tx) + v11 * tx) * ty;
      if (Number.isFinite(v)) return v;
    }
    return Number.NaN;
  }
}

// ---------------------------------------------------------------------------------------------
// Byggnader

export interface ByggnaderBuf {
  antal: number;
  ids: string[];
  origo: Origo;
  punkter: Float32Array;
  ringstart: Uint32Array;
  byggnad_ring: Uint32Array;
  z_fot: Float32Array;
  z_tak: Float32Array;
  z_tak_fore: Float32Array;
  hojd_m: Float32Array;
  status: Uint8Array;
}

export function byggnaderFran(b: Behallare): ByggnaderBuf {
  const h = b.huvud as unknown as { antal: number; ids: string[]; origo: Origo };
  const d = b.delar;
  return {
    antal: h.antal,
    ids: h.ids,
    origo: h.origo,
    punkter: d.punkter as Float32Array,
    ringstart: d.ringstart as Uint32Array,
    byggnad_ring: d.byggnad_ring as Uint32Array,
    z_fot: d.z_fot as Float32Array,
    z_tak: d.z_tak as Float32Array,
    z_tak_fore: d.z_tak_fore as Float32Array,
    hojd_m: d.hojd_m as Float32Array,
    status: d.status as Uint8Array,
  };
}

/** Ett hus som ska extruderas: ringar i scenens (x, z) och golv- och takhöjd (scenens y). */
export interface Hus {
  ringar: Array<Array<[number, number]>>;
  yFot: number;
  /** Höjden till det högsta på taket (nock). Med takform stiger taket ur en takfot lägre än så. */
  yTak: number;
  /** Takform (R6). Utan värde, eller platt, ritas ett platt tak (som förut). */
  takform?: Takform;
}

export interface HusGeometri {
  position: Float32Array;
  normal: Float32Array;
  index: Uint32Array;
  /** Vilket hus (index i listan) varje triangel hör till (för klick och hovring). */
  triHus: Uint32Array;
  /** Linjesegment (par av punkter) för tak- och fotkanter och hörn. */
  kanter: Float32Array;
}

/** Extruderar husen: väggar (en yta per kant), tak (triangulerat med hål) och linjekanter. */
export function extrudera(hus: Hus[]): HusGeometri {
  const pos: number[] = [];
  const nor: number[] = [];
  const ind: number[] = [];
  const triHus: number[] = [];
  const kanter: number[] = [];
  hus.forEach((h, hi) => {
    const tak = h.takform && h.takform !== "platt" ? takFor(h.takform, h.ringar[0] ?? [], h.yTak - h.yFot) : null;
    if (tak) {
      const fot = h.yTak - tak.resning;
      const topp = (x: number, z: number): number => fot + tak.hojdOverFot(x, z);
      for (const ring of h.ringar) {
        const n = ring.length;
        if (n < 3) continue;
        for (let k = 0; k < n; k++) {
          const a = ring[k] as [number, number];
          const b = ring[(k + 1) % n] as [number, number];
          const l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
          const nx = (b[1] - a[1]) / l;
          const nz = -(b[0] - a[0]) / l;
          const delar = delaKant(a, b, tak);
          for (let i = 0; i + 1 < delar.length; i++) {
            const p = delar[i] as [number, number];
            const q = delar[i + 1] as [number, number];
            const yp = topp(p[0], p[1]);
            const yq = topp(q[0], q[1]);
            const base = pos.length / 3;
            pos.push(p[0], h.yFot, p[1], q[0], h.yFot, q[1], q[0], yq, q[1], p[0], yp, p[1]);
            for (let c = 0; c < 4; c++) nor.push(nx, 0, nz);
            ind.push(base, base + 1, base + 2, base, base + 2, base + 3);
            triHus.push(hi, hi);
            kanter.push(p[0], yp, p[1], q[0], yq, q[1]);
            kanter.push(p[0], h.yFot, p[1], q[0], h.yFot, q[1]);
          }
          kanter.push(a[0], h.yFot, a[1], a[0], topp(a[0], a[1]), a[1]);
        }
      }
      for (const t of takTrianglar(h.ringar, tak)) {
        const [p0, p1, p2] = t as [[number, number, number], [number, number, number], [number, number, number]];
        const ux = p1[0] - p0[0];
        const uy = p1[1] - p0[1];
        const uz = p1[2] - p0[2];
        const vx = p2[0] - p0[0];
        const vy = p2[1] - p0[1];
        const vz = p2[2] - p0[2];
        let nx = uy * vz - uz * vy;
        let ny = uz * vx - ux * vz;
        let nz = ux * vy - uy * vx;
        const nl = Math.hypot(nx, ny, nz) || 1;
        const tecken = ny < 0 ? -1 : 1; // normalen pekar uppåt
        nx = (tecken * nx) / nl;
        ny = (tecken * ny) / nl;
        nz = (tecken * nz) / nl;
        const base = pos.length / 3;
        for (const p of t) {
          pos.push(p[0], fot + p[1], p[2]);
          nor.push(nx, ny, nz);
        }
        ind.push(base, base + 1, base + 2);
        triHus.push(hi);
      }
      return;
    }
    h.ringar.forEach((ring) => {
      const n = ring.length;
      if (n < 3) return;
      for (let k = 0; k < n; k++) {
        const a = ring[k] as [number, number];
        const b = ring[(k + 1) % n] as [number, number];
        const dx = b[0] - a[0];
        const dz = b[1] - a[1];
        const l = Math.hypot(dx, dz) || 1;
        const nx = dz / l;
        const nz = -dx / l;
        const base = pos.length / 3;
        pos.push(a[0], h.yFot, a[1], b[0], h.yFot, b[1], b[0], h.yTak, b[1], a[0], h.yTak, a[1]);
        for (let q = 0; q < 4; q++) nor.push(nx, 0, nz);
        ind.push(base, base + 1, base + 2, base, base + 2, base + 3);
        triHus.push(hi, hi);
        kanter.push(a[0], h.yTak, a[1], b[0], h.yTak, b[1]);
        kanter.push(a[0], h.yFot, a[1], b[0], h.yFot, b[1]);
        kanter.push(a[0], h.yFot, a[1], a[0], h.yTak, a[1]);
      }
    });
    const yttre = (h.ringar[0] ?? []).map(([x, z]) => new Vector2(x, z));
    const hal = h.ringar.slice(1).map((r) => r.map(([x, z]) => new Vector2(x, z)));
    if (yttre.length >= 3) {
      const tri = ShapeUtils.triangulateShape(yttre, hal);
      const alla = [...yttre, ...hal.flat()];
      const base = pos.length / 3;
      for (const p of alla) {
        pos.push(p.x, h.yTak, p.y);
        nor.push(0, 1, 0);
      }
      for (const t of tri) {
        ind.push(base + (t[0] as number), base + (t[1] as number), base + (t[2] as number));
        triHus.push(hi);
      }
    }
  });
  return {
    position: Float32Array.from(pos),
    normal: Float32Array.from(nor),
    index: Uint32Array.from(ind),
    triHus: Uint32Array.from(triHus),
    kanter: Float32Array.from(kanter),
  };
}

/** Husen ur en byggnadsbuffert (urval = vilka som tas med, t.ex. alla med viss status). */
export function husFranBuffert(b: ByggnaderBuf, urval: (i: number) => boolean = () => true, hojdFran: "tak" | "fore" = "tak"): { hus: Hus[]; index: number[] } {
  const hus: Hus[] = [];
  const index: number[] = [];
  for (let i = 0; i < b.antal; i++) {
    if (!urval(i)) continue;
    const r0 = b.byggnad_ring[i] as number;
    const r1 = b.byggnad_ring[i + 1] as number;
    const ringar: Array<Array<[number, number]>> = [];
    for (let r = r0; r < r1; r++) {
      const ring: Array<[number, number]> = [];
      for (let p = b.ringstart[r] as number; p < (b.ringstart[r + 1] as number); p++) {
        ring.push([b.punkter[p * 2] as number, -(b.punkter[p * 2 + 1] as number)]);
      }
      ringar.push(ring);
    }
    hus.push({ ringar, yFot: b.z_fot[i] as number, yTak: (hojdFran === "fore" ? b.z_tak_fore[i] : b.z_tak[i]) as number });
    index.push(i);
  }
  return { hus, index };
}

export const STATUS_OFORANDRAD = 0;
export const STATUS_NY = 1;
export const STATUS_ANDRAD = 2;
export const STATUS_BORT = 3;

// ---------------------------------------------------------------------------------------------
// Vattenyta

export interface VattenYta {
  position: Float32Array;
  color: Float32Array;
  index: Uint32Array;
  /** Antal celler som fick en yta. */
  celler: number;
  /** Största djup bland dem (m). */
  maxDjup: number;
}

export interface MarkRuta {
  rut: Rut;
  x0: number;
  y0: number;
  cell: number;
  lo: number;
  hi: number;
  q: Uint16Array;
  enhet: string;
}

export function markRutaFran(b: Behallare): MarkRuta {
  const h = b.huvud as unknown as { lod: number; tx: number; ty: number; x0: number; y0: number; cell_m: number; v_lo: number; v_hi: number; enhet: string };
  return { rut: { lod: h.lod, tx: h.tx, ty: h.ty }, x0: h.x0, y0: h.y0, cell: h.cell_m, lo: h.v_lo, hi: h.v_hi, q: b.delar.v as Uint16Array, enhet: h.enhet };
}

/** Värdet i cellen som rymmer (e, n), eller null om inget värde (tak, utanför, utan data). */
export function markVarde(r: MarkRuta, e: number, n: number): number | null {
  const kol = Math.floor((e - r.x0) / r.cell);
  const rad = CELLER - 1 - Math.floor((n - r.y0) / r.cell);
  if (kol < 0 || kol >= CELLER || rad < 0 || rad >= CELLER) return null;
  const q = r.q[rad * CELLER + kol] as number;
  return q === INGET_VARDE ? null : avkvantisera(q, r.lo, r.hi);
}

/**
 * Vattenyta: en plan fyrkant per cell där djupet är minst `troskel`, på höjden terräng + djup.
 * Ytan är lösarens maxdjup i varje cell; i en fylld sänka blir nivån därför densamma i alla
 * celler. Färgen kommer ur `farg(djup)` (samma klasser som 2D).
 */
export function vattenYta(r: MarkRuta, hojd: (e: number, n: number) => number, o: Origo, troskel: number, farg: (djup: number) => [number, number, number, number]): VattenYta {
  const pos: number[] = [];
  const col: number[] = [];
  const ind: number[] = [];
  let celler = 0;
  let maxDjup = 0;
  for (let rad = 0; rad < CELLER; rad++) {
    for (let kol = 0; kol < CELLER; kol++) {
      const q = r.q[rad * CELLER + kol] as number;
      if (q === INGET_VARDE) continue;
      const djup = avkvantisera(q, r.lo, r.hi);
      if (!(djup >= troskel)) continue;
      const e0 = r.x0 + kol * r.cell;
      const n1 = r.y0 + (CELLER - rad) * r.cell;
      const z = hojd(e0 + r.cell / 2, n1 - r.cell / 2);
      if (!Number.isFinite(z)) continue;
      const y = z + djup - o.z;
      const f = farg(djup);
      const k = pos.length / 3;
      const x0 = e0 - o.e;
      const x1 = x0 + r.cell;
      const z0 = -(n1 - o.n);
      const z1 = z0 + r.cell;
      pos.push(x0, y, z0, x1, y, z0, x1, y, z1, x0, y, z1);
      for (let c = 0; c < 4; c++) col.push(f[0] / 255, f[1] / 255, f[2] / 255);
      ind.push(k, k + 2, k + 1, k, k + 3, k + 2);
      celler++;
      if (djup > maxDjup) maxDjup = djup;
    }
  }
  return { position: Float32Array.from(pos), color: Float32Array.from(col), index: Uint32Array.from(ind), celler, maxDjup };
}
