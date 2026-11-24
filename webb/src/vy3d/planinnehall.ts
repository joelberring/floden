/*
 * Det ritade förslaget som 3D-vyn ritar ovanpå basen (R6): hus (med takform), ny mark med slänter och murar.
 * Läses ur planförslagets dokument (`PlanDok`) och ritlagren (K50); här görs bara ett urval och en omformning,
 * ingenting räknas. Lager som är släckta ritas inte.
 */
import type { PlanDok } from "../karta/plandok.ts";
import type { PlanHus, PlanInnehall } from "./scen.ts";
import type { MarkRoll, PlanMark, PlanMur } from "./planyta.ts";

/** Det 3D-vyn behöver veta om sidan: dokumentet, basens byggnader och om förslaget redan visas som resultat. */
export interface PlanKalla {
  dok: PlanDok;
  info: { byggnader: ReadonlyArray<{ id: string; ring: Array<[number, number]>; hal: Array<Array<[number, number]>>; hojd_m: number }> };
  /** Visar resultatet av ett ritat förslag (då ritas förslaget inte om en gång till som överlägg). */
  arRitatResultat(): boolean;
  /** Takform per nytt hus (id -> K9-takform), bara avvikelser från förvalet. */
  takform?(): ReadonlyMap<string, string>;
}

/** Förslagets hus (nya, höjda, rivna) som scenen ritar direkt: höjder över marken, inget beräknat. */
export function planHus(k: PlanKalla): PlanHus[] {
  const d = k.dok;
  if (k.arRitatResultat()) return [];
  const ut: PlanHus[] = [];
  const bas = new Map(k.info.byggnader.map((b) => [b.id, b]));
  const tak = k.takform?.();
  for (const h of d.ny.values()) {
    if (!d.arSynligt(h)) continue;
    const post: PlanHus = { slag: "ny", ringar: [h.ring.map((p) => [p.x, p.y] as [number, number])], hojd_m: h.hojd_m };
    const tf = tak?.get(h.id);
    if (tf !== undefined) post.takform = tf;
    ut.push(post);
  }
  for (const [id, hojd] of d.hojd) {
    const b = bas.get(id);
    if (b) ut.push({ slag: "andrad", ringar: [b.ring, ...b.hal], hojd_m: hojd });
  }
  for (const id of d.bort) {
    const b = bas.get(id);
    if (b) ut.push({ slag: "bort", ringar: [b.ring, ...b.hal], hojd_m: b.hojd_m });
  }
  return ut;
}

const MARKROLLER: ReadonlySet<string> = new Set<MarkRoll>(["mark", "hardgjort", "vegetation", "vatten"]);

/** Ritade markytor (ny mark, hårdgjort, vegetation, vatten) i tända lager, med sina egenskaper (nivå, lutning, slänt). */
export function planMark(k: PlanKalla): PlanMark[] {
  if (k.arRitatResultat()) return [];
  const d = k.dok;
  const ut: PlanMark[] = [];
  for (const f of [...d.figurer.values()].sort((a, b) => a.id.localeCompare(b.id))) {
    const roll = d.lagerForFigur(f)?.roll;
    if (f.typ !== "yta" || roll === undefined || !MARKROLLER.has(roll) || !d.arFigurSynlig(f) || f.punkter.length < 3) continue;
    ut.push({ id: f.id, roll: roll as MarkRoll, ring: f.punkter.map((p) => [p.x, p.y] as [number, number]), egenskaper: f.egenskaper });
  }
  return ut;
}

/** Ritade murar och kanter (brytlinjer) i tända lager: typ och höjd över marken. Linjer som bara tar bort basens brytlinjer ritas inte. */
export function planMurar(k: PlanKalla): PlanMur[] {
  if (k.arRitatResultat()) return [];
  const d = k.dok;
  const ut: PlanMur[] = [];
  for (const f of [...d.figurer.values()].sort((a, b) => a.id.localeCompare(b.id))) {
    const e = f.egenskaper;
    if (f.typ !== "linje" || d.lagerForFigur(f)?.roll !== "brytlinje" || !d.arFigurSynlig(f) || e.bort === true || f.punkter.length < 2) continue;
    const hojd = typeof e.hojd_m === "number" ? e.hojd_m : Number.NaN;
    if (!(hojd > 0)) continue;
    ut.push({ id: f.id, typ: typeof e.typ === "string" ? e.typ : "mur", hojd_m: hojd, linje: f.punkter.map((p) => [p.x, p.y] as [number, number]) });
  }
  return ut;
}

/** Hela det ritade förslaget för 3D-vyn. */
export function planInnehall(k: PlanKalla): PlanInnehall {
  return { hus: planHus(k), mark: planMark(k), murar: planMurar(k) };
}
