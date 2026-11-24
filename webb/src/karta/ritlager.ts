/*
 * Ritlager (K50 `floden-ritlager/v1`) i klienten: typer och rena funktioner. Ett ritlager har en roll i
 * beräkningen (byggnad, mark, hårdgjort, vegetation, vatten, brytlinje, öppning); servern avgör vad ett
 * objekt blir i K9/K1 efter rollen (`karna.ritlager.oversatt`). Klienten ritar bara geometri och väljer
 * lager, och räknar inget. Stil är bara visning.
 */
import { formatera } from "../tal.ts";

export type Roll = "byggnad" | "mark" | "hardgjort" | "vegetation" | "vatten" | "brytlinje" | "oppning";
export type Geometrityp = "polygon" | "linje" | "punkt";
export type Dataklass = "oppen" | "intern" | "skyddad";

export const KONTRAKT_RITLAGER = "floden-ritlager/v1";

export interface RitLager {
  lager_id: string;
  namn: string;
  geometri: Geometrityp;
  roll: Roll;
  stil: { fyllning?: string; linje?: string; linjebredd_px?: number; monster?: string | null };
  synlig: boolean;
  last: boolean;
  tonad: boolean;
  opacitet: number;
  kalla: string;
  dataklass: Dataklass;
  egenskaper_mall: Record<string, string>;
  egenskaper_forval: Record<string, unknown>;
}

export interface RitObjekt {
  objekt_id: string;
  lager_id: string;
  geometri: { type: string; coordinates: unknown };
  egenskaper: Record<string, unknown>;
}

export interface RitlagerDok {
  kontrakt: typeof KONTRAKT_RITLAGER;
  lager?: RitLager[];
  objekt: RitObjekt[];
}

/** Vad en roll är och vad den blir i beräkningen (kommer från servern, `roller` i projektinfo). */
export interface RollInfo {
  roll: Roll;
  namn: string;
  geometri: Geometrityp[];
  blir: string;
  egenskaper: string[];
}

/** Roller som klientens ritverktyg kan rita: hus med husverktyget, övriga med linje- och ytverktyget (R3). */
export const RITBARA_ROLLER: ReadonlySet<Roll> = new Set<Roll>(["byggnad", "mark", "hardgjort", "vegetation", "vatten", "brytlinje", "oppning"]);

export const kopiaLager = (l: RitLager): RitLager => ({
  ...l,
  stil: { ...l.stil },
  egenskaper_mall: { ...l.egenskaper_mall },
  egenskaper_forval: { ...l.egenskaper_forval },
});

/** Lagrets dataklass är inte öppen: det lämnas inte ut via MCP. */
export const arStangt = (l: RitLager): boolean => l.dataklass !== "oppen" || /^(baskarta:|ledning|lokal:)/.test(l.kalla);

/** Nästa lediga lager-id för en roll: `vatten-2`, `vatten-3`, ... (a-z, siffror och bindestreck som servern kräver). */
export function nyttLagerId(lager: readonly RitLager[], roll: Roll): string {
  const finns = new Set(lager.map((l) => l.lager_id));
  let n = 2;
  while (finns.has(`${roll}-${n}`)) n++;
  return `${roll}-${n}`;
}

/** Ett nytt tomt lager av en roll, med stil från rollens första befintliga lager (eller en neutral). */
export function nyttLager(lager: readonly RitLager[], roll: Roll, namn: string, info: readonly RollInfo[]): RitLager {
  const mall = lager.find((l) => l.roll === roll);
  const ri = info.find((r) => r.roll === roll);
  const geometri: Geometrityp = mall?.geometri ?? ri?.geometri[0] ?? "polygon";
  return {
    lager_id: nyttLagerId(lager, roll),
    namn: namn.trim() || `${ri?.namn ?? roll} ${formatera(lager.filter((l) => l.roll === roll).length + 1, 0)}`,
    geometri,
    roll,
    stil: mall ? { ...mall.stil } : { fyllning: "#cccccc", linje: "#666666", linjebredd_px: 2 },
    synlig: true,
    last: false,
    tonad: false,
    opacitet: 1,
    kalla: "ritat",
    dataklass: "oppen",
    egenskaper_mall: mall ? { ...mall.egenskaper_mall } : {},
    egenskaper_forval: mall ? { ...mall.egenskaper_forval } : {},
  };
}
