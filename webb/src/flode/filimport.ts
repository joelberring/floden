/*
 * "Läs in fil" i Förslag: hus, brytlinjer/öppningar och ritytor ur en GeoJSON-fil (WGS 84 enligt
 * standarden, eller SWEREF 99 18 00 när koordinaterna uppenbart är meter) eller ett sparat förslag
 * (K9-ändringar). Det inlästa går genom samma kontroll hos servern som allt ritat; här görs bara tolkningen.
 */
import { normaliseraRing, type Punkt } from "../karta/geom.ts";
import type { K9Andring } from "../karta/plandok.ts";
import { wgs84TillSweref } from "../sweref.ts";
import { formatera } from "../tal.ts";

export interface InlastHus {
  ring: Punkt[];
  hojd_m: number | null;
}

export type InlastLinjeRoll = "brytlinje" | "oppning";
export type InlastYtaRoll = "mark" | "hardgjort" | "vegetation" | "vatten";

export interface InlastLinje {
  roll: InlastLinjeRoll;
  punkter: Punkt[];
  egenskaper: Record<string, unknown>;
}

export interface InlastYta {
  roll: InlastYtaRoll;
  ring: Punkt[];
  egenskaper: Record<string, unknown>;
}

export interface InlastSammanfattning {
  antalHus: number;
  antalLinjer: number;
  antalYtor: number;
  text: string;
}

export type Inlast =
  | {
      slag: "hus";
      hus: InlastHus[];
      linjer: InlastLinje[];
      ytor: InlastYta[];
      sammanfattning: InlastSammanfattning;
    }
  | { slag: "forslag"; andringar: K9Andring[] }
  | { slag: "fel"; text: string };

type Pos = readonly number[];

const YTA_ROLLER: ReadonlySet<string> = new Set<InlastYtaRoll>(["mark", "hardgjort", "vegetation", "vatten"]);

function konverteraPunkt(k: Pos): Punkt | null {
  const [a, b] = k;
  if (typeof a !== "number" || typeof b !== "number" || !Number.isFinite(a) || !Number.isFinite(b)) return null;
  // Meter (SWEREF) om talen är stora, annars längd och bredd i grader.
  const [x, y] = Math.abs(a) > 1000 || Math.abs(b) > 1000 ? [a, b] : wgs84TillSweref(a, b);
  return { x, y };
}

function tillRing(koord: ReadonlyArray<Pos>): Punkt[] | null {
  const pts: Punkt[] = [];
  for (const k of koord) {
    const p = konverteraPunkt(k);
    if (!p) return null;
    pts.push(p);
  }
  const f = pts[0];
  const s = pts[pts.length - 1];
  if (f && s && pts.length > 1 && f.x === s.x && f.y === s.y) pts.pop();
  return pts.length >= 3 ? normaliseraRing(pts) : null;
}

function tillLinje(koord: ReadonlyArray<Pos>): Punkt[] | null {
  const r = (v: number): number => Math.round(v * 100) / 100;
  const pts: Punkt[] = [];
  for (const k of koord) {
    const p = konverteraPunkt(k);
    if (!p) return null;
    const q = { x: r(p.x), y: r(p.y) };
    const sista = pts[pts.length - 1];
    if (!sista || sista.x !== q.x || sista.y !== q.y) pts.push(q);
  }
  return pts.length >= 2 ? pts : null;
}

function objAv(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function talAv(e: Record<string, unknown>, nycklar: readonly string[]): number | null {
  for (const n of nycklar) {
    const raw = e[n];
    if (raw === undefined || raw === null || raw === "") continue;
    const v = Number(raw);
    if (Number.isFinite(v)) return v;
  }
  return null;
}

function hojdAv(egenskaper: unknown): number | null {
  const e = objAv(egenskaper);
  if (!e) return null;
  const v = talAv(e, ["hojd_m", "hojd", "height"]);
  return v !== null && v > 0 ? v : null;
}

function linjeEgenskaper(egenskaper: unknown): Record<string, unknown> {
  const e = objAv(egenskaper);
  if (!e) return {};
  const ut: Record<string, unknown> = {};
  if (typeof e["typ"] === "string" && e["typ"].trim()) ut["typ"] = e["typ"].trim();
  for (const k of ["hojd_m", "bredd_m", "botten_rh2000", "tolerans_m"] as const) {
    const v = talAv(e, [k]);
    if (v !== null) ut[k] = v;
  }
  if (typeof e["bort"] === "boolean") ut["bort"] = e["bort"];
  return ut;
}

function ytaEgenskaper(egenskaper: unknown): Record<string, unknown> {
  const e = objAv(egenskaper);
  if (!e) return {};
  const ut: Record<string, unknown> = {};
  for (const k of ["delta_m", "niva_rh2000", "hojd_m", "volym_m3", "botten_rh2000", "markslag"] as const) {
    const v = talAv(e, [k]);
    if (v !== null) ut[k] = v;
  }
  const lutning = objAv(e["lutning"]);
  if (lutning) ut["lutning"] = { ...lutning };
  const slant = objAv(e["slant"]);
  if (slant) ut["slant"] = { ...slant };
  return ut;
}

interface Samling {
  hus: InlastHus[];
  linjer: InlastLinje[];
  ytor: InlastYta[];
}

function geometrier(g: unknown, egenskaper: unknown, ut: Samling): void {
  if (!g || typeof g !== "object") return;
  const o = g as { type?: string; coordinates?: unknown; geometries?: unknown[] };
  const e = objAv(egenskaper);
  const roll = typeof e?.["roll"] === "string" ? e["roll"].trim().toLowerCase() : "";

  if (o.type === "Polygon" || o.type === "MultiPolygon") {
    const polygoner: ReadonlyArray<ReadonlyArray<ReadonlyArray<Pos>>> =
      o.type === "Polygon"
        ? [o.coordinates as ReadonlyArray<ReadonlyArray<Pos>>]
        : (o.coordinates as ReadonlyArray<ReadonlyArray<ReadonlyArray<Pos>>>);
    if (YTA_ROLLER.has(roll)) {
      const eg = ytaEgenskaper(egenskaper);
      for (const p of polygoner) {
        const yttre = p?.[0];
        const ring = yttre ? tillRing(yttre) : null;
        if (ring) ut.ytor.push({ roll: roll as InlastYtaRoll, ring, egenskaper: { ...eg } });
      }
    } else {
      const hojd = hojdAv(egenskaper);
      for (const p of polygoner) {
        const yttre = p?.[0];
        const ring = yttre ? tillRing(yttre) : null;
        if (ring) ut.hus.push({ ring, hojd_m: hojd });
      }
    }
    return;
  }

  if (o.type === "LineString" || o.type === "MultiLineString") {
    const linjer: ReadonlyArray<ReadonlyArray<Pos>> =
      o.type === "LineString"
        ? [o.coordinates as ReadonlyArray<Pos>]
        : (o.coordinates as ReadonlyArray<ReadonlyArray<Pos>>);
    const linjeRoll: InlastLinjeRoll = roll === "oppning" ? "oppning" : "brytlinje";
    const eg = linjeEgenskaper(egenskaper);
    for (const l of linjer) {
      const punkter = Array.isArray(l) ? tillLinje(l) : null;
      if (punkter) ut.linjer.push({ roll: linjeRoll, punkter, egenskaper: { ...eg } });
    }
    return;
  }

  if (o.type === "GeometryCollection") {
    for (const x of o.geometries ?? []) geometrier(x, egenskaper, ut);
  }
}

function byggSammanfattning(s: Samling): InlastSammanfattning {
  const delar: string[] = [];
  if (s.hus.length > 0) delar.push(`${formatera(s.hus.length, 0)} hus`);
  if (s.linjer.length > 0) delar.push(`${formatera(s.linjer.length, 0)} ${s.linjer.length === 1 ? "linje" : "linjer"}`);
  if (s.ytor.length > 0) delar.push(`${formatera(s.ytor.length, 0)} ${s.ytor.length === 1 ? "yta" : "ytor"}`);
  return {
    antalHus: s.hus.length,
    antalLinjer: s.linjer.length,
    antalYtor: s.ytor.length,
    text: delar.join(", "),
  };
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
  const samling: Samling = { hus: [], linjer: [], ytor: [] };
  if (o.type === "FeatureCollection") {
    for (const f of o.features ?? []) geometrier((f as { geometry?: unknown })?.geometry, (f as { properties?: unknown })?.properties, samling);
  } else if (o.type === "Feature") geometrier(o.geometry, o.properties, samling);
  else geometrier(d, null, samling);
  if (samling.hus.length === 0 && samling.linjer.length === 0 && samling.ytor.length === 0) {
    return { slag: "fel", text: "Hittade inga polygoner eller linjer i filen." };
  }
  return {
    slag: "hus",
    hus: samling.hus,
    linjer: samling.linjer,
    ytor: samling.ytor,
    sammanfattning: byggSammanfattning(samling),
  };
}
