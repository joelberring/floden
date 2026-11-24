/*
 * Nytt projekt: ren logik utan DOM (så att den går att testa med Nodes testkörare). Allt i SWEREF 99 18 00
 * (EPSG:3011, meter). Servern gör de bindande kontrollerna (`POST /api/projekt`, K48); här visas bara samma
 * besked i förväg: ligger avgränsningen inom giltig kärna, delvis utanför (varning) eller utanför yttre gräns (fel).
 */
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
export function placering(avgr: Rektangel3011, g: Pick<GeografiPost, "karna_3011" | "gransen_3011">): Placering {
  if (!tacker(g.gransen_3011, avgr)) return { slag: "utanfor" };
  if (tacker(g.karna_3011, avgr)) return { slag: "inom" };
  const inne = snitt(avgr, g.karna_3011);
  const procent = 100 * (1 - (inne ? yta(inne) : 0) / yta(avgr));
  return { slag: "delvis", procentUtanfor: procent };
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
    if (yta(f.avgransning) < MIN_YTA_M2) fel.push(`Avgränsningen är för liten (minst ${MIN_YTA_M2} m²).`);
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

export function arealText(r: Rektangel3011): string {
  const a = yta(r);
  const t = a >= 1e6 ? `${(a / 1e6).toLocaleString("sv-SE", { maximumFractionDigits: 2 })} km²` : `${Math.round(a).toLocaleString("sv-SE")} m²`;
  return `${Math.round(r[2] - r[0]).toLocaleString("sv-SE")} × ${Math.round(r[3] - r[1]).toLocaleString("sv-SE")} m, ${t}`;
}
