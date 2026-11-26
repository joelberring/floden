/*
 * Snabbväljare för grundkarta direkt på kartan: Ren (svartvit arkitektritning utan rasterunderlag),
 * OpenStreetMap, Ortofoto, Terräng (skuggning) eller Höjdkurvor, samt ett gemensamt opacitetsreglage.
 * Ren, DOM-fri modul som enbart rör grundkartslagren i `LagerLista` (aldrig analys-, byggnads- eller planlager).
 */
import type { LagerLista } from "../karta/lager.ts";
import { OSM_ID } from "../karta/osm.ts";
import { esc } from "../sidor.ts";
import { formatera } from "../tal.ts";
import { HOJDKURVOR_ID, MARKHOJD_ID, ORTO_ID, SKUGGNING_ID } from "./rasterlager.ts";

export type GrundkartaVal = "ren" | "osm" | "ortofoto" | "skuggning" | "hojdkurvor";

const ORDNING: readonly GrundkartaVal[] = ["ren", "osm", "ortofoto", "skuggning", "hojdkurvor"];

const ETIKETT: Record<GrundkartaVal, string> = {
  ren: "Ren",
  osm: "OSM",
  ortofoto: "Orto",
  skuggning: "Terräng",
  hojdkurvor: "Höjdkurvor",
};

const LAGER_ID_FOR_VAL: Record<Exclude<GrundkartaVal, "ren">, string> = {
  osm: OSM_ID,
  ortofoto: ORTO_ID,
  skuggning: SKUGGNING_ID,
  hojdkurvor: HOJDKURVOR_ID,
};

/** Alla rasterunderlag som styrs av grundkarteväljaren (aldrig analys-, byggnads- eller ritlager). */
const GRUNDKARTA_LAGER_IDS: readonly string[] = [OSM_ID, ORTO_ID, SKUGGNING_ID, HOJDKURVOR_ID, MARKHOJD_ID];

function finnsTillgangligt(lagerLista: LagerLista, id: string): boolean {
  const l = lagerLista.hamta(id);
  return Boolean(l && !l.ejTillgangligt);
}

/**
 * Vilka grundkartor som finns tillgängliga för projektet.
 * `"ren"` och `"osm"` finns alltid; övriga när motsvarande lager finns och är tillgängligt i `lagerLista`.
 */
export function tillgangligaGrundkartor(lagerLista: LagerLista): GrundkartaVal[] {
  const ut: GrundkartaVal[] = ["ren", "osm"];
  if (finnsTillgangligt(lagerLista, ORTO_ID)) ut.push("ortofoto");
  if (finnsTillgangligt(lagerLista, SKUGGNING_ID)) ut.push("skuggning");
  if (finnsTillgangligt(lagerLista, HOJDKURVOR_ID)) ut.push("hojdkurvor");
  return ut;
}

/** Läser av vilken grundkarta (och opacitet) som är aktiv i `lagerLista` just nu. */
export function aktuellGrundkarta(lagerLista: LagerLista): { val: GrundkartaVal; opacitet: number } {
  for (const val of ["ortofoto", "skuggning", "hojdkurvor", "osm"] as const) {
    const l = lagerLista.hamta(LAGER_ID_FOR_VAL[val]);
    if (l && !l.ejTillgangligt && l.synlig) {
      return { val, opacitet: l.opacitet };
    }
  }
  return { val: "ren", opacitet: 1 };
}

/**
 * Växlar grundkarta rent:
 * - `"ren"` släcker alla grundkartslager och anropar `osmSetter(false)`.
 * - Övriga tänder vald grundkarta med angiven `opacitet` (0–1) och släcker konkurrerande grundkartor.
 * Rör aldrig analyslager, byggnadslager eller förslagslager.
 */
export function tillampaGrundkarta(
  val: GrundkartaVal,
  opacitet: number,
  lagerLista: LagerLista,
  osmSetter?: (pa: boolean) => void,
): void {
  const klampad = Number.isFinite(opacitet) ? Math.min(1, Math.max(0, opacitet)) : 1;
  const aktivtId = val === "ren" ? null : LAGER_ID_FOR_VAL[val];

  for (const id of GRUNDKARTA_LAGER_IDS) {
    const l = lagerLista.hamta(id);
    if (!l || l.ejTillgangligt) continue;
    if (id === aktivtId) {
      lagerLista.sattSynlig(id, true);
      lagerLista.sattOpacitet(id, klampad);
    } else if (l.synlig) {
      lagerLista.sattSynlig(id, false);
    }
  }

  osmSetter?.(val === "osm");
}

/**
 * Kompakt segmenterad knapprad `[Ren | OSM | Orto | Terräng | Höjdkurvor]` + opacitetsslider (`0–100 %`)
 * när `val !== "ren"`, samt hopfällbart läge (`oppen = false`) som en liten prydlig knapp.
 * Stramt svartvitt via `var(--f-*)`-tokens.
 */
export function grundkartvalHtml(
  val: GrundkartaVal,
  opacitet: number,
  tillgangliga: readonly GrundkartaVal[],
  oppen = true,
): string {
  const klampad = Number.isFinite(opacitet) ? Math.min(1, Math.max(0, opacitet)) : 1;
  const procent = Math.round(klampad * 100);

  if (!oppen) {
    const sammanfattning = val === "ren" ? "Ren karta ▾" : `${ETIKETT[val]} · ${formatera(procent, 0)}\u202F% ▾`;
    return `<div class="gk-valjare gk-hopfalld" role="group" aria-label="Grundkarta" style="background:var(--f-yta, #fff);color:var(--f-text, #111);border:1px solid var(--f-linje, #d8d8d8)"><button type="button" class="gk-oppna" data-grundkarta-vaxla aria-expanded="false" aria-label="Visa grundkarteväljare">${esc(sammanfattning)}</button></div>`;
  }

  const tillatna = new Set(tillgangliga);

  const knappar = ORDNING.filter((k) => tillatna.has(k))
    .map((k) => {
      const aktiv = k === val;
      return `<button type="button" class="gk-knapp${aktiv ? " aktiv" : ""}" data-grundkarta="${k}" aria-pressed="${aktiv ? "true" : "false"}">${esc(ETIKETT[k])}</button>`;
    })
    .join("");

  const minimera = `<button type="button" class="gk-minimera" data-grundkarta-vaxla aria-expanded="true" aria-label="Minimera grundkarteväljare" title="Minimera grundkarteväljare">−</button>`;

  const slider =
    val !== "ren"
      ? `<label class="gk-opacitet"><span>Opacitet</span><input type="range" data-grundkarta-opacitet min="0" max="100" step="5" value="${procent}" aria-label="Grundkartans opacitet" /><span class="gk-procent">${esc(formatera(procent, 0))}\u202F%</span></label>`
      : "";

  return `<div class="gk-valjare" role="group" aria-label="Grundkarta" style="background:var(--f-yta, #fff);color:var(--f-text, #111);border:1px solid var(--f-linje, #d8d8d8)"><div class="gk-knappar">${knappar}${minimera}</div>${slider}</div>`;
}
