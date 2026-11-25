/* Förklaringar av vad man tittar på i kartan ("Så läser du kartan"). Ren text, inga beroenden. */
import type { LagerPost } from "./fragor-typer.ts";
import type { ProjektVy } from "./types.ts";

export type Lage = "fore" | "efter" | "skillnad" | "svep" | "3d";

export const LAGEN: Array<[Lage, string]> = [
  ["fore", "Före"],
  ["efter", "Efter"],
  ["skillnad", "Skillnad"],
  ["svep", "Svep"],
  ["3d", "3D"],
];

export const OSAKERHET_FORE_NOTIS = "Osäkerhetsintervall avser läget före (projektets markmodell).";

/** Kort notis till kartans teckenförklaring när osäkerhetsintervall visas. */
export function osakerhetNotisText(osakerhetFore: boolean): string {
  return osakerhetFore ? OSAKERHET_FORE_NOTIS : "";
}

/** Kort förklaring av vad man tittar på: 2–3 meningar, anpassad efter vy och läge. */
export function lasKartan(vy: ProjektVy, lage: Lage, paahittat = true): string {
  const avsnitt = vy.avsnitt;
  const farger =
    avsnitt === "dagsljus"
      ? "Färgerna visar hur många timmar solen når marken en dag: mörkblått är få timmar och gult är många."
      : avsnitt === "ekologi"
        ? "Färgerna visar strömtäthet och viktiga spridningsstråk i landskapet: ljusare nyanser markerar flaskhalsar."
        : avsnitt === "luft"
          ? "Färgerna visar luftmiljö på gånghöjd (vindkomfort, vindfara, vindförstärkning eller årsmedelhalt) enligt teckenförklaringen."
          : "Blå ytor visar var regnvatten blir stående och hur djupt: ju mörkare blått, desto djupare. Mark utan färg är torr.";
  const grått = "Ljusgrå ytor är byggnader (tak, inte mark), där visas inget markvärde. Den skogsgröna linjen visar projektets avgränsning.";
  const lagestext: Record<Lage, string> = {
    fore: "Före är kvarteret som det är utan förändringen.",
    efter: `Efter är kvarteret med förändringen (${vy.efter_namn.toLowerCase()}).`,
    skillnad:
      avsnitt === "dagsljus"
        ? "Skillnad visar bara det som ändrats: blått är färre soltimmar, rött är fler."
        : avsnitt === "luft"
          ? "Skillnad visar bara det som ändrats mellan före och efter: blått är minskning, rött är ökning."
          : "Skillnad visar bara det som ändrats: rött är djupare vatten efter förändringen, blått är grundare.",
    svep: "Dra reglaget under kartan: till vänster syns före, till höger efter.",
    "3d": "I 3D visas skillnaden: kameran följer kartan, och fasaderna kan färgas med värdet.",
  };
  return `${farger} ${grått} ${lagestext[lage]}${paahittat ? " Kvarteret är påhittat." : ""}`;
}


/** Kort förklaring av kartan för det valda lagret (2–3 meningar). */
export function lasKartanText(l: LagerPost | null, paahittat: boolean): string {
  const farger =
    l?.roll === "soltimmar"
      ? "Färgerna visar hur många timmar solen når marken: mörkblått är få timmar och gult är många."
      : l?.roll === "max_djup"
        ? "Blå ytor visar var regnvatten blir stående och hur djupt: ju mörkare blått, desto djupare."
        : l?.roll === "ankomsttid"
          ? "Färgerna visar ankomsttid (minuter från regnstart tills vattendjupet når 0,1 m): ljusgult är snabbt, mörklila är sent."
          : l?.roll === "hastighet"
            ? "Färgerna visar vattnets strömningshastighet i m/s: mörkare är långsammare och ljusgult är snabbast."
            : l?.roll === "komfortklass" || l?.roll === "faraklass" || l?.roll === "forstarkning" || l?.roll === "arsmedel"
              ? "Färgerna visar beräknad luft- eller vindmiljö på gånghöjd (skissmetod); klasser och enheter står i teckenförklaringen."
              : "Färgerna visar värdet i varje cell; skalan står under rubriken Lager.";
  const typ =
    l?.typ === "skillnad"
      ? "Skillnad visar bara det som ändrats: blått är en minskning och rött en ökning (efter minus före)."
      : "Före är läget som det är, efter är med förändringen; byt lager för att jämföra.";
  return `${farger} Ljusgrå ytor är byggnader (tak, inte mark). ${typ}${paahittat ? " Kvarteret är påhittat." : ""}`;
}
