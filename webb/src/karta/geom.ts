/*
 * Ritgeometri i SWEREF 99 18 00 (meter): x = E, y = N. Ren geometri utan webbläsare.
 *
 * Endast det som behövs för att rita, träffa och kontrollera en skiss. Kontrollen som gäller
 * (giltig form, höjd, utbredning) görs av servern med kärnans kod; den här filen låter bara
 * klienten avstå från att skicka uppenbart ogiltiga former och visa en skiss med yta.
 */

export interface Punkt {
  x: number;
  y: number;
}

/** Öppen ring: sista hörnet upprepas inte. */
export type Ring = Punkt[];

export const lika = (a: Punkt, b: Punkt, tol = 1e-9): boolean => Math.abs(a.x - b.x) < tol && Math.abs(a.y - b.y) < tol;

export const avstand = (a: Punkt, b: Punkt): number => Math.hypot(a.x - b.x, a.y - b.y);

/** Polygonens signerade yta (m², positiv moturs). */
export function signeradYta(ring: readonly Punkt[]): number {
  let s = 0;
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i] as Punkt;
    const b = ring[(i + 1) % ring.length] as Punkt;
    s += a.x * b.y - b.x * a.y;
  }
  return s / 2;
}

export const yta = (ring: readonly Punkt[]): number => (ring.length >= 3 ? Math.abs(signeradYta(ring)) : 0);

/** Antal våningar vid våningshöjden (minst 1), som kärnans uppskattning. */
export function vaningar(hojd: number, vaningshojd = 3): number {
  return Math.max(1, Math.floor(hojd / vaningshojd));
}

/** Uppskattad BTA = yta x våningar. En uppskattning av skissen, inget resultat. */
export function btaUppskattning(ytaM2: number, hojd: number, vaningshojd = 3): number {
  return ytaM2 * vaningar(hojd, vaningshojd);
}

export interface Box {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export function boxAv(ring: readonly Punkt[]): Box {
  const b = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  for (const p of ring) {
    if (p.x < b.minX) b.minX = p.x;
    if (p.x > b.maxX) b.maxX = p.x;
    if (p.y < b.minY) b.minY = p.y;
    if (p.y > b.maxY) b.maxY = p.y;
  }
  return b;
}

/** Närmaste punkt på sträckan a-b: läget t (0-1), punkten och avståndet. */
export function projicera(p: Punkt, a: Punkt, b: Punkt): { t: number; at: Punkt; distance: number } {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const l2 = dx * dx + dy * dy;
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2));
  const at = { x: a.x + t * dx, y: a.y + t * dy };
  return { t, at, distance: Math.hypot(p.x - at.x, p.y - at.y) };
}

/** Skärning mellan sträckorna a-b och c-d (inte parallella), annars undefined. */
export function skarning(a: Punkt, b: Punkt, c: Punkt, d: Punkt): { at: Punkt; t: number; u: number } | undefined {
  const rx = b.x - a.x;
  const ry = b.y - a.y;
  const sx = d.x - c.x;
  const sy = d.y - c.y;
  const nämnare = rx * sy - ry * sx;
  if (Math.abs(nämnare) < 1e-12 * Math.max(1, Math.hypot(rx, ry) * Math.hypot(sx, sy))) return undefined;
  const qx = c.x - a.x;
  const qy = c.y - a.y;
  const t = (qx * sy - qy * sx) / nämnare;
  const u = (qx * ry - qy * rx) / nämnare;
  if (t < -1e-9 || t > 1 + 1e-9 || u < -1e-9 || u > 1 + 1e-9) return undefined;
  return { at: { x: a.x + t * rx, y: a.y + t * ry }, t, u };
}

/** Punkt i ring (jämn-udda-regeln över alla ringar; ett hål räknas inte). */
export function punktIRinger(p: Punkt, ringar: readonly (readonly Punkt[])[]): boolean {
  let korsningar = 0;
  for (const ring of ringar) {
    for (let i = 0, f = ring.length - 1; i < ring.length; f = i, i++) {
      const a = ring[i] as Punkt;
      const b = ring[f] as Punkt;
      if (a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) korsningar++;
    }
  }
  return korsningar % 2 === 1;
}

/**
 * Korsar ringens kanter varandra? Grannkanter delar ett hörn och räknas inte; en kant som
 * rör en icke-grann kant (även i ett hörn) räknas som korsning. Skiss: servern kontrollerar på riktigt.
 */
export function sjalvkorsande(ring: readonly Punkt[]): boolean {
  const n = ring.length;
  if (n < 4) return false;
  for (let i = 0; i < n; i++) {
    const a = ring[i] as Punkt;
    const b = ring[(i + 1) % n] as Punkt;
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue;
      const c = ring[j] as Punkt;
      const d = ring[(j + 1) % n] as Punkt;
      if (skarning(a, b, c, d)) return true;
    }
  }
  return false;
}

/** Ringen moturs och med hörnen avrundade till 1 cm (samma regel som servern; första hörnet behålls). */
export function normaliseraRing(ring: readonly Punkt[]): Ring {
  const r = (v: number): number => Math.round(v * 100) / 100;
  const ren: Punkt[] = [];
  for (const p of ring) {
    const q = { x: r(p.x), y: r(p.y) };
    const sista = ren[ren.length - 1];
    if (!sista || !lika(sista, q)) ren.push(q);
  }
  const forsta = ren[0];
  const sista = ren[ren.length - 1];
  if (ren.length > 1 && forsta && sista && lika(forsta, sista)) ren.pop();
  if (ren.length >= 3 && signeradYta(ren) < 0) return [ren[0] as Punkt, ...ren.slice(1).reverse()];
  return ren;
}

/** GeoJSON-ring (sluten) av en öppen ring. */
export function tillGeoRing(ring: readonly Punkt[]): number[][] {
  const r = ring.map((p) => [p.x, p.y]);
  const f = r[0];
  return f ? [...r, [...f]] : [];
}

/** Öppen ring av en GeoJSON-ring. */
export function franGeoRing(koord: readonly (readonly number[])[]): Ring {
  const r = koord.map((p) => ({ x: p[0] as number, y: p[1] as number }));
  const f = r[0];
  const s = r[r.length - 1];
  if (r.length > 1 && f && s && lika(f, s)) r.pop();
  return r;
}
