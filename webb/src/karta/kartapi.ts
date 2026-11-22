/* Anrop till kartverktygets API (`/api/kartverktyg/...`). Servern kontrollerar och räknar; här skickas bara geometri. */
import { lasJson } from "../api";
import type { Utbredning, ProjektVyLike } from "./kartapi-typer";
import type { K9Andring } from "./plandok";
import type { WmsSvar } from "./wms";

export type { Utbredning };

const BAS = "/api/kartverktyg";
const post = <T>(url: string, kropp: unknown): Promise<T> =>
  lasJson<T>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(kropp) });

export interface ProjektInfo {
  id: string;
  epsg: number;
  hojd_max_m: number;
  vaningshojd_m: number;
  utbredning: Utbredning;
  baser: Record<string, { rutor: Utbredning[]; analyser: string[] }>;
  fokus: number[] | null;
  byggnader: Array<{ id: string; ring: Array<[number, number]>; hal: Array<Array<[number, number]>>; hojd_m: number; baser: string[] }>;
  byggnader_trunkerade: boolean;
  analyser: Array<{ id: string; namn: string; mall: string; analys: string; forval: boolean }>;
  scenarier: ScenarioPost[];
  ortofoto: { finns: false } | { finns: true; antal_filer: number; png: string; kalla: string };
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
}

export interface Kontroll {
  giltig: boolean;
  fel: string[];
  varningar: string[];
  hus: Array<{ andring_id: string; yta_m2: number; hojd_m: number; vaningar: number; bta_m2: number; vaningshojd_m: number }>;
  andringar: K9Andring[];
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

export interface KorningPost {
  mall: string;
  status: { status: string; [k: string]: unknown };
  vyer: ProjektVyLike[];
}

export const hamtaProjektInfo = (p: string): Promise<ProjektInfo> => lasJson(`${BAS}/${encodeURIComponent(p)}`);
export const kontrolleraAndringar = (p: string, andringar: K9Andring[]): Promise<Kontroll> => post(`${BAS}/${encodeURIComponent(p)}/validera`, { andringar });
export const listaScenarier = (p: string): Promise<{ scenarier: ScenarioPost[] }> => lasJson(`${BAS}/${encodeURIComponent(p)}/scenarier`);
export const hamtaScenario = (p: string, slug: string): Promise<Scenario> => lasJson(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}`);
export const sparaScenario = (p: string, namn: string, andringar: K9Andring[], slug?: string): Promise<Scenario> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier${slug ? `/${encodeURIComponent(slug)}` : ""}`, { namn, andringar });
export const bytScenarioNamn = (p: string, slug: string, namn: string): Promise<Scenario> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/byt-namn`, { namn });
export const taBortScenario = (p: string, slug: string): Promise<{ borttaget: boolean }> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/ta-bort`, {});
export const planeraScenario = (p: string, slug: string, analyser: string[]): Promise<{ slug: string; fragor: PlanPost[] }> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/plan`, { analyser });
export const korScenario = (p: string, slug: string, analyser: string[]): Promise<{ slug: string; fragor: Array<PlanPost & { korning: { korning_id: string } }> }> =>
  post(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/kor`, { analyser });
export const scenarioStatus = (p: string, slug: string): Promise<{ slug: string; korningar: KorningPost[] }> =>
  lasJson(`${BAS}/${encodeURIComponent(p)}/scenarier/${encodeURIComponent(slug)}/status`);
export const lasWmsLager = (url: string): Promise<WmsSvar> => post(`${BAS}/wms/lager`, { url });

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
