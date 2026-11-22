/*
 * Träff: vilket hus man pekar på. Alla hus inom träffytan listas, ytor man står i närmast
 * först (ritat sist överst), så att den som står i en tät kvartersbebyggelse kan välja.
 *
 * Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af, packages/map-revision/src/hit.ts):
 * `hitDistance` (avstånd, jämn-udda-regeln, kantens tolerans), `hitTest` (ruta först, sortering
 * på nivå, avstånd, ritordning) och `boxTest` (markera med en ruta, Liang-Barsky). O-Tid
 * har nivåerna punkt, linje, yta; här finns bara ytor (hus), men nivån behålls så att ett
 * hus man står i går före ett hus som bara ligger nära.
 */
import { boxAv, projicera, punktIRinger, type Box, type Punkt } from "./geom.ts";

export interface HusForm {
  id: string;
  /** Yttre ring (öppen) och eventuella hål. */
  ringar: ReadonlyArray<readonly Punkt[]>;
}

export interface Traff {
  id: string;
  /** Avstånd (m); 0 inne i huset. */
  distance: number;
  /** 0 = man står i huset, 1 = bara nära kanten. */
  niva: number;
}

function kantAvstand(p: Punkt, ringar: ReadonlyArray<readonly Punkt[]>): number {
  let basta = Infinity;
  for (const ring of ringar) {
    for (let i = 0; i < ring.length; i++) {
      const a = ring[i] as Punkt;
      const b = ring[(i + 1) % ring.length] as Punkt;
      basta = Math.min(basta, projicera(p, a, b).distance);
    }
  }
  return basta;
}

/** Husets avstånd till punkten om det är inom `tolerans` (m), annars undefined. */
export function traffAvstand(hus: HusForm, p: Punkt, tolerans: number): { distance: number; niva: number } | undefined {
  if (punktIRinger(p, hus.ringar)) return { distance: 0, niva: 0 };
  const d = kantAvstand(p, hus.ringar);
  return d <= tolerans ? { distance: d, niva: 1 } : undefined;
}

/** Husen under punkten, högst `grans`. Senare hus i listan ligger ovanpå (vinner vid lika). */
export function traffTest(hus: readonly HusForm[], p: Punkt, tolerans: number, grans = 8, rutor?: readonly Box[]): Traff[] {
  const hittade: Array<{ t: Traff; ordning: number }> = [];
  hus.forEach((h, i) => {
    const r = rutor?.[i] ?? boxAv(h.ringar[0] ?? []);
    const pad = tolerans * 1.5;
    if (p.x < r.minX - pad || p.x > r.maxX + pad || p.y < r.minY - pad || p.y > r.maxY + pad) return;
    const t = traffAvstand(h, p, tolerans);
    if (t) hittade.push({ t: { id: h.id, ...t }, ordning: i });
  });
  return hittade
    .sort((a, b) => a.t.niva - b.t.niva || a.t.distance - b.t.distance || b.ordning - a.ordning)
    .slice(0, grans)
    .map((x) => x.t);
}

function strackaRuta(a: Punkt, b: Punkt, box: Box): boolean {
  let t0 = 0;
  let t1 = 1;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const sidor: Array<[number, number]> = [
    [-dx, a.x - box.minX],
    [dx, box.maxX - a.x],
    [-dy, a.y - box.minY],
    [dy, box.maxY - a.y],
  ];
  for (const [pp, q] of sidor) {
    if (pp === 0) {
      if (q < 0) return false;
      continue;
    }
    const t = q / pp;
    if (pp < 0) {
      if (t > t1) return false;
      if (t > t0) t0 = t;
    } else {
      if (t < t0) return false;
      if (t < t1) t1 = t;
    }
  }
  return true;
}

/**
 * Husen som en ruta rör vid (markera med en ruta): ett hus räknas när någon kant går genom
 * rutan. Ett hus som bara omsluter rutan räknas inte.
 */
export function rutTest(hus: readonly HusForm[], box: Box): string[] {
  const ut: string[] = [];
  for (const h of hus) {
    const egen = boxAv(h.ringar[0] ?? []);
    if (egen.maxX < box.minX || egen.minX > box.maxX || egen.maxY < box.minY || egen.minY > box.maxY) continue;
    const ror = h.ringar.some((ring) =>
      ring.some((p, i) => strackaRuta(p, ring[(i + 1) % ring.length] as Punkt, box)),
    );
    if (ror) ut.push(h.id);
  }
  return ut;
}
