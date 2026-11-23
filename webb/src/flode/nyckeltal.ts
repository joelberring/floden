/*
 * Resultatpanelens tal och meningar. Ren kod: tar emot tal ur körningen (mätvärdestabellen och
 * markytorna) och formaterar dem. Ett tal som saknas blir "–"; inget tal räknas fram här.
 */
import { formatera } from "../sidor.ts";

export type Amne = "vatten" | "dagsljus";

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
}

export interface Tal {
  etikett: string;
  varde: string;
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

export function slutsats(amne: Amne, u: Underlag): string {
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

function staplar(poster: Array<[string, number | null, number, string]>): Stapel[] {
  const stor = Math.max(0, ...poster.map(([, v]) => v ?? 0));
  return poster.map(([etikett, v, dec, enhet]) => ({
    etikett,
    varde: tal(v, dec, enhet),
    andel: v === null || stor <= 0 ? (v === null ? null : 0) : v / stor,
  }));
}

export function jamforelse(amne: Amne, u: Underlag): { rubrik: string; staplar: Stapel[] } {
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
