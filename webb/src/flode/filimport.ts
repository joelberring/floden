/*
 * "Läs in fil" i Förslag: hus ur en GeoJSON-fil (WGS 84 enligt standarden, eller SWEREF 99 18 00
 * när koordinaterna uppenbart är meter) eller ett sparat förslag (K9-ändringar). Det inlästa går
 * genom samma kontroll hos servern som allt ritat; här görs bara tolkningen.
 */
import { normaliseraRing, type Punkt } from "../karta/geom.ts";
import type { K9Andring } from "../karta/plandok.ts";
import { wgs84TillSweref } from "../sweref.ts";

export interface InlastHus {
  ring: Punkt[];
  hojd_m: number | null;
}

export type Inlast =
  | { slag: "hus"; hus: InlastHus[] }
  | { slag: "forslag"; andringar: K9Andring[] }
  | { slag: "fel"; text: string };

type Pos = readonly number[];

function tillRing(koord: ReadonlyArray<Pos>): Punkt[] | null {
  const pts: Punkt[] = [];
  for (const k of koord) {
    const [a, b] = k;
    if (typeof a !== "number" || typeof b !== "number" || !Number.isFinite(a) || !Number.isFinite(b)) return null;
    // Meter (SWEREF) om talen är stora, annars längd och bredd i grader.
    const [x, y] = Math.abs(a) > 1000 || Math.abs(b) > 1000 ? [a, b] : wgs84TillSweref(a, b);
    pts.push({ x, y });
  }
  const f = pts[0];
  const s = pts[pts.length - 1];
  if (f && s && pts.length > 1 && f.x === s.x && f.y === s.y) pts.pop();
  return pts.length >= 3 ? normaliseraRing(pts) : null;
}

function hojdAv(egenskaper: unknown): number | null {
  if (!egenskaper || typeof egenskaper !== "object") return null;
  const e = egenskaper as Record<string, unknown>;
  for (const n of ["hojd_m", "hojd", "height"]) {
    const v = Number(e[n]);
    if (e[n] !== undefined && e[n] !== null && e[n] !== "" && Number.isFinite(v) && v > 0) return v;
  }
  return null;
}

function geometrier(g: unknown, egenskaper: unknown, ut: InlastHus[]): void {
  if (!g || typeof g !== "object") return;
  const o = g as { type?: string; coordinates?: unknown; geometries?: unknown[] };
  const hojd = hojdAv(egenskaper);
  const polygoner: ReadonlyArray<ReadonlyArray<ReadonlyArray<Pos>>> =
    o.type === "Polygon"
      ? [o.coordinates as ReadonlyArray<ReadonlyArray<Pos>>]
      : o.type === "MultiPolygon"
        ? (o.coordinates as ReadonlyArray<ReadonlyArray<ReadonlyArray<Pos>>>)
        : [];
  for (const p of polygoner) {
    const yttre = p?.[0];
    const ring = yttre ? tillRing(yttre) : null;
    if (ring) ut.push({ ring, hojd_m: hojd });
  }
  if (o.type === "GeometryCollection") for (const x of o.geometries ?? []) geometrier(x, egenskaper, ut);
}

export function tolkaFil(text: string): Inlast {
  let d: unknown;
  try {
    d = JSON.parse(text);
  } catch {
    return { slag: "fel", text: "Filen är inte giltig JSON. Läs in en GeoJSON-fil eller ett sparat förslag." };
  }
  if (!d || typeof d !== "object") return { slag: "fel", text: "Filen innehåller inga hus." };
  const o = d as { type?: string; features?: unknown[]; andringar?: unknown; geometry?: unknown; properties?: unknown };
  if (Array.isArray(o.andringar)) {
    const ok = o.andringar.every((a) => a && typeof a === "object" && typeof (a as K9Andring).typ === "string");
    return ok ? { slag: "forslag", andringar: o.andringar as K9Andring[] } : { slag: "fel", text: "Förslagsfilen har ändringar som inte går att tolka." };
  }
  const hus: InlastHus[] = [];
  if (o.type === "FeatureCollection") {
    for (const f of o.features ?? []) geometrier((f as { geometry?: unknown })?.geometry, (f as { properties?: unknown })?.properties, hus);
  } else if (o.type === "Feature") geometrier(o.geometry, o.properties, hus);
  else geometrier(d, null, hus);
  if (hus.length === 0) return { slag: "fel", text: "Hittade inga polygoner i filen. Husen ska vara polygoner (Polygon eller MultiPolygon)." };
  return { slag: "hus", hus };
}
