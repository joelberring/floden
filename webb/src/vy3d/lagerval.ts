/*
 * Vad som visas på vilken sida i 3D, för varje läge (före, efter, skillnad, svep, sida vid sida).
 * Ren logik utan WebGL: läget och körningens lager ger en konfiguration per sida. Alla värden
 * som visas kommer ur körningens lager och fasadresultat; här väljs bara vilka.
 */
import type { LagerPost } from "../fragor-typer.ts";

export type Lage = "fore" | "efter" | "skillnad" | "svep" | "sidavid";

export interface LagerUrval {
  fore: LagerPost | null;
  efter: LagerPost | null;
  skillnad: LagerPost | null;
}

export interface FasadVal {
  /** Scenariot vars VSC visas. */
  scenario: string;
  /** Scenariot som skillnaden räknas mot (efter minus detta), annars null. */
  jamfor: string | null;
}

export interface SidKonfig {
  /** Scenariot vars byggnader ritas (null = projektets bas). */
  scen: string | null;
  mark: LagerPost | null;
  fasad: FasadVal | null;
  etikett: string;
}

export interface Uppdelning {
  a: SidKonfig;
  b: SidKonfig | null;
  delning: "ingen" | "svep" | "sida";
}

const scenarioAv = (l: LagerPost | null): string | null => (l ? l.scenario : null);

export function valjSidor(lage: Lage, lager: LagerUrval, fasadScenarier: readonly string[]): Uppdelning {
  const foreScen = scenarioAv(lager.fore);
  const efterScen = scenarioAv(lager.efter) ?? (lager.skillnad ? (lager.skillnad.efter ?? lager.skillnad.scenario) : null);
  const harFasad = (s: string | null): s is string => s !== null && fasadScenarier.includes(s);
  const sida = (scen: string | null, mark: LagerPost | null, jamfor: string | null, etikett: string): SidKonfig => ({
    scen,
    mark,
    fasad: harFasad(scen) ? { scenario: scen, jamfor: jamfor !== null && harFasad(jamfor) ? jamfor : null } : null,
    etikett,
  });
  const fore = sida(foreScen, lager.fore, null, "Före");
  const efter = sida(efterScen, lager.efter, null, "Efter");
  switch (lage) {
    case "fore":
      return { a: fore, b: null, delning: "ingen" };
    case "efter":
      return { a: efter, b: null, delning: "ingen" };
    case "skillnad":
      return { a: sida(efterScen, lager.skillnad, foreScen, "Skillnad (efter minus före)"), b: null, delning: "ingen" };
    case "svep":
      return { a: fore, b: efter, delning: "svep" };
    case "sidavid":
      return { a: fore, b: efter, delning: "sida" };
  }
}

/** Tidssteg för vatten: lager med roll `djup_t<minut>` i samma analys och scenario. */
export interface TidSteg {
  lager: string;
  minut: number;
}

/** Lagret för ett tidsreglage (0..1 av reglagets längd); utan tidsserie används maxdjupet. */
export function lagerForTid(steg: readonly TidSteg[] | undefined, reglage: number, maxdjup: string | null): { lager: string | null; minut: number | null } {
  if (!steg || steg.length === 0) return { lager: maxdjup, minut: null };
  const i = Math.min(steg.length - 1, Math.max(0, Math.round(reglage * (steg.length - 1))));
  const s = steg[i] as TidSteg;
  return { lager: s.lager, minut: s.minut };
}

/** Är lagret ett maxdjup (vatten) som ritas som yta snarare än som färg på marken? */
export const arVattenYta = (l: LagerPost | null): boolean => l !== null && l.typ !== "skillnad" && /^(max_djup|djup_t\d+)$/.test(l.roll);
