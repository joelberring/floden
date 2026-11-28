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

/** URL för projektets klickbara väglänkar (`GET` / `POST /api/projekt/<id>/trafik`). */
export function projektTrafikUrl(projekt: string): string {
  return `/api/projekt/${encodeURIComponent(projekt)}/trafik`;
}

/** Obligatorisk, framträdande varningsbanner i Steg 2 (får aldrig döljas). */
export const BULLER_SKISS_BANNER =
  "SKISS — ej Nord2000-konform (formell bullerrapport spärrad)";

export type TrafikKallmarke = "NVDB" | "Kommun" | "Egen" | null;
export type TrafikScenarioLage = "nulage" | "prognos";

/** En klickbar väglänk ur geografins `lager.trafik` (med ev. projektjusteringar, EPSG:3011). */
export interface TrafikLank {
  lank_id: string;
  koordinater: Array<[number, number]>;
  gatunamn: string | null;
  vagnummer?: string | null;
  vaghallare?: string | null;
  hastighet_km_h: number | null;
  korfalt?: number | null;
  adt_fordon_dygn: number | null;
  prognos_adt_fordon_dygn: number | null;
  andel_tung_pct: number | null;
  matar?: number | null;
  adt_kalla: string | null;
  kallmarke: TrafikKallmarke;
  osaker: boolean;
  har_adt: boolean;
  egen_justering?: boolean;
}

/** En vald och redigerbar väglänk i Steg 2-trafiktabellen (delas mellan Buller och Luft). */
export interface TrafikValdLank {
  lank_id: string;
  gatunamn: string | null;
  koordinater: Array<[number, number]>;
  adt_fordon_dygn: number | null;
  prognos_adt_fordon_dygn: number | null;
  hastighet_km_h: number | null;
  andel_tung_pct: number | null;
  matar?: number | null;
  adt_kalla: string | null;
  kallmarke: TrafikKallmarke;
  osaker: boolean;
}

export interface TrafikJusteringPost {
  lank_id: string;
  adt_fordon_dygn: number | null;
  prognos_adt_fordon_dygn: number | null;
  hastighet_km_h: number | null;
  andel_tung_pct: number | null;
  kalla: string;
}

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
  trafikLankar?: readonly TrafikLank[];
  valdaTrafikLankar?: readonly TrafikValdLank[];
  trafikLage?: TrafikScenarioLage;
  trafikLaddar?: boolean;
  trafikSparar?: boolean;
  trafikFel?: string | null;
}

export interface TrafikTabellOpts {
  tillgangliga: readonly TrafikLank[];
  valda: readonly TrafikValdLank[];
  trafikLage: TrafikScenarioLage;
  laddar?: boolean;
  sparar?: boolean;
  fel?: string | null;
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

/** Tolkar decimaltal med svenskt komma, punkt samt ASCII- eller typografiskt minustecken. */
export function tolkaBullerTal(text: string): number | null {
  const ren = text.trim().replace(/\s+/g, "").replace(/[\u2212\u2013\u2014]/g, "-").replace(",", ".");
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

function tolkaKallmarke(v: unknown): TrafikKallmarke {
  if (v === "NVDB" || v === "Kommun" || v === "Egen") return v;
  return null;
}

function valfrittTal(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function valfriText(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  return s ? s : null;
}

/** Tolkar GeoJSON FeatureCollection från `GET /api/projekt/<id>/trafik` (EPSG:3011) utan att hitta på ÅDT. */
export function tolkaProjektTrafikGeoJson(geojson: unknown): TrafikLank[] {
  if (!geojson || typeof geojson !== "object") return [];
  const features = (geojson as { features?: unknown }).features;
  if (!Array.isArray(features)) return [];

  const ut: TrafikLank[] = [];
  for (let i = 0; i < features.length; i += 1) {
    const feat = features[i];
    if (!feat || typeof feat !== "object") continue;
    const geom = (feat as { geometry?: unknown }).geometry;
    if (!geom || typeof geom !== "object" || (geom as { type?: unknown }).type !== "LineString") {
      continue;
    }
    const rawCoords = (geom as { coordinates?: unknown }).coordinates;
    if (!Array.isArray(rawCoords) || rawCoords.length < 2) continue;
    const koordinater: Array<[number, number]> = [];
    let ok = true;
    for (const pt of rawCoords) {
      if (!Array.isArray(pt) || pt.length < 2 || !arTal(pt[0]) || !arTal(pt[1])) {
        ok = false;
        break;
      }
      koordinater.push([pt[0], pt[1]]);
    }
    if (!ok || koordinater.length < 2) continue;

    const propsRaw = (feat as { properties?: unknown }).properties;
    const props: Record<string, unknown> =
      propsRaw && typeof propsRaw === "object" ? (propsRaw as Record<string, unknown>) : {};
    const lid = valfriText(props.lank_id) ?? valfriText((feat as { id?: unknown }).id) ?? `lank_${i + 1}`;
    const adt = valfrittTal(props.adt_fordon_dygn);
    const prognos = valfrittTal(props.prognos_adt_fordon_dygn);
    const harNagonAdt = adt !== null || prognos !== null;
    const tung = harNagonAdt ? valfrittTal(props.andel_tung_pct) : null;
    const adtKalla = harNagonAdt ? valfriText(props.adt_kalla) : null;
    const kallmarke = harNagonAdt ? tolkaKallmarke(props.kallmarke) : null;

    ut.push({
      lank_id: lid,
      koordinater,
      gatunamn: valfriText(props.gatunamn),
      vagnummer: valfriText(props.vagnummer),
      vaghallare: valfriText(props.vaghallare),
      hastighet_km_h: valfrittTal(props.hastighet_km_h),
      korfalt: valfrittTal(props.korfalt),
      adt_fordon_dygn: adt,
      prognos_adt_fordon_dygn: prognos,
      andel_tung_pct: tung,
      matar: valfrittTal(props.matar),
      adt_kalla: adtKalla,
      kallmarke,
      osaker: Boolean(props.osaker),
      har_adt: adt !== null,
      egen_justering: Boolean(props.egen_justering),
    });
  }
  return ut;
}

/** Visningstext för källmärke (`NVDB 2025`, `Kommun`, `Egen` eller `ÅDT saknas`). */
export function trafikKallmarkeEtikett(
  lank: Pick<TrafikLank, "kallmarke" | "adt_kalla" | "matar" | "adt_fordon_dygn">,
): string {
  if (lank.kallmarke === "Egen") return "Egen";
  if (lank.kallmarke === "NVDB") {
    return arTal(lank.matar) ? `NVDB ${Math.round(lank.matar)}` : "NVDB";
  }
  if (lank.kallmarke === "Kommun") {
    return arTal(lank.matar) ? `Kommun ${Math.round(lank.matar)}` : "Kommun";
  }
  if (!arTal(lank.adt_fordon_dygn)) return "ÅDT saknas";
  return lank.adt_kalla ?? "Okänd källa";
}

/** Returnerar aktivt ÅDT-värde för Nuläge eller Prognos (faller tillbaka på nuläge i prognosläge). */
export function effektivAdtForLank(
  lank: Pick<TrafikValdLank, "adt_fordon_dygn" | "prognos_adt_fordon_dygn">,
  lage: TrafikScenarioLage,
): number | null {
  if (lage === "prognos") {
    if (arTal(lank.prognos_adt_fordon_dygn)) return lank.prognos_adt_fordon_dygn;
    return arTal(lank.adt_fordon_dygn) ? lank.adt_fordon_dygn : null;
  }
  return arTal(lank.adt_fordon_dygn) ? lank.adt_fordon_dygn : null;
}

/** Klick på en väglänk lägger till den i trafiktabellen (eller tar bort den om den redan är vald). */
export function valjEllerAvmarkeraTrafikLank(
  valda: readonly TrafikValdLank[],
  lank: TrafikLank,
): TrafikValdLank[] {
  if (valda.some((v) => v.lank_id === lank.lank_id)) {
    return valda.filter((v) => v.lank_id !== lank.lank_id);
  }
  const ny: TrafikValdLank = {
    lank_id: lank.lank_id,
    gatunamn: lank.gatunamn,
    koordinater: lank.koordinater.map(([e, n]) => [e, n]),
    adt_fordon_dygn: lank.adt_fordon_dygn,
    prognos_adt_fordon_dygn: lank.prognos_adt_fordon_dygn,
    hastighet_km_h: lank.hastighet_km_h,
    andel_tung_pct: lank.andel_tung_pct,
    matar: lank.matar,
    adt_kalla: lank.adt_kalla,
    kallmarke: lank.kallmarke,
    osaker: lank.osaker,
  };
  return [...valda, ny];
}

/** Uppdaterar användarredigerade trafikvärden på en vald väglänk och märker källan som `Egen`. */
export function uppdateraValdTrafikLank(
  valda: readonly TrafikValdLank[],
  lankId: string,
  andring: Partial<
    Pick<
      TrafikValdLank,
      "adt_fordon_dygn" | "prognos_adt_fordon_dygn" | "hastighet_km_h" | "andel_tung_pct"
    >
  >,
): TrafikValdLank[] {
  return valda.map((v) => {
    if (v.lank_id !== lankId) return v;
    const nyAdt = "adt_fordon_dygn" in andring ? (andring.adt_fordon_dygn ?? null) : v.adt_fordon_dygn;
    const nyPrognos =
      "prognos_adt_fordon_dygn" in andring
        ? (andring.prognos_adt_fordon_dygn ?? null)
        : v.prognos_adt_fordon_dygn;
    const nyHast = "hastighet_km_h" in andring ? (andring.hastighet_km_h ?? null) : v.hastighet_km_h;
    const nyTung = "andel_tung_pct" in andring ? (andring.andel_tung_pct ?? null) : v.andel_tung_pct;
    const harNagonAdt = arTal(nyAdt) || arTal(nyPrognos);
    return {
      ...v,
      adt_fordon_dygn: nyAdt,
      prognos_adt_fordon_dygn: nyPrognos,
      hastighet_km_h: nyHast,
      andel_tung_pct: nyTung,
      kallmarke: harNagonAdt ? "Egen" : null,
      adt_kalla: harNagonAdt ? "Egen uppgift" : null,
      osaker: false,
    };
  });
}

/** Validerar alla valda väglänkar för aktivt trafikläge (Nuläge eller Prognos). */
export function valideraValdaTrafikLankar(
  valda: readonly TrafikValdLank[],
  lage: TrafikScenarioLage = "nulage",
): string | null {
  if (valda.length === 0) {
    return "Välj minst en väglänk på kartan eller rita en väg manuellt.";
  }
  for (const lank of valda) {
    const etikett = lank.gatunamn ?? lank.lank_id;
    const adt = effektivAdtForLank(lank, lage);
    if (!arTal(adt) || adt < 0) {
      return `ÅDT saknas för "${etikett}" — ange trafikflöde (fordon/dygn >= 0).`;
    }
    if (!arTal(lank.hastighet_km_h) || lank.hastighet_km_h <= 0) {
      return `Hastighet måste vara > 0 km/h för "${etikett}".`;
    }
    if (!arTal(lank.andel_tung_pct) || lank.andel_tung_pct < 0 || lank.andel_tung_pct > 100) {
      return `Ange andel tung trafik (0–100 %) för "${etikett}".`;
    }
    if (lank.koordinater.length < 2) {
      return `Väglänk "${etikett}" saknar giltig linjegeometri.`;
    }
  }
  return null;
}

/** Konverterar valda väglänkar till `vagsegment` för `POST /api/analys/buller`. */
export function byggBullerVagSegmentFranValda(
  valda: readonly TrafikValdLank[],
  lage: TrafikScenarioLage = "nulage",
): BullerVagSegment[] {
  return valda.map((lank) => ({
    id: lank.lank_id,
    koordinater: lank.koordinater.map(([e, n]) => [e, n]),
    adt_fordon_dygn: effektivAdtForLank(lank, lage) as number,
    hastighet_km_h: lank.hastighet_km_h as number,
    andel_tung_pct: lank.andel_tung_pct as number,
  }));
}

/** Bygger `POST /api/analys/buller`-begäran från klickade väglänkar (+ ev. manuellt ritad väg). */
export function byggBullerBegaranMedTrafik(
  omrade_id: string,
  p: BullerParam,
  valda: readonly TrafikValdLank[],
  opts?: { scenario?: string | null; trafikLage?: TrafikScenarioLage },
): {
  omrade_id: string;
  scenario?: string;
  mottagarhojd_m: number;
  vagsegment: BullerVagSegment[];
} {
  const scenario = opts?.scenario ?? null;
  const lage = opts?.trafikLage ?? "nulage";
  if (valda.length === 0) {
    return byggBullerBegaran(omrade_id, p, scenario);
  }
  const vagsegment = byggBullerVagSegmentFranValda(valda, lage);
  const harManuell =
    arTal(p.vag_start_e) &&
    arTal(p.vag_start_n) &&
    arTal(p.vag_slut_e) &&
    arTal(p.vag_slut_n) &&
    arTal(p.adt_fordon_dygn) &&
    arTal(p.hastighet_km_h) &&
    arTal(p.andel_tung_pct) &&
    Math.hypot(p.vag_slut_e - p.vag_start_e, p.vag_slut_n - p.vag_start_n) >= 0.1;
  if (harManuell) {
    vagsegment.push({
      id: "manuell_vag_1",
      koordinater: [
        [p.vag_start_e as number, p.vag_start_n as number],
        [p.vag_slut_e as number, p.vag_slut_n as number],
      ],
      adt_fordon_dygn: p.adt_fordon_dygn as number,
      hastighet_km_h: p.hastighet_km_h as number,
      andel_tung_pct: p.andel_tung_pct as number,
    });
  }
  return {
    omrade_id,
    ...(scenario ? { scenario } : {}),
    mottagarhojd_m: p.mottagarhojd_m,
    vagsegment,
  };
}

/** Bygger JSON-kropp för `POST /api/projekt/<id>/trafik` från användarens valda trafiktabell. */
export function byggTrafikJusteringarPayload(valda: readonly TrafikValdLank[]): {
  ersatt: false;
  justeringar: TrafikJusteringPost[];
} {
  return {
    ersatt: false,
    justeringar: valda.map((lank) => ({
      lank_id: lank.lank_id,
      adt_fordon_dygn: lank.adt_fordon_dygn,
      prognos_adt_fordon_dygn: lank.prognos_adt_fordon_dygn,
      hastighet_km_h: lank.hastighet_km_h,
      andel_tung_pct: lank.andel_tung_pct,
      kalla: "Egen uppgift",
    })),
  };
}

/** Renderar klickbara väglänkar, Nuläge/Prognos-växlare och redigerbar trafiktabell (Buller + Luft). */
export function trafikTabellHtml(opts: TrafikTabellOpts): string {
  const { tillgangliga, valda, trafikLage } = opts;
  const valdaIdn = new Set(valda.map((v) => v.lank_id));
  const medAdt = tillgangliga.filter((l) => l.har_adt).length;
  const utanAdt = tillgangliga.length - medAdt;

  const statusHtml = opts.laddar
    ? `<p class="flode-liten flode-dampad" role="status">Läser väglänkar ur projektets trafiklager …</p>`
    : opts.fel
      ? `<p class="flode-liten flode-fel" role="alert">${esc(opts.fel)}</p>`
      : tillgangliga.length > 0
        ? `<p class="flode-liten flode-dampad">Klicka på en gata i kartan eller listan: <strong>${esc(formatera(medAdt, 0))}</strong> med ÅDT (fast linje), <strong>${esc(formatera(utanAdt, 0))}</strong> saknar ÅDT (streckad linje).</p>`
        : `<p class="flode-liten flode-dampad">Projektets geografi saknar trafiklager — rita väg på kartan nedan.</p>`;

  const snabblistaHtml =
    tillgangliga.length > 0
      ? `<div class="ar-bl-trafik-lista" role="group" aria-label="Väglänkar i projektområdet">
          ${tillgangliga
            .map((lk) => {
              const vald = valdaIdn.has(lk.lank_id);
              const namn = lk.gatunamn ?? lk.lank_id;
              const marke = trafikKallmarkeEtikett(lk);
              const metaTxt = arTal(lk.adt_fordon_dygn)
                ? `${formatera(lk.adt_fordon_dygn, 0)} ÅDT · ${marke}`
                : "ÅDT saknas";
              const stilKlass = lk.har_adt ? "har-adt" : "saknar-adt";
              return `<button type="button" class="flode-knapp liten ar-bl-lank-knapp ${stilKlass}${vald ? " svart" : ""}" data-bl-valj-lank="${esc(lk.lank_id)}" aria-pressed="${vald}">
                <span>${esc(namn)}</span>
                <span class="ar-bl-lank-meta">${esc(metaTxt)}</span>
              </button>`;
            })
            .join("")}
        </div>`
      : "";

  const tabellHtml =
    valda.length > 0
      ? `<div class="ar-bl-trafik-tabell-ram">
          <div class="ar-bl-trafik-tabell-scroll">
          <table class="ar-bl-trafik-tabell" aria-label="Valda väglänkar för buller och luft">
            <thead>
              <tr>
                <th>Gata / länk</th>
                <th>Nuläge ÅDT</th>
                <th>Prognos-ÅDT</th>
                <th>Hast. (km/h)</th>
                <th>Tung (%)</th>
                <th>Källa</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              ${valda
                .map((lk) => {
                  const namn = lk.gatunamn ?? lk.lank_id;
                  const marke = trafikKallmarkeEtikett(lk);
                  const badgeKlass = lk.kallmarke ? `ar-kallmarke-${lk.kallmarke.toLowerCase()}` : "ar-kallmarke-saknas";
                  const osakerHtml = lk.osaker
                    ? ` <span class="ar-bl-osaker-badge" title="Tvetydig matchning i trafikfil — kontrollera värdet">Osäker</span>`
                    : "";
                  return `<tr data-bl-vald-rad="${esc(lk.lank_id)}">
                    <td><strong>${esc(namn)}</strong>${osakerHtml}</td>
                    <td><input type="text" inputmode="decimal" aria-label="Nuläge ÅDT för ${esc(namn)}" data-bl-lank-id="${esc(lk.lank_id)}" data-bl-lank-falt="adt_fordon_dygn" value="${esc(fmtInput(lk.adt_fordon_dygn, 0))}" placeholder="ÅDT saknas – ange värde" /></td>
                    <td><input type="text" inputmode="decimal" aria-label="Prognos-ÅDT för ${esc(namn)}" data-bl-lank-id="${esc(lk.lank_id)}" data-bl-lank-falt="prognos_adt_fordon_dygn" value="${esc(fmtInput(lk.prognos_adt_fordon_dygn, 0))}" placeholder="valfritt" /></td>
                    <td><input type="text" inputmode="decimal" aria-label="Hastighet km/h för ${esc(namn)}" data-bl-lank-id="${esc(lk.lank_id)}" data-bl-lank-falt="hastighet_km_h" value="${esc(fmtInput(lk.hastighet_km_h, 0))}" placeholder="km/h" /></td>
                    <td><input type="text" inputmode="decimal" aria-label="Tung andel procent för ${esc(namn)}" data-bl-lank-id="${esc(lk.lank_id)}" data-bl-lank-falt="andel_tung_pct" value="${esc(fmtInput(lk.andel_tung_pct, 1))}" placeholder="%" /></td>
                    <td><span class="ar-bl-kallmarke ${badgeKlass}" data-bl-kallmarke="${esc(lk.kallmarke ?? "saknas")}">${esc(marke)}</span></td>
                    <td><button type="button" class="flode-knapp liten" data-bl-ta-bort-lank="${esc(lk.lank_id)}" aria-label="Ta bort ${esc(namn)}">×</button></td>
                  </tr>`;
                })
                .join("")}
            </tbody>
          </table>
          </div>
          <div class="ar-bl-trafik-atgarder">
            <button type="button" class="flode-knapp liten" id="ar-bl-spara-trafik"${opts.sparar ? " disabled" : ""}>${opts.sparar ? "Sparar …" : "Spara trafikuppgifter"}</button>
          </div>
        </div>`
      : "";

  return `
    <div class="ar-bl-trafik-sektion" id="ar-bl-trafik-sektion">
      <div class="ar-bl-trafik-huvud">
        <span class="ar-sektion-rubrik">Väglänkar i projektet</span>
        <div class="flode-segment liten" role="group" aria-label="Trafikläge nuläge eller prognos">
          <button type="button" data-bl-trafik-lage="nulage" aria-pressed="${trafikLage === "nulage"}">Nuläge</button>
          <button type="button" data-bl-trafik-lage="prognos" aria-pressed="${trafikLage === "prognos"}">Prognos</button>
        </div>
      </div>
      ${statusHtml}
      ${snabblistaHtml}
      ${tabellHtml}
    </div>`;
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

  const visaTrafikSektion =
    t.trafikLankar !== undefined ||
    t.valdaTrafikLankar !== undefined ||
    Boolean(t.trafikLaddar) ||
    Boolean(t.trafikFel);
  const trafikSektionHtml = visaTrafikSektion
    ? trafikTabellHtml({
        tillgangliga: t.trafikLankar ?? [],
        valda: t.valdaTrafikLankar ?? [],
        trafikLage: t.trafikLage ?? "nulage",
        laddar: t.trafikLaddar,
        sparar: t.trafikSparar,
        fel: t.trafikFel,
      })
    : "";

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
        ${trafikSektionHtml}
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
  onValdaTrafikAndrade?: (valda: readonly TrafikValdLank[], lage: TrafikScenarioLage) => void;
  onSparaTrafik?: (payload: {
    ersatt: false;
    justeringar: TrafikJusteringPost[];
  }) => Promise<TrafikLank[] | void>;
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
  sattTrafikLankar(lankar: readonly TrafikLank[]): void;
  klickaTrafikLank(lankId: string): boolean;
  valdaTrafikLankar(): readonly TrafikValdLank[];
  trafikLage(): TrafikScenarioLage;
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
    const valda = state.valdaTrafikLankar ?? [];
    const lage = state.trafikLage ?? "nulage";
    let fel: string | null = null;
    if (valda.length > 0) {
      if (!arTal(state.param.mottagarhojd_m) || state.param.mottagarhojd_m <= 0) {
        fel = "Mottagarhöjd måste vara > 0 m.";
      } else {
        fel = valideraValdaTrafikLankar(valda, lage);
      }
    } else {
      fel = valideraBullerParam(state.param);
    }
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
      const begaran = byggBullerBegaranMedTrafik(opts.projekt, state.param, valda, {
        scenario: state.scenario,
        trafikLage: lage,
      });
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

  const sparaTrafik = async (): Promise<void> => {
    const valda = state.valdaTrafikLankar ?? [];
    if (valda.length === 0) return;
    state.trafikSparar = true;
    state.trafikFel = null;
    rita();
    try {
      const payload = byggTrafikJusteringarPayload(valda);
      if (opts.onSparaTrafik) {
        const uppd = await opts.onSparaTrafik(payload);
        if (uppd) state.trafikLankar = uppd;
      } else {
        const { lasJson } = await import("../api.ts");
        const svar = await lasJson<unknown>(projektTrafikUrl(opts.projekt), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        state.trafikLankar = tolkaProjektTrafikGeoJson(svar);
      }
    } catch (e) {
      state.trafikFel = e instanceof Error ? e.message : String(e);
    } finally {
      state.trafikSparar = false;
      rita();
    }
  };

  const klickaTrafikLank = (lankId: string): boolean => {
    const hittad = (state.trafikLankar ?? []).find((l) => l.lank_id === lankId);
    if (!hittad) return false;
    state.valdaTrafikLankar = valjEllerAvmarkeraTrafikLank(state.valdaTrafikLankar ?? [], hittad);
    state.fel = null;
    opts.onValdaTrafikAndrade?.(state.valdaTrafikLankar, state.trafikLage ?? "nulage");
    rita();
    return true;
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
    "change",
    (h) => {
      const inp = (h.target as Element).closest<HTMLInputElement>("[data-bl-lank-falt]");
      if (!inp) return;
      const lid = inp.dataset.blLankId;
      const falt = inp.dataset.blLankFalt as
        | "adt_fordon_dygn"
        | "prognos_adt_fordon_dygn"
        | "hastighet_km_h"
        | "andel_tung_pct"
        | undefined;
      if (!lid || !falt) return;
      const tal = tolkaBullerTal(inp.value);
      state.valdaTrafikLankar = uppdateraValdTrafikLank(state.valdaTrafikLankar ?? [], lid, {
        [falt]: tal,
      });
      opts.onValdaTrafikAndrade?.(state.valdaTrafikLankar, state.trafikLage ?? "nulage");
      rita();
    },
    { signal: opts.signal },
  );

  behallare.addEventListener(
    "click",
    (h) => {
      const m = (h.target as Element).closest<HTMLElement>(
        "#ar-bl-kor,#ar-bl-fyll-vag,#ar-bl-rita-vag,#ar-bl-spara-trafik,[data-bl-lager],[data-bl-trafik-lage],[data-bl-valj-lank],[data-bl-ta-bort-lank]",
      );
      if (!m) return;
      if (m.id === "ar-bl-spara-trafik") {
        void sparaTrafik();
        return;
      }
      const trafikLageVal = m.dataset.blTrafikLage;
      if (trafikLageVal === "nulage" || trafikLageVal === "prognos") {
        state.trafikLage = trafikLageVal;
        opts.onValdaTrafikAndrade?.(state.valdaTrafikLankar ?? [], trafikLageVal);
        rita();
        return;
      }
      const valjId = m.dataset.blValjLank;
      if (valjId) {
        klickaTrafikLank(valjId);
        return;
      }
      const taBortId = m.dataset.blTaBortLank;
      if (taBortId) {
        state.valdaTrafikLankar = (state.valdaTrafikLankar ?? []).filter(
          (v) => v.lank_id !== taBortId,
        );
        opts.onValdaTrafikAndrade?.(state.valdaTrafikLankar, state.trafikLage ?? "nulage");
        rita();
        return;
      }
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
    sattTrafikLankar: (lankar: readonly TrafikLank[]) => {
      state.trafikLankar = [...lankar];
      rita();
    },
    klickaTrafikLank,
    valdaTrafikLankar: () => state.valdaTrafikLankar ?? [],
    trafikLage: () => state.trafikLage ?? "nulage",
  };
}
