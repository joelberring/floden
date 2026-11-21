/*
 * Rastervy i SWEREF 99 18 00 (EPSG:3011).
 *
 * Rastren som servern ritat (PNG i rutnätets egna koordinater) läggs direkt på en duk där
 * x är öst (E) och y är nord (N) i meter. Ingen kartbakgrund från någon extern tjänst behövs:
 * de syntetiska rutorna har inga verkliga koordinater att lägga en bakgrundskarta mot.
 * En valfri bakgrundskarta (OpenStreetMap-plattor, av som standard) ritas som lösa plattor
 * som placeras med SWEREF-omräkningen i `sweref.ts`.
 */
import { swerefTillWgs84, wgs84TillSweref } from "./sweref";
import type { Utbredning } from "./fragor-typer";

export interface VyLager {
  id: string;
  bild: HTMLImageElement;
  utbredning: Utbredning;
  opacitet: number;
  synlig: boolean;
}

export interface Bakgrund {
  paa: boolean;
  /** Plattmall, t.ex. https://tile.openstreetmap.org/{z}/{x}/{y}.png */
  urlMall: string;
}

const PLATT_PX = 256;
const MAX_PLATTOR = 80;

function trevligtSteg(minst: number): number {
  const pot = 10 ** Math.floor(Math.log10(minst));
  for (const f of [1, 2, 5, 10]) {
    if (pot * f >= minst) return pot * f;
  }
  return pot * 10;
}

function formatTal(v: number): string {
  return Math.round(v).toLocaleString("sv-SE");
}

export class RasterVy {
  private readonly duk: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private lager: VyLager[] = [];
  private rutor: Utbredning[] = [];
  private bakgrund: Bakgrund = { paa: false, urlMall: "" };
  private cx = 0;
  private cy = 0;
  private mpp = 1; // meter per CSS-pixel
  private bredd = 0;
  private hojd = 0;
  private dpr = 1;
  private harVy = false;
  private ritaBegard = 0;
  private readonly plattor = new Map<string, HTMLImageElement>();
  private readonly storlekslyssnare: ResizeObserver;
  private dra: { x: number; y: number } | null = null;

  /** Anropas med markörens SWEREF-koordinat (E, N), eller null när markören lämnar vyn. */
  onMarkor: ((e: number | null, n: number | null) => void) | null = null;

  constructor(private readonly behallare: HTMLElement) {
    this.duk = document.createElement("canvas");
    this.duk.className = "rastervy-duk";
    this.duk.tabIndex = 0;
    this.duk.setAttribute("role", "img");
    this.duk.setAttribute("aria-label", "Karta i SWEREF 99 18 00. Dra för att panorera, rulla för att zooma.");
    behallare.appendChild(this.duk);
    const ctx = this.duk.getContext("2d");
    if (!ctx) throw new Error("Webbläsaren saknar stöd för canvas.");
    this.ctx = ctx;

    this.storlekslyssnare = new ResizeObserver(() => this.matStorlek());
    this.storlekslyssnare.observe(behallare);
    this.duk.addEventListener("wheel", (h) => this.vid_hjul(h), { passive: false });
    this.duk.addEventListener("pointerdown", (h) => this.vid_ned(h));
    this.duk.addEventListener("pointermove", (h) => this.vid_rorelse(h));
    this.duk.addEventListener("pointerup", (h) => this.vid_upp(h));
    this.duk.addEventListener("pointerleave", () => this.onMarkor?.(null, null));
    this.duk.addEventListener("dblclick", (h) => {
      const [e, n] = this.tillVarld(h.offsetX, h.offsetY);
      this.zoomaVid(e, n, 0.5);
    });
    this.matStorlek();
  }

  avsluta(): void {
    this.storlekslyssnare.disconnect();
    this.duk.remove();
  }

  settLager(lager: VyLager[]): void {
    this.lager = lager;
    this.rita();
  }

  settRutor(rutor: Utbredning[]): void {
    this.rutor = rutor;
    this.rita();
  }

  settBakgrund(bakgrund: Bakgrund): void {
    this.bakgrund = bakgrund;
    this.rita();
  }

  /** Anpassar vyn så att utbredningen syns med en liten marginal. */
  anpassa(u: Utbredning): void {
    this.cx = (u.x_min + u.x_max) / 2;
    this.cy = (u.y_min + u.y_max) / 2;
    const b = Math.max(1, this.bredd - 48);
    const h = Math.max(1, this.hojd - 48);
    this.mpp = Math.max((u.x_max - u.x_min) / b, (u.y_max - u.y_min) / h, 1e-3);
    this.harVy = true;
    this.rita();
  }

  zooma(faktor: number): void {
    this.mpp *= faktor;
    this.rita();
  }

  private matStorlek(): void {
    const r = this.behallare.getBoundingClientRect();
    this.bredd = Math.max(1, Math.round(r.width));
    this.hojd = Math.max(1, Math.round(r.height));
    this.dpr = window.devicePixelRatio || 1;
    this.duk.width = Math.round(this.bredd * this.dpr);
    this.duk.height = Math.round(this.hojd * this.dpr);
    this.duk.style.width = `${this.bredd}px`;
    this.duk.style.height = `${this.hojd}px`;
    this.rita();
  }

  /* ---------- Koordinater ---------- */

  private tillSkarm(e: number, n: number): [number, number] {
    return [(e - this.cx) / this.mpp + this.bredd / 2, this.hojd / 2 - (n - this.cy) / this.mpp];
  }

  private tillVarld(x: number, y: number): [number, number] {
    return [this.cx + (x - this.bredd / 2) * this.mpp, this.cy - (y - this.hojd / 2) * this.mpp];
  }

  /* ---------- Interaktion ---------- */

  private zoomaVid(e: number, n: number, faktor: number): void {
    // Håller världspunkten (e, n) kvar under markören.
    this.cx = e - (e - this.cx) * faktor;
    this.cy = n - (n - this.cy) * faktor;
    this.mpp *= faktor;
    this.rita();
  }

  private vid_hjul(h: WheelEvent): void {
    h.preventDefault();
    const [e, n] = this.tillVarld(h.offsetX, h.offsetY);
    this.zoomaVid(e, n, h.deltaY > 0 ? 1.25 : 0.8);
  }

  private vid_ned(h: PointerEvent): void {
    this.duk.setPointerCapture(h.pointerId);
    this.dra = { x: h.offsetX, y: h.offsetY };
    this.duk.classList.add("drar");
  }

  private vid_rorelse(h: PointerEvent): void {
    const [e, n] = this.tillVarld(h.offsetX, h.offsetY);
    this.onMarkor?.(e, n);
    if (!this.dra) return;
    this.cx -= (h.offsetX - this.dra.x) * this.mpp;
    this.cy += (h.offsetY - this.dra.y) * this.mpp;
    this.dra = { x: h.offsetX, y: h.offsetY };
    this.rita();
  }

  private vid_upp(h: PointerEvent): void {
    this.duk.releasePointerCapture(h.pointerId);
    this.dra = null;
    this.duk.classList.remove("drar");
  }

  /* ---------- Ritning ---------- */

  rita(): void {
    if (this.ritaBegard) return;
    this.ritaBegard = window.requestAnimationFrame(() => {
      this.ritaNu();
      this.ritaBegard = 0;
    });
  }

  private ritaNu(): void {
    const c = this.ctx;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--f-karta-bas") || "#efefef";
    c.fillRect(0, 0, this.bredd, this.hojd);
    if (!this.harVy) return;

    if (this.bakgrund.paa && this.bakgrund.urlMall) this.ritaPlattor();
    this.ritaRutnat();
    for (const l of this.lager) {
      if (!l.synlig || !l.bild.complete || l.bild.naturalWidth === 0) continue;
      const [x0, y0] = this.tillSkarm(l.utbredning.x_min, l.utbredning.y_max);
      const [x1, y1] = this.tillSkarm(l.utbredning.x_max, l.utbredning.y_min);
      const cellPx = (l.utbredning.x_max - l.utbredning.x_min) / l.bild.naturalWidth / this.mpp;
      c.globalAlpha = l.opacitet;
      c.imageSmoothingEnabled = cellPx < 1; // skarpa celler när man zoomat in
      c.drawImage(l.bild, x0, y0, x1 - x0, y1 - y0);
      c.globalAlpha = 1;
    }
    this.ritaRutor();
    this.ritaSkalstock();
  }

  private ritaRutnat(): void {
    const c = this.ctx;
    const [e0, n1] = this.tillVarld(0, 0);
    const [e1, n0] = this.tillVarld(this.bredd, this.hojd);
    const steg = trevligtSteg(this.mpp * 110);
    c.lineWidth = 1;
    c.strokeStyle = "rgba(17,17,17,0.10)";
    c.fillStyle = "#6b6b6b";
    c.font = "10px ui-monospace, Menlo, monospace";
    c.textBaseline = "top";
    c.textAlign = "left";
    c.beginPath();
    for (let e = Math.ceil(e0 / steg) * steg; e <= e1; e += steg) {
      const [x] = this.tillSkarm(e, 0);
      c.moveTo(Math.round(x) + 0.5, 0);
      c.lineTo(Math.round(x) + 0.5, this.hojd);
    }
    for (let n = Math.ceil(n0 / steg) * steg; n <= n1; n += steg) {
      const [, y] = this.tillSkarm(0, n);
      c.moveTo(0, Math.round(y) + 0.5);
      c.lineTo(this.bredd, Math.round(y) + 0.5);
    }
    c.stroke();
    for (let e = Math.ceil(e0 / steg) * steg; e <= e1; e += steg) {
      const [x] = this.tillSkarm(e, 0);
      c.fillText(formatTal(e), x + 3, 3);
    }
    for (let n = Math.ceil(n0 / steg) * steg; n <= n1; n += steg) {
      const [, y] = this.tillSkarm(0, n);
      c.fillText(formatTal(n), 3, y + 3);
    }
  }

  private ritaRutor(): void {
    const c = this.ctx;
    c.save();
    c.setLineDash([4, 3]);
    c.lineWidth = 1;
    c.strokeStyle = "rgba(17,17,17,0.55)";
    for (const r of this.rutor) {
      const [x0, y0] = this.tillSkarm(r.x_min, r.y_max);
      const [x1, y1] = this.tillSkarm(r.x_max, r.y_min);
      c.strokeRect(Math.round(x0) + 0.5, Math.round(y0) + 0.5, Math.round(x1 - x0), Math.round(y1 - y0));
    }
    c.restore();
  }

  private ritaSkalstock(): void {
    const c = this.ctx;
    const langd = trevligtSteg(this.mpp * 90);
    const px = langd / this.mpp;
    const x = 16;
    const y = this.hojd - 20;
    c.fillStyle = "rgba(255,255,255,0.85)";
    c.fillRect(x - 8, y - 18, px + 16, 32);
    c.strokeStyle = "#111111";
    c.fillStyle = "#111111";
    c.lineWidth = 2;
    c.beginPath();
    c.moveTo(x, y - 5);
    c.lineTo(x, y);
    c.lineTo(x + px, y);
    c.lineTo(x + px, y - 5);
    c.stroke();
    c.font = "11px ui-monospace, Menlo, monospace";
    c.textBaseline = "bottom";
    c.textAlign = "left";
    c.fillText(langd >= 1000 ? `${langd / 1000} km` : `${langd} m`, x, y - 7);
  }

  /* ---------- Valfri bakgrundskarta ---------- */

  private ritaPlattor(): void {
    const [e0, n1] = this.tillVarld(0, 0);
    const [e1, n0] = this.tillVarld(this.bredd, this.hojd);
    const hornen = [swerefTillWgs84(e0, n0), swerefTillWgs84(e1, n0), swerefTillWgs84(e0, n1), swerefTillWgs84(e1, n1)];
    const lonMin = Math.min(...hornen.map((h) => h[0]));
    const lonMax = Math.max(...hornen.map((h) => h[0]));
    const latMin = Math.min(...hornen.map((h) => h[1]));
    const latMax = Math.max(...hornen.map((h) => h[1]));
    const lat = (latMin + latMax) / 2;
    const z = Math.min(19, Math.max(3, Math.round(Math.log2((156543.03392 * Math.cos((lat * Math.PI) / 180)) / this.mpp))));
    const n = 2 ** z;
    const tx = (lon: number): number => Math.floor(((lon + 180) / 360) * n);
    const ty = (la: number): number => {
      const r = (la * Math.PI) / 180;
      return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n);
    };
    const xa = Math.max(0, tx(lonMin));
    const xb = Math.min(n - 1, tx(lonMax));
    const ya = Math.max(0, ty(latMax));
    const yb = Math.min(n - 1, ty(latMin));
    if ((xb - xa + 1) * (yb - ya + 1) > MAX_PLATTOR) return;
    const lonAv = (x: number): number => (x / n) * 360 - 180;
    const latAv = (y: number): number => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI;
    const c = this.ctx;
    for (let x = xa; x <= xb; x++) {
      for (let y = ya; y <= yb; y++) {
        const url = this.bakgrund.urlMall.replace("{z}", String(z)).replace("{x}", String(x)).replace("{y}", String(y));
        let bild = this.plattor.get(url);
        if (!bild) {
          bild = new Image();
          bild.onload = () => this.rita();
          bild.src = url;
          this.plattor.set(url, bild);
        }
        if (!bild.complete || bild.naturalWidth === 0) continue;
        // Affin passning på tre hörn; över en platta (några hundra meter) är avvikelsen försumbar.
        const nv = wgs84TillSweref(lonAv(x), latAv(y));
        const no = wgs84TillSweref(lonAv(x + 1), latAv(y));
        const sv = wgs84TillSweref(lonAv(x), latAv(y + 1));
        const [ax, ay] = this.tillSkarm(nv[0], nv[1]);
        const [bx, by] = this.tillSkarm(no[0], no[1]);
        const [dx, dy] = this.tillSkarm(sv[0], sv[1]);
        c.save();
        c.transform((bx - ax) / PLATT_PX, (by - ay) / PLATT_PX, (dx - ax) / PLATT_PX, (dy - ay) / PLATT_PX, ax, ay);
        c.globalAlpha = 0.9;
        c.drawImage(bild, 0, 0);
        c.restore();
      }
    }
  }
}
