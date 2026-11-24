/*
 * Nytt projekt: ren logik utan DOM (så att den går att testa med Nodes testkörare). Allt i SWEREF 99 18 00
 * (EPSG:3011, meter). Servern gör de bindande kontrollerna (`POST /api/projekt`, K48); här visas bara samma
 * besked i förväg: ligger avgränsningen inom giltig kärna, delvis utanför (varning) eller utanför yttre gräns (fel).
 */
import { formatera, formateraHogst } from "../tal.ts";
import type { GeografiPost, Rektangel3011, SkapaProjektBegaran } from "../types.ts";

export interface Pkt {
  x: number;
  y: number;
}

export const MIN_YTA_M2 = 100;
export const NAMN_MAX = 120;

/** Omslutande rektangel [xmin, ymin, xmax, ymax]; null utan punkter. */
export function omslutande(pkt: ReadonlyArray<Pkt>): Rektangel3011 | null {
  if (pkt.length === 0) return null;
  return [
    Math.min(...pkt.map((p) => p.x)),
    Math.min(...pkt.map((p) => p.y)),
    Math.max(...pkt.map((p) => p.x)),
    Math.max(...pkt.map((p) => p.y)),
  ];
}

/** De fyra hörnen i en rektangel som en stängd GeoJSON-polygon. */
export function rektangelPolygon(r: Rektangel3011): GeoJSON.Polygon {
  const [x0, y0, x1, y1] = r;
  return { type: "Polygon", coordinates: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]] };
}

/** Polygon ur ritade punkter (stängd ring, medsols/moturs som ritat); minst tre punkter. */
export function punkterPolygon(pkt: ReadonlyArray<Pkt>): GeoJSON.Polygon | null {
  if (pkt.length < 3) return null;
  const ring = pkt.map((p) => [p.x, p.y]);
  ring.push([pkt[0]?.x ?? 0, pkt[0]?.y ?? 0]);
  return { type: "Polygon", coordinates: [ring] };
}

export const yta = (r: Rektangel3011): number => Math.max(0, r[2] - r[0]) * Math.max(0, r[3] - r[1]);

type Par = [number, number];

/** Hörnen i polygonens yttre ring utan den stängande upprepningen. */
function horn(poly: GeoJSON.Polygon): Par[] {
  const ring = (poly.coordinates[0] ?? []).map((p) => [p[0] ?? 0, p[1] ?? 0] as Par);
  const f = ring[0];
  const s = ring[ring.length - 1];
  if (ring.length > 1 && f && s && f[0] === s[0] && f[1] === s[1]) ring.pop();
  return ring;
}

/** Ytan inom en ring (m², snörningsformeln; absolutbelopp). */
export function ringYta(ring: ReadonlyArray<readonly [number, number]>): number {
  let a = 0;
  for (let i = 0; i < ring.length; i++) {
    const [x0, y0] = ring[i] as readonly [number, number];
    const [x1, y1] = ring[(i + 1) % ring.length] as readonly [number, number];
    a += x0 * y1 - x1 * y0;
  }
  return Math.abs(a) / 2;
}

/** Polygonens yta (m²). */
export const polygonYta = (poly: GeoJSON.Polygon): number => ringYta(horn(poly));

/** Avgränsningens yta: polygonens om den finns, annars rektangelns. */
export const avgransningsYta = (r: Rektangel3011, poly?: GeoJSON.Polygon | null): number => (poly ? polygonYta(poly) : yta(r));

/** Ytan av en ring inom en rektangel (klippning mot rektangelns fyra kanter; rektangeln är konvex, så ytan blir rätt även för en konkav ring). */
export function ytaInomRektangel(ring: ReadonlyArray<readonly [number, number]>, r: Rektangel3011): number {
  let pts: Par[] = ring.map((p) => [p[0], p[1]] as Par);
  const kanter: Array<(p: Par) => number> = [(p) => p[0] - r[0], (p) => r[2] - p[0], (p) => p[1] - r[1], (p) => r[3] - p[1]];
  for (const d of kanter) {
    const ut: Par[] = [];
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i] as Par;
      const b = pts[(i + 1) % pts.length] as Par;
      const da = d(a);
      const db = d(b);
      if (da >= 0) ut.push(a);
      if ((da >= 0) !== (db >= 0)) {
        const t = da / (da - db);
        ut.push([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]);
      }
    }
    pts = ut;
    if (pts.length === 0) return 0;
  }
  return ringYta(pts);
}

const kors = (o: Par, a: Par, b: Par): number => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

/** Skär två sträckor varandra (även i en ändpunkt som inte är gemensam)? Returnerar skärningspunkten, eller null. */
function strackorSkar(a: Par, b: Par, c: Par, d: Par): Par | null {
  const d1 = kors(c, d, a);
  const d2 = kors(c, d, b);
  const d3 = kors(a, b, c);
  const d4 = kors(a, b, d);
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) {
    const t = d3 / (d3 - d4);
    return [c[0] + t * (d[0] - c[0]), c[1] + t * (d[1] - c[1])];
  }
  const pa = (p: Par, q: Par, r: Par): boolean =>
    kors(q, r, p) === 0 && Math.min(q[0], r[0]) <= p[0] && p[0] <= Math.max(q[0], r[0]) && Math.min(q[1], r[1]) <= p[1] && p[1] <= Math.max(q[1], r[1]);
  for (const p of [a, b]) if (pa(p, c, d)) return p;
  for (const p of [c, d]) if (pa(p, a, b)) return p;
  return null;
}

/** Första punkten där polygonens kanter korsar varandra (grannkanter som delar hörn räknas inte), annars null. */
export function sjalvkorsning(pkt: ReadonlyArray<Pkt>): Pkt | null {
  const n = pkt.length;
  if (n < 4) return null;
  const p: Par[] = pkt.map((q) => [q.x, q.y]);
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue; // grannkanter
      const x = strackorSkar(p[i] as Par, p[(i + 1) % n] as Par, p[j] as Par, p[(j + 1) % n] as Par);
      if (x) return { x: x[0], y: x[1] };
    }
  }
  return null;
}

/** Svensk förklaring om polygonen är ogiltig (samma fel som servern ger), annars null. Minst tre hörn krävs för att kontrollera. */
export function polygonFel(pkt: ReadonlyArray<Pkt>): string | null {
  const ren = pkt.filter((q, i) => i === 0 || q.x !== pkt[i - 1]?.x || q.y !== pkt[i - 1]?.y);
  if (ren.length < 3) return "Polygonen behöver minst 3 olika hörn.";
  const x = sjalvkorsning(ren);
  if (x) return `Polygonen korsar sig själv nära E ${formatera(x.x, 0)}, N ${formatera(x.y, 0)}. Rita om den så att kanterna inte skär varandra.`;
  if (ringYta(ren.map((q) => [q.x, q.y] as Par)) === 0) return "Avgränsningen har ingen yta: hörnen ligger på en rak linje.";
  return null;
}

export function tacker(yttre: Rektangel3011, inre: Rektangel3011): boolean {
  return yttre[0] <= inre[0] && yttre[1] <= inre[1] && yttre[2] >= inre[2] && yttre[3] >= inre[3];
}

export function snitt(a: Rektangel3011, b: Rektangel3011): Rektangel3011 | null {
  const r: Rektangel3011 = [Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])];
  return r[2] > r[0] && r[3] > r[1] ? r : null;
}

export type Placering =
  | { slag: "inom" }
  | { slag: "delvis"; procentUtanfor: number }
  | { slag: "utanfor" };

/** Samma regler som servern: utanför yttre gräns är fel, utanför kärnan är en varning. */
export function placering(
  avgr: Rektangel3011,
  g: Pick<GeografiPost, "karna_3011" | "gransen_3011">,
  polygon?: GeoJSON.Polygon | null,
): Placering {
  if (!tacker(g.gransen_3011, avgr)) return { slag: "utanfor" };
  if (!polygon) {
    if (tacker(g.karna_3011, avgr)) return { slag: "inom" };
    const inne = snitt(avgr, g.karna_3011);
    const procent = 100 * (1 - (inne ? yta(inne) : 0) / yta(avgr));
    return { slag: "delvis", procentUtanfor: procent };
  }
  // Polygonen avgör: andelen av dess yta som ligger utanför kärnan (rektangeln runt den kan sticka ut utan att polygonen gör det).
  const total = polygonYta(polygon);
  if (total <= 0) return { slag: "inom" };
  const ring = horn(polygon);
  const innanfor = ytaInomRektangel(ring, g.karna_3011);
  const procent = (100 * (total - innanfor)) / total;
  return procent < 1e-6 ? { slag: "inom" } : { slag: "delvis", procentUtanfor: procent };
}

export function placeringText(p: Placering, gransen: Rektangel3011): { text: string; nivå: "ok" | "varning" | "fel" } {
  if (p.slag === "inom") return { text: "Avgränsningen ligger inom geografins giltiga kärna.", nivå: "ok" };
  if (p.slag === "delvis") {
    return {
      text: `${Math.round(p.procentUtanfor)} % av avgränsningen ligger utanför geografins giltiga kärna. Projektet kan skapas, men full tillrinning och skuggor utifrån är inte garanterade där.`,
      nivå: "varning",
    };
  }
  return {
    text: `Avgränsningen ligger utanför geografins yttre gräns (${gransen.map((v) => Math.round(v)).join(", ")}). Där finns ingen data: rita inom den heldragna linjen.`,
    nivå: "fel",
  };
}

const EPOST = /[^@\s]+@[^@\s]+/;
const PERSONNUMMER = /(?<!\d)(\d{6}|\d{8})[-+]?\d{4}(?!\d)/;

export interface FormularIn {
  geografi: GeografiPost | null;
  namn: string;
  avgransning: Rektangel3011 | null;
  /** Den ritade polygonen (om avgränsningen är en polygon): ytan och giltigheten kontrolleras på den. */
  polygon?: GeoJSON.Polygon | null;
  /** Polygonens hörn som ritats, för kontroll av självkorsning. */
  horn?: ReadonlyArray<Pkt> | null;
  organisation: string | null;
  flera: boolean;
}

/** Det som hindrar "Skapa" (tom lista = går att skapa). */
export function formularFel(f: FormularIn): string[] {
  const fel: string[] = [];
  if (!f.geografi) fel.push("Välj geografi.");
  const n = f.namn.trim();
  if (n === "") fel.push("Ange ett namn.");
  else if (n.length > NAMN_MAX) fel.push(`Namnet får vara högst ${NAMN_MAX} tecken.`);
  else if (EPOST.test(n) || PERSONNUMMER.test(n)) fel.push("Skriv inga personuppgifter i namnet.");
  if (!f.avgransning) fel.push("Rita avgränsningen på kartan.");
  else {
    const pf = f.horn ? polygonFel(f.horn) : null;
    if (pf) fel.push(pf);
    if (!pf && avgransningsYta(f.avgransning, f.polygon) < MIN_YTA_M2) fel.push(`Avgränsningen är för liten (minst ${MIN_YTA_M2} m²).`);
    if (f.geografi && placering(f.avgransning, f.geografi).slag === "utanfor") fel.push("Avgränsningen ligger utanför geografins yttre gräns.");
  }
  if (f.flera && !f.organisation) fel.push("Välj organisation.");
  return fel;
}

export function skapaBegaran(
  geografi: GeografiPost,
  namn: string,
  avgransning: GeoJSON.Polygon | Rektangel3011,
  synlig: boolean,
  organisation: string | null,
): SkapaProjektBegaran {
  return {
    geografi: geografi.ref,
    namn: namn.trim(),
    avgransning,
    organisation_synlig: synlig,
    ...(organisation ? { organisation } : {}),
  };
}

/** "bredd × höjd m, yta": rektangelns mått, och ytan på polygonen om den ritats som polygon (rutnätet är rektangeln runt den). */
export function arealText(r: Rektangel3011, polygon?: GeoJSON.Polygon | null): string {
  const a = avgransningsYta(r, polygon);
  const t = a >= 1e6 ? `${formateraHogst(a / 1e6, 2)} km²` : `${formatera(a, 0)} m²`;
  return `${formatera(r[2] - r[0], 0)} × ${formatera(r[3] - r[1], 0)} m, ${polygon ? "polygonens yta " : ""}${t}`;
}
