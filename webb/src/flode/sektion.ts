/*
 * SCALGO-liknande sektion / tvärsnitt: ren, DOM-fri frontendkärna + stram svartvit SVG-generator
 * och CSV-export för kontraktet `floden-sektion/v1` (SWEREF 99 18 00 / EPSG:3011, RH 2000).
 *
 * Alla tal i gränssnittet formateras med `../tal.ts` (svenskt decimalkomma, aldrig `toFixed`
 * i visningstext). Fysiken är facit: inga höjder eller djup gissas här.
 */
import type { Punkt } from "../karta/geom.ts";
import { esc } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";
import { avstandMeter, formateraDjupCm } from "./matverktyg.ts";

export interface SektionSammanfattning {
  langd_m: number;
  antal_punkter: number;
  steg_m: number;
  mark_fore_min_m: number | null;
  mark_fore_max_m: number | null;
  mark_efter_min_m: number | null;
  mark_efter_max_m: number | null;
  max_djup_fore_m: number | null;
  max_djup_efter_m: number | null;
  oversvammad_bredd_002m_fore_m: number | null;
  oversvammad_bredd_002m_efter_m: number | null;
  oversvammad_bredd_010m_fore_m: number | null;
  oversvammad_bredd_010m_efter_m: number | null;
}

export interface SektionSvar {
  kontrakt: "floden-sektion/v1";
  epsg: number;
  hojdsystem: string;
  har_efter: boolean;
  avstand_m: number[];
  e: number[];
  n: number[];
  mark_fore_m: Array<number | null>;
  mark_efter_m: Array<number | null>;
  vatten_djup_fore_m: Array<number | null>;
  vatten_djup_efter_m: Array<number | null>;
  vatten_yta_fore_m: Array<number | null>;
  vatten_yta_efter_m: Array<number | null>;
  byggnad_fore: boolean[];
  byggnad_efter: boolean[];
  takhojd_fore_m: Array<number | null>;
  takhojd_efter_m: Array<number | null>;
  sammanfattning: SektionSammanfattning;
}

export interface SektionPunktAvlasning {
  index: number;
  avstandM: number;
  e: number;
  n: number;
  markForeM: number | null;
  markEfterM: number | null;
  vattenDjupForeM: number | null;
  vattenDjupEfterM: number | null;
  vattenYtaForeM: number | null;
  vattenYtaEfterM: number | null;
  byggnadFore: boolean;
  byggnadEfter: boolean;
  takhojdForeM: number | null;
  takhojdEfterM: number | null;
  avstandText: string;
  markForeText: string;
  markEfterText: string;
  djupForeText: string;
  djupEfterText: string;
  vattenYtaForeText: string;
  vattenYtaEfterText: string;
  takhojdForeText: string;
  takhojdEfterText: string;
  sammanfattningText: string;
}

export interface SektionLinjeVy {
  punkter: Punkt[];
  langdM: number;
  text: string;
  klar: boolean;
}

export interface SektionSvgVal {
  bredd?: number;
  hojd?: number;
  aktivIndex?: number | null;
  visaFore?: boolean;
  visaEfter?: boolean;
}

const MAX_HORNPUNKTER = 100;
const SVG_PAD_V = 54;
const SVG_PAD_H = 18;
const SVG_PAD_T = 22;
const SVG_PAD_B = 28;
const STANDARD_SVG_BREDD = 680;
const STANDARD_SVG_HOJD = 210;

/** Total längd längs en polylinje i SWEREF 99 18 00 (meter). */
export function polylinjeLangdM(punkter: readonly Punkt[]): number {
  let summa = 0;
  for (let i = 1; i < punkter.length; i++) {
    summa += avstandMeter(punkter[i - 1] as Punkt, punkter[i] as Punkt);
  }
  return summa;
}

/** Formaterar sektionslängd med svenskt decimalkomma, t.ex. `Sektion: 84,3 m`. */
export function formateraSektionLangd(meter: number): string {
  if (!Number.isFinite(meter) || meter < 0) return "Sektion: –";
  const dec = meter >= 100 ? 0 : 1;
  return `Sektion: ${formatera(meter, dec)} m`;
}

/**
 * Tillståndsmaskin för sektionsverktyget:
 * - Klick 1 sätter startpunkt i EPSG:3011
 * - Shift+klick lägger till mellanpunkt (polylinje)
 * - Klick 2 (utan Shift) slutför och låser linjen direkt
 * - Musrörelse visar levande sträcka till markören
 * - `rensa()` rensar linjen; `avsluta()` rensar och stänger verktyget; `avbryt()` (Esc) rensar först, sedan stänger
 */
export class SektionVerktyg {
  aktiv = false;
  punkter: Punkt[] = [];
  markor: Punkt | null = null;
  klar = false;
  private lyssnare: Array<() => void> = [];

  vidAndring(f: () => void): void {
    this.lyssnare.push(f);
  }

  private signalera(): void {
    for (const f of this.lyssnare) f();
  }

  sattAktiv(pa: boolean): void {
    if (this.aktiv === pa) return;
    this.aktiv = pa;
    if (!pa) {
      this.punkter = [];
      this.markor = null;
      this.klar = false;
    }
    this.signalera();
  }

  vaxla(): void {
    this.sattAktiv(!this.aktiv);
  }

  rensa(): void {
    this.punkter = [];
    this.markor = null;
    this.klar = false;
    this.signalera();
  }

  avsluta(): void {
    this.aktiv = false;
    this.punkter = [];
    this.markor = null;
    this.klar = false;
    this.signalera();
  }

  klick(p: Punkt, val?: { shift?: boolean }): boolean {
    if (!this.aktiv) return false;
    const ny: Punkt = { x: p.x, y: p.y };
    if (this.klar || this.punkter.length === 0) {
      this.punkter = [ny];
      this.klar = false;
      this.markor = ny;
      this.signalera();
      return true;
    }

    const foregaende = this.punkter[this.punkter.length - 1] as Punkt;
    const dist = avstandMeter(foregaende, ny);

    if (val?.shift) {
      if (dist > 1e-6 && this.punkter.length < MAX_HORNPUNKTER) {
        this.punkter = [...this.punkter, ny];
      }
      this.markor = ny;
      this.signalera();
      return true;
    }

    if (dist > 1e-6 && this.punkter.length < MAX_HORNPUNKTER) {
      this.punkter = [...this.punkter, ny];
    }
    if (this.punkter.length >= 2 && polylinjeLangdM(this.punkter) > 0) {
      this.klar = true;
      this.markor = null;
    }
    this.signalera();
    return true;
  }

  /** Låser en pågående polylinje om minst två punkter med längd > 0 finns. */
  slutfor(): boolean {
    if (!this.aktiv || this.klar || this.punkter.length < 2 || polylinjeLangdM(this.punkter) <= 0) {
      return false;
    }
    this.klar = true;
    this.markor = null;
    this.signalera();
    return true;
  }

  ror(p: Punkt): void {
    if (!this.aktiv || this.punkter.length === 0 || this.klar) return;
    this.markor = { x: p.x, y: p.y };
    this.signalera();
  }

  lamnar(): void {
    if (!this.aktiv || this.punkter.length === 0 || this.klar) return;
    this.markor = null;
    this.signalera();
  }

  /** Esc rensar pågående/låst linje; andra Esc stänger verktyget. */
  avbryt(): boolean {
    if (!this.aktiv) return false;
    if (this.punkter.length > 0) {
      this.rensa();
      return true;
    }
    this.avsluta();
    return true;
  }

  linje(): SektionLinjeVy | null {
    if (!this.aktiv || this.punkter.length === 0) return null;
    const alla: Punkt[] =
      !this.klar && this.markor ? [...this.punkter, this.markor] : [...this.punkter];
    if (alla.length < 2) return null;
    const langdM = polylinjeLangdM(alla);
    return {
      punkter: alla,
      langdM,
      text: formateraSektionLangd(langdM),
      klar: this.klar,
    };
  }

  /** Returnerar låsta punkter som `[[e, n], ...]` i EPSG:3011 för API-anrop. */
  punkterSomPar(): Array<[number, number]> {
    return this.punkter.map((p) => [p.x, p.y]);
  }

  hjalpText(): string {
    if (!this.aktiv) return "";
    if (this.punkter.length === 0) {
      return "Sektion: klicka startpunkt på kartan (Shift+klick för mellanpunkter, Esc avslutar).";
    }
    if (!this.klar) {
      const l = this.linje();
      return l
        ? `${l.text} — klicka slutpunkt för att visa profil (Shift+klick lägger till brytpunkt).`
        : "Klicka slutpunkt för att visa profil.";
    }
    const l = this.linje();
    return `${l?.text ?? ""} · Klicka igen för ny sektion, Esc rensar.`;
  }
}

function formateraHojd(v: number | null, hojdsystem: string): string {
  if (v === null || !Number.isFinite(v)) return "–";
  return `${formatera(v, 2)} m ${hojdsystem}`;
}

function formateraDjup(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "–";
  return formateraDjupCm(v);
}

/** Formaterad avläsning vid samplingsindex `index` för hårkors och synkad kartmarkör. */
export function avlasningVidIndex(data: SektionSvar, index: number): SektionPunktAvlasning | null {
  const n = data.avstand_m.length;
  if (!Number.isInteger(index) || index < 0 || index >= n) return null;
  const avstandM = data.avstand_m[index] as number;
  const e = data.e[index] as number;
  const nord = data.n[index] as number;
  const markForeM = data.mark_fore_m[index] ?? null;
  const markEfterM = data.mark_efter_m[index] ?? null;
  const vattenDjupForeM = data.vatten_djup_fore_m[index] ?? null;
  const vattenDjupEfterM = data.vatten_djup_efter_m[index] ?? null;
  const vattenYtaForeM = data.vatten_yta_fore_m[index] ?? null;
  const vattenYtaEfterM = data.vatten_yta_efter_m[index] ?? null;
  const byggnadFore = Boolean(data.byggnad_fore[index]);
  const byggnadEfter = Boolean(data.byggnad_efter[index]);
  const takhojdForeM = data.takhojd_fore_m[index] ?? null;
  const takhojdEfterM = data.takhojd_efter_m[index] ?? null;

  const hs = data.hojdsystem || "RH 2000";
  const avstandText = `${formatera(avstandM, avstandM >= 100 ? 0 : 1)} m`;
  const markForeText = formateraHojd(markForeM, hs);
  const markEfterText = formateraHojd(markEfterM, hs);
  const djupForeText = formateraDjup(vattenDjupForeM);
  const djupEfterText = formateraDjup(vattenDjupEfterM);
  const vattenYtaForeText = formateraHojd(vattenYtaForeM, hs);
  const vattenYtaEfterText = formateraHojd(vattenYtaEfterM, hs);
  const takhojdForeText =
    takhojdForeM !== null && Number.isFinite(takhojdForeM)
      ? `Tak ${formatera(takhojdForeM, 1)} m ${hs}`
      : "–";
  const takhojdEfterText =
    takhojdEfterM !== null && Number.isFinite(takhojdEfterM)
      ? `Tak ${formatera(takhojdEfterM, 1)} m ${hs}`
      : "–";

  const delar = [`Avstånd ${avstandText}`, `Mark ${markForeText}`];
  if (vattenDjupForeM !== null && vattenDjupForeM > 0) {
    delar.push(`Djup före ${djupForeText}`);
  }
  if (data.har_efter && vattenDjupEfterM !== null && vattenDjupEfterM > 0) {
    delar.push(`Djup efter ${djupEfterText}`);
  }

  return {
    index,
    avstandM,
    e,
    n: nord,
    markForeM,
    markEfterM,
    vattenDjupForeM,
    vattenDjupEfterM,
    vattenYtaForeM,
    vattenYtaEfterM,
    byggnadFore,
    byggnadEfter,
    takhojdForeM,
    takhojdEfterM,
    avstandText,
    markForeText,
    markEfterText,
    djupForeText,
    djupEfterText,
    vattenYtaForeText,
    vattenYtaEfterText,
    takhojdForeText,
    takhojdEfterText,
    sammanfattningText: delar.join(" · "),
  };
}

/** Mappar en X-koordinat i SVG-koordinatsystemet (`0..bredd`) till närmaste samplingsindex. */
export function indexFranSvgX(
  data: SektionSvar,
  x: number,
  bredd = STANDARD_SVG_BREDD,
): number | null {
  const avst = data.avstand_m;
  const n = avst.length;
  if (n === 0 || !Number.isFinite(x) || !Number.isFinite(bredd)) return null;
  if (n === 1) return 0;
  const plotBredd = Math.max(1, bredd - SVG_PAD_V - SVG_PAD_H);
  const t = Math.max(0, Math.min(1, (x - SVG_PAD_V) / plotBredd));
  const total = avst[n - 1] as number;
  const mal = t * total;

  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((avst[mid] as number) < mal) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs((avst[lo - 1] as number) - mal) <= Math.abs((avst[lo] as number) - mal)) {
    return lo - 1;
  }
  return lo;
}

function rundaKoord(v: number): string {
  return String(Math.round(v * 100) / 100);
}

function finaTicks(minV: number, maxV: number, antal = 4): number[] {
  const spann = maxV - minV;
  if (!Number.isFinite(spann) || spann <= 0) return [minV];
  const raa = spann / Math.max(1, antal);
  const mag = 10 ** Math.floor(Math.log10(raa));
  const norm = raa / mag;
  const steg = norm <= 1 ? mag : norm <= 2 ? 2 * mag : norm <= 5 ? 5 * mag : 10 * mag;
  const start = Math.ceil(minV / steg) * steg;
  const ut: number[] = [];
  for (let v = start; v <= maxV + steg * 1e-6; v += steg) {
    ut.push(Math.round(v * 1e6) / 1e6);
    if (ut.length > 20) break;
  }
  return ut.length > 0 ? ut : [minV, maxV];
}

interface ByggnadBlock {
  startIdx: number;
  slutIdx: number;
  takhojdM: number | null;
  markBasM: number;
}

function byggnadBlock(
  byggnad: readonly boolean[],
  tak: readonly (number | null)[],
  mark: readonly (number | null)[],
  fallbackMark: number,
): ByggnadBlock[] {
  const block: ByggnadBlock[] = [];
  let i = 0;
  while (i < byggnad.length) {
    if (!byggnad[i]) {
      i++;
      continue;
    }
    const start = i;
    let maxTak: number | null = null;
    let minMark = Infinity;
    while (i < byggnad.length && byggnad[i]) {
      const t = tak[i] ?? null;
      if (t !== null && Number.isFinite(t)) {
        maxTak = maxTak === null ? t : Math.max(maxTak, t);
      }
      const m = mark[i] ?? null;
      if (m !== null && Number.isFinite(m)) {
        minMark = Math.min(minMark, m);
      }
      i++;
    }
    block.push({
      startIdx: start,
      slutIdx: i - 1,
      takhojdM: maxTak,
      markBasM: Number.isFinite(minMark) ? minMark : fallbackMark,
    });
  }
  return block;
}

function vattenSegment(
  avst: readonly number[],
  mark: readonly (number | null)[],
  yta: readonly (number | null)[],
  toX: (d: number) => number,
  toY: (z: number) => number,
): { fyllPaths: string[]; ytaPaths: string[] } {
  const fyllPaths: string[] = [];
  const ytaPaths: string[] = [];
  let i = 0;
  while (i < avst.length) {
    const m = mark[i] ?? null;
    const y = yta[i] ?? null;
    if (m === null || y === null || y <= m) {
      i++;
      continue;
    }
    const start = i;
    while (i < avst.length) {
      const mi = mark[i] ?? null;
      const yi = yta[i] ?? null;
      if (mi === null || yi === null || yi <= mi) break;
      i++;
    }
    const slut = i - 1;
    if (slut === start) {
      // Ensam våt punkt: rita en smal vertikal pelare så även 1-cells-sänkor syns.
      const cx = toX(avst[start] as number);
      const yTop = toY(yta[start] as number);
      const yBot = toY(mark[start] as number);
      const dx = 1.5;
      fyllPaths.push(
        `M${rundaKoord(cx - dx)},${rundaKoord(yTop)} L${rundaKoord(cx + dx)},${rundaKoord(yTop)} L${rundaKoord(cx + dx)},${rundaKoord(yBot)} L${rundaKoord(cx - dx)},${rundaKoord(yBot)} Z`,
      );
      ytaPaths.push(`M${rundaKoord(cx - dx)},${rundaKoord(yTop)} L${rundaKoord(cx + dx)},${rundaKoord(yTop)}`);
      continue;
    }
    const ovre: string[] = [];
    const nedre: string[] = [];
    for (let k = start; k <= slut; k++) {
      ovre.push(`${rundaKoord(toX(avst[k] as number))},${rundaKoord(toY(yta[k] as number))}`);
    }
    for (let k = slut; k >= start; k--) {
      nedre.push(`${rundaKoord(toX(avst[k] as number))},${rundaKoord(toY(mark[k] as number))}`);
    }
    fyllPaths.push(`M${ovre.join(" L")} L${nedre.join(" L")} Z`);
    ytaPaths.push(`M${ovre.join(" L")}`);
  }
  return { fyllPaths, ytaPaths };
}

function kurvaPaths(
  avst: readonly number[],
  z: readonly (number | null)[],
  toX: (d: number) => number,
  toY: (val: number) => number,
): string[] {
  const paths: string[] = [];
  let seg: string[] = [];
  for (let i = 0; i < avst.length; i++) {
    const v = z[i] ?? null;
    if (v === null || !Number.isFinite(v)) {
      if (seg.length >= 2) paths.push(`M${seg.join(" L")}`);
      seg = [];
      continue;
    }
    seg.push(`${rundaKoord(toX(avst[i] as number))},${rundaKoord(toY(v))}`);
  }
  if (seg.length >= 2) paths.push(`M${seg.join(" L")}`);
  return paths;
}

/**
 * Ritar en stram svartvit arkitekt-SVG av sektionen (`floden-sektion/v1`).
 *
 * Y-axeln skalas enbart efter `mark + vattenyta` (+15 % marginal uppåt); byggnader som sticker
 * upp ovanför diagramtaket klipps med snedmarkering + `Tak XX,X m` så att 10–30 cm vattendjup
 * alltid syns tydligt.
 */
export function ritaSektionSvg(data: SektionSvar, val?: SektionSvgVal): string {
  const bredd = Math.max(240, Math.round(val?.bredd ?? STANDARD_SVG_BREDD));
  const hojd = Math.max(120, Math.round(val?.hojd ?? STANDARD_SVG_HOJD));
  const visaFore = val?.visaFore ?? true;
  const visaEfter = (val?.visaEfter ?? true) && data.har_efter;
  const aktivIndex = val?.aktivIndex ?? null;

  const plotX0 = SVG_PAD_V;
  const plotX1 = bredd - SVG_PAD_H;
  const plotY0 = SVG_PAD_T;
  const plotY1 = hojd - SVG_PAD_B;
  const plotB = Math.max(1, plotX1 - plotX0);
  const plotH = Math.max(1, plotY1 - plotY0);

  const avst = data.avstand_m;
  const n = avst.length;
  const totalLangd = n > 0 ? Math.max(1e-6, avst[n - 1] as number) : 1;

  // Samla Y-intervall ENBART från mark + vattenyta (aldrig takhöjd!).
  let yMin = Infinity;
  let yMax = -Infinity;
  const samla = (arr: readonly (number | null)[]): void => {
    for (const v of arr) {
      if (v !== null && Number.isFinite(v)) {
        if (v < yMin) yMin = v;
        if (v > yMax) yMax = v;
      }
    }
  };
  if (visaFore) {
    samla(data.mark_fore_m);
    samla(data.vatten_yta_fore_m);
  }
  if (visaEfter) {
    samla(data.mark_efter_m);
    samla(data.vatten_yta_efter_m);
  }
  if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) {
    yMin = 0;
    yMax = 1;
  }

  const raaSpann = Math.max(yMax - yMin, 0.4);
  const domMin = yMin - raaSpann * 0.1;
  const domMax = yMax + raaSpann * 0.15;
  const domSpann = Math.max(domMax - domMin, 1e-6);

  const toX = (d: number): number => plotX0 + (d / totalLangd) * plotB;
  const toY = (z: number): number => plotY1 - ((z - domMin) / domSpann) * plotH;

  const delar: string[] = [];
  const hs = data.hojdsystem || "RH 2000";
  const langdEtikett = `${formatera(data.sammanfattning.langd_m, data.sammanfattning.langd_m >= 100 ? 0 : 1)} m`;

  delar.push(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${bredd} ${hojd}" width="${bredd}" height="${hojd}" role="img" aria-label="${esc(`Sektionsprofil ${langdEtikett}, höjder i ${hs}`)}">`,
  );
  delar.push(`<rect x="0" y="0" width="${bredd}" height="${hojd}" fill="#ffffff"/>`);

  // Rutnät och Y-axelticks
  const yTicks = finaTicks(domMin, domMax, 4);
  const yDec = domSpann < 2 ? 2 : 1;
  for (const t of yTicks) {
    if (t < domMin - 1e-9 || t > domMax + 1e-9) continue;
    const py = toY(t);
    delar.push(
      `<line x1="${rundaKoord(plotX0)}" y1="${rundaKoord(py)}" x2="${rundaKoord(plotX1)}" y2="${rundaKoord(py)}" stroke="#d9d9d9" stroke-width="1"/>`,
    );
    delar.push(
      `<text x="${rundaKoord(plotX0 - 6)}" y="${rundaKoord(py + 3.5)}" text-anchor="end" font-family="ui-sans-serif, system-ui, sans-serif" font-size="10" fill="#111111">${esc(formatera(t, yDec))}</text>`,
    );
  }
  delar.push(
    `<text x="${rundaKoord(plotX0)}" y="13" text-anchor="start" font-family="ui-sans-serif, system-ui, sans-serif" font-size="10" fill="#111111">${esc(`m ${hs}`)}</text>`,
  );

  // X-axelticks
  const xTicks = finaTicks(0, totalLangd, 5);
  const xDec = totalLangd < 20 ? 1 : 0;
  for (const t of xTicks) {
    if (t < 0 || t > totalLangd + 1e-9) continue;
    const px = toX(t);
    delar.push(
      `<line x1="${rundaKoord(px)}" y1="${rundaKoord(plotY1)}" x2="${rundaKoord(px)}" y2="${rundaKoord(plotY1 + 4)}" stroke="#111111" stroke-width="1"/>`,
    );
    delar.push(
      `<text x="${rundaKoord(px)}" y="${rundaKoord(plotY1 + 17)}" text-anchor="middle" font-family="ui-sans-serif, system-ui, sans-serif" font-size="10" fill="#111111">${esc(`${formateraHogst(t, xDec)} m`)}</text>`,
    );
  }

  // Byggnadsblock (ritade i neutral gråton; klipps snyggt vid diagramtaket om takhojd > domMax)
  const ritByggnader = (
    bygg: readonly boolean[],
    tak: readonly (number | null)[],
    mark: readonly (number | null)[],
    fyll: string,
    kant: string,
    streckad: boolean,
  ): void => {
    const block = byggnadBlock(bygg, tak, mark, yMin);
    for (const b of block) {
      const d0 = avst[b.startIdx] as number;
      const d1 = avst[b.slutIdx] as number;
      let x0 = toX(d0);
      let x1 = toX(d1);
      if (x1 - x0 < 3) {
        const mid = (x0 + x1) / 2;
        x0 = Math.max(plotX0, mid - 2);
        x1 = Math.min(plotX1, mid + 2);
      }
      const yBot = Math.min(plotY1, toY(b.markBasM));
      const takM = b.takhojdM;
      const klippt = takM !== null && takM > domMax;
      const yTop = takM === null ? plotY0 : klippt ? plotY0 : Math.max(plotY0, toY(takM));
      const h = Math.max(1, yBot - yTop);
      const dashAttr = streckad ? ` stroke-dasharray="3 2"` : "";
      delar.push(
        `<rect data-sektion="byggnad"${klippt ? ' data-klippt="true"' : ""} x="${rundaKoord(x0)}" y="${rundaKoord(yTop)}" width="${rundaKoord(x1 - x0)}" height="${rundaKoord(h)}" fill="${fyll}" fill-opacity="0.72" stroke="${kant}" stroke-width="1"${dashAttr}/>`,
      );
      if (klippt && takM !== null) {
        // Sned klippningsmarkering vid diagramtaket + etikett `Tak XX,X m`
        const mX = (x0 + x1) / 2;
        const k0 = Math.max(plotX0, x0 + 2);
        const k1 = Math.min(plotX1, x1 - 2);
        const sned: string[] = [];
        for (let sx = k0; sx <= k1 - 4; sx += 6) {
          sned.push(
            `M${rundaKoord(sx)},${rundaKoord(plotY0 + 5)} L${rundaKoord(sx + 4)},${rundaKoord(plotY0 - 2)}`,
          );
        }
        if (sned.length === 0) {
          sned.push(
            `M${rundaKoord(mX - 3)},${rundaKoord(plotY0 + 5)} L${rundaKoord(mX + 3)},${rundaKoord(plotY0 - 2)}`,
          );
        }
        delar.push(
          `<path data-sektion="byggnad-klipp" d="${sned.join(" ")}" stroke="#111111" stroke-width="1.1" fill="none"/>`,
        );
        const takEtikett = `Tak ${formatera(takM, 1)} m`;
        const textX = Math.max(plotX0 + 34, Math.min(plotX1 - 34, mX));
        delar.push(
          `<text data-sektion="byggnad-tak-etikett" x="${rundaKoord(textX)}" y="${rundaKoord(plotY0 + 14)}" text-anchor="middle" font-family="ui-sans-serif, system-ui, sans-serif" font-size="10" font-weight="600" fill="#111111">${esc(takEtikett)}</text>`,
        );
      }
    }
  };

  if (visaFore) {
    ritByggnader(
      data.byggnad_fore,
      data.takhojd_fore_m,
      data.mark_fore_m,
      "#d2d2d2",
      "#2b2b2b",
      visaEfter,
    );
  }
  if (visaEfter) {
    const markRefEfter = data.mark_efter_m.map((v, idx) => v ?? data.mark_fore_m[idx] ?? null);
    ritByggnader(
      data.byggnad_efter,
      data.takhojd_efter_m,
      markRefEfter,
      "#d2d2d2",
      "#2b2b2b",
      false,
    );
  }

  // Vattenytor (före och efter)
  if (visaFore) {
    const vf = vattenSegment(avst, data.mark_fore_m, data.vatten_yta_fore_m, toX, toY);
    for (const d of vf.fyllPaths) {
      delar.push(`<path data-sektion="vatten-fore" d="${d}" fill="#7ec4f8" fill-opacity="0.45"/>`);
    }
    for (const d of vf.ytaPaths) {
      delar.push(`<path data-sektion="vattenyta-fore" d="${d}" fill="none" stroke="#2f8be6" stroke-width="1.5"/>`);
    }
  }
  if (visaEfter) {
    const markRefEfter = data.mark_efter_m.map((v, idx) => v ?? data.mark_fore_m[idx] ?? null);
    const ve = vattenSegment(avst, markRefEfter, data.vatten_yta_efter_m, toX, toY);
    for (const d of ve.fyllPaths) {
      delar.push(`<path data-sektion="vatten-efter" d="${d}" fill="#1a52b8" fill-opacity="0.55"/>`);
    }
    for (const d of ve.ytaPaths) {
      delar.push(`<path data-sektion="vattenyta-efter" d="${d}" fill="none" stroke="#182b7a" stroke-width="1.75"/>`);
    }
  }

  // Marklinjer
  if (visaFore) {
    const mf = kurvaPaths(avst, data.mark_fore_m, toX, toY);
    const farg = visaEfter ? "#666666" : "#111111";
    const dash = visaEfter ? ' stroke-dasharray="5 3"' : "";
    for (const d of mf) {
      delar.push(
        `<path data-sektion="mark-fore" d="${d}" fill="none" stroke="${farg}" stroke-width="1.6"${dash}/>`,
      );
    }
  }
  if (visaEfter) {
    const me = kurvaPaths(avst, data.mark_efter_m, toX, toY);
    for (const d of me) {
      delar.push(
        `<path data-sektion="mark-efter" d="${d}" fill="none" stroke="#111111" stroke-width="1.8"/>`,
      );
    }
  }

  // Ram kring diagramytan
  delar.push(
    `<rect x="${rundaKoord(plotX0)}" y="${rundaKoord(plotY0)}" width="${rundaKoord(plotB)}" height="${rundaKoord(plotH)}" fill="none" stroke="#111111" stroke-width="1"/>`,
  );

  // Vertikal hårkorslinje + markörer vid aktivIndex
  if (aktivIndex !== null && Number.isInteger(aktivIndex) && aktivIndex >= 0 && aktivIndex < n) {
    const hx = toX(avst[aktivIndex] as number);
    delar.push(
      `<line data-sektion="harkors" x1="${rundaKoord(hx)}" y1="${rundaKoord(plotY0)}" x2="${rundaKoord(hx)}" y2="${rundaKoord(plotY1)}" stroke="#111111" stroke-width="1" stroke-dasharray="3 2"/>`,
    );
    const mfVal = visaFore ? (data.mark_fore_m[aktivIndex] ?? null) : null;
    if (mfVal !== null) {
      delar.push(
        `<circle cx="${rundaKoord(hx)}" cy="${rundaKoord(toY(mfVal))}" r="3" fill="#ffffff" stroke="#111111" stroke-width="1.4"/>`,
      );
    }
    const meVal = visaEfter ? (data.mark_efter_m[aktivIndex] ?? null) : null;
    if (meVal !== null) {
      delar.push(
        `<circle cx="${rundaKoord(hx)}" cy="${rundaKoord(toY(meVal))}" r="3" fill="#111111" stroke="#ffffff" stroke-width="1"/>`,
      );
    }
    const vyf = visaFore ? (data.vatten_yta_fore_m[aktivIndex] ?? null) : null;
    if (vyf !== null) {
      delar.push(
        `<circle cx="${rundaKoord(hx)}" cy="${rundaKoord(toY(vyf))}" r="3" fill="#2f8be6" stroke="#ffffff" stroke-width="1"/>`,
      );
    }
    const vye = visaEfter ? (data.vatten_yta_efter_m[aktivIndex] ?? null) : null;
    if (vye !== null) {
      delar.push(
        `<circle cx="${rundaKoord(hx)}" cy="${rundaKoord(toY(vye))}" r="3" fill="#182b7a" stroke="#ffffff" stroke-width="1"/>`,
      );
    }
  }

  delar.push("</svg>");
  return delar.join("");
}

function csvCell(v: number | boolean | null | undefined): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "true" : "false";
  return Number.isFinite(v) ? String(v) : "";
}

/** Exporterar sektionen som CSV med enheter i alla kolumnnamn. */
export function sektionTillCsv(data: SektionSvar, avgransare = ";"): string {
  const kolumner = [
    "avstand_m",
    "e_3011",
    "n_3011",
    "mark_fore_m",
    "mark_efter_m",
    "vatten_djup_fore_m",
    "vatten_djup_efter_m",
    "vatten_yta_fore_m",
    "vatten_yta_efter_m",
    "byggnad_fore",
    "byggnad_efter",
    "takhojd_fore_m",
    "takhojd_efter_m",
  ];
  const rader: string[] = [kolumner.join(avgransare)];
  const n = data.avstand_m.length;
  for (let i = 0; i < n; i++) {
    rader.push(
      [
        csvCell(data.avstand_m[i]),
        csvCell(data.e[i]),
        csvCell(data.n[i]),
        csvCell(data.mark_fore_m[i]),
        csvCell(data.mark_efter_m[i]),
        csvCell(data.vatten_djup_fore_m[i]),
        csvCell(data.vatten_djup_efter_m[i]),
        csvCell(data.vatten_yta_fore_m[i]),
        csvCell(data.vatten_yta_efter_m[i]),
        csvCell(data.byggnad_fore[i]),
        csvCell(data.byggnad_efter[i]),
        csvCell(data.takhojd_fore_m[i]),
        csvCell(data.takhojd_efter_m[i]),
      ].join(avgransare),
    );
  }
  return rader.join("\n") + "\n";
}

/**
 * Väljer vilka resultat-lager som får skickas som vattenlager till `POST .../sektion` (B11).
 * Bara vattendjupslager (`roll === "max_djup"` och inte skillnadslager) får ritas som vattenyta;
 * alla andra roller (soltimmar, ankomsttid, luft, ekologi m.fl.) ger `null` så ren mark- och
 * byggnadsprofil ritas.
 */
export function sektionVattenLager(
  lager: ReadonlyArray<{ id: string; roll: string; typ: string }> | null | undefined,
  vy: { fore_lager?: string | null; efter_lager?: string | null } | null | undefined,
): { fore_lager: string | null; efter_lager: string | null } {
  const arVattenDjup = (id: string | null | undefined): string | null => {
    if (!id || !lager) return null;
    const post = lager.find((la) => la.id === id);
    return post && post.roll === "max_djup" && post.typ !== "skillnad" ? id : null;
  };
  return {
    fore_lager: arVattenDjup(vy?.fore_lager),
    efter_lager: arVattenDjup(vy?.efter_lager),
  };
}
