/*
 * Underlag från andra tjänster: WMS (via servern som proxy) och kakelmallar (WMTS/XYZ).
 *
 * Idén (adress, läs lagren, välj lager, kom ihåg senast använda) är O-Tids (MIT, joelberring/otid @
 * 4a446af, apps/web/src/components/map-project/map-project-wms-panel.tsx). Där hämtar servern bildrutor
 * som läggs som underlag; här hämtar kartmotorn bildbrickor löpande, och servern (`/api/kartverktyg/wms`)
 * läser lagren och hämtar brickorna, så att webbläsaren inte behöver nå tjänsten (CORS, intranät).
 * Inget externt är förvalt: användaren anger adressen själv.
 */

export interface WmsLager {
  namn: string;
  titel: string;
  /** Koordinatsystemet som tjänsten kan leverera brickor i: 3857 eller 4326, annars null. */
  stod: "3857" | "4326" | null;
}

export interface WmsSvar {
  url: string;
  titel: string;
  version: string;
  lager: WmsLager[];
  anvandbara: WmsLager[];
}

/** Ett sparat underlag. */
export interface Underlag {
  id: string;
  typ: "wms" | "kakel";
  namn: string;
  url: string;
  lager?: string;
  stod?: "3857" | "4326";
  version?: string;
  /** Källa att ange på kartan. */
  kalla: string;
}

/** Mall för MapLibre: brickor via servern. `{bbox-epsg-3857}` fylls i av MapLibre. */
export function wmsBrickmall(u: Pick<Underlag, "url" | "lager" | "stod" | "version">): string {
  const q = new URLSearchParams({
    url: u.url,
    lager: u.lager ?? "",
    crs: u.stod ?? "3857",
    version: u.version ?? "1.3.0",
    bredd: "256",
    hojd: "256",
  });
  // {bbox-epsg-3857} får inte kodas om.
  return `/api/kartverktyg/wms/bild?${q.toString()}&bbox={bbox-epsg-3857}`;
}

/** En kakelmall måste innehålla {z}, {x} och {y} och börja med http(s). */
export function giltigKakelmall(mall: string): boolean {
  return /^https?:\/\//.test(mall) && mall.includes("{z}") && mall.includes("{x}") && mall.includes("{y}");
}

/** Värd för tillskrivningen. */
export function vardAv(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

const UNDERLAG_NYCKEL = "floden.kartunderlag.v1.";

/** Sparade underlag per projekt (try/catch: går det inte att läsa är listan tom). */
export function lasUnderlag(projekt: string, lagring: { getItem(k: string): string | null } | null = lagringAv()): Underlag[] {
  try {
    const t = lagring?.getItem(UNDERLAG_NYCKEL + projekt);
    const d = t ? (JSON.parse(t) as Underlag[]) : [];
    return Array.isArray(d) ? d.filter((u) => u && typeof u.id === "string" && typeof u.url === "string") : [];
  } catch {
    return [];
  }
}

export function sparaUnderlag(projekt: string, lista: readonly Underlag[], lagring: { setItem(k: string, v: string): void } | null = lagringAv()): void {
  try {
    lagring?.setItem(UNDERLAG_NYCKEL + projekt, JSON.stringify(lista));
  } catch {
    /* Det går inte att spara; underlaget finns kvar tills sidan laddas om. */
  }
}

function lagringAv(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}
