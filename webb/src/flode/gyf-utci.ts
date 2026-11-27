/*
 * Steg 2-paneler för Svensk Grönytefaktor (GYF, U16) och Värmestress UTCI / T_mrt (U15).
 *
 * Principer:
 * - Alla tal räknas deterministiskt i backend (`GET/POST /api/analys/gyf` och `/api/analys/varmestress`).
 *   Klienten formaterar bara med svensk formatering (`tal.ts`) och uppskattar aldrig egna tal.
 * - Ren, avskalad svartvit UI-krom med tydlig `SKISS`-märkning.
 * - Noll WebGL-/Vite-specifika statiska importer så hela modulen kan testas direkt i Node (`npm test`).
 */
import type { Teckenforklaring, Utbredning } from "../fragor-typer.ts";
import { esc } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";

export type GyfProfilId = "stockholm" | "goteborg" | "malmo";

export const GYF_PROFILER: ReadonlyArray<{ id: GyfProfilId; namn: string; kommun: string }> = [
  { id: "stockholm", namn: "Stockholm", kommun: "Stockholms stad" },
  { id: "goteborg", namn: "Göteborg", kommun: "Göteborgs Stad" },
  { id: "malmo", namn: "Malmö", kommun: "Malmö stad" },
];

/** Väljer kommunprofil för GYF automatiskt ur projektets `kommun` eller geografireferens. */
export function forvaldGyfProfilForKommun(
  kommun?: string | null,
  geografi?: string | null,
): GyfProfilId {
  const raw = (kommun || geografi || "").trim().toLowerCase();
  if (raw.startsWith("goteborg") || raw.includes("göteborg")) return "goteborg";
  if (raw.startsWith("malmo") || raw.includes("malmö")) return "malmo";
  if (raw.startsWith("stockholm")) return "stockholm";
  return "stockholm";
}

export interface GyfDelfaktorPost {
  faktor_id: string;
  namn: string;
  faktor: number;
  antal_celler: number;
  area_m2: number;
  andel_av_tomt_pct: number;
  eko_effektiv_area_m2: number;
  bidrag_gyf: number;
}

export interface GyfTillaggPost {
  tillagg_id: string;
  namn: string;
  area_m2: number;
  faktor: number;
  andel_av_tomt_pct: number;
  eko_effektiv_area_m2: number;
  bidrag_gyf: number;
}

export interface GyfResultatDel {
  profil_id: string;
  profil_namn: string;
  kommun: string;
  metodstatus: string;
  riktmarke: number;
  cellstorlek_m: number;
  tomt_celler: number;
  tomt_area_m2: number;
  grund_eko_effektiv_area_m2: number;
  tillagg_eko_effektiv_area_m2: number;
  eko_effektiv_area_m2: number;
  gyf: number;
  uppfyller_mal: boolean;
  saknas_till_mal_m2: number;
  delfaktorer: GyfDelfaktorPost[];
  tillagg: GyfTillaggPost[];
  flaggade_celler: number;
  flaggad_area_m2: number;
  flaggad_andel_pct: number;
  okanda_koder: number[];
  ogiltiga_celler: number;
  varningar: string[];
}

export interface GyfJamforelseDel {
  delta_gyf: number;
  delta_eko_effektiv_area_m2: number;
  delta_grund_eko_effektiv_area_m2?: number;
  delta_tillagg_eko_effektiv_area_m2?: number;
  delta_tomt_area_m2?: number;
  uppfyller_mal: boolean;
  uppfyller_mal_fore: boolean;
  uppfyller_mal_efter: boolean;
  saknas_till_mal_fore_m2: number;
  saknas_till_mal_m2: number;
  varningar: string[];
}

export interface GyfAnalysSvar {
  format: string;
  omrade_id: string;
  profil: GyfProfilId | string;
  profil_namn: string;
  kommun: string;
  riktmarke: number;
  scenario: string | null;
  metodstatus: string;
  fore: GyfResultatDel;
  efter: GyfResultatDel | null;
  jamforelse: GyfJamforelseDel | null;
  delfaktorer: GyfDelfaktorPost[];
  tillagg: GyfTillaggPost[];
  flaggad_area_m2: number;
  flaggad_andel_pct: number;
  varningar: string[];
}

export interface VarmestressParam {
  t_luft_c: number;
  relativ_fuktighet_pct: number;
  vind_10m_m_s: number;
  sol_direkt_w_m2: number;
  sol_diffus_w_m2: number;
  solhojd_grad: number;
  solazimut_grad?: number;
}

export const STANDARD_VARMESTRESS_PARAM: Readonly<VarmestressParam> = {
  t_luft_c: 30.0,
  relativ_fuktighet_pct: 45.0,
  vind_10m_m_s: 1.5,
  sol_direkt_w_m2: 750.0,
  sol_diffus_w_m2: 120.0,
  solhojd_grad: 50.0,
  solazimut_grad: 180.0,
};

export type VarmestressForvalId = "varmebolja" | "sommardag" | "anpassa";

export interface VarmestressForvalPost {
  id: Exclude<VarmestressForvalId, "anpassa">;
  etikett: string;
  param: Readonly<VarmestressParam>;
}

/** En-klicks väderförval för Mikroklimat (UTCI / T_mrt) i Steg 2. */
export const VARMESTRESS_FORVAL: ReadonlyArray<VarmestressForvalPost> = [
  {
    id: "varmebolja",
    etikett: "Värmebölja · 30 °C, 21 juli kl. 14",
    param: { ...STANDARD_VARMESTRESS_PARAM },
  },
  {
    id: "sommardag",
    etikett: "Sommardag · 25 °C",
    param: {
      t_luft_c: 25.0,
      relativ_fuktighet_pct: 50.0,
      vind_10m_m_s: 2.5,
      sol_direkt_w_m2: 650.0,
      sol_diffus_w_m2: 110.0,
      solhojd_grad: 48.0,
      solazimut_grad: 180.0,
    },
  },
];

export type VarmestressLagerRoll = "utci_c" | "t_mrt_c";

export interface VarmestressLagerRef {
  id: string;
  roll: VarmestressLagerRoll | string;
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

export interface UtciStressklassPost {
  klass_id: number;
  namn_sv: string;
  nedre_grans_c: number | null;
  ovre_grans_c: number | null;
  andel: number;
  andel_pct: number;
}

export interface VarmestressStatBlock {
  utci_medel_c: number | null;
  utci_min_c?: number | null;
  utci_max_c: number | null;
  utci_p90_c?: number | null;
  tmrt_medel_c: number | null;
  tmrt_min_c?: number | null;
  tmrt_max_c: number | null;
  tmrt_p90_c?: number | null;
  antal_aktiva_markceller?: number;
  antal_giltiga_celler?: number;
  utanfor_giltighetsomrade_pct: number;
  stressklasser: UtciStressklassPost[];
  varningar: string[];
}

export interface VarmestressJamforelseBlock {
  delta_utci_medel_c: number | null;
  delta_utci_min_c?: number | null;
  delta_utci_max_c?: number | null;
  delta_tmrt_medel_c: number | null;
  delta_t_mrt_medel_c?: number | null;
  delta_andel_stark_varmestress_pct?: number;
  delta_andel_mycket_stark_varmestress_pct?: number;
  delta_andel_extrem_varmestress_pct?: number;
  antal_jamforda_utci_celler?: number;
  varningar: string[];
}

export interface VarmestressAnalysSvar {
  format: string;
  omrade_id: string;
  scenario: string | null;
  metodstatus: string;
  parametrar: VarmestressParam;
  geometri?: {
    cellstorlek_m: number;
    utbredning: Utbredning;
    svf_medel_fore: number;
    solbelyst_andel_fore_pct: number;
    trad_andel_fore_pct: number;
    svf_medel_efter?: number;
    solbelyst_andel_efter_pct?: number;
    trad_andel_efter_pct?: number;
  };
  utci_medel_c: number | null;
  utci_max_c: number | null;
  tmrt_medel_c: number | null;
  tmrt_max_c: number | null;
  utanfor_giltighetsomrade_pct: number;
  stressklasser: UtciStressklassPost[];
  fore: VarmestressStatBlock;
  efter: VarmestressStatBlock | null;
  jamforelse: VarmestressJamforelseBlock | null;
  lager: {
    utci_c_fore?: VarmestressLagerRef | null;
    t_mrt_c_fore?: VarmestressLagerRef | null;
    utci_c_efter?: VarmestressLagerRef | null;
    t_mrt_c_efter?: VarmestressLagerRef | null;
    utci_c_skillnad?: VarmestressLagerRef | null;
    t_mrt_c_skillnad?: VarmestressLagerRef | null;
  };
  varningar: string[];
}

export interface GyfPanelTillstand {
  profil: GyfProfilId;
  laddar: boolean;
  fel: string | null;
  svar: GyfAnalysSvar | null;
  scenario: string | null;
}

export interface VarmestressPanelTillstand {
  param: VarmestressParam;
  laddar: boolean;
  fel: string | null;
  svar: VarmestressAnalysSvar | null;
  scenario: string | null;
  aktivtLagerRoll: VarmestressLagerRoll | null;
  forval?: VarmestressForvalId;
  aktivtLage?: string;
}

const arTal = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Bygger query-URL för skrivlös `GET /api/analys/gyf` (`lasare`-kompatibel). */
export function gyfAnalysUrl(
  omrade_id: string,
  profil: GyfProfilId = "stockholm",
  scenario?: string | null,
): string {
  const q = new URLSearchParams();
  q.set("omrade_id", omrade_id);
  q.set("profil", profil);
  if (scenario) q.set("scenario", scenario);
  return `/api/analys/gyf?${q.toString()}`;
}

/** Bygger query-URL för skrivlös `GET /api/analys/varmestress` (`lasare`-kompatibel). */
export function varmestressAnalysUrl(
  omrade_id: string,
  param: Partial<VarmestressParam> = {},
  scenario?: string | null,
): string {
  const p: VarmestressParam = { ...STANDARD_VARMESTRESS_PARAM, ...param };
  const q = new URLSearchParams();
  q.set("omrade_id", omrade_id);
  q.set("t_luft_c", String(p.t_luft_c));
  q.set("relativ_fuktighet_pct", String(p.relativ_fuktighet_pct));
  q.set("vind_10m_m_s", String(p.vind_10m_m_s));
  q.set("sol_direkt_w_m2", String(p.sol_direkt_w_m2));
  q.set("sol_diffus_w_m2", String(p.sol_diffus_w_m2));
  q.set("solhojd_grad", String(p.solhojd_grad));
  if (arTal(p.solazimut_grad)) q.set("solazimut_grad", String(p.solazimut_grad));
  if (scenario) q.set("scenario", scenario);
  return `/api/analys/varmestress?${q.toString()}`;
}

/** Tolkar decimaltal med svensk komma- eller punktnotation. */
export function tolkaSvensktTal(text: string): number | null {
  const ren = text.trim().replace(/\s+/g, "").replace(",", ".");
  if (!ren) return null;
  const v = Number(ren);
  return Number.isFinite(v) ? v : null;
}

/** Validerar väderparametrar innan serveranrop. Returnerar svensk feltext eller null. */
export function valideraVarmestressParam(p: VarmestressParam): string | null {
  if (!arTal(p.t_luft_c) || p.t_luft_c <= -273.15) return "Lufttemperatur måste vara ett tal över −273,15 °C.";
  if (!arTal(p.relativ_fuktighet_pct) || p.relativ_fuktighet_pct < 0 || p.relativ_fuktighet_pct > 100) {
    return "Relativ fuktighet måste ligga mellan 0 och 100 %.";
  }
  if (!arTal(p.vind_10m_m_s) || p.vind_10m_m_s < 0) return "Vindhastighet (10 m) måste vara >= 0 m/s.";
  if (!arTal(p.sol_direkt_w_m2) || p.sol_direkt_w_m2 < 0) return "Direkt solstrålning måste vara >= 0 W/m².";
  if (!arTal(p.sol_diffus_w_m2) || p.sol_diffus_w_m2 < 0) return "Diffus solstrålning måste vara >= 0 W/m².";
  if (!arTal(p.solhojd_grad) || p.solhojd_grad < -90 || p.solhojd_grad > 90) {
    return "Solhöjd måste ligga mellan −90° och 90°.";
  }
  return null;
}

function formateraDelta(v: number, decimaler: number, enhet = ""): string {
  const tecken = v > 1e-9 ? "+" : v < -1e-9 ? "−" : "";
  const tal = formatera(Math.abs(v), decimaler);
  return `${tecken}${tal}${enhet ? ` ${enhet}` : ""}`;
}

/** Väljer rätt värmestresslager (`fore`, `efter` eller `skillnad`) för kartläget i Steg 2. */
export function valjVarmestressLager(
  svar: VarmestressAnalysSvar | null,
  roll: VarmestressLagerRoll | null,
  lage: string,
): VarmestressLagerRef | null {
  if (!svar || !roll) return null;
  if (roll === "utci_c") {
    if (lage === "efter") return svar.lager.utci_c_efter ?? svar.lager.utci_c_fore ?? null;
    if (lage === "skillnad") return svar.lager.utci_c_skillnad ?? svar.lager.utci_c_fore ?? null;
    return svar.lager.utci_c_fore ?? null;
  }
  if (lage === "efter") return svar.lager.t_mrt_c_efter ?? svar.lager.t_mrt_c_fore ?? null;
  if (lage === "skillnad") return svar.lager.t_mrt_c_skillnad ?? svar.lager.t_mrt_c_fore ?? null;
  return svar.lager.t_mrt_c_fore ?? null;
}

/** Renderar GYF-panelen (Steg 2) i stram svartvit UI-krom med tydlig `SKISS`-märkning. */
export function gyfPanelHtml(t: GyfPanelTillstand): string {
  const s = t.svar;
  const aktiv = s?.efter ?? s?.fore ?? null;
  const profilOptions = GYF_PROFILER.map(
    (p) => `<option value="${p.id}"${p.id === t.profil ? " selected" : ""}>${esc(p.namn)}</option>`,
  ).join("");

  let kropp = `<p class="flode-liten flode-dampad">Välj kommunprofil och beräkna grönytefaktor direkt mot projektets K1-markslag${t.scenario ? ` och förslaget <strong>${esc(t.scenario)}</strong>` : ""}.</p>`;
  if (t.laddar) {
    kropp = `<p class="flode-liten flode-dampad" role="status">Beräknar grönytefaktor …</p>`;
  } else if (t.fel) {
    kropp = `<p class="flode-liten flode-fel" role="alert">${esc(t.fel)}</p>`;
  } else if (s && aktiv) {
    const rikt = formatera(s.riktmarke, 2);
    const gyfTxt = formatera(aktiv.gyf, 2);
    const malTxt = aktiv.uppfyller_mal
      ? `Uppfyller riktmärket ${rikt}`
      : `Saknar ${formatera(aktiv.saknas_till_mal_m2, 0)} m² ekoeffektiv yta till ${rikt}`;
    const deltaHtml =
      s.jamforelse && s.efter
        ? `<div><dt>Förändring (ΔGYF)</dt><dd>${esc(formateraDelta(s.jamforelse.delta_gyf, 3))} <span class="flode-dampad">(före ${esc(formatera(s.fore.gyf, 2))})</span></dd></div>`
        : "";
    const synligaFaktorer = s.delfaktorer.filter((d) => d.area_m2 > 0 || d.antal_celler > 0);
    const tabellRader = (synligaFaktorer.length > 0 ? synligaFaktorer : s.delfaktorer)
      .map(
        (d) =>
          `<tr><td>${esc(d.namn)}</td><td>${esc(formatera(d.faktor, 2))}</td><td>${esc(formatera(d.area_m2, 0))} m²</td><td>${esc(formatera(d.andel_av_tomt_pct, 1))} %</td><td>${esc(formatera(d.bidrag_gyf, 3))}</td></tr>`,
      )
      .join("");
    const tillaggHtml =
      s.tillagg.length > 0
        ? `<div class="ar-gyf-tillagg"><span class="ar-sektion-rubrik">Tilläggsfaktorer (krontäckning / skikt)</span><ul class="flode-liten">${s.tillagg
            .map(
              (x) =>
                `<li><strong>${esc(x.namn)}</strong>: ${esc(formatera(x.area_m2, 0))} m² × ${esc(formatera(x.faktor, 2))} = +${esc(formatera(x.bidrag_gyf, 3))} GYF</li>`,
            )
            .join("")}</ul></div>`
        : "";
    const flaggadHtml =
      s.flaggad_area_m2 > 0
        ? `<p class="flode-ruta varning flode-liten">Flaggad / osäker yta: <strong>${esc(formatera(s.flaggad_area_m2, 1))} m²</strong> (${esc(formatera(s.flaggad_andel_pct, 1))} % av tomtarean) räknas med faktor 0,0 och gissas aldrig.</p>`
        : "";
    const varningHtml =
      s.varningar.length > 0
        ? `<ul class="flode-liten flode-dampad ar-gyf-varningar">${s.varningar.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>`
        : "";

    kropp = `
      <dl class="flode-tal ar-gyf-tal">
        <div><dt>GYF (${esc(s.kommun)})</dt><dd><strong>${esc(gyfTxt)}</strong> <span class="flode-dampad">/ riktmärke ${esc(rikt)}</span></dd></div>
        <div><dt>Målstatus</dt><dd>${esc(malTxt)}</dd></div>
        ${deltaHtml}
        <div><dt>Ekoeffektiv yta</dt><dd>${esc(formatera(aktiv.eko_effektiv_area_m2, 0))} m² <span class="flode-dampad">av ${esc(formatera(aktiv.tomt_area_m2, 0))} m²</span></dd></div>
      </dl>
      ${flaggadHtml}
      <table class="flode-tabell ar-gyf-tabell">
        <thead><tr><th>Delfaktor</th><th>Vikt</th><th>Area</th><th>Andel</th><th>Bidrag</th></tr></thead>
        <tbody>${tabellRader}</tbody>
      </table>
      ${tillaggHtml}
      ${varningHtml}`;
  }

  return `
    <section class="ar-doman-kort ar-gyf-panel" id="ar-gyf-panel" aria-label="Grönytefaktor (GYF)">
      <div class="ar-doman-huvud">
        <h2 class="ar-doman-rubrik">Grönytefaktor (GYF)</h2>
        <span class="ar-skiss-badge" title="Metodstatus: skiss (schablonvikter i data/gyf/profiler.toml)">SKISS</span>
      </div>
      <div class="ar-utci-gyf-kropp">
        <div class="ar-gyf-kontroller">
          <label class="flode-liten">Kommunprofil
            <select id="ar-gyf-profil" aria-label="GYF-kommunprofil">${profilOptions}</select>
          </label>
          <button type="button" class="flode-knapp svart liten" id="ar-gyf-kor"${t.laddar ? " disabled" : ""}>${t.laddar ? "Beräknar …" : "Beräkna GYF"}</button>
        </div>
        <div id="ar-gyf-ut">${kropp}</div>
      </div>
    </section>`;
}

/** Renderar Värmestress-panelen (UTCI / T_mrt, Steg 2) i stram svartvit UI-krom med `SKISS`-märkning. */
export function varmestressPanelHtml(t: VarmestressPanelTillstand): string {
  const s = t.svar;
  const p = t.param;
  const forval: VarmestressForvalId = t.forval ?? "varmebolja";
  const aktivtLager = valjVarmestressLager(s, t.aktivtLagerRoll, t.aktivtLage ?? "nulage");

  let kropp = `<p class="flode-liten flode-dampad">Välj ett väderförval ovan för att beräkna 6-riktnings T<sub>mrt</sub> och UTCI ur projektets K1 v2-geometri (verklig SVF och skugga)${t.scenario ? ` för nuläge och förslag <strong>${esc(t.scenario)}</strong>` : ""}.</p>`;
  if (t.laddar) {
    kropp = `<p class="flode-liten flode-dampad" role="status">Beräknar SVF, skugga, T<sub>mrt</sub> och UTCI …</p>`;
  } else if (t.fel) {
    kropp = `<p class="flode-liten flode-fel" role="alert">${esc(t.fel)}</p>`;
  } else if (s) {
    const utciMedel = arTal(s.utci_medel_c) ? `${formatera(s.utci_medel_c, 1)} °C` : "–";
    const utciMax = arTal(s.utci_max_c) ? `${formatera(s.utci_max_c, 1)} °C` : "–";
    const tmrtMedel = arTal(s.tmrt_medel_c) ? `${formatera(s.tmrt_medel_c, 1)} °C` : "–";
    const tmrtMax = arTal(s.tmrt_max_c) ? `${formatera(s.tmrt_max_c, 1)} °C` : "–";

    let jfrHtml = "";
    if (s.jamforelse && arTal(s.jamforelse.delta_utci_medel_c)) {
      const du = s.jamforelse.delta_utci_medel_c;
      const dt = s.jamforelse.delta_tmrt_medel_c;
      const kylText = du < -0.05 ? "svalare" : du > 0.05 ? "varmare" : "oförändrat";
      jfrHtml = `
        <div><dt>Före/efter ΔUTCI (medel)</dt><dd><strong>${esc(formateraDelta(du, 2, "°C"))}</strong> <span class="flode-dampad">(${kylText})</span></dd></div>
        ${arTal(dt) ? `<div><dt>Före/efter ΔT<sub>mrt</sub> (medel)</dt><dd>${esc(formateraDelta(dt, 2, "°C"))}</dd></div>` : ""}`;
    }

    const synligaKlasser = s.stressklasser
      .filter((k) => k.andel_pct > 0.01)
      .sort((a, b) => b.klass_id - a.klass_id);
    const klassRader = (synligaKlasser.length > 0 ? synligaKlasser : s.stressklasser.filter((k) => k.klass_id >= 0).reverse())
      .map((k) => {
        const bredd = Math.min(100, Math.max(0, Math.round(k.andel_pct)));
        return `<tr>
          <td>${esc(k.namn_sv)}</td>
          <td class="ar-vs-stapel-cell"><span class="ar-vs-stapel" style="width:${bredd}%"></span></td>
          <td>${esc(formatera(k.andel_pct, 1))} %</td>
        </tr>`;
      })
      .join("");

    const utanforHtml =
      s.utanfor_giltighetsomrade_pct > 0
        ? `<p class="flode-ruta varning flode-liten"><strong>${esc(formatera(s.utanfor_giltighetsomrade_pct, 1))} %</strong> av aktiva markceller ligger utanför Brödes (2012) giltighetsintervall för UTCI och extrapoleras inte.</p>`
        : "";
    const varningHtml =
      s.varningar.length > 0
        ? `<ul class="flode-liten flode-dampad ar-gyf-varningar">${s.varningar.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>`
        : "";

    const geoHtml = s.geometri
      ? `<p class="flode-liten flode-dampad">Geometri: medel-SVF ${esc(formatera(s.geometri.svf_medel_fore, 2))}, solbelyst mark ${esc(formatera(s.geometri.solbelyst_andel_fore_pct, 0))} %${s.geometri.trad_andel_fore_pct > 0 ? `, trädkrontäckning ${esc(formatera(s.geometri.trad_andel_fore_pct, 1))} %` : ""}.</p>`
      : "";

    const inlineLegendHtml = aktivtLager
      ? `<div class="ar-inline-legend" aria-label="Teckenförklaring för värmestress">${varmelagerTeckenHtml(aktivtLager)}</div>`
      : "";

    kropp = `
      <div class="flode-segment liten ar-vs-lagerval" role="group" aria-label="Visa värmestresslager på kartan">
        <button type="button" data-vs-lager="utci_c" aria-pressed="${t.aktivtLagerRoll === "utci_c"}">UTCI °C</button>
        <button type="button" data-vs-lager="t_mrt_c" aria-pressed="${t.aktivtLagerRoll === "t_mrt_c"}">T_mrt °C</button>
        <button type="button" data-vs-lager="av" aria-pressed="${t.aktivtLagerRoll === null}">Dölj</button>
      </div>
      ${inlineLegendHtml}
      <dl class="flode-tal ar-vs-tal">
        <div><dt>UTCI (medel / max)</dt><dd><strong>${esc(utciMedel)}</strong> <span class="flode-dampad">/ ${esc(utciMax)}</span></dd></div>
        <div><dt>T<sub>mrt</sub> (medel / max)</dt><dd><strong>${esc(tmrtMedel)}</strong> <span class="flode-dampad">/ ${esc(tmrtMax)}</span></dd></div>
        ${jfrHtml}
      </dl>
      ${utanforHtml}
      <table class="flode-tabell ar-vs-tabell">
        <thead><tr><th>UTCI-stressklass</th><th>Fördelning</th><th>Andel</th></tr></thead>
        <tbody>${klassRader}</tbody>
      </table>
      ${geoHtml}
      ${varningHtml}`;
  }

  const forvalKnappar = [
    ...VARMESTRESS_FORVAL.map(
      (f) =>
        `<button type="button" data-vs-forval="${f.id}" aria-pressed="${forval === f.id}"${t.laddar ? " disabled" : ""}>${esc(f.etikett)}</button>`,
    ),
    `<button type="button" data-vs-forval="anpassa" aria-pressed="${forval === "anpassa"}">Anpassa själv</button>`,
  ].join("");

  const paramSamman =
    forval !== "anpassa"
      ? `<p class="flode-liten flode-dampad ar-vs-paramrad">${esc(formateraHogst(p.t_luft_c, 1))} °C · ${esc(formateraHogst(p.relativ_fuktighet_pct, 0))} % RH · vind ${esc(formateraHogst(p.vind_10m_m_s, 1))} m/s · solhöjd ${esc(formateraHogst(p.solhojd_grad, 0))}°</p>`
      : "";

  return `
    <section class="ar-doman-kort ar-vs-panel" id="ar-vs-panel" aria-label="Mikroklimat och värmestress">
      <div class="ar-doman-huvud">
        <h2 class="ar-doman-rubrik">Mikroklimat · Värmestress (UTCI / T<sub>mrt</sub>)</h2>
        <span class="ar-skiss-badge" title="Metodstatus: skiss (Bröde 2012 + 6-riktnings T_mrt)">SKISS</span>
      </div>
      <div class="ar-utci-gyf-kropp">
        <div class="ar-vs-forval" role="group" aria-label="Väderförval för värmestress">${forvalKnappar}</div>
        ${paramSamman}
        <div class="ar-vs-grid"${forval === "anpassa" ? "" : " hidden"}>
          <label class="flode-liten">Lufttemp (°C)
            <input id="ar-vs-tluft" type="text" inputmode="decimal" value="${esc(formateraHogst(p.t_luft_c, 1))}" />
          </label>
          <label class="flode-liten">Rel. fuktighet (%)
            <input id="ar-vs-rh" type="text" inputmode="decimal" value="${esc(formateraHogst(p.relativ_fuktighet_pct, 1))}" />
          </label>
          <label class="flode-liten">Vind 10 m (m/s)
            <input id="ar-vs-vind" type="text" inputmode="decimal" value="${esc(formateraHogst(p.vind_10m_m_s, 2))}" />
          </label>
          <label class="flode-liten">Solhöjd (°)
            <input id="ar-vs-solhojd" type="text" inputmode="decimal" value="${esc(formateraHogst(p.solhojd_grad, 1))}" />
          </label>
        </div>
        <div class="ar-vs-atgarder">
          <button type="button" class="flode-knapp svart liten" id="ar-vs-kor"${t.laddar ? " disabled" : ""}>${t.laddar ? "Beräknar …" : "Beräkna &amp; visa värmestress"}</button>
        </div>
        <div id="ar-vs-ut">${kropp}</div>
      </div>
    </section>`;
}

/** Teckenförklaring för ett aktivt värmestresslager (`utci_c` eller `t_mrt_c`). */
export function varmelagerTeckenHtml(lager: VarmestressLagerRef): string {
  const tf = lager.teckenforklaring;
  const titel = `${lager.etikett}${lager.enhet ? ` (${lager.enhet})` : ""} · skiss`;
  if (tf.typ === "divergerande") {
    return `<span class="flode-kort-rubrik">${esc(titel)}</span>
      <span class="flode-band" style="background:linear-gradient(90deg,${esc(tf.farg_minskning)},#f5f5f5,${esc(tf.farg_okning)})"></span>
      <span class="flode-bandtext"><span>−${esc(formatera(Math.abs(tf.max), 1))} ${esc(lager.enhet)}</span><span>+${esc(formatera(Math.abs(tf.max), 1))} ${esc(lager.enhet)}</span></span>
      <span class="flode-liten flode-dampad">Blått = svalare efter åtgärd, rött = varmare.${lager.tak_maskat ? " Tak är maskade." : ""}</span>`;
  }
  if (tf.typ === "kontinuerlig") {
    return `<span class="flode-kort-rubrik">${esc(titel)}</span>
      <span class="flode-band" style="background:linear-gradient(90deg,${tf.stopp.map((c) => esc(c)).join(",")})"></span>
      <span class="flode-bandtext"><span>${esc(formatera(tf.min, 1))}</span><span>${esc(formatera(tf.max, 1))} ${esc(lager.enhet)}</span></span>
      ${lager.tak_maskat ? `<span class="flode-liten flode-dampad">Ljusgrå yta: tak, inte mark.</span>` : ""}`;
  }
  return `<span class="flode-kort-rubrik">${esc(titel)}</span>`;
}

/** Sammanhållet HTML-block för GYF- och Värmestress-panelerna i Steg 2. */
export function gyfUtciPanelerHtml(
  gyf: GyfPanelTillstand,
  varmestress: VarmestressPanelTillstand,
): string {
  return `<section class="ar-gyf-utci" aria-label="Grönytefaktor och värmestress">${gyfPanelHtml(gyf)}${varmestressPanelHtml(varmestress)}</section>`;
}

export interface GyfUtciKopplingOpts {
  projekt: string;
  hamtaScenario(): string | null;
  hamtaLage(): string;
  onVarmelagerAndrat(lager: VarmestressLagerRef | null): void;
  signal: AbortSignal;
  initialProfil?: GyfProfilId;
  hamtaAktivDoman?: () => string;
  onStatusAndrad?: () => void;
}

export interface GyfUtciStatus {
  gyfLaddar: boolean;
  gyfHarSvar: boolean;
  vsLaddar: boolean;
  vsHarSvar: boolean;
}

export interface GyfUtciKontroller {
  uppdateraKontext(): void;
  synkaVarmelager(): void;
  rensaVarmelager(): void;
  sattStandardProfil(profil: GyfProfilId): void;
  sattAktivDoman(doman: string): void;
  status(): GyfUtciStatus;
}

/** Kopplar GYF- och Värmestress-panelerna i Steg 2 mot backend-API och karta. */
export function kopplaGyfUtciPaneler(
  behallare: HTMLElement,
  opts: GyfUtciKopplingOpts,
): GyfUtciKontroller {
  const gyfState: GyfPanelTillstand = {
    profil: opts.initialProfil ?? "stockholm",
    laddar: false,
    fel: null,
    svar: null,
    scenario: opts.hamtaScenario(),
  };
  const vsState: VarmestressPanelTillstand = {
    param: { ...STANDARD_VARMESTRESS_PARAM },
    laddar: false,
    fel: null,
    svar: null,
    scenario: opts.hamtaScenario(),
    aktivtLagerRoll: null,
    forval: "varmebolja",
    aktivtLage: opts.hamtaLage(),
  };

  let manuelltProfilVal = false;
  let aktivDoman: string | null = opts.hamtaAktivDoman ? opts.hamtaAktivDoman() : null;
  let gyfSeq = 0;
  let vsSeq = 0;

  const rita = (): void => {
    vsState.aktivtLage = opts.hamtaLage();
    if (aktivDoman === null) {
      behallare.hidden = false;
      behallare.innerHTML = gyfUtciPanelerHtml(gyfState, vsState);
      return;
    }
    if (aktivDoman === "ekologi") {
      behallare.hidden = false;
      behallare.innerHTML = `<section class="ar-gyf-utci" aria-label="Svensk Grönytefaktor">${gyfPanelHtml(gyfState)}</section>`;
      return;
    }
    if (aktivDoman === "mikroklimat") {
      behallare.hidden = false;
      behallare.innerHTML = `<section class="ar-gyf-utci" aria-label="Mikroklimat och värmestress">${varmestressPanelHtml(vsState)}</section>`;
      return;
    }
    behallare.hidden = true;
    behallare.innerHTML = "";
  };

  const meddelaStatus = (): void => {
    opts.onStatusAndrad?.();
  };

  const lasVsParam = (): VarmestressParam | string => {
    if (vsState.forval !== "anpassa") {
      const fel = valideraVarmestressParam(vsState.param);
      return fel ?? { ...vsState.param };
    }
    const tLuft = tolkaSvensktTal(behallare.querySelector<HTMLInputElement>("#ar-vs-tluft")?.value ?? "");
    const rh = tolkaSvensktTal(behallare.querySelector<HTMLInputElement>("#ar-vs-rh")?.value ?? "");
    const vind = tolkaSvensktTal(behallare.querySelector<HTMLInputElement>("#ar-vs-vind")?.value ?? "");
    const solhojd = tolkaSvensktTal(behallare.querySelector<HTMLInputElement>("#ar-vs-solhojd")?.value ?? "");
    if (tLuft === null || rh === null || vind === null || solhojd === null) {
      return "Ange giltiga tal för lufttemperatur, relativ fuktighet, vind och solhöjd.";
    }
    const ny: VarmestressParam = {
      ...vsState.param,
      t_luft_c: tLuft,
      relativ_fuktighet_pct: rh,
      vind_10m_m_s: vind,
      solhojd_grad: solhojd,
    };
    const fel = valideraVarmestressParam(ny);
    return fel ?? ny;
  };

  const korGyf = async (): Promise<void> => {
    const nr = ++gyfSeq;
    gyfState.scenario = opts.hamtaScenario();
    gyfState.laddar = true;
    gyfState.fel = null;
    rita();
    meddelaStatus();
    try {
      const { lasJson } = await import("../api.ts");
      const svar = await lasJson<GyfAnalysSvar>(
        gyfAnalysUrl(opts.projekt, gyfState.profil, gyfState.scenario),
      );
      if (nr !== gyfSeq) return;
      gyfState.svar = svar;
      gyfState.fel = null;
    } catch (e) {
      if (nr !== gyfSeq) return;
      gyfState.fel = e instanceof Error ? e.message : String(e);
    } finally {
      if (nr === gyfSeq) {
        gyfState.laddar = false;
        rita();
        meddelaStatus();
      }
    }
  };

  const synkaVarmelager = (): void => {
    vsState.aktivtLage = opts.hamtaLage();
    opts.onVarmelagerAndrat(valjVarmestressLager(vsState.svar, vsState.aktivtLagerRoll, vsState.aktivtLage));
  };

  const korVarmestress = async (): Promise<void> => {
    const tolkad = lasVsParam();
    if (typeof tolkad === "string") {
      vsState.fel = tolkad;
      rita();
      return;
    }
    const nr = ++vsSeq;
    vsState.param = tolkad;
    vsState.scenario = opts.hamtaScenario();
    vsState.laddar = true;
    vsState.fel = null;
    rita();
    meddelaStatus();
    try {
      const { lasJson } = await import("../api.ts");
      const svar = await lasJson<VarmestressAnalysSvar>(
        varmestressAnalysUrl(opts.projekt, vsState.param, vsState.scenario),
      );
      if (nr !== vsSeq) return;
      vsState.svar = svar;
      vsState.fel = null;
      if (vsState.aktivtLagerRoll === null) vsState.aktivtLagerRoll = "utci_c";
      synkaVarmelager();
    } catch (e) {
      if (nr !== vsSeq) return;
      vsState.fel = e instanceof Error ? e.message : String(e);
    } finally {
      if (nr === vsSeq) {
        vsState.laddar = false;
        rita();
        meddelaStatus();
      }
    }
  };

  behallare.addEventListener(
    "change",
    (h) => {
      const el = h.target as HTMLElement;
      if (el.id === "ar-gyf-profil") {
        const val = (el as HTMLSelectElement).value as GyfProfilId;
        if (GYF_PROFILER.some((p) => p.id === val)) {
          manuelltProfilVal = true;
          gyfState.profil = val;
          if (gyfState.svar) void korGyf();
        }
      }
    },
    { signal: opts.signal },
  );

  behallare.addEventListener(
    "click",
    (h) => {
      const m = (h.target as Element).closest<HTMLElement>("#ar-gyf-kor,#ar-vs-kor,[data-vs-lager],[data-vs-forval]");
      if (!m) return;
      if (m.id === "ar-gyf-kor") {
        void korGyf();
        return;
      }
      if (m.id === "ar-vs-kor") {
        void korVarmestress();
        return;
      }
      const fv = m.dataset.vsForval as VarmestressForvalId | undefined;
      if (fv) {
        vsState.forval = fv;
        vsState.fel = null;
        if (fv === "anpassa") {
          rita();
          behallare.querySelector<HTMLInputElement>("#ar-vs-tluft")?.focus();
          return;
        }
        const post = VARMESTRESS_FORVAL.find((x) => x.id === fv);
        if (post) {
          vsState.param = { ...post.param };
          void korVarmestress();
        }
        return;
      }
      const lag = m.dataset.vsLager;
      if (lag === "utci_c" || lag === "t_mrt_c") {
        vsState.aktivtLagerRoll = lag;
        synkaVarmelager();
        rita();
      } else if (lag === "av") {
        vsState.aktivtLagerRoll = null;
        synkaVarmelager();
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
      const andrad = nySc !== gyfState.scenario || nySc !== vsState.scenario || nyLage !== vsState.aktivtLage;
      gyfState.scenario = nySc;
      vsState.scenario = nySc;
      vsState.aktivtLage = nyLage;
      if (andrad) rita();
      synkaVarmelager();
    },
    synkaVarmelager,
    rensaVarmelager: () => {
      if (vsState.aktivtLagerRoll !== null) {
        vsState.aktivtLagerRoll = null;
        opts.onVarmelagerAndrat(null);
        rita();
      }
    },
    sattStandardProfil: (profil: GyfProfilId) => {
      if (manuelltProfilVal || gyfState.profil === profil) return;
      gyfState.profil = profil;
      rita();
    },
    sattAktivDoman: (doman: string) => {
      aktivDoman = doman;
      rita();
    },
    status: () => ({
      gyfLaddar: gyfState.laddar,
      gyfHarSvar: gyfState.svar !== null,
      vsLaddar: vsState.laddar,
      vsHarSvar: vsState.svar !== null,
    }),
  };
}
