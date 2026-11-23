/*
 * Känsliga överfall som kartlager: punkterna ur `POST /api/analys/bifurkationer` (SWEREF 99 18 00,
 * EPSG:3011) görs om till GeoJSON för kartmotorn. Omräkningen sker med den exakta `sweref.ts`
 * (Gauss-Krüger, kontrollerad mot pyproj); ingen egen projektion finns här. Texten för en punkt är
 * densamma i panelen "Känsliga överfall & 2D-kontroll" och i kartans ruta vid klick eller hovring.
 * Modulen är ren (inga beroenden på kartbiblioteket eller sidan) och testas i overfall.test.ts.
 */
import { swerefTillWgs84 } from "../sweref.ts";
import type { LagerDef } from "./lager.ts";

/** De fält ur en bifurkationspunkt som visas. */
export interface OverfallPunkt {
  nod_id: number;
  overfall_x: number;
  overfall_y: number;
  marginal_m: number;
  paverkad_volym_m3: number;
  instabil: boolean;
}

export const OVERFALL_LAGER_ID = "ana:overfall";
export const MAT_I_FALT = "mät in i fält";

/** Samma rad som panelen listar: nod, läge i SWEREF, marginal och berörd volym. */
export function overfallBeskrivning(p: OverfallPunkt): string {
  return `Nod ${p.nod_id} (E ${Math.round(p.overfall_x)}, N ${Math.round(p.overfall_y)}): marginal ${p.marginal_m.toFixed(3)} m, berör ${Math.round(p.paverkad_volym_m3)} m³`;
}

/** Punkterna som ritas: de instabila med ändliga koordinater. */
export function ritadeOverfall<T extends OverfallPunkt>(punkter: readonly T[]): T[] {
  return punkter.filter((p) => p.instabil && Number.isFinite(p.overfall_x) && Number.isFinite(p.overfall_y));
}

export function overfallTillGeoJson(punkter: readonly OverfallPunkt[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: ritadeOverfall(punkter).map((p) => ({
      type: "Feature",
      properties: { nod_id: p.nod_id, beskrivning: overfallBeskrivning(p), marginal_m: p.marginal_m, volym_m3: p.paverkad_volym_m3 },
      geometry: { type: "Point", coordinates: swerefTillWgs84(p.overfall_x, p.overfall_y) },
    })),
  };
}

/** Lagret i lagerpanelet (gruppen Resultat, bara 2D). Id:t börjar inte på `res:`, så nya beräkningsresultat rör det inte. */
export function overfallLager(antal: number): LagerDef {
  return {
    id: OVERFALL_LAGER_ID,
    grupp: "resultat",
    namn: "Känsliga överfall",
    synlig: true,
    opacitet: 1,
    hjalp: `${antal} överfallspunkter där rinnvägen kan ändras inom höjdosäkerheten (${MAT_I_FALT}). Klicka eller håll över en punkt för värden.`,
    visning: "2d",
  };
}
