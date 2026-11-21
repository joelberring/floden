export type BboxTuple = [number, number, number, number];

export type PercentilNyckel = "p10" | "p50" | "p90";

export type JamforLage = "svep" | "fore" | "efter" | "differens" | "sidavid";

export type LagerId =
  | "hillshade"
  | "byggnader"
  | "brytlinjer"
  | "sankor"
  | "flodesvagar"
  | "maxdjup"
  | "osakerhet"
  | "differens"
  | "risk"
  | "rutt";

export interface GatusektionSpec {
  namn: string;
  total_bredd_m: number;
  korbana_bredd_m: number;
  gangbana_bredd_m: number;
  kantsten_hojd_m: number;
  tvarfall_korbana_andel: number;
  tvarfall_gangbana_andel: number;
  brunnsavstand_m: number;
}

export interface HalsaSvar {
  status: string;
  version: string;
  kontrakt: {
    manifest: string;
    lapp: string;
    resultat: string;
  };
  epsg: number;
  hojdsystem: string;
  kallor: string[];
  gatusektioner: GatusektionSpec[];
}

export interface OmradeSammanfattning {
  omrade_id: string;
  kod: string;
  namn: string;
  grupp: string;
  beskrivning: string;
  bredd_px: number;
  hojd_px: number;
  upplosning_m: number;
  bbox_3011: BboxTuple;
  bbox_wgs84: BboxTuple;
  standard_regn: {
    intensitet_mm_h: number;
    varaktighet_s: number;
  };
  kallor: string[];
}

export interface OmradenSvar {
  epsg: number;
  hojdsystem: string;
  omraden: OmradeSammanfattning[];
}

export interface GeoJsonFeature<P = Record<string, unknown>> {
  type: "Feature";
  id?: string | number;
  geometry: {
    type: "Point" | "LineString" | "Polygon";
    coordinates: number[] | number[][] | number[][][];
  };
  properties: P;
}

export interface GeoJsonCollection<P = Record<string, unknown>> {
  type: "FeatureCollection";
  features: Array<GeoJsonFeature<P>>;
}

export interface RasterBild {
  png_base64: string;
  data_uri: string;
  bredd_px: number;
  hojd_px: number;
}

export interface HillshadeBild extends RasterBild {
  z_min_rh2000: number;
  z_max_rh2000: number;
}

export interface DjupLagerData extends RasterBild {
  max_djup_m: number;
  medel_djup_m: number;
  oversvammad_area_m2: number;
}

export interface SimuleringDjupData extends DjupLagerData {
  in_m3: number;
  lagrat_m3: number;
  ut_m3: number;
  volymfel_rel: number;
}

export interface DiffLagerData extends RasterBild {
  min_diff_m: number;
  max_diff_m: number;
  max_okning_djup_granne_m?: number;
}

export interface OsakerhetBlock {
  mark_osakerhet_medel_m?: number;
  mark_osakerhet_max_m?: number;
  percentiler: number[];
  antal_scenarier: number;
  max_volymfel_rel: number;
  total_kortid_s: number;
  p10: DjupLagerData;
  p50: DjupLagerData;
  p90: DjupLagerData;
}

export interface OmradeLagerSvar {
  omrade_id: string;
  kod: string;
  namn: string;
  beskrivning: string;
  epsg: number;
  hojdsystem: string;
  bas_recept_sha256: string;
  upplosning_m: number;
  bredd_px: number;
  hojd_px: number;
  bbox_3011: BboxTuple;
  bbox_wgs84: BboxTuple;
  standard_regn: {
    tid_s: number[];
    intensitet_mm_h: number[];
    intensitet_mm_h_medel: number;
    varaktighet_s: number;
  };
  hillshade: HillshadeBild;
  bas_max_djup: DjupLagerData;
  sankor: GeoJsonCollection;
  flodesvagar: GeoJsonCollection;
  brytlinjer: GeoJsonCollection;
  byggnader: GeoJsonCollection;
  osakerhet: OsakerhetBlock;
  gatusektioner: GatusektionSpec[];
  kallor: string[];
}

export interface LappGataPayload {
  gata_id: string;
  mittlinje: Array<[number, number]>;
  sektion: string;
}

export interface LappByggnadPayload {
  byggnad_id: string;
  polygon: Array<[number, number]>;
  entre_rh2000: number;
  entre_punkt: [number, number];
}

export interface LappMagasinPayload {
  magasin_id: string;
  polygon: Array<[number, number]>;
  volym_m3: number;
}

export type RegnTyp = "cds" | "block";

export interface RegnForvalInfo {
  regn_id: string;
  namn: string;
  region: string;
  typ: string;
  aterkomsttid_ar: number;
  klimatfaktor: number;
  varaktighet_min: number;
  tidssteg_min: number;
  toppfaktor: number;
  statistikkalla: string;
  beskrivning: string;
  total_djup_mm: number;
  max_intensitet_mm_h: number;
}

export interface RegnKatalogSvar {
  kontrakt: string;
  standard_aterkomsttider_ar: number[];
  standard_klimatfaktorer: number[];
  forval: RegnForvalInfo[];
}

export interface ForlusterBegaran {
  aktivera: boolean;
  jordart: string;
  grundvatten: "normal" | "hog";
  tjale: boolean;
  initial_mm: number;
}

export interface ForlusterSammanfattning {
  jordart: string;
  grundvatten: string;
  tjale: boolean;
  initial_mm: number;
  brunnar_igensatta_andel: number;
  brutto_regn_mm: number;
  initial_forlust_medel_mm: number;
  infiltration_medel_mm: number;
  netto_avrinning_medel_mm: number;
  effektiv_avrinningskoefficient: number;
}

export interface KorBegaran {
  omrade_id: string;
  intensitet_mm_h?: number;
  varaktighet_s?: number;
  regn_id?: string;
  aterkomsttid_ar?: number;
  klimatfaktor?: number;
  varaktighet_min?: number;
  regntyp?: RegnTyp;
  lapp_id?: string;
  landxml?: string;
  gator?: LappGataPayload[];
  byggnader?: LappByggnadPayload[];
  magasin?: LappMagasinPayload[];
  forluster?: ForlusterBegaran;
}

export interface RunManifestK0 {
  kontrakt: string;
  modul: string;
  kodversion: string;
  start: string;
  indata: Array<{ roll: string; fil: string; sha256: string }>;
  parametrar: Record<string, unknown>;
  utdata: Array<{ roll: string; fil: string; sha256: string }>;
  matvarden: Record<string, unknown>;
  varningar: string[];
}

export interface KorSvar {
  omrade_id: string;
  lapp_id: string | null;
  har_lapp: boolean;
  epsg: number;
  hojdsystem: string;
  bbox_3011: BboxTuple;
  bbox_wgs84: BboxTuple;
  regn: {
    tid_s: number[];
    intensitet_mm_h: number[];
    regn_id?: string;
    namn?: string;
    total_djup_mm?: number;
    max_intensitet_mm_h?: number;
    klimatfaktor?: number;
    aterkomsttid_ar?: number;
    statistikkalla?: string;
  };
  fore: SimuleringDjupData;
  efter: SimuleringDjupData;
  differens: DiffLagerData;
  efter_hillshade: HillshadeBild;
  efter_lager: {
    sankor: GeoJsonCollection;
    flodesvagar: GeoJsonCollection;
    brytlinjer: GeoJsonCollection;
    byggnader: GeoJsonCollection;
  };
  osakerhet: OsakerhetBlock;
  fordrojningsvolym_m3: number;
  grann_forsamring_volym_m3: number;
  max_okning_djup_granne_m: number;
  nya_drabbade_entreer: Array<{
    entre_id: string;
    fore_max_djup_m: number;
    efter_max_djup_m: number;
    diff_max_djup_m: number;
  }>;
  projekt_entreer: Array<{
    byggnad_id: string;
    entre_rh2000: number;
    max_vattenniva_rh2000: number;
    marginal_m: number;
    overstiger_entre: boolean;
  }>;
  forluster?: ForlusterSammanfattning | null;
  kortid_s: number;
  manifest: RunManifestK0;
  kallor: string[];
}

export interface RiskBegaran {
  omrade_id: string;
  gransdjup_m: number;
  max_aterkomsttid_ar: number;
  klimatfaktor: number;
  tidshorisont_ar: number;
  varaktighet_min?: number;
  regntyp?: RegnTyp;
  aterkomsttider_ar?: number[];
  jordart?: string;
  grundvatten?: "normal" | "hog";
  tjale?: boolean;
  initial_mm?: number;
}

export interface RiskRasterData extends RasterBild {
  max_sannolikhet: number;
}

export interface RiskStegPost {
  aterkomsttid_ar: number;
  total_djup_mm: number;
  max_djup_m: number;
  area_over_grans_m2: number;
}

export interface RiskSvar {
  omrade_id: string;
  epsg: number;
  hojdsystem: string;
  bbox_3011: BboxTuple;
  bbox_wgs84: BboxTuple;
  gransdjup_m: number;
  max_aterkomsttid_ar: number;
  aterkomsttider_ar: number[];
  klimatfaktor: number;
  tidshorisont_ar: number;
  varaktighet_min: number;
  regntyp: string;
  statistikkalla: string;
  kumulativ_risk_karta: RiskRasterData;
  max_djup_vid_tmax: DjupLagerData;
  sammanfattning: {
    area_over_grans_m2: number;
    andel_over_grans: number;
    max_arlig_sannolikhet: number;
    max_kumulativ_sannolikhet: number;
    min_kritisk_aterkomsttid_ar: number | null;
    max_forvantat_arligt_djup_m: number;
    max_volymfel_rel: number;
    antal_aterkomsttider: number;
  };
  steg: RiskStegPost[];
  kortid_s: number;
  manifest: RunManifestK0;
  kallor: string[];
}

export type RapportTyp =
  | "skyfall_oversikt"
  | "detaljplan_fore_efter"
  | "fastighetsagare"
  | "raddningstjanst"
  | "klimatanpassning";

export type RapportFormat = "html" | "md" | "docx";

export interface RapportBegaran {
  omrade_id: string;
  rapporttyp: RapportTyp;
  format: RapportFormat;
  titel?: string;
  regn_id?: string;
  aterkomsttid_ar?: number;
  klimatfaktor?: number;
  intensitet_mm_h?: number;
  varaktighet_s?: number;
  forluster?: ForlusterBegaran;
}

export interface RapportSvar {
  omrade_id: string;
  rapporttyp: string;
  format: string;
  filnamn: string;
  mime_typ: string;
  innehall_base64: string;
  storlek_bytes: number;
  kontroll_godkand: boolean;
  antal_kontrollerade_tal: number;
  varningar: string[];
  kortid_s: number;
  manifest: RunManifestK0;
  kallor: string[];
}

export type FordonTyp = "slacksbil" | "ambulans" | "storskalig";

export interface RuttBegaran {
  omrade_id: string;
  fordon_typ: FordonTyp;
  gransdjup_m: number;
  max_aterkomsttid_ar: number;
  klimatfaktor: number;
  tidshorisont_ar: number;
  tillat_testvarden: boolean;
}

export interface RuttKantPost {
  kant_id: string;
  kritisk_aterkomsttid_ar: number | null;
  arlig_overskridande_sannolikhet: number;
  kumulativ_sannolikhet: number;
  max_djup_vid_tmax_m: number;
}

export interface RuttSvar {
  omrade_id: string;
  fordon_typ: string;
  gransdjup_m: number;
  max_aterkomsttid_ar: number;
  klimatfaktor: number;
  tidshorisont_ar: number;
  testvarden_anvanda: boolean;
  varning: string;
  antal_kanter_totalt: number;
  antal_kanter_med_risk: number;
  max_kumulativ_kant_sannolikhet: number;
  kanter: RuttKantPost[];
  vagnat_geojson: GeoJsonCollection;
  kortid_s: number;
  manifest: RunManifestK0;
  kallor: string[];
}
