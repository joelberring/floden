/*
 * Flödets adresser och 3-stegs stegrad: 1 Projekt → 2 Karta & analys → 3 Rapport.
 * `#/` är projektlistan (steg 1), `#/p/<projekt>/karta` är den sammanhållna arbetsytan (steg 2),
 * och `#/p/<projekt>/rapport` är rapportsteget (steg 3).
 *
 * 100 % bakåtkompatibla adresser: `#/p/<projekt>`, `#/p/<projekt>/forslag` och `#/p/<projekt>/resultat`
 * öppnar steg 2 (`Karta & analys`) i passande initialt kartläge; `#/p/<projekt>/fragor` fungerar fortsatt.
 * Ren kod utan DOM, så att den går att testa med Nodes testkörare.
 */
import { esc } from "../sidor.ts";

export type HuvudSteg = "projekt" | "karta" | "rapport";
export type Steg = HuvudSteg | "forslag" | "fragor" | "resultat";

export const STEG: ReadonlyArray<{ id: HuvudSteg; namn: string }> = [
  { id: "projekt", namn: "Projekt" },
  { id: "karta", namn: "Karta & analys" },
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

const PROJEKTSTEG: ReadonlySet<string> = new Set(["karta", "forslag", "fragor", "resultat", "rapport"]);

/** Mappar ett (ev. äldre) flödessteg till motsvarande huvudsteg i den 3-stegs stegraden. */
export function huvudSteg(steg: Steg | null): HuvudSteg | null {
  if (steg === null) return null;
  if (steg === "projekt") return "projekt";
  if (steg === "rapport" || steg === "fragor") return "rapport";
  return "karta";
}

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
  if (steg === undefined) return { steg: "karta", projekt };
  if (steg === "installningar" && delar.length === 3) return { steg: "karta", projekt, installningar: true };
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

/** Stegraden: 3 tydliga steg med riktiga länkar och `aria-current="page"` på aktivt huvudsteg. */
export function stegradHtml(projekt: string | null, aktivt: Steg | null): string {
  const valt = huvudSteg(aktivt);
  const lankar = STEG.map((s, i) => {
    const pa = s.id === valt;
    return `<a class="flode-steg${pa ? " aktivt" : ""}" href="${flodeAdress(projekt, s.id)}"${pa ? ' aria-current="page"' : ""}><span class="flode-steg-nr">${i + 1}</span> ${esc(s.namn)}</a>`;
  }).join("");
  return `<nav class="flode-stegrad" aria-label="Steg">${lankar}</nav>`;
}

export function nastaSteg(aktivt: Steg): HuvudSteg | null {
  const h = huvudSteg(aktivt);
  const i = STEG.findIndex((s) => s.id === h);
  return STEG[i + 1]?.id ?? null;
}
