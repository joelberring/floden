/*
 * Steg 2 · Karta & analys: den sammanhållna arbetsytan som förenar Nuläge, Redigera förslag,
 * Efter, Skillnad, Svep och 3D på samma karta.
 *
 * Principer:
 * - Nuläge först: vattendjupet visas direkt när projektet öppnas (eller en tydlig knapp "Beräkna nuläge").
 * - Vatten under medan man ritar: halvtransparent nulägesdjup + valfria flödesvägar under ritlagren.
 * - 6 självförklarande ritverktyg (Flytta/markera, Nytt hus, Höj/sänk mark, Mur/vall, Öppning/dike, Ta bort)
 *   + snabbknappar för markhöjning/sänkning (-1,0 till +1,0 m) och hopfällbar "Fler ▾".
 * - Regnväljare + "Beräkna nuläge" / "Beräkna förslag" direkt i panelen utan hopp till Frågor-sidan.
 * - Permanent grafisk skalstock, mätverktyg ("Mät bredd") i alla kartlägen, snabbknappar för vattenlager
 *   och pekavläsning med vattendjup i cm (och m) tillsammans med markhöjd.
 */
import { hamtaAvlasning, hamtaEkologiAnalys, hamtaMarkhojd, hamtaProjekt, hamtaResultat, lasJson, type EkologiAnalysFraga } from "../api.ts";
import { ApiFel } from "../apifel.ts";
import type { AvlasningSvar, LagerPost, ResultatSvar } from "../fragor-typer.ts";
import { andraFalt, etikettFor, faltFor, hojdlage, lutningFranHornhojder, tolkaTal, type Falt } from "../karta/egenskaper.ts";
import { fordrojd } from "../karta/fordrojning.ts";
import { infogaPunkter } from "../karta/formedit.ts";
import { btaUppskattning, yta, type Punkt } from "../karta/geom.ts";
import {
  bytScenarioNamn,
  foreslaSkarvatgard,
  hamtaBifurkationer,
  hamtaProjektInfo,
  hamtaScenario,
  hamtaSektion,
  hamtaSvfIntervall,
  hamtaTidsserie,
  hamtaVattenIntervall,
  kontrolleraAndringar,
  lageIdagStatus,
  listaScenarier,
  scenarioStatus,
  sparaScenario,
  taBortScenario,
  type BifurkationsPunktPost,
  type Kontroll,
  type ProjektInfo,
  type ScenarioPost,
  type Utbredning,
} from "../karta/kartapi.ts";
import { arGrovPekare, Kartmotor, TOM_RITVY, type AnalysVektor, type RasterLager, type RitVy, type VyByggnad } from "../karta/kartmotor.ts";
import { LagerLista } from "../karta/lager.ts";
import { monteraLagerpanel } from "../karta/lagerpanel.ts";
import { langd } from "../karta/linje.ts";
import { tolkaLangdOchBaring, tolkaSwerefKoordinat } from "../karta/linjeverktyg.ts";
import { OVERFALL_LAGER_ID, overfallLager, ritadeOverfall } from "../karta/overfall.ts";
import { OSM_ID } from "../karta/osm.ts";
import { PlanDok } from "../karta/plandok.ts";
import { nyttLager, type Roll, type RollInfo } from "../karta/ritlager.ts";
import { monteraRitlagerpanel } from "../karta/ritlagerpanel.ts";
import { FORVALD_HOJD_M, Ritverktyg, TANGENTER, type Meddelande, type Verktyg } from "../karta/ritverktyg.ts";
import { fyndUtbredning, skarvFigurer, skarvPanelHtml, tillampaAtgard, utfallText, type AtgardSvar, type Skarvar } from "../karta/skarv.ts";
import { bindningAv, KOMMANDON, LAGRINGSNYCKEL, lasTangenter, medBindning, sparaTangenter, tangentEtikett, type Kommando } from "../karta/tangenter.ts";
import { ARBETSLAGEN, lasKartan, type ArbetsLage, type Lage } from "../kartforklaring.ts";
import { fasadForklaringHtml, skapa3dVy, VY3D_ID } from "../kartsida3d.ts";
import { kontoLage } from "../konto.ts";
import { esc } from "../sidor.ts";
import { swerefTillWgs84, wgs84TillSweref } from "../sweref.ts";
import { formatera, formateraHogst, talFormat } from "../tal.ts";
import type { ProjektSvar, ProjektVy } from "../types.ts";
import { meterPerPxFranZoom, zoomFranMeterPerPx } from "../vy3d/koord.ts";
import { flodeAdress } from "./adress.ts";
import {
  forvaldNyckel,
  GRANSER,
  gransPos,
  gransVarde,
  lagerVal,
  sankaKortHtml,
  sankaUrl,
  valText,
  vektorScenario,
  vektorTeckenHtml,
  vektorTillKarta,
  vektorUrl,
  vektorVal,
  vyMedVal,
  type LagerVal,
  type SankaSvar,
  type VektorSlag,
  type VektorSvar,
} from "./analyslager.ts";
import {
  berakningsomfangHtml,
  type BerakningsomfangInfo,
} from "./berakningsomfang.ts";
import {
  bullerLagerTeckenHtml,
  kopplaBullerPanel,
  type BullerKontroller,
  type BullerLagerRef,
} from "./bullerflik.ts";
import {
  kopplaCadBimPanel,
  laggInCadImportIDok,
  type CadBimKontroller,
} from "./cadbim.ts";
import {
  forvaldGyfProfilForKommun,
  kopplaGyfUtciPaneler,
  varmelagerTeckenHtml,
  type GyfUtciKontroller,
  type VarmestressLagerRef,
} from "./gyf-utci.ts";
import { felBeskedHtml, kopplaLaddaOm, sidFel } from "./felhandling.ts";
import { tolkaFil } from "./filimport.ts";
import { beraknaMarkvolym, forslagInfoHtml, markvolymText, sparStatus, type HusUppgift } from "./forslagtext.ts";
import {
  aktuellGrundkarta,
  grundkartvalHtml,
  tillampaGrundkarta,
  tillgangligaGrundkartor,
  type GrundkartaVal,
} from "./grundkartval.ts";
import { BasLager, byggnaderMedForslag, skapaKarta } from "./kartdel.ts";
import { arPagaende, senasteKlaraAvsnitt, startaKorning, startaLageIdag, vidKorning } from "./korning.ts";
import { LUFT_AMNE, luftLagerVal, luftNyckeltal, luftPanelHtml, luftTomPanelHtml } from "./luftflik.ts";
import { BYGGNADER_ID, HOJDKURVOR_ID } from "./rasterlager.ts";
import { markhojdAvlasning } from "./markhojd.ts";
import { formateraDjupCm, kortMarkhojdText, MatVerktyg, skalstockHtml, skalstockMatt } from "./matverktyg.ts";
import {
  antagandeRader,
  dpcTabellRader,
  flaskhalsPunkter,
  intervallForklaring,
  jamforelse,
  kallRad,
  nyckeltal,
  regnText,
  resultatEtikett,
  slutsats,
  type Amne,
  type EkologiSvar,
  type Marksiffror,
  type Underlag,
} from "./nyckeltal.ts";
import { osakerhetTal, overfallRader, type OsakerhetSvar } from "./osakerhet.ts";
import { cadMattHtml, kroppHtml, ordlistaHtml, osakerhetHtml, talListaHtml, type CadInmatningsLage, type OsakerhetVisning } from "./panelhtml.ts";
import { projektKallaText } from "./projektkalla.ts";
import type { Avslutbar } from "./projektlista.ts";
import {
  medRegnForval,
  regnEtikett,
  regnValHtml,
  tolkaRegnTal,
  valdRegnText,
  valideraRegnVal,
  type Berakningstyp,
  type EgetRegnLage,
  type RegnForvalId,
  type RegnTyp,
} from "./regnval.ts";
import { teckenHtml } from "./resultat.ts";
import {
  avlasningVidIndex,
  indexFranSvgX,
  ritaSektionSvg,
  sektionTillCsv,
  sektionVattenLager,
  SektionVerktyg,
  type SektionSvar,
} from "./sektion.ts";
import {
  formateraKlockslagUtc,
  solValHtml,
  STANDARD_KLOCKSLAG_UTC,
  valideraSolVal,
  type SolBrukstid,
  type SolDatumForval,
  type SolLage,
} from "./solval.ts";
import {
  hoppaTillTopp,
  narmasteStegForTid,
  nastaSteg,
  skapaTidsspelareTillstand,
  tidMinFranSvgX,
  tidsspelareHtml,
  tillMaxdjup,
  type TidsserieSvar,
  type TidsspelareTillstand,
} from "./tidsspelare.ts";
import {
  aterladdaSparadeVyer,
  bedomInstallningsStatus,
  forslagKropp,
  FORVALD_TAKFORM,
  kanStartaKorning,
  lasArbetsytaUiLage,
  lasAvtappning,
  skapaDokFor,
  sparaArbetsytaUiLage,
  sparaTillstand,
  takformFran,
  TAKFORMER,
  tillstandFor,
} from "./tillstand.ts";
import { monteraUnderlagForm, taBortUnderlag } from "./underlag.ts";

interface MarkSvar {
  lager_id: string;
  troskel: number;
  siffror: Marksiffror | null;
}

interface EkologiLagerRef {
  lager_id?: string;
  png?: string;
  utbredning?: Utbredning;
}

type EkologiMedLager = EkologiSvar & {
  lager?: {
    stromtathet_fore?: EkologiLagerRef | null;
    stromtathet_efter?: EkologiLagerRef | null;
    stromtathet_skillnad?: EkologiLagerRef | null;
    kansighet_fore?: EkologiLagerRef | null;
    kansighet_efter?: EkologiLagerRef | null;
    kansighet_skillnad?: EkologiLagerRef | null;
  };
};

type Kalla = "ritat" | "projekt" | "lage";
export type PlaneringsDoman = Amne | typeof LUFT_AMNE | "mikroklimat" | "buller";
type ResultatAmne = PlaneringsDoman;
type HuvudVerktyg = "valj" | "hus" | "mark" | "mur" | "oppning" | "friyta";
type FriYtaRoll = "hardgjort" | "vegetation" | "vatten";

const TAL0 = talFormat(0);
const TAL1 = talFormat(1);
const TAL2 = talFormat(2);
export const AMNEN_ORDNING: readonly PlaneringsDoman[] = [
  "vatten",
  "dagsljus",
  "mikroklimat",
  "ekologi",
  "luft",
  "buller",
];
export const AMNESNAMN: Record<PlaneringsDoman, string> = {
  vatten: "Skyfall",
  dagsljus: "Sol & Dagsljus",
  mikroklimat: "Mikroklimat",
  ekologi: "Ekologi & GYF",
  luft: "Vind & Luft",
  buller: "Buller",
};
const KOR_AMNESNAMN: Record<PlaneringsDoman, string> = {
  vatten: "skyfall",
  dagsljus: "dagsljus",
  mikroklimat: "mikroklimat",
  ekologi: "ekologi",
  luft: "luft",
  buller: "buller",
};
export const EKOLOGI_UTAN_NMD_TEXT =
  "Geografin saknar NMD-underlag för konnektivitet — du kan ändå beräkna Grönytefaktor (GYF) här, eller mappa om geografin med NMD.";
export const RASTER_OPACITET_MIN = 0.2;
export const RASTER_OPACITET_MAX = 1.0;
export const RASTER_OPACITET_FORVAL = 0.85;

/** Klämmer opacitet för aktivt analysraster till [0,20; 1,00]. */
export function klammaRasterOpacitet(v: number): number {
  if (!Number.isFinite(v)) return RASTER_OPACITET_FORVAL;
  return Math.min(RASTER_OPACITET_MAX, Math.max(RASTER_OPACITET_MIN, v));
}

const MARK_SNABB_M = [-1.0, -0.5, -0.2, 0.2, 0.5, 1.0] as const;

/** Knapp-id för "Visa osäkerhet" (måste matcha `osakerhetHtml` i `panelhtml.ts`, W-B4). */
export const OSAKERHET_KNAPP_ID = "rs-osakerhet";

/** Delegerad klickselektor i arbetsytan (Steg 2). */
export const ARBETSYTA_KLICK_SELEKTOR =
  "[data-lage],[data-amne],[data-kalla],[data-vy],[data-lager-val],[data-eko-lager],[data-vektor-knapp],[data-regn-forval],[data-regn-eget],[data-skyfall-metod],[data-regn-berakningstyp],[data-regn-regntyp],[data-sol-lage],[data-sol-datum],[data-sol-brukstid],[data-grundkarta],[data-grundkarta-vaxla],[data-dock-flik],[data-sektion-handling],[data-tid-handling],[data-hv],[data-friyta-roll],[data-v],[data-mark-delta],[data-cad-lage],#rs-osakerhet,#ar-kor-nulage,#ar-tom-kor-nulage,#ar-kor-forslag,#ar-kor-alla,#ar-rita-forslag,#ar-skikt-vaxla,#ar-tecken-vaxla,#ar-sanka-stang,#ar-panel-vaxla,#ar-mat-knapp,#ar-sektion-knapp,#ar-las-centrum,#ar-redigera-3d-knapp,#ar-redigera-3d-stang,#ar-riv-knapp,#ar-in,#ar-ut,#ar-anpassa,#ar-spara";

/** Mappar ett analys-id (`"vatten.skyfall"`, `"dagsljus.sol"`, …) eller mallnamn till arbetsytans ämne. */
export function amneForAnalysId(analysId: string, mall?: string | null): ResultatAmne | null {
  const m = (mall ?? (analysId.includes(".") ? analysId.slice(0, analysId.indexOf(".")) : analysId)).trim().toLowerCase();
  if (m === "vatten" || m === "skyfall") return "vatten";
  if (m === "dagsljus" || m === "sol") return "dagsljus";
  if (m === "ekologi") return "ekologi";
  if (m === "luft") return "luft";
  return null;
}

/** Sant när projektet har NMD-underlag eller körbar ekologimall (inkl. demokvarteret). */
export function harEkologiUnderlag(
  info: Pick<ProjektInfo, "id" | "har_nmd" | "analyser"> | null | undefined,
): boolean {
  if (!info) return false;
  if (info.har_nmd === true) return true;
  if (info.id === "demokvarteret") return true;
  return Array.isArray(info.analyser) && info.analyser.some((a) => amneForAnalysId(a.id, a.mall) === "ekologi");
}

/** Projektets förvalda körbara analyser (alla ämnen), med fallback till samtliga om inget är markerat `forval`. */
export function forvaldaAnalyserForInfo(info: Pick<ProjektInfo, "analyser"> | null | undefined): string[] {
  if (!info || !Array.isArray(info.analyser)) return [];
  const alla = info.analyser.filter((x) => x.forval).map((x) => x.id);
  return alla.length > 0 ? alla : info.analyser.map((x) => x.id);
}

/** Distinkta ämnen som har minst en körbar analys i projektets `info.analyser`. */
export function korbaraAmnenI(info: Pick<ProjektInfo, "analyser"> | null | undefined): ResultatAmne[] {
  if (!info || !Array.isArray(info.analyser)) return [];
  const hittade = new Set<ResultatAmne>();
  for (const a of info.analyser) {
    const am = amneForAnalysId(a.id, a.mall);
    if (am) hittade.add(am);
  }
  return AMNEN_ORDNING.filter((x) => hittade.has(x));
}

/**
 * Väljer körbara K20-analyser enbart för aktivt ämne (`"vatten"` → skyfall, `"dagsljus"` → dagsljus/sol,
 * `"ekologi"` → ekologi, `"luft"` → luft). Faller ALDRIG tyst tillbaka på Skyfall eller andra domäner
 * när domänen saknar egen körbar mall — returnerar då tom lista `[]`.
 */
export function analyserForAktivtAmne(
  info: Pick<ProjektInfo, "analyser"> | null | undefined,
  amne: ResultatAmne,
): string[] {
  if (!info || !Array.isArray(info.analyser) || info.analyser.length === 0) return [];
  const matchande = info.analyser.filter((a) => amneForAnalysId(a.id, a.mall) === amne);
  if (matchande.length === 0) return [];
  const forvalda = matchande.filter((a) => a.forval).map((a) => a.id);
  return forvalda.length > 0 ? forvalda : matchande.map((a) => a.id);
}

export interface DomanStatusIndata {
  aktivDoman: PlaneringsDoman;
  pagarK20: boolean;
  harVyer: ReadonlySet<string>;
  harNmd: boolean;
  ekologiHarSvar: boolean;
  gyfLaddar: boolean;
  gyfHarSvar: boolean;
  vsLaddar: boolean;
  vsHarSvar: boolean;
  bullerLaddar: boolean;
  bullerHarSvar: boolean;
}

export interface DomanStatusUt {
  kod: "klar" | "kor" | "skiss";
  etikett: string;
  prickKlass: "klar" | "kor" | "skiss" | "skiss-klar" | "redo";
  titel: string;
}

/** Ärlig statusprick och synlig textetikett (`Klar` / `Kör` / `Skiss`) för varje planeringsdomän i vänsterpanelen. */
export function domanStatus(doman: PlaneringsDoman, d: DomanStatusIndata): DomanStatusUt {
  if (doman === "vatten") {
    if (d.pagarK20 && d.aktivDoman === "vatten") {
      return { kod: "kor", etikett: "Kör", prickKlass: "kor", titel: "Skyfallsberäkning pågår" };
    }
    if (d.harVyer.has("vatten")) {
      return { kod: "klar", etikett: "Klar", prickKlass: "klar", titel: "Skyfallsresultat beräknat" };
    }
    return { kod: "kor", etikett: "Kör", prickKlass: "redo", titel: "Ej beräknad ännu — välj Skyfall för att köra" };
  }
  if (doman === "dagsljus") {
    if (d.pagarK20 && d.aktivDoman === "dagsljus") {
      return { kod: "kor", etikett: "Kör", prickKlass: "kor", titel: "Dagsljusberäkning pågår" };
    }
    if (d.harVyer.has("dagsljus")) {
      return { kod: "klar", etikett: "Klar", prickKlass: "klar", titel: "Sol- och dagsljusresultat beräknat" };
    }
    return { kod: "kor", etikett: "Kör", prickKlass: "redo", titel: "Ej beräknad ännu — välj Sol & Dagsljus för att köra" };
  }
  if (doman === "mikroklimat") {
    if (d.vsLaddar) {
      return { kod: "kor", etikett: "Kör", prickKlass: "kor", titel: "Beräknar UTCI och T_mrt …" };
    }
    if (d.vsHarSvar) {
      return { kod: "klar", etikett: "Klar", prickKlass: "klar", titel: "Värmestress (UTCI / T_mrt) beräknad · metodstatus: skiss" };
    }
    return { kod: "skiss", etikett: "Skiss", prickKlass: "skiss", titel: "Mikroklimat (UTCI & T_mrt) · metodstatus: skiss" };
  }
  if (doman === "ekologi") {
    if (d.gyfLaddar || (d.pagarK20 && d.aktivDoman === "ekologi")) {
      return { kod: "kor", etikett: "Kör", prickKlass: "kor", titel: "Beräknar ekologi / GYF …" };
    }
    if (d.harVyer.has("ekologi") || d.ekologiHarSvar || d.gyfHarSvar) {
      return { kod: "klar", etikett: "Klar", prickKlass: "klar", titel: "Ekologi / GYF tillgänglig (skiss)" };
    }
    return {
      kod: "skiss",
      etikett: "Skiss",
      prickKlass: "skiss",
      titel: d.harNmd
        ? "Ekologisk konnektivitet (NMD) och Svensk GYF (skiss)"
        : "Saknar NMD-underlag för konnektivitet — Svensk GYF (skiss) tillgänglig",
    };
  }
  if (doman === "luft") {
    if (d.pagarK20 && d.aktivDoman === "luft") {
      return { kod: "kor", etikett: "Kör", prickKlass: "kor", titel: "Luftberäkning pågår" };
    }
    if (d.harVyer.has("luft")) {
      return { kod: "klar", etikett: "Klar", prickKlass: "klar", titel: "Vind- och luftresultat beräknat (skiss)" };
    }
    return {
      kod: "skiss",
      etikett: "Skiss",
      prickKlass: "skiss",
      titel: "Inga luftkörningar ännu — kräver väder- och trafikunderlag",
    };
  }
  // Buller är alltid SKISS (ej Nord2000-konform, formell bullerrapport spärrad).
  if (d.bullerLaddar) {
    return { kod: "kor", etikett: "Kör", prickKlass: "kor", titel: "Beräknar bullerskiss …" };
  }
  return {
    kod: "skiss",
    etikett: "Skiss",
    prickKlass: d.bullerHarSvar ? "skiss-klar" : "skiss",
    titel: "SKISS — ej Nord2000-konform (formell bullerrapport spärrad)",
  };
}

export interface SkiktPanelIndata {
  aktivDoman: PlaneringsDoman;
  statusIndata: DomanStatusIndata;
  hopfalld: boolean;
  basSynlig: {
    osm: boolean;
    hojdkurvor: boolean;
    byggnader: boolean;
  };
  tillgangligaVektorer: readonly VektorSlag[];
  aktivaVektorer: ReadonlySet<VektorSlag>;
  rasterOpacitet: number;
}

/** Renderar domänflikarna (alla 6 alltid synliga, vertical tablist) för `#ar-skikt-panel`. */
export function skiktDomanerHtml(aktivDoman: PlaneringsDoman, statusIndata: DomanStatusIndata): string {
  return AMNEN_ORDNING.map((id) => {
    const aktiv = id === aktivDoman;
    const st = domanStatus(id, statusIndata);
    return `<button type="button" role="tab" class="ar-skikt-doman" data-amne="${id}" aria-selected="${aktiv}" tabindex="${aktiv ? 0 : -1}" title="${esc(st.titel)}">
      <span class="ar-skikt-doman-namn"><span class="ar-skikt-prick ${st.prickKlass}" aria-hidden="true"></span><span>${esc(AMNESNAMN[id])}</span></span>
      <span class="ar-skikt-status ${st.prickKlass}">${esc(st.etikett)}</span>
    </button>`;
  }).join("");
}

/** Renderar snabbkryssrutorna för kartlager (OSM, Höjdkurvor, Byggnader + ev. Flödesvägar & Sänkor). */
export function skiktLagerHtml(indata: Pick<SkiktPanelIndata, "basSynlig" | "tillgangligaVektorer" | "aktivaVektorer">): string {
  const rader = [
    `<label class="ar-skikt-kryss"><input type="checkbox" data-skikt-bas="${OSM_ID}"${indata.basSynlig.osm ? " checked" : ""} /><span>OpenStreetMap</span></label>`,
    `<label class="ar-skikt-kryss"><input type="checkbox" data-skikt-bas="${HOJDKURVOR_ID}"${indata.basSynlig.hojdkurvor ? " checked" : ""} /><span>Höjdkurvor</span></label>`,
    `<label class="ar-skikt-kryss"><input type="checkbox" data-skikt-bas="${BYGGNADER_ID}"${indata.basSynlig.byggnader ? " checked" : ""} /><span>Byggnader</span></label>`,
  ];
  if (indata.tillgangligaVektorer.includes("flodesvagar")) {
    rader.push(
      `<label class="ar-skikt-kryss"><input type="checkbox" data-skikt-vektor="flodesvagar"${indata.aktivaVektorer.has("flodesvagar") ? " checked" : ""} /><span>Flödesvägar</span></label>`,
    );
  }
  if (indata.tillgangligaVektorer.includes("sankor")) {
    rader.push(
      `<label class="ar-skikt-kryss"><input type="checkbox" data-skikt-vektor="sankor"${indata.aktivaVektorer.has("sankor") ? " checked" : ""} /><span>Sänkor</span></label>`,
    );
  }
  return `<span class="ar-skikt-rubrik">Kartlager</span><div class="ar-skikt-krysslista">${rader.join("")}</div>`;
}

/** Renderar komplett HTML för den vänsterställda 6-domäns skiktpanelen (`#ar-skikt-panel`). */
export function skiktPanelHtml(indata: SkiktPanelIndata): string {
  const opPct = Math.round(klammaRasterOpacitet(indata.rasterOpacitet) * 100);
  const huvud = indata.hopfalld
    ? `<button type="button" class="ar-skikt-oppna" id="ar-skikt-vaxla" aria-expanded="false" aria-controls="ar-skikt-kropp" aria-label="Visa skiktpanel">Skikt · ${esc(AMNESNAMN[indata.aktivDoman])} ▸</button>`
    : `<span class="ar-skikt-titel">Planeringsskikt</span><button type="button" class="ar-min-knapp ar-skikt-vaxla" id="ar-skikt-vaxla" aria-expanded="true" aria-controls="ar-skikt-kropp" aria-label="Fäll ihop skiktpanel" title="Fäll ihop skiktpanel">−</button>`;
  return `<div class="ar-skikt-huvud" id="ar-skikt-huvud">${huvud}</div>
    <div class="ar-skikt-kropp" id="ar-skikt-kropp"${indata.hopfalld ? " hidden" : ""}>
      <div role="tablist" aria-label="Planeringsdomäner" aria-orientation="vertical" class="ar-skikt-domaner" id="ar-flikar">${skiktDomanerHtml(indata.aktivDoman, indata.statusIndata)}</div>
      <div class="ar-skikt-lager" id="ar-skikt-lager">${skiktLagerHtml(indata)}</div>
      <div class="ar-skikt-opacitet-block">
        <label class="ar-skikt-opacitet" for="ar-raster-opacitet">
          <span class="ar-skikt-opacitet-rad"><span>Opacitet</span><output id="ar-raster-opacitet-ut">${opPct}\u202F%</output></span>
          <input type="range" id="ar-raster-opacitet" min="20" max="100" step="5" value="${opPct}" aria-label="Opacitet för aktivt analyslager" />
        </label>
      </div>
    </div>`;
}

/**
 * Ämnesmedveten startspärr: `regnFel` och `avtappningFel` blockerar bara när vatten/skyfall ingår
 * i `analyser`, och `solFel` blockerar bara när dagsljus/sol ingår i `analyser`.
 */
export function kanStartaKorningForAnalyser(
  analyser: readonly string[],
  regnFel: string | null,
  avtappningFel: boolean,
  solFel: string | null,
): boolean {
  if (analyser.length === 0) return false;
  const harVatten = analyser.some((id) => amneForAnalysId(id) === "vatten");
  const harSol = analyser.some((id) => amneForAnalysId(id) === "dagsljus");
  return kanStartaKorning(
    analyser,
    harVatten ? regnFel : null,
    harVatten ? avtappningFel : false,
    harSol ? solFel : null,
  );
}

/** Etiketter för primära körknappar (ämnesspecifika när projektet har > 1 körbart ämne) samt sekundär "alla ämnen"-knapp. */
export function korKnappEtiketterForAmne(
  info: Pick<ProjektInfo, "analyser"> | null | undefined,
  amne: ResultatAmne,
): {
  arFlerAmnen: boolean;
  nulage: string;
  raknaOmNulage: string;
  forslag: string;
  allaNulage: string;
  allaForslag: string;
} {
  const korbara = korbaraAmnenI(info);
  const harEgetAmne = Boolean(info?.analyser?.some((a) => amneForAnalysId(a.id, a.mall) === amne));
  const arFlerAmnen = korbara.length > 1 && harEgetAmne;
  const namn = KOR_AMNESNAMN[amne];
  return {
    arFlerAmnen,
    nulage: arFlerAmnen ? `Beräkna ${namn} (nuläge)` : "Beräkna nuläge",
    raknaOmNulage: arFlerAmnen ? `Räkna om ${namn}` : "Räkna om nuläge",
    forslag: arFlerAmnen ? `Beräkna ${namn} (förslag)` : "Beräkna förslag",
    allaNulage: "Beräkna alla ämnen",
    allaForslag: "Beräkna alla ämnen (förslag)",
  };
}

/**
 * Väljer vilket ämne som ska visas efter avslutad körning (även vid delfel):
 * behåller aktivt ämne om det blev klart eller redan har en vy, annars växlar det
 * till första ämne som blev klart så lyckade delresultat visas direkt på kartan.
 */
export function valjAmneEfterKorning(
  aktuelltAmne: ResultatAmne,
  vyer: readonly Pick<ProjektVy, "avsnitt">[],
  klaraAvsnitt: readonly string[],
): ResultatAmne {
  if (klaraAvsnitt.includes(aktuelltAmne) && vyer.some((v) => v.avsnitt === aktuelltAmne)) {
    return aktuelltAmne;
  }
  if (vyer.some((v) => v.avsnitt === aktuelltAmne)) {
    return aktuelltAmne;
  }
  for (const a of klaraAvsnitt) {
    if ((AMNEN_ORDNING as readonly string[]).includes(a) && vyer.some((v) => v.avsnitt === a)) {
      return a as ResultatAmne;
    }
  }
  return aktuelltAmne;
}

/** Kopplar 3D-vyns hovringsavläsning, objekttext och fel till arbetsytans DOM-uppdateringar (W-B4). */
export function skapa3dAterkoppling(mal: {
  avlasning(e: number | null, n: number | null): void;
  sattText(txt: string | null): void;
  sattFel(txt: string | null): void;
}): {
  avlasning(e: number | null, n: number | null): void;
  text(txt: string | null): void;
  fel(txt: string): void;
} {
  return {
    avlasning: (e, n) => {
      if (e !== null && n !== null) mal.sattText(null);
      mal.avlasning(e, n);
    },
    text: (txt) => {
      mal.sattText(txt && txt.trim() ? txt.trim() : null);
    },
    fel: (txt) => {
      const ren = txt.trim();
      mal.sattFel(ren ? (/^3d/i.test(ren) ? ren : `3D-vy: ${ren}`) : null);
    },
  };
}

/** Uppdaterar `#ar-hjalp` med 3D-fel, verktygshjälp eller mät-/sektionshjälp (W-B4). */
export function renderaHjalpElement(
  h: Pick<HTMLElement, "hidden" | "textContent" | "setAttribute"> & {
    classList: Pick<DOMTokenList, "add" | "remove" | "toggle">;
  },
  indata: {
    treDFel: string | null;
    sektionAktiv: boolean;
    sektionText: string;
    matAktiv: boolean;
    matText: string;
    redigeraAktiv: boolean;
    meddelande: Meddelande | null;
    verktygText: string;
  },
): void {
  if (indata.treDFel) {
    h.hidden = false;
    h.classList.add("fel", "flode-fel");
    h.setAttribute("role", "alert");
    h.textContent = indata.treDFel;
    return;
  }
  h.setAttribute("role", "status");
  if (indata.sektionAktiv) {
    h.hidden = false;
    h.classList.remove("fel", "flode-fel");
    h.textContent = indata.sektionText;
    return;
  }
  if (indata.matAktiv) {
    h.hidden = false;
    h.classList.remove("fel", "flode-fel");
    h.textContent = indata.matText;
    return;
  }
  if (!indata.redigeraAktiv) {
    h.hidden = true;
    h.classList.remove("fel", "flode-fel");
    return;
  }
  const text = indata.meddelande ? indata.meddelande.text : indata.verktygText;
  h.hidden = !text;
  h.textContent = text;
  const arFel = indata.meddelande?.typ === "fel";
  h.classList.toggle("fel", arFel);
  h.classList.toggle("flode-fel", arFel);
  if (arFel) h.setAttribute("role", "alert");
}

/** Domänkorrekt hjälptext i nulägespanelen (aldrig hårdkodat "vattendjup" för sol/ekologi/luft/mikroklimat/buller, W-B8 / U5d). */
export function nulageTipsHtmlForAmne(amne: ResultatAmne): string {
  if (amne === "vatten") {
    return "Håll pekaren över kartan eller tryck <strong>Läs av kartcentrum</strong> (Alt+I) för exakt vattendjup och markhöjd. Använd <strong>Mät bredd</strong> eller <strong>Sektion</strong> för att granska rinnstråk, eller växla till <strong>Redigera förslag</strong> för att rita hus och ändra markhöjd ovanpå vattnet.";
  }
  if (amne === "dagsljus") {
    return "Håll pekaren över kartan eller tryck <strong>Läs av kartcentrum</strong> (Alt+I) för soltimmar, SVF och markhöjd. Växla till <strong>Redigera förslag</strong> för att rita hus och se hur skuggningen förändras.";
  }
  if (amne === "mikroklimat") {
    return "Välj ett väderförval för att beräkna UTCI och T<sub>mrt</sub> på kartan, och växla till <strong>Redigera förslag</strong> för att pröva skuggande hus eller träd.";
  }
  if (amne === "buller") {
    return "Använd <strong>Rita väg på kartan</strong> (2 punkter) och ange ÅDT för att beräkna en bullerskiss. Växla till <strong>Redigera förslag</strong> för att prova bullerskärmar eller byggnader.";
  }
  if (amne === "luft") {
    return "Håll pekaren över kartan eller tryck <strong>Läs av kartcentrum</strong> (Alt+I) för vind- eller haltvärde på gånghöjd och markhöjd. Växla till <strong>Redigera förslag</strong> för att pröva ett nytt förslag.";
  }
  return "Håll pekaren över kartan eller tryck <strong>Läs av kartcentrum</strong> (Alt+I) för spridningsvärde och markhöjd. Välj habitatnätverk ovan eller beräkna Svensk Grönytefaktor (GYF) nedan.";
}

/** Domänkorrekt underlagsetikett i Redigera förslag (aldrig "vatten" för sol/ekologi/luft/mikroklimat/buller, U5d). */
export function underlagEtikettForAmne(amne: ResultatAmne): string {
  if (amne === "vatten") return "Visa nulägets vatten under";
  if (amne === "dagsljus") return "Visa nulägets solkarta under";
  if (amne === "mikroklimat") return "Visa nulägets värmekarta under";
  if (amne === "buller") return "Visa nulägets bullerskarta under";
  if (amne === "luft") return "Visa nulägets luftkarta under";
  return "Visa nulägets ekologikarta under";
}

/** Domänkorrekt gruppetikett för kartlagerknapparna i Steg 2 (U5d). */
export function lagerGruppEtikettForAmne(amne: ResultatAmne): string {
  return `Välj kartlager för ${AMNESNAMN[amne].toLowerCase()}`;
}

/** Domänkorrekt "Så läser du kartan"-text (förhindrar vattendjupstext i Ekologi skillnad, U5d). */
export function lasKartanForAmne(amne: ResultatAmne, vy: ProjektVy, lage: Lage, paahittat = true): string {
  const grund = lasKartan(vy, lage, paahittat);
  if (amne === "ekologi" && lage === "skillnad") {
    return grund.replace(
      "Skillnad visar bara det som ändrats: rött är djupare vatten efter förändringen, blått är grundare.",
      "Skillnad visar bara det som ändrats mellan före och efter i spridningsflödet: rött är ökning och blått är minskning.",
    );
  }
  return grund;
}

/* ---------- U5c: Tangentbordsavläsning av kartvärden (SWEREF 99 18 00) ---------- */

export type PilTangent = "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight";

export function arPilTangent(key: string): key is PilTangent {
  return key === "ArrowUp" || key === "ArrowDown" || key === "ArrowLeft" || key === "ArrowRight";
}

/** Kortkommando Alt+I (hanterar både `key === "i"` och macOS `code === "KeyI"`). */
export function arKartcentrumKortkommando(
  h: Pick<KeyboardEvent, "key" | "code" | "altKey" | "ctrlKey" | "metaKey">,
): boolean {
  return h.altKey && !h.ctrlKey && !h.metaKey && (h.key.toLowerCase() === "i" || h.code === "KeyI");
}

/** Räknar om kartcentrum (WGS 84) till SWEREF 99 18 00 (E, N i meter). */
export function beraknaKartcentrumSweref(vy: { lng: number; lat: number }): { e: number; n: number } {
  const [e, n] = wgs84TillSweref(vy.lng, vy.lat);
  return { e, n };
}

/** Flyttar kartcentrum deterministiskt i SWEREF 99 18 00 vid piltangent på fokuserad karta (U5c). */
export function forflyttaKartvyMedPil(
  vy: { lng: number; lat: number; zoom: number },
  tangent: PilTangent,
  meterPerPx: number,
  finsteg = false,
): { lng: number; lat: number; zoom: number; e: number; n: number } {
  const { e, n } = beraknaKartcentrumSweref(vy);
  const mpp = Number.isFinite(meterPerPx) && meterPerPx > 0 ? meterPerPx : 1;
  const stegM = Math.max(finsteg ? 0.5 : 2, mpp * (finsteg ? 12 : 48));
  const nyE = tangent === "ArrowRight" ? e + stegM : tangent === "ArrowLeft" ? e - stegM : e;
  const nyN = tangent === "ArrowUp" ? n + stegM : tangent === "ArrowDown" ? n - stegM : n;
  const [lng, lat] = swerefTillWgs84(nyE, nyN);
  return { lng, lat, zoom: vy.zoom, e: nyE, n: nyN };
}

/** Visningstext för kartcentrum i `#ar-avl` (SWEREF 99 18 00, meter). */
export function formateraKartcentrumEtikett(e: number, n: number): string {
  return `Kartcentrum · E ${formatera(Math.round(e), 0)}, N ${formatera(Math.round(n), 0)}`;
}

/** Kopplar tangentbordsstyrd avläsning (knapp, Alt+I och piltangenter) till samma deterministiska värdeavläsning som mus-hovring (U5c). */
export function skapaTangentbordsAvlasning(mal: {
  hamtaVy(): { lng: number; lat: number; zoom: number } | null;
  settVy(vy: { lng: number; lat: number; zoom: number }): void;
  meterPerPx(): number;
  lasAv(e: number, n: number): void;
}): {
  lasCentrum(): { e: number; n: number } | null;
  stegPil(tangent: PilTangent, finsteg?: boolean): { e: number; n: number } | null;
} {
  return {
    lasCentrum: () => {
      const nu = mal.hamtaVy();
      if (!nu) return null;
      const punkt = beraknaKartcentrumSweref(nu);
      mal.lasAv(punkt.e, punkt.n);
      return punkt;
    },
    stegPil: (tangent, finsteg = false) => {
      const nu = mal.hamtaVy();
      if (!nu) return null;
      const ny = forflyttaKartvyMedPil(nu, tangent, mal.meterPerPx(), finsteg);
      mal.settVy({ lng: ny.lng, lat: ny.lat, zoom: ny.zoom });
      mal.lasAv(ny.e, ny.n);
      return { e: ny.e, n: ny.n };
    },
  };
}

/** Tolkar och tillämpar numerisk CAD-inmatning (längd + bäring eller SWEREF E, N) på aktivt ritverktyg. */
export function tillampaCadInmatning(
  mal: {
    laggTillPunktSweref(e_m: number, n_m: number): boolean;
    laggTillPunktMedMatt(langd_m: number, baring_grader: number): boolean;
  },
  inmatning: {
    lage: CadInmatningsLage;
    langdText: string;
    baringText: string;
    eText: string;
    nText: string;
  },
): { ok: true } | { ok: false; fel: string } {
  if (inmatning.lage === "sweref") {
    const tolkad = tolkaSwerefKoordinat(inmatning.eText, inmatning.nText);
    if (!tolkad.ok) return { ok: false, fel: tolkad.fel };
    const lagt = mal.laggTillPunktSweref(tolkad.varde.x, tolkad.varde.y);
    return lagt ? { ok: true } : { ok: false, fel: "Punkten kunde inte läggas till i aktivt ritverktyg." };
  }
  const tolkad = tolkaLangdOchBaring(inmatning.langdText, inmatning.baringText);
  if (!tolkad.ok) return { ok: false, fel: tolkad.fel };
  const lagt = mal.laggTillPunktMedMatt(tolkad.varde.langd_m, tolkad.varde.baring_grader);
  return lagt
    ? { ok: true }
    : { ok: false, fel: "Sätt en första punkt (klicka i kartan eller ange E, N) innan du anger längd och bäring." };
}

/** Mätvärden i nulägespanelen för Ekologi-fliken (ECA före, PC före och antal habitatfläckar, W-B8). */
export function nulageEkologiTalHtml(u: Underlag): string {
  const ekoTal = nyckeltal("ekologi", u);
  const ecaFore = ekoTal[0];
  const pcFore = ekoTal[3];
  const flackAntal = u.ekologi?.fore?.antal_flackar;
  const poster = [
    ...(ecaFore ? [ecaFore] : []),
    ...(pcFore ? [pcFore] : []),
    {
      etikett: "Habitatfläckar i nuläget",
      varde: typeof flackAntal === "number" && Number.isFinite(flackAntal) ? formatera(flackAntal, 0) : "–",
    },
  ];
  return talListaHtml(poster);
}

/** Uppdaterar avläsningsrutan `#ar-avl` för 2D, 3D och tangentbordsavläsning (markvärden, 3D-objekttext och markhöjd, W-B4 / U5c). */
export function renderaAvlasningElement(
  ruta: Pick<HTMLElement, "hidden" | "innerHTML">,
  indata: {
    senasteAvl: AvlasningSvar | null;
    vy: Pick<ProjektVy, "fore_lager" | "efter_lager" | "skillnad_lager" | "lage_idag"> | null;
    lage: ArbetsLage;
    amne: ResultatAmne;
    visaTak: boolean;
    treDText: string | null;
    hojdText: string | null;
    punktEtikett?: string | null;
    tomText?: string | null;
  },
): void {
  const delar: string[] = [];
  const { senasteAvl, vy, lage, amne, visaTak, treDText, hojdText, punktEtikett, tomText } = indata;
  if (punktEtikett) {
    delar.push(`<span class="ar-avl-punkt">${esc(punktEtikett)}</span>`);
  }
  let antalVarden = 0;
  if (senasteAvl && vy && !senasteAvl.varden.every((x) => !x.inom)) {
    const rader: Array<[string, string | null]> =
      lage === "nulage" || lage === "redigera" || vy.lage_idag
        ? [["Nuläge", vy.fore_lager]]
        : [
            ["Nuläge", vy.fore_lager],
            ["Efter", vy.efter_lager],
            ["Skillnad", vy.skillnad_lager],
          ];
    for (const [namn, id] of rader) {
      const x = senasteAvl.varden.find((v) => v.lager_id === id && v.inom);
      if (!x) continue;
      let text: string;
      if (x.tak && !visaTak) text = "tak, inte mark";
      else if (x.varde === null) text = amne === "vatten" ? "torr mark (0 cm)" : "inget värde";
      else if (amne === "vatten" && x.enhet === "m") {
        text = `${formateraDjupCm(x.varde, x.typ === "skillnad")}${x.tak ? " (tak)" : ""}`;
      } else {
        text = `${x.typ === "skillnad" && x.varde > 0 ? "+" : ""}${formatera(x.varde, 2)}${x.enhet ? ` ${x.enhet}` : ""}${x.tak ? " (tak)" : ""}`;
      }
      delar.push(`<span><span class="flode-dampad">${esc(namn)}</span> <strong>${esc(text)}</strong></span>`);
      antalVarden += 1;
    }
  }
  if (treDText) delar.push(`<span class="ar-avl-3d"><strong>${esc(treDText)}</strong></span>`);
  if (hojdText) delar.push(`<span class="flode-dampad" id="ar-hojd">${esc(hojdText)}</span>`);
  if (punktEtikett && antalVarden === 0 && !treDText && !hojdText && tomText) {
    delar.push(`<span class="flode-dampad">${esc(tomText)}</span>`);
  }
  ruta.hidden = delar.length === 0;
  ruta.innerHTML = delar.join("");
}

const FIGURFARG: Record<string, string> = {
  mark: "#8b5e3c",
  hardgjort: "#555555",
  vegetation: "#2e7d32",
  vatten: "#1565c0",
  brytlinje: "#111111",
  oppning: "#c2410c",
};

const TAKHJALP: Record<string, string> = {
  sadel: "Sadeltak: nocken längs husets längdaxel. Takvattnet räknas som för brutet tak.",
  pulpet: "Pulpettak: 3D-vyn lutar taket mot söder; takfallets riktning anges inte i förslaget, så takvattnet räknas som för brutet tak.",
  mansard: "Mansardtak: nocken längs husets längdaxel. Takvattnet räknas som för brutet tak.",
};

const ROLL_SNABBNAMN: Record<string, string> = {
  max_djup: "Vattendjup",
  fara: "Fara (h·v)",
  hastighet: "Hastighet",
  ankomsttid: "Ankomsttid",
  soltimmar: "Soltimmar",
  skuggmask: "Skugga",
  stromtathet: "Strömtäthet (flaskhalsar)",
  kansighet: "Känslighet (åtgärdspotential)",
};

const lagerPost = (r: ResultatSvar | undefined, id: string | null): LagerPost | null => (id && r ? (r.lager.find((l) => l.id === id) ?? null) : null);

/** Väljer ett befintligt redigerbart ritlager med önskad roll, eller skapar ett nytt direkt. */
function aktiveraRollLager(dok: PlanDok, roll: Roll, roller: readonly RollInfo[]): void {
  const befintligt = dok.lager.find((l) => l.roll === roll && l.synlig && !l.last);
  if (befintligt) {
    dok.sattAktivtLager(befintligt.lager_id);
    return;
  }
  const dolt = dok.lager.find((l) => l.roll === roll);
  if (dolt) {
    dok.andraLager(dolt.lager_id, (l) => {
      l.synlig = true;
      l.last = false;
    });
    dok.sattAktivtLager(dolt.lager_id);
    return;
  }
  const namn: Record<Roll, string> = {
    byggnad: "Nya hus",
    mark: "Markhöjning / sänkning",
    hardgjort: "Hårdgjord yta",
    vegetation: "Vegetation",
    vatten: "Vatten",
    brytlinje: "Mur / vall",
    oppning: "Öppning / dike",
  };
  const nytt = nyttLager(dok.lager, roll, namn[roll], roller);
  if (roll === "brytlinje") {
    nytt.geometri = "linje";
    nytt.egenskaper_forval = { typ: "mur", hojd_m: 0.5 };
  } else if (roll === "oppning") {
    nytt.geometri = "linje";
    nytt.egenskaper_forval = { typ: "trumma", bredd_m: 1 };
  } else if (roll === "mark") {
    nytt.geometri = "polygon";
  }
  dok.laggTillLager(nytt);
  dok.sattAktivtLager(nytt.lager_id);
}

function hjalpText(v: Ritverktyg, aktivRoll: Roll | undefined): string {
  const u = v.linje.utkast.length;
  if (arGrovPekare()) {
    if (v.verktyg === "hus") return v.utkast.length === 0 ? "Tryck husets första hörn." : v.utkast.length < 3 ? "Tryck nästa hörn." : "Tryck första hörnet eller Klar.";
    if (v.verktyg === "yta") return u === 0 ? "Tryck markytans första hörn." : "Tryck hörnen, avsluta med första hörnet eller Klar.";
    if (v.verktyg === "linje") return u === 0 ? "Tryck linjens första punkt." : "Tryck nästa punkt, Klar avslutar.";
    return "Tryck på ett hus, en markyta eller linje för att markera.";
  }
  switch (v.verktyg) {
    case "hus":
      return v.utkast.length === 0 ? "Nytt hus: klicka första hörnet i kartan." : v.utkast.length < 3 ? "Klicka nästa hörn. Backsteg tar bort senaste, Esc avbryter." : "Klicka hörnen, dubbelklicka eller Enter för att avsluta.";
    case "yta":
      return u === 0
        ? aktivRoll === "mark"
          ? "Höj / sänk mark: klicka hörnen på ytan du vill höja eller sänka."
          : "Klicka ytans första hörn i kartan."
        : "Klicka nästa hörn. Klicka första hörnet, dubbelklicka eller Enter för att sluta ytan.";
    case "linje":
      return u === 0
        ? aktivRoll === "oppning"
          ? "Öppning / dike: klicka startpunkten i kartan."
          : "Mur / vall: klicka startpunkten i kartan."
        : "Klicka nästa punkt, dubbelklicka eller Enter för att avsluta.";
    case "valj":
      return "Klicka för att markera. Dra ett hus, en yta eller ett hörn för att flytta.";
    case "klipp":
      return "Klicka på en linje där den ska klippas i två.";
    case "ihop":
      return v.linje.ihopForsta ? "Klicka på nästa linje i samma lager." : "Klicka på den första av två linjer att slå ihop.";
    case "laggtill":
      return "Klicka på en kant i en markerad figur eller ett nytt hus för att lägga till ett hörn.";
    case "tabort":
      return "Klicka på ett hörn i en markerad figur eller ett nytt hus för att ta bort det.";
    default:
      return "Markera en figur och välj verktyg.";
  }
}

export function visaArbetsyta(rot: HTMLElement, projekt: string, initialtLage: ArbetsLage = "nulage"): Avslutbar {
  const t = tillstandFor(projekt);
  let avslutad = false;
  const ac = new AbortController();
  const avbryt: Array<() => void> = [];

  // Analys- & karttillstånd
  let projektSvar: ProjektSvar | null = null;
  let info: ProjektInfo | null = null;
  let amne: ResultatAmne = "vatten";
  let kalla: Kalla = "projekt";
  let vy: ProjektVy | null = null;
  let vyBas: ProjektVy | null = null;
  let valtNyckel: string | null = null;
  let r: ResultatSvar | undefined;
  let mark: MarkSvar | null = null;
  let ekologi: EkologiMedLager | null = null;
  let ekoLagerVal: "stromtathet" | "kansighet" = "stromtathet";
  let ekologiFel: string | null = null;
  let aktivtVarmelager: VarmestressLagerRef | null = null;
  let aktivtBullerlager: BullerLagerRef | null = null;
  let natverkLista: Array<{ natverk_id: string; visningsnamn: string; korbar: boolean; lokalt: boolean }> = [];
  let valtNatverk: string | null = null;
  let lage: ArbetsLage = initialtLage;
  let svep = 0.5;
  let visaTak = false;
  let visaVattenUnder = true;
  let visaFlodeUnder = false;
  let rasterOpacitetVal = RASTER_OPACITET_FORVAL;
  let skiktHopfalld = typeof window !== "undefined" && window.innerWidth <= 860;
  let avtappningFel = false;
  let regnFel: string | null = valideraRegnVal(t.regn);
  let solFel: string | null = valideraSolVal(t.sol);

  // Vektorlager, osäkerhet och avläsning
  const vektorPa = new Set<VektorSlag>();
  const vektorCache = new Map<string, VektorSvar>();
  const vektorLaddar = new Set<VektorSlag>();
  const vektorAvbryt = new Map<VektorSlag, { nyckel: string; ctrl: AbortController }>();
  const vektorFelPerSlag = new Map<VektorSlag, string>();
  const vektorGrans: Partial<Record<VektorSlag, number>> = {};
  let sankaHtml: string | null = null;
  let sankaSeq = 0;
  const osak: OsakerhetSvar = { vatten: null, svf: null, bifurkation: null, fel: [] };
  const osakPa = new Set<Amne>();
  let osakLaddar = false;
  let overfall: BifurkationsPunktPost[] = [];
  let onskadVy: string | null = null;
  const cache = new Map<string, ResultatSvar>();

  // Kartmotorer, mät-, sektions- & tidsspelarverktyg
  let a: Kartmotor | null = null;
  let b: Kartmotor | null = null;
  let bas: BasLager | null = null;
  let basLagerListan: RasterLager[] = [];
  let vy3d: { avmontera(): void; uppdatera?(): void; uppdateraPlan?(): void } | null = null;
  let vy3dDock: { avmontera(): void; uppdatera?(): void; uppdateraPlan?(): void } | null = null;
  let redigera3dPa = false;
  const planera3d = fordrojd(() => {
    vy3d?.uppdateraPlan?.();
    vy3dDock?.uppdateraPlan?.();
  }, 150);
  const kartLager = new LagerLista(projekt);
  const lager3dLista = new LagerLista(projekt, null);
  const mat = new MatVerktyg();
  const sektion = new SektionVerktyg();
  let sektionData: SektionSvar | null = null;
  let sektionLaddar = false;
  let sektionFel: string | null = null;
  let sektionAktivIndex: number | null = null;
  let sektionSeq = 0;
  let tidsserie: TidsserieSvar | null = null;
  const tidsserieCache = new Map<string, TidsserieSvar>();
  const tidsserieBilder = new Map<string, HTMLImageElement>();
  let tidTillstand: TidsspelareTillstand = skapaTidsspelareTillstand();
  let tidTimer: number | null = null;
  let tidSeq = 0;
  let dockFlik: "tid" | "sektion" = "tid";
  let friYtaRoll: FriYtaRoll = "hardgjort";
  let seq = 0;
  let avlSeq = 0;
  let avlTimer: number | null = null;
  let hojdText: string | null = null;
  let senasteAvl: AvlasningSvar | null = null;
  let treDText: string | null = null;
  let treDFel: string | null = null;
  let centrumAktiv = false;
  let centrumEtikett: string | null = null;

  // Numerisk CAD-inmatning i ritläget
  let cadOppen = false;
  let cadLage: CadInmatningsLage = "sweref";
  let cadManuelltLage = false;
  let cadLangdText = "";
  let cadBaringText = "";
  let cadEText = "";
  let cadNText = "";
  let cadFel: string | null = null;
  let cadNyckel = "";

  // Redigeringstillstånd (Ritverktyg, kontroll, sparande)
  let verktyg: Ritverktyg | null = null;
  let huvudVal: HuvudVerktyg = "valj";
  let kontroll: Kontroll | null = null;
  let kontrollFel: string | null = null;
  let meddelande: Meddelande | null = null;
  let sparFel: string | null = null;
  let sparat = "";
  let sparar = false;
  let sparTid: string | null = null;
  let kontrollTimer: number | null = null;
  let ritaBegard = 0;
  let skarvar: Skarvar | null = null;
  let skarvForslag: AtgardSvar | null = null;
  let skarvForslagNr = -1;
  let skarvForeslar = false;
  let skarvFel: string | null = null;
  let skarvMeddelande: string | null = null;
  let skarvTillampad = false;
  let sparKo: Promise<void> = Promise.resolve();
  let scenarier: ScenarioPost[] = [];
  let listFel: string | null = null;
  let figurNyckel = "";
  let faltFel: { nyckel: string; text: string } | null = null;
  let korAutoByt: "nulage" | "efter" | null = null;
  const uiLage = lasArbetsytaUiLage();
  if (skiktHopfalld) uiLage.teckenMinimerad = true;
  let ritaGrundkarta = (): void => {};

  rot.innerHTML = `
    <div class="flode-sida flode-fast ar-sida${uiLage.panelDold ? " panel-dold" : ""}">
      <section class="flode-karta ar-karta" aria-label="Karta">
        <div class="flode-karta-yta" id="ar-a" tabindex="0" role="region" aria-label="Karta i SWEREF 99 18 00. Använd piltangenter för att flytta kartan och Alt+I eller Enter för att läsa av kartcentrum."></div>
        <div class="flode-karta-yta flode-b" id="ar-b" hidden></div>
        <div class="flode-karta-yta" id="ar-3d" hidden></div>
        <div class="ar-centrum-sikte" id="ar-centrum-sikte" aria-hidden="true" hidden></div>

        <div class="ar-vaxel" role="group" aria-label="Kartläge" id="ar-vaxel">
          ${ARBETSLAGEN.map(([id, n]) => `<button type="button" data-lage="${id}" aria-pressed="${id === lage}">${esc(n)}</button>`).join("")}
        </div>

        <div class="ar-vattenunder" id="ar-vattenunder" hidden>
          <label class="flode-kryss"><input type="checkbox" id="ar-vatten-under" checked /> <span id="ar-vatten-under-text">${esc(underlagEtikettForAmne(amne))}</span></label>
          <label class="flode-kryss" id="ar-flode-under-rad" hidden><input type="checkbox" id="ar-flode-under" /> Flödesvägar under</label>
        </div>

        <nav class="ar-skikt-panel${skiktHopfalld ? " ar-skikt-hopfalld" : ""}" id="ar-skikt-panel" aria-label="Planeringsskikt och kartlager"${lage === "redigera" ? " hidden" : ""}>
          <div class="ar-skikt-huvud" id="ar-skikt-huvud">
            <span class="ar-skikt-titel">Planeringsskikt</span>
            <button type="button" class="ar-min-knapp ar-skikt-vaxla" id="ar-skikt-vaxla" aria-expanded="${!skiktHopfalld}" aria-controls="ar-skikt-kropp" aria-label="Fäll ihop skiktpanel" title="Fäll ihop skiktpanel">−</button>
          </div>
          <div class="ar-skikt-kropp" id="ar-skikt-kropp"${skiktHopfalld ? " hidden" : ""}>
            <div role="tablist" aria-label="Planeringsdomäner" aria-orientation="vertical" class="ar-skikt-domaner" id="ar-flikar"></div>
            <div class="ar-skikt-lager" id="ar-skikt-lager"></div>
            <div class="ar-skikt-opacitet-block">
              <label class="ar-skikt-opacitet" for="ar-raster-opacitet">
                <span class="ar-skikt-opacitet-rad"><span>Opacitet</span><output id="ar-raster-opacitet-ut">${Math.round(rasterOpacitetVal * 100)}\u202F%</output></span>
                <input type="range" id="ar-raster-opacitet" min="20" max="100" step="5" value="${Math.round(rasterOpacitetVal * 100)}" aria-label="Opacitet för aktivt analyslager" />
              </label>
            </div>
          </div>
        </nav>

        <div class="ar-verktyg" id="ar-verktyg" role="toolbar" aria-label="Ritverktyg" aria-orientation="vertical" hidden>
          <button type="button" data-hv="valj" aria-pressed="true" title="Flytta eller markera hus, markytor och linjer (${TANGENTER.valj})">Flytta / markera</button>
          <button type="button" data-hv="hus" aria-pressed="false" title="Rita nytt hus (${TANGENTER.hus})">Nytt hus</button>
          <button type="button" data-hv="mark" aria-pressed="false" title="Rita en yta för att höja eller sänka marken (${TANGENTER.yta})">Höj / sänk mark</button>
          <button type="button" data-hv="mur" aria-pressed="false" title="Rita mur, vall eller kantsten (${TANGENTER.linje})">Mur / vall</button>
          <button type="button" data-hv="oppning" aria-pressed="false" title="Rita öppning, trumma eller dike">Öppning / dike</button>
          <button type="button" data-hv="friyta" aria-pressed="false" title="Rita hårdgjord yta, vegetation eller vattenyta">Fri yta</button>
          <div class="ar-friyta-roller" id="ar-friyta-roller" role="group" aria-label="Roll för fri yta" hidden>
            <button type="button" data-friyta-roll="hardgjort" aria-pressed="true">Hårdgjort</button>
            <button type="button" data-friyta-roll="vegetation" aria-pressed="false">Vegetation</button>
            <button type="button" data-friyta-roll="vatten" aria-pressed="false">Vatten</button>
          </div>
          <button type="button" data-v="bort" title="Ta bort markerat eller riv befintligt hus (Delete)" disabled>Ta bort</button>
          <span class="flode-verktyg-skil" aria-hidden="true"></span>
          <button type="button" id="ar-redigera-3d-knapp" aria-pressed="false" title="Visa dockad 3D-förhandsvisning medan du ritar">3D-vy</button>
          <div class="ar-verktyg-par">
            <button type="button" data-v="angra" title="Ångra (${TANGENTER.angra})" disabled>Ångra</button>
            <button type="button" data-v="goraom" title="Gör om (Skift+${TANGENTER.angra})" disabled>Gör om</button>
          </div>
          <div class="ar-verktyg-par" id="ar-rit-kontroller" hidden>
            <button type="button" data-v="klar" title="Avsluta ritning (Enter)">Klar</button>
            <button type="button" data-v="backa" title="Backa en punkt (Backsteg)">Backa</button>
          </div>
          <details class="ar-fler" id="ar-fler">
            <summary>Fler ▾</summary>
            <div class="ar-fler-lista">
              <button type="button" data-v="laggtill" aria-pressed="false" title="Lägg till hörn (${TANGENTER.laggtill})">Lägg till hörn</button>
              <button type="button" data-v="tabort" aria-pressed="false" title="Ta bort hörn (${TANGENTER.tabort})">Ta bort hörn</button>
              <button type="button" data-v="klipp" aria-pressed="false" title="Klipp linje (${TANGENTER.klipp})">Klipp</button>
              <button type="button" data-v="ihop" aria-pressed="false" title="Slå ihop linjer (${TANGENTER.ihop})">Slå ihop</button>
              <button type="button" data-v="vand" title="Vänd linjens riktning (${TANGENTER.vand})" disabled>Vänd</button>
              <button type="button" data-v="forenkla" title="Förenkla figur (${TANGENTER.forenkla})" disabled>Förenkla</button>
              <span class="flode-verktyg-skil" aria-hidden="true"></span>
              <button type="button" data-v="fast" aria-pressed="true" title="Fäst mot hörn och kanter (${TANGENTER.fast})">Fäst</button>
              <button type="button" data-v="foljkant" aria-pressed="false" title="Följ kant (${TANGENTER.foljkant})">Följ kant</button>
              <button type="button" data-v="rat" aria-pressed="false" title="Rät vinkel (${TANGENTER.ratVinkel})">Rät vinkel</button>
              <button type="button" data-v="avbryt" title="Avbryt ritning (Esc)" disabled>Avbryt</button>
              <span class="flode-verktyg-skil" aria-hidden="true"></span>
              <button type="button" data-v="fil" title="Läs in hus, linjer och ytor ur en GeoJSON-fil eller sparat förslag">Läs in GeoJSON</button>
              <input type="file" id="ar-fil" aria-label="Välj en GeoJSON-fil" accept=".geojson,.json,application/geo+json,application/json" hidden />
            </div>
          </details>
        </div>

        <div class="ar-kartverktyg">
          <div class="ar-zoomrad" id="ar-zoomrad" role="group" aria-label="Kartverktyg">
            <button type="button" id="ar-mat-knapp" aria-pressed="false" title="Mät bredd eller avstånd i meter mellan två punkter (M)">Mät bredd</button>
            <button type="button" id="ar-sektion-knapp" aria-pressed="false" title="Rita sektion för mark-, byggnads- och vattenprofil (S)">Sektion</button>
            <button type="button" id="ar-las-centrum" title="Läs av kartvärde och markhöjd vid kartcentrum (Alt+I)">Läs av kartcentrum</button>
            <span class="ar-zoom-skil" aria-hidden="true"></span>
            <button type="button" id="ar-in" aria-label="Zooma in">+</button>
            <button type="button" id="ar-ut" aria-label="Zooma ut">−</button>
            <button type="button" id="ar-anpassa">Anpassa</button>
          </div>
          <div class="ar-grundkarta" id="ar-grundkarta" hidden></div>
        </div>

        <div class="ar-redigera-3d" id="ar-redigera-3d" hidden>
          <div class="ar-redigera-3d-huvud">
            <span>3D-vy</span>
            <button type="button" id="ar-redigera-3d-stang" aria-label="Stäng 3D-förhandsvisning">×</button>
          </div>
          <div class="ar-redigera-3d-yta" id="ar-redigera-3d-yta"></div>
        </div>

        <div class="flode-svep" id="ar-svep-rad" hidden>
          <span>Nuläge</span><input id="ar-svep" type="range" min="0" max="100" value="50" aria-label="Svepgräns: till vänster nuläge, till höger efter" /><span>Efter</span>
        </div>

        <div class="ar-tom-kort" id="ar-tom-kort" hidden></div>
        <div class="flode-kartkort flode-forklaring ar-tecken" id="ar-tecken" hidden></div>
        <div class="flode-kartkort ar-hjalp" id="ar-hjalp" role="status" hidden></div>
        <div class="ar-hoger-botten" id="ar-hoger-botten">
          <div class="flode-kartkort ar-sanka-kort" id="ar-sanka" role="status" hidden></div>
          <div class="flode-kartkort flode-avlasning ar-avlasning" id="ar-avl" role="status" aria-live="polite" aria-atomic="true" hidden></div>
          <div class="ar-notis" id="ar-notis" hidden></div>
          <div class="ar-skalstock" id="ar-skalstock" aria-live="off"></div>
        </div>
        <button type="button" class="ar-panel-vaxla" id="ar-panel-vaxla" aria-expanded="${!uiLage.panelDold}" aria-label="${uiLage.panelDold ? "Visa sidopanel" : "Dölj sidopanel"}" title="${uiLage.panelDold ? "Visa sidopanel" : "Dölj sidopanel"}">${uiLage.panelDold ? "⟨" : "⟩"}</button>
        <div class="ar-botten-dock" id="ar-botten-dock" hidden></div>
        <div class="flode-kartkort flode-kartkort-hoger" id="ar-laddar">Hämtar kartan …</div>
      </section>

      <aside class="flode-panel ar-panel" aria-label="Analys och förslag">
        <div id="ar-regn-plats"></div>
        <div class="ar-kor-block" id="ar-kor-block"></div>

        <div id="ar-analys-del">
          <div id="ar-innehall" class="flode-panelinnehall"><p class="flode-dampad">Hämtar underlag …</p></div>
          <div id="ar-gyf-utci-plats" class="ar-gyf-utci-plats" hidden></div>
          <div id="ar-buller-plats" class="ar-buller-plats" hidden></div>
        </div>

        <div id="ar-redigera-del" class="flode-panelinnehall" hidden>
          <div class="flode-panelhuvud">
            <h1 class="flode-h" id="ar-redigera-rubrik">Redigera förslag</h1>
            <p class="flode-liten flode-dampad">Rita nya hus eller höj/sänk mark direkt ovanpå nuläget.</p>
          </div>
          <div class="flode-falt"><label for="ar-namn">Förslagets namn</label>
            <input id="ar-namn" type="text" maxlength="80" placeholder="Mitt förslag" autocomplete="off" /></div>
          <div class="flode-sparrad">
            <button type="button" class="flode-knapp" id="ar-spara" aria-describedby="ar-sparstatus">Spara</button>
            <p class="flode-liten flode-dampad flode-sparstatus" id="ar-sparstatus" role="status" aria-live="polite"></p>
          </div>

          <div id="ar-cad-plats" hidden></div>

          <section class="flode-ruta" id="ar-hus-kort" aria-label="Valt hus" hidden>
            <div class="flode-tva">
              <div class="flode-falt"><label for="ar-hojd">Höjd (m)</label>
                <input id="ar-hojd" type="text" inputmode="decimal" aria-describedby="ar-hojd-hjalp" disabled /></div>
              <div class="flode-falt"><label for="ar-tak">Tak</label>
                <select id="ar-tak" disabled>${TAKFORMER.map((f) => `<option value="${f.id}">${esc(f.namn)}</option>`).join("")}</select></div>
            </div>
            <div class="ar-hus-atgarder" id="ar-hus-atgarder" hidden>
              <button type="button" class="flode-knapp" id="ar-riv-knapp">Riv byggnad</button>
            </div>
            <p class="flode-liten flode-dampad" id="ar-hojd-hjalp"></p>
          </section>

          <section class="flode-ruta" id="ar-figur" aria-labelledby="ar-figur-rubrik" hidden>
            <h2 class="flode-h2" id="ar-figur-rubrik">Egenskaper</h2>
            <div class="ar-linje-kontext" id="ar-linje-kontext" role="group" aria-label="Linjeredigering" hidden>
              <button type="button" data-v="klipp" aria-pressed="false" title="Klipp linje (${TANGENTER.klipp})">Klipp</button>
              <button type="button" data-v="ihop" aria-pressed="false" title="Slå ihop linjer (${TANGENTER.ihop})">Slå ihop</button>
              <button type="button" data-v="vand" title="Vänd linjens riktning (${TANGENTER.vand})">Vänd</button>
              <button type="button" data-v="forenkla" title="Förenkla linje (${TANGENTER.forenkla})">Förenkla</button>
            </div>
            <div id="ar-mark-snabb-plats"></div>
            <div id="ar-figur-falt"></div>
            <p class="flode-liten flode-dampad" id="ar-figur-info"></p>
          </section>

          <div class="flode-ruta" id="ar-info" role="status"></div>
          <p class="flode-liten ar-markvolym" id="ar-markvolym" hidden></p>
          <section class="flode-ruta" id="ar-skarv" aria-labelledby="skarv-rubrik" hidden></section>
          <p class="flode-liten flode-dampad" id="ar-projektkalla" hidden></p>

          <details class="flode-detalj" id="ar-ritlager-ruta">
            <summary>Ritlager</summary>
            <div class="ritlagerpanel" id="ar-ritlager"></div>
          </details>
          <details class="flode-detalj" id="ar-lager-ruta">
            <summary>Lager och underlag</summary>
            <div class="lagerpanel" id="ar-lagerpanel"></div>
            <details class="flode-detalj inre" id="ar-underlag-ruta"><summary>Lägg till underlag från en tjänst</summary><div id="ar-underlag"></div></details>
          </details>
          <details class="flode-detalj" id="ar-sparade-ruta">
            <summary>Sparade förslag</summary>
            <div id="ar-sparade"></div>
          </details>
          <details class="flode-detalj" id="ar-tangenter-ruta">
            <summary>Tangenter</summary>
            <div id="ar-tangenter"></div>
          </details>
        </div>

        <div id="ar-cadbim-plats" class="ar-cadbim-plats"></div>

        ${ordlistaHtml()}

        <div class="flode-rad-slut">
          <a class="flode-knapp stor" href="${flodeAdress(projekt, "rapport")}">Gå vidare till rapport</a>
        </div>
      </aside>
    </div>`;

  const el = <T extends HTMLElement>(id: string): T => rot.querySelector(`#${id}`) as T;
  const lyssna = (m: HTMLElement, typ: string, f: (h: Event) => void): void => m.addEventListener(typ, f, { signal: ac.signal });
  let bullerKontroller: BullerKontroller | null = null;
  let ritaSkiktPanel = (): void => {};
  let synkaMatOchBullerLinje = (): void => {};
  let synkaMarkorStilRef = (): void => {};
  let visaHjalpRef = (): void => {};
  const gyfUtciKontroller: GyfUtciKontroller = kopplaGyfUtciPaneler(el("ar-gyf-utci-plats"), {
    projekt,
    hamtaScenario: () => (kalla === "ritat" && t.scenario ? t.scenario.slug : null),
    hamtaLage: () => lage,
    hamtaAktivDoman: () => amne,
    onVarmelagerAndrat: (nytt) => {
      if (nytt) bullerKontroller?.rensaBullerlager();
      aktivtVarmelager = nytt;
      synkaKarta();
    },
    onStatusAndrad: () => ritaSkiktPanel(),
    signal: ac.signal,
  });
  bullerKontroller = kopplaBullerPanel(el("ar-buller-plats"), {
    projekt,
    hamtaScenario: () => (kalla === "ritat" && t.scenario ? t.scenario.slug : null),
    hamtaLage: () => lage,
    hamtaUtbredning: () => info?.utbredning ?? null,
    hamtaAktivDoman: () => amne,
    onBullerlagerAndrat: (nytt) => {
      if (nytt) gyfUtciKontroller.rensaVarmelager();
      aktivtBullerlager = nytt;
      synkaKarta();
    },
    onRitaVagAndrad: (aktiv) => {
      if (aktiv) {
        if (mat.aktiv) mat.sattAktiv(false);
        if (sektion.aktiv) sektion.avsluta();
      }
      synkaMarkorStilRef();
      visaHjalpRef();
    },
    onVagLinjeAndrad: () => synkaMatOchBullerLinje(),
    onStatusAndrad: () => ritaSkiktPanel(),
    signal: ac.signal,
  });
  const cadBimKontroller: CadBimKontroller = kopplaCadBimPanel(el("ar-cadbim-plats"), {
    projekt,
    hamtaScenario: () => (t.scenario ? t.scenario.slug : null),
    onImporterat: (svar) => {
      const utfall = laggInCadImportIDok(t.dok, svar, info?.ritlager?.roller ?? []);
      planeraKontroll();
      planeraRitning();
      return utfall;
    },
    signal: ac.signal,
  });

  const vyerFor = (k: Kalla): ProjektVy[] => (k === "ritat" ? t.ritVyer : k === "lage" ? t.lageVyer : (projektSvar?.vyer ?? [])).filter((v) => v.avsnitt === amne);
  const harEfter = (): boolean => Boolean(vy && !vy.lage_idag && lagerPost(r, vy.efter_lager));
  const harFore = (): boolean => Boolean(vy && lagerPost(r, vy.fore_lager));

  const forvaldaAnalyser = (): string[] => forvaldaAnalyserForInfo(info);
  const aktivaAnalyser = (): string[] => analyserForAktivtAmne(info, amne);

  /** Regnetikett ur körningens resultat (enkel 1-radig text). */
  const korningRegnRad = (): string | null => {
    if (!r || amne !== "vatten") return null;
    const regnPost = r.antaganden.regn[0];
    return regnEtikett(regnPost) ?? regnText(regnPost);
  };

  // ---------- Skalstock & nedladdning ----------
  const uppdateraSkalstock = (mpp: number): void => {
    el("ar-skalstock").innerHTML = skalstockHtml(skalstockMatt(mpp));
  };

  const laddaNerFil = (innehall: string, filnamn: string, mime: string): void => {
    const blob = new Blob([innehall], { type: mime });
    const url = URL.createObjectURL(blob);
    const lank = document.createElement("a");
    lank.href = url;
    lank.download = filnamn;
    document.body.appendChild(lank);
    lank.click();
    lank.remove();
    URL.revokeObjectURL(url);
  };

  // ---------- Bottendock: Sektion & Tidsspelare ----------
  const stoppaTidTimer = (): void => {
    if (tidTimer !== null) window.clearInterval(tidTimer);
    tidTimer = null;
  };

  const ritaBottenDock = (): void => {
    const dock = el("ar-botten-dock");
    const kartaEl = rot.querySelector<HTMLElement>(".ar-karta");
    const harSektion = sektionLaddar || sektionData !== null || sektionFel !== null;
    const harTid = amne === "vatten" && tidsserie !== null && tidsserie.tidssteg.length > 0 && lage !== "3d";
    if (!harSektion && !harTid) {
      dock.hidden = true;
      dock.innerHTML = "";
      kartaEl?.classList.remove("har-botten");
      return;
    }
    if (dockFlik === "sektion" && !harSektion) dockFlik = "tid";
    if (dockFlik === "tid" && !harTid) dockFlik = "sektion";

    const flikarHtml =
      harSektion && harTid
        ? `<div class="ar-dock-flikar" role="tablist" aria-label="Bottenpanel">
            <button type="button" role="tab" data-dock-flik="tid" aria-selected="${dockFlik === "tid"}">Tid</button>
            <button type="button" role="tab" data-dock-flik="sektion" aria-selected="${dockFlik === "sektion"}">Sektion</button>
          </div>`
        : "";

    if (dockFlik === "sektion" && harSektion) {
      let innehall = "";
      if (sektionLaddar) {
        innehall = `<div class="ar-sektion-huvud"><span class="ar-sektion-samman">Hämtar sektion …</span><div class="ar-sektion-knappar"><button type="button" data-sektion-handling="stang" aria-label="Stäng sektion">×</button></div></div>`;
      } else if (sektionFel) {
        innehall = `<div class="ar-sektion-huvud"><span class="flode-fel flode-liten" role="alert">${esc(sektionFel)}</span><div class="ar-sektion-knappar"><button type="button" data-sektion-handling="stang" aria-label="Stäng sektion">×</button></div></div>`;
      } else if (sektionData) {
        const s = sektionData.sammanfattning;
        const langdTxt = `${formatera(s.langd_m, s.langd_m >= 100 ? 0 : 1)} m`;
        const brFore = s.oversvammad_bredd_010m_fore_m;
        const brEfter = s.oversvammad_bredd_010m_efter_m;
        const breddDel =
          sektionData.har_efter && brEfter !== null
            ? `Översvämmad bredd (≥ 10 cm): ${brFore !== null ? formatera(brFore, 1) : "–"} → ${formatera(brEfter, 1)} m`
            : brFore !== null
              ? `Översvämmad bredd (≥ 10 cm): ${formatera(brFore, 1)} m`
              : "Översvämmad bredd (≥ 10 cm): –";
        const avl = sektionAktivIndex !== null ? avlasningVidIndex(sektionData, sektionAktivIndex) : null;
        const avlRad = avl
          ? `Avstånd ${avl.avstandText} · Mark ${avl.markForeText} · Djup ${avl.djupForeText}${sektionData.har_efter ? ` → ${avl.djupEfterText}` : ""} · Vattenyta ${avl.vattenYtaForeText}`
          : "Hovra över profilen för exakt avläsning i kartan.";
        const svg = ritaSektionSvg(sektionData, {
          bredd: 720,
          hojd: 176,
          aktivIndex: sektionAktivIndex,
          visaFore: true,
          visaEfter: sektionData.har_efter,
        });
        innehall = `<div class="ar-sektion-huvud">
            <span class="ar-sektion-samman"><strong>Sektion ${esc(langdTxt)}</strong> · ${esc(breddDel)}</span>
            <div class="ar-sektion-knappar">
              <button type="button" data-sektion-handling="svg" title="Ladda ner profil som SVG">SVG</button>
              <button type="button" data-sektion-handling="csv" title="Ladda ner samplade värden som CSV">CSV</button>
              <button type="button" data-sektion-handling="stang" aria-label="Stäng sektion">×</button>
            </div>
          </div>
          <div class="ar-sektion-avl" id="ar-sektion-avl">${esc(avlRad)}</div>
          <div class="ar-sektion-svg" data-sektion-svg>${svg}</div>`;
      }
      dock.innerHTML = `${flikarHtml}<div class="ar-sektion-panel">${innehall}</div>`;
    } else if (harTid && tidsserie) {
      dock.innerHTML = `${flikarHtml}${tidsspelareHtml(tidsserie, tidTillstand)}`;
    }
    dock.hidden = false;
    kartaEl?.classList.add("har-botten");
  };

  const stangSektion = (): void => {
    sektionSeq += 1;
    sektionData = null;
    sektionLaddar = false;
    sektionFel = null;
    sektionAktivIndex = null;
    sektion.avsluta();
    a?.settSektionLinje(null);
    b?.settSektionLinje(null);
    a?.settSektionMarkor(null);
    b?.settSektionMarkor(null);
    ritaBottenDock();
  };

  const hamtaSektionForLinje = async (): Promise<void> => {
    const pts = sektion.punkterSomPar();
    if (pts.length < 2) return;
    const nr = ++sektionSeq;
    sektionLaddar = true;
    sektionFel = null;
    sektionAktivIndex = null;
    dockFlik = "sektion";
    a?.settSektionMarkor(null);
    b?.settSektionMarkor(null);
    ritaBottenDock();
    try {
      const vattenLager = sektionVattenLager(r?.lager, vy);
      const svar = await hamtaSektion(projekt, {
        punkter: pts,
        korning_id: vy?.korning_id ?? null,
        fore_lager: vattenLager.fore_lager,
        efter_lager: vattenLager.efter_lager,
      });
      if (nr !== sektionSeq || avslutad) return;
      sektionData = svar;
      sektionLaddar = false;
      sektionFel = null;
    } catch (e) {
      if (nr !== sektionSeq || avslutad) return;
      sektionData = null;
      sektionLaddar = false;
      sektionFel = e instanceof Error ? e.message : String(e);
    }
    ritaBottenDock();
  };

  const uppdateraTidSpelning = (): void => {
    stoppaTidTimer();
    if (!tidTillstand.spelar || !tidsserie || tidsserie.tidssteg.length === 0) return;
    const ms = tidTillstand.hastighet === 2 ? 350 : 700;
    tidTimer = window.setInterval(() => {
      if (avslutad || !tidsserie) {
        stoppaTidTimer();
        return;
      }
      tidTillstand = nastaSteg(tidTillstand, tidsserie.tidssteg.length);
      synkaKarta();
      ritaBottenDock();
    }, ms);
  };

  const aktivtTidsserieScenario = (): string | null => {
    if (amne !== "vatten" || !r || !vy || !Array.isArray(r.tidsserie_scenarier) || r.tidsserie_scenarier.length === 0) {
      return null;
    }
    const foreSc = lagerPost(r, vy.fore_lager)?.scenario ?? null;
    const efterSc = lagerPost(r, vy.efter_lager)?.scenario ?? null;
    const onskad = lage === "efter" ? (efterSc ?? foreSc) : foreSc;
    if (onskad && r.tidsserie_scenarier.includes(onskad)) return onskad;
    return r.tidsserie_scenarier[0] ?? null;
  };

  const laddaTidsserie = async (): Promise<void> => {
    const sc = aktivtTidsserieScenario();
    if (!sc || !vy) {
      stoppaTidTimer();
      tidsserie = null;
      tidTillstand = skapaTidsspelareTillstand();
      ritaBottenDock();
      return;
    }
    const nyckel = `${vy.korning_id}|${sc}`;
    const cachad = tidsserieCache.get(nyckel);
    if (cachad) {
      if (tidsserie !== cachad) {
        stoppaTidTimer();
        tidsserie = cachad;
        tidTillstand = skapaTidsspelareTillstand();
      }
      ritaBottenDock();
      return;
    }
    const nr = ++tidSeq;
    try {
      const svar = await hamtaTidsserie(vy.korning_id, sc);
      if (nr !== tidSeq || avslutad) return;
      tidsserieCache.set(nyckel, svar);
      for (const steg of svar.tidssteg) {
        if (!tidsserieBilder.has(steg.png_url)) {
          const img = new Image();
          img.src = steg.png_url;
          tidsserieBilder.set(steg.png_url, img);
        }
      }
      stoppaTidTimer();
      tidsserie = svar;
      tidTillstand = skapaTidsspelareTillstand();
      if (!sektionData && !sektionLaddar) dockFlik = "tid";
      ritaBottenDock();
    } catch {
      if (nr !== tidSeq || avslutad) return;
      tidsserie = null;
      ritaBottenDock();
    }
  };

  // ---------- Regn & körningsblock ----------
  type ForberaknadBasInfo = { kalla?: string; kommun?: string; antal_rutor?: number };
  const forberaknadBasRad = (): string => {
    const fb = (info as (ProjektInfo & { forberaknad_bas?: ForberaknadBasInfo | null }) | null)?.forberaknad_bas;
    if (!fb) return "";
    const plats = fb.kommun || fb.kalla;
    if (!plats) return "";
    const rutor = typeof fb.antal_rutor === "number" && Number.isFinite(fb.antal_rutor) ? ` (${formatera(fb.antal_rutor, 0)} rutor)` : "";
    return `<p class="flode-liten flode-dampad ar-forberaknad">Förberäknat underlag: ${esc(plats)}${esc(rutor)}</p>`;
  };

  const ritaRegnOchKor = (): void => {
    const regnPlats = el("ar-regn-plats");
    const visaRegn = amne === "vatten" || lage === "redigera";
    regnPlats.hidden = !visaRegn;
    regnPlats.innerHTML = visaRegn
      ? regnValHtml(t.regn, info?.regn_forval, t.avtappning, regnFel, avtappningFel, t.skyfallMetod)
      : "";

    const pagar = arPagaende(t);
    const harRitatForslag = !t.dok.tom();
    const korBlock = el("ar-kor-block");
    const arEgenDoman = amne === "mikroklimat" || amne === "buller";
    const arTomLuft = amne === "luft" && aktivaAnalyser().length === 0;
    const arEkologiUtanNmd = amne === "ekologi" && Boolean(info) && !harEkologiUnderlag(info);

    if (lage !== "redigera" && (arEgenDoman || arTomLuft)) {
      korBlock.hidden = true;
      korBlock.innerHTML = "";
      el("ar-tom-kort").hidden = true;
      ritaSkiktPanel();
      return;
    }
    korBlock.hidden = false;

    const korAmne: ResultatAmne =
      lage === "redigera" && aktivaAnalyser().length === 0 ? "vatten" : amne;
    const malAnalyser =
      lage === "redigera" && aktivaAnalyser().length === 0 ? forvaldaAnalyser() : aktivaAnalyser();
    const inst = bedomInstallningsStatus(t, korAmne, projektSvar?.vyer);
    const etiketter = korKnappEtiketterForAmne(info, korAmne);
    const harEgetAmne = Boolean(info?.analyser?.some((a) => amneForAnalysId(a.id, a.mall) === korAmne));
    const harAktuellt =
      korAmne === "vatten"
        ? !inst.regnUtdaterat
        : korAmne === "dagsljus"
          ? !inst.solUtdaterat
          : harEgetAmne
            ? vyerFor("lage").length > 0 || vyerFor("ritat").length > 0 || vyerFor("projekt").length > 0
            : false;
    const visatRegn = korAmne === "vatten" ? korningRegnRad() : null;
    const valtRegn = korAmne === "vatten" ? valdRegnText(t.regn, info?.regn_forval, t.avtappning, t.skyfallMetod) : null;
    const aktivVarning =
      arEkologiUtanNmd && lage !== "redigera"
        ? EKOLOGI_UTAN_NMD_TEXT
        : korAmne === "vatten"
          ? inst.regnUtdaterat
            ? visatRegn && valtRegn
              ? `Visar just nu ${visatRegn}. Tryck på knappen ovan för att räkna om med ${valtRegn}.`
              : t.skyfallMetod === "2d"
                ? "Vald skyfallsmetod (2D) eller regn skiljer sig från visat resultat (eller saknar beräkning)."
                : "Valt regn skiljer sig från visat resultat (eller saknar beräkning)."
            : null
          : korAmne === "dagsljus"
            ? inst.solUtdaterat
              ? "Vald solinställning skiljer sig från visat resultat (eller saknar beräkning)."
              : null
            : harEgetAmne
              ? null
              : null;
    const kanKoraAktivt = !pagar && kanStartaKorningForAnalyser(malAnalyser, regnFel, avtappningFel, solFel);
    const kanKoraAlla = !pagar && kanStartaKorningForAnalyser(forvaldaAnalyser(), regnFel, avtappningFel, solFel);

    let knappar = "";
    let allaMal: "nulage" | "forslag" = "nulage";
    if (arEkologiUtanNmd && lage !== "redigera") {
      knappar = `<button type="button" class="flode-knapp" id="ar-kor-nulage" disabled title="Geografin saknar NMD-underlag för konnektivitet">Beräkna konnektivitet (saknar NMD)</button>`;
      allaMal = "nulage";
    } else if (harRitatForslag) {
      if (lage === "nulage" && !harAktuellt) {
        knappar = `<button type="button" class="flode-knapp svart" id="ar-kor-nulage"${kanKoraAktivt ? "" : " disabled"}>${pagar && t.korning?.lage ? "Beräknar …" : esc(etiketter.nulage)}</button>`;
        knappar += `<button type="button" class="flode-knapp" id="ar-kor-forslag"${kanKoraAktivt ? "" : " disabled"}>${pagar && !t.korning?.lage ? "Beräknar …" : esc(etiketter.forslag)}</button>`;
        allaMal = "nulage";
      } else {
        knappar = `<button type="button" class="flode-knapp svart" id="ar-kor-forslag"${kanKoraAktivt ? "" : " disabled"}>${pagar && !t.korning?.lage ? "Beräknar …" : esc(etiketter.forslag)}</button>`;
        if (!harAktuellt) {
          knappar += `<button type="button" class="flode-knapp" id="ar-kor-nulage"${kanKoraAktivt ? "" : " disabled"}>${pagar && t.korning?.lage ? "Beräknar …" : esc(etiketter.nulage)}</button>`;
        }
        allaMal = "forslag";
      }
    } else if (!harAktuellt) {
      knappar = `<button type="button" class="flode-knapp svart" id="ar-kor-nulage"${kanKoraAktivt ? "" : " disabled"}>${pagar ? "Beräknar …" : esc(etiketter.nulage)}</button>`;
      allaMal = "nulage";
    } else {
      knappar = `<button type="button" class="flode-knapp" id="ar-rita-forslag">Rita förslag</button><button type="button" class="flode-knapp" id="ar-kor-nulage"${kanKoraAktivt ? "" : " disabled"}>${pagar ? "Beräknar …" : esc(etiketter.raknaOmNulage)}</button>`;
      allaMal = "nulage";
    }

    const allaKnapp =
      etiketter.arFlerAmnen && !arEkologiUtanNmd
        ? `<button type="button" class="flode-knapp diskret ar-kor-alla" id="ar-kor-alla" data-kor-alla="${allaMal}"${kanKoraAlla ? "" : " disabled"}>${esc(allaMal === "forslag" ? etiketter.allaForslag : etiketter.allaNulage)}</button>`
        : "";

    const status = pagar
      ? `<p class="flode-liten" role="status" aria-live="polite">${esc(t.korning?.text ?? "Beräknar …")}${valtRegn ? ` · ${esc(valtRegn)}` : ""}</p>`
      : t.korning?.fel
        ? `<p class="flode-fel flode-liten" role="alert">${esc(t.korning.fel)}</p>`
        : aktivVarning
          ? `<p class="flode-liten flode-dampad">${esc(aktivVarning)}</p>`
          : "";

    const omfangInfo =
      (info as (ProjektInfo & { berakningsomfang?: BerakningsomfangInfo | null }) | null)?.berakningsomfang ?? null;
    const omfangHtml = korAmne === "vatten" ? berakningsomfangHtml(omfangInfo) : "";

    korBlock.innerHTML = `<div class="ar-kor-knappar">${knappar}</div>${allaKnapp}${omfangHtml}${status}${korAmne === "vatten" ? forberaknadBasRad() : ""}`;

    // Tom-kort mitt på kartan när nuläget inte är beräknat än (aldrig för domäner som saknar K20-mall eller redan har direktdata)
    const tomKort = el("ar-tom-kort");
    const harDirektEko = amne === "ekologi" && ekologi !== null;
    const visaTomKort =
      lage === "nulage" &&
      !harFore() &&
      !harDirektEko &&
      !pagar &&
      Boolean(info) &&
      !arEgenDoman &&
      !arTomLuft &&
      !arEkologiUtanNmd &&
      malAnalyser.length > 0;
    tomKort.hidden = !visaTomKort;
    if (visaTomKort) {
      const tomBeskrivning =
        amne === "vatten"
          ? `Kör en skyfallsberäkning för projektets befintliga läge med <strong>${esc(valdRegnText(t.regn, info?.regn_forval, t.avtappning, t.skyfallMetod))}</strong> så visas vattendjup och flödesvägar direkt på kartan.`
          : amne === "dagsljus"
            ? "Kör en nulägesberäkning för projektets befintliga läge så visas soltimmar och skuggning direkt på kartan."
            : amne === "ekologi"
              ? "Kör en nulägesberäkning för projektets befintliga läge så visas ekologisk konnektivitet direkt på kartan."
              : "Kör en nulägesberäkning för projektets befintliga läge så visas vindkomfort och luftresultat direkt på kartan.";
      const tomOmfang = amne === "vatten" ? berakningsomfangHtml(omfangInfo, { medId: false }) : "";
      tomKort.innerHTML = `<h2>Nuläget är inte beräknat än</h2>
        <p>${tomBeskrivning}</p>
        ${tomOmfang}
        <button type="button" class="flode-knapp svart stor" id="ar-tom-kor-nulage"${kanKoraAktivt ? "" : " disabled"}>${esc(etiketter.nulage)}</button>`;
    }
    ritaSkiktPanel();
  };

  // ---------- Vektorlager & snabbknappar ----------
  const kartLageForAnalys = (): Lage => (lage === "nulage" || lage === "redigera" ? "fore" : lage);

  const vektorNyckel = (slag: VektorSlag): string | null => {
    const sc = r && vy ? vektorScenario(r.lager, vy, kartLageForAnalys()) : null;
    return sc && vy ? `${vy.korning_id}|${sc}|${slag}|${vektorGrans[slag] ?? ""}` : null;
  };
  const vektorerForResultat = () => {
    if (!r || amne === "mikroklimat" || amne === "buller") return [];
    const alla = vektorVal(amne, r.antaganden.analyser);
    if (!Array.isArray(r.vektorlager)) return alla;
    const harRannsten = r.vektorlager.some((v) => v.roll === "rannsten_flode");
    return alla.filter((v) => v.slag !== "rannsten_flode" || harRannsten);
  };
  const tillgangligaVektorer = (): VektorSlag[] => vektorerForResultat().map((x) => x.slag);
  const aktivaVektorer = (): VektorSvar[] => {
    if (lage === "3d") return [];
    const onskade: ReadonlySet<VektorSlag> =
      lage === "redigera" ? (visaFlodeUnder ? new Set<VektorSlag>(["flodesvagar"]) : new Set<VektorSlag>()) : vektorPa;
    return tillgangligaVektorer()
      .filter((s) => onskade.has(s))
      .map((s) => vektorCache.get(vektorNyckel(s) ?? ""))
      .filter((x): x is VektorSvar => Boolean(x));
  };
  const sattVektorer = (): void => {
    const alla = aktivaVektorer().map(vektorTillKarta);
    if (amne === "ekologi" && ekologi && lage !== "3d" && lage !== "redigera") {
      const punkter = flaskhalsPunkter(ekologi, lage === "nulage" ? "fore" : "efter");
      if (punkter.length > 0) {
        alla.push({
          fc: {
            type: "FeatureCollection",
            features: punkter.map((p, idx) => ({
              type: "Feature",
              properties: {
                farg: "#111111",
                beskrivning: `Flaskhals ${idx + 1} · Strömtäthet ${p.stromtathet_text}`,
              },
              geometry: { type: "Point", coordinates: swerefTillWgs84(p.e_m, p.n_m) },
            })),
          },
          etiketter: [],
        });
      }
    }
    const ut: AnalysVektor | null =
      alla.length === 0 ? null : { fc: { type: "FeatureCollection", features: alla.flatMap((x) => x.fc.features) }, etiketter: alla.flatMap((x) => x.etiketter) };
    a?.settAnalysVektor(ut);
    b?.settAnalysVektor(null);
  };
  const vektorEtikett = (slag: VektorSlag): string =>
    slag === "sankor" ? "sänkor" : slag === "flodesvagar" ? "flödesvägar" : slag === "rannsten_flode" ? "rännstenar" : "fasadpunkter";

  const arAvbrutetVektorFel = (e: unknown, signal: AbortSignal): boolean =>
    signal.aborted || (e instanceof Error && e.name === "AbortError");

  const lagerNotData = (): { text: string; arFel: boolean; arVarning: boolean } => {
    const roller = rollerNu();
    const aktivNyckel = valtNyckel ?? (vyBas ? forvaldNyckel(roller, vyBas) : null);
    const valdRoll = roller.find((x) => x.nyckel === aktivNyckel);
    const arKontrollVatten = amne === "vatten" && Boolean(valdRoll?.kontroll);
    const basText = arKontrollVatten && valdRoll
      ? `Visar kontrollager i stället för vattendjup (${valdRoll.etikett}). Välj Vattendjup ovan för att se regnets vatten på kartan.`
      : valdRoll && vyBas
        ? valText(valdRoll, kartLageForAnalys(), Boolean(vyBas.lage_idag))
        : "";
    const laddText =
      vektorLaddar.size > 0
        ? `Hämtar ${[...vektorLaddar].map(vektorEtikett).join(", ")} …`
        : "";
    const felLista = [...vektorFelPerSlag.values()];
    const delar = [basText, laddText, ...felLista].filter(Boolean);
    return { text: delar.join(" "), arFel: felLista.length > 0, arVarning: arKontrollVatten && felLista.length === 0 };
  };

  const uppdateraLagerNot = (): void => {
    const notEl = rot.querySelector<HTMLElement>("#ar-lager-not");
    if (!notEl) return;
    const { text, arFel, arVarning } = lagerNotData();
    notEl.textContent = text;
    notEl.hidden = text.length === 0;
    notEl.classList.toggle("flode-fel", arFel);
    notEl.classList.toggle("ar-lager-varning", arVarning);
    notEl.classList.toggle("flode-dampad", !arFel && !arVarning);
  };

  const laddaVektorer = async (): Promise<void> => {
    const onskade: ReadonlySet<VektorSlag> =
      lage === "redigera" ? (visaFlodeUnder ? new Set<VektorSlag>(["flodesvagar"]) : new Set<VektorSlag>()) : vektorPa;
    const tillgangliga = new Set<VektorSlag>(tillgangligaVektorer());
    const behov = [...tillgangliga].filter((s) => onskade.has(s));

    // Avbryt inaktuella förfrågningar (t.ex. när reglaget flyttas eller ett lager släcks).
    for (const [slag, post] of [...vektorAvbryt.entries()]) {
      const ny = onskade.has(slag) && tillgangliga.has(slag) ? vektorNyckel(slag) : null;
      if (!ny || ny !== post.nyckel) {
        post.ctrl.abort();
        vektorAvbryt.delete(slag);
        vektorLaddar.delete(slag);
      }
    }
    for (const slag of [...vektorFelPerSlag.keys()]) {
      if (!onskade.has(slag) || !tillgangliga.has(slag)) vektorFelPerSlag.delete(slag);
    }

    const uppdrag: Promise<void>[] = [];
    for (const slag of behov) {
      const nyckel = vektorNyckel(slag);
      if (!nyckel || !vy) continue;
      if (vektorCache.has(nyckel)) {
        vektorFelPerSlag.delete(slag);
        continue;
      }
      if (vektorAvbryt.get(slag)?.nyckel === nyckel) continue;
      const [kid, sc] = nyckel.split("|") as [string, string];
      const ctrl = new AbortController();
      vektorAvbryt.set(slag, { nyckel, ctrl });
      vektorLaddar.add(slag);
      vektorFelPerSlag.delete(slag);
      uppdrag.push(
        (async () => {
          try {
            const svar = await lasJson<VektorSvar>(
              vektorUrl(kid, sc, slag, vektorGrans[slag] ?? null),
              { signal: ctrl.signal },
            );
            if (avslutad || vektorAvbryt.get(slag)?.ctrl !== ctrl) return;
            vektorAvbryt.delete(slag);
            vektorLaddar.delete(slag);
            vektorCache.set(nyckel, svar);
            sattVektorer();
            ritaTecken();
            uppdateraLagerNot();
          } catch (e) {
            if (avslutad || arAvbrutetVektorFel(e, ctrl.signal)) return;
            if (vektorAvbryt.get(slag)?.ctrl !== ctrl) return;
            vektorAvbryt.delete(slag);
            vektorLaddar.delete(slag);
            vektorFelPerSlag.set(
              slag,
              `${vektorEtikett(slag)[0]?.toUpperCase()}${vektorEtikett(slag).slice(1)} gick inte att hämta: ${e instanceof Error ? e.message : String(e)}`,
            );
            uppdateraLagerNot();
          }
        })(),
      );
    }
    uppdateraLagerNot();
    if (uppdrag.length > 0) await Promise.all(uppdrag);
  };

  const rollerNu = (): LagerVal[] => {
    if (!vyBas || !r || amne === "mikroklimat" || amne === "buller") return [];
    return amne === LUFT_AMNE ? luftLagerVal(r.lager, vyBas) : lagerVal(r.lager, vyBas, amne);
  };
  const tillampaVal = (): ProjektVy | null => (vyBas ? vyMedVal(vyBas, rollerNu().find((x) => x.nyckel === valtNyckel) ?? null) : null);

  /** Snabbknappar för vatten- och analyslager (inte dolda i en lång <select>). */
  const snabbLagerHtml = (): string => {
    if (!r || !vyBas) return "";
    const roller = rollerNu();
    const aktivNyckel = valtNyckel ?? forvaldNyckel(roller, vyBas);
    const vektorer = vektorerForResultat();
    if (roller.length === 0 && vektorer.length === 0) return "";
    const matt = roller.filter((x) => !x.kontroll);
    const kontroller = roller.filter((x) => x.kontroll);
    const knappar: string[] = [];
    for (const m of matt) {
      const etikett = ROLL_SNABBNAMN[m.roll] ?? m.etikett;
      knappar.push(`<button type="button" data-lager-val="${esc(m.nyckel)}" aria-pressed="${m.nyckel === aktivNyckel}">${esc(etikett)}</button>`);
    }
    for (const v of vektorer) {
      const kort = v.slag === "sankor" ? "Sänkor" : v.slag === "flodesvagar" ? "Flödesvägar" : v.slag === "rannsten_flode" ? "Rännstenar" : v.namn;
      knappar.push(`<button type="button" data-vektor-knapp="${v.slag}" aria-pressed="${vektorPa.has(v.slag)}" title="${esc(v.hjalp)}">${esc(kort)}</button>`);
    }
    for (const k of kontroller) {
      knappar.push(`<button type="button" data-lager-val="${esc(k.nyckel)}" aria-pressed="${k.nyckel === aktivNyckel}">${esc(k.etikett)}</button>`);
    }
    const gransRader = vektorer
      .filter((x) => vektorPa.has(x.slag) && GRANSER[x.slag])
      .map((x) => {
        const g = GRANSER[x.slag]!;
        const v = vektorGrans[x.slag] ?? g.forval;
        return `<label class="ar-grans-rad">${esc(g.namn)} <input type="range" min="0" max="100" value="${gransPos(g, v)}" data-grans="${x.slag}" aria-label="${esc(g.namn)}" /> <output data-grans-ut="${x.slag}">${formateraHogst(v, 1)} ${esc(g.enhet)}</output></label>`;
      })
      .join("");
    const notData = lagerNotData();
    const notKlass = notData.arFel
      ? "flode-liten flode-fel"
      : notData.arVarning
        ? "flode-liten ar-lager-varning"
        : "flode-liten flode-dampad";
    return `<div class="ar-lagerblock" aria-label="Kartlager">
      <span class="ar-sektion-rubrik">Kartlager</span>
      <div class="ar-lagerknappar" role="group" aria-label="${esc(lagerGruppEtikettForAmne(amne))}">${knappar.join("")}</div>
      ${gransRader ? `<div class="ar-grans-rader">${gransRader}</div>` : ""}
      <p class="${notKlass}" id="ar-lager-not" role="status" aria-live="polite"${notData.text ? "" : " hidden"}>${esc(notData.text)}</p>
    </div>`;
  };

  // ---------- Teckenförklaring (kompakt, minimerbar, alltid med 1-radig regnetikett) ----------
  const ritaTecken = (): void => {
    const tecken = el("ar-tecken");
    if (lage === "redigera" && !visaVattenUnder) {
      tecken.hidden = true;
      return;
    }
    const fore = lagerPost(r, vy?.fore_lager ?? null);
    const efter = lagerPost(r, vy?.efter_lager ?? null);
    const skill = lagerPost(r, vy?.skillnad_lager ?? null);
    const kl = kartLageForAnalys();
    const tl = kl === "fore" || kl === "svep" ? fore : kl === "efter" ? efter : skill;
    const fasad = lager3dLista.hamta("3d:fasad");
    const fasadTand = lage === "3d" && Boolean(fasad?.synlig) && !fasad?.ejTillgangligt;
    const harDjupSkala = Boolean(tl && vy && amne === "vatten" && tl.roll === "max_djup" && tl.typ !== "skillnad");
    const vektorHtml = lage === "3d" ? [] : aktivaVektorer().map((v) => vektorTeckenHtml(v, harDjupSkala));
    const regnRad = korningRegnRad();
    const harVarmelager = Boolean(amne === "mikroklimat" && aktivtVarmelager && lage !== "3d" && lage !== "redigera");
    const harBullerlager = Boolean(amne === "buller" && aktivtBullerlager && lage !== "3d" && lage !== "redigera");
    const tom = (!tl || !vy) && !fasadTand && !harVarmelager && !harBullerlager && vektorHtml.length === 0;
    tecken.hidden = tom;
    if (tom) return;

    tecken.classList.toggle("ar-tecken-minimerad", uiLage.teckenMinimerad);
    if (uiLage.teckenMinimerad) {
      tecken.innerHTML = `<button type="button" class="ar-tecken-oppna" id="ar-tecken-vaxla" aria-expanded="false" aria-label="Visa teckenförklaring">Teckenförklaring ▸</button>`;
      return;
    }

    let html = "";
    if (harBullerlager && aktivtBullerlager) {
      html += bullerLagerTeckenHtml(aktivtBullerlager);
    } else if (harVarmelager && aktivtVarmelager) {
      html += varmelagerTeckenHtml(aktivtVarmelager);
    } else {
      if (regnRad && (tl || vektorHtml.length > 0)) {
        html += `<span class="ar-tecken-regn">${esc(regnRad)}</span>`;
      }
      if (tl && vy) {
        const namn =
          lage === "redigera"
            ? amne === "vatten" && tl.roll === "max_djup"
              ? "Nulägets vattendjup (underlag)"
              : `Nulägets ${tl.etikett.toLowerCase()} (underlag)`
            : lage === "skillnad"
              ? `Skillnad i ${tl.etikett.toLowerCase()}`
              : tl.etikett;
        const takKryss = lage !== "3d" && lage !== "redigera" && tl.tak_maskat ? `<label class="flode-kryss"><input type="checkbox" id="ar-visa-tak" ${visaTak ? "checked" : ""}/> Visa värden även på tak</label>` : "";
        const osakFore = (amne === "vatten" || amne === "dagsljus") && osakPa.has(amne);
        html += teckenHtml(tl, namn, osakFore) + takKryss;
      }
    }
    if (fasadTand) html += fasadForklaringHtml("skillnad", Boolean(skill));
    html += vektorHtml.map((x) => `<div class="flode-vektortecken">${x}</div>`).join("");
    tecken.innerHTML = `<div class="ar-tecken-huvud"><span class="ar-tecken-titel">Teckenförklaring</span><button type="button" class="ar-min-knapp" id="ar-tecken-vaxla" aria-expanded="true" aria-label="Minimera teckenförklaring" title="Minimera teckenförklaring">−</button></div><div class="ar-tecken-kropp">${html}</div>`;
  };

  // ---------- Avläsning (vattendjup tydligt i cm + m, samt Mark RH 2000) ----------
  const markhojd = markhojdAvlasning(
    (e, n) => hamtaMarkhojd(projekt, e, n),
    (text) => {
      hojdText = kortMarkhojdText(text);
      ritaAvlasning();
    },
  );
  avbryt.push(() => markhojd.avsluta());

  const ritaAvlasning = (): void => {
    const sankaEl = el("ar-sanka");
    if (sankaHtml && lage !== "3d" && lage !== "redigera" && vektorPa.has("sankor")) {
      sankaEl.innerHTML = `<button type="button" class="ar-kort-stang" id="ar-sanka-stang" aria-label="Stäng sänkainformation" title="Stäng">×</button>${sankaHtml}`;
      sankaEl.hidden = false;
    } else {
      sankaEl.hidden = true;
      sankaEl.innerHTML = "";
    }

    el("ar-centrum-sikte").hidden = !centrumAktiv || lage === "3d";
    renderaAvlasningElement(el("ar-avl"), {
      senasteAvl,
      vy,
      lage,
      amne,
      visaTak,
      treDText,
      hojdText,
      punktEtikett: centrumAktiv ? centrumEtikett : null,
      tomText: centrumAktiv ? "Inget beräknat kartvärde i denna punkt" : null,
    });
  };

  const planeraAvlasning = (e: number | null, n: number | null, kallaLage: "pekare" | "centrum" = "pekare"): void => {
    if (kallaLage === "pekare") {
      if (e !== null && n !== null) {
        centrumAktiv = false;
        centrumEtikett = null;
      } else if (centrumAktiv) {
        return;
      }
    } else if (e !== null && n !== null) {
      centrumAktiv = true;
      centrumEtikett = formateraKartcentrumEtikett(e, n);
      treDText = null;
    }
    markhojd.ror(e, n);
    if (avlTimer !== null) window.clearTimeout(avlTimer);
    avlTimer = null;
    const v = vy;
    const visaRasterAvl = lage !== "redigera" || visaVattenUnder;
    if (e === null || n === null || !v || !r || !visaRasterAvl) {
      avlSeq += 1;
      senasteAvl = null;
      ritaAvlasning();
      return;
    }
    const hamtaNu = (): void => {
      const nr = ++avlSeq;
      hamtaAvlasning(v.korning_id, v.skillnad_lager, e, n).then(
        (svar) => {
          if (nr === avlSeq && !avslutad) {
            senasteAvl = svar;
            ritaAvlasning();
          }
        },
        () => {
          if (nr === avlSeq && !avslutad) {
            senasteAvl = null;
            ritaAvlasning();
          }
        },
      );
    };
    if (kallaLage === "centrum") {
      ritaAvlasning();
      hamtaNu();
    } else {
      avlTimer = window.setTimeout(hamtaNu, 90);
    }
  };

  const tangentbordsAvlasning = skapaTangentbordsAvlasning({
    hamtaVy: () => a?.hamtaVy() ?? null,
    settVy: (nyVy) => {
      a?.settVy(nyVy);
      b?.settVy(nyVy);
      if (lage === "redigera") planeraRitning();
    },
    meterPerPx: () => a?.meterPerPx() ?? 1,
    lasAv: (e, n) => planeraAvlasning(e, n, "centrum"),
  });

  const lasAvKartcentrum = (): void => {
    tangentbordsAvlasning.lasCentrum();
  };

  const visaSanka = (e: number, n: number): void => {
    if (mat.aktiv || !vy || lage === "3d" || lage === "redigera" || !vektorPa.has("sankor")) return;
    const nr = ++sankaSeq;
    lasJson<SankaSvar>(sankaUrl(vy.korning_id, e, n, vektorScenario(r?.lager ?? [], vy, kartLageForAnalys()))).then(
      (svar) => {
        if (nr !== sankaSeq || avslutad) return;
        sankaHtml = sankaKortHtml(svar);
        ritaAvlasning();
      },
      () => {
        if (nr !== sankaSeq || avslutad) return;
        sankaHtml = null;
        ritaAvlasning();
      },
    );
  };

  // ---------- Kartan & synkning ----------
  const rasterAv = (id: string, lp: LagerPost | null, synlig: boolean, opacitet = 0.85, overrideUrl?: string): RasterLager[] =>
    lp
      ? [
          {
            id,
            opacitet,
            synlig,
            kalla: {
              slag: "bild",
              url: overrideUrl ?? (visaTak && lp.png_med_tak ? lp.png_med_tak : lp.png),
              utbredning: lp.utbredning,
              skarpa: true,
            },
          },
        ]
      : [];

  const byggnaderFor = (lp: LagerPost | null): VyByggnad[] => {
    if (lage === "redigera" && info) return byggnaderMedForslag(info, t.dok);
    if (r?.byggnader && lp) {
      const sid = lp.typ === "skillnad" ? (lp.efter ?? lp.scenario) : lp.scenario;
      const p = r.byggnader.scenarier[sid];
      if (p) return p.map((x) => ({ ring: x.ring, hal: x.hal, status: x.status }));
    }
    return (info?.byggnader ?? []).map((x) => ({ ring: x.ring, hal: x.hal, status: "oforandrad" as const }));
  };

  const overfallStil = (): { synlig: boolean; opacitet: number } => {
    const l = kartLager.hamta(OVERFALL_LAGER_ID);
    return { synlig: amne === "vatten" && osakPa.has("vatten") && lage !== "redigera" && Boolean(l?.synlig), opacitet: l?.opacitet ?? 1 };
  };

  const synkaKarta = (): void => {
    if (avslutad || !a || !info || !bas) return;
    const fore = lagerPost(r, vy?.fore_lager ?? null);
    const efter = lagerPost(r, vy?.efter_lager ?? null);
    const skill = lagerPost(r, vy?.skillnad_lager ?? null);
    const delat = lage === "svep" && Boolean(fore && efter);

    let visad: LagerPost | null = null;
    let rasterOpacitet = rasterOpacitetVal;
    if (lage === "nulage" || lage === "svep") visad = fore;
    else if (lage === "efter") visad = efter;
    else if (lage === "skillnad") visad = skill;
    else if (lage === "redigera" && visaVattenUnder) {
      visad = fore;
      rasterOpacitet = 0.45;
    }

    const tidStegUrl =
      amne === "vatten" && tidsserie && tidTillstand.aktivtSteg !== null && lage !== "3d" && lage !== "skillnad"
        ? tidsserie.tidssteg[tidTillstand.aktivtSteg]?.png_url
        : undefined;
    let ekoOverrideUrl: string | undefined;
    let ekoOverrideUtbredning: Utbredning | undefined;
    if (amne === "ekologi" && ekologi?.lager && lage !== "3d" && lage !== "redigera") {
      const ref =
        ekoLagerVal === "kansighet"
          ? lage === "nulage" || lage === "svep"
            ? ekologi.lager.kansighet_fore
            : lage === "efter"
              ? ekologi.lager.kansighet_efter ?? ekologi.lager.kansighet_fore
              : ekologi.lager.kansighet_skillnad ?? ekologi.lager.kansighet_fore
          : !visad
            ? lage === "nulage" || lage === "svep"
              ? ekologi.lager.stromtathet_fore
              : lage === "efter"
                ? ekologi.lager.stromtathet_efter ?? ekologi.lager.stromtathet_fore
                : ekologi.lager.stromtathet_skillnad ?? ekologi.lager.stromtathet_fore
            : null;
      if (ref?.lager_id) {
        const hittad = lagerPost(r, ref.lager_id);
        if (hittad) visad = hittad;
      }
      if (ref?.png && (!visad || visad.id !== ref.lager_id)) {
        ekoOverrideUrl = ref.png;
        ekoOverrideUtbredning = ref.utbredning;
      }
    }
    const bullerVisad = amne === "buller" ? aktivtBullerlager : null;
    const varmeVisad = amne === "mikroklimat" ? aktivtVarmelager : null;
    const ovan: RasterLager[] =
      lage === "3d"
        ? []
        : bullerVisad && lage !== "redigera"
          ? [{ id: "res:visad", opacitet: rasterOpacitet, synlig: true, kalla: { slag: "bild", url: bullerVisad.png, utbredning: bullerVisad.utbredning, skarpa: true } }]
          : varmeVisad && lage !== "redigera"
            ? [{ id: "res:visad", opacitet: rasterOpacitet, synlig: true, kalla: { slag: "bild", url: varmeVisad.png, utbredning: varmeVisad.utbredning, skarpa: true } }]
            : !visad && ekoOverrideUrl && ekoOverrideUtbredning
              ? [{ id: "res:visad", opacitet: rasterOpacitet, synlig: true, kalla: { slag: "bild", url: ekoOverrideUrl, utbredning: ekoOverrideUtbredning, skarpa: true } }]
              : rasterAv("res:visad", visad, true, rasterOpacitet, ekoOverrideUrl ?? tidStegUrl);
    bas.synka = () => a?.settLager([...basLagerListan, ...ovan]);
    bas.synka();

    const medVarden = lage !== "3d" && lage !== "redigera" && visaTak && Boolean(visad?.png_med_tak);
    const byggStil = bas.byggnadStil();
    const bStil = { synlig: byggStil.synlig, opacitet: medVarden ? byggStil.opacitet * 0.25 : byggStil.opacitet };
    a.settByggnadStil(bStil);
    a.settPlanStil(bas.planStil());
    a.settOverfall(overfall);
    a.settOverfallStil(overfallStil());
    const poly = r?.projekt_avgransning?.polygon_3011 ?? info.avgransning_polygon_3011 ?? null;
    a.settProjektPolygon(poly);
    a.settByggnader(byggnaderFor(lage === "svep" ? fore : visad));

    const notis =
      lage === "skillnad" && !skill && Boolean(fore) && !vy?.lage_idag
        ? "Ingen skillnad för det här lagret: välj Nuläge eller Efter"
        : lage !== "3d" && lage !== "redigera" && !visaTak && visad?.tak_maskat
          ? "Ljusgrå yta: tak, inte mark"
          : null;
    a.settNotis(null);
    const notisEl = el("ar-notis");
    notisEl.hidden = !notis;
    notisEl.textContent = notis ?? "";

    // Visa underlagsreglaget i Redigera förslag när nulägeslager finns (domänkorrekt etikett, U5d)
    const vattenUnderBar = el("ar-vattenunder");
    vattenUnderBar.hidden = lage !== "redigera" || !fore;
    el("ar-vatten-under-text").textContent = underlagEtikettForAmne(amne);
    el("ar-flode-under-rad").hidden = amne !== "vatten" || !tillgangligaVektorer().includes("flodesvagar");

    el("ar-b").hidden = !delat;
    el("ar-svep-rad").hidden = !delat;
    if (delat) {
      if (!b) {
        b = new Kartmotor(el("ar-b"), a.hamtaVy());
        b.onVyAndrad = () => b && a?.settVy(b.hamtaVy());
        b.onMarkor = planeraAvlasning;
      }
      b.settLager([...basLagerListan, ...rasterAv("res:efter", efter, true, rasterOpacitet)]);
      b.settByggnadStil(bStil);
      b.settOverfall(overfall);
      b.settOverfallStil(overfallStil());
      b.settProjektPolygon(poly);
      b.settByggnader(byggnaderFor(efter));
      b.settMatLinje(mat.linje());
      el("ar-b").style.clipPath = `inset(0 0 0 ${Math.round(svep * 1000) / 10}%)`;
    } else if (b) {
      b.avsluta();
      b = null;
    }

    if (lage !== "redigera") {
      a.settRit(TOM_RITVY);
      a.settSkarv(null, false);
    }
    synkaMatOchBullerLinje();
    const sekLinje = sektion.linje()?.punkter.map((p) => [p.x, p.y] as [number, number]) ?? (sektion.punkter.length >= 2 ? sektion.punkterSomPar() : null);
    a.settSektionLinje(sekLinje);
    b?.settSektionLinje(sekLinje);
    sattVektorer();
    ritaTecken();
    ritaVaxel();
    ritaRegnOchKor();
    ritaBottenDock();
    vy3d?.uppdatera?.();
    vy3dDock?.uppdatera?.();
    if (vektorPa.size > 0 || (lage === "redigera" && visaFlodeUnder)) void laddaVektorer();
  };

  const ritaVaxel = (): void => {
    const efterFinns = harEfter();
    for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("#ar-vaxel [data-lage]"))) {
      const id = k.dataset.lage as ArbetsLage;
      k.setAttribute("aria-pressed", String(id === lage));
      const behovEfter = id === "efter" || id === "skillnad" || id === "svep";
      k.disabled = behovEfter && !efterFinns;
      k.title = behovEfter && !efterFinns ? "Beräkna ett förslag först" : "";
    }
    const redigera = lage === "redigera";
    el("ar-verktyg").hidden = !redigera;
    el("ar-skikt-panel").hidden = redigera;
    rot.querySelector<HTMLElement>(".ar-karta")?.classList.toggle("har-skikt-panel", !redigera && !skiktHopfalld);
    el("ar-analys-del").hidden = redigera;
    el("ar-redigera-del").hidden = !redigera;
    el("ar-mat-knapp").setAttribute("aria-pressed", String(mat.aktiv));
    el("ar-sektion-knapp").setAttribute("aria-pressed", String(sektion.aktiv));
    el("ar-redigera-3d-knapp").setAttribute("aria-pressed", String(redigera3dPa));
    visaHjalp();
  };

  const visaHjalp = (): void => {
    const aktivRoll = t.dok.lagerAv(t.dok.aktivtLager)?.roll;
    const bullerRitar = Boolean(amne === "buller" && bullerKontroller?.arRitarVag());
    renderaHjalpElement(el("ar-hjalp"), {
      treDFel,
      sektionAktiv: sektion.aktiv,
      sektionText: sektion.hjalpText(),
      matAktiv: mat.aktiv || bullerRitar,
      matText: bullerRitar ? (bullerKontroller?.hjalpText() ?? "") : mat.hjalpText(),
      redigeraAktiv: lage === "redigera" && Boolean(verktyg),
      meddelande,
      verktygText: verktyg ? hjalpText(verktyg, aktivRoll) : "",
    });
  };
  visaHjalpRef = visaHjalp;

  // ---------- 3D-vy ----------
  const kartvy3d = (): { e: number; n: number; mpp: number; hojdPx: number } => {
    const v = (a as Kartmotor).hamtaVy();
    const [e, n] = wgs84TillSweref(v.lng, v.lat);
    return { e, n, mpp: meterPerPxFranZoom(v.zoom, v.lat), hojdPx: el("ar-a").clientHeight || 600 };
  };
  const koppla3dAterkoppling = () =>
    skapa3dAterkoppling({
      avlasning: planeraAvlasning,
      sattText: (txt) => {
        treDText = txt;
        ritaAvlasning();
      },
      sattFel: (txt) => {
        treDFel = txt;
        visaHjalp();
      },
    });
  lager3dLista.vidAndring(() => ritaTecken());
  lager3dLista.registrera({ id: "geo:byggnader", grupp: "geometri", namn: "Byggnader", synlig: true, opacitet: 1, hjalp: "Byggnaderna i 3D." });
  lager3dLista.registrera({ id: "plan:aktuellt", grupp: "planforslag", namn: "Aktuellt förslag", synlig: true, opacitet: 1, hjalp: "Det du ritar just nu." });

  const sattRedigera3d = (pa: boolean): void => {
    redigera3dPa = pa && lage === "redigera";
    const ruta = el("ar-redigera-3d");
    const yta3d = el("ar-redigera-3d-yta");
    ruta.hidden = !redigera3dPa;
    el("ar-redigera-3d-knapp").setAttribute("aria-pressed", String(redigera3dPa));
    if (redigera3dPa && !vy3dDock && a && info) {
      treDText = null;
      treDFel = null;
      visaHjalp();
      lager3dLista.sattSynlig(VY3D_ID, true);
      vy3dDock = skapa3dVy({
        projekt,
        lager: lager3dLista,
        info,
        dok: t.dok,
        takform: () => t.takform,
        vy: () => vy,
        resultat: () => r,
        lage: () => "fore",
        svep: () => svep,
        arRitatResultat: () => kalla === "ritat",
        kartvy: () => {
          const v = (a as Kartmotor).hamtaVy();
          const [e, n] = wgs84TillSweref(v.lng, v.lat);
          return { e, n, mpp: meterPerPxFranZoom(v.zoom, v.lat), hojdPx: yta3d.clientHeight || 220 };
        },
        settKartvy: () => undefined,
        ...koppla3dAterkoppling(),
      }).montera(yta3d);
    } else if (!redigera3dPa && vy3dDock) {
      vy3dDock.avmontera();
      vy3dDock = null;
      treDText = null;
      treDFel = null;
      planeraAvlasning(null, null);
      visaHjalp();
    }
  };

  const stallLage = (nytt: ArbetsLage): void => {
    if ((nytt === "efter" || nytt === "skillnad" || nytt === "svep") && !harEfter()) return;
    const till3d = nytt === "3d";
    lage = nytt;
    if (lage === "redigera" || lage === "3d") bullerKontroller?.avbrytVagRitning();
    if (lage !== "redigera" && redigera3dPa) sattRedigera3d(false);
    if (till3d && !vy3d && a && info) {
      treDText = null;
      treDFel = null;
      planeraAvlasning(null, null);
      const harSkill = Boolean(vy && lagerPost(r, vy.skillnad_lager));
      for (const id of ["fore", "efter", "skillnad"]) lager3dLista.sattSynlig(`res:${id}`, id === (harSkill ? "skillnad" : "fore"));
      lager3dLista.sattSynlig(VY3D_ID, true);
      el("ar-3d").hidden = false;
      el("ar-a").style.visibility = "hidden";
      vy3d = skapa3dVy({
        projekt,
        lager: lager3dLista,
        info,
        dok: t.dok,
        takform: () => t.takform,
        vy: () => vy,
        resultat: () => r,
        lage: () => (harSkill ? "skillnad" : "fore"),
        svep: () => svep,
        arRitatResultat: () => kalla === "ritat",
        kartvy: kartvy3d,
        settKartvy: (e, n, mpp) => {
          const [lng, lat] = swerefTillWgs84(e, n);
          a?.settVy({ lng, lat, zoom: zoomFranMeterPerPx(mpp, lat) });
        },
        ...koppla3dAterkoppling(),
      }).montera(el("ar-3d"));
    } else if (!till3d && vy3d) {
      vy3d.avmontera();
      vy3d = null;
      treDText = null;
      treDFel = null;
      planeraAvlasning(null, null);
      el("ar-3d").hidden = true;
      el("ar-a").style.visibility = "";
      window.requestAnimationFrame(() => a?.matOm());
    }
    void laddaTidsserie();
    gyfUtciKontroller.synkaVarmelager();
    bullerKontroller?.synkaBullerlager();
    synkaKarta();
    if (lage === "redigera") planeraRitning();
    else ritaAnalysPanel();
  };

  // ---------- Analyspanel ----------
  const underlag = (): Underlag => {
    const fore = lagerPost(r, vy?.fore_lager ?? null);
    const efter = lagerPost(r, vy?.efter_lager ?? null);
    const tab = r?.tabeller.find((x) => x.analys === (amne === "vatten" ? "skyfall" : "sol"));
    const rad = (sc: string | undefined): Record<string, unknown> | undefined => tab?.rader.find((x) => x.scenario === sc)?.varden;
    const num = (o: Record<string, unknown> | undefined, k: string): number | null => (typeof o?.[k] === "number" ? (o[k] as number) : null);
    const rf = rad(fore?.scenario);
    const re = rad(efter?.scenario);
    return {
      troskel: mark?.troskel ?? null,
      siffror: mark?.siffror ?? null,
      ekologi,
      foreDjup_m: num(rf, "max_djup_m"),
      efterDjup_m: num(re, "max_djup_m"),
      foreLagrat_m3: num(rf, "lagrat_m3"),
      efterLagrat_m3: num(re, "lagrat_m3"),
      foreUt_m3: num(rf, "ut_m3"),
      efterUt_m3: num(re, "ut_m3"),
    };
  };

  const osakerhetVisning = (): OsakerhetVisning => {
    const a2: Amne = amne === "vatten" || amne === "dagsljus" ? amne : "ekologi";
    const arOsakAmne = amne === "vatten" || amne === "dagsljus";
    return {
      pa: arOsakAmne && osakPa.has(a2),
      laddar: osakLaddar,
      tal: osakerhetTal(a2, osak),
      rader: amne === "vatten" ? overfallRader(osak.bifurkation) : [],
      fel: osak.fel,
      overfallLager: amne === "vatten" && osak.bifurkation ? { synlig: Boolean(kartLager.hamta(OVERFALL_LAGER_ID)?.synlig), antal: overfall.length } : null,
    };
  };

  const laddaOsakerhet = async (ny: ResultatAmne): Promise<void> => {
    if (ny !== "vatten" && ny !== "dagsljus") return;
    osakPa.add(ny);
    osak.fel = [];
    const behovs = ny === "vatten" ? !osak.vatten || !osak.bifurkation : !osak.svf;
    if (!behovs) {
      ritaAnalysPanel();
      synkaKarta();
      return;
    }
    osakLaddar = true;
    ritaAnalysPanel();
    const msg = (e: unknown): string => (e instanceof Error ? e.message : String(e));
    if (ny === "vatten") {
      const [v, bRes] = await Promise.allSettled([hamtaVattenIntervall(projekt), hamtaBifurkationer(projekt)]);
      if (v.status === "fulfilled") osak.vatten = v.value;
      else osak.fel.push(`Skyfallsintervallet gick inte att hämta: ${msg(v.reason)}`);
      if (bRes.status === "fulfilled") {
        osak.bifurkation = bRes.value;
        overfall = ritadeOverfall(bRes.value.punkter);
        kartLager.registrera(overfallLager(overfall.length));
      } else osak.fel.push(`Överfallen gick inte att hämta: ${msg(bRes.reason)}`);
    } else {
      try {
        osak.svf = await hamtaSvfIntervall(projekt);
      } catch (e) {
        osak.fel.push(`Soluppskattningen gick inte att hämta: ${msg(e)}`);
      }
    }
    osakLaddar = false;
    if (avslutad) return;
    ritaAnalysPanel();
    ritaRegnOchKor();
    synkaKarta();
  };

  const samlaTillgangligaVyer = (): Set<string> => {
    const s = new Set<string>();
    for (const v of [...(projektSvar?.vyer ?? []), ...t.ritVyer, ...t.lageVyer]) {
      if (v?.avsnitt) s.add(v.avsnitt);
    }
    return s;
  };

  ritaSkiktPanel = (): void => {
    if (avslutad) return;
    const panel = el("ar-skikt-panel");
    const huvud = el("ar-skikt-huvud");
    const kropp = el("ar-skikt-kropp");
    const flikarEl = el("ar-flikar");
    const lagerEl = el("ar-skikt-lager");
    const opSlider = rot.querySelector<HTMLInputElement>("#ar-raster-opacitet");
    const opUt = rot.querySelector<HTMLElement>("#ar-raster-opacitet-ut");

    const aktivFokusAmne =
      document.activeElement instanceof HTMLElement && flikarEl.contains(document.activeElement)
        ? document.activeElement.dataset.amne
        : null;

    panel.classList.toggle("ar-skikt-hopfalld", skiktHopfalld);
    kropp.hidden = skiktHopfalld;
    huvud.innerHTML = skiktHopfalld
      ? `<button type="button" class="ar-skikt-oppna" id="ar-skikt-vaxla" aria-expanded="false" aria-controls="ar-skikt-kropp" aria-label="Visa skiktpanel">Skikt · ${esc(AMNESNAMN[amne])} ▸</button>`
      : `<span class="ar-skikt-titel">Planeringsskikt</span><button type="button" class="ar-min-knapp ar-skikt-vaxla" id="ar-skikt-vaxla" aria-expanded="true" aria-controls="ar-skikt-kropp" aria-label="Fäll ihop skiktpanel" title="Fäll ihop skiktpanel">−</button>`;

    const gyfVs = gyfUtciKontroller.status();
    const bl = bullerKontroller?.status() ?? { laddar: false, harSvar: false, ritarVag: false };
    const statusIndata: DomanStatusIndata = {
      aktivDoman: amne,
      pagarK20: arPagaende(t),
      harVyer: samlaTillgangligaVyer(),
      harNmd: harEkologiUnderlag(info),
      ekologiHarSvar: ekologi !== null,
      gyfLaddar: gyfVs.gyfLaddar,
      gyfHarSvar: gyfVs.gyfHarSvar,
      vsLaddar: gyfVs.vsLaddar,
      vsHarSvar: gyfVs.vsHarSvar,
      bullerLaddar: bl.laddar,
      bullerHarSvar: bl.harSvar,
    };
    flikarEl.innerHTML = skiktDomanerHtml(amne, statusIndata);
    if (aktivFokusAmne) {
      flikarEl.querySelector<HTMLElement>(`[data-amne="${aktivFokusAmne}"]`)?.focus();
    }

    lagerEl.innerHTML = skiktLagerHtml({
      basSynlig: {
        osm: Boolean(kartLager.hamta(OSM_ID)?.synlig),
        hojdkurvor: Boolean(kartLager.hamta(HOJDKURVOR_ID)?.synlig),
        byggnader: Boolean(kartLager.hamta(BYGGNADER_ID)?.synlig),
      },
      tillgangligaVektorer: tillgangligaVektorer(),
      aktivaVektorer: vektorPa,
    });

    const opPct = Math.round(klammaRasterOpacitet(rasterOpacitetVal) * 100);
    if (opSlider && document.activeElement !== opSlider) opSlider.value = String(opPct);
    if (opUt) opUt.textContent = `${opPct}\u202F%`;
    rot.querySelector<HTMLElement>(".ar-karta")?.classList.toggle("har-skikt-panel", lage !== "redigera" && !skiktHopfalld);
  };

  const ekoLagerVaxelHtml = (): string => {
    if (amne !== "ekologi" || !ekologi?.lager) return "";
    const harKansighet = Boolean(
      ekologi.lager.kansighet_fore || ekologi.lager.kansighet_efter || ekologi.lager.kansighet_skillnad,
    );
    if (!harKansighet) return "";
    if (rollerNu().some((x) => x.roll === "kansighet")) return "";
    return `<div class="ar-lagerblock" aria-label="Ekologilager">
      <span class="ar-sektion-rubrik">Ekologilager</span>
      <div class="flode-segment liten" role="group" aria-label="Välj ekologilager">
        <button type="button" data-eko-lager="stromtathet" aria-pressed="${ekoLagerVal === "stromtathet"}">Strömtäthet (flaskhalsar)</button>
        <button type="button" data-eko-lager="kansighet" aria-pressed="${ekoLagerVal === "kansighet"}">Känslighet (åtgärdspotential)</button>
      </div>
      <p class="flode-liten flode-dampad">${
        ekoLagerVal === "kansighet"
          ? "Förbättringspotential (känslighet ∂R/∂G): visar var åtgärder ger störst förbättring av konnektiviteten."
          : "Strömtäthet (flaskhalsar): visar var spridningsflödet koncentreras i landskapet."
      }</p>
    </div>`;
  };

  const dpcTabellHtml = (): string => {
    if (amne !== "ekologi" || !ekologi) return "";
    const rader = dpcTabellRader(ekologi, 5);
    if (rader.length === 0) return "";
    return `<section class="ar-dpc" aria-label="Viktigaste habitatfläckar (dPC)">
      <span class="ar-sektion-rubrik">Viktigaste habitatfläckar (dPC)</span>
      <table class="flode-tabell ar-dpc-tabell">
        <thead><tr><th>Fläck</th><th>Area</th><th>dPC</th><th>Intra</th><th>Flux</th><th>Conn.</th></tr></thead>
        <tbody>${rader
          .map(
            (rad) =>
              `<tr><td>${esc(rad.flack_id)}</td><td>${esc(rad.area)}</td><td><strong>${esc(rad.dpc_procent)}</strong></td><td>${esc(rad.intra)}</td><td>${esc(rad.flux)}</td><td>${esc(rad.connector)}</td></tr>`,
          )
          .join("")}</tbody>
      </table>
    </section>`;
  };

  const ritaAnalysPanel = (): void => {
    if (avslutad) return;
    ritaSkiktPanel();
    gyfUtciKontroller.sattAktivDoman(amne);
    bullerKontroller?.sattAktivDoman(amne);
    const inne = el("ar-innehall");
    if (amne === "mikroklimat" || amne === "buller") {
      inne.hidden = true;
      inne.innerHTML = "";
      return;
    }
    inne.hidden = false;

    const pagar = arPagaende(t);
    const solUtdaterat =
      amne === "dagsljus" &&
      !pagar &&
      !solFel &&
      bedomInstallningsStatus(t, amne, projektSvar?.vyer).solUtdaterat;
    const solBlock =
      amne === "dagsljus"
        ? solValHtml(t.sol, solFel) +
          (solUtdaterat
            ? `<p class="flode-liten flode-dampad ar-sol-utdaterad" role="status">Vald solinställning skiljer sig från visat resultat (eller saknar beräkning).</p>`
            : "")
        : "";
    if (!vy || !r) {
      if (amne === LUFT_AMNE) {
        inne.innerHTML = pagar
          ? `<h1 class="flode-h" id="ar-rubrik">Beräknar …</h1><p class="flode-dampad" role="status">${esc(t.korning?.text ?? "")}</p>`
          : luftTomPanelHtml({
              harMall: aktivaAnalyser().length > 0,
              rapportHref: flodeAdress(projekt, "rapport"),
            });
        return;
      }
      if (amne === "ekologi") {
        if (info && !harEkologiUnderlag(info)) {
          inne.innerHTML = `<div class="ar-doman-huvud"><h1 class="flode-h" id="ar-rubrik">Ekologi &amp; GYF</h1><span class="ar-skiss-badge">SKISS</span></div>
            <p class="flode-ruta varning flode-liten" id="ar-eko-utan-nmd">${esc(EKOLOGI_UTAN_NMD_TEXT)}</p>`;
          return;
        }
        const natverkValDirekt =
          natverkLista.length > 0
            ? `<label class="flode-liten">Habitatnätverk <select id="ar-natverk" aria-label="Habitatnätverk">${natverkLista.map((n) => `<option value="${esc(n.natverk_id)}"${n.natverk_id === (valtNatverk ?? ekologi?.artprofil_id) ? " selected" : ""}>${esc(n.visningsnamn)}</option>`).join("")}</select></label>`
            : "";
        if (ekologi) {
          const uDirekt = underlag();
          const ekoKallaDirekt = [resultatEtikett(ekologi), kallRad(ekologi), ...antagandeRader(ekologi), intervallForklaring(ekologi)].filter(Boolean);
          const ekoKallaHtmlDirekt =
            ekoKallaDirekt.length > 0
              ? `<ul class="flode-liten flode-dampad" id="rs-eko-kalla">${ekoKallaDirekt.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
              : "";
          const ekoVarningDirekt = [...(ekologi.varningar ?? []), ...(ekologiFel ? [`Ekologisvaret gick inte att hämta: ${ekologiFel}`] : [])];
          const ekoTalDirekt = ekologi.efter ? talListaHtml(nyckeltal("ekologi", uDirekt)) : nulageEkologiTalHtml(uDirekt);
          inne.innerHTML = `${natverkValDirekt}${ekoLagerVaxelHtml()}
            <div class="ar-doman-huvud"><h1 class="flode-h" id="ar-rubrik">Ekologi · Konnektivitet</h1><span class="ar-skiss-badge">SKISS</span></div>
            ${ekoTalDirekt}
            ${dpcTabellHtml()}
            ${ekoKallaHtmlDirekt}
            ${ekoVarningDirekt.length > 0 ? `<p class="flode-liten flode-dampad">${esc(ekoVarningDirekt.join(" "))}</p>` : ""}
            <p class="flode-liten flode-dampad">${nulageTipsHtmlForAmne("ekologi")}</p>`;
          return;
        }
        if (ekologiFel) {
          inne.innerHTML = `${natverkValDirekt}<h1 class="flode-h" id="ar-rubrik">Ekologi &amp; GYF</h1>
            <p class="flode-liten flode-fel" role="alert">Ekologikonnektivitet kunde inte hämtas: ${esc(ekologiFel)}</p>
            <p class="flode-liten flode-dampad">Du kan ändå beräkna Grönytefaktor (GYF) nedan.</p>`;
          return;
        }
      }
      const nulageKnappNamn = korKnappEtiketterForAmne(info, amne).nulage;
      inne.innerHTML = pagar
        ? `${solBlock}<h1 class="flode-h" id="ar-rubrik">Beräknar …</h1><p class="flode-dampad" role="status">${esc(t.korning?.text ?? "")}</p>`
        : `${solBlock}<h1 class="flode-h" id="ar-rubrik">${esc(AMNESNAMN[amne])} – nuläge</h1>
           <p class="flode-dampad">Inga beräknade resultat för ${esc(AMNESNAMN[amne].toLowerCase())} ännu. Tryck <strong>${esc(nulageKnappNamn)}</strong> ovan för att räkna ut projektets befintliga läge, eller byt till <strong>Redigera förslag</strong> för att rita ändringar.</p>`;
      return;
    }

    const harRitat = t.ritVyer.length > 0;
    const harLage = t.lageVyer.length > 0;
    const vyer = vyerFor(kalla);
    const kallKnappar = [
      harRitat ? `<button type="button" data-kalla="ritat" aria-pressed="${kalla === "ritat"}">Ditt förslag</button>` : "",
      harLage ? `<button type="button" data-kalla="lage" aria-pressed="${kalla === "lage"}">Nuläge (eget)</button>` : "",
      (projektSvar?.vyer?.length ?? 0) > 0 ? `<button type="button" data-kalla="projekt" aria-pressed="${kalla === "projekt"}">Projektets exempel</button>` : "",
    ].filter(Boolean);
    const kallVal = kallKnappar.length > 1 ? `<div class="flode-segment liten" role="group" aria-label="Resultatkälla">${kallKnappar.join("")}</div>` : "";
    const lagerBlock = snabbLagerHtml() + ekoLagerVaxelHtml();

    if (amne === LUFT_AMNE) {
      const foreScen = lagerPost(r, vy.fore_lager)?.scenario;
      const efterScen = lagerPost(r, vy.efter_lager)?.scenario;
      inne.innerHTML = `${kallVal}${lagerBlock}${luftPanelHtml({
        korningId: vy.korning_id,
        roller: rollerNu(),
        tal: luftNyckeltal(r.tabeller, foreScen, efterScen),
        las: lasKartanForAmne(amne, vy, kartLageForAnalys(), info?.paahittat !== false),
        lageIdag: kalla === "lage",
      })}`;
      return;
    }

    const u = underlag();
    const regn = amne === "vatten" ? korningRegnRad() : null;
    const vattenAnt = amne === "vatten" ? [...new Set((r.antaganden.vatten ?? []).flatMap((x) => [x.takvatten_text, x.avtappning_text]).filter((x): x is string => Boolean(x)))] : [];
    const natverkVal =
      amne === "ekologi" && natverkLista.length > 0
        ? `<label class="flode-liten">Habitatnätverk <select id="ar-natverk" aria-label="Habitatnätverk">${natverkLista.map((n) => `<option value="${esc(n.natverk_id)}"${n.natverk_id === (valtNatverk ?? ekologi?.artprofil_id) ? " selected" : ""}>${esc(n.visningsnamn)}</option>`).join("")}</select></label>`
        : "";
    const ekoKalla = amne === "ekologi" ? [resultatEtikett(ekologi), kallRad(ekologi), ...antagandeRader(ekologi), intervallForklaring(ekologi)].filter(Boolean) : [];
    const ekoVarning = amne === "ekologi" ? [...(ekologi?.varningar ?? []), ...(ekologiFel ? [`Ekologisvaret gick inte att hämta: ${ekologiFel}`] : [])] : [];

    if (kalla === "lage" || lage === "nulage") {
      const djupTxt = u.foreDjup_m !== null && u.foreDjup_m !== undefined ? formateraDjupCm(u.foreDjup_m) : "–";
      const volTxt = u.foreLagrat_m3 !== null && u.foreLagrat_m3 !== undefined ? `${formatera(u.foreLagrat_m3, 0)} m³` : "–";
      const utTxt = u.foreUt_m3 !== null && u.foreUt_m3 !== undefined ? `${formatera(u.foreUt_m3, 0)} m³` : null;
      const arStatiskVatten = amne === "vatten" && r.antaganden.analyser.some((a) => a.losare === "vatten.statisk_fyllning");
      let nulageTal = "";
      if (amne === "vatten") {
        const utRad = utTxt !== null ? `<div><dt>Avrunnet ut ur området</dt><dd>${esc(utTxt)}</dd></div>` : "";
        const statiskNot = arStatiskVatten
          ? `<p class="flode-liten flode-dampad">Statisk beräkning fyller lågpunkter upp till bräddpunkten; överskottet rinner vidare (Avrunnet). Fulla lågpunkter blir inte djupare vid större regn — välj Flöde i tid (dynamisk) eller 2D lokal tröghet för rinnande flödesdjup.</p>`
          : "";
        nulageTal = `<dl class="flode-tal"><div><dt>Största djup i nuläget</dt><dd>${esc(djupTxt)}</dd></div><div><dt>Stående vatten i lågpunkter</dt><dd>${esc(volTxt)}</dd></div>${utRad}</dl>${statiskNot}`;
      } else if (amne === "ekologi") {
        nulageTal = nulageEkologiTalHtml(u);
      }
      const nulageTipsHtml = nulageTipsHtmlForAmne(amne);
      const ekoKallaHtml =
        ekoKalla.length > 0
          ? `<ul class="flode-liten flode-dampad" id="rs-eko-kalla">${ekoKalla.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
          : "";
      const nulageOsakHtml = amne === "vatten" || amne === "dagsljus" ? osakerhetHtml(osakerhetVisning()) : "";
      const anm = [regn ? `Regn: ${regn}.` : "", ...vattenAnt.map((x) => `${x}.`), ...ekoVarning, `Körning ${vy.korning_id}.`].filter(Boolean).join(" ");
      const visatRegnHtml = regn ? `<p class="flode-liten ar-visat-regn" id="ar-visat-regn"><strong>Visat regn:</strong> ${esc(regn)}</p>` : "";
      inne.innerHTML = `${kallVal}${solBlock}${natverkVal}${lagerBlock}
        <h1 class="flode-h" id="ar-rubrik">Nuläge · ${esc(AMNESNAMN[amne])}</h1>
        ${visatRegnHtml}
        ${nulageTal}
        ${nulageOsakHtml}
        ${dpcTabellHtml()}
        ${ekoKallaHtml}
        <p class="flode-liten flode-dampad">${nulageTipsHtml}</p>
        <p class="flode-liten flode-dampad">${esc(anm)}</p>
        <details class="flode-detalj"><summary>Så läser du kartan</summary><p class="flode-liten flode-dampad">${esc(lasKartanForAmne(amne, vy, "fore", info?.paahittat !== false))}</p></details>`;
      return;
    }

    const tal = nyckeltal(amne, u);
    const jfr = jamforelse(amne, u);
    const ingress = amne === "dagsljus" || amne === "ekologi" ? projektSvar?.avsnitt?.find((x) => x.id === amne)?.ingress : null;
    const raderRegn = [regn ? `Regn: ${regn}.` : "", ...vattenAnt.map((x) => `${x}.`), ingress ?? "", ...ekoVarning, `Körning ${vy.korning_id}.`].filter(Boolean).join(" ");
    const visatRegnHtml = regn ? `<p class="flode-liten ar-visat-regn" id="ar-visat-regn"><strong>Visat regn:</strong> ${esc(regn)}</p>` : "";
    const vyVal =
      vyer.length > 1
        ? `<div class="flode-chips liten" role="group" aria-label="Förändring">${vyer.map((v) => `<button type="button" data-vy="${esc(v.id)}" aria-pressed="${v.id === vy?.id}">${esc(v.efter_namn)}</button>`).join("")}</div>`
        : "";
    inne.innerHTML = `${kallVal}${vyVal}${solBlock}${natverkVal}${lagerBlock}${visatRegnHtml}${kroppHtml({
      slutsats: slutsats(amne, u),
      tal,
      jamforelse: jfr,
      kalla: ekoKalla,
      anmarkning: raderRegn,
      osakerhet: amne === "ekologi" ? null : osakerhetVisning(),
      las: lasKartanForAmne(amne, vy, kartLageForAnalys(), info?.paahittat !== false),
    })}${dpcTabellHtml()}`;
  };

  // ---------- Laddning av resultatvy ----------
  const valjVy = async (ny: ProjektVy | null): Promise<void> => {
    const nr = ++seq;
    if (ny?.id !== vyBas?.id) valtNyckel = null;
    vyBas = ny;
    vy = ny;
    r = undefined;
    mark = null;
    ekologi = null;
    ekoLagerVal = "stromtathet";
    ekologiFel = null;
    if (ny) {
      try {
        const res = cache.get(ny.korning_id) ?? (await hamtaResultat(ny.korning_id));
        cache.set(ny.korning_id, res);
        const m =
          amne === "ekologi" || amne === LUFT_AMNE || amne === "mikroklimat" || amne === "buller" || kalla === "lage" || ny.lage_idag
            ? null
            : await lasJson<MarkSvar>(`/api/korningar/${encodeURIComponent(ny.korning_id)}/marksiffror?lager=${encodeURIComponent(ny.skillnad_lager)}`).catch(() => null);
        if (nr !== seq || avslutad) return;
        r = res;
        vy = tillampaVal() ?? ny;
        mark = m;
      } catch {
        if (nr !== seq) return;
        r = undefined;
      }
    }
    // Ekologi: hämta direkt via skrivlös GET /api/analys/ekologi även när ingen K20-vy finns ännu (om NMD/demokvarteret).
    if (amne === "ekologi" && harEkologiUnderlag(info)) {
      let eko: EkologiMedLager | null = null;
      let ekoFel: string | null = null;
      if (natverkLista.length === 0) {
        natverkLista = await lasJson<{ natverk: typeof natverkLista }>("/api/ekologi/natverk")
          .then((s) => s.natverk.filter((n) => n.korbar && !n.lokalt))
          .catch(() => []);
      }
      const ekoFraga: EkologiAnalysFraga = {
        omrade_id: projekt,
        ...(valtNatverk ? { natverk: valtNatverk } : {}),
        ...(kalla === "ritat" && t.scenario ? { scenario: t.scenario.slug } : {}),
      };
      try {
        eko = await hamtaEkologiAnalys<EkologiMedLager>(ekoFraga);
      } catch (e) {
        ekoFel = e instanceof Error ? e.message : String(e);
      }
      if (nr !== seq || avslutad) return;
      ekologi = eko;
      ekologiFel = ekoFel;
    }
    if ((lage === "efter" || lage === "skillnad" || lage === "svep") && !harEfter()) {
      lage = "nulage";
    }
    void laddaTidsserie();
    gyfUtciKontroller.uppdateraKontext();
    bullerKontroller?.uppdateraKontext();
    cadBimKontroller.uppdateraKontext();
    ritaAnalysPanel();
    ritaRegnOchKor();
    synkaKarta();
  };

  const valjAmne = async (ny: ResultatAmne): Promise<void> => {
    amne = ny;
    valtNyckel = null;
    if (amne !== "buller") bullerKontroller?.avbrytVagRitning();
    gyfUtciKontroller.sattAktivDoman(amne);
    bullerKontroller?.sattAktivDoman(amne);
    if (vyerFor(kalla).length === 0) {
      kalla =
        t.ritVyer.some((v) => v.avsnitt === ny)
          ? "ritat"
          : t.lageVyer.some((v) => v.avsnitt === ny)
            ? "lage"
            : "projekt";
    }
    const onskad = onskadVy ? vyerFor(kalla).find((v) => v.id === onskadVy) : undefined;
    onskadVy = null;
    await valjVy(onskad ?? vyerFor(kalla)[0] ?? null);
    ritaRegnOchKor();
  };

  // ---------- Redigering: ritning, verktyg, markhöjning/sänkning & spara ----------
  const planeraRitning = (): void => {
    if (ritaBegard || lage !== "redigera") return;
    ritaBegard = window.requestAnimationFrame(() => {
      ritaBegard = 0;
      uppdateraRit();
    });
  };

  const knapp = (sel: string): HTMLButtonElement | null => rot.querySelector<HTMLButtonElement>(sel);

  const uppdateraVerktygKnappar = (): void => {
    if (!verktyg) return;
    const v = verktyg;
    const aktivRoll = t.dok.lagerAv(t.dok.aktivtLager)?.roll;
    if (v.verktyg === "valj") huvudVal = "valj";
    else if (v.verktyg === "hus") huvudVal = "hus";
    else if (v.verktyg === "yta" && aktivRoll === "mark") huvudVal = "mark";
    else if (v.verktyg === "yta" && (aktivRoll === "hardgjort" || aktivRoll === "vegetation" || aktivRoll === "vatten")) {
      huvudVal = "friyta";
      friYtaRoll = aktivRoll;
    } else if (v.verktyg === "linje" && aktivRoll === "brytlinje") huvudVal = "mur";
    else if (v.verktyg === "linje" && aktivRoll === "oppning") huvudVal = "oppning";

    for (const hv of ["valj", "hus", "mark", "mur", "oppning", "friyta"] as const) {
      const pa =
        hv === "valj"
          ? v.verktyg === "valj"
          : hv === "hus"
            ? v.verktyg === "hus"
            : hv === "mark"
              ? v.verktyg === "yta" && huvudVal === "mark"
              : hv === "friyta"
                ? v.verktyg === "yta" && huvudVal === "friyta"
                : hv === "mur"
                  ? v.verktyg === "linje" && huvudVal === "mur"
                  : v.verktyg === "linje" && huvudVal === "oppning";
      knapp(`[data-hv="${hv}"]`)?.setAttribute("aria-pressed", String(pa));
    }
    const friYtaBar = el("ar-friyta-roller");
    friYtaBar.hidden = !(v.verktyg === "yta" && huvudVal === "friyta");
    for (const rKnapp of Array.from(friYtaBar.querySelectorAll<HTMLButtonElement>("[data-friyta-roll]"))) {
      rKnapp.setAttribute("aria-pressed", String(rKnapp.dataset.friytaRoll === friYtaRoll));
    }
    for (const fv of ["laggtill", "tabort", "klipp", "ihop"] as const) {
      for (const bKnapp of Array.from(rot.querySelectorAll<HTMLButtonElement>(`[data-v="${fv}"]`))) {
        bKnapp.setAttribute("aria-pressed", String(v.verktyg === fv));
      }
    }
    knapp('[data-v="fast"]')?.setAttribute("aria-pressed", String(v.fastPa));
    knapp('[data-v="rat"]')?.setAttribute("aria-pressed", String(v.ratVinkel));
    knapp('[data-v="foljkant"]')?.setAttribute("aria-pressed", String(v.foljKant));
    const bort = knapp('[data-v="bort"]');
    if (bort) bort.disabled = !v.val && !v.linje.val;
    for (const k of ["vand", "forenkla"]) {
      for (const bKnapp of Array.from(rot.querySelectorAll<HTMLButtonElement>(`[data-v="${k}"]`))) {
        bKnapp.disabled = !v.linje.val;
      }
    }
    const ritar = v.utkast.length > 0 || v.linje.utkast.length > 0;
    el("ar-rit-kontroller").hidden = !ritar;
    const avb = knapp('[data-v="avbryt"]');
    if (avb) avb.disabled = !ritar;
    const angra = knapp('[data-v="angra"]');
    if (angra) angra.disabled = !t.dok.kanAngra();
    const goraOm = knapp('[data-v="goraom"]');
    if (goraOm) goraOm.disabled = !t.dok.kanGoraOm();
    visaHjalp();
  };

  const klockslag = (d: Date): string => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const nyckelNu = (): string => JSON.stringify(forslagKropp(t.dok, t.takform)) + (el<HTMLInputElement>("ar-namn").value.trim() || "Mitt förslag");

  const ritaSparStatus = (): void => {
    const st = sparStatus({
      antal: t.dok.tillAndringar().length + t.dok.figurer.size,
      likaSomSparat: nyckelNu() === sparat,
      sparatNamn: t.scenario?.namn ?? null,
      tid: sparTid,
      giltig: kontroll ? kontroll.giltig : null,
      kontrollFel,
      sparFel,
      sparar,
    });
    const rad = el("ar-sparstatus");
    rad.textContent = st.text;
    rad.className = `flode-liten flode-sparstatus ${st.typ === "fel" || st.typ === "ogiltigt" || st.typ === "sparat" ? st.typ : "flode-dampad"}`;
    rad.setAttribute("role", st.typ === "fel" ? "alert" : "status");
    el<HTMLButtonElement>("ar-spara").disabled = sparar;
    ritaRegnOchKor();
  };

  const infoHtml = (): string => {
    if (!verktyg || !info) return "";
    const val = verktyg.val;
    let hus: HusUppgift | null = null;
    if (val?.ny) {
      const ring = verktyg.ringAv(val);
      const h = verktyg.hojdAv(val);
      const k = kontroll?.hus.find((x) => x.andring_id === val.id);
      hus = {
        yta_m2: k?.yta_m2 ?? (ring ? yta(ring) : 0),
        vaningar: k?.vaningar ?? Math.max(1, Math.round((h ?? 0) / info.vaningshojd_m)),
        bta_m2: k?.bta_m2 ?? (h !== null && ring ? btaUppskattning(yta(ring), h, info.vaningshojd_m) : 0),
      };
    }
    const antal = t.dok.tillAndringar().length + t.dok.figurer.size;
    return forslagInfoHtml({
      antal,
      hus,
      kontrollFel,
      fel: kontroll?.fel ?? [],
      varningar: (kontroll?.varningar ?? []).filter((v) => !(kontroll?.skarvar && /sänka vid skarven|sänkor till \(lista/.test(v))),
      information: kontroll?.information ?? [],
      sparFel,
      sparatSom: t.scenario && antal > 0 && kontroll?.giltig ? t.scenario.namn : null,
    });
  };

  const ritaSkarv = (): void => {
    const skarvRot = el("ar-skarv");
    const html = skarvPanelHtml({
      skarvar,
      vantar: kontroll === null && !t.dok.tom(),
      foreslar: skarvForeslar,
      forslag: skarvForslag,
      fel: skarvFel,
      meddelande: skarvMeddelande,
    });
    if (skarvRot.innerHTML === html) return;
    skarvRot.innerHTML = html;
    skarvRot.hidden = html === "";
  };

  const faltHtml = (f: Falt, fel: string | undefined): string => {
    const id = `ar-f-${f.nyckel}`;
    const hjalp = f.hjalp ? `<span class="flode-liten flode-dampad" id="${id}-h">${esc(f.hjalp)}</span>` : "";
    const beskr = ` aria-describedby="${id}-h${fel ? ` ${id}-fel` : ""}"`;
    let k: string;
    if (f.slag === "val") {
      k = `<select id="${id}" data-nyckel="${f.nyckel}"${beskr}>${(f.alternativ ?? []).map((opt) => `<option value="${esc(opt.varde)}"${opt.varde === f.varde ? " selected" : ""}>${esc(opt.etikett)}</option>`).join("")}</select>`;
    } else if (f.slag === "ja_nej") {
      k = `<input type="checkbox" id="${id}" data-nyckel="${f.nyckel}"${f.varde === "ja" ? " checked" : ""}${beskr} />`;
    } else {
      k = `<input type="text" inputmode="decimal" id="${id}" data-nyckel="${f.nyckel}" value="${esc(f.varde)}"${beskr} autocomplete="off" />${f.enhet ? ` <span class="flode-dampad">${esc(f.enhet)}</span>` : ""}`;
    }
    return `<div class="flode-falt"><label for="${id}">${esc(f.etikett)}</label>${k}${hjalp}${fel ? `<span class="flode-fel flode-liten" id="${id}-fel" role="alert">${esc(fel)}</span>` : ""}</div>`;
  };

  const uppdateraFigurPanel = (): void => {
    const figurRot = el("ar-figur");
    const figurFalt = el("ar-figur-falt");
    const markSnabbPlats = el("ar-mark-snabb-plats");
    if (!verktyg) {
      figurRot.hidden = true;
      return;
    }
    const f = verktyg.linje.figur(verktyg.linje.val);
    if (!f) {
      figurRot.hidden = true;
      el("ar-linje-kontext").hidden = true;
      figurNyckel = "";
      return;
    }
    const lager = t.dok.lagerForFigur(f);
    const roll = lager?.roll ?? "mark";
    const falt = faltFor(roll, f.egenskaper);
    const pk = verktyg.linje.punkterAv(f);
    const vald = verktyg.linje.valdPunkt;
    const z = vald !== null ? pk[vald]?.z : undefined;
    const nyckel = JSON.stringify([f.id, f.egenskaper, faltFel, vald, z, pk.length]);
    figurRot.hidden = false;
    el("ar-linje-kontext").hidden = f.typ !== "linje";
    el("ar-figur-rubrik").textContent = roll === "mark" ? "Markhöjning / sänkning" : `${etikettFor(roll, f.egenskaper, f.typ)} (${lager?.namn ?? f.lager})`;

    if (roll === "mark") {
      const nuDelta = typeof f.egenskaper["delta_m"] === "number" ? (f.egenskaper["delta_m"] as number) : null;
      markSnabbPlats.innerHTML = `<div class="ar-mark-snabb" role="group" aria-label="Snabbval höj eller sänk mark">
        ${MARK_SNABB_M.map((d) => {
          const text = `${d > 0 ? "+" : "−"}${formatera(Math.abs(d), 1)} m`;
          const aktiv = nuDelta !== null && Math.abs(nuDelta - d) < 1e-6;
          return `<button type="button" data-mark-delta="${d}" aria-pressed="${aktiv}">${esc(text)}</button>`;
        }).join("")}
      </div>`;
    } else {
      markSnabbPlats.innerHTML = "";
    }

    const langdText = f.typ === "linje" ? `Längd ${TAL1.format(langd(pk))} m. ` : `Yta ${TAL0.format(yta(pk))} m². `;
    const hornInfo =
      roll === "mark" && vald !== null
        ? `<div class="flode-falt"><label for="ar-f-hornhojd">Höjd på hörn ${TAL0.format(vald + 1)}</label><input type="text" inputmode="decimal" id="ar-f-hornhojd" data-nyckel="hornhojd" value="${z === undefined ? "" : esc(TAL2.format(z))}" /> <span class="flode-dampad">m RH 2000</span></div>`
        : "";
    if (document.activeElement && figurFalt.contains(document.activeElement) && nyckel === figurNyckel) return;
    figurNyckel = nyckel;
    const aktivId = document.activeElement instanceof HTMLElement && figurFalt.contains(document.activeElement) ? document.activeElement.id : "";
    figurFalt.innerHTML = falt.map((x) => faltHtml(x, faltFel?.nyckel === x.nyckel ? faltFel.text : undefined)).join("") + hornInfo;
    if (aktivId) figurFalt.querySelector<HTMLElement>(`#${aktivId}`)?.focus();
    el("ar-figur-info").textContent = `${langdText}Servern kontrollerar värdena när förslaget sparas.`;
  };

  const uppdateraCadPanel = (tvinga = false): void => {
    const plats = el("ar-cad-plats");
    const ritarAktivt =
      lage === "redigera" &&
      Boolean(verktyg && (verktyg.verktyg === "hus" || verktyg.verktyg === "linje" || verktyg.verktyg === "yta"));
    if (!ritarAktivt || !verktyg) {
      plats.hidden = true;
      plats.innerHTML = "";
      cadNyckel = "";
      cadFel = null;
      return;
    }
    const utkast = verktyg.verktyg === "hus" ? verktyg.utkast : verktyg.linje.utkast;
    const antalPunkter = utkast.length;
    const senastePunkt = verktyg.sistaUtkastPunkt();
    if (!cadManuelltLage) {
      cadLage = antalPunkter > 0 ? "matt" : "sweref";
    }
    const nyckel = JSON.stringify([
      verktyg.verktyg,
      cadOppen,
      cadLage,
      antalPunkter,
      senastePunkt?.x ?? null,
      senastePunkt?.y ?? null,
      cadFel,
    ]);
    plats.hidden = false;
    if (!tvinga && nyckel === cadNyckel) return;
    const aktivId = document.activeElement instanceof HTMLElement && plats.contains(document.activeElement) ? document.activeElement.id : "";
    cadNyckel = nyckel;
    plats.innerHTML = cadMattHtml({
      synlig: true,
      oppen: cadOppen,
      lage: cadLage,
      antalPunkter,
      senastePunkt,
      langdText: cadLangdText,
      baringText: cadBaringText,
      eText: cadEText,
      nText: cadNText,
      fel: cadFel,
    });
    if (aktivId) plats.querySelector<HTMLElement>(`#${aktivId}`)?.focus();
  };

  const uppdateraRedigeraPanel = (): void => {
    if (!verktyg || !info) return;
    uppdateraCadPanel();
    const hojdFalt = el<HTMLInputElement>("ar-hojd");
    const takFalt = el<HTMLSelectElement>("ar-tak");
    const husKort = el("ar-hus-kort");
    const val = verktyg.val;
    const hjalp = el("ar-hojd-hjalp");
    const atgarder = el("ar-hus-atgarder");
    const rivKnapp = el<HTMLButtonElement>("ar-riv-knapp");
    if (!val) {
      husKort.hidden = true;
      atgarder.hidden = true;
      hojdFalt.disabled = true;
      takFalt.disabled = true;
      if (document.activeElement !== hojdFalt) hojdFalt.value = "";
      hjalp.textContent = "";
    } else {
      husKort.hidden = false;
      const h = verktyg.hojdAv(val);
      const riven = t.dok.bort.has(val.id);
      atgarder.hidden = val.ny;
      if (!val.ny) {
        rivKnapp.textContent = riven ? "Ångra rivning" : "Riv byggnad";
        rivKnapp.setAttribute("aria-pressed", String(riven));
      }
      hojdFalt.disabled = riven;
      if (document.activeElement !== hojdFalt) hojdFalt.value = h === null ? "" : String(Math.round(h * 100) / 100);
      takFalt.disabled = !val.ny;
      takFalt.value = (val.ny ? t.takform.get(val.id) : null) ?? FORVALD_TAKFORM;
      const tak = val.ny ? (t.takform.get(val.id) ?? FORVALD_TAKFORM) : FORVALD_TAKFORM;
      hjalp.textContent = val.ny
        ? `Högst ${TAL0.format(info.hojd_max_m)} m.${TAKHJALP[tak] ? ` ${TAKHJALP[tak]}` : ""}`
        : `Befintlig byggnad${riven ? ", riven" : ""}.`;
    }
    el("ar-info").innerHTML = infoHtml();
    const markYtor = Array.from(t.dok.figurer.values())
      .filter((fg) => fg.typ === "yta")
      .map((fg) => ({
        roll: t.dok.lagerForFigur(fg)?.roll,
        punkter: fg.punkter,
        egenskaper: fg.egenskaper,
      }));
    const volText = markvolymText(beraknaMarkvolym(markYtor));
    const volEl = el("ar-markvolym");
    volEl.textContent = volText;
    volEl.hidden = volText === "";
    ritaSparStatus();
    ritaSkarv();
    uppdateraFigurPanel();
  };

  const uppdateraRit = (): void => {
    if (!a || !verktyg || !info || lage !== "redigera") return;
    const v = verktyg;
    const dok = t.dok;
    const hus: RitVy["hus"] = [];
    for (const h of dok.ny.values()) {
      if (dok.arSynligt(h)) hus.push({ id: h.id, ring: v.dragRing?.id === h.id ? v.dragRing.ring : h.ring, vald: Boolean(v.val?.ny && v.val.id === h.id), slag: "ny" });
    }
    if (v.val && !v.val.ny) {
      const bHus = info.byggnader.find((x) => x.id === v.val?.id);
      if (bHus) hus.push({ id: bHus.id, ring: bHus.ring.map(([x, y]) => ({ x, y })), vald: true, slag: dok.bort.has(bHus.id) ? "bort" : "andrad" });
    }
    let horn: Punkt[] = [];
    let mitt: Punkt[] = [];
    if (v.val?.ny && v.verktyg !== "hus") {
      const ring = v.ringAv(v.val);
      if (ring) {
        horn = ring;
        if (v.verktyg === "laggtill" || v.verktyg === "valj") mitt = infogaPunkter(ring).map((m) => m.at);
      }
    }
    const figurer: NonNullable<RitVy["figurer"]> = v.linje.synliga().map((g) => {
      const l = dok.lagerForFigur(g);
      return {
        id: g.id,
        typ: g.typ,
        punkter: v.linje.punkterAv(g),
        vald: v.linje.val === g.id || v.linje.ihopForsta === g.id,
        roll: l?.roll ?? "mark",
        farg: l?.stil.linje ?? l?.stil.fyllning ?? FIGURFARG[l?.roll ?? "mark"] ?? "#111111",
      };
    });
    const valdFigur = v.linje.figur(v.linje.val);
    if (valdFigur && dok.arFigurRedigerbar(valdFigur) && v.verktyg !== "linje" && v.verktyg !== "yta") {
      const pk = v.linje.punkterAv(valdFigur);
      horn = pk;
      if (v.verktyg === "laggtill" || v.verktyg === "valj") {
        const n = valdFigur.typ === "yta" ? pk.length : pk.length - 1;
        mitt = Array.from({ length: n }, (_, i) => {
          const pA = pk[i] as Punkt;
          const pB = pk[(i + 1) % pk.length] as Punkt;
          return { x: (pA.x + pB.x) / 2, y: (pA.y + pB.y) / 2 };
        });
      }
    }
    const ritar = v.verktyg === "hus" || v.verktyg === "linje" || v.verktyg === "yta";
    a.settRit({
      ...TOM_RITVY,
      hus,
      figurer,
      foljForhand: v.linje.foljForhand ?? [],
      brytlinjer: (info.brytlinjer ?? []).map((br) => br.koordinater.map(([x, y]) => ({ x: x as number, y: y as number }))),
      utkast: v.verktyg === "hus" ? v.utkast : v.linje.utkast,
      mark: ritar ? v.mark : null,
      fast: v.fast ? { at: v.fast.at, slag: ({ hornpunkt: "hörn", skarning: "skärning", kant: "kant" } as Record<string, string>)[v.fast.typ] ?? "fäst" } : null,
      lasLinje: v.lasLinje,
      horn,
      mitt,
      etikett: v.ritText(),
      valdPunkt: v.linje.valdPunkt,
    });
    a.settSkarv(skarvFigurer(skarvar, a.meterPerPx()), kontroll === null && skarvar !== null);
    a.settMarkorstil(mat.aktiv ? "crosshair" : v.verktyg === "valj" ? (v.drar ? "grabbing" : "") : "crosshair");
    a.settByggnader(byggnaderMedForslag(info, dok));
    uppdateraVerktygKnappar();
    uppdateraRedigeraPanel();
  };

  const spara = async (kropp: ReturnType<typeof forslagKropp>): Promise<void> => {
    const namnFalt = el<HTMLInputElement>("ar-namn");
    const namn = (namnFalt.value.trim() || "Mitt förslag").trim();
    const nyckel = JSON.stringify(kropp) + namn;
    if (nyckel === sparat) return;
    sparar = true;
    ritaSparStatus();
    try {
      const gammal = t.scenario;
      let d = await sparaScenario(projekt, gammal ? gammal.namn : namn, kropp, gammal?.slug);
      if (gammal && gammal.namn !== namn) d = await bytScenarioNamn(projekt, gammal.slug, namn);
      t.scenario = { slug: d.slug, namn: d.namn };
      t.namn = namnFalt.value.trim();
      sparat = nyckel;
      sparFel = null;
      sparTid = klockslag(new Date());
      sparaTillstand(t);
      cadBimKontroller.uppdateraKontext();
      void lasSparade();
    } catch (e) {
      sparFel = e instanceof Error ? e.message : String(e);
    }
    sparar = false;
    ritaSparStatus();
  };

  const kontrolleraOchSpara = async (): Promise<void> => {
    const kropp = forslagKropp(t.dok, t.takform);
    if (t.dok.tom()) {
      kontroll = null;
      kontrollFel = null;
      skarvar = null;
      skarvForslag = null;
      planeraRitning();
      ritaRegnOchKor();
      return;
    }
    try {
      kontroll = await kontrolleraAndringar(projekt, kropp);
      kontrollFel = null;
      skarvar = kontroll.skarvar ?? null;
      if (skarvTillampad) {
        skarvTillampad = false;
        skarvMeddelande = `Åtgärden är tillämpad. Kontrollen efter åtgärden: ${utfallText({ antal: skarvar?.antal ?? 0, tillkommande_volym_m3: skarvar?.tillkommande_volym_m3 ?? 0 })}.`;
      }
    } catch (e) {
      kontroll = null;
      kontrollFel = e instanceof Error ? e.message : String(e);
      skarvar = null;
    }
    if (avslutad) return;
    ritaSparStatus();
    if (kontroll?.giltig) {
      sparKo = sparKo.then(() => spara(forslagKropp(t.dok, t.takform)));
      await sparKo;
    }
    planeraRitning();
  };

  const planeraKontroll = (): void => {
    if (kontrollTimer !== null) window.clearTimeout(kontrollTimer);
    kontrollTimer = window.setTimeout(() => void kontrolleraOchSpara(), 250);
    planeraRitning();
  };

  const sparaNu = async (): Promise<boolean> => {
    if (kontrollTimer !== null) window.clearTimeout(kontrollTimer);
    kontrollTimer = null;
    if (t.dok.tom()) {
      ritaSparStatus();
      return Boolean(t.scenario);
    }
    const namnFalt = el<HTMLInputElement>("ar-namn");
    if (!namnFalt.value.trim()) {
      namnFalt.value = "Mitt förslag";
      t.namn = "Mitt förslag";
      sparaTillstand(t);
    }
    sparFel = null;
    await sparKo;
    await kontrolleraOchSpara();
    return !sparFel && kontroll?.giltig === true && Boolean(t.scenario);
  };

  const ritaSparade = (): void => {
    const rad = scenarier
      .map(
        (x) => `<li class="flode-scen${t.scenario?.slug === x.slug ? " aktivt" : ""}">
          <span>${esc(x.namn)} <span class="flode-dampad flode-liten">${formatera(x.antal_andringar, 0)} ändring${x.antal_andringar === 1 ? "" : "ar"}</span></span>
          <span class="flode-scen-knappar">
            <button type="button" data-oppna="${esc(x.slug)}">Öppna</button>
            <button type="button" data-bytnamn="${esc(x.slug)}">Byt namn</button>
            <button type="button" data-tabort="${esc(x.slug)}" aria-label="Ta bort ${esc(x.namn)}">Ta bort</button>
          </span></li>`,
      )
      .join("");
    el("ar-sparade").innerHTML = `
      <div class="flode-rad"><button type="button" class="flode-knapp" id="ar-nytt">Nytt tomt förslag</button></div>
      ${listFel ? `<p class="flode-fel flode-liten" role="alert">${esc(listFel)}</p>` : ""}
      ${rad ? `<ul class="flode-scenlista">${rad}</ul>` : `<p class="flode-liten flode-dampad">Inga sparade förslag än.</p>`}`;
  };

  const lasSparade = async (): Promise<void> => {
    try {
      scenarier = (await listaScenarier(projekt)).scenarier;
    } catch {
      /* Behåll befintlig lista. */
    }
    if (!avslutad) ritaSparade();
  };

  const oppnaScenario = async (slug: string): Promise<void> => {
    const d = await hamtaScenario(projekt, slug);
    if (avslutad) return;
    t.takform = takformFran(d.andringar);
    verktyg?.markera(null);
    t.dok.fran(d.fria_andringar ?? d.andringar, d.ritlager);
    t.scenario = { slug: d.slug, namn: d.namn };
    t.namn = d.namn;
    el<HTMLInputElement>("ar-namn").value = d.namn;
    sparat = JSON.stringify(forslagKropp(t.dok, t.takform)) + d.namn;
    sparaTillstand(t);
    ritaSparade();
    ritaRegnOchKor();
    bullerKontroller?.uppdateraKontext();
    cadBimKontroller.uppdateraKontext();
  };

  // ---------- Körningar direkt från kartan ----------
  const korNulageNu = async (omfang: "aktivt" | "alla" = "aktivt"): Promise<void> => {
    const analyser =
      omfang === "alla"
        ? forvaldaAnalyser()
        : lage === "redigera" && aktivaAnalyser().length === 0
          ? forvaldaAnalyser()
          : aktivaAnalyser();
    regnFel = valideraRegnVal(t.regn);
    solFel = valideraSolVal(t.sol);
    if (!kanStartaKorningForAnalyser(analyser, regnFel, avtappningFel, solFel)) return;
    korAutoByt = "nulage";
    await startaLageIdag(t, analyser);
  };

  const korForslagNu = async (omfang: "aktivt" | "alla" = "aktivt"): Promise<void> => {
    const analyser =
      omfang === "alla"
        ? forvaldaAnalyser()
        : lage === "redigera" && aktivaAnalyser().length === 0
          ? forvaldaAnalyser()
          : aktivaAnalyser();
    regnFel = valideraRegnVal(t.regn);
    solFel = valideraSolVal(t.sol);
    if (!kanStartaKorningForAnalyser(analyser, regnFel, avtappningFel, solFel)) return;
    const ok = await sparaNu();
    if (!ok || !t.scenario || avslutad) {
      if (lage !== "redigera") stallLage("redigera");
      return;
    }
    korAutoByt = "efter";
    await startaKorning(t, analyser);
  };

  let senastHanteradKorning: typeof t.korning = null;
  const avlyssnaKorning = vidKorning(() => {
    if (avslutad) return;
    ritaRegnOchKor();
    if (t.korning?.klar && t.korning !== senastHanteradKorning) {
      senastHanteradKorning = t.korning;
      const klaraAvsnitt = senasteKlaraAvsnitt(projekt);
      const harNyaKlara = !t.korning.fel || klaraAvsnitt.length > 0;
      if (harNyaKlara && t.korning.lage && t.lageVyer.length > 0) {
        kalla = "lage";
        if (korAutoByt === "nulage") lage = "nulage";
        korAutoByt = null;
        void valjAmne(valjAmneEfterKorning(amne, t.lageVyer, klaraAvsnitt));
        return;
      }
      if (harNyaKlara && !t.korning.lage && t.ritVyer.length > 0) {
        kalla = "ritat";
        if (korAutoByt === "efter") lage = "efter";
        korAutoByt = null;
        void valjAmne(valjAmneEfterKorning(amne, t.ritVyer, klaraAvsnitt));
        return;
      }
      korAutoByt = null;
    }
    ritaAnalysPanel();
  });
  avbryt.push(avlyssnaKorning);

  // ---------- Mät- och sektionsverktyg ----------
  synkaMatOchBullerLinje = (): void => {
    const linje =
      mat.linje() ??
      (amne === "buller" && lage !== "redigera" && lage !== "3d" ? (bullerKontroller?.vagLinje() ?? null) : null);
    a?.settMatLinje(linje);
    b?.settMatLinje(linje);
  };

  const synkaMarkorStil = (): void => {
    a?.settMarkorstil(
      sektion.aktiv || mat.aktiv || (amne === "buller" && Boolean(bullerKontroller?.arRitarVag()))
        ? "crosshair"
        : lage === "redigera" && verktyg && verktyg.verktyg !== "valj"
          ? "crosshair"
          : "",
    );
  };
  synkaMarkorStilRef = synkaMarkorStil;

  mat.vidAndring(() => {
    if (mat.aktiv && sektion.aktiv) sektion.avsluta();
    if (mat.aktiv) bullerKontroller?.avbrytVagRitning();
    synkaMatOchBullerLinje();
    synkaMarkorStil();
    ritaVaxel();
  });

  let sektionVarKlar = false;
  sektion.vidAndring(() => {
    if (sektion.aktiv && mat.aktiv) mat.sattAktiv(false);
    if (sektion.aktiv) bullerKontroller?.avbrytVagRitning();
    const l = sektion.linje();
    const pts = l ? l.punkter.map((p) => [p.x, p.y] as [number, number]) : null;
    a?.settSektionLinje(pts);
    b?.settSektionLinje(pts);
    synkaMarkorStil();
    ritaVaxel();
    if (sektion.klar && !sektionVarKlar) {
      sektionVarKlar = true;
      void hamtaSektionForLinje();
    } else if (!sektion.klar) {
      sektionVarKlar = false;
    }
  });

  // Hovring i sektions-SVG:n & klick/drag i hydrografen
  const bottenDockEl = el("ar-botten-dock");
  const uppdateraSektionHovring = (clientX: number): void => {
    if (!sektionData) return;
    const svgEl = bottenDockEl.querySelector<SVGSVGElement>("[data-sektion-svg] svg");
    if (!svgEl) return;
    const rect = svgEl.getBoundingClientRect();
    if (rect.width <= 0) return;
    const viewBredd = svgEl.viewBox?.baseVal?.width || 720;
    const svgX = ((clientX - rect.left) / rect.width) * viewBredd;
    const idx = indexFranSvgX(sektionData, svgX, viewBredd);
    if (idx === null || idx === sektionAktivIndex) return;
    sektionAktivIndex = idx;
    const avl = avlasningVidIndex(sektionData, idx);
    if (avl) {
      a?.settSektionMarkor([avl.e, avl.n]);
      b?.settSektionMarkor([avl.e, avl.n]);
      const avlEl = bottenDockEl.querySelector<HTMLElement>("#ar-sektion-avl");
      if (avlEl) {
        avlEl.textContent = `Avstånd ${avl.avstandText} · Mark ${avl.markForeText} · Djup ${avl.djupForeText}${sektionData.har_efter ? ` → ${avl.djupEfterText}` : ""} · Vattenyta ${avl.vattenYtaForeText}`;
      }
    }
    const svgWrap = bottenDockEl.querySelector<HTMLElement>("[data-sektion-svg]");
    if (svgWrap) {
      svgWrap.innerHTML = ritaSektionSvg(sektionData, {
        bredd: 720,
        hojd: 176,
        aktivIndex: sektionAktivIndex,
        visaFore: true,
        visaEfter: sektionData.har_efter,
      });
    }
  };

  let hydroDrar = false;
  const hoppaHydrograf = (clientX: number): void => {
    if (!tidsserie || tidsserie.tidssteg.length === 0) return;
    const svgEl = bottenDockEl.querySelector<SVGSVGElement>("[data-tid-hydrograf] svg");
    if (!svgEl) return;
    const rect = svgEl.getBoundingClientRect();
    if (rect.width <= 0) return;
    const viewBredd = svgEl.viewBox?.baseVal?.width || 320;
    const svgX = ((clientX - rect.left) / rect.width) * viewBredd;
    const tidMin = tidMinFranSvgX(svgX, viewBredd, tidsserie.slut_tid_min);
    const steg = narmasteStegForTid(tidsserie, tidMin);
    if (steg === null) return;
    stoppaTidTimer();
    tidTillstand = { ...tidTillstand, spelar: false, aktivtSteg: steg };
    synkaKarta();
    ritaBottenDock();
  };

  lyssna(bottenDockEl, "mousemove", (h) => {
    const me = h as MouseEvent;
    if (hydroDrar) {
      hoppaHydrograf(me.clientX);
      return;
    }
    if ((me.target as Element).closest("[data-sektion-svg]")) {
      uppdateraSektionHovring(me.clientX);
    }
  });
  lyssna(bottenDockEl, "mouseleave", () => {
    hydroDrar = false;
    if (sektionAktivIndex !== null && sektionData) {
      sektionAktivIndex = null;
      a?.settSektionMarkor(null);
      b?.settSektionMarkor(null);
      ritaBottenDock();
    }
  });
  lyssna(bottenDockEl, "mousedown", (h) => {
    const me = h as MouseEvent;
    if ((me.target as Element).closest("[data-tid-hydrograf]")) {
      hydroDrar = true;
      hoppaHydrograf(me.clientX);
    }
  });
  window.addEventListener(
    "mouseup",
    () => {
      hydroDrar = false;
    },
    { signal: ac.signal },
  );

  // ---------- Händelsehantering ----------
  lyssna(rot, "click", (h) => {
    const target = h.target as Element;
    const m = target.closest<HTMLElement>(ARBETSYTA_KLICK_SELEKTOR);
    if (!m) return;

    if (m.id === "ar-skikt-vaxla") {
      skiktHopfalld = !skiktHopfalld;
      ritaSkiktPanel();
      ritaVaxel();
      return;
    }
    if (m.id === "ar-tecken-vaxla") {
      uiLage.teckenMinimerad = !uiLage.teckenMinimerad;
      sparaArbetsytaUiLage(uiLage);
      ritaTecken();
      return;
    }
    if (m.dataset.grundkartaVaxla !== undefined) {
      uiLage.grundkartaOppen = !uiLage.grundkartaOppen;
      sparaArbetsytaUiLage(uiLage);
      ritaGrundkarta();
      return;
    }
    if (m.id === "ar-sanka-stang") {
      sankaHtml = null;
      ritaAvlasning();
      return;
    }
    if (m.id === "ar-panel-vaxla") {
      uiLage.panelDold = !uiLage.panelDold;
      sparaArbetsytaUiLage(uiLage);
      const sida = rot.querySelector<HTMLElement>(".ar-sida");
      sida?.classList.toggle("panel-dold", uiLage.panelDold);
      m.textContent = uiLage.panelDold ? "⟨" : "⟩";
      m.setAttribute("aria-expanded", String(!uiLage.panelDold));
      const etikett = uiLage.panelDold ? "Visa sidopanel" : "Dölj sidopanel";
      m.setAttribute("aria-label", etikett);
      m.title = etikett;
      window.requestAnimationFrame(() => {
        a?.matOm();
        b?.matOm();
      });
      return;
    }
    if (m.id === "ar-rita-forslag") {
      stallLage("redigera");
      return;
    }

    if (m.id === "ar-mat-knapp") {
      if (sektion.aktiv) sektion.avsluta();
      mat.vaxla();
      return;
    }
    if (m.id === "ar-sektion-knapp") {
      if (mat.aktiv) mat.sattAktiv(false);
      sektion.vaxla();
      return;
    }
    if (m.id === "ar-las-centrum") {
      lasAvKartcentrum();
      return;
    }
    if (m.dataset.cadLage) {
      cadLage = m.dataset.cadLage === "sweref" ? "sweref" : "matt";
      cadManuelltLage = true;
      cadOppen = true;
      cadFel = null;
      uppdateraCadPanel(true);
      return;
    }
    if (m.id === "ar-redigera-3d-knapp") {
      sattRedigera3d(!redigera3dPa);
      return;
    }
    if (m.id === "ar-redigera-3d-stang") {
      sattRedigera3d(false);
      return;
    }
    if (m.id === "ar-riv-knapp" && verktyg?.val && !verktyg.val.ny) {
      const id = verktyg.val.id;
      const riven = t.dok.bort.has(id);
      t.dok.sattBort(id, !riven);
      planeraRitning();
      return;
    }
    if (m.dataset.dockFlik) {
      dockFlik = m.dataset.dockFlik as "tid" | "sektion";
      ritaBottenDock();
      return;
    }
    if (m.dataset.sektionHandling) {
      const handling = m.dataset.sektionHandling;
      if (handling === "stang") stangSektion();
      else if (handling === "svg" && sektionData) {
        const svg = ritaSektionSvg(sektionData, { bredd: 900, hojd: 240, visaFore: true, visaEfter: sektionData.har_efter });
        laddaNerFil(svg, `sektion-${projekt}.svg`, "image/svg+xml;charset=utf-8");
      } else if (handling === "csv" && sektionData) {
        laddaNerFil(sektionTillCsv(sektionData), `sektion-${projekt}.csv`, "text/csv;charset=utf-8");
      }
      return;
    }
    if (m.dataset.tidHandling && tidsserie) {
      const handling = m.dataset.tidHandling;
      if (handling === "spela") {
        const skaSpela = !tidTillstand.spelar;
        tidTillstand = {
          ...tidTillstand,
          spelar: skaSpela,
          aktivtSteg: skaSpela && tidTillstand.aktivtSteg === null ? 0 : tidTillstand.aktivtSteg,
        };
        uppdateraTidSpelning();
      } else if (handling === "hastighet") {
        tidTillstand = { ...tidTillstand, hastighet: tidTillstand.hastighet === 1 ? 2 : 1 };
        if (tidTillstand.spelar) uppdateraTidSpelning();
      } else if (handling === "topp") {
        stoppaTidTimer();
        tidTillstand = hoppaTillTopp(tidsserie, tidTillstand);
      } else if (handling === "maxdjup") {
        stoppaTidTimer();
        tidTillstand = tillMaxdjup(tidTillstand);
      }
      synkaKarta();
      ritaBottenDock();
      return;
    }
    if (m.dataset.grundkarta) {
      const val = m.dataset.grundkarta as GrundkartaVal;
      const nu = aktuellGrundkarta(kartLager);
      tillampaGrundkarta(val, nu.val === "ren" ? 0.85 : nu.opacitet, kartLager);
      basLagerListan = bas?.lagerListan() ?? [];
      synkaKarta();
      return;
    }
    if (m.dataset.skyfallMetod) {
      t.skyfallMetod = m.dataset.skyfallMetod === "2d" ? "2d" : "graf";
      regnFel = valideraRegnVal(t.regn);
      sparaTillstand(t);
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.regnBerakningstyp) {
      t.regn = { ...t.regn, berakningstyp: m.dataset.regnBerakningstyp as Berakningstyp };
      regnFel = valideraRegnVal(t.regn);
      sparaTillstand(t);
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.regnRegntyp) {
      t.regn = { ...t.regn, regntyp: m.dataset.regnRegntyp as RegnTyp };
      regnFel = valideraRegnVal(t.regn);
      sparaTillstand(t);
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.solLage) {
      const nyttLage = m.dataset.solLage as SolLage;
      t.sol = {
        ...t.sol,
        klockslag_utc: nyttLage === "skuggmask" ? (t.sol.klockslag_utc ?? STANDARD_KLOCKSLAG_UTC) : null,
      };
      solFel = valideraSolVal(t.sol);
      sparaTillstand(t);
      ritaAnalysPanel();
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.ekoLager) {
      ekoLagerVal = m.dataset.ekoLager === "kansighet" ? "kansighet" : "stromtathet";
      ritaAnalysPanel();
      synkaKarta();
      return;
    }
    if (m.dataset.solDatum) {
      t.sol = { ...t.sol, datumForval: m.dataset.solDatum as SolDatumForval };
      solFel = valideraSolVal(t.sol);
      sparaTillstand(t);
      ritaAnalysPanel();
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.solBrukstid) {
      t.sol = { ...t.sol, brukstid: m.dataset.solBrukstid as SolBrukstid };
      solFel = valideraSolVal(t.sol);
      sparaTillstand(t);
      ritaAnalysPanel();
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.friytaRoll && verktyg && info) {
      friYtaRoll = m.dataset.friytaRoll as FriYtaRoll;
      huvudVal = "friyta";
      aktiveraRollLager(t.dok, friYtaRoll, info.ritlager?.roller ?? []);
      verktyg.sattVerktyg("yta");
      planeraRitning();
      return;
    }
    if (m.id === "ar-in") {
      a?.zooma(0.7);
      return;
    }
    if (m.id === "ar-ut") {
      a?.zooma(1 / 0.7);
      return;
    }
    if (m.id === "ar-anpassa" && info) {
      a?.anpassa(info.utbredning);
      return;
    }
    if (m.id === "ar-kor-nulage" || m.id === "ar-tom-kor-nulage") {
      void korNulageNu("aktivt");
      return;
    }
    if (m.id === "ar-kor-forslag") {
      void korForslagNu("aktivt");
      return;
    }
    if (m.id === "ar-kor-alla") {
      if (m.dataset.korAlla === "forslag") void korForslagNu("alla");
      else void korNulageNu("alla");
      return;
    }
    if (m.id === "ar-spara") {
      void sparaNu().then((ok) => {
        if (avslutad) return;
        meddelande = ok && !t.dok.tom() ? { typ: "info", text: `Förslaget är sparat som ${t.scenario?.namn ?? ""}.` } : null;
        visaHjalp();
      });
      return;
    }
    if (m.dataset.regnForval) {
      t.regn = medRegnForval(t.regn, m.dataset.regnForval as RegnForvalId);
      regnFel = valideraRegnVal(t.regn);
      sparaTillstand(t);
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.regnEget) {
      t.regn = { ...t.regn, forval: "eget", egetLage: m.dataset.regnEget as EgetRegnLage };
      regnFel = valideraRegnVal(t.regn);
      sparaTillstand(t);
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.lage) {
      stallLage(m.dataset.lage as ArbetsLage);
      return;
    }
    if (m.dataset.amne) {
      void valjAmne(m.dataset.amne as ResultatAmne);
      return;
    }
    if (m.dataset.kalla) {
      kalla = m.dataset.kalla as Kalla;
      if (kalla === "lage" && lage !== "nulage" && lage !== "redigera" && lage !== "3d") lage = "nulage";
      void valjVy(vyerFor(kalla)[0] ?? null);
      return;
    }
    if (m.dataset.vy) {
      void valjVy(vyerFor(kalla).find((v) => v.id === m.dataset.vy) ?? null);
      return;
    }
    if (m.dataset.lagerVal) {
      valtNyckel = m.dataset.lagerVal;
      vy = tillampaVal();
      ritaAnalysPanel();
      synkaKarta();
      return;
    }
    if (m.dataset.vektorKnapp) {
      const slag = m.dataset.vektorKnapp as VektorSlag;
      if (vektorPa.has(slag)) vektorPa.delete(slag);
      else vektorPa.add(slag);
      if (slag === "sankor" && !vektorPa.has(slag)) {
        sankaHtml = null;
        ritaAvlasning();
      }
      ritaAnalysPanel();
      sattVektorer();
      ritaTecken();
      void laddaVektorer();
      return;
    }
    if (m.id === OSAKERHET_KNAPP_ID) {
      if ((amne === "vatten" || amne === "dagsljus") && osakPa.has(amne)) {
        osakPa.delete(amne);
        ritaAnalysPanel();
        synkaKarta();
      } else void laddaOsakerhet(amne);
      return;
    }
    if (m.dataset.markDelta && verktyg) {
      const f = verktyg.linje.figur(verktyg.linje.val);
      const d = Number(m.dataset.markDelta);
      if (f && Number.isFinite(d)) {
        t.dok.sattFigurEgenskaper(f.id, { niva_rh2000: null, lutning: null, delta_m: d });
        faltFel = null;
        uppdateraFigurPanel();
        planeraKontroll();
      }
      return;
    }
    if (m.dataset.hv && verktyg && info) {
      if (mat.aktiv) mat.sattAktiv(false);
      if (sektion.aktiv) sektion.avsluta();
      meddelande = null;
      const hv = m.dataset.hv as HuvudVerktyg;
      huvudVal = hv;
      const roller = info.ritlager?.roller ?? [];
      if (hv === "valj") verktyg.sattVerktyg("valj");
      else if (hv === "hus") {
        aktiveraRollLager(t.dok, "byggnad", roller);
        verktyg.sattVerktyg("hus");
      } else if (hv === "mark") {
        aktiveraRollLager(t.dok, "mark", roller);
        verktyg.sattVerktyg("yta");
      } else if (hv === "friyta") {
        aktiveraRollLager(t.dok, friYtaRoll, roller);
        verktyg.sattVerktyg("yta");
      } else if (hv === "mur") {
        aktiveraRollLager(t.dok, "brytlinje", roller);
        verktyg.sattVerktyg("linje");
      } else if (hv === "oppning") {
        aktiveraRollLager(t.dok, "oppning", roller);
        verktyg.sattVerktyg("linje");
      }
      planeraRitning();
      return;
    }
    if (m.dataset.v && verktyg) {
      if (mat.aktiv) mat.sattAktiv(false);
      if (sektion.aktiv) sektion.avsluta();
      meddelande = null;
      const vid = m.dataset.v;
      if (vid === "bort") verktyg.taBortVal();
      else if (vid === "angra") verktyg.angra();
      else if (vid === "goraom") verktyg.goraOm();
      else if (vid === "fast") verktyg.vaxlaFast();
      else if (vid === "rat") verktyg.vaxlaRatVinkel();
      else if (vid === "foljkant") verktyg.vaxlaFoljKant();
      else if (vid === "vand") verktyg.kommando("vand");
      else if (vid === "forenkla") verktyg.kommando("forenkla");
      else if (vid === "klar") verktyg.tangent({ key: "Enter", code: "Enter", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false });
      else if (vid === "backa") verktyg.tangent({ key: "Backspace", code: "Backspace", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false });
      else if (vid === "avbryt") verktyg.tangent({ key: "Escape", code: "Escape", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false });
      else if (vid === "fil") el<HTMLInputElement>("ar-fil").click();
      else verktyg.sattVerktyg(vid as Verktyg);
      planeraRitning();
    }
  });

  lyssna(rot, "input", (h) => {
    const s = h.target as HTMLInputElement;
    if (s.id === "ar-raster-opacitet") {
      rasterOpacitetVal = klammaRasterOpacitet(Number(s.value) / 100);
      const ut = rot.querySelector<HTMLElement>("#ar-raster-opacitet-ut");
      if (ut) ut.textContent = `${Math.round(rasterOpacitetVal * 100)}\u202F%`;
      synkaKarta();
      return;
    }
    if (s.dataset.solKlockslag !== undefined) {
      const ut = rot.querySelector<HTMLElement>("[data-sol-klockslag-ut]");
      const v = Number(s.value);
      if (ut && Number.isFinite(v)) ut.textContent = formateraKlockslagUtc(v);
      return;
    }
    if (s.dataset.grundkartaOpacitet !== undefined) {
      const nu = aktuellGrundkarta(kartLager);
      const op = Math.min(1, Math.max(0, Number(s.value) / 100));
      tillampaGrundkarta(nu.val, op, kartLager);
      const procentEl = rot.querySelector<HTMLElement>(".gk-procent");
      if (procentEl) procentEl.textContent = `${formatera(Math.round(op * 100), 0)}\u202F%`;
      basLagerListan = bas?.lagerListan() ?? [];
      synkaKarta();
      return;
    }
    if (s.dataset.tidSlider !== undefined && tidsserie) {
      stoppaTidTimer();
      const idx = Math.min(tidsserie.tidssteg.length - 1, Math.max(0, Math.round(Number(s.value))));
      tidTillstand = { ...tidTillstand, spelar: false, aktivtSteg: idx };
      synkaKarta();
      ritaBottenDock();
      return;
    }
    if (s.id === "ar-svep") {
      svep = Number(s.value) / 100;
      el("ar-b").style.clipPath = `inset(0 0 0 ${Math.round(svep * 1000) / 10}%)`;
      vy3d?.uppdatera?.();
      return;
    }
    if (s.id === "ar-namn") {
      ritaSparStatus();
      return;
    }
    if (s.id === "ar-cad-langd") {
      cadLangdText = s.value;
      cadFel = null;
      return;
    }
    if (s.id === "ar-cad-baring") {
      cadBaringText = s.value;
      cadFel = null;
      return;
    }
    if (s.id === "ar-cad-e") {
      cadEText = s.value;
      cadFel = null;
      return;
    }
    if (s.id === "ar-cad-n") {
      cadNText = s.value;
      cadFel = null;
      return;
    }
    const slag = s.dataset.grans as VektorSlag | undefined;
    const g = slag ? GRANSER[slag] : undefined;
    if (slag && g) {
      const ut = rot.querySelector(`[data-grans-ut="${slag}"]`);
      if (ut) ut.textContent = `${formateraHogst(gransVarde(g, Number(s.value)), 1)} ${g.enhet}`;
    }
  });

  lyssna(rot, "change", (h) => {
    const m = h.target as HTMLInputElement | HTMLSelectElement;
    if (m.dataset.skiktBas) {
      kartLager.sattSynlig(m.dataset.skiktBas, (m as HTMLInputElement).checked);
      basLagerListan = bas?.lagerListan() ?? [];
      synkaKarta();
      ritaSkiktPanel();
      return;
    }
    if (m.dataset.skiktVektor) {
      const slag = m.dataset.skiktVektor as VektorSlag;
      if ((m as HTMLInputElement).checked) vektorPa.add(slag);
      else vektorPa.delete(slag);
      if (slag === "sankor" && !vektorPa.has(slag)) {
        sankaHtml = null;
        ritaAvlasning();
      }
      ritaAnalysPanel();
      sattVektorer();
      ritaTecken();
      void laddaVektorer();
      return;
    }
    if (m.dataset.solKlockslag !== undefined) {
      const v = Number(m.value);
      t.sol = { ...t.sol, klockslag_utc: Number.isFinite(v) ? v : STANDARD_KLOCKSLAG_UTC };
      solFel = valideraSolVal(t.sol);
      if (!solFel) sparaTillstand(t);
      ritaAnalysPanel();
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.solKryss) {
      const k = m.dataset.solKryss;
      const checked = (m as HTMLInputElement).checked;
      if (k === "sammanhangande") t.sol = { ...t.sol, sammanhangande: checked };
      else if (k === "inkludera_vsc") t.sol = { ...t.sol, inkludera_vsc: checked };
      solFel = valideraSolVal(t.sol);
      sparaTillstand(t);
      ritaAnalysPanel();
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.solFalt === "egetDatumIso") {
      t.sol = { ...t.sol, datumForval: "eget", egetDatumIso: m.value.trim() };
      solFel = valideraSolVal(t.sol);
      if (!solFel) sparaTillstand(t);
      ritaAnalysPanel();
      ritaRegnOchKor();
      return;
    }
    if (m.dataset.regnFalt) {
      const k = m.dataset.regnFalt;
      if (k === "avtappning") {
        const res = lasAvtappning(m.value);
        avtappningFel = !res.ok;
        if (res.ok) {
          t.avtappning = res.varde;
          sparaTillstand(t);
        }
      } else {
        const v = tolkaRegnTal(m.value);
        if (k === "regn_mm") t.regn = { ...t.regn, regn_mm: v };
        else if (k === "aterkomsttid_ar") t.regn = { ...t.regn, aterkomsttid_ar: v };
        else if (k === "varaktighet_min") t.regn = { ...t.regn, varaktighet_min: v };
        else if (k === "klimatfaktor") t.regn = { ...t.regn, klimatfaktor: v };
        regnFel = valideraRegnVal(t.regn);
        if (!regnFel) sparaTillstand(t);
      }
      ritaRegnOchKor();
      return;
    }
    if (m.id === "ar-vatten-under") {
      visaVattenUnder = (m as HTMLInputElement).checked;
      synkaKarta();
      return;
    }
    if (m.id === "ar-flode-under") {
      visaFlodeUnder = (m as HTMLInputElement).checked;
      synkaKarta();
      return;
    }
    if (m.id === "ar-visa-tak") {
      visaTak = (m as HTMLInputElement).checked;
      synkaKarta();
      return;
    }
    if (m.id === "rs-overfall") {
      kartLager.sattSynlig(OVERFALL_LAGER_ID, (m as HTMLInputElement).checked);
      synkaKarta();
      return;
    }
    if (m.id === "ar-natverk") {
      valtNatverk = m.value;
      void valjVy(vy);
      return;
    }
    if (m.dataset.grans) {
      const slag = m.dataset.grans as VektorSlag;
      const g = GRANSER[slag];
      if (g) {
        vektorGrans[slag] = gransVarde(g, Number(m.value));
        void laddaVektorer();
      }
      return;
    }
    if (m.id === "ar-hojd" && verktyg) {
      const n = Number(m.value.replace(",", "."));
      if (m.value.trim() === "" || !Number.isFinite(n)) {
        meddelande = { typ: "fel", text: "Ange höjden som ett tal i meter." };
        visaHjalp();
        return;
      }
      meddelande = null;
      verktyg.sattHojd(n);
      return;
    }
    if (m.id === "ar-tak" && verktyg) {
      const v = verktyg.val;
      if (!v?.ny) return;
      if (m.value === FORVALD_TAKFORM) t.takform.delete(v.id);
      else t.takform.set(v.id, m.value);
      planeraKontroll();
      return;
    }
    if (m.id === "ar-namn") {
      t.namn = m.value.trim();
      sparaTillstand(t);
      planeraKontroll();
      return;
    }
    if (m.dataset.nyckel && verktyg) {
      const nyckel = m.dataset.nyckel;
      const f = verktyg.linje.figur(verktyg.linje.val);
      if (!f) return;
      faltFel = null;
      if (nyckel === "hornhojd") {
        const text = (m as HTMLInputElement).value;
        const v = tolkaTal(text);
        if (text.trim() !== "" && v === undefined) faltFel = { nyckel, text: "Skriv höjden som ett tal, till exempel 12,5." };
        else verktyg.linje.sattPunktHojd(v);
        if (faltFel) uppdateraFigurPanel();
        return;
      }
      const roll = t.dok.lagerForFigur(f)?.roll ?? "mark";
      const text = m instanceof HTMLInputElement && m.type === "checkbox" ? (m.checked ? "ja" : "nej") : m.value;
      let res = andraFalt(roll, f.egenskaper, nyckel, text, { punkter: f.punkter });
      if (nyckel === "hojdlage" && text === "hornhojder") {
        const l = lutningFranHornhojder(f.punkter);
        if ("fel" in l) {
          faltFel = { nyckel, text: l.fel };
          res = { patch: { niva_rh2000: null, delta_m: null, lutning: null } };
        } else res = { patch: { niva_rh2000: null, delta_m: null, lutning: l.lutning } };
      }
      if ("fel" in res) faltFel = { nyckel, text: res.fel };
      else t.dok.sattFigurEgenskaper(f.id, res.patch);
      uppdateraFigurPanel();
      planeraKontroll();
    }
  });

  // GeoJSON-filimport
  el<HTMLInputElement>("ar-fil").addEventListener(
    "change",
    () => {
      const filFalt = el<HTMLInputElement>("ar-fil");
      const fil = filFalt.files?.[0];
      filFalt.value = "";
      if (!fil) return;
      void fil.text().then((text) => {
        const res = tolkaFil(text);
        if (res.slag === "fel") meddelande = { typ: "fel", text: res.text };
        else if (res.slag === "forslag") {
          t.takform = takformFran(res.andringar);
          t.dok.fran(res.andringar, { kontrakt: "floden-ritlager/v1", objekt: [] });
          meddelande = { typ: "info", text: `Läste in ${res.andringar.length} ändringar ur förslagsfilen.` };
        } else {
          const roller = info?.ritlager?.roller ?? [];
          t.dok.grupp("fil", () => {
            if (res.hus.length > 0) {
              aktiveraRollLager(t.dok, "byggnad", roller);
              for (const h of res.hus) t.dok.laggTillHus(h.ring, h.hojd_m ?? FORVALD_HOJD_M);
            }
            for (const l of res.linjer) {
              aktiveraRollLager(t.dok, l.roll, roller);
              const lid = t.dok.aktivtLager;
              if (lid) t.dok.laggTillFigur("linje", l.punkter, lid, l.egenskaper);
            }
            for (const y of res.ytor) {
              aktiveraRollLager(t.dok, y.roll, roller);
              const lid = t.dok.aktivtLager;
              if (lid) t.dok.laggTillFigur("yta", y.ring, lid, y.egenskaper);
            }
          });
          meddelande = { typ: "info", text: `Läste in ${res.sammanfattning.text}.` };
        }
        visaHjalp();
        planeraKontroll();
      });
    },
    { signal: ac.signal },
  );

  // Skarvar
  el("ar-skarv").addEventListener(
    "click",
    (h) => {
      const k = (h.target as Element).closest<HTMLElement>("[data-skarv],[data-skarv-fynd]");
      if (!k || !info) return;
      if (k.dataset.skarvFynd) {
        const f = skarvar?.fynd.find((x) => String(x.nr) === k.dataset.skarvFynd);
        if (f) a?.anpassa(fyndUtbredning(f));
        return;
      }
      const vad = k.dataset.skarv;
      if (vad === "avbryt") {
        skarvForslag = null;
        skarvFel = null;
      } else if (vad === "jamna") {
        skarvFel = null;
        skarvMeddelande = null;
        skarvForeslar = true;
        const nr = t.dok.andringsNr;
        ritaSkarv();
        void foreslaSkarvatgard(projekt, forslagKropp(t.dok, t.takform))
          .then((res) => {
            skarvForslag = res;
            skarvForslagNr = nr;
          })
          .catch((e: unknown) => {
            skarvFel = e instanceof Error ? e.message : String(e);
          })
          .finally(() => {
            skarvForeslar = false;
            if (!avslutad) ritaSkarv();
          });
      } else if (vad === "tillampa" && skarvForslag?.atgard) {
        if (skarvForslagNr !== t.dok.andringsNr) {
          skarvFel = "Förslaget har ändrats sedan åtgärden föreslogs.";
          skarvForslag = null;
        } else {
          const res = tillampaAtgard(t.dok, skarvForslag.atgard, info.ritlager?.roller ?? []);
          if (res.ok) {
            skarvMeddelande = "Åtgärden är tillämpad (Ångra tar tillbaka den).";
            skarvTillampad = true;
            skarvFel = null;
            skarvForslag = null;
            verktyg?.markera(null);
          } else skarvFel = res.fel;
        }
      }
      ritaSkarv();
    },
    { signal: ac.signal },
  );

  // Sparade förslag
  el("ar-sparade").addEventListener(
    "click",
    (h) => {
      const k = (h.target as Element).closest<HTMLButtonElement>("button");
      if (!k) return;
      const fel = (e: unknown): void => {
        listFel = e instanceof Error ? e.message : String(e);
        ritaSparade();
      };
      if (k.id === "ar-nytt") {
        t.dok.rensa();
        verktyg?.markera(null);
        t.scenario = null;
        t.namn = "";
        t.takform.clear();
        el<HTMLInputElement>("ar-namn").value = "";
        sparat = "";
        kontroll = null;
        sparaTillstand(t);
        planeraKontroll();
        ritaSparade();
      } else if (k.dataset.oppna) {
        listFel = null;
        void oppnaScenario(k.dataset.oppna).catch(fel);
      } else if (k.dataset.bytnamn) {
        const slug = k.dataset.bytnamn;
        const gammalt = scenarier.find((x) => x.slug === slug)?.namn ?? "";
        const nytt = window.prompt("Nytt namn på förslaget", gammalt);
        if (!nytt || nytt.trim() === gammalt) return;
        void bytScenarioNamn(projekt, slug, nytt.trim()).then((d) => {
          if (t.scenario?.slug === slug) {
            t.scenario = { slug: d.slug ?? slug, namn: d.namn };
            t.namn = d.namn;
            el<HTMLInputElement>("ar-namn").value = d.namn;
            sparat = JSON.stringify(forslagKropp(t.dok, t.takform)) + d.namn;
            sparaTillstand(t);
          }
          return lasSparade();
        }, fel);
      } else if (k.dataset.tabort) {
        const slug = k.dataset.tabort;
        const namn = scenarier.find((x) => x.slug === slug)?.namn ?? slug;
        if (!window.confirm(`Ta bort förslaget ${namn}?`)) return;
        void taBortScenario(projekt, slug).then(() => {
          if (t.scenario?.slug === slug) {
            t.scenario = null;
            sparat = "";
            sparaTillstand(t);
          }
          return lasSparade();
        }, fel);
      }
    },
    { signal: ac.signal },
  );

  // Tangentbord på ämnesflikar (piltangenter, Home/End genom alla 6 domäner)
  el("ar-flikar").addEventListener(
    "keydown",
    (h) => {
      const ke = h as KeyboardEvent;
      const k = ke.key;
      if (k === "ArrowRight" || k === "ArrowDown" || k === "ArrowLeft" || k === "ArrowUp" || k === "Home" || k === "End") {
        ke.preventDefault();
        const n = AMNEN_ORDNING.length;
        const idx = Math.max(0, AMNEN_ORDNING.indexOf(amne));
        const nasta =
          k === "Home"
            ? (AMNEN_ORDNING[0] as PlaneringsDoman)
            : k === "End"
              ? (AMNEN_ORDNING[n - 1] as PlaneringsDoman)
              : (AMNEN_ORDNING[(idx + (k === "ArrowRight" || k === "ArrowDown" ? 1 : n - 1)) % n] as PlaneringsDoman);
        void valjAmne(nasta).then(() => rot.querySelector<HTMLElement>(`#ar-flikar [data-amne="${amne}"]`)?.focus());
      }
    },
    { signal: ac.signal },
  );

  // CAD-måttformulär: Enter eller "Lägg till punkt" + öppna/stäng-tillstånd på <details id="ar-cad">
  lyssna(rot, "submit", (h) => {
    const form = (h.target as Element | null)?.closest<HTMLFormElement>("#ar-cad-form");
    if (!form || !verktyg) return;
    h.preventDefault();
    const res = tillampaCadInmatning(verktyg, {
      lage: cadLage,
      langdText: cadLangdText,
      baringText: cadBaringText,
      eText: cadEText,
      nText: cadNText,
    });
    if (!res.ok) {
      cadFel = meddelande?.typ === "fel" ? meddelande.text : res.fel;
      cadOppen = true;
      uppdateraCadPanel(true);
      return;
    }
    cadFel = null;
    cadOppen = true;
    cadManuelltLage = false;
    if (cadLage === "sweref") {
      cadEText = "";
      cadNText = "";
    }
    planeraRitning();
    uppdateraCadPanel(true);
    window.requestAnimationFrame(() => {
      const nastaFalt = rot.querySelector<HTMLInputElement>(cadLage === "matt" ? "#ar-cad-langd" : "#ar-cad-e");
      nastaFalt?.focus();
      nastaFalt?.select();
    });
  });
  rot.addEventListener(
    "toggle",
    (h) => {
      const d = h.target as HTMLDetailsElement | null;
      if (d?.id === "ar-cad") cadOppen = d.open;
    },
    { capture: true, signal: ac.signal },
  );

  // Tangentbord på fokuserad kartbehållare (#ar-a): piltangenter flyttar och läser av kartcentrum (U5c)
  el("ar-a").addEventListener(
    "keydown",
    (h) => {
      if (lage === "3d" || h.ctrlKey || h.metaKey || h.altKey) return;
      if (arPilTangent(h.key)) {
        h.preventDefault();
        h.stopPropagation();
        tangentbordsAvlasning.stegPil(h.key, h.shiftKey);
        return;
      }
      if ((h.key === "Enter" || h.key === " ") && lage !== "redigera" && !mat.aktiv && !sektion.aktiv) {
        h.preventDefault();
        lasAvKartcentrum();
      }
    },
    { capture: true, signal: ac.signal },
  );

  // Tangentbord (Alt+I för kartcentrum, Esc, S för Sektion, M för Mät bredd i alla 2D-lägen, samt ritverktyg)
  const tangent = (h: KeyboardEvent): void => {
    const m = h.target as HTMLElement | null;
    if (m && (m.tagName === "INPUT" || m.tagName === "TEXTAREA" || m.tagName === "SELECT" || m.isContentEditable)) return;
    if (arKartcentrumKortkommando(h)) {
      h.preventDefault();
      lasAvKartcentrum();
      return;
    }
    if (h.key === "Escape") {
      if (bullerKontroller?.avbrytVagRitning()) {
        synkaMarkorStil();
        visaHjalp();
        h.preventDefault();
        return;
      }
      if (sektion.aktiv) {
        if (sektion.avbryt()) h.preventDefault();
        return;
      }
      if (mat.aktiv) {
        if (mat.avbryt()) h.preventDefault();
        return;
      }
      if (centrumAktiv && lage !== "redigera") {
        centrumAktiv = false;
        centrumEtikett = null;
        senasteAvl = null;
        hojdText = null;
        ritaAvlasning();
        h.preventDefault();
        return;
      }
    }
    if (!h.ctrlKey && !h.metaKey && !h.altKey && !h.shiftKey && lage !== "3d") {
      const k = h.key.toLowerCase();
      if (k === "m") {
        h.preventDefault();
        if (sektion.aktiv) sektion.avsluta();
        mat.vaxla();
        return;
      }
      if (k === "s" && (lage !== "redigera" || sektion.aktiv)) {
        h.preventDefault();
        if (mat.aktiv) mat.sattAktiv(false);
        sektion.vaxla();
        return;
      }
    }
    if (lage === "redigera" && verktyg && verktyg.tangent(h)) h.preventDefault();
  };
  document.addEventListener("keydown", tangent);
  avbryt.push(() => document.removeEventListener("keydown", tangent));

  // ---------- Initiering ----------
  const klar = (async () => {
    try {
      [projektSvar, info] = await Promise.all([
        hamtaProjekt(projekt).catch((e: unknown) => {
          if (e instanceof ApiFel && e.status === 404) return null;
          throw e;
        }),
        hamtaProjektInfo(projekt),
        aterladdaSparadeVyer(t, { lageIdagStatus, scenarioStatus, listaScenarier }),
      ]);
    } catch (e) {
      el("ar-laddar").innerHTML = felBeskedHtml(sidFel(e, "Kartan", kontoLage(), `/#/p/${encodeURIComponent(projekt)}/karta`), esc);
      kopplaLaddaOm(el("ar-laddar"));
      return;
    }
    if (avslutad) return;
    el("ar-laddar").hidden = true;

    gyfUtciKontroller.sattStandardProfil(forvaldGyfProfilForKommun(info.kommun, info.geografi));

    const kallText = projektKallaText(info);
    if (kallText) {
      el("ar-projektkalla").textContent = kallText;
      el("ar-projektkalla").hidden = false;
    }

    skapaDokFor(t, info);
    const dok = t.dok;
    if (dok.lager.length === 0) dok.sattLager(info.ritlager?.lager ?? []);

    a = skapaKarta(el("ar-a"), info);
    a.onVyAndrad = () => {
      if (b && a) b.settVy(a.hamtaVy());
      if (lage === "redigera") planeraRitning();
    };
    a.onSkalaAndrad = uppdateraSkalstock;
    a.onMarkor = (e, n) => {
      if (amne === "buller" && bullerKontroller?.arRitarVag()) {
        bullerKontroller.uppdateraForhandPunkt(e, n);
      }
      planeraAvlasning(e, n);
    };
    a.onKlickPunkt = (e, n) => {
      if (lage !== "redigera" && lage !== "3d" && amne === "buller" && bullerKontroller?.hanteraKartKlick(e, n)) {
        synkaMarkorStil();
        visaHjalp();
        return;
      }
      visaSanka(e, n);
    };

    bas = new BasLager(a, info, kartLager);
    bas.registrera(projekt);
    ritaGrundkarta = (): void => {
      const gkEl = el("ar-grundkarta");
      const nu = aktuellGrundkarta(kartLager);
      const tillgangliga = tillgangligaGrundkartor(kartLager);
      gkEl.innerHTML = grundkartvalHtml(nu.val, nu.opacitet, tillgangliga, uiLage.grundkartaOppen);
      gkEl.hidden = false;
    };
    const synkaBas = (): void => {
      if (!bas) return;
      basLagerListan = bas.lagerListan();
      ritaGrundkarta();
      ritaSkiktPanel();
      synkaKarta();
    };
    kartLager.vidAndring(() => {
      ritaGrundkarta();
      ritaSkiktPanel();
    });
    ritaGrundkarta();
    monteraLagerpanel(el("ar-lagerpanel"), kartLager, {
      andrad: synkaBas,
      taBort: (id) => taBortUnderlag(projekt, bas!, id),
      grupper: ["underlag", "geometri"],
    });
    monteraRitlagerpanel(el("ar-ritlager"), dok, info.ritlager?.roller ?? [], { andrad: () => planeraRitning() });
    monteraUnderlagForm(el("ar-underlag"), projekt, bas, ac.signal);
    void bas.ladda(projekt).then(synkaBas);

    // Ritverktyg
    verktyg = new Ritverktyg(
      dok,
      info.byggnader.map((bx) => ({
        id: bx.id,
        ring: bx.ring.map(([x, y]) => ({ x, y })),
        hal: bx.hal.map((hl) => hl.map(([x, y]) => ({ x, y }))),
        hojd_m: bx.hojd_m,
      })),
      {
        meterPerPx: () => a?.meterPerPx() ?? 1,
        meddela: (m) => {
          meddelande = m;
          visaHjalp();
        },
        figurSkapad: (id) => {
          const f = dok.figurer.get(id);
          const roll = f ? dok.lagerForFigur(f)?.roll : undefined;
          if (f && roll === "mark" && hojdlage(f.egenskaper) === undefined) {
            dok.sattFigurEgenskaper(id, { delta_m: 0.5 });
          }
          window.requestAnimationFrame(() => el("ar-figur-falt").querySelector<HTMLElement>("input,select")?.focus());
        },
        husSkapat: () =>
          window.requestAnimationFrame(() => {
            const f = el<HTMLInputElement>("ar-hojd");
            if (!f.disabled) {
              f.focus();
              f.select();
            }
          }),
      },
      [
        ...(info.avgransning_polygon_3011
          ? [{ id: "avgr", punkter: (info.avgransning_polygon_3011.coordinates[0] ?? []).slice(0, -1).map(([x, y]) => ({ x: x as number, y: y as number })), sluten: true }]
          : []),
        ...(info.brytlinjer ?? []).map((br, i) => ({ id: `bl:${i}`, punkter: br.koordinater.map(([x, y]) => ({ x: x as number, y: y as number })), sluten: false })),
      ],
    );
    try {
      verktyg.tangentkarta = lasTangenter(window.localStorage.getItem(LAGRINGSNYCKEL));
    } catch {
      /* Förval gäller. */
    }
    verktyg.vidAndring(planeraRitning);

    // Tangenter i inställningsrutan
    const KOMMANDONAMN: Record<Kommando, string> = {
      valj: "Flytta / markera", hus: "Nytt hus", linje: "Mur / linje", yta: "Mark / yta", laggtill: "Lägg till hörn", tabort: "Ta bort hörn",
      klipp: "Klipp", ihop: "Slå ihop", vand: "Vänd", forenkla: "Förenkla", foljkant: "Följ kant", fast: "Fäst", ratVinkel: "Rät vinkel", angra: "Ångra", goraOm: "Gör om",
    };
    const ritaTangenter = (): void => {
      if (!verktyg) return;
      const k = verktyg.tangentkarta;
      el("ar-tangenter").innerHTML = `<table class="flode-tabell"><tbody>${KOMMANDON.map((c) => `<tr><th scope="row">${esc(KOMMANDONAMN[c])}</th><td><input type="text" readonly data-kommando="${c}" value="${esc(tangentEtikett(k[c]))}" aria-label="Tangent för ${esc(KOMMANDONAMN[c])}" size="6" /></td></tr>`).join("")}</tbody></table>
        <button type="button" class="flode-knapp" id="ar-tangenter-forval">Återställ förval</button>`;
    };
    el("ar-tangenter").addEventListener("keydown", (h) => {
      const m = h.target as HTMLInputElement;
      const c = m.dataset["kommando"] as Kommando | undefined;
      if (!c || h.key === "Tab" || !verktyg) return;
      h.preventDefault();
      const bnd = bindningAv(h);
      if (!bnd) return;
      const res = medBindning(verktyg.tangentkarta, c, bnd);
      verktyg.tangentkarta = res.karta;
      try {
        window.localStorage.setItem(LAGRINGSNYCKEL, sparaTangenter(res.karta));
      } catch {
        /* Lokal lagring otillgänglig. */
      }
      ritaTangenter();
    });
    el("ar-tangenter").addEventListener("click", (h) => {
      if ((h.target as Element).id !== "ar-tangenter-forval" || !verktyg) return;
      verktyg.tangentkarta = lasTangenter(null);
      try {
        window.localStorage.removeItem(LAGRINGSNYCKEL);
      } catch {
        /* Lokal lagring otillgänglig. */
      }
      ritaTangenter();
    });
    ritaTangenter();

    // Pekarhändelser: sektion och mätverktyg har förtur i alla lägen; i Redigera förslag styr Ritverktyg
    a.settPekare({
      ror: (pt, e) => {
        if (sektion.aktiv) {
          sektion.ror(pt);
          return;
        }
        if (mat.aktiv) {
          mat.ror(pt);
          return;
        }
        if (lage === "redigera") verktyg?.ror(pt, e);
      },
      klick: (pt, e) => {
        if (sektion.aktiv) {
          sektion.klick(pt, { shift: e.shift });
          return;
        }
        if (mat.aktiv) {
          mat.klick(pt);
          return;
        }
        if (lage === "redigera") verktyg?.klick(pt, e);
      },
      dubbelklick: () => {
        if (sektion.aktiv) {
          sektion.slutfor();
          return;
        }
        if (mat.aktiv) return;
        if (lage === "redigera") verktyg?.dubbelklick();
      },
      ned: (pt, e) => {
        if (sektion.aktiv || mat.aktiv) return false;
        return lage === "redigera" ? Boolean(verktyg?.ned(pt, e)) : false;
      },
      upp: () => {
        if (lage === "redigera") verktyg?.upp();
      },
      lamnar: () => {
        if (sektion.aktiv) sektion.lamnar();
        if (mat.aktiv) mat.lamnar();
        if (lage === "redigera") verktyg?.lamnar();
      },
    });

    // Ladda ev. sparat förslag
    const namnFalt = el<HTMLInputElement>("ar-namn");
    namnFalt.value = t.namn || t.scenario?.namn || "";
    if (t.scenario && dok.tom()) {
      try {
        const d = await hamtaScenario(projekt, t.scenario.slug);
        if (avslutad) return;
        t.takform = takformFran(d.andringar);
        dok.fran(d.fria_andringar ?? d.andringar, d.ritlager);
        namnFalt.value = d.namn;
        t.namn = d.namn;
        sparat = JSON.stringify(forslagKropp(dok, t.takform)) + d.namn;
      } catch {
        t.scenario = null;
      }
    }
    dok.vidAndring(() => {
      kontroll = null;
      planeraKontroll();
      planera3d();
    });
    ritaSparade();
    void lasSparade();
    if (!dok.tom()) planeraKontroll();

    // Välj initial resultatkälla och vy
    const fraga = window.location.hash.split("?")[1] ?? "";
    const vyUtanLank = new URLSearchParams(fraga).get("vy");
    const vyAvLank = vyUtanLank ? projektSvar?.vyer.find((v) => v.id === vyUtanLank) : undefined;
    kalla = vyAvLank ? "projekt" : t.ritVyer.length > 0 && !t.korning?.lage ? "ritat" : t.lageVyer.length > 0 ? "lage" : "projekt";
    if (vyAvLank) {
      amne = vyAvLank.avsnitt as ResultatAmne;
      onskadVy = vyAvLank.id;
    }
    ritaSkiktPanel();
    ritaRegnOchKor();
    await valjAmne(amne);
    if (lage === "redigera") planeraRitning();
    uppdateraSkalstock(a.meterPerPx());
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => a?.matOm()));
  })();

  return {
    klar,
    avsluta: () => {
      avslutad = true;
      for (const post of vektorAvbryt.values()) post.ctrl.abort();
      vektorAvbryt.clear();
      vektorLaddar.clear();
      stoppaTidTimer();
      if (kontrollTimer !== null) window.clearTimeout(kontrollTimer);
      if (avlTimer !== null) window.clearTimeout(avlTimer);
      if (ritaBegard) window.cancelAnimationFrame(ritaBegard);
      planera3d.avbryt();
      ac.abort();
      for (const f of avbryt) f();
      vy3d?.avmontera();
      vy3dDock?.avmontera();
      b?.avsluta();
      a?.avsluta();
      bas?.avsluta();
    },
  };
}
