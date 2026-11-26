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
  tillrinning: ["#c6dbef", "#6baed6", "#2171b5", "#08306b"],
  stromtathet: ["#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"],
  luft: ["#0d0887", "#7e03a8", "#cc4678", "#f89441", "#f0f921"],
};

/** Vattendjupets klasser (från djup i meter), samma som rastret. */
export const VATTEN_KLASSER: ReadonlyArray<{ fran: number; farg: string; etikett: string }> = [
  { fran: 0.02, farg: "#cfe8ff", etikett: "0,02–0,10 m · ytavrinning" },
  { fran: 0.1, farg: "#7ec4f8", etikett: "0,10–0,20 m · märkbart vatten" },
  { fran: 0.2, farg: "#2f8be6", etikett: "0,20–0,30 m · svårframkomligt" },
  { fran: 0.3, farg: "#1a52b8", etikett: "0,30–0,50 m · ofarbart / risk" },
  { fran: 0.5, farg: "#182b7a", etikett: "0,50–1,00 m · allvarligt djup" },
  { fran: 1.0, farg: "#3b1363", etikett: "> 1,00 m · mycket djupt" },
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

/** Kända roller med svensk etikett, enhet och kort förklaring (kompletterar serverns metadata). */
export const ROLL_ETIKETTER: Record<string, { etikett: string; enhet: string; hjalp: string; fil?: string }> = {
  ankomsttid: {
    etikett: "Ankomsttid (tid till 0,1 m)",
    enhet: "min",
    fil: "tid_till_0_1m.tif",
    hjalp: "Tid från regnstart tills vattendjupet når 0,1 m (tid_till_0_1m.tif).",
  },
  rannsten_flode: {
    etikett: "Rännstenar (1D-flöde)",
    enhet: "m³/s",
    fil: "rannsten_flode.geojson",
    hjalp: "Beräknat 1D-flöde längs kantsten och rännstenar.",
  },
  skuggmask: {
    etikett: "Ögonblicksskugga",
    enhet: "",
    fil: "skuggmask.tif",
    hjalp: "Binär skuggmask (skugga eller solbelyst mark) vid valt klockslag i UTC.",
  },
  stromtathet: {
    etikett: "Strömtäthet (flaskhalsar)",
    enhet: "",
    fil: "stromtathet.tif",
    hjalp: "Kretsteoretisk strömtäthet som pekar ut spridningsflaskhalsar i landskapet.",
  },
  kansighet: {
    etikett: "Förbättringspotential (känslighet ∂R/∂G)",
    enhet: "",
    fil: "kansighet.tif",
    hjalp: "Adjungerad känslighet ∂R_eff/∂G: visar var åtgärder ger störst förbättring av konnektiviteten.",
  },
};

/** Etikett och enhet för en roll, med reserv från kända svenska etiketter om servern utelämnat texten. */
export function rollEtikett(roll: string, etikett?: string, enhet?: string): { etikett: string; enhet: string } {
  const k = ROLL_ETIKETTER[roll];
  return {
    etikett: etikett && etikett !== roll ? etikett : (k?.etikett ?? etikett ?? roll),
    enhet: enhet || k?.enhet || "",
  };
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
    const info = rollEtikett(forste.roll, forste.etikett, forste.enhet);
    ut.push({
      nyckel,
      analys: forste.analys,
      roll: forste.roll,
      etikett: info.etikett,
      enhet: info.enhet,
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
export function visaHtml(roller: readonly LagerVal[], vald: string | null, vektorer: readonly VektorVal[], pa: ReadonlySet<VektorSlag>, text: string, grans: Partial<Record<VektorSlag, number>> = {}): string {
  if (roller.length < 2 && vektorer.length === 0) return "";
  const alt = (v: LagerVal): string => `<option value="${esc(v.nyckel)}"${v.nyckel === vald ? " selected" : ""}>${esc(v.etikett)}${v.enhet ? ` (${esc(v.enhet)})` : ""}</option>`;
  const matt = roller.filter((v) => !v.kontroll);
  const kontroller = roller.filter((v) => v.kontroll);
  const grupper = [matt.length > 0 ? `<optgroup label="Mått">${matt.map(alt).join("")}</optgroup>` : "", kontroller.length > 0 ? `<optgroup label="Kontroller">${kontroller.map(alt).join("")}</optgroup>` : ""].join("");
  const val = roller.length > 1 ? `<label class="flode-liten">Visa <select id="rs-visa-val" aria-label="Visa lager">${grupper}</select></label>` : "";
  const reglage = (x: VektorVal): string => {
    const g = GRANSER[x.slag];
    if (!g) return "";
    const v = grans[x.slag] ?? g.forval;
    return `<label class="flode-liten flode-grans" data-grans-rad="${x.slag}"${pa.has(x.slag) ? "" : " hidden"}>${esc(g.namn)} <input type="range" min="0" max="${GRANS_STEG}" value="${gransPos(g, v)}" data-grans="${x.slag}" aria-label="${esc(g.namn)}"/> <output data-grans-ut="${x.slag}">${formateraHogst(v, 1)} ${g.enhet}</output></label>`;
  };
  const krysser = vektorer.map((x) => `<label class="flode-kryss" title="${esc(x.hjalp)}"><input type="checkbox" data-vektor="${x.slag}" ${pa.has(x.slag) ? "checked" : ""}/> ${esc(x.namn)}</label>${reglage(x)}`).join("");
  return `<div class="flode-visa" id="rs-visa">${val}${krysser}<p class="flode-liten flode-dampad" id="rs-visa-text">${esc(text)}</p></div>`;
}

// ---------------------------------------------------------------------------------- vektorlager

export type VektorSlag = "sankor" | "flodesvagar" | "rannsten_flode" | "vsc";

/** Reglage som sållar bort smått: minsta tillrinning (m²) för flödesvägar, minsta volym (m³) för sänkor. Logaritmiska. */
export interface Grans {
  param: "min_yta_m2" | "min_volym_m3";
  namn: string;
  enhet: string;
  lo: number;
  hi: number;
  forval: number;
}
export const GRANSER: Partial<Record<VektorSlag, Grans>> = {
  flodesvagar: { param: "min_yta_m2", namn: "Minsta tillrinning", enhet: "m²", lo: 100, hi: 10000, forval: 500 },
  sankor: { param: "min_volym_m3", namn: "Minsta volym", enhet: "m³", lo: 0.1, hi: 100, forval: 1 },
};
const GRANS_STEG = 100;
/** Reglagets läge (0 till 100) till värde, logaritmiskt och avrundat till två värdesiffror. */
export function gransVarde(g: Grans, pos: number): number {
  const v = g.lo * Math.pow(g.hi / g.lo, Math.min(1, Math.max(0, pos / GRANS_STEG)));
  const f = Math.pow(10, Math.floor(Math.log10(v)) - 1);
  return Math.round(v / f) * f;
}
export function gransPos(g: Grans, v: number): number {
  return Math.round((GRANS_STEG * Math.log(v / g.lo)) / Math.log(g.hi / g.lo));
}

export interface VektorVal {
  slag: VektorSlag;
  namn: string;
  hjalp: string;
}

const VEKTORER: Record<VektorSlag, VektorVal> = {
  sankor: { slag: "sankor", namn: "Sänkor med volym", hjalp: "Sänkornas yta när de är fulla, med kapacitet i kubikmeter och djup vid fullt. Sänkor som fylls ihop visas som en." },
  flodesvagar: { slag: "flodesvagar", namn: "Flödesvägar", hjalp: "Avrinningsnätet: små rännilar som går ihop till större stråk nedströms. Bredd och färg visar tillrinningen (m²) och hovring ger ordning (Strahler)." },
  rannsten_flode: { slag: "rannsten_flode", namn: "Rännstenar (1D-flöde)", hjalp: "1D-flöde längs kantsten och rännstenar (m³/s). Färg och bredd visar toppflödet längs varje sträcka." },
  vsc: { slag: "vsc", namn: "Fasadpunkter (VSC)", hjalp: "Dagsljus på fasaden (vertikal himmelskomponent, VSC) i procent per punkt." },
};

/** Vektorlagren som finns för ämnet, ur lösarna körningen använde. */
export function vektorVal(amne: VisaAmne, analyser: readonly Pick<AnalysAntagande, "losare">[]): VektorVal[] {
  const har = (l: string): boolean => analyser.some((a) => a.losare === l);
  if (amne === "vatten") {
    const ut: VektorVal[] = [];
    if (har("vatten.statisk_fyllning") || har("vatten.dynamik")) ut.push(VEKTORER.sankor, VEKTORER.flodesvagar);
    if (har("vatten.2d") || har("vatten.hybrid") || har("vatten.rannsten")) ut.push(VEKTORER.rannsten_flode);
    return ut;
  }
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
  meta: { antal_totalt: number; visade: number; dolda?: number };
}

const tal = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const punkt = (p: unknown): [number, number] => {
  const [e, n] = p as [number, number];
  return swerefTillWgs84(e, n);
};
const ring = (r: unknown): Array<[number, number]> => (r as unknown[]).map(punkt);

/** Skala för flödesvägarnas tillrinning (m²): lägsta och högsta i svaret. Färg och bredd följer logaritmen. */
export function tillrinningSkala(svar: Pick<VektorSvar, "features">): { lo: number; hi: number } {
  const t = svar.features.map((f) => tal(f.properties.tillrinning_m2)).filter((x): x is number => x !== null && x > 0);
  return t.length === 0 ? { lo: 1, hi: 1 } : { lo: Math.min(...t), hi: Math.max(...t) };
}

/** Andel (0 till 1) på den logaritmiska skalan. */
export function logAndel(v: number, lo: number, hi: number): number {
  return hi <= lo || v <= lo ? 0 : Math.min(1, Math.log(v / lo) / Math.log(hi / lo));
}

/** Linjebredd (px) för en flödesväg: smal för rännilar, bred för stråk. */
export function flodesBredd(v: number, lo: number, hi: number): number {
  return 1.2 + 4.8 * logAndel(v, lo, hi);
}

export const VSC_SKALA = { lo: 0, hi: 100 } as const;

export function sankaBeskrivning(p: Record<string, unknown>): string {
  const slut = p.slutsanka === true ? " Slutet område: vattnet har ingen väg ut." : "";
  const delar = (tal(p.delsankor) ?? 1) > 1 ? ` Sammanslagen av ${formatera(tal(p.delsankor) ?? 1, 0)} sänkor som fylls ihop.` : "";
  return `Sänka ${formatera(tal(p.nod_id) ?? 0, 0)}: rymmer ${formatera(tal(p.kapacitet_m3) ?? 0, 1)} m³, djup vid fullt ${formatera(tal(p.djup_m) ?? 0, 2)} m, yta ${formatera(tal(p.yta_m2) ?? 0, 0)} m², avrinningsområde ${formatera(tal(p.avrinningsyta_m2) ?? 0, 0)} m².${delar}${slut}`;
}

export function flodesvagBeskrivning(p: Record<string, unknown>): string {
  return `Flödesväg: tillrinning ${formatera(tal(p.tillrinning_m2) ?? 0, 0)} m², Strahler-ordning ${formatera(tal(p.ordning) ?? 1, 0)}, längd ${formatera(tal(p.langd_m) ?? 0, 0)} m, lutning ${formatera((tal(p.lutning) ?? 0) * 100, 1)} %.`;
}

export function fasadBeskrivning(p: Record<string, unknown>): string {
  const v = tal(p.vsc_procent);
  const status = typeof p.status === "string" && p.status !== "ok" && p.status !== "" ? ` (${p.status})` : "";
  return `Fasadpunkt på ${formatera(tal(p.z_m) ?? 0, 1)} m höjd: ${v === null ? "VSC saknas" : `VSC ${formatera(v, 1)} %`}${status}.`;
}

const rannstenFlode = (p: Record<string, unknown>): number | null =>
  tal(p.flode_m3_s) ?? tal(p.max_flode_m3_s) ?? tal(p.q_max_m3_s);

/** Skala för 1D-rännstenarnas toppflöde (m³/s): lägsta och högsta i svaret. */
export function rannstenSkala(svar: Pick<VektorSvar, "features">): { lo: number; hi: number } {
  const q = svar.features.map((f) => rannstenFlode(f.properties)).filter((x): x is number => x !== null && x >= 0);
  return q.length === 0 ? { lo: 0, hi: 0 } : { lo: Math.min(...q), hi: Math.max(...q) };
}

/** Linjebredd (px) för en 1D-rännsten efter flödet. */
export function rannstenBredd(q: number, lo: number, hi: number): number {
  const andel = hi > lo ? Math.min(1, Math.max(0, (q - lo) / (hi - lo))) : 0.5;
  return 1.8 + 4.2 * andel;
}

export function rannstenBeskrivning(p: Record<string, unknown>): string {
  const q = rannstenFlode(p);
  const djup = tal(p.djup_m) ?? tal(p.max_djup_m);
  const langd = tal(p.langd_m);
  const delar: string[] = [
    q === null ? "Rännsten (1D): flöde saknas" : `Rännsten (1D): toppflöde ${formatera(q, 3)} m³/s`,
  ];
  if (djup !== null) delar.push(`djup ${formatera(djup, 2)} m`);
  if (langd !== null) delar.push(`längd ${formatera(langd, 0)} m`);
  return `${delar.join(", ")}.`;
}

/** Serverns vektorsvar (SWEREF-meter) som färdiga figurer för kartan (WGS 84) med färg, bredd och text. */
export function vektorTillKarta(svar: VektorSvar): AnalysVektor {
  const features: GeoJSON.Feature[] = [];
  const etiketter: AnalysVektor["etiketter"] = [];
  const ts = svar.slag === "flodesvagar" ? tillrinningSkala(svar) : { lo: 1, hi: 1 };
  const rs = svar.slag === "rannsten_flode" ? rannstenSkala(svar) : { lo: 0, hi: 0 };
  for (const f of svar.features) {
    const p = f.properties;
    if (svar.slag === "sankor" && f.geometry.type === "MultiPolygon") {
      const farg = djupFarg(tal(p.djup_m) ?? 0) ?? VATTEN_KLASSER[0]?.farg ?? INGEN_FARG;
      features.push({ type: "Feature", properties: { farg, beskrivning: sankaBeskrivning(p) }, geometry: { type: "MultiPolygon", coordinates: (f.geometry.coordinates as unknown[]).map((pol) => (pol as unknown[]).map(ring)) } });
    } else if (svar.slag === "flodesvagar" && f.geometry.type === "LineString") {
      const yta = tal(p.tillrinning_m2) ?? ts.lo;
      const farg = varFarg(PALETTER.tillrinning as readonly string[], logAndel(yta, ts.lo, ts.hi), 0, 1);
      features.push({ type: "Feature", properties: { farg, bredd: flodesBredd(yta, ts.lo, ts.hi), beskrivning: flodesvagBeskrivning(p) }, geometry: { type: "LineString", coordinates: ring(f.geometry.coordinates) } });
    } else if (svar.slag === "rannsten_flode" && f.geometry.type === "LineString") {
      const q = rannstenFlode(p) ?? rs.lo;
      const farg = varFarg(PALETTER.tillrinning as readonly string[], q, rs.lo, rs.hi);
      features.push({ type: "Feature", properties: { farg, bredd: rannstenBredd(q, rs.lo, rs.hi), beskrivning: rannstenBeskrivning(p) }, geometry: { type: "LineString", coordinates: ring(f.geometry.coordinates) } });
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
  const delvis = svar.meta.visade + (svar.meta.dolda ?? 0) < svar.meta.antal_totalt ? `<span class="flode-liten flode-dampad">De ${formatera(svar.meta.visade, 0)} största av ${formatera(svar.meta.antal_totalt, 0)} visas.</span>` : "";
  if (svar.slag === "sankor") {
    const klasser = VATTEN_KLASSER.map((k) => `<span><i style="background:${k.farg}"></i>från ${formateraHogst(k.fran, 2)} m</span>`).join("");
    const dolda = (svar.meta.dolda ?? 0) > 0 ? `<span class="flode-liten flode-dampad">${formatera(svar.meta.dolda ?? 0, 0)} små sänkor är dolda (reglaget Minsta volym). Klicka på en sänka för vattenytans mått.</span>` : "";
    return `<span class="flode-kort-rubrik">Sänkor: djup vid fullt, etikett = volym (m³)</span><span class="flode-klasser">${klasser}</span>${delvis}${dolda}`;
  }
  if (svar.slag === "flodesvagar") {
    const { lo, hi } = tillrinningSkala(svar);
    return `<span class="flode-kort-rubrik">Flödesvägar: tillrinning (m²)</span><span class="flode-band" style="background:linear-gradient(90deg,${(PALETTER.tillrinning as readonly string[]).join(",")})"></span><span class="flode-bandtext"><span>${formatera(lo, 0)}</span><span>${formatera(hi, 0)} m²</span></span><span class="flode-liten flode-dampad">Bredd och färg följer tillrinningen (logaritmisk).</span>`;
  }
  if (svar.slag === "rannsten_flode") {
    const { lo, hi } = rannstenSkala(svar);
    return `<span class="flode-kort-rubrik">Rännstenar (1D): toppflöde (m³/s)</span><span class="flode-band" style="background:linear-gradient(90deg,${(PALETTER.tillrinning as readonly string[]).join(",")})"></span><span class="flode-bandtext"><span>${formatera(lo, 3)}</span><span>${formatera(hi, 3)} m³/s</span></span><span class="flode-liten flode-dampad">1D-flöde längs kantsten och rännstenar.</span>${delvis}`;
  }
  return `<span class="flode-kort-rubrik">Fasadpunkter: VSC</span><span class="flode-band" style="background:linear-gradient(90deg,${(PALETTER.sekventiell as readonly string[]).join(",")})"></span><span class="flode-bandtext"><span>${formatera(VSC_SKALA.lo, 0)}</span><span>${formatera(VSC_SKALA.hi, 0)} %</span></span><span class="flode-liten flode-dampad"><i style="background:${INGEN_FARG}"></i> grå: VSC saknas</span>${delvis}`;
}

/** Adressen till vektorlagret (samma behörighet som körningens övriga resultat). */
export function vektorUrl(korningId: string, scenario: string, slag: VektorSlag, gransVarden: number | null = null): string {
  const g = GRANSER[slag];
  const fraga = g && gransVarden !== null ? `?${g.param}=${gransVarden}` : "";
  return `/api/korningar/${encodeURIComponent(korningId)}/vektor/${encodeURIComponent(scenario)}/${slag}${fraga}`;
}

/** Svaret från `/api/korningar/<id>/sanka`: den sammanhängande vattenytan i en punkt, per läge. */
export interface SankaLage {
  lager_id: string;
  scenario: string;
  typ: string;
  vatt: boolean;
  max_djup_m?: number;
  medel_djup_m?: number;
  vat_yta_m2?: number;
  volym_m3?: number;
}
export interface SankaSvar {
  e: number;
  n: number;
  grans_m: number;
  lagen: SankaLage[];
  skillnad: Record<string, number> | null;
}

export function sankaUrl(korningId: string, e: number, n: number, scenario: string | null): string {
  const sc = scenario ? `&scenario=${encodeURIComponent(scenario)}` : "";
  return `/api/korningar/${encodeURIComponent(korningId)}/sanka?e=${e}&n=${n}${sc}`;
}

/** Kortets text för vattenytan: största och medeldjup, våt yta och volym före, efter och skillnad. */
export function sankaKortHtml(s: SankaSvar): string {
  const rad = (namn: string, l: SankaLage | undefined): string =>
    !l || !l.vatt
      ? `<span><span class="flode-dampad">${esc(namn)}</span> <strong>ingen vattenyta här</strong></span>`
      : `<span><span class="flode-dampad">${esc(namn)}</span> <strong>största djup ${formatera(l.max_djup_m ?? 0, 2)} m, medeldjup ${formatera(l.medel_djup_m ?? 0, 2)} m, våt yta ${formatera(l.vat_yta_m2 ?? 0, 0)} m², volym ${formatera(l.volym_m3 ?? 0, 1)} m³</strong></span>`;
  const fore = s.lagen.find((l) => l.typ === "bas");
  const efter = s.lagen.find((l) => l.typ === "scenario");
  const t = (v: number | undefined, d: number): string => (v === undefined ? "" : `${v > 0 ? "+" : ""}${formatera(v, d)}`);
  const d = s.skillnad;
  const skill = d ? `<span><span class="flode-dampad">Skillnad</span> <strong>volym ${t(d.volym_m3, 1)} m³, våt yta ${t(d.vat_yta_m2, 0)} m², största djup ${t(d.max_djup_m, 2)} m</strong></span>` : "";
  return `<span class="flode-kort-rubrik">Vattenytan här (djup över ${formatera(s.grans_m * 100, 0)} cm, sammanhängande)</span>${rad("Före", fore)}${efter ? rad("Efter", efter) : ""}${efter ? skill : ""}`;
}
