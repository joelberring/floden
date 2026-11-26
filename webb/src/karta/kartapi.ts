/* Anrop till kartverktygets API (`/api/kartverktyg/...`). Servern kontrollerar och räknar; här skickas bara geometri. */
import { lasJson } from "../api";
import type { Utbredning, ProjektVyLike } from "./kartapi-typer";
import type { K9Andring } from "./plandok";
import type { RitLager, RitlagerDok, RollInfo } from "./ritlager";
import type { AtgardSvar, Skarvar } from "./skarv";
import type { WmsSvar } from "./wms";
import type { SektionSvar } from "../flode/sektion";
import type { TidsserieSvar } from "../flode/tidsspelare";
import {
  gyfAnalysUrl,
  varmestressAnalysUrl,
  type GyfAnalysSvar,
  type GyfProfilId,
  type VarmestressAnalysSvar,
  type VarmestressParam,
} from "../flode/gyf-utci";

export type {
  GyfAnalysSvar,
  GyfProfilId,
  Utbredning,
  VarmestressAnalysSvar,
  VarmestressParam,
};
export { gyfAnalysUrl, varmestressAnalysUrl };

const BAS = "/api/kartverktyg";
const post = <T>(url: string, kropp: unknown): Promise<T> =>
  lasJson<T>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(kropp) });

/** Regnparametrar i `POST .../plan` och `POST .../kor` (spår 1 + Våg 2: exakt backend-kontrakt). */
export interface RegnParam {
  aterkomsttid_ar?: number | null;
  varaktighet_min?: number | null;
  klimatfaktor?: number | null;
  regn_mm?: number | null;
  berakningstyp?: "statisk" | "dynamisk" | null;
  regntyp?: "cds" | "block" | null;
}

/** Skyfallsmetod i `POST .../plan`, `POST .../kor` och statussvar (`"graf"` är standard). */
export type SkyfallMetod = "graf" | "2d";

/** Solparametrar i `POST .../plan` och `POST .../kor` (Våg 2: exakt backend-kontrakt). */
export interface SolParam {
  datum_iso?: string | null;
  start_timme?: number | null;
  slut_timme?: number | null;
  sammanhangande?: boolean | null;
  inkludera_vsc?: boolean | null;
  klockslag_utc?: number | null;
}

/** Begäran till `POST /api/kartverktyg/<projekt>/sektion` (`floden-sektion/v1`). */
export interface SektionBegaran {
  punkter: Array<[number, number]>;
  korning_id?: string | null;
  fore_lager?: string | null;
  efter_lager?: string | null;
  steg_m?: number | null;
}

/** Projektets förvalda regn ur `GET /api/kartverktyg/<projekt>` (`info.regn_forval`). */
export interface RegnForval extends RegnParam {
  kalla?: string | null;
}

export interface ProjektInfo {
  id: string;
  epsg: number;
  hojd_max_m: number;
  vaningshojd_m: number;
  utbredning: Utbredning;
  baser: Record<string, { rutor: Utbredning[]; analyser: string[] }>;
  fokus: number[] | null;
  byggnader: Array<{ id: string; ring: Array<[number, number]>; hal: Array<Array<[number, number]>>; hojd_m: number; baser: string[]; i_projektet?: boolean }>;
  byggnader_trunkerade: boolean;
  analyser: Array<{ id: string; namn: string; mall: string; analys: string; forval: boolean }>;
  scenarier: ScenarioPost[];
  ortofoto: { finns: false } | { finns: true; antal_filer: number; png: string; kalla: string };
  /** Additivt (spår 1): projektets förvalda regn ur skyfallsmallen, eller null om projektet saknar skyfallsmall. */
  regn_forval?: RegnForval | null;
  /** Additivt (R2, K50): förvalda ritlager och rollerna (vad ett objekt blir i beräkningen). */
  ritlager?: { kontrakt: string; lager: RitLager[]; roller: RollInfo[] };
  /** Additivt (projekt i en geografi): vad för slags projekt, och dess avgränsning och dataklass (K38). */
  paahittat?: boolean;
  slag?: "demo" | "geografi";
  namn?: string;
  geografi?: string | null;
  avgransning_3011?: number[] | null;
  avgransning_polygon_3011?: { type: "Polygon"; coordinates: number[][][] } | null;
  oppen?: boolean;
  licens?: string | null;
  varningar?: string[];
  /** Additivt (R3): basens brytlinjer (fästmål och visning). */
  brytlinjer?: Array<{ typ: string; hojd_m: number; koordinater: Array<[number, number]> }>;
  brytlinjer_trunkerade?: boolean;
}

export interface ScenarioPost {
  slug: string;
  namn: string;
  andrad: string | null;
  antal_andringar: number;
  korningar: Record<string, { korning_id: string; ref: string; k9_nyckel: string }>;
}

export interface Scenario extends ScenarioPost {
  format: string;
  projekt: string;
  skapad: string;
  beskrivning: string;
  andringar: K9Andring[];
  /** Additivt (K50): förslagets lager och objekt; förslag utan lager får de förvalda. */
  ritlager?: RitlagerDok;
  /** Additivt: K9-ändringar som inte kommer ur ritade objekt. */
  fria_andringar?: K9Andring[];
}

export interface Kontroll {
  giltig: boolean;
  fel: string[];
  varningar: string[];
  /** Upplysningar som inte hindrar körning (t.ex. att ett hus stänger in en gård). */
  information?: string[];
  hus: Array<{ andring_id: string; yta_m2: number; hojd_m: number; vaningar: number; bta_m2: number; vaningshojd_m: number }>;
  andringar: K9Andring[];
  ritlager?: RitlagerDok | null;
  /** Additivt (R1, K49): skarvkontrollens fynd, eller null/utelämnat när förslaget saknar ny mark. */
  skarvar?: Skarvar | null;
}

/** Det klienten skickar: fria K9-ändringar och (K50) ritlagren med objekt. */
export interface ForslagIn {
  andringar: K9Andring[];
  ritlager?: RitlagerDok;
}

export interface PlanPost {
  mall: string;
  ref: string;
  fraga_id: string;
  k9: string;
  k9_nyckel: string;
  analyser: string[];
  paverkansradie_m: Record<string, number | null>;
  uppskattad_tid_s: number | null;
  tid_text: string | null;
  plan: {
    jobb: Array<{ analys: string; scenario?: string; losare: string; i_cache: boolean; radie_m: number | null; [k: string]: unknown }>;
    [k: string]: unknown;
  };
}

/** Strukturerade regnparametrar ur en klar körning (additivt i `status` och `lage-idag/status`, B5). */
export interface KorningRegnInfo {
  aterkomsttid_ar: number | null;
  varaktighet_min: number | null;
  klimatfaktor: number | null;
  regn_mm: number | null;
  berakningstyp: "statisk" | "dynamisk";
  regntyp: "cds" | "block" | null;
  kalla: string | null;
  ar_standard: boolean;
  skyfall_metod?: SkyfallMetod | null;
}

/** Strukturerade solparametrar ur en klar körning (additivt i `status` och `lage-idag/status`, B14). */
export interface KorningSolInfo {
  datum_iso: string | null;
  start_timme: number | null;
  slut_timme: number | null;
  sammanhangande: boolean;
  inkludera_vsc: boolean;
  klockslag_utc: number | null;
  ar_standard: boolean;
}

export interface KorningPost {
  mall: string;
  status: { status: string; [k: string]: unknown };
  vyer: ProjektVyLike[];
  /** Varningar från en klar körning (t.ex. slutna områden). */
  varningar?: string[];
  /** Läsbar regntext ur körningens antaganden. */
  regn_text?: string | null;
  /** Strukturerade regnparametrar ur körningen (additivt). */
  regn?: KorningRegnInfo | null;
  /** Använd skyfallsmetod (`"graf"` eller `"2d"`) för vattenkörningen (additivt). */
  skyfall_metod?: SkyfallMetod | null;
  /** Avtappning till ledningsnät i mm/h, eller null för standard (0 mm/h ej angiven). */
  avtappning_mm_h?: number | null;
  /** Strukturerade solparametrar ur körningen (additivt). */
  sol?: KorningSolInfo | null;
}

export const hamtaProjektInfo = (p: string): Promise<ProjektInfo> => lasJson(`${BAS}/${encodeURIComponent(p)}`);
export const kontrolleraAndringar = (p: string, forslag: ForslagIn): Promise<Kontroll> => post(`${BAS}/${encodeURIComponent(p)}/validera`, forslag);
/** Föreslår och verifierar en åtgärd för skarven (R5); ändrar inget på servern. */
export const foreslaSkarvatgard = (p: string, forslag: ForslagIn): Promise<AtgardSvar> => post(`${BAS}/${encodeURIComponent(p)}/skarvatgard`, forslag);
export const listaScenarier = (p: string): Promise<{ scenarier: ScenarioPost[] }> => lasJson(`${BAS}/${encodeURIComponent(p)}/scenarier`);
export const hamtaScenario = (p: string, slug: string): Promise<Scenario> => lasJson(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}`);
export const sparaScenario = (p: string, namn: string, forslag: ForslagIn, slug?: string): Promise<Scenario> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier${slug ? `/${encodeURIComponent(slug)}` : ""}`, { namn, ...forslag });
export const bytScenarioNamn = (p: string, slug: string, namn: string): Promise<Scenario> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/byt-namn`, { namn });
export const taBortScenario = (p: string, slug: string): Promise<{ borttaget: boolean }> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/ta-bort`, {});
/** `avtappning_mm_h` null = lösarens standard 0 mm/h (ledningsnätet antas fullt). `regn` / `sol` null = projektets förval. `skyfall_metod` null / `"graf"` = standard sänkgraf. */
export const planeraScenario = (
  p: string,
  slug: string,
  analyser: string[],
  avtappning_mm_h: number | null = null,
  regn: RegnParam | null = null,
  sol: SolParam | null = null,
  skyfall_metod: SkyfallMetod | null = null,
): Promise<{ slug: string; fragor: PlanPost[] }> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/plan`, {
    analyser,
    avtappning_mm_h,
    ...(regn ? { regn } : {}),
    ...(sol ? { sol } : {}),
    ...(skyfall_metod && skyfall_metod !== "graf" ? { skyfall_metod } : {}),
  });
export const korScenario = (
  p: string,
  slug: string,
  analyser: string[],
  avtappning_mm_h: number | null = null,
  regn: RegnParam | null = null,
  sol: SolParam | null = null,
  skyfall_metod: SkyfallMetod | null = null,
): Promise<{ slug: string; fragor: Array<PlanPost & { korning: { korning_id: string } }> }> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/kor`, {
    analyser,
    avtappning_mm_h,
    ...(regn ? { regn } : {}),
    ...(sol ? { sol } : {}),
    ...(skyfall_metod && skyfall_metod !== "graf" ? { skyfall_metod } : {}),
  });
export const scenarioStatus = (p: string, slug: string): Promise<{ slug: string; korningar: KorningPost[] }> =>
  lasJson(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/status`);
/** Läget idag (U5a): projektets befintliga läge, bara före, utan förslag. Samma behörighet och körkö som `kor`. */
export interface LagePlan {
  lage: "idag";
  fragor: PlanPost[];
  uppskattad_tid_s: number | null;
}
export const planeraLageIdag = (
  p: string,
  analyser: string[],
  avtappning_mm_h: number | null = null,
  regn: RegnParam | null = null,
  sol: SolParam | null = null,
  skyfall_metod: SkyfallMetod | null = null,
): Promise<LagePlan> =>
  post(`${BAS}/${encodeURIComponent(p)}/lage-idag/plan`, {
    analyser,
    avtappning_mm_h,
    ...(regn ? { regn } : {}),
    ...(sol ? { sol } : {}),
    ...(skyfall_metod && skyfall_metod !== "graf" ? { skyfall_metod } : {}),
  });
export const korLageIdag = (
  p: string,
  analyser: string[],
  avtappning_mm_h: number | null = null,
  regn: RegnParam | null = null,
  sol: SolParam | null = null,
  skyfall_metod: SkyfallMetod | null = null,
): Promise<{ lage: "idag"; fragor: Array<PlanPost & { korning: { korning_id: string } }> }> =>
  post(`${BAS}/${encodeURIComponent(p)}/lage-idag/kor`, {
    analyser,
    avtappning_mm_h,
    ...(regn ? { regn } : {}),
    ...(sol ? { sol } : {}),
    ...(skyfall_metod && skyfall_metod !== "graf" ? { skyfall_metod } : {}),
  });
export const lageIdagStatus = (p: string): Promise<{ lage: "idag"; korningar: KorningPost[] }> =>
  lasJson(`${BAS}/${encodeURIComponent(p)}/lage-idag/status`);
/** Hämtar höjd-, byggnads- och vattenprofil längs en sektion i EPSG:3011 (`floden-sektion/v1`). */
export const hamtaSektion = (p: string, begaran: SektionBegaran): Promise<SektionSvar> =>
  post(`${BAS}/${encodeURIComponent(p)}/sektion`, begaran);
/** Hämtar dynamisk tidsserie och hydrograf för en skyfallskörning (`floden-tidsserie/v1`). */
export const hamtaTidsserie = (korningId: string, scenario: string): Promise<TidsserieSvar> =>
  lasJson(`/api/korningar/${encodeURIComponent(korningId)}/tidsserie?scenario=${encodeURIComponent(scenario)}`);
export const lasWmsLager = (url: string): Promise<WmsSvar> => post(`${BAS}/wms/lager`, { url });

export interface BifurkationsPunktPost {
  nod_id: number;
  primar_ut_till_id: number | null;
  alternativ_ut_till_id: number | null;
  overfall_hojd_m: number;
  alternativ_hojd_m: number;
  marginal_m: number;
  osakerhet_komb_m: number;
  sakerhetsmarginal: number | null;
  instabil: boolean;
  overfall_x: number;
  overfall_y: number;
  alternativ_x: number;
  alternativ_y: number;
  paverkad_avrinningsyta_m2: number;
  paverkad_volym_m3: number;
}

export interface BifurkationAnalysSvar {
  format: string;
  omrade_id: string;
  k_sigma: number;
  referensregn_mm: number;
  antal_instabila: number;
  punkter: BifurkationsPunktPost[];
  felindikator: {
    andel_2d_yta: number;
    antal_2d_celler: number;
    antal_aktiva_celler: number;
    antal_noder_2d: number;
    antal_noder_totalt: number;
  };
}

/** Hämtar topologiskt instabila överfallspunkter ("mät in i fält") och 2D-felindikator. */
export const hamtaBifurkationer = (
  omrade_id: string,
  k_sigma = 2.0,
  referensregn_mm = 100.0,
): Promise<BifurkationAnalysSvar> =>
  post("/api/analys/bifurkationer", { omrade_id, k_sigma, referensregn_mm });

export interface VattenIntervallSvar {
  format: string;
  omrade_id: string;
  klimatfaktor_intervall?: [number, number];
  antal_korningar: number;
  antal_bifurkationsgrenar: number;
  max_volymfel_rel: number;
  sammanfattning: {
    antal_noder: number;
    antal_monoton_garanterad: number;
    antal_bifurkationspaverkade: number;
    storsta_min_djup_m: number;
    storsta_max_djup_m: number;
  };
}

export interface SvfIntervallSvar {
  format: string;
  omrade_id: string;
  osakerhet_byggnad_m: number;
  antal_riktningar: number;
  svf_min_medel: number;
  svf_max_medel: number;
  bredd_max: number;
  antal_osakra_celler: number;
}

/** Hämtar monotont garanterade vattenståndsintervall (K40). */
export const hamtaVattenIntervall = (
  omrade_id: string,
  klimatfaktor_intervall: [number, number] = [1.0, 1.25],
): Promise<VattenIntervallSvar> =>
  post("/api/analys/intervall", { omrade_id, klimatfaktor_intervall });

/** Hämtar monotont garanterade SVF-intervall vid byggnadshöjdsosäkerhet (K41). */
export const hamtaSvfIntervall = (
  omrade_id: string,
  osakerhet_byggnad_m = 1.0,
  antal_riktningar = 16,
): Promise<SvfIntervallSvar> =>
  post("/api/analys/svf-intervall", { omrade_id, osakerhet_byggnad_m, antal_riktningar });

/** Läser Svensk Grönytefaktor (GYF, skiss) utan sidskrivning via `GET /api/analys/gyf`. */
export const hamtaGyfAnalys = (
  omrade_id: string,
  profil: GyfProfilId = "stockholm",
  scenario?: string | null,
): Promise<GyfAnalysSvar> => lasJson(gyfAnalysUrl(omrade_id, profil, scenario));

/** Beräknar Svensk Grönytefaktor (GYF, skiss) via `POST /api/analys/gyf`. */
export const beraknaGyfAnalys = (begaran: {
  omrade_id: string;
  profil?: GyfProfilId;
  scenario?: string | null;
  tillagg?: Array<{ tillagg_id: string; area_m2: number; faktor?: number }>;
}): Promise<GyfAnalysSvar> => post("/api/analys/gyf", begaran);

/** Läser Värmestress UTCI/T_mrt (skiss) utan sidskrivning via `GET /api/analys/varmestress`. */
export const hamtaVarmestressAnalys = (
  omrade_id: string,
  param: Partial<VarmestressParam> = {},
  scenario?: string | null,
): Promise<VarmestressAnalysSvar> => lasJson(varmestressAnalysUrl(omrade_id, param, scenario));

/** Beräknar Värmestress UTCI/T_mrt (skiss) med explicita väderparametrar via `POST /api/analys/varmestress`. */
export const beraknaVarmestressAnalys = (
  begaran: { omrade_id: string; scenario?: string | null } & Partial<VarmestressParam>,
): Promise<VarmestressAnalysSvar> => post("/api/analys/varmestress", begaran);

/** Hämtar en rasterbild med utbredningen ur huvudet `X-Floden-Utbredning` och ger en objekt-URL. */
export async function hamtaBildMedUtbredning(url: string): Promise<{ url: string; utbredning: Utbredning }> {
  const svar = await fetch(url);
  if (!svar.ok) throw new Error(`Bilden gick inte att hämta (HTTP ${svar.status}).`);
  const h = svar.headers.get("X-Floden-Utbredning") ?? "";
  const [x_min, y_min, x_max, y_max] = h.split(",").map(Number);
  if ([x_min, y_min, x_max, y_max].some((v) => v === undefined || !Number.isFinite(v))) throw new Error("Bilden saknar utbredning.");
  const blob = await svar.blob();
  return { url: URL.createObjectURL(blob), utbredning: { x_min: x_min as number, y_min: y_min as number, x_max: x_max as number, y_max: y_max as number } };
}
