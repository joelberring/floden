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
  | "differens";

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

export interface KorBegaran {
  omrade_id: string;
  intensitet_mm_h: number;
  varaktighet_s: number;
  lapp_id?: string;
  landxml?: string;
  gator?: LappGataPayload[];
  byggnader?: LappByggnadPayload[];
  magasin?: LappMagasinPayload[];
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
  kortid_s: number;
  manifest: RunManifestK0;
  kallor: string[];
}
