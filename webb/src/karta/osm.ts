/*
 * OpenStreetMap som valfri bakgrundskarta (rasterplattor från tile.openstreetmap.org).
 *
 * Följer OSM:s tile usage policy (https://operations.osmfoundation.org/policies/tiles/):
 *  - normal användning: plattorna hämtas bara för det som syns i kartfönstret (MapLibre), ingen förhämtning av områden,
 *    inget hämtas alls när bakgrunden är avstängd (lagret och källan tas bort ur kartan, se `endastSynlig`);
 *  - webbläsarens egen cache används och dess cache-huvuden respekteras (inget `no-cache`, ingen egen kopia);
 *  - en tydlig identifiering: en webbläsare kan inte sätta User-Agent, men skickar `Referer` med sidans ursprung (servern ger
 *    `Referrer-Policy: strict-origin-when-cross-origin`, se `src/webb/api.py` och `deploy/Caddyfile`);
 *  - synlig attribution "© OpenStreetMap-bidragsgivare" med länk till upphovsrättssidan, så länge plattorna visas;
 *  - zoom högst 19 (MapLibre förstorar plattorna därutöver).
 * Bakgrunden är förvald bara där det inte finns något annat som visar marken (nytt projekt, projekt i en geografi);
 * valet kan stängas av och sparas i webbläsaren. Servern behöver tillåta `https://tile.openstreetmap.org` för bilder och anrop
 * i Content-Security-Policy (`deploy/Caddyfile`). Inget i den här filen beror på kartbiblioteket.
 */
import type { Lagring } from "./lager.ts";

export const OSM_ID = "underlag:osm";
export const OSM_MALL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const OSM_MAXZOOM = 19;
export const OSM_VARD = "https://tile.openstreetmap.org";
export const OSM_UPPHOVSRATT = "https://www.openstreetmap.org/copyright";
export const OSM_ATTRIBUTION = `© <a href="${OSM_UPPHOVSRATT}" target="_blank" rel="noopener noreferrer">OpenStreetMap-bidragsgivare</a>`;
export const OSM_ATTRIBUTION_TEXT = "© OpenStreetMap-bidragsgivare";

/** Rasterkälla för kartmotorn (kakelmall, högst zoom 19, hämtas bara när lagret är tänt). */
export const OSM_KALLA = {
  slag: "kakel" as const,
  mall: OSM_MALL,
  kalla: OSM_ATTRIBUTION,
  storlek: 256,
  maxzoom: OSM_MAXZOOM,
  endastSynlig: true,
};

const NYCKEL = "floden.osm.bakgrund.v1";

/** Det sparade valet för sidor utan egen lagerlista (Nytt projekt); `null` när inget valts. */
export function lasOsmVal(lagring: Lagring | null): boolean | null {
  try {
    const v = lagring?.getItem(NYCKEL);
    return v === "pa" ? true : v === "av" ? false : null;
  } catch {
    return null;
  }
}

export function sparaOsmVal(lagring: Lagring | null, pa: boolean): void {
  try {
    lagring?.setItem(NYCKEL, pa ? "pa" : "av");
  } catch {
    /* Går det inte att spara fungerar kartan ändå. */
  }
}

export function webbLagringOsm(): Lagring | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}
