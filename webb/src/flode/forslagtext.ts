/*
 * Texten i Förslags infopanel som ren funktion: antal ändringar, det markerade husets yta, våningar och
 * bruttoarea (en uppskattning, ingen beräkning), serverns kontroll och om förslaget är sparat.
 * Alla tal går genom `tal.ts`. Ingen DOM, så att texten går att testa.
 */
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
