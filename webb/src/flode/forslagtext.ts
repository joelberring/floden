/*
 * Texten i Förslags infopanel som ren funktion: antal ändringar, det markerade husets yta, våningar och
 * bruttoarea (en uppskattning, ingen beräkning), serverns kontroll och om förslaget är sparat.
 * Alla tal går genom `tal.ts`. Ingen DOM, så att texten går att testa.
 */
import { yta, type Punkt } from "../karta/geom.ts";
import { esc } from "../sidor.ts";
import { formatera } from "../tal.ts";

export interface HusUppgift {
  yta_m2: number;
  vaningar: number;
  bta_m2: number;
}

export interface ForslagInfo {
  antal: number;
  hus: HusUppgift | null;
  kontrollFel: string | null;
  fel: readonly string[];
  varningar: readonly string[];
  information: readonly string[];
  sparFel: string | null;
  /** Namnet på det sparade förslaget när allt är grönt och sparat; annars null. */
  sparatSom: string | null;
}

const andringar = (n: number): string => `${formatera(n, 0)} ändring${n === 1 ? "" : "ar"}`;

export function forslagInfoHtml(i: ForslagInfo): string {
  if (i.kontrollFel) return `<p class="flode-fel" role="alert">${esc(i.kontrollFel)}</p>`;
  const delar: string[] = [];
  if (i.hus) {
    delar.push(
      `<p>Yta ${formatera(i.hus.yta_m2, 0)} m², ${formatera(i.hus.vaningar, 0)} våningar, bruttoarea ungefär ${formatera(i.hus.bta_m2, 0)} m² (uppskattning, ingen beräkning).</p>`,
    );
  }
  delar.push(
    i.antal === 0
      ? `<p>Inget ritat än. Rita ett hus eller markera en byggnad för att ändra den.</p>`
      : `<p>${andringar(i.antal)} i förslaget.</p>`,
  );
  for (const f of i.fel) delar.push(`<p class="flode-fel" role="alert">${esc(f)}</p>`);
  for (const f of i.varningar) delar.push(`<p class="flode-varning">${esc(f)}</p>`);
  for (const f of i.information) delar.push(`<p class="flode-dampad">${esc(f)}</p>`);
  if (i.sparFel) delar.push(`<p class="flode-fel" role="alert">${esc(i.sparFel)}</p>`);
  else if (i.sparatSom) delar.push(`<p class="flode-dampad">Sparat som ${esc(i.sparatSom)} på den här datorn.</p>`);
  return delar.join("");
}

/** Förslagets sparstatus bredvid knappen "Spara förslag". */
export type SparTyp = "tomt" | "sparat" | "osparat" | "sparar" | "fel" | "ogiltigt";

export interface SparIn {
  /** Antal ändringar och figurer i förslaget. */
  antal: number;
  /** Förslagets innehåll och namn är det som senast sparades. */
  likaSomSparat: boolean;
  /** Namnet på det sparade förslaget (filen på datorn), om något sparats. */
  sparatNamn: string | null;
  /** Klockslag (redan formaterat) för senaste lyckade sparning i den här sessionen. */
  tid: string | null;
  /** Kontrollen: true giltig, false fel, null ej svarat (pågår eller ej startad). */
  giltig: boolean | null;
  kontrollFel: string | null;
  sparFel: string | null;
  sparar: boolean;
}

export function sparStatus(i: SparIn): { typ: SparTyp; text: string } {
  if (i.sparar) return { typ: "sparar", text: "Sparar …" };
  if (i.sparFel) return { typ: "fel", text: `Kunde inte spara: ${i.sparFel}` };
  if (i.antal === 0) {
    return {
      typ: "tomt",
      text: i.sparatNamn ? `Förslaget är tomt. Det sparade förslaget ${i.sparatNamn} finns kvar som det var.` : "Inget att spara än. Rita något först.",
    };
  }
  if (i.kontrollFel || i.giltig === false) {
    return { typ: "ogiltigt", text: "Kan inte sparas än: förslaget har fel som måste rättas först (se nedan). Det som sparades senast finns kvar." };
  }
  if (i.likaSomSparat && i.sparatNamn) {
    return { typ: "sparat", text: `Sparat som ${i.sparatNamn}${i.tid ? ` kl. ${i.tid}` : ""} på den här datorn.` };
  }
  return { typ: "osparat", text: i.giltig === null ? "Osparade ändringar. Kontrollerar förslaget …" : "Osparade ändringar. Tryck Spara förslag." };
}

export interface MarkYtaIn {
  /** Ritlagrets roll (enbart `"mark"` ingår; om utelämnad antas `"mark"`). */
  roll?: string;
  /** Öppen ring i SWEREF 99 18 00 (meter). Används om `yta_m2` inte anges direkt. */
  punkter?: readonly Punkt[];
  /** Färdigberäknad yta i m². */
  yta_m2?: number;
  egenskaper?: Record<string, unknown>;
}

export interface MarkVolym {
  /** Summa av `yta_m2 * delta_m` för markytor med `delta_m > 0` (m³). */
  fyllnad_m3: number;
  /** Summa av `yta_m2 * |delta_m|` för markytor med `delta_m < 0` (m³, positivt tal). */
  schakt_m3: number;
  /** `fyllnad_m3 - schakt_m3` (m³). */
  netto_m3: number;
  /** Antal markytor med explicit `delta_m` som ingick i summan. */
  antal_beraknade: number;
  /** Antal markytor med absolut nivå (`niva_rh2000`) eller `lutning` som kräver serverns terrängmodell. */
  ej_berakningsbara: number;
}

/**
 * Deterministisk schakt- och fyllnadsvolym för ritade `mark`-ytor med explicit `delta_m`.
 * Gissar aldrig för ytor med `niva_rh2000` eller `lutning` (de kräver serverns terrängmodell och räknas i `ej_berakningsbara`).
 */
export function beraknaMarkvolym(ytor: readonly MarkYtaIn[]): MarkVolym {
  let fyllnad_m3 = 0;
  let schakt_m3 = 0;
  let antal_beraknade = 0;
  let ej_berakningsbara = 0;

  for (const y of ytor) {
    if (y.roll !== undefined && y.roll !== "mark") continue;
    const e = y.egenskaper ?? {};
    const delta = e["delta_m"];
    if (typeof delta === "number" && Number.isFinite(delta)) {
      const area =
        typeof y.yta_m2 === "number" && Number.isFinite(y.yta_m2) && y.yta_m2 >= 0
          ? y.yta_m2
          : y.punkter
            ? yta(y.punkter)
            : 0;
      const v = area * delta;
      if (v > 0) fyllnad_m3 += v;
      else if (v < 0) schakt_m3 += -v;
      antal_beraknade++;
    } else {
      const harNiva = typeof e["niva_rh2000"] === "number" && Number.isFinite(e["niva_rh2000"]);
      const harLutning = Boolean(e["lutning"] && typeof e["lutning"] === "object");
      if (harNiva || harLutning) ej_berakningsbara++;
    }
  }

  return {
    fyllnad_m3,
    schakt_m3,
    netto_m3: fyllnad_m3 - schakt_m3,
    antal_beraknade,
    ej_berakningsbara,
  };
}

/** Kompakt formatering av schakt- och fyllnadsvolym via `../tal.ts`. */
export function markvolymText(v: MarkVolym): string {
  if (v.antal_beraknade === 0 && v.ej_berakningsbara === 0) return "";
  const delar: string[] = [];
  if (v.antal_beraknade > 0) {
    const nettoPrefix = v.netto_m3 > 0 ? "+" : "";
    delar.push(
      `Fyllnad ${formatera(v.fyllnad_m3, 0)} m³ · Schakt ${formatera(v.schakt_m3, 0)} m³ · Netto ${nettoPrefix}${formatera(v.netto_m3, 0)} m³`,
    );
  }
  if (v.ej_berakningsbara > 0) {
    const n = formatera(v.ej_berakningsbara, 0);
    delar.push(`${n} ${v.ej_berakningsbara === 1 ? "markyta" : "markytor"} med nivå/lutning kräver terrängmodell`);
  }
  return delar.join(" · ");
}
