/* Förklaringar av vad man tittar på i kartan ("Så läser du kartan"). Ren text, inga beroenden. */
import type { LagerPost } from "./fragor-typer.ts";
import type { ProjektVy } from "./types.ts";

export type Lage = "fore" | "efter" | "skillnad" | "svep" | "sidavid";

export const LAGEN: Array<[Lage, string]> = [
  ["fore", "Före"],
  ["efter", "Efter"],
  ["skillnad", "Skillnad"],
  ["svep", "Svep"],
  ["sidavid", "Sida vid sida"],
];

/** Kort förklaring av vad man tittar på: 2–3 meningar, anpassad efter vy och läge. */
export function lasKartan(vy: ProjektVy, lage: Lage): string {
  const dagsljus = vy.avsnitt === "dagsljus";
  const farger = dagsljus
    ? "Färgerna visar hur många timmar solen når marken en dag: mörkblått är få timmar och gult är många."
    : "Blå ytor visar var regnvatten blir stående och hur djupt: ju mörkare blått, desto djupare. Mark utan färg är torr.";
  const grått = "Ljusgrå ytor är byggnader (tak, inte mark), där visas inget markvärde.";
  const lagestext: Record<Lage, string> = {
    fore: "Före är kvarteret som det är utan förändringen.",
    efter: `Efter är kvarteret med förändringen (${vy.efter_namn.toLowerCase()}).`,
    skillnad: dagsljus
      ? "Skillnad visar bara det som ändrats: blått är färre soltimmar, rött är fler."
      : "Skillnad visar bara det som ändrats: rött är djupare vatten efter förändringen, blått är grundare.",
    svep: "Dra reglaget under kartan: till vänster syns före, till höger efter.",
    sidavid: "Före till vänster och efter till höger; kartorna följs åt när du flyttar eller zoomar.",
  };
  return `${farger} ${grått} ${lagestext[lage]} Kvarteret är påhittat.`;
}


/** Kort förklaring av kartan för det valda lagret (2–3 meningar). */
export function lasKartanText(l: LagerPost | null, paahittat: boolean): string {
  const farger =
    l?.roll === "soltimmar"
      ? "Färgerna visar hur många timmar solen når marken: mörkblått är få timmar och gult är många."
      : l?.roll === "max_djup"
        ? "Blå ytor visar var regnvatten blir stående och hur djupt: ju mörkare blått, desto djupare."
        : "Färgerna visar värdet i varje cell; skalan står under rubriken Lager.";
  const typ =
    l?.typ === "skillnad"
      ? "Skillnad visar bara det som ändrats: blått är en minskning och rött en ökning (efter minus före)."
      : "Före är läget som det är, efter är med förändringen; byt lager för att jämföra.";
  return `${farger} Ljusgrå ytor är byggnader (tak, inte mark). ${typ}${paahittat ? " Kvarteret är påhittat." : ""}`;
}
