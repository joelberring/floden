/*
 * Resultatpanelens HTML som rena funktioner (inga DOM-anrop), så att texten går att testa: sifferformen,
 * escaping och att inget tal visas med decimalpunkt. `resultat.ts` sätter bara in resultatet.
 */
import { esc } from "../sidor.ts";
import { formatera } from "../tal.ts";
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

/* ---------- U5d: Kompakt ordlista för Steg 2 ---------- */

export interface OrdlistaPost {
  term: string;
  forklaring: string;
}

/** Kompakt domänordlista för Steg 2 (U5d): centrala begrepp i skyfall, sol, ekologi och luft. */
export const ORDLISTA_POSTER: readonly OrdlistaPost[] = [
  {
    term: "RH 2000",
    forklaring: "Sveriges nationella höjdsystem i meter över havet (markhöjd, vattennivå och murkrön).",
  },
  {
    term: "SWEREF 99 18 00",
    forklaring: "Plant svenskt koordinatsystem i meter (EPSG:3011: E = öst, N = norr) för alla kartmått och ritningar.",
  },
  {
    term: "SVF",
    forklaring: "Sky View Factor – synlig himmelsandel (0–1) från en markpunkt; 0 är helt skymd himmel och 1 är helt fri horisont.",
  },
  {
    term: "VSC",
    forklaring: "Vertical Sky Component – procentandel av himmelns dagsljus som når en vertikal fasadpunkt.",
  },
  {
    term: "ECA / dPC",
    forklaring: "Ekologisk konnektivitet: ECA är effektiv sammanhängande habitatyta (ha) och dPC visar varje fläcks relativa bidrag (%) till spridning.",
  },
  {
    term: "MKN",
    forklaring: "Miljökvalitetsnorm för utomhusluft, exempelvis PM10 (dygns- och årsmedelvärden enligt luftkvalitetsförordningen).",
  },
  {
    term: "NEN 8100",
    forklaring: "Standard för vindkomfort på gånghöjd i klasserna A–E (från lugn sittplats till olämplig för gång) samt vindfara.",
  },
  {
    term: "Skiss",
    forklaring: "Metodstatus för en förenklad översiktsberäkning i tidigt skede – ger riktning men ersätter inte fördjupad utredning.",
  },
] as const;

/** Hopfällbar svartvit ordlista (<details>) för Steg 2 (U5d). */
export function ordlistaHtml(oppen = false): string {
  const rader = ORDLISTA_POSTER.map(
    (p) => `<div class="ar-ordlista-post"><dt>${esc(p.term)}</dt><dd>${esc(p.forklaring)}</dd></div>`,
  ).join("");
  return `<details class="flode-detalj ar-ordlista" id="ar-ordlista"${oppen ? " open" : ""}>
    <summary>Ordlista och begrepp</summary>
    <dl class="ar-ordlista-lista">${rader}</dl>
  </details>`;
}

/* ---------- Numerisk CAD-måttinmatning i ritläget ---------- */

export type CadInmatningsLage = "matt" | "sweref";

export interface CadMattVisning {
  /** Om ett ritverktyg för hörn/punkter är aktivt (hus, linje eller yta). */
  synlig: boolean;
  /** Om CAD-måttpanelen är utfälld. */
  oppen: boolean;
  /** Aktivt inmatningsläge: längd + bäring eller absoluta SWEREF-koordinater (E, N). */
  lage: CadInmatningsLage;
  /** Antal punkter i pågående utkast. */
  antalPunkter: number;
  /** Senaste utkastpunkten i SWEREF 99 18 00 (meter), eller null när utkastet är tomt. */
  senastePunkt: { x: number; y: number } | null;
  langdText: string;
  baringText: string;
  eText: string;
  nText: string;
  fel: string | null;
}

/** Kompakt, hopfällbar svartvit CAD-måttpanel för exakt ritning med längd + bäring eller E, N (SWEREF). */
export function cadMattHtml(c: CadMattVisning): string {
  if (!c.synlig) return "";
  const status =
    c.antalPunkter === 0
      ? "Startpunkt"
      : `${formatera(c.antalPunkter, 0)} ${c.antalPunkter === 1 ? "punkt" : "punkter"}`;
  const refText =
    c.lage === "matt"
      ? c.senastePunkt
        ? `Från punkt ${formatera(c.antalPunkter, 0)}: E ${formatera(c.senastePunkt.x, 2)}, N ${formatera(c.senastePunkt.y, 2)} · 0° = norr, 90° = öst`
        : "Sätt första punkten i kartan eller byt till E, N (SWEREF)."
      : "SWEREF 99 18 00 (meter). Decimalkomma eller decimalpunkt.";
  const falt =
    c.lage === "matt"
      ? `<div class="ar-cad-falt">
          <div class="flode-falt"><label for="ar-cad-langd">Längd (m)</label><input id="ar-cad-langd" name="langd" type="text" inputmode="decimal" autocomplete="off" placeholder="12,5" value="${esc(c.langdText)}" /></div>
          <div class="flode-falt"><label for="ar-cad-baring">Bäring (°)</label><input id="ar-cad-baring" name="baring" type="text" inputmode="decimal" autocomplete="off" placeholder="90" value="${esc(c.baringText)}" /></div>
        </div>`
      : `<div class="ar-cad-falt">
          <div class="flode-falt"><label for="ar-cad-e">E (SWEREF m)</label><input id="ar-cad-e" name="e" type="text" inputmode="decimal" autocomplete="off" placeholder="150120,5" value="${esc(c.eText)}" /></div>
          <div class="flode-falt"><label for="ar-cad-n">N (SWEREF m)</label><input id="ar-cad-n" name="n" type="text" inputmode="decimal" autocomplete="off" placeholder="6580450,0" value="${esc(c.nText)}" /></div>
        </div>`;
  const felHtml = c.fel ? `<p class="flode-fel flode-liten ar-cad-fel" id="ar-cad-fel" role="alert">${esc(c.fel)}</p>` : "";
  return `<details class="ar-cad" id="ar-cad"${c.oppen ? " open" : ""}>
    <summary class="ar-cad-summary"><span>Exakta mått (CAD)</span><span class="ar-cad-status flode-liten flode-dampad">${esc(status)}</span></summary>
    <div class="ar-cad-kropp">
      <div class="flode-segment liten ar-cad-lage" role="group" aria-label="Inmatningssätt för exakta mått">
        <button type="button" data-cad-lage="matt" aria-pressed="${c.lage === "matt"}">Längd + bäring</button>
        <button type="button" data-cad-lage="sweref" aria-pressed="${c.lage === "sweref"}">E, N (SWEREF)</button>
      </div>
      <p class="flode-liten flode-dampad ar-cad-ref">${esc(refText)}</p>
      <form class="ar-cad-form" id="ar-cad-form" novalidate>
        ${falt}
        <button type="submit" class="flode-knapp svart ar-cad-knapp" id="ar-cad-lagg-till">Lägg till punkt</button>
      </form>
      ${felHtml}
    </div>
  </details>`;
}
