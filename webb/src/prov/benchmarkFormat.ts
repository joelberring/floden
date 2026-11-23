/*
 * Formatet `floden-benchmark/v1` (K37 i docs/DATAKONTRAKT.md) för webbläsarens prov.
 *
 * Samma format som serverns `floden benchmark` (src/benchmark/format.py), med källa
 * "webblasare". Klientens tal är f32 och bara skissläge; det står i anmärkningen.
 */
import type { FaltJamforelse } from "./jamfor.ts";
import { median } from "./jamfor.ts";

export const BENCHMARK_FORMAT = "floden-benchmark/v1" as const;
export const SKISSLAGE_TEXT =
  "Skissläge: tal från klientens f32-beräkning får bara användas som skiss. " +
  "Facit är serverns f64-beräkning. Tiderna gäller den här webbläsaren och det här grafikkortet.";

export interface BenchmarkFall {
  id: string;
  namn: string;
  status: "ok" | "fel" | "hoppas_over";
  skal?: string;
  tid_s?: { median: number; min: number; max: number; n: number; alla: number[] };
  minne?: Record<string, number | null>;
  avvikelse?: {
    varde: number;
    enhet: string;
    grans: number | null;
    godkand: boolean | null;
    beskrivning: string;
    matt: Record<string, number>;
  } | null;
  extra?: Record<string, unknown>;
}

export interface BenchmarkDel {
  status: "kord" | "hoppas_over" | "kraver_lokal_kallmapp";
  skal: string | null;
  fall: BenchmarkFall[];
}

export interface BenchmarkDokument {
  format: typeof BENCHMARK_FORMAT;
  kalla: "server" | "webblasare";
  skapad_utc: string;
  kodversion: string;
  maskin: Record<string, unknown>;
  installning: Record<string, unknown>;
  anmarkning: string;
  delar: Record<string, BenchmarkDel>;
}

/** Tidtagning som `tid_s`-objektet i K37 (sekunder). */
export function tidObjekt(tiderSekunder: readonly number[]): NonNullable<BenchmarkFall["tid_s"]> {
  return {
    median: median(tiderSekunder),
    min: Math.min(...tiderSekunder),
    max: Math.max(...tiderSekunder),
    n: tiderSekunder.length,
    alla: tiderSekunder.map((t) => Math.round(t * 1e6) / 1e6),
  };
}

/** Ett fall ur en fältjämförelse. `godkand` betyder största felet <= toleransen. */
export function fallFranJamforelse(args: {
  id: string;
  namn: string;
  tiderSekunder: readonly number[];
  enhet: string;
  jamforelse: FaltJamforelse;
  beskrivning: string;
  minne?: Record<string, number | null>;
  extra?: Record<string, unknown>;
}): BenchmarkFall {
  const j = args.jamforelse;
  return {
    id: args.id,
    namn: args.namn,
    status: "ok",
    tid_s: tidObjekt(args.tiderSekunder),
    minne: args.minne ?? {},
    avvikelse: {
      // Ett icke-ändligt fel kan inte skrivas i JSON; då skrivs inget värde och fallet är underkänt.
      varde: Number.isFinite(j.storstaFel) ? j.storstaFel : Number.MAX_VALUE,
      enhet: args.enhet,
      grans: j.tolerans,
      godkand: Number.isFinite(j.storstaFel) && j.storstaFel <= j.tolerans,
      beskrivning: args.beskrivning,
      matt: {
        medelfel: Number.isFinite(j.medelFel) ? j.medelFel : -1,
        andel_over_tolerans: j.andelOverTolerans,
        tolerans: j.tolerans,
        antal_celler: j.antal,
        antal_ickeandliga: j.antalIckeAndliga,
      },
    },
    extra: args.extra ?? {},
  };
}

export function hoppatFall(id: string, namn: string, skal: string): BenchmarkFall {
  return { id, namn, status: "hoppas_over", skal };
}

export function skapaBenchmarkDokument(args: {
  kodversion: string;
  maskin: Record<string, unknown>;
  installning: Record<string, unknown>;
  delar: Record<string, BenchmarkDel>;
  skapadUtc?: string;
}): BenchmarkDokument {
  const dok: BenchmarkDokument = {
    format: BENCHMARK_FORMAT,
    kalla: "webblasare",
    skapad_utc: args.skapadUtc ?? new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    kodversion: args.kodversion,
    maskin: args.maskin,
    installning: args.installning,
    anmarkning: SKISSLAGE_TEXT,
    delar: args.delar,
  };
  const fel = kontrolleraDokument(dok);
  if (fel.length > 0) throw new Error(`Ogiltigt benchmarkdokument: ${fel.join("; ")}`);
  return dok;
}

const DELSTATUS = ["kord", "hoppas_over", "kraver_lokal_kallmapp"];
const FALLSTATUS = ["ok", "fel", "hoppas_over"];

function ar_tal(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Samma regler som `benchmark.format.kontrollera` i Python. Tom lista = giltigt. */
export function kontrolleraDokument(d: unknown): string[] {
  const fel: string[] = [];
  if (typeof d !== "object" || d === null || Array.isArray(d)) return ["Dokumentet måste vara ett objekt."];
  const o = d as Record<string, unknown>;
  if (o.format !== BENCHMARK_FORMAT) fel.push(`format måste vara ${BENCHMARK_FORMAT}.`);
  if (o.kalla !== "server" && o.kalla !== "webblasare") fel.push("kalla måste vara server eller webblasare.");
  for (const k of ["skapad_utc", "kodversion"]) {
    if (typeof o[k] !== "string" || o[k] === "") fel.push(`${k} saknas.`);
  }
  for (const k of ["maskin", "installning", "delar"]) {
    if (typeof o[k] !== "object" || o[k] === null || Array.isArray(o[k])) fel.push(`${k} måste vara ett objekt.`);
  }
  if (fel.length > 0) return fel;
  for (const [namn, del] of Object.entries(o.delar as Record<string, BenchmarkDel>)) {
    if (!DELSTATUS.includes(del?.status)) fel.push(`delar.${namn}.status ogiltig.`);
    else if (del.status !== "kord" && !del.skal) fel.push(`delar.${namn} är inte körd och måste ange skal.`);
    if (!Array.isArray(del?.fall)) {
      fel.push(`delar.${namn}.fall måste vara en lista.`);
      continue;
    }
    for (const f of del.fall) {
      const var_ = `delar.${namn}.${f?.id}`;
      if (typeof f?.id !== "string" || f.id === "") fel.push(`delar.${namn}: varje fall måste ha ett id.`);
      if (!FALLSTATUS.includes(f?.status)) fel.push(`${var_}.status ogiltig.`);
      else if (f.status !== "ok") {
        if (!f.skal) fel.push(`${var_} har status ${f.status} och måste ange skal.`);
        continue;
      }
      if (!ar_tal(f?.tid_s?.median) || (f.tid_s?.median ?? -1) < 0) fel.push(`${var_}.tid_s.median måste vara ett tal >= 0.`);
      if (typeof f?.minne !== "object" || f.minne === null) fel.push(`${var_}.minne måste vara ett objekt.`);
      const av = f?.avvikelse;
      if (av != null) {
        if (!ar_tal(av.varde)) fel.push(`${var_}.avvikelse.varde måste vara ett tal.`);
        if (typeof av.enhet !== "string") fel.push(`${var_}.avvikelse.enhet saknas.`);
      }
    }
  }
  return fel;
}
