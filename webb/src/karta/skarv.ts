/*
 * Skarven i klienten (R5): skarvkontrollens fynd (K49 `floden-skarvkontroll/v1`) som figurer i kartan
 * (sänkans yta, pil från botten till överfallet, punkt med nummer), som rader i panelen och åtgärden
 * "Jämna ut skarven" (K49-tillägget `floden-skarvatgard/v1`) som tillämpas på planförslagets objekt.
 *
 * Ren logik utan webbläsare. Klienten räknar inget: alla tal (djup, yta, volym) kommer ur serverns
 * kontroll och skrivs bara ut med `tal.ts`. Åtgärdsförslaget tas fram och verifieras av servern
 * (`sankgraf.skarvatgard`, deterministiskt); här tillämpas det på objekten efter bekräftelse.
 */
import { esc } from "../sidor.ts";
import { formatera } from "../tal.ts";
import type { Punkt } from "./geom.ts";
import type { PlanDok } from "./plandok.ts";
import { nyttLager, type RollInfo } from "./ritlager.ts";

export type GeoYta = { type: "Polygon"; coordinates: number[][][] } | { type: "MultiPolygon"; coordinates: number[][][][] };

/** Ett fynd ur `skarvar.fynd` (K49). `omrade` är sänkans yttre kant (additivt, R5). */
export interface SkarvFynd {
  nr: number;
  typ: "ny" | "djupare";
  x_m: number;
  y_m: number;
  botten_rh2000: number;
  overfall_rh2000: number;
  overfall_x_m: number;
  overfall_y_m: number;
  storsta_djup_m: number;
  djup_fore_m: number;
  yta_m2: number;
  volym_m3: number;
  tillkommande_volym_m3: number;
  avstand_till_kant_m: number;
  atgard: string;
  omrade?: GeoYta;
}

export interface Skarvar {
  kontrakt: string;
  band_m: number;
  antal: number;
  tillkommande_volym_m3: number;
  ovriga_nya_antal: number;
  ovriga_tillkommande_volym_m3: number;
  fynd: SkarvFynd[];
}

/** Svaret från `POST /api/kartverktyg/<projekt>/skarvatgard` (`floden-skarvatgard/v1`). */
export interface Utfall {
  antal: number;
  tillkommande_volym_m3: number;
}

export interface AtgardAndra {
  andring_id: string;
  egenskaper: Record<string, unknown>;
}

export interface AtgardNy {
  typ: string;
  andring_id: string;
  geometri: { type: "LineString"; coordinates: number[][] };
  egenskaper: Record<string, unknown>;
}

export interface Atgard {
  slag: "slant" | "utlopp" | "kombination";
  text: string;
  steg: Array<{ slag: string; text: string }>;
  mal_id: string;
  andra: AtgardAndra[];
  nya: AtgardNy[];
  efter: Utfall;
  loser_allt: boolean;
}

export interface AtgardSvar {
  kontrakt: string;
  fore: Utfall;
  atgard: Atgard | null;
  skal: string;
  skarvar?: Skarvar | null;
}

// ------------------------------------------------------------------ texter (tal via tal.ts)

export const fyndNamn = (f: Pick<SkarvFynd, "typ">): string => (f.typ === "ny" ? "Ny sänka" : "Djupare sänka");

/** Djup, yta och volym med enheter (utan nummer). */
export function fyndBeskrivning(f: SkarvFynd): string {
  return `${fyndNamn(f)}: största djup ${formatera(f.storsta_djup_m, 2)} m, yta ${formatera(f.yta_m2, 0)} m², volym ${formatera(f.volym_m3, 1)} m³ (nytt ${formatera(f.tillkommande_volym_m3, 1)} m³)`;
}

/** Raden i rutan i kartan: numret och beskrivningen. */
export const fyndText = (f: SkarvFynd): string => `${formatera(f.nr, 0)}. ${fyndBeskrivning(f)}`;

/** Överfallets rad: var vattnet rinner över och på vilken nivå. */
export function overfallText(f: SkarvFynd): string {
  return `Överfall ${formatera(f.overfall_x_m, 0)} E, ${formatera(f.overfall_y_m, 0)} N på ${formatera(f.overfall_rh2000, 2)} m RH 2000`;
}

export function utfallText(u: Utfall): string {
  if (u.antal === 0) return "inga sänkor vid skarven";
  return `${formatera(u.antal, 0)} ${u.antal === 1 ? "sänka" : "sänkor"} vid skarven, ${formatera(u.tillkommande_volym_m3, 1)} m³ nytt`;
}

// ------------------------------------------------------------------ figurer i kartan

export interface SkarvFigurer {
  /** Sänkornas yta: yttre ringar (SWEREF). Utan `omrade` ritas en cirkel med samma yta (`yta_m2`). */
  ytor: Array<{ nr: number; ring: Punkt[] }>;
  /** Överfallspilen från botten till överfallet: linjen och pilspetsens triangel. */
  pilar: Array<{ nr: number; linje: Punkt[]; spets: Punkt[] }>;
  /** Punkten vid botten med nummer och rutans text. */
  punkter: Array<{ nr: number; p: Punkt; text: string }>;
}

const ring = (r: number[][]): Punkt[] => r.map(([x, y]) => ({ x: x as number, y: y as number }));

/** Ytterringarna i ett fynds `omrade` (en per delyta). Tom lista om fyndet saknar område. */
export function omradesRingar(o: GeoYta | undefined): Punkt[][] {
  if (!o) return [];
  if (o.type === "Polygon") return o.coordinates[0] ? [ring(o.coordinates[0])] : [];
  return o.coordinates.flatMap((p) => (p[0] ? [ring(p[0])] : []));
}

/** Cirkel (24 hörn) med arean `yta_m2` runt `mitt`: används när fyndet inte har någon kant. */
export function likaStorCirkel(mitt: Punkt, yta_m2: number): Punkt[] {
  const r = Math.sqrt(Math.max(yta_m2, 0) / Math.PI);
  return Array.from({ length: 24 }, (_, i) => ({ x: mitt.x + r * Math.cos((i / 24) * 2 * Math.PI), y: mitt.y + r * Math.sin((i / 24) * 2 * Math.PI) }));
}

/** Pilspetsens triangel vid `till`, riktad bort från `fran`; `storlek` är spetsens längd i meter. */
export function pilspets(fran: Punkt, till: Punkt, storlek: number): Punkt[] {
  const dx = till.x - fran.x;
  const dy = till.y - fran.y;
  const l = Math.hypot(dx, dy);
  if (l < 1e-9 || storlek <= 0) return [];
  const ux = dx / l;
  const uy = dy / l;
  const s = Math.min(storlek, l * 0.6);
  const bx = till.x - ux * s;
  const by = till.y - uy * s;
  return [till, { x: bx - uy * s * 0.45, y: by + ux * s * 0.45 }, { x: bx + uy * s * 0.45, y: by - ux * s * 0.45 }];
}

/** Fynden som figurer. `meterPerPx` ger pilspetsens storlek (cirka 12 pixlar, minst 1 m). */
export function skarvFigurer(s: Skarvar | null | undefined, meterPerPx = 0.2): SkarvFigurer {
  const ut: SkarvFigurer = { ytor: [], pilar: [], punkter: [] };
  if (!s) return ut;
  for (const f of s.fynd) {
    const botten = { x: f.x_m, y: f.y_m };
    const over = { x: f.overfall_x_m, y: f.overfall_y_m };
    const ringar = omradesRingar(f.omrade);
    if (ringar.length === 0) ringar.push(likaStorCirkel(botten, f.yta_m2));
    for (const r of ringar) ut.ytor.push({ nr: f.nr, ring: r });
    ut.pilar.push({ nr: f.nr, linje: [botten, over], spets: pilspets(botten, over, Math.max(1, 12 * meterPerPx)) });
    ut.punkter.push({ nr: f.nr, p: botten, text: `${fyndText(f)}. ${overfallText(f)}.` });
  }
  return ut;
}

/** Fyndets utbredning (för att zooma till det): sänkans kant, botten och överfall, med marginal 10 m. */
export function fyndUtbredning(f: SkarvFynd): { x_min: number; y_min: number; x_max: number; y_max: number } {
  const p: Punkt[] = [{ x: f.x_m, y: f.y_m }, { x: f.overfall_x_m, y: f.overfall_y_m }, ...omradesRingar(f.omrade).flat()];
  const xs = p.map((q) => q.x);
  const ys = p.map((q) => q.y);
  return { x_min: Math.min(...xs) - 10, y_min: Math.min(...ys) - 10, x_max: Math.max(...xs) + 10, y_max: Math.max(...ys) + 10 };
}

// ------------------------------------------------------------------ åtgärden på objekten

export type TillampaSvar = { ok: true; andrade: string[]; nya: string[] } | { ok: false; fel: string };

/**
 * Tillämpar ett åtgärdsförslag på planförslagets objekt som ett steg för ångra: nya egenskaper på ytan
 * (släntens förhållande) och nya objekt (utlopp som linje i ett öppningslager; lagret skapas om det saknas).
 * Allt eller inget: finns ytan inte som ritat objekt (t.ex. kommer den ur en fil) ändras ingenting.
 */
export function tillampaAtgard(dok: PlanDok, a: Atgard, roller: readonly RollInfo[] = []): TillampaSvar {
  for (const r of a.andra) {
    if (!dok.figurer.has(r.andring_id)) return { ok: false, fel: `Ytan ${r.andring_id} är inte ritad i det här förslaget (den kommer ur en fil eller ett annat verktyg), så den kan inte ändras här. Ändra den där den kommer ifrån.` };
  }
  for (const n of a.nya) {
    if (n.typ !== "oppning_ny") return { ok: false, fel: `Klienten kan inte lägga ett objekt av typen ${n.typ}.` };
  }
  const andrade: string[] = [];
  const nya: string[] = [];
  dok.grupp("skarv-atgard", () => {
    for (const r of a.andra) {
      dok.sattFigurEgenskaper(r.andring_id, r.egenskaper);
      andrade.push(r.andring_id);
    }
    for (const n of a.nya) {
      let lager = dok.lager.find((l) => l.roll === "oppning");
      if (!lager) {
        lager = nyttLager(dok.lager, "oppning", "Passage och trumma", roller);
        dok.laggTillLager(lager);
      }
      const id = dok.figurer.has(n.andring_id) || dok.ny.has(n.andring_id) ? dok.nyttFigurId("linje") : n.andring_id;
      dok.laggTillFigur("linje", n.geometri.coordinates.map(([x, y]) => ({ x: x as number, y: y as number })), lager.lager_id, n.egenskaper, id);
      nya.push(id);
    }
  });
  return { ok: true, andrade, nya };
}

// ------------------------------------------------------------------ panelen

export interface SkarvPanelTillstand {
  skarvar: Skarvar | null | undefined;
  /** Kontrollen körs om (fynden kan vara föråldrade). */
  vantar: boolean;
  /** Åtgärden tas fram hos servern. */
  foreslar: boolean;
  forslag: AtgardSvar | null;
  fel: string | null;
  /** Text efter att en åtgärd tillämpats. */
  meddelande: string | null;
}

/** Panelen "Skarvar": fynden som lista, knappen "Jämna ut skarven" och förslaget med Tillämpa och Avbryt. */
export function skarvPanelHtml(t: SkarvPanelTillstand): string {
  const s = t.skarvar;
  if (!s || s.fynd.length === 0) return t.meddelande ? `<p class="flode-liten" role="status">${esc(t.meddelande)}</p>` : "";
  const rader = s.fynd
    .map(
      (f) => `<li class="skarv-fynd"><button type="button" class="skarv-nr" data-skarv-fynd="${f.nr}" aria-label="Visa sänka ${formatera(f.nr, 0)} i kartan">${formatera(f.nr, 0)}</button>
        <span>${esc(fyndBeskrivning(f))}<br /><span class="flode-dampad flode-liten">${esc(overfallText(f))}</span></span></li>`,
    )
    .join("");
  let atgard = "";
  if (t.foreslar) atgard = `<p class="flode-liten flode-dampad" role="status">Prövar åtgärder och kontrollerar dem …</p>`;
  else if (t.forslag?.atgard) {
    const a = t.forslag.atgard;
    const steg = a.steg.length > 1 ? `<ol class="flode-liten">${a.steg.map((x) => `<li>${esc(x.text)}</li>`).join("")}</ol>` : `<p>${esc(a.text)}</p>`;
    atgard = `<div class="skarv-forslag" role="group" aria-label="Föreslagen åtgärd">
      <h3 class="skarv-rubrik">Föreslagen åtgärd</h3>${steg}
      <p class="flode-liten">Före: ${esc(utfallText(t.forslag.fore))}. Efter åtgärden: ${esc(utfallText(a.efter))}.${a.loser_allt ? "" : " Åtgärden tar inte bort alla sänkor."}</p>
      <div class="flode-rad"><button type="button" class="flode-knapp svart" data-skarv="tillampa">Tillämpa åtgärden</button><button type="button" class="flode-knapp" data-skarv="avbryt">Avbryt</button></div></div>`;
  } else if (t.forslag) {
    atgard = `<p class="flode-liten" role="status">${esc(t.forslag.skal || "Ingen åtgärd hittades.")}</p>`;
  }
  const knapp = t.forslag?.atgard || t.foreslar ? "" : `<div class="flode-rad"><button type="button" class="flode-knapp" data-skarv="jamna"${t.vantar ? " disabled" : ""}>Jämna ut skarven</button></div>`;
  const ovriga = s.ovriga_nya_antal > 0 ? `<p class="flode-liten flode-dampad">Dessutom ${formatera(s.ovriga_nya_antal, 0)} sänka(or) som inte ligger vid skarven (${formatera(s.ovriga_tillkommande_volym_m3, 1)} m³ nytt).</p>` : "";
  return `<h2 class="flode-h2" id="skarv-rubrik">Skarvar</h2>
    <p class="flode-liten">Kontrollen hittade ${formatera(s.fynd.length, 0)} ${s.fynd.length === 1 ? "ny eller djupare sänka" : "nya eller djupare sänkor"} vid kanten på ny mark, ${formatera(s.tillkommande_volym_m3, 1)} m³ nytt${t.vantar ? " (räknas om)" : ""}.</p>
    <ul class="skarv-lista">${rader}</ul>${ovriga}${knapp}${atgard}
    ${t.fel ? `<p class="flode-fel flode-liten" role="alert">${esc(t.fel)}</p>` : ""}
    ${t.meddelande ? `<p class="flode-liten" role="status">${esc(t.meddelande)}</p>` : ""}
    <p class="flode-liten flode-dampad">Sänkorna är en tidig varning ur markmodellen (före och efter), inte vattenberäkningen. Ytan i kartan är sänkan vid fullt vatten upp till överfallet.</p>`;
}
