/*
 * Takformer i 3D (R6): platt, sadel, pulpet och mansard för ett hus ur planförslaget. Rena funktioner
 * utan WebGL, i scenkoordinater (x = öster, y = upp, z = söder).
 *
 * Det här är visning, inte beräkning. Husets höjd (`hojd_m`, K9) är höjden till det högsta på taket
 * (nock, eller högsta kanten på ett pulpettak), så 3D-vyn ligger inom samma volym som beräkningarna
 * läser; väggarna går upp till takfoten och taket stiger ur den. Takformen styr takvattnet på
 * servern (`sankgraf.takvatten`), men takvinkeln och pulpettakets fallriktning finns inte i K9 och är
 * ett val för visningen: sadel och mansard har nocken längs husets längdaxel (minsta omslutande
 * rektangel), pulpettaket faller mot den långsida som vetter mest åt söder.
 *
 * Taket är en funktion av avståndet d från nocklinjen tvärs husets längdaxel. Den är styckvis linjär,
 * så varje triangel i husets yta delas där profilen knäcker (nock, mansardens knäck) och får en
 * rät yta per del. Väggarna följer takets höjd längs kanten.
 */
import { ShapeUtils, Vector2 } from "three";

export type Takform = "platt" | "sadel" | "pulpet" | "mansard";
export const TAKFORMER_3D: readonly Takform[] = ["platt", "sadel", "pulpet", "mansard"];

/** Sadeltakets vinkel (grader) och pulpettakets lutning (grader) i visningen. */
export const SADEL_VINKEL_GRADER = 27;
export const PULPET_VINKEL_GRADER = 10;
/** Mansardtakets resning i förhållande till halva husbredden, och var knäcken ligger (andel av halva bredden från nocken). */
export const MANSARD_RESNING = 0.8;
export const MANSARD_KNAK = 0.7;
/** Taket får högst så stor del av husets höjd. */
export const MAX_ANDEL_HOJD = 0.5;

/**
 * Takformen för en K9-takform (text). Okänd eller saknad takform räknas som brutet tak (sadel), som
 * takvattnet (`docs/VATTEN.md`); "platt", "pulpet" och "mansard" med sina vanliga ord.
 */
export function tolkaTakform(text: string | null | undefined): Takform {
  const t = (text ?? "").trim().toLowerCase();
  if (["platt", "plant", "flat"].includes(t)) return "platt";
  if (["pulpet", "pulpettak", "skillion"].includes(t)) return "pulpet";
  if (["mansard", "mansardtak", "gambrel"].includes(t)) return "mansard";
  return "sadel";
}

type P2 = [number, number];

/** Konvext hölje (Andrews monotona kedja), moturs. */
export function konvextHolje(punkter: readonly P2[]): P2[] {
  const p = [...punkter].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const kors = (o: P2, a: P2, b: P2): number => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const ned: P2[] = [];
  for (const q of p) {
    while (ned.length >= 2 && kors(ned[ned.length - 2] as P2, ned[ned.length - 1] as P2, q) <= 0) ned.pop();
    ned.push(q);
  }
  const upp: P2[] = [];
  for (const q of [...p].reverse()) {
    while (upp.length >= 2 && kors(upp[upp.length - 2] as P2, upp[upp.length - 1] as P2, q) <= 0) upp.pop();
    upp.push(q);
  }
  ned.pop();
  upp.pop();
  return [...ned, ...upp];
}

export interface Rektangel {
  /** Mittpunkt. */
  c: P2;
  /** Enhetsvektor längs den längre sidan (nocken). */
  u: P2;
  /** Enhetsvektor tvärs (profilens riktning), vänd så att den pekar åt söder (+z) i första hand. */
  v: P2;
  /** Halv längd längs u och halv bredd längs v (hl >= hw). */
  hl: number;
  hw: number;
}

/** Minsta omslutande rektangel (roterande skjutmått över höljets kanter). Samma ring ger samma rektangel. */
export function minstaRektangel(ring: readonly P2[]): Rektangel | null {
  const h = konvextHolje(ring);
  if (h.length < 3) return null;
  let basta: { area: number; vinkel: number; xmin: number; xmax: number; ymin: number; ymax: number } | null = null;
  for (let i = 0; i < h.length; i++) {
    const a = h[i] as P2;
    const b = h[(i + 1) % h.length] as P2;
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (l < 1e-9) continue;
    const ux = (b[0] - a[0]) / l;
    const uz = (b[1] - a[1]) / l;
    let xmin = Infinity;
    let xmax = -Infinity;
    let ymin = Infinity;
    let ymax = -Infinity;
    for (const q of h) {
      const x = q[0] * ux + q[1] * uz;
      const y = -q[0] * uz + q[1] * ux;
      xmin = Math.min(xmin, x);
      xmax = Math.max(xmax, x);
      ymin = Math.min(ymin, y);
      ymax = Math.max(ymax, y);
    }
    const area = (xmax - xmin) * (ymax - ymin);
    const vinkel = Math.atan2(uz, ux);
    if (basta === null || area < basta.area - 1e-9 || (Math.abs(area - basta.area) <= 1e-9 && vinkel < basta.vinkel)) basta = { area, vinkel, xmin, xmax, ymin, ymax };
  }
  if (basta === null) return null;
  const ux = Math.cos(basta.vinkel);
  const uz = Math.sin(basta.vinkel);
  const bx = basta.xmax - basta.xmin;
  const by = basta.ymax - basta.ymin;
  const cx = (basta.xmin + basta.xmax) / 2;
  const cy = (basta.ymin + basta.ymax) / 2;
  const centrum: P2 = [cx * ux - cy * uz, cx * uz + cy * ux];
  // Längdaxeln är den längre sidan; den andra är profilens riktning.
  let u: P2 = bx >= by ? [ux, uz] : [-uz, ux];
  let v: P2 = bx >= by ? [-uz, ux] : [-ux, -uz];
  if (v[1] < -1e-9 || (Math.abs(v[1]) <= 1e-9 && v[0] < 0)) {
    v = [-v[0], -v[1]];
    u = [-u[0], -u[1]];
  }
  return { c: centrum, u, v, hl: Math.max(bx, by) / 2, hw: Math.min(bx, by) / 2 };
}

export interface Tak {
  form: Takform;
  rektangel: Rektangel;
  /** Takets resning (m): höjden från takfot till det högsta. */
  resning: number;
  /** Värden på d där profilen knäcker (nock, mansardens knäck). */
  knackar: number[];
  /** Avståndet d (m) från nocklinjen, tvärs längdaxeln, positivt åt söder. */
  d(x: number, z: number): number;
  /** Takets höjd (m) över takfoten i (x, z). */
  hojdOverFot(x: number, z: number): number;
}

/** Takprofilen för ett hus med yttre ring `ring` och totalhöjden `hojd` (m, fot till högsta punkten). Null för platt tak eller ett hus utan rektangel. */
export function takFor(form: Takform, ring: readonly P2[], hojd: number): Tak | null {
  if (form === "platt") return null;
  const r = minstaRektangel(ring);
  if (!r || r.hw < 1e-6 || !(hojd > 0)) return null;
  const tan = (g: number): number => Math.tan((g * Math.PI) / 180);
  const nominell = form === "sadel" ? r.hw * tan(SADEL_VINKEL_GRADER) : form === "mansard" ? r.hw * MANSARD_RESNING : 2 * r.hw * tan(PULPET_VINKEL_GRADER);
  const tak = Math.min(nominell, MAX_ANDEL_HOJD * hojd);
  const b = MANSARD_KNAK * r.hw;
  const d = (x: number, z: number): number => (x - r.c[0]) * r.v[0] + (z - r.c[1]) * r.v[1];
  const hojdOverFot = (x: number, z: number): number => {
    const dd = d(x, z);
    const a = Math.min(Math.abs(dd), r.hw);
    if (form === "sadel") return tak * (1 - a / r.hw);
    if (form === "mansard") return a >= b ? (tak * 0.7 * (r.hw - a)) / (r.hw - b) : tak * (0.7 + (0.3 * (b - a)) / b);
    return (tak * (r.hw - Math.max(-r.hw, Math.min(r.hw, dd)))) / (2 * r.hw);
  };
  return { form, rektangel: r, resning: tak, knackar: form === "sadel" ? [0] : form === "mansard" ? [-b, 0, b] : [], d, hojdOverFot };
}

type Tri = [P2, P2, P2];

/** Delar en triangel längs linjen d = k: de delar som ligger på var sida, som trianglar. */
export function delaTriangel(tri: Tri, d: (p: P2) => number, k: number): Tri[] {
  const dv = tri.map((p) => d(p) - k);
  const EPS = 1e-9;
  if (dv.every((v) => v >= -EPS) || dv.every((v) => v <= EPS)) return [tri];
  const sida = (positiv: boolean): P2[] => {
    const ut: P2[] = [];
    for (let i = 0; i < 3; i++) {
      const a = tri[i] as P2;
      const b = tri[(i + 1) % 3] as P2;
      const da = dv[i] as number;
      const db = dv[(i + 1) % 3] as number;
      const aIn = positiv ? da >= -EPS : da <= EPS;
      if (aIn) ut.push(a);
      if ((da > EPS && db < -EPS) || (da < -EPS && db > EPS)) {
        const t = da / (da - db);
        ut.push([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]);
      }
    }
    return ut;
  };
  const fan = (poly: P2[]): Tri[] => {
    const ut: Tri[] = [];
    for (let i = 1; i + 1 < poly.length; i++) ut.push([poly[0] as P2, poly[i] as P2, poly[i + 1] as P2]);
    return ut.filter((t) => Math.abs((t[1][0] - t[0][0]) * (t[2][1] - t[0][1]) - (t[1][1] - t[0][1]) * (t[2][0] - t[0][0])) > 1e-12);
  };
  return [...fan(sida(true)), ...fan(sida(false))];
}

export interface TakBuffertar {
  pos: number[];
  nor: number[];
  ind: number[];
  triHus: number[];
  kanter: number[];
}

/** Husets yta (yttre ring och hål) uppdelad längs takets knäckar, med höjd över takfoten per hörn. */
export function takTrianglar(ringar: ReadonlyArray<readonly P2[]>, tak: Tak): Array<Array<[number, number, number]>> {
  const yttre = (ringar[0] ?? []).map(([x, z]) => new Vector2(x, z));
  const hal = ringar.slice(1).map((r) => r.map(([x, z]) => new Vector2(x, z)));
  if (yttre.length < 3) return [];
  const alla = [...yttre, ...hal.flat()];
  const idx = ShapeUtils.triangulateShape(yttre, hal);
  const ut: Array<Array<[number, number, number]>> = [];
  const d = (p: P2): number => tak.d(p[0], p[1]);
  for (const t of idx) {
    let delar: Tri[] = [[0, 1, 2].map((i) => [(alla[t[i] as number] as Vector2).x, (alla[t[i] as number] as Vector2).y] as P2) as Tri];
    for (const k of tak.knackar) delar = delar.flatMap((x) => delaTriangel(x, d, k));
    for (const x of delar) ut.push(x.map(([px, pz]) => [px, tak.hojdOverFot(px, pz), pz] as [number, number, number]));
  }
  return ut;
}

/** Punkterna längs kanten a -> b där profilen knäcker (inklusive ändarna), i ordning. */
export function delaKant(a: P2, b: P2, tak: Tak): P2[] {
  const da = tak.d(a[0], a[1]);
  const db = tak.d(b[0], b[1]);
  const ts: number[] = [0, 1];
  for (const k of tak.knackar) {
    if ((da - k) * (db - k) < 0) ts.push((k - da) / (db - da));
  }
  ts.sort((x, y) => x - y);
  return ts.map((t) => [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])] as P2);
}
