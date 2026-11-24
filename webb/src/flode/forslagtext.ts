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
