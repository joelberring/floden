/*
 * Resultatpanelens HTML som rena funktioner (inga DOM-anrop), så att texten går att testa: sifferformen,
 * escaping och att inget tal visas med decimalpunkt. `resultat.ts` sätter bara in resultatet.
 */
import { esc } from "../sidor.ts";
import { OSAKERHET_NOT } from "./osakerhet.ts";
import type { Stapel, Tal } from "./nyckeltal.ts";

export function talListaHtml(tal: readonly Tal[]): string {
  return `<dl class="flode-tal">${tal
    .map((x) => `<div><dt>${esc(x.etikett)}</dt><dd>${esc(x.varde)}</dd>${x.under ? `<dd class="flode-tal-under">${esc(x.under)}</dd>` : ""}</div>`)
    .join("")}</dl>`;
}

export function staplarHtml(rubrik: string, staplar: readonly Stapel[]): string {
  return `<div class="flode-staplar"><span class="flode-liten flode-dampad">${esc(rubrik)}</span>
    ${staplar
      .map(
        (s) =>
          `<div class="flode-stapel"><span class="flode-dampad">${esc(s.etikett)}</span><span class="flode-stapel-fyll${s.etikett === "Före" || s.etikett === "Mindre sol" ? " ljus" : ""}" style="width:${s.andel === null ? 0 : Math.max(2, Math.round(s.andel * 100))}%"></span><span>${esc(s.varde)}</span></div>`,
      )
      .join("")}
  </div>`;
}

export interface OsakerhetVisning {
  /** Knappen är intryckt: osäkerheten visas. */
  pa: boolean;
  /** Hämtas just nu. */
  laddar: boolean;
  tal: readonly Tal[];
  /** Instabila överfall som rader, bara för vatten. */
  rader: readonly string[];
  fel: readonly string[];
  /** Lagret "Känsliga överfall" på kartan: null när analysen inte körts eller ämnet saknar lager. */
  overfallLager: { synlig: boolean; antal: number } | null;
}

/** Knappen "Visa osäkerhet" med det den visar: nyckeltal, överfallslager och rader. */
export function osakerhetHtml(o: OsakerhetVisning): string {
  const knapp = `<button type="button" class="flode-knapp" id="rs-osakerhet" aria-pressed="${o.pa}"${o.laddar ? " disabled" : ""}>${o.laddar ? "Hämtar …" : o.pa ? "Dölj osäkerhet" : "Visa osäkerhet"}</button>`;
  if (!o.pa) return `<div class="flode-osakerhet">${knapp}</div>`;
  const lager = o.overfallLager
    ? `<label class="flode-kryss"><input type="checkbox" id="rs-overfall" ${o.overfallLager.synlig ? "checked" : ""}/> Känsliga överfall på kartan (${esc(String(o.overfallLager.antal))})</label>`
    : "";
  const rader = o.rader.length > 0 ? `<details class="flode-liten"><summary>Var bör vi mäta</summary><ul>${o.rader.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></details>` : "";
  const fel = o.fel.map((f) => `<p class="flode-fel flode-liten" role="alert">${esc(f)}</p>`).join("");
  return `<div class="flode-osakerhet">${knapp}
    ${o.tal.length > 0 ? talListaHtml(o.tal) : ""}${lager}${rader}${fel}
    <p class="flode-liten flode-dampad">${esc(OSAKERHET_NOT)}</p></div>`;
}

export interface KroppIndata {
  slutsats: string;
  tal: readonly Tal[];
  jamforelse: { rubrik: string; staplar: readonly Stapel[] };
  /** Rader om källa och antaganden (ekologi). */
  kalla: readonly string[];
  /** Löpande text under staplarna: regn, antaganden, varningar och körningens id. */
  anmarkning: string;
  osakerhet: OsakerhetVisning | null;
  /** "Så läser du kartan": en förklaring av vad kartan visar. */
  las?: string;
}

/** Hela resultatpanelens innehåll under valen (slutsats, tal, staplar, osäkerhet, källa). */
export function kroppHtml(k: KroppIndata): string {
  return `<h1 class="flode-slutsats" id="rs-rubrik">${esc(k.slutsats)}</h1>
      ${talListaHtml(k.tal)}
      ${staplarHtml(k.jamforelse.rubrik, k.jamforelse.staplar)}
      ${k.osakerhet ? osakerhetHtml(k.osakerhet) : ""}
      ${k.kalla.length > 0 ? `<ul class="flode-liten flode-dampad" id="rs-eko-kalla">${k.kalla.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <p class="flode-liten flode-dampad">${esc(k.anmarkning)}</p>
      ${k.las ? `<details class="flode-detalj"><summary>Så läser du kartan</summary><p class="flode-liten flode-dampad">${esc(k.las)}</p></details>` : ""}`;
}
