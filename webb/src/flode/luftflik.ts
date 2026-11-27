/*
 * Luft-flik i steg 4 · Resultat (PLAN.md Uppdrag 3).
 * Visas bara när projektet har luftkörningar. Hanterar kartlagren vindkomfort, vindfara,
 * vindförstärkning och årsmedel samt MKN-intervall som tal, med tydlig skiss-märkning.
 * Inga tal räknas fram här: alla siffror kommer ur körningens tabeller eller MKN-svaret.
 */
import type { LagerPost, MatvardeTabell } from "../fragor-typer.ts";
import { esc } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";
import type { ProjektVy } from "../types.ts";
import { lagerVal, type LagerVal } from "./analyslager.ts";
import { formateraMknIntervall, tal, type MknIntervallSvar, type Tal } from "./nyckeltal.ts";
import { talListaHtml } from "./panelhtml.ts";

export const LUFT_AMNE = "luft" as const;
export const LUFT_FLIK_NAMN = "Luft";
export const LUFT_SKISS_NOTIS =
  "Metodstatus: skiss. Vind- och haltberäkningen är inte validerad mot vindtunnel eller mätstationer och ska användas som tidigt planeringsunderlag.";

export interface LuftRollMeta {
  etikett: string;
  enhet: string;
  ordning: number;
}

/** De fyra primära luftlagren med enhet och visningsordning. */
export const LUFT_ROLLER: Record<string, LuftRollMeta> = {
  komfortklass: { etikett: "Vindkomfort (klass)", enhet: "klass", ordning: 0 },
  faraklass: { etikett: "Vindfara (klass)", enhet: "klass", ordning: 1 },
  forstarkning: { etikett: "Vindförstärkning på gånghöjd", enhet: "gånger", ordning: 2 },
  arsmedel: { etikett: "Årsmedelhalt", enhet: "µg/m³", ordning: 3 },
};

/** Sant bara när minst en vy hör till ämnet luft (demokvarteret saknar luftkörningar). */
export function harLuftKorningar(vyer: readonly Pick<ProjektVy, "avsnitt">[]): boolean {
  return vyer.some((v) => v.avsnitt === LUFT_AMNE);
}

/** Flikknappen för Luft i steg 4 (dold när projektet saknar luftkörningar). */
export function luftFlikKnappHtml(synlig: boolean, aktiv: boolean): string {
  return `<button type="button" role="tab" data-amne="${LUFT_AMNE}" aria-selected="${aktiv}"${synlig ? "" : " hidden"}>${LUFT_FLIK_NAMN}</button>`;
}

/** Luftens kartlager i körningen, med garanterad enhet och fast ordning (komfort, fara, förstärkning, årsmedel). */
export function luftLagerVal(lager: readonly LagerPost[], vy: Pick<ProjektVy, "fore_lager" | "efter_lager">): LagerVal[] {
  return lagerVal(lager, vy, LUFT_AMNE)
    .map((v) => {
      const meta = LUFT_ROLLER[v.roll];
      if (!meta) return v;
      return {
        ...v,
        etikett: v.etikett && v.etikett !== v.roll ? v.etikett : meta.etikett,
        enhet: v.enhet || meta.enhet,
      };
    })
    .sort((a, b) => {
      const oa = LUFT_ROLLER[a.roll]?.ordning ?? 99;
      const ob = LUFT_ROLLER[b.roll]?.ordning ?? 99;
      return Number(a.kontroll) - Number(b.kontroll) || oa - ob || a.etikett.localeCompare(b.etikett, "sv");
    });
}

const num = (o: Record<string, unknown> | undefined, ...nycklar: string[]): number | null => {
  if (!o) return null;
  for (const k of nycklar) {
    const v = o[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return null;
};

/** Nyckeltal ur luftkörningens mätvärdestabeller och eventuellt MKN-intervall. Saknade tal blir "–". */
export function luftNyckeltal(
  tabeller: readonly MatvardeTabell[],
  foreScen: string | undefined,
  efterScen: string | undefined,
  mkn?: MknIntervallSvar | null,
): Tal[] {
  const luftTab = tabeller.filter((t) => t.losare.startsWith("luft.") || t.analys.toLowerCase().includes("luft") || t.analys.toLowerCase().includes("vind"));
  const vardenFor = (scen: string | undefined): Record<string, unknown> | undefined => {
    if (!scen) return undefined;
    const samman: Record<string, unknown> = {};
    let hittad = false;
    for (const tab of luftTab) {
      const rad = tab.rader.find((r) => r.scenario === scen);
      if (rad) {
        Object.assign(samman, rad.varden);
        hittad = true;
      }
    }
    return hittad ? samman : undefined;
  };
  const vf = vardenFor(foreScen);
  const ve = vardenFor(efterScen ?? foreScen);
  const ut: Tal[] = [
    {
      etikett: "Max vindförstärkning före",
      varde: tal(num(vf, "max_forstarkning", "forstarkning_max"), 2, "gånger"),
    },
    {
      etikett: "Max vindförstärkning efter",
      varde: tal(num(ve, "max_forstarkning", "forstarkning_max"), 2, "gånger"),
    },
    {
      etikett: "Årsmedelhalt max",
      varde: tal(num(ve, "max_arsmedel_ug_m3", "arsmedel_max_ug_m3", "arsmedel_ug_m3"), 1, "µg/m³"),
    },
    {
      etikett: "Andel yta med vindfara",
      varde: tal(num(ve, "andel_fara_procent", "vindfara_procent"), 1, "%"),
    },
  ];
  if (mkn) {
    ut.push(...formateraMknIntervall(mkn).tal);
  }
  return ut;
}

export interface LuftPanelIndata {
  korningId: string;
  roller: readonly LagerVal[];
  tal: readonly Tal[];
  mkn?: MknIntervallSvar | null;
  las?: string;
  lageIdag?: boolean;
}

/** Luft-flikens panelinnehåll i steg 4, med skiss-märkning, lagerenheter och nyckeltal. */
export function luftPanelHtml(indata: LuftPanelIndata): string {
  const rubrik = indata.lageIdag ? "Läget idag: luft och vind (skiss)" : "Luft och vind på gånghöjd (skiss)";
  const mknStatus = indata.mkn ? `<p class="flode-ruta">${esc(formateraMknIntervall(indata.mkn).status)}</p>` : "";
  const lagerLista =
    indata.roller.length > 0
      ? `<ul class="flode-liten flode-dampad" id="rs-luft-lager">${indata.roller
          .map((l) => `<li>${esc(l.etikett)}${l.enhet ? ` (${esc(l.enhet)})` : ""}</li>`)
          .join("")}</ul>`
      : "";
  return `
    <p class="flode-liten"><span class="flode-skiss-marke">Skiss</span> ${esc(LUFT_SKISS_NOTIS)}</p>
    <h1 class="flode-slutsats" id="rs-rubrik">${esc(rubrik)}</h1>
    ${talListaHtml(indata.tal)}
    ${mknStatus}
    ${lagerLista}
    <p class="flode-liten flode-dampad">Alla tal kommer ur körningen ${esc(indata.korningId)}.</p>
    ${indata.las ? `<details class="flode-detalj"><summary>Så läser du kartan</summary><p class="flode-liten flode-dampad">${esc(indata.las)}</p></details>` : ""}`;
}

export interface LuftTomIndata {
  harMall?: boolean;
  rapportHref?: string | null;
  vaderLuft?: ProjektVaderLuftData | null;
  emissionsVal?: EmissionsKallaVal;
  skissResultat?: LuftSkissAnalysSvar | null;
  laddar?: boolean;
  fel?: string | null;
}

export interface SmhiVindrosSektorData {
  sektor_index: number;
  namn: string;
  centrum_grad: number;
  antal: number;
  andel_pct: number;
  andel_av_vind_pct?: number;
  medelvind_m_s: number | null;
  p95_vind_m_s?: number | null;
}

export interface SmhiVindrosData {
  station_id: string | null;
  station_namn: string | null;
  station_avstand_km?: number | null;
  ar?: number | null;
  antal_giltiga: number;
  andel_svag_vind_pct: number;
  medelvind_m_s: number;
  p95_vind_m_s: number;
  sektorer: readonly SmhiVindrosSektorData[];
}

export interface BakgrundAmneData {
  amne: string;
  enhet: string;
  arsmedel_ug_m3: number | null;
  percentil_90_4_ug_m3: number | null;
  max_dygn_ug_m3?: number | null;
  antal_dygn?: number;
}

export interface BakgrundLuftData {
  tillganglig: boolean;
  station_id?: string | null;
  station_namn?: string | null;
  avstand_km?: number | null;
  kalla?: string | null;
  period_start?: string | null;
  period_slut?: string | null;
  sha256?: string | null;
  amnen: Partial<Record<"NO2" | "PM10" | "O3" | "PM2.5" | string, BakgrundAmneData>>;
  varningar?: readonly string[];
}

export interface ProjektVaderLuftData {
  har_vader_luft: boolean;
  kalla_text?: string | null;
  kalla?: string | null;
  vindros?: SmhiVindrosData | null;
  bakgrund_luft?: BakgrundLuftData | null;
  har_vader_timserie?: boolean;
  varningar?: readonly string[];
}

export type EmissionsKallaLage = "egen_tabell" | "skiss_schablon";

export interface EmissionsKallaVal {
  lage: EmissionsKallaLage;
  egenFilnamn?: string | null;
  egenSha256?: string | null;
  schablonBekraftad?: boolean;
}

export interface LuftSkissSegmentResultat {
  id: string;
  gatunamn: string;
  adt_fordon_dygn: number;
  hastighet_km_h: number;
  andel_tung_pct: number;
  lokalt_tillskott_nox_arsmedel_ug_m3: number;
  lokalt_tillskott_pm10_arsmedel_ug_m3: number;
  pm10_berakning?: string;
}

export interface LuftSkissAnalysSvar {
  format: string;
  metodstatus: string;
  ar_schablon: boolean;
  emissionskalla: {
    kalla: string;
    ar: number | null;
    sha256: string;
    ar_schablon: boolean;
    filnamn?: string | null;
    varning?: string | null;
  };
  nortrip_kord: boolean;
  nortrip_antal_timmar?: number;
  vindros_param: {
    station_namn: string | null;
    ar?: number | null;
    medelvind_m_s: number;
    dominerande_sektor?: string | null;
    effektiv_vind_m_s: number;
  };
  lokalt_tillskott: {
    nox_arsmedel_ug_m3: number;
    nox_dygn_ug_m3: number;
    no2_arsmedel_ug_m3: number | null;
    no2_dygn_ug_m3: number | null;
    pm10_arsmedel_ug_m3: number;
    pm10_dygn_ug_m3: number;
  };
  bakgrund: {
    no2_arsmedel_ug_m3: number | null;
    pm10_arsmedel_ug_m3: number | null;
    pm10_p90_4_ug_m3: number | null;
    o3_arsmedel_ug_m3: number | null;
  };
  totalt: {
    no2_arsmedel_ug_m3: number | null;
    pm10_arsmedel_ug_m3: number | null;
    pm10_dygn_90_4_ug_m3: number | null;
  };
  mkn_jamforelse?: {
    no2_arsmedel_grans_ug_m3: number;
    no2_arsmedel_klarar_skiss: boolean | null;
    pm10_arsmedel_grans_ug_m3: number;
    pm10_arsmedel_klarar_skiss: boolean | null;
    pm10_dygn_90_4_grans_ug_m3: number;
    pm10_dygn_90_4_klarar_skiss: boolean | null;
  };
  per_segment: readonly LuftSkissSegmentResultat[];
  varningar: readonly string[];
}

export interface LuftSteg2PanelIndata {
  vaderLuft?: ProjektVaderLuftData | null;
  emissionsVal: EmissionsKallaVal;
  skissResultat?: LuftSkissAnalysSvar | null;
  antalVaglankar?: number;
  laddar?: boolean;
  fel?: string | null;
  harMall?: boolean;
  rapportHref?: string | null;
}

const arTal = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

const avrundaKoord = (v: number): string => String(Math.round(v * 10) / 10);

/** Kompakt 16-sektors SVG-vindros (ren TS, svartvit krom, svensk talformatering). */
export function vindrosSvgHtml(vindros: SmhiVindrosData, storlekPx = 180): string {
  const storlek = Math.max(120, Math.round(storlekPx));
  const cx = storlek / 2;
  const cy = storlek / 2;
  const maxR = storlek * 0.36;
  const minR = storlek * 0.08;
  const maxAndel = Math.max(1, ...vindros.sektorer.map((s) => (arTal(s.andel_pct) ? s.andel_pct : 0)));
  const halvBreddRad = (Math.PI / 16) * 0.85;

  const ringar = [0.33, 0.66, 1.0]
    .map(
      (f) =>
        `<circle cx="${avrundaKoord(cx)}" cy="${avrundaKoord(cy)}" r="${avrundaKoord(minR + (maxR - minR) * f)}" fill="none" stroke="#d4d4d4" stroke-width="0.8" />`,
    )
    .join("");

  const kilar = vindros.sektorer
    .map((s) => {
      const andel = arTal(s.andel_pct) && s.andel_pct > 0 ? s.andel_pct : 0;
      if (andel <= 0) return "";
      const r = minR + ((maxR - minR) * andel) / maxAndel;
      // 0° = norr (uppåt i SVG, -pi/2 rad), medurs.
      const vinkel = ((s.centrum_grad - 90) * Math.PI) / 180;
      const a0 = vinkel - halvBreddRad;
      const a1 = vinkel + halvBreddRad;
      const x1 = cx + r * Math.cos(a0);
      const y1 = cy + r * Math.sin(a0);
      const x2 = cx + r * Math.cos(a1);
      const y2 = cy + r * Math.sin(a1);
      const titel = `${s.namn}: ${formatera(andel, 1)} %${arTal(s.medelvind_m_s) ? `, medel ${formatera(s.medelvind_m_s, 1)} m/s` : ""}`;
      return `<path d="M ${avrundaKoord(cx)} ${avrundaKoord(cy)} L ${avrundaKoord(x1)} ${avrundaKoord(y1)} A ${avrundaKoord(r)} ${avrundaKoord(r)} 0 0 1 ${avrundaKoord(x2)} ${avrundaKoord(y2)} Z" fill="#171717" fill-opacity="0.82" stroke="#ffffff" stroke-width="0.7" data-sektor="${esc(s.namn)}"><title>${esc(titel)}</title></path>`;
    })
    .join("");

  const etikettR = maxR + 13;
  const kompass = [
    ["N", 0],
    ["O", 90],
    ["S", 180],
    ["V", 270],
  ]
    .map(([txt, deg]) => {
      const rad = (((deg as number) - 90) * Math.PI) / 180;
      const x = cx + etikettR * Math.cos(rad);
      const y = cy + etikettR * Math.sin(rad) + 3.5;
      return `<text x="${avrundaKoord(x)}" y="${avrundaKoord(y)}" text-anchor="middle" font-size="10" font-weight="600" fill="#171717">${txt}</text>`;
    })
    .join("");

  const stationDel = vindros.station_namn
    ? `SMHI ${vindros.station_namn}${arTal(vindros.ar) ? `, ${Math.round(vindros.ar)}` : ""}`
    : "SMHI";
  const aria = `Vindros 16 sektorer (${stationDel}), medelvind ${formatera(vindros.medelvind_m_s, 1)} m/s`;

  return `
    <div class="ar-luft-vindros-kort" id="ar-luft-vindros">
      <div class="ar-luft-kort-huvud">
        <span class="ar-sektion-rubrik">SMHI-vindros (16 sektorer)</span>
        <span class="flode-liten flode-dampad">${esc(stationDel)}</span>
      </div>
      <div class="ar-luft-vindros-rad">
        <svg class="ar-vindros-svg" width="${storlek}" height="${storlek}" viewBox="0 0 ${storlek} ${storlek}" role="img" aria-label="${esc(aria)}">
          ${ringar}
          ${kilar}
          <circle cx="${avrundaKoord(cx)}" cy="${avrundaKoord(cy)}" r="${avrundaKoord(minR)}" fill="#ffffff" stroke="#525252" stroke-width="0.9" />
          ${kompass}
        </svg>
        <dl class="flode-tal ar-luft-vindros-tal">
          <div><dt>Medelvind</dt><dd><strong>${esc(formatera(vindros.medelvind_m_s, 1))} m/s</strong></dd></div>
          <div><dt>p95 vind</dt><dd>${esc(formatera(vindros.p95_vind_m_s, 1))} m/s</dd></div>
          <div><dt>Svag vind</dt><dd>${esc(formatera(vindros.andel_svag_vind_pct, 1))} %</dd></div>
        </dl>
      </div>
    </div>`;
}

/** Kort för urban bakgrundshalt (NO₂, PM₁₀, O₃) med källa/period eller ärlig "bakgrund saknas". */
export function bakgrundLuftKortHtml(bakgrund: BakgrundLuftData | null | undefined): string {
  if (!bakgrund || !bakgrund.tillganglig) {
    const forstaVarning = bakgrund?.varningar?.[0];
    const varning = forstaVarning
      ? `<p class="flode-liten flode-dampad">${esc(forstaVarning)}</p>`
      : "";
    return `
      <div class="ar-luft-bakgrund-kort ar-luft-bakgrund-saknas" id="ar-luft-bakgrund">
        <div class="ar-luft-kort-huvud">
          <span class="ar-sektion-rubrik">Urban bakgrundshalt (NO₂, PM₁₀, O₃)</span>
          <span class="flode-liten flode-dampad">Bakgrund saknas</span>
        </div>
        <p class="flode-liten">Urban bakgrundshalt saknas för projektet. Importera lokal mätfil (SMHI CSV eller kommunal JSON) eller ange bakgrundsvärde manuellt — Floden hittar aldrig på bakgrundshalter.</p>
        ${varning}
      </div>`;
  }

  const amneLista: Array<{ nyckel: "NO2" | "PM10" | "O3"; etikett: string }> = [
    { nyckel: "NO2", etikett: "NO₂" },
    { nyckel: "PM10", etikett: "PM₁₀" },
    { nyckel: "O3", etikett: "O₃" },
  ];
  const rader = amneLista
    .map(({ nyckel, etikett }) => {
      const post = bakgrund.amnen[nyckel];
      const arTxt = post && arTal(post.arsmedel_ug_m3) ? `${formatera(post.arsmedel_ug_m3, 1)} µg/m³` : "saknas";
      const p90Txt =
        post && arTal(post.percentil_90_4_ug_m3) ? `${formatera(post.percentil_90_4_ug_m3, 1)} µg/m³` : "saknas";
      return `<tr><td><strong>${etikett}</strong></td><td>${esc(arTxt)}</td><td>${esc(p90Txt)}</td></tr>`;
    })
    .join("");

  const metaDelar: string[] = [];
  if (bakgrund.station_namn) metaDelar.push(bakgrund.station_namn);
  if (arTal(bakgrund.avstand_km)) metaDelar.push(`${formatera(bakgrund.avstand_km, 1)} km`);
  if (bakgrund.period_start && bakgrund.period_slut) {
    metaDelar.push(`${bakgrund.period_start}–${bakgrund.period_slut}`);
  }
  const kallaRad = [bakgrund.kalla, ...metaDelar].filter(Boolean).join(" · ");

  return `
    <div class="ar-luft-bakgrund-kort" id="ar-luft-bakgrund">
      <div class="ar-luft-kort-huvud">
        <span class="ar-sektion-rubrik">Urban bakgrundshalt (NO₂, PM₁₀, O₃)</span>
      </div>
      <table class="flode-tabell ar-luft-bakgrund-tabell">
        <thead><tr><th>Ämne</th><th>Årsmedel</th><th>90,4-p dygn</th></tr></thead>
        <tbody>${rader}</tbody>
      </table>
      ${kallaRad ? `<p class="flode-liten flode-dampad">${esc(kallaRad)}</p>` : ""}
    </div>`;
}

/** Väljare för emissionskälla enligt val 3A: lokal CSV/TOML-tabell vs aktivt skiss-schablonval. */
export function emissionsKallaValHtml(val: EmissionsKallaVal): string {
  const arEgen = val.lage === "egen_tabell";
  const arSchablon = val.lage === "skiss_schablon";
  const filStatus = val.egenFilnamn
    ? `<p class="flode-liten flode-dampad">Laddad tabell: <strong>${esc(val.egenFilnamn)}</strong>${val.egenSha256 ? ` (SHA-256 ${esc(val.egenSha256.slice(0, 12))})` : ""}</p>`
    : `<p class="flode-liten flode-dampad">Ladda upp lokal CSV- eller TOML-tabell med emissionsfaktorer (t.ex. kommunens HBEFA-utdrag).</p>`;

  return `
    <fieldset class="ar-luft-emission-val" id="ar-luft-emission-val">
      <legend class="ar-sektion-rubrik">Emissionskälla (vägtrafik)</legend>
      <div class="ar-luft-emission-alternativ" role="radiogroup" aria-label="Val av emissionskälla">
        <label class="flode-liten">
          <input type="radio" name="ar-luft-emissionskalla" value="egen_tabell"${arEgen ? " checked" : ""} />
          <strong>Egen emissionsfaktortabell (CSV/TOML)</strong>
        </label>
        <label class="flode-liten">
          <input type="radio" name="ar-luft-emissionskalla" value="skiss_schablon"${arSchablon ? " checked" : ""} />
          <strong>Publikt skiss-schablonset</strong> <span class="ar-skiss-badge">SKISS</span>
        </label>
      </div>
      ${
        arEgen
          ? `<div class="ar-luft-egen-tabell">
              <input type="file" id="ar-luft-emission-fil" accept=".csv,.toml" aria-label="Välj emissionsfaktortabell (CSV eller TOML)" />
              ${filStatus}
            </div>`
          : `<div class="flode-ruta varning ar-luft-schablon-varning">
              <p class="flode-liten"><strong>Aktivt skissval (ej HBEFA):</strong> Det publika schablonsetet bygger på öppna EMEP/EEA-riktvärden och används aldrig tyst som standard. Får inte användas för formell MKN-prövning.</p>
              <label class="flode-liten">
                <input type="checkbox" id="ar-luft-schablon-bekrafta"${val.schablonBekraftad ? " checked" : ""} />
                Jag väljer aktivt publikt skiss-schablonset för tidig skiss.
              </label>
            </div>`
      }
    </fieldset>`;
}

/** Visning av deterministisk Luft-skissanalys (NO₂/PM₁₀, NORTRIP-status, skiss-märkning). */
export function luftSkissResultatHtml(
  resultat: LuftSkissAnalysSvar | null | undefined,
  laddar = false,
  fel: string | null = null,
): string {
  if (laddar) {
    return `<p class="flode-liten flode-dampad" role="status">Beräknar skissutsläpp och halter …</p>`;
  }
  if (fel) {
    return `<p class="flode-liten flode-fel" role="alert">${esc(fel)}</p>`;
  }
  if (!resultat) {
    return "";
  }

  const fmtUg = (v: number | null | undefined): string => (arTal(v) ? `${formatera(v, 1)} µg/m³` : "–");
  const kallaEtikett = resultat.ar_schablon
    ? `Publikt skiss-schablonset (${resultat.emissionskalla.sha256.slice(0, 12)})`
    : `${resultat.emissionskalla.filnamn || resultat.emissionskalla.kalla} (${resultat.emissionskalla.sha256.slice(0, 12)})`;
  const nortripTxt = resultat.nortrip_kord
    ? `NORTRIP vägdamm (${formateraHogst(resultat.nortrip_antal_timmar ?? 0, 0)} timmar)`
    : "Utan timvis NORTRIP-fuktmodell";

  const segRader = resultat.per_segment
    .map(
      (s) =>
        `<tr><td>${esc(s.gatunamn)}</td><td>${esc(formatera(s.adt_fordon_dygn, 0))}</td><td>${esc(formatera(s.hastighet_km_h, 0))} km/h</td><td>${esc(formatera(s.lokalt_tillskott_nox_arsmedel_ug_m3, 1))}</td><td>${esc(formatera(s.lokalt_tillskott_pm10_arsmedel_ug_m3, 1))}</td></tr>`,
    )
    .join("");

  const varningarHtml =
    resultat.varningar.length > 0
      ? `<ul class="flode-liten flode-dampad ar-luft-varningar">${resultat.varningar.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>`
      : "";

  return `
    <div class="ar-luft-skiss-resultat" id="ar-luft-skiss-resultat">
      <div class="ar-doman-huvud">
        <h3 class="ar-sektion-rubrik">Skissresultat · Trafikalstring &amp; halter</h3>
        <span class="ar-skiss-badge" title="Metodstatus: skiss">SKISS</span>
      </div>
      <p class="flode-liten flode-dampad">Källa: ${esc(kallaEtikett)} · ${esc(nortripTxt)}</p>
      <table class="flode-tabell ar-luft-resultat-tabell">
        <thead><tr><th>Ämne</th><th>Lokalt tillskott</th><th>Bakgrund</th><th>Totalt (skiss)</th></tr></thead>
        <tbody>
          <tr>
            <td><strong>NO₂ årsmedel</strong></td>
            <td>${esc(fmtUg(resultat.lokalt_tillskott.no2_arsmedel_ug_m3))}</td>
            <td>${esc(fmtUg(resultat.bakgrund.no2_arsmedel_ug_m3))}</td>
            <td><strong>${esc(fmtUg(resultat.totalt.no2_arsmedel_ug_m3))}</strong></td>
          </tr>
          <tr>
            <td><strong>PM₁₀ årsmedel</strong></td>
            <td>${esc(fmtUg(resultat.lokalt_tillskott.pm10_arsmedel_ug_m3))}</td>
            <td>${esc(fmtUg(resultat.bakgrund.pm10_arsmedel_ug_m3))}</td>
            <td><strong>${esc(fmtUg(resultat.totalt.pm10_arsmedel_ug_m3))}</strong></td>
          </tr>
          <tr>
            <td><strong>PM₁₀ 90,4-p dygn</strong></td>
            <td>${esc(fmtUg(resultat.lokalt_tillskott.pm10_dygn_ug_m3))}</td>
            <td>${esc(fmtUg(resultat.bakgrund.pm10_p90_4_ug_m3))}</td>
            <td><strong>${esc(fmtUg(resultat.totalt.pm10_dygn_90_4_ug_m3))}</strong></td>
          </tr>
        </tbody>
      </table>
      ${
        segRader
          ? `<table class="flode-tabell ar-luft-segment-tabell">
              <thead><tr><th>Gata</th><th>ÅDT</th><th>Hast.</th><th>ΔNOx (µg/m³)</th><th>ΔPM₁₀ (µg/m³)</th></tr></thead>
              <tbody>${segRader}</tbody>
            </table>`
          : ""
      }
      ${varningarHtml}
    </div>`;
}

/** Samlad Steg 2-panel för Vind & Luft: vindros, bakgrundshalt, emissionskällval & skissresultat. */
export function luftSteg2PanelHtml(indata: LuftSteg2PanelIndata): string {
  const vl = indata.vaderLuft;
  const vindrosHtml =
    vl?.har_vader_luft && vl.vindros
      ? vindrosSvgHtml(vl.vindros)
      : `<div class="flode-ruta ar-luft-vindros-saknas"><p class="flode-liten"><strong>SMHI-vindros saknas.</strong> Mappa geografin med SMHI-väderlagret för att visa 16-sektors vindros.</p></div>`;
  const bakgrundHtml = bakgrundLuftKortHtml(vl?.bakgrund_luft ?? null);
  const emissionHtml = emissionsKallaValHtml(indata.emissionsVal);
  const kanKora =
    Boolean(vl?.har_vader_luft && vl.vindros) &&
    (indata.emissionsVal.lage === "egen_tabell"
      ? Boolean(indata.emissionsVal.egenFilnamn)
      : Boolean(indata.emissionsVal.schablonBekraftad)) &&
    !indata.laddar;
  const vagText =
    arTal(indata.antalVaglankar) && indata.antalVaglankar > 0
      ? `<p class="flode-liten flode-dampad">Valda väglänkar: <strong>${esc(formatera(indata.antalVaglankar, 0))}</strong></p>`
      : `<p class="flode-liten flode-dampad">Välj minst en väglänk med ÅDT för att köra skissberäkningen.</p>`;
  const resultatHtml = luftSkissResultatHtml(indata.skissResultat, indata.laddar, indata.fel);

  return `
    <section class="ar-doman-kort ar-luft-steg2-panel" id="ar-luft-steg2-panel" aria-label="Vind och Luft">
      <div class="ar-doman-huvud">
        <h1 class="flode-h" id="ar-rubrik">Vind &amp; Luft</h1>
        <span class="ar-skiss-badge" title="Metodstatus: skiss">SKISS</span>
      </div>
      <p class="flode-liten flode-dampad">${esc(LUFT_SKISS_NOTIS)}</p>
      ${vindrosHtml}
      ${bakgrundHtml}
      ${emissionHtml}
      <div class="ar-luft-kor-rad">
        ${vagText}
        <button type="button" class="flode-knapp svart liten" id="ar-luft-skiss-kor"${kanKora ? "" : " disabled"}>${indata.laddar ? "Beräknar …" : "Kör luftskiss"}</button>
      </div>
      <div id="ar-luft-skiss-ut">${resultatHtml}</div>
    </section>`;
}

/** Ärlig översikt för domänen Vind & Luft i Steg 2 när inga luftkörningar finns ännu. */
export function luftTomPanelHtml(indata: LuftTomIndata = {}): string {
  if (indata.vaderLuft?.har_vader_luft && indata.vaderLuft.vindros) {
    return luftSteg2PanelHtml({
      vaderLuft: indata.vaderLuft,
      emissionsVal: indata.emissionsVal ?? { lage: "egen_tabell" },
      skissResultat: indata.skissResultat ?? null,
      laddar: indata.laddar ?? false,
      fel: indata.fel ?? null,
      harMall: indata.harMall,
      rapportHref: indata.rapportHref,
    });
  }
  const harMall = Boolean(indata.harMall);
  const korTips = harMall
    ? `<p class="flode-liten">Projektet har en luftmall redo. Tryck på beräkningsknappen ovan för att räkna vindkomfort och luftkvalitet i nuläget.</p>`
    : `<div class="flode-ruta ar-luft-tom-ruta">
        <p><strong>Inga vind- eller luftkörningar finns i projektet ännu.</strong></p>
        <p>Floden hittar aldrig på väder- eller trafikdata. För att räkna Vind &amp; Luft på kartan krävs:</p>
        <ul class="ar-luft-krav">
          <li><strong>Vindkomfort (NEN 8100, skiss):</strong> väderunderlag (vindros / SMHI-vindstatistik) och en luftmall (<code>luft.toml</code>) i projektet.</li>
          <li><strong>Luftkvalitet &amp; MKN (PM10/NO₂, skiss):</strong> trafik- och emissionsunderlag samt bakgrundshalt.</li>
        </ul>
        ${
          indata.rapportHref
            ? `<p class="flode-liten">Har du redan en extern timserie (CSV) och bakgrundshalt kan du skapa en MKN-rapport direkt i <a href="${esc(indata.rapportHref)}">Steg 3 · Rapport</a>.</p>`
            : ""
        }
      </div>`;
  return `
    <div class="ar-doman-huvud">
      <h1 class="flode-h" id="ar-rubrik">Vind &amp; Luft</h1>
      <span class="ar-skiss-badge" title="Metodstatus: skiss">SKISS</span>
    </div>
    <p class="flode-liten flode-dampad">${esc(LUFT_SKISS_NOTIS)}</p>
    ${korTips}`;
}
