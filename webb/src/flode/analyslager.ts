/*
 * Alla analyser som kartlager i steg 4 ("Visa"): vilka roller en körning har per ämne, vilka lager
 * som hör ihop som före, efter och skillnad, och vektorlagren som bara finns som data (sänkor,
 * flödesvägar, fasadpunkter med VSC). Ren kod utan beroende på kartbiblioteket, så att den går att
 * testa (analyslager.test.ts). Inga värden räknas här: lagren, skalorna och attributen kommer ur
 * serverns svar; här väljs, färgas (efter samma skalor som servern ritar rastren med) och skrivs ut
 * med `tal.ts`.
 *
 * Färgskalorna `PALETTER` och `VATTEN_KLASSER` ska vara desamma som i `src/webb/resultatlager.py`;
 * ett test i tests/webb jämför filerna.
 */
import type { AnalysAntagande, LagerPost } from "../fragor-typer.ts";
import type { AnalysVektor } from "../karta/kartmotor.ts";
import { esc } from "../sidor.ts";
import { swerefTillWgs84 } from "../sweref.ts";
import { formatera, formateraHogst } from "../tal.ts";
import type { ProjektVy } from "../types.ts";

export type VisaAmne = "vatten" | "dagsljus" | "ekologi" | "luft" | "buller";

export const PALETTER: Record<string, readonly string[]> = {
  sekventiell: ["#00204d", "#31446b", "#666970", "#958f78", "#cbba69", "#ffea46"],
  hastighet: ["#440154", "#3b528b", "#21908d", "#5dc863", "#fde725"],
  tid: ["#ffffcc", "#fed976", "#fd8d3c", "#bd0026", "#4d004b"],
  stromtathet: ["#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"],
  luft: ["#0d0887", "#7e03a8", "#cc4678", "#f89441", "#f0f921"],
};

/** Vattendjupets klasser (från djup i meter), samma som rastret. */
export const VATTEN_KLASSER: ReadonlyArray<{ fran: number; farg: string }> = [
  { fran: 0.02, farg: "#e4edf6" },
  { fran: 0.1, farg: "#c9d9ea" },
  { fran: 0.2, farg: "#7fa3c8" },
  { fran: 0.3, farg: "#4f7daa" },
  { fran: 0.5, farg: "#2b5d8c" },
];

const INGEN_FARG = "#9e9e9e";

const hex = (h: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];

/** Färgen på platsen `t` (0 till 1) längs färgstoppen, linjärt mellan stoppen. */
export function skalFarg(stopp: readonly string[], t: number): string {
  if (stopp.length === 0) return INGEN_FARG;
  const c = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0)) * (stopp.length - 1);
  const i = Math.min(Math.floor(c), stopp.length - 2);
  if (stopp.length === 1) return stopp[0] as string;
  const f = c - i;
  const a = hex(stopp[i] as string);
  const b = hex(stopp[i + 1] as string);
  return `#${[0, 1, 2].map((k) => Math.round((a[k] as number) * (1 - f) + (b[k] as number) * f).toString(16).padStart(2, "0")).join("")}`;
}

/** Färgen för värdet `v` på skalan lo..hi (hela skalan i en färg om lo = hi). */
export function varFarg(stopp: readonly string[], v: number, lo: number, hi: number): string {
  return skalFarg(stopp, hi > lo ? (v - lo) / (hi - lo) : 0);
}

/** Vattendjupets klassfärg (null under den lägsta gränsen: inget syns). */
export function djupFarg(djup_m: number): string | null {
  let farg: string | null = null;
  for (const k of VATTEN_KLASSER) if (djup_m >= k.fran) farg = k.farg;
  return farg;
}

// ---------------------------------------------------------------------------------- rasterroller

/** Domänen en lösare hör till -> ämnet i resultatpanelen (geometri, till exempel lutning, visas under skyfall). */
export function amneForDoman(doman: string | undefined): VisaAmne | null {
  if (doman === "vatten" || doman === "geometri") return "vatten";
  if (doman === "dagsljus" || doman === "ekologi" || doman === "luft" || doman === "buller") return doman;
  return null;
}

/** En roll i körningen som kartlager, med lagren för före, efter och skillnad för den förändring som visas. */
export interface LagerVal {
  nyckel: string;
  analys: string;
  roll: string;
  etikett: string;
  enhet: string;
  kontroll: boolean;
  fore: string | null;
  efter: string | null;
  skillnad: string | null;
  /** Skillnaden är efter minus före av de visade lagren (frågan jämförde inte rollen själv). */
  harledd: boolean;
}

const lagerNyckel = (l: Pick<LagerPost, "analys" | "roll">): string => `${l.analys}|${l.roll}`;

/**
 * Rollerna i körningen för ett ämne, för den förändring som `vy` visar (före- och efterscenariot tas
 * ur vyns egna lager). Mått före kontroller, och inom varje grupp frågans primära roll först.
 */
export function lagerVal(lager: readonly LagerPost[], vy: Pick<ProjektVy, "fore_lager" | "efter_lager">, amne: VisaAmne): LagerVal[] {
  const post = (id: string | null): LagerPost | undefined => (id ? lager.find((l) => l.id === id) : undefined);
  const foreScen = post(vy.fore_lager)?.scenario ?? null;
  const efterScen = post(vy.efter_lager)?.scenario ?? null;
  const grupper = new Map<string, LagerPost[]>();
  for (const l of lager) {
    if (amneForDoman(l.doman) !== amne) continue;
    const g = grupper.get(lagerNyckel(l)) ?? [];
    g.push(l);
    grupper.set(lagerNyckel(l), g);
  }
  const ut: LagerVal[] = [];
  for (const [nyckel, g] of grupper) {
    const forste = g[0] as LagerPost;
    const fore = g.find((l) => l.typ !== "skillnad" && l.scenario === foreScen) ?? null;
    const efter = efterScen && efterScen !== foreScen ? (g.find((l) => l.typ !== "skillnad" && l.scenario === efterScen) ?? null) : null;
    const skillnad = g.find((l) => l.typ === "skillnad" && l.fore === foreScen && l.efter === efterScen) ?? null;
    if (!fore && !efter) continue;
    ut.push({
      nyckel,
      analys: forste.analys,
      roll: forste.roll,
      etikett: forste.etikett,
      enhet: forste.enhet,
      kontroll: Boolean(forste.kontroll),
      fore: fore?.id ?? null,
      efter: efter?.id ?? null,
      skillnad: skillnad?.id ?? null,
      harledd: Boolean(skillnad?.harledd),
    });
  }
  const primar = (v: LagerVal): number => (lager.find((l) => l.id === v.fore)?.primar ? 0 : 1);
  return ut.sort((a, b) => Number(a.kontroll) - Number(b.kontroll) || primar(a) - primar(b) || a.etikett.localeCompare(b.etikett, "sv"));
}

/** Vyn med lagren för den valda rollen i stället för frågans egen (samma körning, samma förändring). */
export function vyMedVal(vy: ProjektVy, v: LagerVal | null): ProjektVy {
  if (!v || !v.fore) return vy;
  return { ...vy, fore_lager: v.fore, efter_lager: vy.lage_idag ? null : v.efter, skillnad_lager: vy.lage_idag ? v.fore : (v.skillnad ?? v.fore) };
}

/** Rollen vars lager vyn redan visar (den förvalda i väljaren). */
export function forvaldNyckel(roller: readonly LagerVal[], vy: Pick<ProjektVy, "fore_lager">): string | null {
  return roller.find((v) => v.fore === vy.fore_lager)?.nyckel ?? roller[0]?.nyckel ?? null;
}

/** Texten under väljaren för det valda lagret: vad det är, enhet och om skillnaden saknas eller är härledd. */
export function valText(v: LagerVal, lage: string, lageIdag: boolean): string {
  const enhet = v.enhet ? ` (${v.enhet})` : "";
  if (v.kontroll) return `${v.etikett}. Ett kontrollager: det visar var resultatet är osäkert, inte ett mått.`;
  if (lageIdag) return `${v.etikett}${enhet}.`;
  if (lage === "skillnad" && !v.skillnad) return `${v.etikett}${enhet}: ingen skillnad finns för det här lagret. Välj Före eller Efter.`;
  if (lage === "skillnad" && v.harledd) return `${v.etikett}${enhet}. Skillnaden är efter minus före av de två lagren; frågan jämförde inte det här måttet själv.`;
  return `${v.etikett}${enhet}.`;
}

/** Väljaren "Visa" med rollerna som alternativ (HTML utan DOM-anrop). */
export function visaHtml(roller: readonly LagerVal[], vald: string | null, vektorer: readonly VektorVal[], pa: ReadonlySet<VektorSlag>, text: string): string {
  if (roller.length < 2 && vektorer.length === 0) return "";
  const alt = (v: LagerVal): string => `<option value="${esc(v.nyckel)}"${v.nyckel === vald ? " selected" : ""}>${esc(v.etikett)}${v.enhet ? ` (${esc(v.enhet)})` : ""}</option>`;
  const matt = roller.filter((v) => !v.kontroll);
  const kontroller = roller.filter((v) => v.kontroll);
  const grupper = [matt.length > 0 ? `<optgroup label="Mått">${matt.map(alt).join("")}</optgroup>` : "", kontroller.length > 0 ? `<optgroup label="Kontroller">${kontroller.map(alt).join("")}</optgroup>` : ""].join("");
  const val = roller.length > 1 ? `<label class="flode-liten">Visa <select id="rs-visa-val" aria-label="Visa lager">${grupper}</select></label>` : "";
  const krysser = vektorer.map((x) => `<label class="flode-kryss" title="${esc(x.hjalp)}"><input type="checkbox" data-vektor="${x.slag}" ${pa.has(x.slag) ? "checked" : ""}/> ${esc(x.namn)}</label>`).join("");
  return `<div class="flode-visa" id="rs-visa">${val}${krysser}<p class="flode-liten flode-dampad" id="rs-visa-text">${esc(text)}</p></div>`;
}

// ---------------------------------------------------------------------------------- vektorlager

export type VektorSlag = "sankor" | "flodesvagar" | "vsc";

export interface VektorVal {
  slag: VektorSlag;
  namn: string;
  hjalp: string;
}

const VEKTORER: Record<VektorSlag, VektorVal> = {
  sankor: { slag: "sankor", namn: "Sänkor med volym", hjalp: "Sänkornas yta när de är fulla, med kapacitet i kubikmeter och djup vid fullt." },
  flodesvagar: { slag: "flodesvagar", namn: "Flödesvägar", hjalp: "Hur vattnet rinner mellan sänkorna och ut ur området, färgade efter gångtid." },
  vsc: { slag: "vsc", namn: "Fasadpunkter (VSC)", hjalp: "Dagsljus på fasaden (vertikal himmelskomponent, VSC) i procent per punkt." },
};

/** Vektorlagren som finns för ämnet, ur lösarna körningen använde. */
export function vektorVal(amne: VisaAmne, analyser: readonly Pick<AnalysAntagande, "losare">[]): VektorVal[] {
  const har = (l: string): boolean => analyser.some((a) => a.losare === l);
  if (amne === "vatten" && (har("vatten.statisk_fyllning") || har("vatten.dynamik"))) return [VEKTORER.sankor, VEKTORER.flodesvagar];
  if (amne === "dagsljus" && har("dagsljus.vsc_fasad")) return [VEKTORER.vsc];
  return [];
}

/** Scenariot vektorlagren hämtas för: före i Före (och Svep), annars efter när det finns. */
export function vektorScenario(lager: readonly LagerPost[], vy: Pick<ProjektVy, "fore_lager" | "efter_lager">, lage: string): string | null {
  const sc = (id: string | null): string | null => lager.find((l) => l.id === id)?.scenario ?? null;
  return lage === "efter" || lage === "skillnad" ? (sc(vy.efter_lager) ?? sc(vy.fore_lager)) : sc(vy.fore_lager);
}

export interface VektorSvar {
  slag: VektorSlag;
  scenario: string;
  korning_id: string;
  features: Array<{ id?: string | number; geometry: { type: string; coordinates: unknown }; properties: Record<string, unknown> }>;
  meta: { antal_totalt: number; visade: number };
}

const tal = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const punkt = (p: unknown): [number, number] => {
  const [e, n] = p as [number, number];
  return swerefTillWgs84(e, n);
};
const ring = (r: unknown): Array<[number, number]> => (r as unknown[]).map(punkt);

/** Skala för flödesvägarnas gångtid: lägsta och högsta i svaret (minuter). */
export function gangtidSkala(svar: Pick<VektorSvar, "features">): { lo: number; hi: number } {
  const t = svar.features.map((f) => tal(f.properties.gangtid_s)).filter((x): x is number => x !== null);
  return t.length === 0 ? { lo: 0, hi: 0 } : { lo: Math.min(...t) / 60, hi: Math.max(...t) / 60 };
}

export const VSC_SKALA = { lo: 0, hi: 100 } as const;

export function sankaBeskrivning(p: Record<string, unknown>): string {
  const slut = p.slutsanka === true ? " Slutet område: vattnet har ingen väg ut." : "";
  return `Sänka ${formatera(tal(p.nod_id) ?? 0, 0)}: rymmer ${formatera(tal(p.kapacitet_m3) ?? 0, 1)} m³, djup vid fullt ${formatera(tal(p.djup_m) ?? 0, 2)} m, yta ${formatera(tal(p.yta_m2) ?? 0, 0)} m², avrinningsområde ${formatera(tal(p.avrinningsyta_m2) ?? 0, 0)} m².${slut}`;
}

export function flodesvagBeskrivning(p: Record<string, unknown>): string {
  return `Flödesväg ${formatera(tal(p.flodesvag_id) ?? 0, 0)}: längd ${formatera(tal(p.langd_m) ?? 0, 0)} m, lutning ${formatera((tal(p.lutning) ?? 0) * 100, 1)} %, gångtid ${formatera((tal(p.gangtid_s) ?? 0) / 60, 1)} min.`;
}

export function fasadBeskrivning(p: Record<string, unknown>): string {
  const v = tal(p.vsc_procent);
  const status = typeof p.status === "string" && p.status !== "ok" && p.status !== "" ? ` (${p.status})` : "";
  return `Fasadpunkt på ${formatera(tal(p.z_m) ?? 0, 1)} m höjd: ${v === null ? "VSC saknas" : `VSC ${formatera(v, 1)} %`}${status}.`;
}

/** Serverns vektorsvar (SWEREF-meter) som färdiga figurer för kartan (WGS 84) med färg, bredd och text. */
export function vektorTillKarta(svar: VektorSvar): AnalysVektor {
  const features: GeoJSON.Feature[] = [];
  const etiketter: AnalysVektor["etiketter"] = [];
  const gt = svar.slag === "flodesvagar" ? gangtidSkala(svar) : { lo: 0, hi: 0 };
  for (const f of svar.features) {
    const p = f.properties;
    if (svar.slag === "sankor" && f.geometry.type === "MultiPolygon") {
      const farg = djupFarg(tal(p.djup_m) ?? 0) ?? VATTEN_KLASSER[0]?.farg ?? INGEN_FARG;
      features.push({ type: "Feature", properties: { farg, beskrivning: sankaBeskrivning(p) }, geometry: { type: "MultiPolygon", coordinates: (f.geometry.coordinates as unknown[]).map((pol) => (pol as unknown[]).map(ring)) } });
    } else if (svar.slag === "flodesvagar" && f.geometry.type === "LineString") {
      const farg = varFarg(PALETTER.tid as readonly string[], (tal(p.gangtid_s) ?? 0) / 60, gt.lo, gt.hi);
      features.push({ type: "Feature", properties: { farg, bredd: 3, beskrivning: flodesvagBeskrivning(p) }, geometry: { type: "LineString", coordinates: ring(f.geometry.coordinates) } });
    } else if (svar.slag === "vsc" && f.geometry.type === "Point") {
      const v = tal(p.vsc_procent);
      const farg = v === null ? INGEN_FARG : varFarg(PALETTER.sekventiell as readonly string[], v, VSC_SKALA.lo, VSC_SKALA.hi);
      features.push({ type: "Feature", properties: { farg, beskrivning: fasadBeskrivning(p) }, geometry: { type: "Point", coordinates: punkt(f.geometry.coordinates) } });
    }
  }
  if (svar.slag === "sankor") {
    // De största sänkorna får en etikett med volymen (svaret är sorterat med störst först).
    for (const f of svar.features.slice(0, ETIKETTER_MAX)) {
      const mitt = f.properties.mitt as [number, number] | undefined;
      const kap = tal(f.properties.kapacitet_m3);
      if (!mitt || kap === null || kap < ETIKETT_MIN_M3) continue;
      const [lng, lat] = swerefTillWgs84(mitt[0], mitt[1]);
      etiketter.push({ lng, lat, text: `${formateraHogst(kap, kap < 10 ? 1 : 0)} m³` });
    }
  }
  return { fc: { type: "FeatureCollection", features }, etiketter };
}

export const ETIKETTER_MAX = 12;
/** Sänkor som rymmer mindre än så här får ingen etikett (de skulle bara skymma kartan). */
export const ETIKETT_MIN_M3 = 1;

/** Teckenförklaringen för ett vektorlager (samma klasser som raster-teckenförklaringen). */
export function vektorTeckenHtml(svar: VektorSvar): string {
  const delvis = svar.meta.visade < svar.meta.antal_totalt ? `<span class="flode-liten flode-dampad">De ${formatera(svar.meta.visade, 0)} största av ${formatera(svar.meta.antal_totalt, 0)} visas.</span>` : "";
  if (svar.slag === "sankor") {
    const klasser = VATTEN_KLASSER.map((k) => `<span><i style="background:${k.farg}"></i>från ${formateraHogst(k.fran, 2)} m</span>`).join("");
    return `<span class="flode-kort-rubrik">Sänkor: djup vid fullt, etikett = volym (m³)</span><span class="flode-klasser">${klasser}</span>${delvis}`;
  }
  if (svar.slag === "flodesvagar") {
    const { lo, hi } = gangtidSkala(svar);
    return `<span class="flode-kort-rubrik">Flödesvägar: gångtid</span><span class="flode-band" style="background:linear-gradient(90deg,${(PALETTER.tid as readonly string[]).join(",")})"></span><span class="flode-bandtext"><span>${formatera(lo, 1)}</span><span>${formatera(hi, 1)} min</span></span>${delvis}`;
  }
  return `<span class="flode-kort-rubrik">Fasadpunkter: VSC</span><span class="flode-band" style="background:linear-gradient(90deg,${(PALETTER.sekventiell as readonly string[]).join(",")})"></span><span class="flode-bandtext"><span>${formatera(VSC_SKALA.lo, 0)}</span><span>${formatera(VSC_SKALA.hi, 0)} %</span></span><span class="flode-liten flode-dampad"><i style="background:${INGEN_FARG}"></i> grå: VSC saknas</span>${delvis}`;
}

/** Adressen till vektorlagret (samma behörighet som körningens övriga resultat). */
export function vektorUrl(korningId: string, scenario: string, slag: VektorSlag): string {
  return `/api/korningar/${encodeURIComponent(korningId)}/vektor/${encodeURIComponent(scenario)}/${slag}`;
}
