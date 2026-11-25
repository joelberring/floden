/*
 * Luft-flik i steg 4 · Resultat (PLAN.md Uppdrag 3).
 * Visas bara när projektet har luftkörningar. Hanterar kartlagren vindkomfort, vindfara,
 * vindförstärkning och årsmedel samt MKN-intervall som tal, med tydlig skiss-märkning.
 * Inga tal räknas fram här: alla siffror kommer ur körningens tabeller eller MKN-svaret.
 */
import type { LagerPost, MatvardeTabell } from "../fragor-typer.ts";
import { esc } from "../sidor.ts";
import type { ProjektVy } from "../types.ts";
import { lagerVal, type LagerVal } from "./analyslager.ts";
import { formateraMknIntervall, tal, type MknIntervallSvar, type Tal } from "./nyckeltal.ts";
import { talListaHtml } from "./panelhtml.ts";

export const LUFT_AMNE = "luft" as const;
export const LUFT_FLIK_NAMN = "Luft";
export const LUFT_SKISS_NOTIS =
  "Metodstatus: skiss. Vind- och haltberäkningen är inte validerad mot vindtunnel eller mätstationer och ska användas som tidigt planeringsunderlag.";

export interface LuftRollMeta {
  etikett: string;
  enhet: string;
  ordning: number;
}

/** De fyra primära luftlagren med enhet och visningsordning. */
export const LUFT_ROLLER: Record<string, LuftRollMeta> = {
  komfortklass: { etikett: "Vindkomfort (klass)", enhet: "klass", ordning: 0 },
  faraklass: { etikett: "Vindfara (klass)", enhet: "klass", ordning: 1 },
  forstarkning: { etikett: "Vindförstärkning på gånghöjd", enhet: "gånger", ordning: 2 },
  arsmedel: { etikett: "Årsmedelhalt", enhet: "µg/m³", ordning: 3 },
};

/** Sant bara när minst en vy hör till ämnet luft (demokvarteret saknar luftkörningar). */
export function harLuftKorningar(vyer: readonly Pick<ProjektVy, "avsnitt">[]): boolean {
  return vyer.some((v) => v.avsnitt === LUFT_AMNE);
}

/** Flikknappen för Luft i steg 4 (dold när projektet saknar luftkörningar). */
export function luftFlikKnappHtml(synlig: boolean, aktiv: boolean): string {
  return `<button type="button" role="tab" data-amne="${LUFT_AMNE}" aria-selected="${aktiv}"${synlig ? "" : " hidden"}>${LUFT_FLIK_NAMN}</button>`;
}

/** Luftens kartlager i körningen, med garanterad enhet och fast ordning (komfort, fara, förstärkning, årsmedel). */
export function luftLagerVal(lager: readonly LagerPost[], vy: Pick<ProjektVy, "fore_lager" | "efter_lager">): LagerVal[] {
  return lagerVal(lager, vy, LUFT_AMNE)
    .map((v) => {
      const meta = LUFT_ROLLER[v.roll];
      if (!meta) return v;
      return {
        ...v,
        etikett: v.etikett && v.etikett !== v.roll ? v.etikett : meta.etikett,
        enhet: v.enhet || meta.enhet,
      };
    })
    .sort((a, b) => {
      const oa = LUFT_ROLLER[a.roll]?.ordning ?? 99;
      const ob = LUFT_ROLLER[b.roll]?.ordning ?? 99;
      return Number(a.kontroll) - Number(b.kontroll) || oa - ob || a.etikett.localeCompare(b.etikett, "sv");
    });
}

const num = (o: Record<string, unknown> | undefined, ...nycklar: string[]): number | null => {
  if (!o) return null;
  for (const k of nycklar) {
    const v = o[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return null;
};

/** Nyckeltal ur luftkörningens mätvärdestabeller och eventuellt MKN-intervall. Saknade tal blir "–". */
export function luftNyckeltal(
  tabeller: readonly MatvardeTabell[],
  foreScen: string | undefined,
  efterScen: string | undefined,
  mkn?: MknIntervallSvar | null,
): Tal[] {
  const luftTab = tabeller.filter((t) => t.losare.startsWith("luft.") || t.analys.toLowerCase().includes("luft") || t.analys.toLowerCase().includes("vind"));
  const vardenFor = (scen: string | undefined): Record<string, unknown> | undefined => {
    if (!scen) return undefined;
    const samman: Record<string, unknown> = {};
    let hittad = false;
    for (const tab of luftTab) {
      const rad = tab.rader.find((r) => r.scenario === scen);
      if (rad) {
        Object.assign(samman, rad.varden);
        hittad = true;
      }
    }
    return hittad ? samman : undefined;
  };
  const vf = vardenFor(foreScen);
  const ve = vardenFor(efterScen ?? foreScen);
  const ut: Tal[] = [
    {
      etikett: "Max vindförstärkning före",
      varde: tal(num(vf, "max_forstarkning", "forstarkning_max"), 2, "gånger"),
    },
    {
      etikett: "Max vindförstärkning efter",
      varde: tal(num(ve, "max_forstarkning", "forstarkning_max"), 2, "gånger"),
    },
    {
      etikett: "Årsmedelhalt max",
      varde: tal(num(ve, "max_arsmedel_ug_m3", "arsmedel_max_ug_m3", "arsmedel_ug_m3"), 1, "µg/m³"),
    },
    {
      etikett: "Andel yta med vindfara",
      varde: tal(num(ve, "andel_fara_procent", "vindfara_procent"), 1, "%"),
    },
  ];
  if (mkn) {
    ut.push(...formateraMknIntervall(mkn).tal);
  }
  return ut;
}

export interface LuftPanelIndata {
  korningId: string;
  roller: readonly LagerVal[];
  tal: readonly Tal[];
  mkn?: MknIntervallSvar | null;
  las?: string;
  lageIdag?: boolean;
}

/** Luft-flikens panelinnehåll i steg 4, med skiss-märkning, lagerenheter och nyckeltal. */
export function luftPanelHtml(indata: LuftPanelIndata): string {
  const rubrik = indata.lageIdag ? "Läget idag: luft och vind (skiss)" : "Luft och vind på gånghöjd (skiss)";
  const mknStatus = indata.mkn ? `<p class="flode-ruta">${esc(formateraMknIntervall(indata.mkn).status)}</p>` : "";
  const lagerLista =
    indata.roller.length > 0
      ? `<ul class="flode-liten flode-dampad" id="rs-luft-lager">${indata.roller
          .map((l) => `<li>${esc(l.etikett)}${l.enhet ? ` (${esc(l.enhet)})` : ""}</li>`)
          .join("")}</ul>`
      : "";
  return `
    <p class="flode-liten"><span class="flode-skiss-marke">Skiss</span> ${esc(LUFT_SKISS_NOTIS)}</p>
    <h1 class="flode-slutsats" id="rs-rubrik">${esc(rubrik)}</h1>
    ${talListaHtml(indata.tal)}
    ${mknStatus}
    ${lagerLista}
    <p class="flode-liten flode-dampad">Alla tal kommer ur körningen ${esc(indata.korningId)}.</p>
    ${indata.las ? `<details class="flode-detalj"><summary>Så läser du kartan</summary><p class="flode-liten flode-dampad">${esc(indata.las)}</p></details>` : ""}`;
}
