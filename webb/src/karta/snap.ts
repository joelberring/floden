/*
 * Fäst: punkten man sätter eller drar fästs mot befintliga byggnaders hörn och kanter, och
 * (som val) till rät vinkel mot förra sträckan eller kanten man startade på.
 *
 * Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af, apps/web/src/lib/map-project-snap.ts
 * och map-project-draw-assist.ts). R3: även öppna linjer (`oppna`: murar, kantlinjer, projektets
 * avgränsning) som fästmål, som O-Tids index över linjer och ytkanter. Samma tre nivåer: 1) hörn, 2) skärning mellan två kanter,
 * 3) närmaste punkt på en kant, och samma vinkellås (45 graders steg mot förra sträckan, 90
 * mot kanten man startat på). Skillnader: O-Tid räknar i kartans mm med ett rutnätsindex i
 * Worker; här räknas i SWEREF-meter med ett enkelt rutnät (Map) eftersom antalet byggnader är
 * hundratals till några tusen.
 */
import { avstand, lika, projicera, skarning, type Punkt } from "./geom.ts";

export type FastTyp = "hornpunkt" | "skarning" | "kant";

export interface FastMal {
  /** Objektet (byggnadens id) fästpunkten ligger på; saknas för det egna utkastet. */
  id: string | undefined;
  /** Sträckans index i ringen (kant, skarning) eller hörnets index (hornpunkt). */
  index: number;
  from?: Punkt;
  to?: Punkt;
  t?: number;
}

export interface FastTraff {
  typ: FastTyp;
  at: Punkt;
  /** Avstånd från pekaren (m). */
  distance: number;
  mal: FastMal;
  annan?: FastMal;
}

interface Strack {
  a: Punkt;
  b: Punkt;
  id: string;
  index: number;
}

export interface FastIndex {
  readonly strackor: Strack[];
  readonly punkter: Array<{ p: Punkt; id: string; index: number }>;
  readonly cell: number;
  readonly rutor: Map<string, { s: number[]; p: number[] }>;
}

const nyckel = (cx: number, cy: number): string => `${cx}:${cy}`;

/** Fästindex över objektens ringar (öppna ringar, id per objekt). */
export function byggFastIndex(objekt: ReadonlyArray<{ id: string; ringar: ReadonlyArray<readonly Punkt[]>; oppna?: boolean }>, cell = 50): FastIndex {
  const strackor: Strack[] = [];
  const punkter: Array<{ p: Punkt; id: string; index: number }> = [];
  const rutor = new Map<string, { s: number[]; p: number[] }>();
  const ruta = (cx: number, cy: number): { s: number[]; p: number[] } => {
    const k = nyckel(cx, cy);
    let r = rutor.get(k);
    if (!r) {
      r = { s: [], p: [] };
      rutor.set(k, r);
    }
    return r;
  };
  for (const o of objekt) {
    for (const ring of o.ringar) {
      let n = 0;
      for (let i = 0; i < ring.length; i++) {
        const a = ring[i] as Punkt;
        const b = ring[(i + 1) % ring.length] as Punkt;
        const pi = punkter.length;
        punkter.push({ p: a, id: o.id, index: i });
        ruta(Math.floor(a.x / cell), Math.floor(a.y / cell)).p.push(pi);
        if (o.oppna && i === ring.length - 1) continue; // en öppen linje har ingen sträcka från sista till första punkten
        const si = strackor.length;
        strackor.push({ a, b, id: o.id, index: n++ });
        const c0 = Math.floor(Math.min(a.x, b.x) / cell);
        const c1 = Math.floor(Math.max(a.x, b.x) / cell);
        const r0 = Math.floor(Math.min(a.y, b.y) / cell);
        const r1 = Math.floor(Math.max(a.y, b.y) / cell);
        for (let cy = r0; cy <= r1; cy++) for (let cx = c0; cx <= c1; cx++) ruta(cx, cy).s.push(si);
      }
    }
  }
  return { strackor, punkter, cell, rutor };
}

/** Det egna utkastet (eller huset som dras): punkter och sträckor som inte finns i indexet. */
export interface FastExtra {
  punkter: readonly Punkt[];
  strackor: ReadonlyArray<readonly [Punkt, Punkt]>;
}

export interface FastFraga {
  punkt: Punkt;
  /** Räckvidd (m). */
  rackvidd: number;
  /** Objekt som inte fäster (det som dras). */
  exkludera?: ReadonlySet<string>;
  extra?: FastExtra;
}

/** Högst så många sträckor nära pekaren prövas mot varandra för skärningar. */
const MAX_SKARNING = 48;

interface Kandidat {
  a: Punkt;
  b: Punkt;
  mal: FastMal;
  distance: number;
  t: number;
  objekt: string | undefined;
}

/** Bästa fästpunkten inom räckvidden: hörn före skärning före kant, eller undefined. */
export function fastAt(index: FastIndex | undefined, f: FastFraga): FastTraff | undefined {
  const { punkt: p, rackvidd: r } = f;
  if (!(r > 0)) return undefined;
  let hornpunkt: FastTraff | undefined;
  const overvag = (at: Punkt, mal: () => FastMal): void => {
    const d = avstand(at, p);
    if (d <= r && (!hornpunkt || d < hornpunkt.distance)) hornpunkt = { typ: "hornpunkt", at, distance: d, mal: mal() };
  };
  const nara: Kandidat[] = [];
  if (index) {
    const c0 = Math.floor((p.x - r) / index.cell);
    const c1 = Math.floor((p.x + r) / index.cell);
    const r0 = Math.floor((p.y - r) / index.cell);
    const r1 = Math.floor((p.y + r) / index.cell);
    const sedda = new Set<number>();
    for (let cy = r0; cy <= r1; cy++) {
      for (let cx = c0; cx <= c1; cx++) {
        const ruta = index.rutor.get(nyckel(cx, cy));
        if (!ruta) continue;
        for (const pi of ruta.p) {
          const q = index.punkter[pi];
          if (!q || f.exkludera?.has(q.id)) continue;
          overvag(q.p, () => ({ id: q.id, index: q.index }));
        }
        for (const si of ruta.s) {
          if (sedda.has(si)) continue;
          sedda.add(si);
          const s = index.strackor[si];
          if (!s || f.exkludera?.has(s.id)) continue;
          const pr = projicera(p, s.a, s.b);
          if (pr.distance > r) continue;
          nara.push({ a: s.a, b: s.b, distance: pr.distance, t: pr.t, objekt: s.id, mal: { id: s.id, index: s.index, from: s.a, to: s.b } });
        }
      }
    }
  }
  f.extra?.punkter.forEach((at, i) => overvag(at, () => ({ id: undefined, index: i })));
  if (hornpunkt) return hornpunkt;
  f.extra?.strackor.forEach(([a, b], i) => {
    const pr = projicera(p, a, b);
    if (pr.distance <= r) nara.push({ a, b, distance: pr.distance, t: pr.t, objekt: undefined, mal: { id: undefined, index: i, from: a, to: b } });
  });
  if (nara.length === 0) return undefined;
  nara.sort((x, y) => x.distance - y.distance);

  // 2) Skärningar mellan de närmaste sträckorna (grannsträckor i samma ring möts i ett hörn och räknas inte).
  let basta: FastTraff | undefined;
  const par = nara.slice(0, MAX_SKARNING);
  for (let i = 0; i < par.length; i++) {
    for (let j = i + 1; j < par.length; j++) {
      const ett = par[i] as Kandidat;
      const tva = par[j] as Kandidat;
      if (ett.objekt === tva.objekt && (Math.abs(ett.mal.index - tva.mal.index) <= 1 || delarAnde(ett, tva))) continue;
      const k = skarning(ett.a, ett.b, tva.a, tva.b);
      if (!k) continue;
      const d = avstand(k.at, p);
      if (d <= r && (!basta || d < basta.distance)) {
        basta = { typ: "skarning", at: k.at, distance: d, mal: { ...ett.mal, t: k.t }, annan: { ...tva.mal, t: k.u } };
      }
    }
  }
  if (basta) return basta;

  // 3) Närmaste punkt på en kant.
  const narmast = nara[0] as Kandidat;
  const at = { x: narmast.a.x + narmast.t * (narmast.b.x - narmast.a.x), y: narmast.a.y + narmast.t * (narmast.b.y - narmast.a.y) };
  return { typ: "kant", at, distance: narmast.distance, mal: { ...narmast.mal, t: narmast.t } };
}

const delarAnde = (a: Kandidat, b: Kandidat): boolean => lika(a.a, b.a) || lika(a.a, b.b) || lika(a.b, b.a) || lika(a.b, b.b);

/** Utkastets punkter som `extra`: de tidigare punkterna och sträckorna mellan dem, utan den senaste. */
export function utkastExtra(punkter: readonly Punkt[]): FastExtra {
  const tidigare = punkter.slice(0, -1);
  return { punkter: tidigare, strackor: tidigare.slice(1).map((at, i) => [tidigare[i] as Punkt, at] as const) };
}

/** Ringens punkter och sträckor som `extra`, utan hörnet som dras och sträckorna till det. */
export function ringExtra(ring: readonly Punkt[], utan?: Punkt): FastExtra {
  const fri = (a: Punkt): boolean => !utan || !lika(a, utan);
  const punkter = ring.filter(fri);
  const strackor: Array<readonly [Punkt, Punkt]> = [];
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i] as Punkt;
    const b = ring[(i + 1) % ring.length] as Punkt;
    if (fri(a) && fri(b)) strackor.push([a, b]);
  }
  return { punkter, strackor };
}

/** Fästets räckvidd i meter: cirka 10 px med mus, 22 px med finger eller penna. */
export function fastRackvidd(pekare: string, meterPerPx: number): number {
  return ((pekare === "touch" || pekare === "pen" ? 22 : 10)) * meterPerPx;
}

const GRAD = Math.PI / 180;

/** Riktningen (radianer, 0 = öster, moturs) som vinkellåset räknar från, och stegets storlek. */
export interface VinkelRef {
  vinkel: number;
  steg: number;
  typ: "strack" | "kant" | "norr";
}

/**
 * Referens för nästa sträcka: förra sträckan (steg), annars kanten som startpunkten är fäst på
 * (vinkelrätt eller parallellt, 90 graders steg), annars norr. `steg` är 90 grader för rät vinkel
 * och 45 med extra tryck (O-Tid använder 45 grader mot förra sträckan).
 */
export function vinkelRef(punkter: readonly Punkt[], start: FastTraff | undefined, stegGrader = 90): VinkelRef {
  const sista = punkter[punkter.length - 1];
  const foregaende = punkter[punkter.length - 2];
  const steg = stegGrader * GRAD;
  if (sista && foregaende && !lika(sista, foregaende)) {
    return { vinkel: Math.atan2(sista.y - foregaende.y, sista.x - foregaende.x), steg, typ: "strack" };
  }
  if (start && sista && lika(start.at, sista)) {
    const { from, to } = start.mal;
    if (from && to && !lika(from, to)) return { vinkel: Math.atan2(to.y - from.y, to.x - from.x), steg: 90 * GRAD, typ: "kant" };
  }
  return { vinkel: Math.PI / 2, steg, typ: "norr" };
}

/** Pekaren låst till närmaste tillåtna riktning från `origin`: riktningen och punkten på strålen. */
export function lasVinkel(origin: Punkt, pekare: Punkt, ref: VinkelRef): { dir: Punkt; at: Punkt } {
  const dx = pekare.x - origin.x;
  const dy = pekare.y - origin.y;
  const rå = Math.atan2(dy, dx);
  const k = Math.round((rå - ref.vinkel) / ref.steg);
  const vinkel = ref.vinkel + k * ref.steg;
  const dir = { x: Math.cos(vinkel), y: Math.sin(vinkel) };
  const langd = Math.max(0, dx * dir.x + dy * dir.y);
  return { dir, at: { x: origin.x + dir.x * langd, y: origin.y + dir.y * langd } };
}

/** Fäst längs den låsta strålen: skärningen mellan strålen och närmaste kant inom räckvidden från `nara`. */
export function fastPaStrale(
  index: FastIndex | undefined,
  f: { origin: Punkt; dir: Punkt; nara: Punkt; rackvidd: number; exkludera?: ReadonlySet<string>; extra?: FastExtra },
): FastTraff | undefined {
  const { origin: o, dir: d, nara, rackvidd: r } = f;
  if (!(r > 0)) return undefined;
  let basta: FastTraff | undefined;
  const overvag = (a: Punkt, b: Punkt, mal: FastMal): void => {
    const sx = b.x - a.x;
    const sy = b.y - a.y;
    const nämnare = d.x * sy - d.y * sx;
    if (Math.abs(nämnare) < 1e-12) return;
    const qx = a.x - o.x;
    const qy = a.y - o.y;
    const s = (qx * sy - qy * sx) / nämnare;
    const u = (qx * d.y - qy * d.x) / nämnare;
    if (s <= 1e-9 || u < -1e-9 || u > 1 + 1e-9) return;
    const at = { x: o.x + s * d.x, y: o.y + s * d.y };
    const dist = avstand(at, nara);
    if (dist <= r && (!basta || dist < basta.distance)) basta = { typ: "kant", at, distance: dist, mal: { ...mal, from: a, to: b, t: u } };
  };
  if (index) {
    const sedda = new Set<number>();
    for (let cy = Math.floor((nara.y - r) / index.cell); cy <= Math.floor((nara.y + r) / index.cell); cy++) {
      for (let cx = Math.floor((nara.x - r) / index.cell); cx <= Math.floor((nara.x + r) / index.cell); cx++) {
        for (const si of index.rutor.get(nyckel(cx, cy))?.s ?? []) {
          if (sedda.has(si)) continue;
          sedda.add(si);
          const s = index.strackor[si];
          if (!s || f.exkludera?.has(s.id)) continue;
          overvag(s.a, s.b, { id: s.id, index: s.index });
        }
      }
    }
  }
  f.extra?.strackor.forEach(([a, b], i) => overvag(a, b, { id: undefined, index: i }));
  return basta;
}
