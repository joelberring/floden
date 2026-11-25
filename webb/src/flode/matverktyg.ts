/*
 * Skalstock, mätverktyg för bredd/avstånd i EPSG:3011 (SWEREF 99 18 00, meter) och tydlig
 * pekavläsning för vattendjup i cm + m. Ren deterministisk kod utan DOM, fullt testbar.
 */
import type { Punkt } from "../karta/geom.ts";
import { esc } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";

/** Rena, avrundade skalstocksvärden i meter. */
export const SKALSTEG_M: readonly number[] = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];

export interface SkalstockMatt {
  meter: number;
  breddPx: number;
  etikett: string;
}

/**
 * Väljer största rena skalstocksvärdet som ryms inom `maxBreddPx` vid aktuell skala (`meterPerPx`).
 * Returnerar null om skalan saknas eller inte är ett positivt ändligt tal.
 */
export function skalstockMatt(meterPerPx: number, maxBreddPx = 120): SkalstockMatt | null {
  if (!Number.isFinite(meterPerPx) || meterPerPx <= 0 || !Number.isFinite(maxBreddPx) || maxBreddPx < 20) return null;
  const maxMeter = meterPerPx * maxBreddPx;
  let valt = SKALSTEG_M[0] as number;
  for (const steg of SKALSTEG_M) {
    if (steg <= maxMeter) valt = steg;
    else break;
  }
  const breddPx = Math.max(24, Math.round(valt / meterPerPx));
  const etikett = valt >= 1000 ? `${formateraHogst(valt / 1000, 1)} km` : `${formateraHogst(valt, 0)} m`;
  return { meter: valt, breddPx, etikett };
}

/** HTML för permanent, stram svartvit grafisk skalstock. */
export function skalstockHtml(m: SkalstockMatt | null): string {
  if (!m) return "";
  return `<div class="ar-skalstock-inre" aria-label="Skala ${esc(m.etikett)}">
    <div class="ar-skalstock-stapel" style="width:${m.breddPx}px">
      <span class="ar-skalstock-halva mork"></span><span class="ar-skalstock-halva ljus"></span>
    </div>
    <div class="ar-skalstock-text" style="width:${m.breddPx}px"><span>0</span><span>${esc(m.etikett)}</span></div>
  </div>`;
}

/** Deterministiskt avstånd/bredd i meter mellan två punkter i SWEREF 99 18 00 (EPSG:3011). */
export function avstandMeter(a: Punkt, b: Punkt): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

/** Formaterar mätt bredd/avstånd i meter med svenskt decimalkomma (t.ex. `Bredd: 8,4 m`). */
export function formateraBredd(meter: number): string {
  if (!Number.isFinite(meter) || meter < 0) return "Bredd: –";
  const dec = meter >= 100 ? 0 : 1;
  return `Bredd: ${formatera(meter, dec)} m`;
}

/** Det kartmotorn behöver för att rita mätlinjen ovanpå kartan. */
export interface MatLinjeVy {
  a: Punkt;
  b: Punkt;
  meter: number;
  text: string;
  klar: boolean;
}

/**
 * Tillståndsmaskin för mätverktyget (`Mät bredd`):
 * - Klick 1 sätter punkt A
 * - Musrörelse visar levande sträcka A → markör
 * - Klick 2 låser punkt B och visar exakt bredd i meter
 * - Klick 3 startar en ny mätning från ny punkt A
 * - Esc rensar mätningen (eller stänger verktyget om ingen mätning pågår)
 */
export class MatVerktyg {
  aktiv = false;
  punktA: Punkt | null = null;
  punktB: Punkt | null = null;
  markor: Punkt | null = null;
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
      this.punktA = null;
      this.punktB = null;
      this.markor = null;
    }
    this.signalera();
  }

  vaxla(): void {
    this.sattAktiv(!this.aktiv);
  }

  rensa(): void {
    this.punktA = null;
    this.punktB = null;
    this.markor = null;
    this.signalera();
  }

  klick(p: Punkt): boolean {
    if (!this.aktiv) return false;
    if (!this.punktA || this.punktB) {
      this.punktA = { x: p.x, y: p.y };
      this.punktB = null;
      this.markor = { x: p.x, y: p.y };
    } else {
      this.punktB = { x: p.x, y: p.y };
      this.markor = null;
    }
    this.signalera();
    return true;
  }

  ror(p: Punkt): void {
    if (!this.aktiv || !this.punktA || this.punktB) return;
    this.markor = { x: p.x, y: p.y };
    this.signalera();
  }

  lamnar(): void {
    if (!this.aktiv || !this.punktA || this.punktB) return;
    this.markor = null;
    this.signalera();
  }

  /** Esc rensar pågående/låst mätning; andra Esc (när tomt) stänger mätverktyget. */
  avbryt(): boolean {
    if (!this.aktiv) return false;
    if (this.punktA) {
      this.rensa();
      return true;
    }
    this.sattAktiv(false);
    return true;
  }

  linje(): MatLinjeVy | null {
    if (!this.aktiv || !this.punktA) return null;
    const slut = this.punktB ?? this.markor;
    if (!slut) return null;
    const meter = avstandMeter(this.punktA, slut);
    return {
      a: this.punktA,
      b: slut,
      meter,
      text: formateraBredd(meter),
      klar: this.punktB !== null,
    };
  }

  hjalpText(): string {
    if (!this.aktiv) return "";
    if (!this.punktA) return "Mät bredd: klicka första punkten på kartan (Esc avslutar).";
    if (!this.punktB) {
      const l = this.linje();
      return l ? `${l.text} — klicka andra punkten för att låsa.` : "Klicka andra punkten för att mäta bredden.";
    }
    const l = this.linje();
    return `${l?.text ?? ""} · Klicka igen för ny mätning, Esc rensar.`;
  }
}

/**
 * Formaterar vattendjup i meter så att det alltid läses tydligt i cm + m, t.ex. `24 cm (0,24 m)`.
 * För skillnader sätts tecken på båda: `+12 cm (+0,12 m)` eller `−8 cm (−0,08 m)`.
 */
export function formateraDjupCm(djupM: number, skillnad = false): string {
  if (!Number.isFinite(djupM)) return "–";
  const cm = Math.round(djupM * 100);
  const tecken = skillnad && djupM > 0 ? "+" : "";
  const cmText = `${tecken}${formatera(cm, 0)} cm`;
  const mText = `${tecken}${formatera(djupM, 2)} m`;
  return `${cmText} (${mText})`;
}

/** Formaterar markhöjdsraden kort och enhetligt: `Mark 12,45 m RH 2000`. */
export function kortMarkhojdText(text: string | null): string | null {
  if (!text) return null;
  return text.replace(/^Markhöjd\s+/, "Mark ");
}
