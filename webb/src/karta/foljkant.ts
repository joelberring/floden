/*
 * Följ kant: ritar en ny linje eller yta längs en befintlig linje eller ytkant, så att den nya kanten
 * sammanfaller exakt med den (samma hörn, ingen glipa, ingen skarv av avrundning). Från den senaste
 * punkten, som är fäst på en linje eller ring, följs samma linje till punkten närmast pekaren; en
 * sluten ring går den kortare vägen runt.
 *
 * Logik porterad från O-Tid (MIT, © 2026 Joel Berring, joelberring/otid @ b0b952f,
 * apps/web/src/lib/map-project-draw-assist.ts: `locate`, `followLine`, `followEdge`). Skillnader:
 * linjerna är raka polylinjer i SWEREF-meter (O-Tid plattar ut kurvor ur fästindexet), och linjens
 * punkter ges direkt av anroparen i stället för att läsas ur ett index med sträcknummer.
 */
import { avstand, lika, type Punkt } from "./geom.ts";
import { kopiaP, type P3 } from "./linje.ts";

/** En linje eller ring att följa. Ringen är öppen (sista punkten upprepas inte). */
export interface FoljLinje {
  readonly punkter: readonly P3[];
  readonly sluten: boolean;
}

export interface LinjeLage {
  /** Sträckans index och läget på den (0-1). */
  readonly i: number;
  readonly t: number;
  readonly at: Punkt;
  readonly distance: number;
}

/** Linjens sträckor som punktföljd (en ring får första punkten sist). */
const kedja = (l: FoljLinje): readonly P3[] => (l.sluten ? [...l.punkter, l.punkter[0] as P3] : l.punkter);

/** Närmaste läge på linjen för punkten. */
export function lokalisera(l: FoljLinje, p: Punkt): LinjeLage {
  const k = kedja(l);
  let basta: LinjeLage = { i: 0, t: 0, at: k[0] as Punkt, distance: Infinity };
  for (let i = 0; i + 1 < k.length; i++) {
    const a = k[i] as Punkt;
    const b = k[i + 1] as Punkt;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const l2 = dx * dx + dy * dy;
    const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2));
    const at = { x: a.x + t * dx, y: a.y + t * dy };
    const distance = Math.hypot(p.x - at.x, p.y - at.y);
    if (distance < basta.distance) basta = { i, t, at, distance };
  }
  return basta;
}

/**
 * Punkterna längs linjen från `fran` till `till` (utan `fran`, med `till` sist): brytpunkterna emellan och
 * slutpunkten. Brytpunkterna är linjens egna (z följer med); slutpunkten får z interpolerad.
 */
export function folj(l: FoljLinje, fran: LinjeLage, till: LinjeLage): P3[] {
  const k = kedja(l);
  const n = k.length - 1;
  const s0 = fran.i + fran.t;
  const s1 = till.i + till.t;
  const kum = [0];
  for (let i = 0; i < n; i++) kum.push((kum[i] as number) + avstand(k[i] as Punkt, k[i + 1] as Punkt));
  const langs = (p: LinjeLage): number => (kum[p.i] as number) + p.t * ((kum[p.i + 1] as number) - (kum[p.i] as number));
  const eps = 1e-9;
  let mellan: Array<{ v: number; d: number }> = [];
  if (!l.sluten) {
    const framat = s1 >= s0;
    for (let v = 1; v < n; v++) if (framat ? v > s0 + eps && v < s1 - eps : v < s0 - eps && v > s1 + eps) mellan.push({ v, d: Math.abs(v - s0) });
  } else {
    const total = kum[n] as number;
    const fore = (((langs(till) - langs(fran)) % total) + total) % total;
    const framat = fore <= total - fore;
    const d1 = framat ? (((s1 - s0) % n) + n) % n : (((s0 - s1) % n) + n) % n;
    for (let v = 0; v < n; v++) {
      const d = framat ? (((v - s0) % n) + n) % n : (((s0 - v) % n) + n) % n;
      if (d > eps && d < d1 - eps) mellan.push({ v, d });
    }
  }
  mellan = mellan.sort((a, b) => a.d - b.d);
  const a = k[till.i] as P3;
  const b = k[till.i + 1] as P3;
  const slut: P3 = { x: till.at.x, y: till.at.y };
  if (a.z !== undefined && b.z !== undefined) slut.z = a.z + till.t * (b.z - a.z);
  return [...mellan.map((m) => kopiaP(k[m.v] as P3)), slut];
}

/** Följ kant: punkterna från fästpunkten `start` längs `linje` mot pekaren, eller undefined när det inte finns något att följa. */
export function foljKant(linje: FoljLinje, start: Punkt, pekare: Punkt, tolerans = 1e-3): { punkter: P3[]; slut: LinjeLage } | undefined {
  const fran = lokalisera(linje, start);
  if (fran.distance > tolerans) return undefined; // startpunkten ligger inte på linjen
  const till = lokalisera(linje, pekare);
  const punkter = folj(linje, fran, till);
  if (punkter.length === 1 && lika(punkter[0] as Punkt, start, 1e-9)) return undefined;
  return { punkter, slut: till };
}
