/*
 * Resultatpanelens tal och meningar. Ren kod: tar emot tal ur körningen (mätvärdestabellen och
 * markytorna) och formaterar dem. Ett tal som saknas blir "–"; inget tal räknas fram här.
 */
import { formatera } from "../sidor.ts";

export type Amne = "vatten" | "dagsljus" | "ekologi";

export interface Marksiffror {
  yta_minskning_m2: number;
  yta_okning_m2: number;
  max_minskning: number;
  max_okning: number;
}

export interface Underlag {
  /** Tröskeln som ytorna räknas över (m för vatten, timmar för sol). */
  troskel: number | null;
  siffror: Marksiffror | null;
  foreDjup_m?: number | null;
  efterDjup_m?: number | null;
  foreLagrat_m3?: number | null;
  efterLagrat_m3?: number | null;
  /** Svaret från `POST /api/analys/ekologi` (K45); null när det saknas. */
  ekologi?: EkologiSvar | null;
}

export interface EkologiLage {
  ECA_m2?: number | null;
  ECA_intervall_m2?: [number, number] | null;
  PC?: number | null;
  PC_intervall?: [number, number] | null;
  antal_flackar?: number | null;
}

/** Det av `floden-ekologi/v1` som panelen visar. */
export interface EkologiSvar {
  fore?: EkologiLage;
  efter?: EkologiLage;
  delta_eca_m2?: number | null;
  delta_eca_procent?: number | null;
  delta_pc?: number | null;
  varningar?: string[];
  artprofil_id?: string;
  /** Nätverket (K46) som kördes; null för en föråldrad artprofil. */
  natverk?: { natverk_id?: string; namn?: string; signalarter?: string[]; antaganden?: string[] } | null;
  /** En rad med signalart och källa, t.ex. "Signalarter: tofsmes, talltita. Källa: …, bil. 2 …". */
  kalla_rad?: string | null;
  antaganden?: string[];
  intervall_typ?: string;
}

/** Signalart och källa på en rad, ur svaret; tom text när svaret saknar nätverk. */
export function kallRad(e: EkologiSvar | null | undefined): string {
  return typeof e?.kalla_rad === "string" ? e.kalla_rad : "";
}

/** Antaganden som ska synas, t.ex. "p saknas i källan; d50 = tröskel/2 (500 effektiva m)." */
export function antagandeRader(e: EkologiSvar | null | undefined): string[] {
  return Array.isArray(e?.antaganden) ? e.antaganden.filter((x): x is string => typeof x === "string") : [];
}

/** Förklaring av vad intervallet är; källbaserat för nätverk. */
export function intervallForklaring(e: EkologiSvar | null | undefined): string {
  return e?.intervall_typ === "kallbaserat"
    ? "Intervallet är lägsta och högsta värde mellan källans scenarier."
    : "";
}

export interface Tal {
  etikett: string;
  varde: string;
  /** Liten rad under talet, t.ex. osäkerhetsintervallet. */
  under?: string;
}

export interface Stapel {
  etikett: string;
  varde: string;
  /** 0–1 av största stapeln; null när talet saknas. */
  andel: number | null;
}

export function tal(v: number | null | undefined, decimaler: number, enhet: string): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return `${formatera(v, decimaler)} ${enhet}`;
}

const nummer = (v: number | null | undefined): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function troskelText(amne: Amne, t: number | null): string {
  if (t === null) return "tröskeln";
  if (amne === "vatten") return t < 1 ? `${formatera(t * 100, 0)} cm` : `${formatera(t, 0)} m`;
  return `${formatera(t, 0)} ${t === 1 ? "timme" : "timmar"}`;
}

/** Enhet för en yta: hektar (1 ha = 10 000 m²) när den är minst en hektar, annars m². */
const arHektar = (v: number): boolean => Math.abs(v) >= 10000;

function ytaI(v: number, hektar: boolean, decimaler: number): string {
  return hektar ? tal(v / 10000, 2, "ha") : tal(v, decimaler, "m²");
}

/** Yta i m² eller ha. Saknat tal blir "–". */
export function yta(v: number | null | undefined, decimaler = 0): string {
  const n = nummer(v);
  return n === null ? "–" : ytaI(n, arHektar(n), decimaler);
}

/** Yta med intervall i samma enhet som värdet; intervallet utelämnas när det saknas. */
export function ytaMedIntervall(v: number | null | undefined, iv: readonly number[] | null | undefined): string {
  const n = nummer(v);
  if (n === null) return "–";
  const h = arHektar(n);
  const i = intervallText(iv, (x) => ytaI(x, h, 0));
  return i ? `${ytaI(n, h, 0)} (${i})` : ytaI(n, h, 0);
}

/** "a till b" för ett giltigt intervall, annars tom text. */
function intervallText(iv: readonly number[] | null | undefined, f: (x: number) => string): string {
  if (!iv || iv.length !== 2 || !Number.isFinite(iv[0]) || !Number.isFinite(iv[1])) return "";
  return `${f(iv[0] as number)} till ${f(iv[1] as number)}`;
}

/** ECA visas alltid i hektar, så att före, efter och staplar har samma enhet. */
const ecaHa = (v: number): string => tal(v / 10000, 2, "ha");

function slutsatsEkologi(e: EkologiSvar | null | undefined): string {
  const d = nummer(e?.delta_eca_m2);
  if (d === null) return "Resultatet har ingen ekologisk konnektivitet att sammanfatta.";
  const pst = nummer(e?.delta_eca_procent);
  const andel = pst === null ? "" : ` (${tal(Math.abs(pst), 1, "%")})`;
  if (d < 0) return `Förslaget minskar den effektiva konnektivitetsytan (ECA) med ${yta(Math.abs(d))}${andel}.`;
  if (d > 0) return `Beräkningen ger en ökning av ECA med ${yta(d)}${andel}; kontrollera den innan den tolkas som en förbättring.`;
  return "Förslaget ändrar inte ECA.";
}

export function slutsats(amne: Amne, u: Underlag): string {
  if (amne === "ekologi") return slutsatsEkologi(u.ekologi);
  const s = u.siffror;
  if (!s) return "Resultatet har inga markvärden att sammanfatta.";
  const tr = troskelText(amne, u.troskel);
  if (amne === "vatten") {
    if (s.yta_okning_m2 <= 0) return `Vattnet blir inte djupare än ${tr} på någon mark.`;
    return `Vattnet blir djupare än ${tr} på ${tal(s.yta_okning_m2, 0, "m²")} mark.`;
  }
  if (s.yta_minskning_m2 <= 0) return `Ingen mark förlorar mer än ${tr} sol.`;
  return `${tal(s.yta_minskning_m2, 0, "m²")} mark förlorar mer än ${tr} sol.`;
}

export function nyckeltal(amne: Amne, u: Underlag): Tal[] {
  const s = u.siffror;
  if (amne === "ekologi") {
    const e = u.ekologi;
    const f = e?.fore;
    const ef = e?.efter;
    const flackar = (v: number | null | undefined): string => (nummer(v) === null ? "–" : formatera(v as number, 0));
    return [
      { etikett: "ECA före", varde: nummer(f?.ECA_m2) === null ? "–" : ecaHa(f?.ECA_m2 as number), under: intervallText(f?.ECA_intervall_m2, ecaHa) },
      { etikett: "ECA efter", varde: nummer(ef?.ECA_m2) === null ? "–" : ecaHa(ef?.ECA_m2 as number), under: intervallText(ef?.ECA_intervall_m2, ecaHa) },
      { etikett: "Förändring av ECA", varde: yta(e?.delta_eca_m2) },
      { etikett: "PC före", varde: tal(nummer(f?.PC), 4, "").trim(), under: intervallText(f?.PC_intervall, (x) => tal(x, 4, "").trim()) },
      { etikett: "PC efter", varde: tal(nummer(ef?.PC), 4, "").trim(), under: intervallText(ef?.PC_intervall, (x) => tal(x, 4, "").trim()) },
      { etikett: "Habitatfläckar före och efter", varde: nummer(f?.antal_flackar) === null && nummer(ef?.antal_flackar) === null ? "–" : `${flackar(f?.antal_flackar)} och ${flackar(ef?.antal_flackar)}` },
    ];
  }
  if (amne === "vatten") {
    return [
      { etikett: "Största djup före", varde: tal(nummer(u.foreDjup_m), 2, "m") },
      { etikett: "Största djup efter", varde: tal(nummer(u.efterDjup_m), 2, "m") },
      { etikett: "Största ökning i en ruta", varde: tal(s?.max_okning, 2, "m") },
      { etikett: "Yta där djupet minskar", varde: tal(s?.yta_minskning_m2, 0, "m²") },
    ];
  }
  return [
    { etikett: "Mark som förlorar sol", varde: tal(s?.yta_minskning_m2, 0, "m²") },
    { etikett: "Största minskning", varde: tal(s?.max_minskning, 1, "timmar") },
    { etikett: "Mark som får mer sol", varde: tal(s?.yta_okning_m2, 0, "m²") },
    { etikett: "Största ökning", varde: tal(s?.max_okning, 1, "timmar") },
  ];
}

const ecaHaTal = (v: number | null | undefined): number | null => {
  const n = nummer(v);
  return n === null ? null : n / 10000;
};

function staplar(poster: Array<[string, number | null, number, string]>): Stapel[] {
  const stor = Math.max(0, ...poster.map(([, v]) => v ?? 0));
  return poster.map(([etikett, v, dec, enhet]) => ({
    etikett,
    varde: tal(v, dec, enhet),
    andel: v === null || stor <= 0 ? (v === null ? null : 0) : v / stor,
  }));
}

export function jamforelse(amne: Amne, u: Underlag): { rubrik: string; staplar: Stapel[] } {
  if (amne === "ekologi") {
    return {
      rubrik: "Effektiv konnektivitetsyta (ECA)",
      staplar: staplar([
        ["Före", ecaHaTal(u.ekologi?.fore?.ECA_m2), 2, "ha"],
        ["Efter", ecaHaTal(u.ekologi?.efter?.ECA_m2), 2, "ha"],
      ]),
    };
  }
  if (amne === "vatten") {
    return {
      rubrik: "Stående vatten i lågpunkter",
      staplar: staplar([
        ["Före", nummer(u.foreLagrat_m3), 0, "m³"],
        ["Efter", nummer(u.efterLagrat_m3), 0, "m³"],
      ]),
    };
  }
  const tr = troskelText("dagsljus", u.troskel);
  return {
    rubrik: `Mark där solen ändras mer än ${tr}`,
    staplar: staplar([
      ["Mindre sol", nummer(u.siffror?.yta_minskning_m2), 0, "m²"],
      ["Mer sol", nummer(u.siffror?.yta_okning_m2), 0, "m²"],
    ]),
  };
}

export interface RegnPost {
  rapport?: string | null;
  kalla?: string | null;
  aterkomsttid_ar?: number | null;
  varaktighet_min?: number | null;
  klimatfaktor?: number | null;
  regn_mm?: number | null;
}

/** Regnet som lösaren använde, som projektsidan skriver det. */
export function regnText(r: RegnPost | undefined): string | null {
  if (!r || r.regn_mm === null || r.regn_mm === undefined) return null;
  const d: string[] = [r.rapport || r.kalla || "Regn"];
  if (typeof r.aterkomsttid_ar === "number") d.push(`${formatera(r.aterkomsttid_ar, 0)} år`);
  if (typeof r.varaktighet_min === "number") {
    const m = r.varaktighet_min;
    d.push(m % 60 === 0 ? `${m / 60} ${m / 60 === 1 ? "timme" : "timmar"}` : `${formatera(m, 0)} minuter`);
  }
  if (typeof r.klimatfaktor === "number") d.push(`klimatfaktor ${formatera(r.klimatfaktor, 1)}`);
  return `${d.join(", ")} = ${formatera(r.regn_mm, 1)} mm`;
}
