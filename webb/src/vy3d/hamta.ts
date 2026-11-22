/*
 * Hämtning av 3D-buffertar från servern, med delning av pågående anrop och mätning av
 * överförda byte och tid (för prestandamätningen i docs/WEBB3D.md).
 */
import { lasBehallare, type Behallare } from "./format.ts";
import { rutNyckel, type Rut, type Utbr } from "./rutnat.ts";
import type { Origo } from "./koord.ts";

export interface Vy3dInfo {
  format: string;
  projekt: string;
  nyckel: string;
  utbredning: Utbr;
  origo: Origo;
  rutnat: { celler: number; cell0_m: number; nivaer: Array<{ lod: number; cell_m: number; rutstorlek_m: number; rutor: number }> };
  hojd: { z_min: number; z_max: number; kvant_lo: number; kvant_hi: number };
}

export interface Korningsinfo {
  korning: string;
  scenarier: string[];
  bas: string;
  fasad: Record<string, string[]>;
  doman: Record<string, string[]>;
  vatten_tidsserier: Record<string, Array<{ lager: string; minut: number }>>;
}

export interface Matning {
  anrop: number;
  bytes: number;
  millisekunder: number;
}

export class Hamtare {
  readonly matning: Matning = { anrop: 0, bytes: 0, millisekunder: 0 };
  private readonly pagar = new Map<string, Promise<unknown>>();
  private readonly klara = new Map<string, unknown>();

  constructor(
    private readonly projekt: string,
    private readonly hamtaFn: (url: string) => Promise<Response> = (u) => fetch(u),
  ) {}

  private bas(): string {
    return `/api/3d/${encodeURIComponent(this.projekt)}`;
  }

  private async delad<T>(nyckel: string, hamta: () => Promise<T>): Promise<T> {
    if (this.klara.has(nyckel)) return this.klara.get(nyckel) as T;
    const p = this.pagar.get(nyckel);
    if (p) return p as Promise<T>;
    const lopande = hamta().then(
      (v) => {
        this.klara.set(nyckel, v);
        this.pagar.delete(nyckel);
        return v;
      },
      (e) => {
        this.pagar.delete(nyckel);
        throw e;
      },
    );
    this.pagar.set(nyckel, lopande);
    return lopande;
  }

  private async buffer(url: string): Promise<ArrayBuffer> {
    const t0 = performance.now();
    const svar = await this.hamtaFn(url);
    if (!svar.ok) {
      let text = `HTTP ${svar.status}`;
      try {
        text = ((await svar.json()) as { fel?: string }).fel ?? text;
      } catch {
        /* inget JSON-fel */
      }
      throw new Error(text);
    }
    const buf = await svar.arrayBuffer();
    this.matning.anrop += 1;
    this.matning.bytes += buf.byteLength;
    this.matning.millisekunder += performance.now() - t0;
    return buf;
  }

  private async json<T>(url: string): Promise<T> {
    const svar = await this.hamtaFn(url);
    if (!svar.ok) throw new Error(((await svar.json().catch(() => ({}))) as { fel?: string }).fel ?? `HTTP ${svar.status}`);
    return (await svar.json()) as T;
  }

  info(): Promise<Vy3dInfo> {
    return this.delad("info", () => this.json<Vy3dInfo>(this.bas()));
  }

  korning(id: string): Promise<Korningsinfo> {
    return this.delad(`k|${id}`, () => this.json<Korningsinfo>(`${this.bas()}/korning/${encodeURIComponent(id)}`));
  }

  terrang(r: Rut): Promise<Behallare> {
    return this.delad(`t|${rutNyckel(r)}`, async () => lasBehallare(await this.buffer(`${this.bas()}/terrang/${rutNyckel(r)}`)));
  }

  byggnader(korning: string | null, scenario: string | null): Promise<Behallare> {
    const q = korning ? `?korning=${encodeURIComponent(korning)}&scenario=${encodeURIComponent(scenario ?? "")}` : "";
    return this.delad(`b|${q}`, async () => lasBehallare(await this.buffer(`${this.bas()}/byggnader${q}`)));
  }

  mark(korning: string, lager: string, r: Rut): Promise<Behallare> {
    return this.delad(`m|${korning}|${lager}|${rutNyckel(r)}`, async () =>
      lasBehallare(await this.buffer(`${this.bas()}/mark/${encodeURIComponent(korning)}/${encodeURIComponent(lager)}/${rutNyckel(r)}`)),
    );
  }

  fasad(korning: string, scenario: string, analys: string, jamfor: string | null): Promise<Behallare> {
    const q = `?analys=${encodeURIComponent(analys)}${jamfor ? `&jamfor=${encodeURIComponent(jamfor)}` : ""}`;
    return this.delad(`f|${korning}|${scenario}|${q}`, async () => lasBehallare(await this.buffer(`${this.bas()}/fasad/${encodeURIComponent(korning)}/${encodeURIComponent(scenario)}${q}`)));
  }
}
