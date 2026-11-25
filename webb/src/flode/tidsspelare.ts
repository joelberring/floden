/*
 * Tidsspelare och hydrograf för dynamiska skyfallskörningar (`floden-tidsserie/v1`).
 * Ren, DOM-fri modul: tillståndsmaskin, SVG-hydrograf (regnstaplar + volym-/djupkurva + tidslinje)
 * och HTML-mall. Stramt svartvitt formspråk via `var(--f-*)`-tokens; alla tal går genom `../tal.ts`.
 * Inga fysikvärden räknas fram eller gissas här — allt läses ur serverns tidsseriesvar.
 */
import { esc } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";

export interface TidsseriePunkt {
  tid_min: number;
  regn_mm_h: number;
  kumulativt_regn_mm: number;
  max_djup_m: number;
  vat_yta_01m_m2: number;
  vat_yta_03m_m2: number;
  volym_m3: number;
  utflode_kum_m3: number;
  max_hastighet_m_s: number | null;
}

export interface TidsstegPost {
  index: number;
  tid_min: number;
  png_url: string;
}

export interface TidsserieSvar {
  kontrakt: "floden-tidsserie/v1";
  losare: string;
  regntyp: string;
  varaktighet_min: number;
  slut_tid_min: number;
  total_regn_mm: number;
  topp_tid_min: number;
  tidssteg_tid_min: number[];
  punkter: TidsseriePunkt[];
  utbredning?: { x_min: number; y_min: number; x_max: number; y_max: number } | [number, number, number, number];
  tidssteg: TidsstegPost[];
}

export interface TidsspelareTillstand {
  spelar: boolean;
  /** Aktivt index i `svar.tidssteg`, eller `null` för statiskt Maxdjup. */
  aktivtSteg: number | null;
  hastighet: 1 | 2;
}

export function skapaTidsspelareTillstand(): TidsspelareTillstand {
  return { spelar: false, aktivtSteg: null, hastighet: 1 };
}

/** Stegar fram ett steg under uppspelning; wrappar till 0 efter sista steget. */
export function nastaSteg(tillstand: TidsspelareTillstand, antalSteg: number): TidsspelareTillstand {
  if (antalSteg <= 0) return { ...tillstand, spelar: false, aktivtSteg: null };
  const nasta = tillstand.aktivtSteg === null ? 0 : (tillstand.aktivtSteg + 1) % antalSteg;
  return { ...tillstand, aktivtSteg: nasta };
}

/** Hittar det tidssteg i `svar.tidssteg` som ligger närmast `tidMin`. */
export function narmasteStegForTid(svar: TidsserieSvar, tidMin: number): number | null {
  if (svar.tidssteg.length === 0 || !Number.isFinite(tidMin)) return null;
  let bastIdx = 0;
  let bastDiff = Infinity;
  for (let i = 0; i < svar.tidssteg.length; i++) {
    const post = svar.tidssteg[i] as TidsstegPost;
    const diff = Math.abs(post.tid_min - tidMin);
    if (diff < bastDiff) {
      bastDiff = diff;
      bastIdx = i;
    }
  }
  return bastIdx;
}

/** Hoppar direkt till tidssteget närmast `svar.topp_tid_min` och pausar. */
export function hoppaTillTopp(svar: TidsserieSvar, tillstand: TidsspelareTillstand): TidsspelareTillstand {
  const idx = narmasteStegForTid(svar, svar.topp_tid_min);
  return { ...tillstand, spelar: false, aktivtSteg: idx };
}

/** Återgår till statiskt Maxdjup (`aktivtSteg = null`) och pausar. */
export function tillMaxdjup(tillstand: TidsspelareTillstand): TidsspelareTillstand {
  return { ...tillstand, spelar: false, aktivtSteg: null };
}

/** Returnerar punkten i `svar.punkter` närmast aktivt tidssteg, eller `null` vid Maxdjup / tomt svar. */
export function aktuellPunkt(svar: TidsserieSvar, tillstand: TidsspelareTillstand): TidsseriePunkt | null {
  if (tillstand.aktivtSteg === null) return null;
  const steg = svar.tidssteg[tillstand.aktivtSteg];
  if (!steg || svar.punkter.length === 0) return null;
  let bast = svar.punkter[0] as TidsseriePunkt;
  let bastDiff = Math.abs(bast.tid_min - steg.tid_min);
  for (let i = 1; i < svar.punkter.length; i++) {
    const p = svar.punkter[i] as TidsseriePunkt;
    const diff = Math.abs(p.tid_min - steg.tid_min);
    if (diff < bastDiff) {
      bastDiff = diff;
      bast = p;
    }
  }
  return bast;
}

const PAD_V = 6;
const PAD_H = 6;
const PAD_T = 6;
const PAD_B = 14;

/** Konverterar en x-koordinat (px i SVG:n) till tid i minuter, klampad till `[0, slutTidMin]`. */
export function tidMinFranSvgX(x: number, bredd: number, slutTidMin: number): number {
  const inreBredd = Math.max(1, bredd - PAD_V - PAD_H);
  if (!Number.isFinite(slutTidMin) || slutTidMin <= 0 || !Number.isFinite(x)) return 0;
  const andel = Math.min(1, Math.max(0, (x - PAD_V) / inreBredd));
  return andel * slutTidMin;
}

const r2 = (v: number): string => String(Math.round(v * 100) / 100);

/**
 * Kompakt svartvit SVG-hydrograf (~64 px hög):
 * - grå staplar för regnintensitet (`regn_mm_h`), visar direkt CDS-topp vs Block-platå
 * - mörkblå kurva (`#1a52b8`) för `volym_m3` (eller `max_djup_m` om volymen är 0 överallt)
 * - vertikal svart tidslinje vid `aktivTidMin` (om satt)
 */
export function ritaHydrografSvg(
  svar: TidsserieSvar,
  aktivTidMin: number | null,
  bredd = 320,
  hojd = 64,
): string {
  const w = Math.max(80, Math.round(bredd));
  const h = Math.max(40, Math.round(hojd));
  const inreW = Math.max(1, w - PAD_V - PAD_H);
  const inreH = Math.max(1, h - PAD_T - PAD_B);
  const yBas = PAD_T + inreH;
  const punkter = svar.punkter;
  const slutTid = Math.max(
    svar.slut_tid_min > 0 ? svar.slut_tid_min : 0,
    punkter.length > 0 ? Math.max(...punkter.map((p) => p.tid_min)) : 0,
  );
  const xAv = (t: number): number => (slutTid > 0 ? PAD_V + (Math.min(slutTid, Math.max(0, t)) / slutTid) * inreW : PAD_V);

  const delar: string[] = [];

  // Baslinje
  delar.push(
    `<line x1="${PAD_V}" y1="${yBas}" x2="${w - PAD_H}" y2="${yBas}" stroke="var(--f-linje, #d8d8d8)" stroke-width="1" />`,
  );

  if (punkter.length > 0 && slutTid > 0) {
    // 1. Grå regnstaplar (regn_mm_h)
    const maxRegn = Math.max(...punkter.map((p) => (Number.isFinite(p.regn_mm_h) ? Math.max(0, p.regn_mm_h) : 0)));
    if (maxRegn > 0) {
      const stapelBredd = Math.max(2, Math.min(12, inreW / Math.max(punkter.length, 1) - 1));
      for (const p of punkter) {
        const regn = Number.isFinite(p.regn_mm_h) ? Math.max(0, p.regn_mm_h) : 0;
        if (regn <= 0) continue;
        const sh = (regn / maxRegn) * (inreH * 0.92);
        const cx = xAv(p.tid_min);
        const rx = Math.max(PAD_V, Math.min(w - PAD_H - stapelBredd, cx - stapelBredd / 2));
        delar.push(
          `<rect x="${r2(rx)}" y="${r2(yBas - sh)}" width="${r2(stapelBredd)}" height="${r2(sh)}" fill="var(--f-linje-stark, #c8c8c8)" />`,
        );
      }
    }

    // 2. Mörkblå kurva: volym_m3, eller max_djup_m om volymen är 0
    const maxVolym = Math.max(...punkter.map((p) => (Number.isFinite(p.volym_m3) ? Math.max(0, p.volym_m3) : 0)));
    const anvandDjup = maxVolym <= 0;
    const maxKurva = anvandDjup
      ? Math.max(...punkter.map((p) => (Number.isFinite(p.max_djup_m) ? Math.max(0, p.max_djup_m) : 0)))
      : maxVolym;

    if (maxKurva > 0) {
      const koord = punkter
        .map((p) => {
          const val = anvandDjup ? Math.max(0, p.max_djup_m) : Math.max(0, p.volym_m3);
          const px = xAv(p.tid_min);
          const py = yBas - (val / maxKurva) * (inreH * 0.92);
          return `${r2(px)},${r2(py)}`;
        })
        .join(" ");
      delar.push(
        `<polyline fill="none" stroke="#1a52b8" stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round" points="${koord}" />`,
      );
    }
  }

  // 3. Vertikal svart tidslinje vid aktivTidMin
  if (aktivTidMin !== null && Number.isFinite(aktivTidMin) && slutTid > 0) {
    const lx = xAv(aktivTidMin);
    delar.push(
      `<line data-tid-markor x1="${r2(lx)}" y1="${PAD_T}" x2="${r2(lx)}" y2="${yBas}" stroke="var(--f-text, #111)" stroke-width="1.5" />`,
    );
  }

  // Enkla tidsetiketter på x-axeln (0 och slut_tid_min)
  const slutEtikett = slutTid > 0 ? `${formateraHogst(slutTid, 0)} min` : "0 min";
  delar.push(
    `<text x="${PAD_V}" y="${h - 2}" font-size="9" fill="var(--f-dampad, #666)" font-family="var(--f-mono, monospace)">0</text>`,
  );
  delar.push(
    `<text x="${w - PAD_H}" y="${h - 2}" text-anchor="end" font-size="9" fill="var(--f-dampad, #666)" font-family="var(--f-mono, monospace)">${esc(slutEtikett)}</text>`,
  );

  const aria = `Hydrograf, ${svar.regntyp === "cds" ? "CDS-regn" : svar.regntyp === "block" ? "blockregn" : svar.regntyp}, ${formateraHogst(svar.varaktighet_min, 0)} minuter`;
  return `<svg class="tid-hydrograf-svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(aria)}" style="display:block;width:100%;height:${h}px;background:var(--f-yta, #fff);border:1px solid var(--f-linje, #d8d8d8)">${delar.join("")}</svg>`;
}

/** Formaterar statusraden för tidsspelaren (exakt ur serverns tidsserie, aldrig gissade tal). */
export function tidsspelareStatusText(svar: TidsserieSvar, tillstand: TidsspelareTillstand): string {
  const p = aktuellPunkt(svar, tillstand);
  if (p) {
    const tid = formateraHogst(p.tid_min, 0);
    const djupCm = Number.isFinite(p.max_djup_m) ? `${formatera(p.max_djup_m * 100, 0)} cm` : "–";
    const volym = Number.isFinite(p.volym_m3) ? `${formatera(p.volym_m3, 0)} m³` : "–";
    return `t = ${tid} min · Djup ${djupCm} · Volym ${volym}`;
  }
  if (svar.punkter.length === 0) return "Maxdjup · Djup – · Volym –";
  let maxDjup = -Infinity;
  let maxVolym = -Infinity;
  for (const pt of svar.punkter) {
    if (Number.isFinite(pt.max_djup_m) && pt.max_djup_m > maxDjup) maxDjup = pt.max_djup_m;
    if (Number.isFinite(pt.volym_m3) && pt.volym_m3 > maxVolym) maxVolym = pt.volym_m3;
  }
  const djupStr = maxDjup >= 0 ? `${formatera(maxDjup * 100, 0)} cm` : "–";
  const volymStr = maxVolym >= 0 ? `${formatera(maxVolym, 0)} m³` : "–";
  return `Maxdjup · Djup ${djupStr} · Volym ${volymStr}`;
}

/** Stram svartvit kontrollrad + hydrograf-SVG för bottendocken. */
export function tidsspelareHtml(svar: TidsserieSvar, tillstand: TidsspelareTillstand): string {
  const maxIndex = Math.max(0, svar.tidssteg.length - 1);
  const sliderVal = tillstand.aktivtSteg === null ? maxIndex : Math.min(maxIndex, Math.max(0, tillstand.aktivtSteg));
  const aktivTidMin = tillstand.aktivtSteg === null ? null : (svar.tidssteg[tillstand.aktivtSteg]?.tid_min ?? null);
  const spelarText = tillstand.spelar ? "⏸ Paus" : "▶ Spela";
  const hastighetText = tillstand.hastighet === 2 ? "2×" : "1×";
  const arMaxdjup = tillstand.aktivtSteg === null;
  const status = tidsspelareStatusText(svar, tillstand);
  const svg = ritaHydrografSvg(svar, aktivTidMin);

  return [
    `<div class="tid-spelare" style="background:var(--f-yta, #fff);color:var(--f-text, #111);border-top:1px solid var(--f-linje, #d8d8d8)">`,
    `<div class="tid-kontroller">`,
    `<button type="button" class="tid-knapp" data-tid-handling="spela" aria-pressed="${tillstand.spelar ? "true" : "false"}">${spelarText}</button>`,
    `<input type="range" class="tid-slider" data-tid-slider min="0" max="${maxIndex}" step="1" value="${sliderVal}" aria-label="Tidpunkt"${svar.tidssteg.length === 0 ? " disabled" : ""} />`,
    `<button type="button" class="tid-knapp" data-tid-handling="hastighet" aria-label="Uppspelningshastighet">${hastighetText}</button>`,
    `<button type="button" class="tid-knapp" data-tid-handling="topp">Topp</button>`,
    `<button type="button" class="tid-knapp" data-tid-handling="maxdjup" aria-pressed="${arMaxdjup ? "true" : "false"}">Maxdjup</button>`,
    `<span class="tid-status">${esc(status)}</span>`,
    `</div>`,
    `<div class="tid-hydrograf" data-tid-hydrograf>${svg}</div>`,
    `</div>`,
  ].join("");
}
