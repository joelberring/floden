/*
 * Flödets adresser och stegrad: 1 Projekt → 2 Förslag → 3 Frågor → 4 Resultat → 5 Rapport.
 * `#/` är projektlistan (steg 1), `#/p/<projekt>/<steg>` är stegen i ett projekt. Ren kod utan DOM,
 * så att den går att testa med Nodes testkörare.
 */
import { esc } from "../sidor.ts";

export type Steg = "projekt" | "forslag" | "fragor" | "resultat" | "rapport";

export const STEG: ReadonlyArray<{ id: Steg; namn: string }> = [
  { id: "projekt", namn: "Projekt" },
  { id: "forslag", namn: "Förslag" },
  { id: "fragor", namn: "Frågor" },
  { id: "resultat", namn: "Resultat" },
  { id: "rapport", namn: "Rapport" },
];

export interface FlodeRutt {
  steg: Steg;
  /** Projektets id; null i projektlistan. */
  projekt: string | null;
  /** Sant på `#/nytt`: vyn där ett nytt projekt skapas (hör till steg 1). */
  nytt?: true;
  /** Sant på `#/p/<projekt>/installningar`: projektets inställningar (nås från projektet och från projektlistan). */
  installningar?: true;
  /** Sant på `#/?arkiverade=1`: projektlistan visar de arkiverade projekten. */
  arkiverade?: true;
}

export const NYTT_PROJEKT_ADRESS = "#/nytt";

const PROJEKTSTEG: ReadonlySet<string> = new Set(["forslag", "fragor", "resultat", "rapport"]);

/** `#/` och `#/p/<projekt>[/<steg>]` → rutt; annat (t.ex. `#/om`) → null. */
export function tolkaFlodeAdress(hash: string): FlodeRutt | null {
  const utan = hash.replace(/^#/, "");
  const [sokvag = "", fraga = ""] = utan.split("?", 2);
  const delar = sokvag.split("/").filter((d) => d.length > 0);
  if (delar.length === 0) {
    return new URLSearchParams(fraga).get("arkiverade") === "1" ? { steg: "projekt", projekt: null, arkiverade: true } : { steg: "projekt", projekt: null };
  }
  if (delar.length === 1 && delar[0] === "nytt") return { steg: "projekt", projekt: null, nytt: true };
  if (delar[0] !== "p" || !delar[1]) return null;
  let projekt: string;
  try {
    projekt = decodeURIComponent(delar[1]);
  } catch {
    return null;
  }
  const steg = delar[2];
  if (steg === undefined) return { steg: "forslag", projekt };
  if (steg === "installningar" && delar.length === 3) return { steg: "forslag", projekt, installningar: true };
  if (!PROJEKTSTEG.has(steg) || delar.length > 3) return null;
  return { steg: steg as Steg, projekt };
}

/** Adressen till ett steg. Projekt-steget är alltid listan. */
export function flodeAdress(projekt: string | null, steg: Steg): string {
  if (steg === "projekt" || projekt === null) return "#/";
  return `#/p/${encodeURIComponent(projekt)}/${steg}`;
}

/** Adressen till projektets inställningar. */
export function installningarAdress(projekt: string): string {
  return `#/p/${encodeURIComponent(projekt)}/installningar`;
}

/** Stegraden: riktiga länkar, `aria-current="page"` på aktivt steg. */
export function stegradHtml(projekt: string | null, aktivt: Steg | null): string {
  const lankar = STEG.map((s, i) => {
    const pa = s.id === aktivt;
    return `<a class="flode-steg${pa ? " aktivt" : ""}" href="${flodeAdress(projekt, s.id)}"${pa ? ' aria-current="page"' : ""}><span class="flode-steg-nr">${i + 1}</span> ${esc(s.namn)}</a>`;
  }).join("");
  return `<nav class="flode-stegrad" aria-label="Steg">${lankar}</nav>`;
}

export function nastaSteg(aktivt: Steg): Steg | null {
  const i = STEG.findIndex((s) => s.id === aktivt);
  return STEG[i + 1]?.id ?? null;
}
