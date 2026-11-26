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

export interface BullerPanelTillstand {
  param: BullerParam;
  laddar: boolean;
  fel: string | null;
  svar: BullerAnalysSvar | null;
  scenario: string | null;
  visaLager: boolean;
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

  let kropp = `<p class="flode-liten flode-dampad">Ange vägsegment och trafikflöde (ÅDT) för att räkna en skisskarta för vägtrafikbuller (L<sub>Aeq,24h</sub>)${t.scenario ? ` för nuläge och förslag <strong>${esc(t.scenario)}</strong>` : ""}.</p>`;
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

    kropp = `
      <div class="flode-segment liten ar-vs-lagerval" role="group" aria-label="Visa bullerlager på kartan">
        <button type="button" data-bl-lager="visa" aria-pressed="${t.visaLager}">Visa L<sub>Aeq</sub> dB(A)</button>
        <button type="button" data-bl-lager="av" aria-pressed="${!t.visaLager}">Dölj</button>
      </div>
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
    <details class="flode-detalj ar-buller-panel" id="ar-buller-panel">
      <summary class="ar-utci-gyf-summary">
        <span>Buller (vägtrafik L<sub>Aeq</sub>)</span>
        <span class="ar-skiss-badge" title="Ej Nord2000-konform — formell bullerrapport spärrad">SKISS</span>
      </summary>
      <div class="ar-utci-gyf-kropp">
        <div class="ar-buller-banner" role="note" aria-label="Buller skissvarning">
          <strong>${esc(BULLER_SKISS_BANNER)}</strong>
          <span>Skissverktyg ovanpå <code>buller.nord2000</code> (<code>nord2000_konform = false</code>). Ej för myndighetsbeslut.</span>
        </div>
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
        <div class="ar-buller-atgarder">
          <button type="button" class="flode-knapp liten" id="ar-bl-fyll-vag">Fyll väg längs projektkant</button>
          <button type="button" class="flode-knapp liten" id="ar-bl-kor"${t.laddar ? " disabled" : ""}>Beräkna bullerskiss</button>
        </div>
        <div id="ar-bl-ut">${kropp}</div>
      </div>
    </details>`;
}

export interface BullerKopplingOpts {
  projekt: string;
  hamtaScenario(): string | null;
  hamtaLage(): string;
  hamtaUtbredning(): Utbredning | null;
  onBullerlagerAndrat(lager: BullerLagerRef | null): void;
  signal: AbortSignal;
}

export interface BullerKontroller {
  uppdateraKontext(): void;
  synkaBullerlager(): void;
  rensaBullerlager(): void;
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
  };

  let oppen = false;
  let seq = 0;

  const rita = (): void => {
    const nu = behallare.querySelector<HTMLDetailsElement>("#ar-buller-panel");
    if (nu) oppen = nu.open;
    behallare.innerHTML = bullerPanelHtml(state);
    const ny = behallare.querySelector<HTMLDetailsElement>("#ar-buller-panel");
    if (ny) ny.open = oppen;
  };

  const synkaBullerlager = (): void => {
    opts.onBullerlagerAndrat(valjBullerLager(state.svar, state.visaLager, opts.hamtaLage()));
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
      oppen = true;
      state.fel = fel;
      rita();
      return;
    }
    const nr = ++seq;
    oppen = true;
    state.scenario = opts.hamtaScenario();
    state.laddar = true;
    state.fel = null;
    rita();
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
      }
    }
  };

  behallare.addEventListener(
    "click",
    (h) => {
      const m = (h.target as Element).closest<HTMLElement>("#ar-bl-kor,#ar-bl-fyll-vag,[data-bl-lager]");
      if (!m) return;
      if (m.id === "ar-bl-fyll-vag") {
        state.param = lasParamUrForm();
        const u = opts.hamtaUtbredning();
        if (u) {
          state.param = { ...state.param, ...foreslaVagKoordinater(u) };
          state.fel = null;
        } else {
          state.fel = "Projektets utbredning har inte laddats ännu.";
        }
        oppen = true;
        rita();
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
      if (nySc !== state.scenario) {
        state.scenario = nySc;
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
  };
}
