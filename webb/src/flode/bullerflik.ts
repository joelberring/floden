/*
 * Steg 2-panel för Buller-skissanalys (Uppdrag 13, K21 `buller.nord2000`).
 *
 * Hårda invarianter:
 * - Visar alltid en framträdande varningsbanner:
 *   `SKISS — ej Nord2000-konform (formell bullerrapport spärrad)`
 * - Alla ljudnivåer (dB(A)) och dämpningar beräknas deterministiskt i backend (`POST /api/analys/buller`).
 * - Trafikindata (ÅDT, hastighet km/h, tung andel %, vägkoordinater i EPSG:3011) anges alltid
 *   uttryckligen av användaren; klienten hittar aldrig på trafik.
 * - Noll WebGL-/Vite-specifika statiska importer så hela modulen kan testas direkt i Node (`npm test`).
 */
import type { Teckenforklaring, Utbredning } from "../fragor-typer.ts";
import { esc } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";

export const BULLER_ANALYS_URL = "/api/analys/buller";

/** Obligatorisk, framträdande varningsbanner i Steg 2 (får aldrig döljas). */
export const BULLER_SKISS_BANNER =
  "SKISS — ej Nord2000-konform (formell bullerrapport spärrad)";

export interface BullerVagSegment {
  id: string;
  koordinater: Array<[number, number]>;
  adt_fordon_dygn: number;
  hastighet_km_h: number;
  andel_tung_pct: number;
}

export interface BullerParam {
  adt_fordon_dygn: number | null;
  hastighet_km_h: number | null;
  andel_tung_pct: number | null;
  mottagarhojd_m: number;
  vag_start_e: number | null;
  vag_start_n: number | null;
  vag_slut_e: number | null;
  vag_slut_n: number | null;
}

export const STANDARD_BULLER_PARAM: Readonly<BullerParam> = {
  adt_fordon_dygn: null,
  hastighet_km_h: 50,
  andel_tung_pct: 10,
  mottagarhojd_m: 2.0,
  vag_start_e: null,
  vag_start_n: null,
  vag_slut_e: null,
  vag_slut_n: null,
};

export interface BullerLagerRef {
  id: string;
  roll: "ekvivalent_ljudniva_dba" | string;
  typ: "bas" | "scenario" | "skillnad";
  etikett: string;
  enhet: string;
  farg: string;
  skala: { min: number; max: number };
  teckenforklaring: Teckenforklaring;
  utbredning: Utbredning;
  pixelstorlek_m: number;
  bredd_px: number;
  hojd_px: number;
  tak_maskat: boolean;
  png: string;
}

export interface BullerSammanfattning {
  nord2000_konform: boolean;
  metodstatus: string;
  rapport_sparrad: boolean;
  markning?: string;
  skissvarning?: string;
  laeq_max_dba: number | null;
  laeq_medel_dba: number | null;
  laeq_energimedel_dba?: number | null;
  laeq_min_dba?: number | null;
  andel_over_55_dba_pct: number;
  andel_over_60_dba_pct: number;
  antal_fasadpunkter: number;
  fasad_laeq_max_dba: number | null;
  fasad_laeq_medel_dba: number | null;
  mottagarhojd_m: number;
  raster_cell_m: number;
  antal_bullerskarmar?: number;
  varningar: string[];
}

export interface BullerJamforelse {
  delta_laeq_max_dba: number | null;
  delta_laeq_medel_dba: number | null;
  delta_laeq_energimedel_dba?: number | null;
  delta_andel_over_55_dba_pct: number | null;
  delta_andel_over_60_dba_pct: number | null;
  delta_fasad_laeq_max_dba?: number | null;
  delta_fasad_laeq_medel_dba?: number | null;
  min_delta_dba: number | null;
  max_delta_dba: number | null;
  medel_delta_dba: number | null;
  max_dampning_dba: number | null;
  antal_jamforda_celler: number;
  nord2000_konform: boolean;
  metodstatus: string;
  rapport_sparrad: boolean;
}

export interface BullerAnalysSvar {
  format: string;
  omrade_id: string;
  scenario: string | null;
  nord2000_konform: boolean;
  metodstatus: string;
  rapport_sparrad: boolean;
  markning?: string;
  skissvarning?: string;
  sammanfattning: BullerSammanfattning;
  fore: BullerSammanfattning;
  efter: BullerSammanfattning | null;
  jamforelse: BullerJamforelse | null;
  fasad_buller: {
    type: "FeatureCollection";
    features: Array<{
      type: "Feature";
      geometry: { type: "Point"; coordinates: [number, number] };
      properties: Record<string, unknown>;
    }>;
  };
  lager: {
    ekvivalent_ljudniva_dba_fore?: BullerLagerRef | null;
    ekvivalent_ljudniva_dba_efter?: BullerLagerRef | null;
    ekvivalent_ljudniva_dba_skillnad?: BullerLagerRef | null;
  };
  png?: string | null;
  varningar: string[];
}

export interface BullerVagLinjeVy {
  a: { x: number; y: number };
  b: { x: number; y: number };
  meter: number;
  text: string;
  klar: boolean;
}

export interface BullerPanelTillstand {
  param: BullerParam;
  laddar: boolean;
  fel: string | null;
  svar: BullerAnalysSvar | null;
  scenario: string | null;
  visaLager: boolean;
  ritarVag?: boolean;
  ritadPunkt1?: { e: number; n: number } | null;
  aktivtLage?: string;
}

const avrunda01 = (v: number): number => Math.round(v * 10) / 10;

/** Bygger mät-/förhandslinje för buller-vägsegment på kartan (kompatibel med `Kartmotor.settMatLinje`). */
export function byggBullerVagLinje(
  e1: number,
  n1: number,
  e2: number,
  n2: number,
  klar: boolean,
): BullerVagLinjeVy {
  const meter = Math.hypot(e2 - e1, n2 - n1);
  const dec = meter >= 100 ? 0 : 1;
  return {
    a: { x: e1, y: n1 },
    b: { x: e2, y: n2 },
    meter,
    text: `Väg: ${formatera(meter, dec)} m`,
    klar,
  };
}

/** Applicerar ett kartklick (steg 1 = startpunkt, steg 2 = slutpunkt) under "Rita väg på kartan". */
export function tillampaBullerVagKlick(
  param: BullerParam,
  ritadPunkt1: { e: number; n: number } | null,
  e: number,
  n: number,
):
  | { klar: false; ritadPunkt1: { e: number; n: number }; fel: null }
  | { klar: true; param: BullerParam; ritadPunkt1: null; fel: null; linje: BullerVagLinjeVy }
  | { klar: false; ritadPunkt1: { e: number; n: number }; fel: string } {
  const pe = avrunda01(e);
  const pn = avrunda01(n);
  if (!ritadPunkt1) {
    return { klar: false, ritadPunkt1: { e: pe, n: pn }, fel: null };
  }
  const langd = Math.hypot(pe - ritadPunkt1.e, pn - ritadPunkt1.n);
  if (langd < 0.1) {
    return {
      klar: false,
      ritadPunkt1,
      fel: "Vägsegmentets start- och slutpunkt får inte sammanfalla — klicka en punkt längre bort.",
    };
  }
  const nyParam: BullerParam = {
    ...param,
    vag_start_e: ritadPunkt1.e,
    vag_start_n: ritadPunkt1.n,
    vag_slut_e: pe,
    vag_slut_n: pn,
  };
  return {
    klar: true,
    param: nyParam,
    ritadPunkt1: null,
    fel: null,
    linje: byggBullerVagLinje(ritadPunkt1.e, ritadPunkt1.n, pe, pn, true),
  };
}

const arTal = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Tolkar decimaltal med svenskt komma eller punkt. */
export function tolkaBullerTal(text: string): number | null {
  const ren = text.trim().replace(/\s+/g, "").replace(",", ".");
  if (!ren) return null;
  const v = Number(ren);
  return Number.isFinite(v) ? v : null;
}

/** Validerar användarens trafik- och vägparametrar (returnerar svensk feltext eller null). */
export function valideraBullerParam(p: BullerParam): string | null {
  if (!arTal(p.adt_fordon_dygn) || p.adt_fordon_dygn < 0) {
    return "Ange trafikflöde (ÅDT, fordon/dygn >= 0) — trafikdata antas aldrig automatiskt.";
  }
  if (!arTal(p.hastighet_km_h) || p.hastighet_km_h <= 0) {
    return "Skyltad hastighet måste vara > 0 km/h.";
  }
  if (!arTal(p.andel_tung_pct) || p.andel_tung_pct < 0 || p.andel_tung_pct > 100) {
    return "Andel tung trafik måste ligga mellan 0 och 100 %.";
  }
  if (!arTal(p.mottagarhojd_m) || p.mottagarhojd_m <= 0) {
    return "Mottagarhöjd måste vara > 0 m.";
  }
  if (!arTal(p.vag_start_e) || !arTal(p.vag_start_n) || !arTal(p.vag_slut_e) || !arTal(p.vag_slut_n)) {
    return "Ange vägsegmentets start- och slutkoordinater (E, N i EPSG:3011).";
  }
  const langd = Math.hypot(p.vag_slut_e - p.vag_start_e, p.vag_slut_n - p.vag_start_n);
  if (langd < 0.1) {
    return "Vägsegmentets start- och slutpunkt får inte sammanfalla.";
  }
  return null;
}

/** Bygger JSON-begäran för `POST /api/analys/buller`. */
export function byggBullerBegaran(
  omrade_id: string,
  p: BullerParam,
  scenario?: string | null,
): {
  omrade_id: string;
  scenario?: string;
  mottagarhojd_m: number;
  vagsegment: BullerVagSegment[];
} {
  const seg: BullerVagSegment = {
    id: "vag_1",
    koordinater: [
      [p.vag_start_e as number, p.vag_start_n as number],
      [p.vag_slut_e as number, p.vag_slut_n as number],
    ],
    adt_fordon_dygn: p.adt_fordon_dygn as number,
    hastighet_km_h: p.hastighet_km_h as number,
    andel_tung_pct: p.andel_tung_pct as number,
  };
  return {
    omrade_id,
    ...(scenario ? { scenario } : {}),
    mottagarhojd_m: p.mottagarhojd_m,
    vagsegment: [seg],
  };
}

/** Beräknar en synlig vägsträcka 10 m innanför projektets södra kant (endast vid aktivt knapptryck). */
export function foreslaVagKoordinater(u: Utbredning): {
  vag_start_e: number;
  vag_start_n: number;
  vag_slut_e: number;
  vag_slut_n: number;
} {
  const bredd = Math.max(2, u.x_max - u.x_min);
  const hojd = Math.max(2, u.y_max - u.y_min);
  const margX = Math.min(10, bredd * 0.1);
  const margY = Math.min(10, hojd * 0.15);
  return {
    vag_start_e: Math.round((u.x_min + margX) * 10) / 10,
    vag_start_n: Math.round((u.y_min + margY) * 10) / 10,
    vag_slut_e: Math.round((u.x_max - margX) * 10) / 10,
    vag_slut_n: Math.round((u.y_min + margY) * 10) / 10,
  };
}

/** Väljer rätt bullerlager (`fore`, `efter` eller `skillnad`) för kartläget i Steg 2. */
export function valjBullerLager(
  svar: BullerAnalysSvar | null,
  visa: boolean,
  lage: string,
): BullerLagerRef | null {
  if (!svar || !visa) return null;
  if (lage === "efter") {
    return svar.lager.ekvivalent_ljudniva_dba_efter ?? svar.lager.ekvivalent_ljudniva_dba_fore ?? null;
  }
  if (lage === "skillnad") {
    return svar.lager.ekvivalent_ljudniva_dba_skillnad ?? svar.lager.ekvivalent_ljudniva_dba_fore ?? null;
  }
  return svar.lager.ekvivalent_ljudniva_dba_fore ?? null;
}

function formateraDeltaDb(v: number, decimaler = 1, enhet = "dB(A)"): string {
  const tecken = v > 1e-9 ? "+" : v < -1e-9 ? "−" : "";
  return `${tecken}${formatera(Math.abs(v), decimaler)}${enhet ? ` ${enhet}` : ""}`;
}

/** Teckenförklaring för bullerraster (`ekvivalent_ljudniva_dba`, dB(A)). */
export function bullerLagerTeckenHtml(lager: BullerLagerRef): string {
  const tf = lager.teckenforklaring;
  const titel = `${lager.etikett}${lager.enhet ? ` (${lager.enhet})` : ""} · SKISS (ej Nord2000-konform)`;
  if (tf.typ === "divergerande") {
    return `<span class="flode-kort-rubrik">${esc(titel)}</span>
      <span class="flode-band" style="background:linear-gradient(90deg,${esc(tf.farg_minskning)},#f5f5f5,${esc(tf.farg_okning)})"></span>
      <span class="flode-bandtext"><span>−${esc(formatera(Math.abs(tf.max), 1))} dB(A)</span><span>+${esc(formatera(Math.abs(tf.max), 1))} dB(A)</span></span>
      <span class="flode-liten flode-dampad">Blått = tystare efter skärm/åtgärd, rött = högre ljudnivå.${lager.tak_maskat ? " Tak är maskade." : ""}</span>`;
  }
  if (tf.typ === "kontinuerlig") {
    return `<span class="flode-kort-rubrik">${esc(titel)}</span>
      <span class="flode-band" style="background:linear-gradient(90deg,${tf.stopp.map((c) => esc(c)).join(",")})"></span>
      <span class="flode-bandtext"><span>${esc(formatera(tf.min, 1))}</span><span>${esc(formatera(tf.max, 1))} ${esc(lager.enhet)}</span></span>
      ${lager.tak_maskat ? `<span class="flode-liten flode-dampad">Ljusgrå yta: tak, inte mark.</span>` : ""}`;
  }
  return `<span class="flode-kort-rubrik">${esc(titel)}</span>`;
}

const fmtInput = (v: number | null, maxDec = 1): string => (arTal(v) ? formateraHogst(v, maxDec) : "");

/** Renderar Buller-skisspanelen (Steg 2) med framträdande varningsbanner, trafikparametrar och diff. */
export function bullerPanelHtml(t: BullerPanelTillstand): string {
  const p = t.param;
  const s = t.svar;
  const sam = s?.sammanfattning ?? null;

  const aktivtLager = valjBullerLager(s, t.visaLager, t.aktivtLage ?? "nulage");
  const ritarVag = Boolean(t.ritarVag);
  const harVag =
    arTal(p.vag_start_e) && arTal(p.vag_start_n) && arTal(p.vag_slut_e) && arTal(p.vag_slut_n);
  const vagLangdM = harVag
    ? Math.hypot((p.vag_slut_e as number) - (p.vag_start_e as number), (p.vag_slut_n as number) - (p.vag_start_n as number))
    : null;

  let vagStatusHtml = `<p class="flode-liten flode-dampad" id="ar-bl-vag-status">Inget vägsegment valt ännu — klicka <strong>Rita väg på kartan</strong> (2 punkter).</p>`;
  if (ritarVag && !t.ritadPunkt1) {
    vagStatusHtml = `<p class="flode-liten ar-bl-vag-aktiv" id="ar-bl-vag-status" role="status">Klicka vägens <strong>startpunkt</strong> direkt på kartan (Esc avbryter).</p>`;
  } else if (ritarVag && t.ritadPunkt1) {
    vagStatusHtml = `<p class="flode-liten ar-bl-vag-aktiv" id="ar-bl-vag-status" role="status">Startpunkt satt (E ${esc(formatera(t.ritadPunkt1.e, 0))}, N ${esc(formatera(t.ritadPunkt1.n, 0))}) — klicka vägens <strong>slutpunkt</strong> på kartan.</p>`;
  } else if (harVag && vagLangdM !== null) {
    vagStatusHtml = `<p class="flode-liten" id="ar-bl-vag-status"><strong>Vägsegment: ${esc(formatera(vagLangdM, vagLangdM >= 100 ? 0 : 1))} m</strong> <span class="flode-dampad">(ritat på kartan)</span></p>`;
  }

  let kropp = `<p class="flode-liten flode-dampad">Rita väg på kartan och ange trafikflöde (ÅDT) för att räkna en skisskarta för vägtrafikbuller (L<sub>Aeq,24h</sub>)${t.scenario ? ` för nuläge och förslag <strong>${esc(t.scenario)}</strong>` : ""}.</p>`;
  if (t.laddar) {
    kropp = `<p class="flode-liten flode-dampad" role="status">Beräknar vägtrafikbuller (buller.nord2000 skiss) …</p>`;
  } else if (t.fel) {
    kropp = `<p class="flode-liten flode-fel" role="alert">${esc(t.fel)}</p>`;
  } else if (s && sam) {
    const laeqMax = arTal(sam.laeq_max_dba) ? `${formatera(sam.laeq_max_dba, 1)} dB(A)` : "–";
    const laeqMedel = arTal(sam.laeq_medel_dba) ? `${formatera(sam.laeq_medel_dba, 1)} dB(A)` : "–";
    const over55 = `${formatera(sam.andel_over_55_dba_pct, 1)} %`;
    const over60 = `${formatera(sam.andel_over_60_dba_pct, 1)} %`;

    const fasadHtml =
      sam.antal_fasadpunkter > 0
        ? `<div><dt>Fasad max / medel (${esc(formatera(sam.antal_fasadpunkter, 0))} pkt)</dt><dd><strong>${esc(
            arTal(sam.fasad_laeq_max_dba) ? `${formatera(sam.fasad_laeq_max_dba, 1)} dB(A)` : "–",
          )}</strong> <span class="flode-dampad">/ ${esc(
            arTal(sam.fasad_laeq_medel_dba) ? `${formatera(sam.fasad_laeq_medel_dba, 1)} dB(A)` : "–",
          )}</span></dd></div>`
        : "";

    let jfrHtml = "";
    if (s.jamforelse && s.efter) {
      const j = s.jamforelse;
      const dMedel = arTal(j.delta_laeq_medel_dba) ? formateraDeltaDb(j.delta_laeq_medel_dba, 1) : "–";
      const dMax = arTal(j.delta_laeq_max_dba) ? formateraDeltaDb(j.delta_laeq_max_dba, 1) : "–";
      const damp = arTal(j.max_dampning_dba) ? `${formatera(j.max_dampning_dba, 1)} dB(A)` : "–";
      const d55 = arTal(j.delta_andel_over_55_dba_pct)
        ? formateraDeltaDb(j.delta_andel_over_55_dba_pct, 1, "procentenheter")
        : "–";
      jfrHtml = `
        <div class="ar-buller-diff">
          <span class="ar-sektion-rubrik">Före / efter (${esc(s.scenario ?? "förslag")})</span>
          <dl class="flode-tal ar-buller-tal">
            <div><dt>ΔL<sub>Aeq</sub> (medel / max)</dt><dd><strong>${esc(dMedel)}</strong> <span class="flode-dampad">/ ${esc(dMax)}</span></dd></div>
            <div><dt>Största dämpning (bakom skärm/hus)</dt><dd><strong>−${esc(damp)}</strong></dd></div>
            <div><dt>Δ andel &gt; 55 dB(A)</dt><dd>${esc(d55)}</dd></div>
          </dl>
        </div>`;
    }

    const varningHtml =
      s.varningar.length > 0
        ? `<ul class="flode-liten flode-dampad ar-gyf-varningar">${s.varningar.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>`
        : "";

    const inlineLegendHtml = aktivtLager
      ? `<div class="ar-inline-legend" aria-label="Teckenförklaring för buller">${bullerLagerTeckenHtml(aktivtLager)}</div>`
      : "";

    kropp = `
      <div class="flode-segment liten ar-vs-lagerval" role="group" aria-label="Visa bullerlager på kartan">
        <button type="button" data-bl-lager="visa" aria-pressed="${t.visaLager}">Visa L<sub>Aeq</sub> dB(A)</button>
        <button type="button" data-bl-lager="av" aria-pressed="${!t.visaLager}">Dölj</button>
      </div>
      ${inlineLegendHtml}
      <dl class="flode-tal ar-buller-tal">
        <div><dt>L<sub>Aeq</sub> max / medel</dt><dd><strong>${esc(laeqMax)}</strong> <span class="flode-dampad">/ ${esc(laeqMedel)}</span></dd></div>
        <div><dt>Andel &gt; 55 dB(A) / &gt; 60 dB(A)</dt><dd><strong>${esc(over55)}</strong> <span class="flode-dampad">/ ${esc(over60)}</span></dd></div>
        ${fasadHtml}
        <div><dt>Status</dt><dd><code>${esc(sam.metodstatus)}</code> · <code>nord2000_konform=${String(sam.nord2000_konform)}</code> · rapport spärrad</dd></div>
      </dl>
      ${jfrHtml}
      ${varningHtml}`;
  }

  return `
    <section class="ar-doman-kort ar-buller-panel" id="ar-buller-panel" aria-label="Buller skissanalys">
      <div class="ar-doman-huvud">
        <h2 class="ar-doman-rubrik">Buller (vägtrafik L<sub>Aeq</sub>)</h2>
        <span class="ar-skiss-badge" title="Ej Nord2000-konform — formell bullerrapport spärrad">SKISS</span>
      </div>
      <div class="ar-utci-gyf-kropp">
        <div class="ar-buller-banner" role="note" aria-label="Buller skissvarning">
          <strong>${esc(BULLER_SKISS_BANNER)}</strong>
          <span>Skissverktyg ovanpå <code>buller.nord2000</code> (<code>nord2000_konform = false</code>). Ej för myndighetsbeslut.</span>
        </div>
        <div class="ar-buller-atgarder">
          <button type="button" class="flode-knapp liten${ritarVag ? " svart" : ""}" id="ar-bl-rita-vag" aria-pressed="${ritarVag}">${ritarVag ? "Avbryt vägritning" : "Rita väg på kartan"}</button>
          <button type="button" class="flode-knapp liten" id="ar-bl-fyll-vag">Fyll väg längs projektkant</button>
        </div>
        ${vagStatusHtml}
        <div class="ar-vs-grid">
          <label class="flode-liten">ÅDT (fordon/dygn)
            <input id="ar-bl-adt" type="text" inputmode="decimal" value="${esc(fmtInput(p.adt_fordon_dygn, 0))}" placeholder="t.ex. 8000" />
          </label>
          <label class="flode-liten">Hastighet (km/h)
            <input id="ar-bl-hast" type="text" inputmode="decimal" value="${esc(fmtInput(p.hastighet_km_h, 0))}" />
          </label>
          <label class="flode-liten">Tung andel (%)
            <input id="ar-bl-tung" type="text" inputmode="decimal" value="${esc(fmtInput(p.andel_tung_pct, 1))}" />
          </label>
          <label class="flode-liten">Mottagarhöjd (m)
            <input id="ar-bl-hojd" type="text" inputmode="decimal" value="${esc(fmtInput(p.mottagarhojd_m, 1))}" />
          </label>
        </div>
        <details class="flode-detalj ar-bl-koord-detalj">
          <summary class="flode-liten">Justera koordinater manuellt (SWEREF 99 18 00)</summary>
          <div class="ar-vs-grid">
            <label class="flode-liten">Väg start E (3011)
              <input id="ar-bl-e1" type="text" inputmode="decimal" value="${esc(fmtInput(p.vag_start_e, 1))}" placeholder="E1" />
            </label>
            <label class="flode-liten">Väg start N (3011)
              <input id="ar-bl-n1" type="text" inputmode="decimal" value="${esc(fmtInput(p.vag_start_n, 1))}" placeholder="N1" />
            </label>
            <label class="flode-liten">Väg slut E (3011)
              <input id="ar-bl-e2" type="text" inputmode="decimal" value="${esc(fmtInput(p.vag_slut_e, 1))}" placeholder="E2" />
            </label>
            <label class="flode-liten">Väg slut N (3011)
              <input id="ar-bl-n2" type="text" inputmode="decimal" value="${esc(fmtInput(p.vag_slut_n, 1))}" placeholder="N2" />
            </label>
          </div>
        </details>
        <div class="ar-vs-atgarder">
          <button type="button" class="flode-knapp svart liten" id="ar-bl-kor"${t.laddar ? " disabled" : ""}>${t.laddar ? "Beräknar …" : "Beräkna bullerskiss"}</button>
        </div>
        <div id="ar-bl-ut">${kropp}</div>
      </div>
    </section>`;
}

export interface BullerKopplingOpts {
  projekt: string;
  hamtaScenario(): string | null;
  hamtaLage(): string;
  hamtaUtbredning(): Utbredning | null;
  onBullerlagerAndrat(lager: BullerLagerRef | null): void;
  signal: AbortSignal;
  hamtaAktivDoman?: () => string;
  onRitaVagAndrad?: (aktiv: boolean) => void;
  onVagLinjeAndrad?: (linje: BullerVagLinjeVy | null) => void;
  onStatusAndrad?: () => void;
}

export interface BullerStatus {
  laddar: boolean;
  harSvar: boolean;
  ritarVag: boolean;
}

export interface BullerKontroller {
  uppdateraKontext(): void;
  synkaBullerlager(): void;
  rensaBullerlager(): void;
  sattAktivDoman(doman: string): void;
  hanteraKartKlick(e: number, n: number): boolean;
  uppdateraForhandPunkt(e: number | null, n: number | null): void;
  avbrytVagRitning(): boolean;
  arRitarVag(): boolean;
  vagLinje(): BullerVagLinjeVy | null;
  hjalpText(): string;
  status(): BullerStatus;
}

/** Kopplar Buller-skisspanelen i Steg 2 mot `POST /api/analys/buller` och kartan. */
export function kopplaBullerPanel(
  behallare: HTMLElement,
  opts: BullerKopplingOpts,
): BullerKontroller {
  const state: BullerPanelTillstand = {
    param: { ...STANDARD_BULLER_PARAM },
    laddar: false,
    fel: null,
    svar: null,
    scenario: opts.hamtaScenario(),
    visaLager: false,
    ritarVag: false,
    ritadPunkt1: null,
    aktivtLage: opts.hamtaLage(),
  };

  let aktivDoman: string | null = opts.hamtaAktivDoman ? opts.hamtaAktivDoman() : null;
  let forhandPunkt: { e: number; n: number } | null = null;
  let seq = 0;

  const vagLinje = (): BullerVagLinjeVy | null => {
    if (state.ritarVag && state.ritadPunkt1 && forhandPunkt) {
      return byggBullerVagLinje(
        state.ritadPunkt1.e,
        state.ritadPunkt1.n,
        forhandPunkt.e,
        forhandPunkt.n,
        false,
      );
    }
    const p = state.param;
    if (arTal(p.vag_start_e) && arTal(p.vag_start_n) && arTal(p.vag_slut_e) && arTal(p.vag_slut_n)) {
      return byggBullerVagLinje(p.vag_start_e, p.vag_start_n, p.vag_slut_e, p.vag_slut_n, true);
    }
    return null;
  };

  const synkaLinje = (): void => {
    opts.onVagLinjeAndrad?.(aktivDoman === null || aktivDoman === "buller" ? vagLinje() : null);
  };

  const rita = (): void => {
    state.aktivtLage = opts.hamtaLage();
    if (aktivDoman !== null && aktivDoman !== "buller") {
      behallare.hidden = true;
      behallare.innerHTML = "";
      return;
    }
    const koordDetalj = behallare.querySelector<HTMLDetailsElement>(".ar-bl-koord-detalj");
    const koordOppen = Boolean(koordDetalj?.open);
    behallare.hidden = false;
    behallare.innerHTML = bullerPanelHtml(state);
    const nyKoord = behallare.querySelector<HTMLDetailsElement>(".ar-bl-koord-detalj");
    if (nyKoord && koordOppen) nyKoord.open = true;
  };

  const synkaBullerlager = (): void => {
    state.aktivtLage = opts.hamtaLage();
    opts.onBullerlagerAndrat(valjBullerLager(state.svar, state.visaLager, state.aktivtLage));
  };

  const lasParamUrForm = (): BullerParam => ({
    adt_fordon_dygn: tolkaBullerTal(behallare.querySelector<HTMLInputElement>("#ar-bl-adt")?.value ?? ""),
    hastighet_km_h: tolkaBullerTal(behallare.querySelector<HTMLInputElement>("#ar-bl-hast")?.value ?? ""),
    andel_tung_pct: tolkaBullerTal(behallare.querySelector<HTMLInputElement>("#ar-bl-tung")?.value ?? ""),
    mottagarhojd_m:
      tolkaBullerTal(behallare.querySelector<HTMLInputElement>("#ar-bl-hojd")?.value ?? "") ??
      STANDARD_BULLER_PARAM.mottagarhojd_m,
    vag_start_e: tolkaBullerTal(behallare.querySelector<HTMLInputElement>("#ar-bl-e1")?.value ?? ""),
    vag_start_n: tolkaBullerTal(behallare.querySelector<HTMLInputElement>("#ar-bl-n1")?.value ?? ""),
    vag_slut_e: tolkaBullerTal(behallare.querySelector<HTMLInputElement>("#ar-bl-e2")?.value ?? ""),
    vag_slut_n: tolkaBullerTal(behallare.querySelector<HTMLInputElement>("#ar-bl-n2")?.value ?? ""),
  });

  const korBuller = async (): Promise<void> => {
    state.param = lasParamUrForm();
    const fel = valideraBullerParam(state.param);
    if (fel) {
      state.fel = fel;
      rita();
      return;
    }
    const nr = ++seq;
    state.ritarVag = false;
    state.ritadPunkt1 = null;
    forhandPunkt = null;
    opts.onRitaVagAndrad?.(false);
    state.scenario = opts.hamtaScenario();
    state.laddar = true;
    state.fel = null;
    rita();
    opts.onStatusAndrad?.();
    try {
      const { lasJson } = await import("../api.ts");
      const begaran = byggBullerBegaran(opts.projekt, state.param, state.scenario);
      const svar = await lasJson<BullerAnalysSvar>(BULLER_ANALYS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(begaran),
      });
      if (nr !== seq) return;
      state.svar = svar;
      state.fel = null;
      state.visaLager = true;
      synkaBullerlager();
    } catch (e) {
      if (nr !== seq) return;
      state.fel = e instanceof Error ? e.message : String(e);
    } finally {
      if (nr === seq) {
        state.laddar = false;
        rita();
        synkaLinje();
        opts.onStatusAndrad?.();
      }
    }
  };

  const avbrytVagRitning = (): boolean => {
    if (!state.ritarVag) return false;
    state.ritarVag = false;
    state.ritadPunkt1 = null;
    forhandPunkt = null;
    opts.onRitaVagAndrad?.(false);
    rita();
    synkaLinje();
    return true;
  };

  behallare.addEventListener(
    "click",
    (h) => {
      const m = (h.target as Element).closest<HTMLElement>(
        "#ar-bl-kor,#ar-bl-fyll-vag,#ar-bl-rita-vag,[data-bl-lager]",
      );
      if (!m) return;
      if (m.id === "ar-bl-rita-vag") {
        state.param = lasParamUrForm();
        if (state.ritarVag) {
          avbrytVagRitning();
          return;
        }
        state.ritarVag = true;
        state.ritadPunkt1 = null;
        forhandPunkt = null;
        state.fel = null;
        opts.onRitaVagAndrad?.(true);
        rita();
        synkaLinje();
        return;
      }
      if (m.id === "ar-bl-fyll-vag") {
        state.param = lasParamUrForm();
        state.ritarVag = false;
        state.ritadPunkt1 = null;
        forhandPunkt = null;
        opts.onRitaVagAndrad?.(false);
        const u = opts.hamtaUtbredning();
        if (u) {
          state.param = { ...state.param, ...foreslaVagKoordinater(u) };
          state.fel = null;
        } else {
          state.fel = "Projektets utbredning har inte laddats ännu.";
        }
        rita();
        synkaLinje();
        return;
      }
      if (m.id === "ar-bl-kor") {
        void korBuller();
        return;
      }
      const val = m.dataset.blLager;
      if (val === "visa") {
        state.visaLager = true;
        synkaBullerlager();
        rita();
      } else if (val === "av") {
        state.visaLager = false;
        synkaBullerlager();
        rita();
      }
    },
    { signal: opts.signal },
  );

  rita();

  return {
    uppdateraKontext: () => {
      const nySc = opts.hamtaScenario();
      const nyLage = opts.hamtaLage();
      if (nySc !== state.scenario || nyLage !== state.aktivtLage) {
        state.scenario = nySc;
        state.aktivtLage = nyLage;
        rita();
      }
      synkaBullerlager();
    },
    synkaBullerlager,
    rensaBullerlager: () => {
      if (state.visaLager) {
        state.visaLager = false;
        opts.onBullerlagerAndrat(null);
        rita();
      }
    },
    sattAktivDoman: (doman: string) => {
      if (doman !== "buller" && state.ritarVag) {
        state.ritarVag = false;
        state.ritadPunkt1 = null;
        forhandPunkt = null;
        opts.onRitaVagAndrad?.(false);
      }
      aktivDoman = doman;
      rita();
      synkaLinje();
    },
    hanteraKartKlick: (e: number, n: number): boolean => {
      if (!state.ritarVag) return false;
      state.param = lasParamUrForm();
      const res = tillampaBullerVagKlick(state.param, state.ritadPunkt1 ?? null, e, n);
      if (res.klar) {
        state.param = res.param;
        state.ritarVag = false;
        state.ritadPunkt1 = null;
        forhandPunkt = null;
        state.fel = null;
        opts.onRitaVagAndrad?.(false);
      } else {
        state.ritadPunkt1 = res.ritadPunkt1;
        state.fel = res.fel;
      }
      rita();
      synkaLinje();
      return true;
    },
    uppdateraForhandPunkt: (e: number | null, n: number | null): void => {
      if (!state.ritarVag || !state.ritadPunkt1) return;
      forhandPunkt = e !== null && n !== null ? { e: avrunda01(e), n: avrunda01(n) } : null;
      synkaLinje();
    },
    avbrytVagRitning,
    arRitarVag: () => Boolean(state.ritarVag),
    vagLinje,
    hjalpText: () => {
      if (!state.ritarVag) return "";
      if (!state.ritadPunkt1) return "Rita väg (Buller): klicka vägens startpunkt på kartan (Esc avbryter).";
      return "Rita väg (Buller): klicka vägens slutpunkt på kartan (Esc avbryter).";
    },
    status: () => ({
      laddar: state.laddar,
      harSvar: state.svar !== null,
      ritarVag: Boolean(state.ritarVag),
    }),
  };
}
