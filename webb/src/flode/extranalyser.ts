/*
 * Kompletterande analysverktyg i resultatpanelen:
 * - Monotont MKN-intervall (`POST /api/analys/luft-mkn`)
 * - Bullerskärmsval med CELF (`POST /api/analys/buller-skarmar`)
 * - Ekologi · åtgärdsval med CELF (`POST /api/analys/ekologi-atgarder`)
 * - Skyfall · kumulativ översvämningsrisk (`POST /api/risk`) och framkomlighet med CELF (`POST /api/rutt`)
 * Alla tal räknas deterministiskt på servern; klienten formaterar bara med svensk formatering (`tal.ts`).
 */
import { esc } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";
import {
  formateraBullerSkarmar,
  formateraMknIntervall,
  tolkaSkarmkandidater,
  tolkaTallista,
  type BullerSkarmarSvar,
  type MknIntervallSvar,
} from "./nyckeltal.ts";

async function postJson<T>(url: string, kropp: unknown): Promise<T> {
  const { lasJson } = await import("../api.ts");
  return lasJson<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(kropp),
  });
}

export interface EkologiAtgardPost {
  atgard_id: string;
  marginal_vinst: number;
  kumulativ_vinst: number;
  vinst_ensam: number;
}

export interface EkologiAtgarderSvar {
  kontrakt?: string;
  k?: number;
  valda_atgarder?: EkologiAtgardPost[];
  total_vinst?: number;
  garanti_faktor?: number;
}

export interface KumulativRiskSteg {
  aterkomsttid_ar: number;
  total_djup_mm: number;
  max_djup_m: number;
  area_over_grans_m2: number;
}

export interface KumulativRiskSvar {
  omrade_id?: string;
  gransdjup_m?: number;
  max_aterkomsttid_ar?: number;
  tidshorisont_ar?: number;
  sammanfattning?: {
    area_over_grans_m2?: number;
    andel_over_grans?: number;
    max_arlig_sannolikhet?: number;
    max_kumulativ_sannolikhet?: number;
    min_kritisk_aterkomsttid_ar?: number | null;
    max_forvantat_arligt_djup_m?: number;
    antal_aterkomsttider?: number;
  };
  steg?: KumulativRiskSteg[];
}

export interface RuttInsatsPost {
  kant_id: string;
  marginalvinst: number;
  kumulativ_vinst: number;
  vinst_ensam: number;
}

export interface RuttAnalysSvar {
  omrade_id?: string;
  fordon_typ?: string;
  gransdjup_m?: number;
  varning?: string;
  antal_kanter_totalt?: number;
  antal_kanter_med_risk?: number;
  max_kumulativ_kant_sannolikhet?: number;
  rekommenderade_insatser?: RuttInsatsPost[];
}

const arTal = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Formaterar svaret från `POST /api/analys/ekologi-atgarder` (CELF). */
export function formateraEkologiAtgarder(svar: EkologiAtgarderSvar | null | undefined): {
  sammanfattning: string;
  garanti: string;
  rader: Array<{ atgard_id: string; marginal_vinst: string; kumulativ_vinst: string; vinst_ensam: string }>;
} {
  const valda = svar?.valda_atgarder ?? [];
  const total = arTal(svar?.total_vinst) ? formatera(svar.total_vinst, 2) : "–";
  const garanti = arTal(svar?.garanti_faktor)
    ? `Submodulär (1 − 1/e)-garanti: minst ${formatera(svar.garanti_faktor * 100, 1)} % av teoretiskt optimum.`
    : "Submodulär (1 − 1/e)-garanti mot teoretiskt optimum.";
  const sammanfattning =
    valda.length === 0
      ? "Inga ekologiåtgärder valda."
      : `${formatera(valda.length, 0)} åtgärd${valda.length === 1 ? "" : "er"} valda · total vinst ${total}.`;
  return {
    sammanfattning,
    garanti,
    rader: valda.map((v) => ({
      atgard_id: v.atgard_id,
      marginal_vinst: arTal(v.marginal_vinst) ? formatera(v.marginal_vinst, 2) : "–",
      kumulativ_vinst: arTal(v.kumulativ_vinst) ? formatera(v.kumulativ_vinst, 2) : "–",
      vinst_ensam: arTal(v.vinst_ensam) ? formatera(v.vinst_ensam, 2) : "–",
    })),
  };
}

/** Formaterar svaret från `POST /api/risk` (kumulativ översvämningsrisk över återkomsttider). */
export function formateraKumulativRisk(svar: KumulativRiskSvar | null | undefined): {
  status: string;
  tal: Array<{ etikett: string; varde: string }>;
  stegRader: Array<{ aterkomsttid: string; regn: string; maxDjup: string; area: string }>;
} {
  const s = svar?.sammanfattning;
  if (!svar || !s) return { status: "Ingen kumulativ risk beräknad.", tal: [], stegRader: [] };
  const grans = arTal(svar.gransdjup_m) ? `${formateraHogst(svar.gransdjup_m, 2)} m` : "–";
  const horisont = arTal(svar.tidshorisont_ar) ? `${formatera(svar.tidshorisont_ar, 0)} år` : "–";
  const status = `Kumulativ risk över gränsdjup ${grans} på ${horisont} (${svar.omrade_id ?? "okänt område"}).`;
  const minT = arTal(s.min_kritisk_aterkomsttid_ar) ? `${formatera(s.min_kritisk_aterkomsttid_ar, 0)} år` : "överskrids ej";
  const tal = [
    {
      etikett: "Max kumulativ sannolikhet",
      varde: arTal(s.max_kumulativ_sannolikhet) ? `${formatera(s.max_kumulativ_sannolikhet * 100, 1)} %` : "–",
    },
    {
      etikett: "Yta över gränsdjup",
      varde: arTal(s.area_over_grans_m2) ? `${formatera(s.area_over_grans_m2, 0)} m²` : "–",
    },
    {
      etikett: "Lägsta kritiska återkomsttid",
      varde: minT,
    },
    {
      etikett: "Förväntat årligt maxdjup",
      varde: arTal(s.max_forvantat_arligt_djup_m) ? `${formatera(s.max_forvantat_arligt_djup_m, 3)} m` : "–",
    },
  ];
  const stegRader = (svar.steg ?? []).map((st) => ({
    aterkomsttid: arTal(st.aterkomsttid_ar) ? `${formatera(st.aterkomsttid_ar, 0)} år` : "–",
    regn: arTal(st.total_djup_mm) ? `${formatera(st.total_djup_mm, 1)} mm` : "–",
    maxDjup: arTal(st.max_djup_m) ? `${formatera(st.max_djup_m, 2)} m` : "–",
    area: arTal(st.area_over_grans_m2) ? `${formatera(st.area_over_grans_m2, 0)} m²` : "–",
  }));
  return { status, tal, stegRader };
}

/** Formaterar svaret från `POST /api/rutt` (framkomlighet och CELF kritiska gatuavsnitt). */
export function formateraRuttAnalys(svar: RuttAnalysSvar | null | undefined): {
  sammanfattning: string;
  varning: string | null;
  tal: Array<{ etikett: string; varde: string }>;
  insatser: Array<{ kant_id: string; marginalvinst: string; kumulativ_vinst: string; vinst_ensam: string }>;
} {
  if (!svar) return { sammanfattning: "Ingen framkomlighetsanalys beräknad.", varning: null, tal: [], insatser: [] };
  const medRisk = arTal(svar.antal_kanter_med_risk) ? formatera(svar.antal_kanter_med_risk, 0) : "–";
  const totalt = arTal(svar.antal_kanter_totalt) ? formatera(svar.antal_kanter_totalt, 0) : "–";
  const sammanfattning = `${medRisk} av ${totalt} gatuavsnitt har framkomlighetsrisk (${svar.fordon_typ ?? "fordon"}).`;
  const tal = [
    {
      etikett: "Gatuavsnitt med risk",
      varde: `${medRisk} / ${totalt}`,
    },
    {
      etikett: "Max kumulativ kantsannolikhet",
      varde: arTal(svar.max_kumulativ_kant_sannolikhet)
        ? `${formatera(svar.max_kumulativ_kant_sannolikhet * 100, 1)} %`
        : "–",
    },
  ];
  const insatser = (svar.rekommenderade_insatser ?? []).map((p) => ({
    kant_id: p.kant_id,
    marginalvinst: arTal(p.marginalvinst) ? formatera(p.marginalvinst, 2) : "–",
    kumulativ_vinst: arTal(p.kumulativ_vinst) ? formatera(p.kumulativ_vinst, 2) : "–",
    vinst_ensam: arTal(p.vinst_ensam) ? formatera(p.vinst_ensam, 2) : "–",
  }));
  return {
    sammanfattning,
    varning: typeof svar.varning === "string" && svar.varning.trim() ? svar.varning : null,
    tal,
    insatser,
  };
}

export function extraAnalyserHtml(): string {
  return `
    <details class="flode-extra" id="rs-extra">
      <summary class="flode-liten">Fristående räknare: MKN, buller, ekologi (CELF) och skyfallsrisk</summary>
      <p class="flode-ruta varning">Egna värden: fälten nedan är förifyllda med exempeltal och hör inte till projektet eller ditt förslag. Skriv in dina egna värden innan du läser av något.</p>
      <div class="flode-extra-block">
        <p class="flode-liten flode-dampad"><strong>Luft · MKN-intervall (PM10)</strong> – ange minst 24 timvärden (µg/m³, lokalt bidrag).</p>
        <textarea id="rs-mkn-serie" rows="2" class="flode-inmatning" aria-label="Timvärden PM10">32, 38, 45, 52, 60, 58, 49, 41, 35, 30, 28, 26, 30, 36, 44, 55, 62, 57, 48, 40, 34, 31, 29, 27</textarea>
        <div class="flode-extra-rad">
          <label class="flode-liten">Bakgrund (µg/m³) <input id="rs-mkn-bakg" type="number" step="0.5" value="12" /></label>
          <button type="button" class="flode-knapp liten" id="rs-mkn-kor">Beräkna MKN-intervall</button>
        </div>
        <div id="rs-mkn-ut" class="flode-liten"></div>
      </div>
      <div class="flode-extra-block">
        <p class="flode-liten flode-dampad"><strong>Buller · Skärmval (CELF)</strong> – grundnivå per mottagare (dB) och skärmkandidater (reduktion i dB).</p>
        <label class="flode-liten">Grundnivåer (dB) <input id="rs-buller-bas" type="text" value="62, 65, 58, 67, 61" /></label>
        <label class="flode-liten">Skärmar (<code>namn: dB, ...</code>)
          <textarea id="rs-buller-skarmar" rows="3" class="flode-inmatning">Skärm A: 6, 4, 1, 0, 2
Skärm B: 0, 3, 2, 7, 5
Skärm C: 3, 5, 0, 4, 1</textarea>
        </label>
        <div class="flode-extra-rad">
          <label class="flode-liten">Riktvärde (dB) <input id="rs-buller-rikt" type="number" step="1" value="60" /></label>
          <label class="flode-liten">Antal skärmar <input id="rs-buller-k" type="number" min="1" max="20" value="2" /></label>
          <button type="button" class="flode-knapp liten" id="rs-buller-kor">Välj bullerskärmar</button>
        </div>
        <div id="rs-buller-ut" class="flode-liten"></div>
      </div>
      <div class="flode-extra-block">
        <p class="flode-liten flode-dampad"><strong>Ekologi · Åtgärdsval (CELF)</strong> – välj <em>k</em> kompensationsåtgärder med submodulär (1 − 1/e)-garanti. Ange en åtgärd per rad som <code>id: v1, v2, ...</code> (täckning per fläck/korridor).</p>
        <label class="flode-liten">Kandidater (<code>id: v1, v2, ...</code>)
          <textarea id="rs-eko-kandidater" rows="3" class="flode-inmatning">Ekodukt: 0,85, 0,70, 0,20
Grönkorridor: 0,45, 0,55, 0,60
Våtmark: 0,30, 0,25, 0,75</textarea>
        </label>
        <div class="flode-extra-rad">
          <label class="flode-liten">Antal åtgärder (k) <input id="rs-eko-k" type="number" min="1" max="20" value="2" /></label>
          <button type="button" class="flode-knapp liten" id="rs-eko-kor">Välj ekologiåtgärder</button>
        </div>
        <div id="rs-eko-ut" class="flode-liten"></div>
      </div>
      <div class="flode-extra-block">
        <p class="flode-liten flode-dampad"><strong>Skyfall · Kumulativ risk &amp; framkomlighet (CELF)</strong> – beräknar kumulativ översvämningsrisk över återkomsttider (<code>/api/risk</code>) samt kritiska gatuavsnitt för räddningstjänst med testvärden (<code>/api/rutt</code>).</p>
        <div class="flode-extra-rad">
          <label class="flode-liten">Testområde <input id="rs-risk-omrade" type="text" value="A1_enkel_sanka" /></label>
          <label class="flode-liten">Gränsdjup (m) <input id="rs-risk-grans" type="number" step="0.05" min="0.05" value="0.2" /></label>
        </div>
        <div class="flode-extra-rad">
          <label class="flode-liten">Max återkomsttid (år) <input id="rs-risk-tmax" type="number" step="10" min="10" value="100" /></label>
          <label class="flode-liten">Tidshorisont (år) <input id="rs-risk-horisont" type="number" step="5" min="1" value="50" /></label>
          <button type="button" class="flode-knapp liten" id="rs-risk-kor">Beräkna kumulativ risk</button>
        </div>
        <div id="rs-risk-ut" class="flode-liten"></div>
        <div class="flode-extra-rad">
          <label class="flode-liten">Fordon
            <select id="rs-rutt-fordon">
              <option value="slacksbil">Släckbil</option>
              <option value="ambulans">Ambulans</option>
              <option value="storskalig">Storskaligt fordon</option>
            </select>
          </label>
          <label class="flode-liten">Antal insatser (k) <input id="rs-rutt-k" type="number" min="1" max="20" value="2" /></label>
          <button type="button" class="flode-knapp liten" id="rs-rutt-kor">Välj kritiska gatuavsnitt</button>
        </div>
        <div id="rs-rutt-ut" class="flode-liten"></div>
      </div>
    </details>`;
}

export function kopplaExtraAnalyser(rot: HTMLElement, signal: AbortSignal): void {
  const mknKnapp = rot.querySelector<HTMLButtonElement>("#rs-mkn-kor");
  const mknUt = rot.querySelector<HTMLElement>("#rs-mkn-ut");
  mknKnapp?.addEventListener(
    "click",
    async () => {
      if (!mknUt) return;
      const serieText = rot.querySelector<HTMLTextAreaElement>("#rs-mkn-serie")?.value ?? "";
      const bakg = Number(rot.querySelector<HTMLInputElement>("#rs-mkn-bakg")?.value ?? "0");
      const serie = tolkaTallista(serieText);
      if (!serie || serie.length < 24) {
        mknUt.innerHTML = `<p class="flode-fel">Ange minst 24 giltiga timvärden.</p>`;
        return;
      }
      mknUt.innerHTML = `<p class="flode-dampad">Beräknar MKN-intervall …</p>`;
      try {
        const svar = await postJson<MknIntervallSvar>("/api/analys/luft-mkn", {
          timvis_lokal_ug_m3: serie,
          bakgrund_ug_m3: Number.isFinite(bakg) ? bakg : 0,
          emissionsfaktor_intervall: [0.8, 1.25],
          depositionsfaktor_intervall: [0.8, 1.25],
        });
        const f = formateraMknIntervall(svar);
        mknUt.innerHTML = `<p>${esc(f.status)}</p><dl class="flode-tal">${f.tal.map((x) => `<div><dt>${esc(x.etikett)}</dt><dd>${esc(x.varde)}</dd></div>`).join("")}</dl>`;
      } catch (e) {
        mknUt.innerHTML = `<p class="flode-fel">${esc(e instanceof Error ? e.message : String(e))}</p>`;
      }
    },
    { signal },
  );

  const bullerKnapp = rot.querySelector<HTMLButtonElement>("#rs-buller-kor");
  const bullerUt = rot.querySelector<HTMLElement>("#rs-buller-ut");
  bullerKnapp?.addEventListener(
    "click",
    async () => {
      if (!bullerUt) return;
      const basText = rot.querySelector<HTMLInputElement>("#rs-buller-bas")?.value ?? "";
      const skarmText = rot.querySelector<HTMLTextAreaElement>("#rs-buller-skarmar")?.value ?? "";
      const rikt = Number(rot.querySelector<HTMLInputElement>("#rs-buller-rikt")?.value ?? "60");
      const k = Math.max(1, Math.round(Number(rot.querySelector<HTMLInputElement>("#rs-buller-k")?.value ?? "2")));
      const bas = tolkaTallista(basText);
      const skarmar = tolkaSkarmkandidater(skarmText);
      if (!bas || !skarmar) {
        bullerUt.innerHTML = `<p class="flode-fel">Kontrollera formatet för grundnivåer och skärmrader.</p>`;
        return;
      }
      bullerUt.innerHTML = `<p class="flode-dampad">Beräknar skärmval …</p>`;
      try {
        const svar = await postJson<BullerSkarmarSvar>("/api/analys/buller-skarmar", {
          bas_niva_db: bas,
          skarm_reduktion_db: skarmar,
          k,
          riktvarde_db: Number.isFinite(rikt) ? rikt : 60,
        });
        const f = formateraBullerSkarmar(svar);
        bullerUt.innerHTML = `
          <p>${esc(f.sammanfattning)}</p>
          ${
            f.rader.length > 0
              ? `<ul>${f.rader.map((r) => `<li><strong>${esc(r.skarm_id)}</strong>: +${esc(r.marginal_vinst)} (totalt ${esc(r.kumulativ_vinst)}, ${esc(r.nya_godkanda)} nya godkända)</li>`).join("")}</ul>`
              : ""
          }`;
      } catch (e) {
        bullerUt.innerHTML = `<p class="flode-fel">${esc(e instanceof Error ? e.message : String(e))}</p>`;
      }
    },
    { signal },
  );

  const ekoKnapp = rot.querySelector<HTMLButtonElement>("#rs-eko-kor");
  const ekoUt = rot.querySelector<HTMLElement>("#rs-eko-ut");
  ekoKnapp?.addEventListener(
    "click",
    async () => {
      if (!ekoUt) return;
      const kandText = rot.querySelector<HTMLTextAreaElement>("#rs-eko-kandidater")?.value ?? "";
      const k = Math.max(1, Math.round(Number(rot.querySelector<HTMLInputElement>("#rs-eko-k")?.value ?? "2")));
      const kandidater = tolkaSkarmkandidater(kandText);
      if (!kandidater) {
        ekoUt.innerHTML = `<p class="flode-fel">Kontrollera formatet för kandidater (en åtgärd per rad: namn: v1, v2, ...).</p>`;
        return;
      }
      ekoUt.innerHTML = `<p class="flode-dampad">Beräknar åtgärdsval (CELF) …</p>`;
      try {
        const svar = await postJson<EkologiAtgarderSvar>("/api/analys/ekologi-atgarder", { k, kandidater });
        const f = formateraEkologiAtgarder(svar);
        ekoUt.innerHTML = `
          <p>${esc(f.sammanfattning)}</p>
          <p class="flode-dampad">${esc(f.garanti)}</p>
          ${
            f.rader.length > 0
              ? `<ul>${f.rader.map((r) => `<li><strong>${esc(r.atgard_id)}</strong>: +${esc(r.marginal_vinst)} (kumulativt ${esc(r.kumulativ_vinst)}, ensam ${esc(r.vinst_ensam)})</li>`).join("")}</ul>`
              : ""
          }`;
      } catch (e) {
        ekoUt.innerHTML = `<p class="flode-fel">${esc(e instanceof Error ? e.message : String(e))}</p>`;
      }
    },
    { signal },
  );

  const riskKnapp = rot.querySelector<HTMLButtonElement>("#rs-risk-kor");
  const riskUt = rot.querySelector<HTMLElement>("#rs-risk-ut");
  riskKnapp?.addEventListener(
    "click",
    async () => {
      if (!riskUt) return;
      const omrade_id = (rot.querySelector<HTMLInputElement>("#rs-risk-omrade")?.value ?? "").trim();
      const gransdjup_m = Number(rot.querySelector<HTMLInputElement>("#rs-risk-grans")?.value ?? "0.2");
      const max_aterkomsttid_ar = Number(rot.querySelector<HTMLInputElement>("#rs-risk-tmax")?.value ?? "100");
      const tidshorisont_ar = Number(rot.querySelector<HTMLInputElement>("#rs-risk-horisont")?.value ?? "50");
      if (!omrade_id || !Number.isFinite(gransdjup_m) || gransdjup_m <= 0) {
        riskUt.innerHTML = `<p class="flode-fel">Ange ett giltigt testområde och positivt gränsdjup.</p>`;
        return;
      }
      riskUt.innerHTML = `<p class="flode-dampad">Beräknar kumulativ översvämningsrisk …</p>`;
      try {
        const svar = await postJson<KumulativRiskSvar>("/api/risk", {
          omrade_id,
          gransdjup_m,
          max_aterkomsttid_ar: Number.isFinite(max_aterkomsttid_ar) ? max_aterkomsttid_ar : 100,
          tidshorisont_ar: Number.isFinite(tidshorisont_ar) ? tidshorisont_ar : 50,
        });
        const f = formateraKumulativRisk(svar);
        const stegHtml =
          f.stegRader.length > 0
            ? `<table class="flode-tabell"><thead><tr><th>T</th><th>Regn</th><th>Maxdjup</th><th>Yta &gt; gräns</th></tr></thead><tbody>${f.stegRader.map((st) => `<tr><td>${esc(st.aterkomsttid)}</td><td>${esc(st.regn)}</td><td>${esc(st.maxDjup)}</td><td>${esc(st.area)}</td></tr>`).join("")}</tbody></table>`
            : "";
        riskUt.innerHTML = `<p>${esc(f.status)}</p><dl class="flode-tal">${f.tal.map((x) => `<div><dt>${esc(x.etikett)}</dt><dd>${esc(x.varde)}</dd></div>`).join("")}</dl>${stegHtml}`;
      } catch (e) {
        riskUt.innerHTML = `<p class="flode-fel">${esc(e instanceof Error ? e.message : String(e))}</p>`;
      }
    },
    { signal },
  );

  const ruttKnapp = rot.querySelector<HTMLButtonElement>("#rs-rutt-kor");
  const ruttUt = rot.querySelector<HTMLElement>("#rs-rutt-ut");
  ruttKnapp?.addEventListener(
    "click",
    async () => {
      if (!ruttUt) return;
      const omrade_id = (rot.querySelector<HTMLInputElement>("#rs-risk-omrade")?.value ?? "").trim();
      const gransdjup_m = Number(rot.querySelector<HTMLInputElement>("#rs-risk-grans")?.value ?? "0.2");
      const max_aterkomsttid_ar = Number(rot.querySelector<HTMLInputElement>("#rs-risk-tmax")?.value ?? "100");
      const tidshorisont_ar = Number(rot.querySelector<HTMLInputElement>("#rs-risk-horisont")?.value ?? "50");
      const fordon_typ = rot.querySelector<HTMLSelectElement>("#rs-rutt-fordon")?.value ?? "slacksbil";
      const antal_insatser = Math.max(1, Math.round(Number(rot.querySelector<HTMLInputElement>("#rs-rutt-k")?.value ?? "2")));
      if (!omrade_id || !Number.isFinite(gransdjup_m) || gransdjup_m <= 0) {
        ruttUt.innerHTML = `<p class="flode-fel">Ange ett giltigt testområde och positivt gränsdjup.</p>`;
        return;
      }
      ruttUt.innerHTML = `<p class="flode-dampad">Beräknar framkomlighet och kritiska gatuavsnitt (CELF) …</p>`;
      try {
        const svar = await postJson<RuttAnalysSvar>("/api/rutt", {
          omrade_id,
          gransdjup_m,
          fordon_typ,
          max_aterkomsttid_ar: Number.isFinite(max_aterkomsttid_ar) ? max_aterkomsttid_ar : 100,
          tidshorisont_ar: Number.isFinite(tidshorisont_ar) ? tidshorisont_ar : 50,
          antal_insatser,
          tillat_testvarden: true,
        });
        const f = formateraRuttAnalys(svar);
        ruttUt.innerHTML = `
          <p>${esc(f.sammanfattning)}</p>
          ${f.varning ? `<p class="flode-dampad">${esc(f.varning)}</p>` : ""}
          <dl class="flode-tal">${f.tal.map((x) => `<div><dt>${esc(x.etikett)}</dt><dd>${esc(x.varde)}</dd></div>`).join("")}</dl>
          ${
            f.insatser.length > 0
              ? `<ul>${f.insatser.map((r) => `<li><strong>${esc(r.kant_id)}</strong>: +${esc(r.marginalvinst)} (kumulativt ${esc(r.kumulativ_vinst)}, ensam ${esc(r.vinst_ensam)})</li>`).join("")}</ul>`
              : ""
          }`;
      } catch (e) {
        ruttUt.innerHTML = `<p class="flode-fel">${esc(e instanceof Error ? e.message : String(e))}</p>`;
      }
    },
    { signal },
  );
}
