/* Typer för API:t för utredningsfrågor (K20): /api/fragor och /api/korningar. */

export interface Utbredning {
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
}

export interface FragaAnalys {
  id: string;
  losare: string;
  doman: string | null;
  visningsnamn?: string;
  scenarier: string[];
  parametrar: Record<string, unknown>;
  beror_pa: string[];
}

export interface FragaScenario {
  id: string;
  bas: string;
  k9: string[];
  lappar: string[];
}

export interface FragaJamforelse {
  analys: string;
  utdata: string;
  fore: string;
  efter: string;
  troskel: number;
}

export interface FragaPost {
  ref: string;
  fil: string;
  hjalp: string;
  fraga_id: string;
  beskrivning: string;
  giltig: boolean;
  fel: string | null;
  scenarier?: FragaScenario[];
  analyser?: FragaAnalys[];
  jamforelser?: FragaJamforelse[];
  indata_saknas?: string[];
  i_demon?: boolean;
}

export interface FragorSvar {
  fragemappar: string[];
  fragor: FragaPost[];
}

export interface PlanRuta {
  ruta_id: string;
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
  hash: string;
}

export interface PlanScenario {
  id: string;
  nyckel: string;
  antal_rutor: number;
  rutor: PlanRuta[];
}

export interface PlanJobb {
  etikett: string;
  analys: string;
  scenario: string;
  ruta: string | null;
  losare: string;
  version: string;
  radie_m: number | null;
  grannar: string[];
  nyckel: string;
  i_cache: boolean;
}

export interface PlanSvar {
  ref: string;
  /** Hash av planens jobbnycklar: en körning med samma nyckel hör till exakt den här frågan. */
  plan_nyckel?: string;
  fraga_id: string;
  sammanfattning: { jobb: number; i_cache: number; att_kora: number };
  per_analys: Record<string, { jobb: number; i_cache: number; att_kora: number }>;
  scenarier: PlanScenario[];
  utbredning: Utbredning | null;
  jobb: PlanJobb[];
}

export type KorStatus = "ko" | "kor" | "klar" | "fel";

export interface KorningSvar {
  korning_id: string;
  ref: string;
  fraga_id: string;
  fraga_fil: string;
  plan_nyckel?: string | null;
  status: KorStatus;
  skapad: string;
  start: string | null;
  slut: string | null;
  fel: string | null;
  /** Klarspråksförklaring av `fel` när felet är känt (annars null). */
  fel_forklaring?: string | null;
  jobb_totalt: number;
  framsteg: { klara: number; totalt: number };
  ateranvand?: boolean;
}

export type Teckenforklaring =
  | { typ: "kontinuerlig"; min: number; max: number; stopp: string[] }
  | { typ: "klasser"; klasser: Array<{ fran: number; farg: string; etikett: string }> }
  /** Heltalsklasser (vindkomfort, kontrollflaggor): en färg per klassvärde. */
  | { typ: "kategorier"; kategorier: Array<{ varde: number; farg: string; etikett: string }> }
  | {
      typ: "divergerande";
      min: number;
      max: number;
      troskel: number;
      farg_minskning: string;
      farg_okning: string;
    };

export interface LagerPost {
  id: string;
  namn: string;
  etikett: string;
  analys: string;
  scenario: string;
  roll: string;
  typ: "bas" | "scenario" | "skillnad";
  primar: boolean;
  /** Ämnet lösaren hör till: vatten, geometri, dagsljus, ekologi, luft, buller. */
  doman?: string;
  /** Kontrollager (avskuren horisont, byggnad utan känd höjd, slutet område): visar osäkerhet, inte ett mått. */
  kontroll?: boolean;
  /** Skillnaden är efter minus före av de visade lagren (frågan jämförde inte rollen själv); ingen GeoTIFF. */
  harledd?: boolean;
  enhet: string;
  farg: string;
  skala: { min: number; max: number };
  teckenforklaring: Teckenforklaring;
  utbredning: Utbredning;
  /** Bounding box för de celler som faktiskt har ett värde (utan byggnadsmaskade celler). */
  synlig_utbredning: Utbredning | null;
  pixelstorlek_m: number;
  bredd_px: number;
  hojd_px: number;
  nedsampling: number;
  png: string;
  /** Samma lager utan takmask: byggnadernas fotavtryck visas med värden. */
  png_med_tak: string | null;
  tak_maskat: boolean;
  tak_celler: number;
  tak_yta_m2: number;
  geotiff: Array<{ ruta: string | null; fil: string; url: string }>;
  fore?: string;
  efter?: string;
}

export interface MatvardeRad {
  scenario: string;
  ruta: string | null;
  fran_cache: boolean;
  kortid_s: number;
  varden: Record<string, unknown>;
}

export interface MatvardeTabell {
  analys: string;
  losare: string;
  kolumner: string[];
  rader: MatvardeRad[];
}

export interface JamforelseResultat {
  namn: string;
  analys: string;
  utdata: string;
  fore: string;
  efter: string;
  troskel: number;
  max_okning: number;
  max_minskning: number;
  yta_okning_m2: number;
  yta_minskning_m2: number;
  rutor: number;
  rutor_med_samma_nyckel: number;
}

export interface VarningPost {
  text: string;
  kalla: "losare" | "webb";
  antal_jobb: number;
  analyser: string[];
  scenarier: string[];
  varianter: Array<{ text: string; antal_jobb: number }>;
}

export interface DataTabellRef {
  analys: string;
  scenario: string;
  ruta: string | null;
  roll: string;
  fil: string;
  url: string;
  forhandsvisning: string;
}

export type ByggnadStatus = "oforandrad" | "ny" | "andrad";

export interface ByggnadPost {
  id: string;
  ring: Array<[number, number]>;
  hal: Array<Array<[number, number]>>;
  hojd_m: number;
  takhojd_rh2000: number;
  hojdkalla: string;
  status: ByggnadStatus;
  /** Bara när projektet har en polygon: räknas byggnaden till projektet (fotavtryckets inre punkt inne i polygonen)? */
  i_projektet?: boolean;
}

export interface ByggnaderSvar {
  bas: string;
  scenarier: Record<string, ByggnadPost[]>;
  /** Sant när projektet har en polygon: byggnader utanför den visas men räknas inte. */
  avgransad_till_projektet?: boolean;
  antal_i_projektet?: Record<string, number>;
}

/** Projektets polygon i ett resultat (`projekt_avgransning`): den avgör vilka celler och byggnader som räknas. */
export interface ProjektAvgransning {
  polygon_3011: GeoJSON.Polygon;
  yta_m2: number;
  epsg: number;
}

export interface RegnAntagande {
  analys: string;
  kalla: string | null;
  rapport?: string | null;
  tabell?: string | null;
  url?: string | null;
  aterkomsttid_ar?: number | null;
  varaktighet_min?: number | null;
  regntyp?: string | null;
  klimatfaktor: number | null;
  klimatfaktor_ingar_i_tabell?: boolean | null;
  regn_mm: number | null;
}

export interface AnalysAntagande {
  analys: string;
  losare: string;
  version: string;
  kodversion: string | null;
  parametrar: Record<string, unknown>;
}

/** Takvatten och avtappning per analys, med lösarens egna texter (webben räknar inget). */
export interface VattenAntagande {
  analys: string;
  takvatten: boolean | null;
  takvatten_text: string | null;
  avtappning_mm_h: number | null;
  avtappning_kalla: string | null;
  avtappning_text: string | null;
}

export interface Antaganden {
  regn: RegnAntagande[];
  analyser: AnalysAntagande[];
  vatten?: VattenAntagande[];
}

export interface AvlasningVarde {
  lager_id: string;
  namn: string;
  typ: "bas" | "scenario" | "skillnad";
  scenario: string;
  enhet: string;
  etikett: string;
  varde: number | null;
  tak: boolean;
  inom: boolean;
}

export interface AvlasningSvar {
  e: number;
  n: number;
  analys: string;
  roll: string;
  varden: AvlasningVarde[];
}

export interface KorningLista {
  korningar: KorningSvar[];
}

export interface DemoDel {
  ref: string;
  korning_id: string;
  jobb: number;
  oforandrad: boolean;
  tid_s: number;
}

export interface DemoSvar {
  finns: boolean;
  lage?: string;
  total_tid_s?: number;
  delar?: {
    demo_dagsljus?: DemoDel;
    demo_vatten?: DemoDel;
    luft?: { status: string; text: string };
  };
}

export interface ResultatSvar {
  korning: KorningSvar;
  fraga_id: string;
  beskrivning: string;
  sammanfattning: { jobb: number; i_cache: number; att_kora: number; total_tid_s: number };
  scenarier: Array<{ id: string; nyckel: string; rutor: number }>;
  tabeller: MatvardeTabell[];
  jamforelser: JamforelseResultat[];
  varningar: VarningPost[];
  lager: LagerPost[];
  byggnader: ByggnaderSvar | null;
  /** Projektets polygon när projektet har en (mark och byggnader räknas bara inne i den), annars null. */
  projekt_avgransning?: ProjektAvgransning | null;
  antaganden: Antaganden;
  datatabeller: DataTabellRef[];
}

export interface TabellForhandsvisning {
  fil: string;
  kolumner: string[];
  rader: Array<Record<string, unknown>>;
  antal_rader: number;
}
