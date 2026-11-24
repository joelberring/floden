/*
 * Lager som kartan ritar: id:n för bakgrundslagren och omvandlingen från ett lager i `LagerLista`
 * (bild med utbredning, eller WMS eller kakel från en annan tjänst) till ett rasterlager för
 * kartmotorn. Ren kod utan beroende på kartbiblioteket, så att den går att testa.
 */
import type { LagerDef } from "../karta/lager.ts";
import type { RasterLager } from "../karta/kartmotor.ts";
import { wmsBrickmall, type Underlag } from "../karta/wms.ts";

export const ORTO_ID = "underlag:orto";
export const SKUGGNING_ID = "geo:skuggning";
export const VEGETATION_ID = "geo:vegetation";
export const BYGGNADER_ID = "geo:byggnader";
export const PLAN_ID = "plan:aktuellt";

/** Ett lager ur listan som kartmotorn kan rita (bild eller kakel); null om det saknar data. */
export function rasterAv(l: Pick<LagerDef, "id" | "synlig" | "opacitet" | "data">, synlig?: boolean): RasterLager | null {
  const d = l.data;
  if (!d) return null;
  if (d.underlag) {
    const u = d.underlag as Underlag;
    const mall = u.typ === "wms" ? wmsBrickmall(u) : u.url;
    return { id: l.id, opacitet: l.opacitet, synlig: synlig ?? l.synlig, kalla: { slag: "kakel", mall, kalla: u.kalla } };
  }
  if (typeof d.url === "string" && d.utbredning) {
    return { id: l.id, opacitet: l.opacitet, synlig: synlig ?? l.synlig, kalla: { slag: "bild", url: d.url, utbredning: d.utbredning as never, skarpa: Boolean(d.skarpa) } };
  }
  return null;
}
