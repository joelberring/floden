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
  status: KorStatus;
  skapad: string;
  start: string | null;
  slut: string | null;
  fel: string | null;
  jobb_totalt: number;
  framsteg: { klara: number; totalt: number };
  ateranvand?: boolean;
}

export type Teckenforklaring =
  | { typ: "kontinuerlig"; min: number; max: number; stopp: string[] }
  | { typ: "klasser"; klasser: Array<{ fran: number; farg: string; etikett: string }> }
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
  enhet: string;
  farg: string;
  skala: { min: number; max: number };
  teckenforklaring: Teckenforklaring;
  utbredning: Utbredning;
  pixelstorlek_m: number;
  bredd_px: number;
  hojd_px: number;
  nedsampling: number;
  png: string;
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
  datatabeller: DataTabellRef[];
}

export interface TabellForhandsvisning {
  fil: string;
  kolumner: string[];
  rader: Array<Record<string, unknown>>;
  antal_rader: number;
}
