/*
 * Steg 3 · handlingsraden längst ned (sticky): ren logik och HTML utan DOM. Raden säger vad som händer eller
 * varför man inte kan köra, och erbjuder alltid en handling. Utan ritat förslag finns inget att köra: beräkningarna
 * görs på ett förslag och jämförs med läget idag (`POST /api/kartverktyg/<projekt>/scenarier/<slug>/kor` kräver ett
 * scenario med minst en ändring som berör området), så raden pekar i stället till steg 2 och till projektets
 * befintliga resultat. Se STATUS.md (U4) för valet.
 */
import { formatera } from "../tal.ts";
import { flodeAdress } from "./adress.ts";

export type KorHinder = "forslag" | "fragor" | "avtappning" | null;

export interface KorIn {
  harForslag: boolean;
  /** Antal beräkningar som de valda frågorna ger. */
  antalBerakningar: number;
  avtappningFel: boolean;
  pagar: boolean;
  /** Text under körning (framsteg). */
  forloppText?: string;
  /** Fel från senaste körningen. */
  korFel?: string | null;
  /** Uppskattad tid för läget idag (text ur `uppskattadTidText`), visas bredvid knappen "Räkna läget idag". */
  lageTidText?: string;
}

/** Uppskattad tid ur planernas `uppskattad_tid_s` (null = okänd): tom summa 0 är "klart i cachen". Talen går via tal.ts. */
export function uppskattadTidText(tider: ReadonlyArray<number | null>): string {
  if (tider.length === 0 || !tider.every((x): x is number => x !== null)) return "–";
  const summa = tider.reduce((a, b) => a + b, 0);
  return summa === 0 ? "klart i cachen" : `ungefär ${formatera(Math.round(summa), 0)} s`;
}

export function korHinder(i: Pick<KorIn, "harForslag" | "antalBerakningar" | "avtappningFel">): KorHinder {
  if (!i.harForslag) return "forslag";
  if (i.avtappningFel) return "avtappning";
  if (i.antalBerakningar === 0) return "fragor";
  return null;
}

export const HINDER_TEXT: Record<Exclude<KorHinder, null>, string> = {
  forslag: "Beräkningarna körs på ett förslag, och du har inte ritat något än.",
  fragor: "Välj minst en fråga som Floden kan räkna på (skyfall eller dagsljus och sol).",
  avtappning: "Rätta avtappningen först.",
};

/** Förklaringen överst på sidan när inget förslag finns. */
export function utanForslagRuta(projekt: string, esc: (s: string) => string): string {
  return `<div class="flode-ruta" id="fr-utanforslag"><p><strong>Inget förslag är ritat.</strong> Beräkningarna körs på ett förslag och jämförs med läget idag. Du kan räkna projektets befintliga läge utan förslag (knappen Räkna läget idag längst ned) och se det i Resultat. Frågorna du väljer följer med när du har ritat ett förslag.</p>
    <p class="flode-handlingar"><a class="flode-knapp svart" href="${esc(flodeAdress(projekt, "forslag"))}">Rita ett förslag</a><a class="flode-knapp" href="${esc(flodeAdress(projekt, "resultat"))}">Visa projektets befintliga resultat</a></p></div>`;
}

/** Innehållet i handlingsraden: statustext och knapp(ar). */
export function korradHtml(i: KorIn, projekt: string, esc: (s: string) => string): string {
  const hinder = i.pagar ? null : korHinder(i);
  let text = "";
  if (i.pagar) text = esc(i.forloppText ?? "Beräknar …");
  else if (hinder === "forslag" && !i.korFel) {
    text = `${esc(HINDER_TEXT.forslag)} Du kan i stället räkna läget idag, utan förslag${i.antalBerakningar === 0 ? " (välj minst en fråga först)" : ""}. Uppskattad tid för läget idag: ${esc(i.lageTidText ?? "–")}.`;
  } else if (i.korFel) text = `<span class="flode-fel" role="alert">${esc(i.korFel)}</span>`;
  else if (hinder) text = esc(HINDER_TEXT[hinder]);
  else text = "Klar att köra. Beräkningarna körs på servern och Resultat öppnas när de är klara.";
  const knappar =
    hinder === "forslag" && !i.pagar
      ? `<a class="flode-knapp svart stor" id="fr-rita" href="${esc(flodeAdress(projekt, "forslag"))}">Rita ett förslag</a><button type="button" class="flode-knapp stor" id="fr-lage"${i.antalBerakningar === 0 || i.avtappningFel ? " disabled" : ""}>Räkna läget idag</button><a class="flode-knapp stor" id="fr-befintligt" href="${esc(flodeAdress(projekt, "resultat"))}">Visa projektets befintliga resultat</a>`
      : `<button type="button" class="flode-knapp svart stor" id="fr-kor" aria-describedby="fr-kor-not"${i.pagar || hinder ? " disabled" : ""}>${i.pagar ? "Beräknar …" : "Kör beräkningarna"}</button>`;
  return `<p class="flode-liten flode-dampad" id="fr-kor-not" role="status" aria-live="polite">${text}</p><div class="flode-handlingar">${knappar}</div>`;
}
