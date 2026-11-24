/*
 * Förslagets ny mark, slänter och murar som ytor i 3D (R6). Rena funktioner utan WebGL, i scenkoordinater
 * (x = öster, y = upp, z = söder; samma origo som övriga 3D-vyn).
 *
 * Det här är visning av det som ritats, inte beräkning: nivån på en yta ritas som i K9 `markhojd`
 * (plan, lutande yta eller markens höjd plus `delta_m`, `docs/DATAKONTRAKT.md`), släntens bredd och
 * lutning som i `lapp.markniva` (förhållande 1:n eller fast bredd, rak mellan ytans kant och markens
 * höjd), och en mur står på marken med sin höjd (`hojd_m`, K9 `brytlinje_ny`). Hur vattnet påverkas
 * räknar servern; en slänt kring en yta ritas utåt från kanten även där servern hoppar över
 * byggnadsceller, och i skarpa hörn är den en förenkling. Markens höjd i vyn är basens.
 */
import { ShapeUtils, Vector2 } from "three";
import type { Origo } from "./koord.ts";

export type MarkRoll = "mark" | "hardgjort" | "vegetation" | "vatten";

/** En ritad markyta (K50-objekt med rollen mark, hårdgjort, vegetation eller vatten): öppen ring i SWEREF (E, N), moturs. */
export interface PlanMark {
  id: string;
  roll: MarkRoll;
  ring: Array<[number, number]>;
  /** Egenskaper som i K50/K9: `niva_rh2000`, `lutning`, `delta_m`, `slant`. */
  egenskaper: Record<string, unknown>;
}

/** En ritad mur eller kant (brytlinje): linjen i SWEREF (E, N), typ och höjd över marken. */
export interface PlanMur {
  id: string;
  typ: string;
  hojd_m: number;
  linje: Array<[number, number]>;
}

export interface Yta3d {
  position: Float32Array;
  normal: Float32Array;
  index: Uint32Array;
}

export type Hojdfn = (e: number, n: number) => number;

/** Tjocklek (m) per brytlinjetyp i visningen. */
export const MURBREDD: Readonly<Record<string, number>> = { mur: 0.3, stodmur: 0.5, kantsten: 0.15, bankkant: 0.3, staket: 0.06 };
/** En ritad yta utan egen nivå (hårdgjort, vegetation, vatten) ligger så här mycket över marken. */
export const DEKAL_M = 0.05;
export const STEG_MUR_M = 2;
export const STEG_KANT_M = 2;
export const MAX_KANT_M = 4;
export const MAX_TRIANGLAR = 6000;

// ------------------------------------------------------------------ nivåer

type P3 = [number, number, number];
const ar3 = (p: unknown): p is P3 => Array.isArray(p) && p.length >= 3 && p.slice(0, 3).every((v) => typeof v === "number" && Number.isFinite(v));

function narmastPaLinje(linje: P3[], e: number, n: number): { d: number; z: number } {
  let basta = Infinity;
  let z = Number.NaN;
  for (let i = 0; i + 1 < linje.length; i++) {
    const a = linje[i] as P3;
    const b = linje[i + 1] as P3;
    const de = b[0] - a[0];
    const dn = b[1] - a[1];
    const l2 = de * de + dn * dn;
    if (l2 <= 1e-9) continue;
    const t = Math.max(0, Math.min(1, ((e - a[0]) * de + (n - a[1]) * dn) / l2));
    const d = Math.hypot(e - (a[0] + t * de), n - (a[1] + t * dn));
    if (d < basta) {
      basta = d;
      z = a[2] + t * (b[2] - a[2]);
    }
  }
  return { d: basta, z };
}

/** Plan i minsta kvadrat genom tre eller fler punkter [E, N, z] (samma lösning som servern): z = z0 + gx (E − E0) + gy (N − N0). */
function planUrPunkter(p: P3[]): { e0: number; n0: number; z0: number; gx: number; gy: number } | null {
  const k = p.length;
  const e0 = p.reduce((s, q) => s + q[0], 0) / k;
  const n0 = p.reduce((s, q) => s + q[1], 0) / k;
  let sxx = 0;
  let sxy = 0;
  let syy = 0;
  let sxz = 0;
  let syz = 0;
  let sz = 0;
  for (const q of p) {
    const x = q[0] - e0;
    const y = q[1] - n0;
    sxx += x * x;
    sxy += x * y;
    syy += y * y;
    sxz += x * q[2];
    syz += y * q[2];
    sz += q[2];
  }
  const det = sxx * syy - sxy * sxy;
  if (Math.abs(det) < 1e-9) return null;
  return { e0, n0, z0: sz / k, gx: (sxz * syy - syz * sxy) / det, gy: (syz * sxx - sxz * sxy) / det };
}

/** Den lutande ytans höjd (m RH 2000) i (e, n) för de fyra formerna i K9 `markhojd.lutning`, eller null om formen inte går att läsa. */
export function lutningHojd(lutning: unknown, e: number, n: number): number | null {
  if (!lutning || typeof lutning !== "object") return null;
  const l = lutning as Record<string, unknown>;
  if (ar3(l.punkt) && typeof l.fall === "number") {
    const az = (((typeof l.riktning_grader === "number" ? l.riktning_grader : 0) as number) * Math.PI) / 180;
    return l.punkt[2] + -l.fall * Math.sin(az) * (e - l.punkt[0]) + -l.fall * Math.cos(az) * (n - l.punkt[1]);
  }
  if (ar3(l.a) && ar3(l.b)) {
    const de = l.b[0] - l.a[0];
    const dn = l.b[1] - l.a[1];
    const l2 = de * de + dn * dn;
    if (l2 <= 1e-12) return null;
    const k = (l.b[2] - l.a[2]) / l2;
    return l.a[2] + k * de * (e - l.a[0]) + k * dn * (n - l.a[1]);
  }
  if (Array.isArray(l.punkter) && l.punkter.length >= 3 && l.punkter.every(ar3)) {
    const pl = planUrPunkter(l.punkter as P3[]);
    return pl ? pl.z0 + pl.gx * (e - pl.e0) + pl.gy * (n - pl.n0) : null;
  }
  if (Array.isArray(l.hojdlinje_a) && Array.isArray(l.hojdlinje_b) && l.hojdlinje_a.every(ar3) && l.hojdlinje_b.every(ar3)) {
    const a = narmastPaLinje(l.hojdlinje_a as P3[], e, n);
    const b = narmastPaLinje(l.hojdlinje_b as P3[], e, n);
    const s = a.d + b.d;
    if (!Number.isFinite(a.z)) return null;
    return s > 1e-9 && Number.isFinite(b.z) ? (a.z * b.d + b.z * a.d) / s : a.z;
  }
  return null;
}

/** Ytans nivå i (e, n): lutande yta, markens höjd plus `delta_m`, plan nivå eller (utan nivå) marken plus en tunn dekal. */
export function markNiva(egenskaper: Record<string, unknown>, hojd: Hojdfn): { niva: Hojdfn; harEgenNiva: boolean } {
  const lut = egenskaper.lutning;
  if (lut && lutningHojd(lut, 0, 0) !== null) return { niva: (e, n) => lutningHojd(lut, e, n) as number, harEgenNiva: true };
  const d = egenskaper.delta_m;
  if (typeof d === "number" && Number.isFinite(d)) return { niva: (e, n) => hojd(e, n) + d, harEgenNiva: true };
  const z = egenskaper.niva_rh2000;
  if (typeof z === "number" && Number.isFinite(z)) return { niva: () => z, harEgenNiva: true };
  return { niva: (e, n) => hojd(e, n) + DEKAL_M, harEgenNiva: false };
}

/** Släntens förhållande (1:n) eller fasta bredd, ur `slant`; null utan slänt. */
export function slantAv(egenskaper: Record<string, unknown>): { forhallande?: number; bredd_m?: number } | null {
  const s = egenskaper.slant;
  if (!s || typeof s !== "object") return null;
  const o = s as Record<string, unknown>;
  if (typeof o.forhallande === "number" && o.forhallande > 0) return { forhallande: o.forhallande };
  if (typeof o.bredd_m === "number" && o.bredd_m > 0) return { bredd_m: o.bredd_m };
  return null;
}

// ------------------------------------------------------------------ geometri

type P2 = [number, number];

/** Ringen med punkter så att ingen kant är längre än `max` m (kanten delas lika). */
export function tata(ring: readonly P2[], max: number, sluten: boolean): P2[] {
  const ut: P2[] = [];
  const n = ring.length;
  const antalKanter = sluten ? n : n - 1;
  for (let i = 0; i < antalKanter; i++) {
    const a = ring[i] as P2;
    const b = ring[(i + 1) % n] as P2;
    const delar = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / max));
    for (let k = 0; k < delar; k++) ut.push([a[0] + ((b[0] - a[0]) * k) / delar, a[1] + ((b[1] - a[1]) * k) / delar]);
  }
  if (!sluten && n > 0) ut.push(ring[n - 1] as P2);
  return ut;
}

class Bygge {
  pos: number[] = [];
  nor: number[] = [];
  ind: number[] = [];

  /** En triangel med egen plan normal (uppåt om `upp`). */
  tri(a: P3, b: P3, c: P3, upp = false): void {
    const ux = b[0] - a[0];
    const uy = b[1] - a[1];
    const uz = b[2] - a[2];
    const vx = c[0] - a[0];
    const vy = c[1] - a[1];
    const vz = c[2] - a[2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const l = Math.hypot(nx, ny, nz);
    if (l < 1e-12) return;
    const t = upp && ny < 0 ? -1 : 1;
    nx = (t * nx) / l;
    ny = (t * ny) / l;
    nz = (t * nz) / l;
    const base = this.pos.length / 3;
    for (const p of [a, b, c]) {
      this.pos.push(p[0], p[1], p[2]);
      this.nor.push(nx, ny, nz);
    }
    this.ind.push(base, base + 1, base + 2);
  }

  quad(a: P3, b: P3, c: P3, d: P3): void {
    this.tri(a, b, c);
    this.tri(a, c, d);
  }

  yta(): Yta3d {
    return { position: Float32Array.from(this.pos), normal: Float32Array.from(this.nor), index: Uint32Array.from(this.ind) };
  }
}

const scen = (e: number, n: number, z: number, o: Origo): P3 => [e - o.e, z - o.z, -(n - o.n)];

type TriE = [P2, P2, P2];

/** Delar trianglar tills ingen kant är längre än `max` (mittpunktsdelning), eller `maxTri` nås. */
export function forfina(tri: TriE[], max: number, maxTri = MAX_TRIANGLAR): TriE[] {
  let nu = tri;
  for (let varv = 0; varv < 8; varv++) {
    const lang = nu.some((t) => [0, 1, 2].some((i) => Math.hypot((t[(i + 1) % 3] as P2)[0] - (t[i] as P2)[0], (t[(i + 1) % 3] as P2)[1] - (t[i] as P2)[1]) > max));
    if (!lang || nu.length * 4 > maxTri) break;
    nu = nu.flatMap((t): TriE[] => {
      const m = (a: P2, b: P2): P2 => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const [a, b, c] = t;
      const ab = m(a, b);
      const bc = m(b, c);
      const ca = m(c, a);
      return [[a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca]];
    });
  }
  return nu;
}

/** Ytan som tak ovanpå marken, och släntremsan runt den (null utan slänt). */
export function markYta(m: PlanMark, hojd: Hojdfn, o: Origo): { yta: Yta3d; slant: Yta3d | null } {
  const { niva, harEgenNiva } = markNiva(m.egenskaper, hojd);
  const ring = m.ring;
  const tom = { yta: new Bygge().yta(), slant: null };
  if (ring.length < 3) return tom;
  const idx = ShapeUtils.triangulateShape(ring.map(([e, n]) => new Vector2(e, n)), []);
  let tri: TriE[] = idx.map((t) => [ring[t[0] as number] as P2, ring[t[1] as number] as P2, ring[t[2] as number] as P2]);
  // Plana nivåer och lutande plan är raka över en triangel; följer nivån marken behövs fler punkter.
  const foljerMark = !harEgenNiva || typeof m.egenskaper.delta_m === "number" || (typeof m.egenskaper.lutning === "object" && m.egenskaper.lutning !== null && "hojdlinje_a" in (m.egenskaper.lutning as object));
  if (foljerMark) tri = forfina(tri, MAX_KANT_M);
  const b = new Bygge();
  for (const t of tri) b.tri(...(t.map(([e, n]) => scen(e, n, niva(e, n), o)) as [P3, P3, P3]), true);
  const s = slantAv(m.egenskaper);
  if (!s) return { yta: b.yta(), slant: null };
  // Släntremsan: från varje hörn ut längs kantens utåtriktade normal så långt släntens bredd är, där höjden möter marken.
  const tat = tata(ring, STEG_KANT_M, true);
  const n = tat.length;
  const bs = new Bygge();
  const ut: Array<{ inre: P3; yttre: P3 } | null> = tat.map((p, i) => {
    const a = tat[(i + n - 1) % n] as P2;
    const c = tat[(i + 1) % n] as P2;
    const n1 = norm([p[1] - a[1], -(p[0] - a[0])]);
    const n2 = norm([c[1] - p[1], -(c[0] - p[0])]);
    const sx = n1[0] + n2[0];
    const sy = n1[1] + n2[1];
    const sl = Math.hypot(sx, sy);
    if (sl < 1e-9) return null;
    const cos = sl / 2; // cos(halva vinkeln mellan normalerna)
    const dir: P2 = [sx / sl, sy / sl];
    const delta = niva(p[0], p[1]) - hojd(p[0], p[1]);
    const w = s.bredd_m ?? Math.abs(delta) * (s.forhallande ?? 0);
    if (!(w > 0.05)) return null;
    const avst = w / Math.max(cos, 0.5);
    const q: P2 = [p[0] + dir[0] * avst, p[1] + dir[1] * avst];
    return { inre: scen(p[0], p[1], niva(p[0], p[1]), o), yttre: scen(q[0], q[1], hojd(q[0], q[1]), o) };
  });
  for (let i = 0; i < n; i++) {
    const a = ut[i];
    const c = ut[(i + 1) % n];
    if (!a || !c) continue;
    bs.tri(a.inre, c.inre, c.yttre, true);
    bs.tri(a.inre, c.yttre, a.yttre, true);
  }
  return { yta: b.yta(), slant: bs.ind.length > 0 ? bs.yta() : null };
}

function norm(v: P2): P2 {
  const l = Math.hypot(v[0], v[1]);
  return l < 1e-12 ? [0, 0] : [v[0] / l, v[1] / l];
}

/** En mur eller kant som ett band på marken med höjd `hojd_m`: sidor, topp och ändar. Tomt för en linje med färre än två punkter. */
export function murYta(m: PlanMur, hojd: Hojdfn, o: Origo): Yta3d {
  const b = new Bygge();
  if (m.linje.length < 2 || !(m.hojd_m > 0)) return b.yta();
  const halv = (MURBREDD[m.typ] ?? 0.3) / 2;
  const p = tata(m.linje, STEG_MUR_M, false);
  const sidor = p.map((q, i) => {
    const a = p[Math.max(0, i - 1)] as P2;
    const c = p[Math.min(p.length - 1, i + 1)] as P2;
    const t = norm([c[0] - a[0], c[1] - a[1]]);
    const nx = -t[1];
    const ny = t[0];
    const z = hojd(q[0], q[1]);
    const mk = (s: number, y: number): P3 => scen(q[0] + nx * halv * s, q[1] + ny * halv * s, y, o);
    return { vf: mk(1, z - 0.1), vt: mk(1, z + m.hojd_m), hf: mk(-1, z - 0.1), ht: mk(-1, z + m.hojd_m) };
  });
  for (let i = 0; i + 1 < sidor.length; i++) {
    const a = sidor[i] as (typeof sidor)[number];
    const c = sidor[i + 1] as (typeof sidor)[number];
    b.quad(a.vf, c.vf, c.vt, a.vt); // vänster sida
    b.quad(c.hf, a.hf, a.ht, c.ht); // höger sida
    b.tri(a.vt, c.vt, c.ht, true); // topp
    b.tri(a.vt, c.ht, a.ht, true);
  }
  const f = sidor[0] as (typeof sidor)[number];
  const s = sidor[sidor.length - 1] as (typeof sidor)[number];
  b.quad(f.hf, f.vf, f.vt, f.ht); // ändarna
  b.quad(s.vf, s.hf, s.ht, s.vt);
  return b.yta();
}
