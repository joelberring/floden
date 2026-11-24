/*
 * Linjer och ytor med höjd: redigering av polylinjer och ringar i SWEREF-meter. Rena funktioner som
 * ger nya listor; z (m RH 2000 eller höjd) följer med punkterna när de finns och interpoleras när en
 * punkt läggs in på en sträcka.
 *
 * Logik porterad från O-Tid (MIT, © 2026 Joel Berring, joelberring/otid @ b0b952f):
 *  - apps/web/src/lib/map-project-join.ts: `cutLineAt` (saxen: delar vid brytpunkt eller på sträckan,
 *    ändpunkter nekas), `joinLines` (ändarna inom tolerans kedjas, vänds åt rätt håll; exakt två linjer
 *    slås alltid ihop vid närmaste ändar), `reversePath` (vänd);
 *  - packages/map-revision/src/generalize.ts: `simplifyPoints` (Douglas-Peucker) och ringens delning vid
 *    längsta diagonalen (`simplifyRing`);
 *  - packages/map-revision/src/edit.ts: `moveVertex`, `insertVertex`, `deleteVertex` (minst 2 punkter på
 *    linje, 3 i ring), `insertPoints`.
 * Skillnader: O-Tids linjer är kommandon (M, L, C) i kartans mm med symbol; här raka polylinjer i meter
 * med z per punkt, och nyckeln är lagret och rollen, inte symbolen.
 */
import { avstand, lika, projicera, type Punkt } from "./geom.ts";

/** Punkt med valfri höjd (m). */
export type P3 = Punkt & { z?: number };

export const kopiaP = (p: P3): P3 => (p.z === undefined ? { x: p.x, y: p.y } : { x: p.x, y: p.y, z: p.z });

/** Punkten vid läget t (0-1) mellan a och b; z interpoleras om båda har z. */
export function mellan(a: P3, b: P3, t: number): P3 {
  const p: P3 = { x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y) };
  if (a.z !== undefined && b.z !== undefined) p.z = a.z + t * (b.z - a.z);
  return p;
}

export function langd(l: readonly Punkt[], sluten = false): number {
  let s = 0;
  const n = sluten ? l.length : l.length - 1;
  for (let i = 0; i < n; i++) s += avstand(l[i] as Punkt, l[(i + 1) % l.length] as Punkt);
  return s;
}

/** Minsta antal punkter: 2 på en linje, 3 i en ring. */
export const minPunkter = (sluten: boolean): number => (sluten ? 3 : 2);

export function flyttaPunkt(l: readonly P3[], index: number, till: Punkt): P3[] {
  if (index < 0 || index >= l.length) return l.map(kopiaP);
  return l.map((p, i) => (i === index ? { ...kopiaP(p), x: till.x, y: till.y } : kopiaP(p)));
}

/** Ny punkt efter punkt `index` (på sträckan till nästa), vid läget t eller i `at`. */
export function infogaPunkt(l: readonly P3[], index: number, t: number, sluten: boolean): P3[] | undefined {
  const sist = sluten ? l.length : l.length - 1;
  if (index < 0 || index >= sist) return undefined;
  const ny = mellan(l[index] as P3, l[(index + 1) % l.length] as P3, t);
  return [...l.slice(0, index + 1).map(kopiaP), ny, ...l.slice(index + 1).map(kopiaP)];
}

export function taBortPunkt(l: readonly P3[], index: number, sluten: boolean): P3[] | undefined {
  if (l.length <= minPunkter(sluten) || index < 0 || index >= l.length) return undefined;
  return l.filter((_, i) => i !== index).map(kopiaP);
}

export function narmastPunkt(l: readonly Punkt[], p: Punkt, tolerans: number): number | undefined {
  let basta: number | undefined;
  let d = tolerans;
  l.forEach((q, i) => {
    const x = avstand(q, p);
    if (x <= d) {
      basta = i;
      d = x;
    }
  });
  return basta;
}

export interface SidaTraff {
  index: number;
  t: number;
  at: Punkt;
  distance: number;
}

/** Närmaste sträcka inom `tolerans` (m). */
export function narmastSida(l: readonly Punkt[], p: Punkt, tolerans: number, sluten: boolean): SidaTraff | undefined {
  let basta: SidaTraff | undefined;
  let d = tolerans;
  const n = sluten ? l.length : l.length - 1;
  for (let i = 0; i < n; i++) {
    const pr = projicera(p, l[i] as Punkt, l[(i + 1) % l.length] as Punkt);
    if (pr.distance <= d) {
      basta = { index: i, t: pr.t, at: pr.at, distance: pr.distance };
      d = pr.distance;
    }
  }
  return basta;
}

/** Avståndet från p till linjen (m) om det är högst `tolerans`, annars undefined. */
export function traffLinje(l: readonly Punkt[], p: Punkt, tolerans: number): number | undefined {
  const t = narmastSida(l, p, tolerans, false);
  return t?.distance;
}

/** Mittpunkter att dra ut till nya punkter. */
export function mittpunkter(l: readonly P3[], sluten: boolean): Array<{ index: number; at: Punkt }> {
  const n = sluten ? l.length : l.length - 1;
  const ut: Array<{ index: number; at: Punkt }> = [];
  for (let i = 0; i < n; i++) ut.push({ index: i, at: mellan(l[i] as P3, l[(i + 1) % l.length] as P3, 0.5) });
  return ut;
}

/** Linjen åt andra hållet (z följer punkterna). */
export const vand = (l: readonly P3[]): P3[] => [...l].reverse().map(kopiaP);

/**
 * Saxen: delar linjen där man klickar, vid en brytpunkt om klicket står på en, annars på sträckan under
 * pekaren. Inget när klicket inte träffar eller står på en ändpunkt.
 */
export function klipp(l: readonly P3[], p: Punkt, tolerans: number): [P3[], P3[]] | undefined {
  if (l.length < 2) return undefined;
  let rad: P3[] = l.map(kopiaP);
  let index = narmastPunkt(rad, p, tolerans);
  if (index === undefined) {
    const s = narmastSida(rad, p, tolerans, false);
    if (!s) return undefined;
    rad = infogaPunkt(rad, s.index, s.t, false) as P3[];
    index = s.index + 1;
  }
  if (index <= 0 || index >= rad.length - 1) return undefined;
  return [rad.slice(0, index + 1).map(kopiaP), rad.slice(index).map(kopiaP)];
}

/** `b` läggs efter `a`: möts ändarna (inom `snap`) blir de en punkt (a:s behålls), annars binds de med en rak sträcka. */
function lagg(a: readonly P3[], b: readonly P3[], snap: number): P3[] {
  const glapp = avstand(a[a.length - 1] as Punkt, b[0] as Punkt);
  return [...a.map(kopiaP), ...(glapp <= snap ? b.slice(1) : b).map(kopiaP)];
}

/**
 * Slår ihop två linjer vid de ändar som ligger närmast varandra (vänder den ena vid behov). Med `tvang`
 * (precis två markerade linjer) slås de alltid ihop, med en rak sträcka emellan om de inte möts; annars
 * krävs att ändarna är inom `tolerans`. Inget resultat annars.
 */
export function slaIhop(a: readonly P3[], b: readonly P3[], tolerans: number, tvang = false): P3[] | undefined {
  if (a.length < 2 || b.length < 2) return undefined;
  const a0 = a[0] as Punkt;
  const a1 = a[a.length - 1] as Punkt;
  const b0 = b[0] as Punkt;
  const b1 = b[b.length - 1] as Punkt;
  const val = [
    { gap: avstand(a1, b0), efter: true, vand: false },
    { gap: avstand(a1, b1), efter: true, vand: true },
    { gap: avstand(a0, b1), efter: false, vand: false },
    { gap: avstand(a0, b0), efter: false, vand: true },
  ];
  let basta = val[0] as (typeof val)[number];
  for (const v of val) if (v.gap < basta.gap) basta = v;
  if (!tvang && basta.gap > tolerans) return undefined;
  const del = basta.vand ? vand(b) : b.map(kopiaP);
  return basta.efter ? lagg(a, del, tolerans) : lagg(del, a, tolerans);
}

function vinkelrat(p: Punkt, a: Punkt, b: Punkt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const l = Math.hypot(dx, dy);
  if (l === 0) return avstand(p, a);
  return Math.abs((p.x - a.x) * dy - (p.y - a.y) * dx) / l;
}

/** Douglas-Peucker: behåller första och sista punkten och de som avviker mer än `tolerans` (m). */
export function forenklaPunkter<T extends Punkt>(punkter: readonly T[], tolerans: number): T[] {
  if (punkter.length <= 2 || tolerans <= 0) return [...punkter];
  const behall = new Uint8Array(punkter.length);
  behall[0] = 1;
  behall[punkter.length - 1] = 1;
  const stapel: Array<[number, number]> = [[0, punkter.length - 1]];
  while (stapel.length) {
    const [f, s] = stapel.pop() as [number, number];
    let varst = -1;
    let index = -1;
    for (let i = f + 1; i < s; i++) {
      const d = vinkelrat(punkter[i] as Punkt, punkter[f] as Punkt, punkter[s] as Punkt);
      if (d > varst) {
        varst = d;
        index = i;
      }
    }
    if (varst > tolerans && index > 0) {
      behall[index] = 1;
      stapel.push([f, index], [index, s]);
    }
  }
  return punkter.filter((_, i) => behall[i] === 1);
}

/**
 * Förenklar en linje eller ring. En ring delas vid punkten längst från den första (en ring har ingen
 * början) och behåller minst tre punkter. Undefined om antalet punkter inte minskar.
 */
export function forenkla(l: readonly P3[], tolerans: number, sluten: boolean): P3[] | undefined {
  let ut: P3[];
  if (!sluten) ut = forenklaPunkter(l, tolerans);
  else {
    if (l.length < 4) return undefined;
    const f = l[0] as Punkt;
    let langst = 0;
    let bast = -1;
    l.forEach((p, i) => {
      const d = avstand(p, f);
      if (d > bast) {
        bast = d;
        langst = i;
      }
    });
    const ett = forenklaPunkter(l.slice(0, langst + 1), tolerans);
    const tva = forenklaPunkter([...l.slice(langst), l[0] as P3], tolerans);
    ut = [...ett, ...tva.slice(1, -1)];
    if (ut.length < 3) return undefined;
  }
  return ut.length < l.length ? ut.map(kopiaP) : undefined;
}

/** Ringen utan upprepad slutpunkt och utan dubbletter efter varandra (öppen ring). */
export function rensaRing(l: readonly P3[]): P3[] {
  const ut: P3[] = [];
  for (const p of l) {
    const s = ut[ut.length - 1];
    if (!s || !lika(s, p, 1e-9)) ut.push(kopiaP(p));
  }
  const f = ut[0];
  const s = ut[ut.length - 1];
  if (ut.length > 1 && f && s && lika(f, s, 1e-9)) ut.pop();
  return ut;
}
