/*
 * Provsidan `#/prov/webgpu`: kör dagsljus- och vattenskuggarna på användarens grafikkort, hämtar
 * serverns facit för samma fall och visar skillnad och tid. Resultatet kan laddas ned som
 * `floden-benchmark/v1` (källa "webblasare"). Skissläge: klientens f32-tal är aldrig rapporttal.
 *
 * Bara för superadmin när inloggning är på (servern avgör, `/api/prov/*` svarar 403 annars) och
 * alltid öppen lokalt när inloggning är av. Sidan har ingen länk i menyn.
 */
import { lasJson } from "../api";
import { WebGpuDagsljusMotor } from "../gpu/webgpuMotor";
import { WebGpuVattenMotor } from "../gpu/vattenMotor";
import type { VattenGpuIndata } from "../gpu/vattenMotor";
import "./prov.css";
import {
  type BenchmarkDel,
  type BenchmarkFall,
  fallFranJamforelse,
  hoppatFall,
  skapaBenchmarkDokument,
  type BenchmarkDokument,
} from "./benchmarkFormat";
import { avkodaFalt, jamforFalt, median } from "./jamfor";
import { provSidaHtml, type ProvFallInfo, type ProvResultatRad, type ProvTillstand } from "./provSida";
import { provTillgang } from "./provtillgang";
import { upptackaWebGpu, ryms, type WebGpuStatus } from "./webgpuStatus";

interface B64 {
  dtyp: string;
  form: number[];
  base64: string;
}
interface FaltFacit extends B64 {
  enhet: string;
}
interface DagsljusIndataSvar {
  nx: number;
  ny: number;
  cell_m: number;
  rmax_celler: number;
  n_riktningar: number;
  steg_h: number;
  z_m: B64;
  sol_hojd_rad: B64;
  sol_azimut_rad: B64;
}
interface VattenIndataSvar {
  nx: number;
  ny: number;
  dx: number;
  dy: number;
  dt: number;
  theta: number;
  min_djup: number;
  z_bed: B64;
  manning: B64;
  aktiv: B64;
  steg: { antal: number; regn_m: number }[];
}
interface ProvFallSvar {
  id: string;
  typ: string;
  namn: string;
  kodversion: string;
  indata: unknown;
  facit: {
    kortid_s: number;
    beraknad_med: string;
    falt: Record<string, FaltFacit>;
    tolerans: Record<string, number>;
    massbalans_rel?: number;
  };
}

const DAGSLJUS_ID = "dagsljus-kvarter300";
const VATTEN_ID = "vatten2d-lutande-plan";

let tillstand: ProvTillstand = {
  tillgang: "laddar",
  webgpu: "laddar",
  fall: [],
  resultat: [],
  problem: [],
  pagar: null,
  fel: null,
  upprepningar: 5,
  toleranser: {},
};
let vy: HTMLElement | null = null;
let lyssnare: ((e: Event) => void) | null = null;
let webgpuStatus: WebGpuStatus | null = null;
let kodversion = "okand";
const fallDelar = new Map<string, { del: string; fall: BenchmarkFall[]; skal?: string }>();
const fallCache = new Map<string, ProvFallSvar>();

function rita(): void {
  if (vy) vy.innerHTML = provSidaHtml(tillstand);
}

function uppdatera(delta: Partial<ProvTillstand>): void {
  tillstand = { ...tillstand, ...delta };
  rita();
}

export function avslutaProv(): void {
  if (vy && lyssnare) vy.removeEventListener("click", lyssnare);
  vy = null;
  lyssnare = null;
}

export async function visaProvWebgpu(behallare: HTMLElement): Promise<void> {
  avslutaProv();
  vy = behallare;
  tillstand = { ...tillstand, tillgang: "laddar", webgpu: "laddar", resultat: [], problem: [], fel: null, pagar: null };
  fallDelar.clear();
  rita();
  lyssnare = (e: Event) => {
    const mal = e.target instanceof Element ? e.target.closest<HTMLElement>("[data-prov]") : null;
    if (!mal || !(mal instanceof HTMLButtonElement)) return;
    void hanteraKlick(mal.dataset.prov ?? "");
  };
  behallare.addEventListener("click", lyssnare);

  try {
    const svar = await fetch("/konto/jag", { headers: { Accept: "application/json" } });
    let data: unknown = null;
    try {
      data = await svar.json();
    } catch {
      data = null;
    }
    const tillgang = provTillgang(svar.status, data);
    if (tillgang !== "tillaten") {
      uppdatera({ tillgang });
      return;
    }
  } catch {
    uppdatera({ tillgang: "okand" });
    return;
  }
  uppdatera({ tillgang: "tillaten" });
  try {
    webgpuStatus = await upptackaWebGpu(typeof navigator === "undefined" ? undefined : (navigator as never));
    const katalog = await lasJson<{ fall: ProvFallInfo[] }>("/api/prov/webgpu/fall");
    const toleranser: Record<string, number> = {};
    for (const f of katalog.fall) {
      const s = await lasJson<ProvFallSvar>(`/api/prov/webgpu/fall/${f.id}`);
      kodversion = s.kodversion;
      for (const [k, v] of Object.entries(s.facit.tolerans)) toleranser[`${f.id}:${k}`] = v;
      fallCache.set(f.id, s);
    }
    uppdatera({ webgpu: webgpuStatus, fall: katalog.fall, toleranser });
  } catch (e) {
    uppdatera({
      webgpu: webgpuStatus ?? "laddar",
      fel: `Kunde inte hämta provfall från servern: ${e instanceof Error ? e.message : String(e)}`,
    });
  }
}

function lasInmatning(): { upprepningar: number; toleranser: Record<string, number> } {
  const upp = Number((vy?.querySelector<HTMLInputElement>("[data-prov-upprepningar]"))?.value ?? 5);
  const toleranser: Record<string, number> = { ...tillstand.toleranser };
  vy?.querySelectorAll<HTMLInputElement>("[data-prov-tolerans]").forEach((el) => {
    const v = Number(el.value);
    if (Number.isFinite(v) && v >= 0) toleranser[el.dataset.provTolerans ?? ""] = v;
  });
  return { upprepningar: Number.isInteger(upp) && upp >= 1 && upp <= 50 ? upp : 5, toleranser };
}

async function hanteraKlick(vad: string): Promise<void> {
  if (tillstand.pagar !== null) return;
  if (vad === "ladda-ned") {
    ladda_ned();
    return;
  }
  const { upprepningar, toleranser } = lasInmatning();
  uppdatera({ upprepningar, toleranser, fel: null, problem: [], resultat: [] });
  fallDelar.clear();
  const lista = vad === "kor-dagsljus" ? [DAGSLJUS_ID] : vad === "kor-vatten" ? [VATTEN_ID] : [DAGSLJUS_ID, VATTEN_ID];
  const rader: ProvResultatRad[] = [];
  const problem: { fallId: string; text: string }[] = [];
  for (const id of lista) {
    uppdatera({ pagar: `Kör ${id} (en uppvärmning och ${upprepningar} tidtagna körningar) …` });
    try {
      const nya = id === DAGSLJUS_ID ? await korDagsljus(upprepningar, toleranser) : await korVatten(upprepningar, toleranser);
      rader.push(...nya);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      problem.push({ fallId: id, text });
      registreraFall(id === DAGSLJUS_ID ? "dagsljus" : "skyfall", {
        id,
        namn: id,
        status: "fel",
        skal: text,
      });
    }
    uppdatera({ resultat: [...rader], problem: [...problem] });
  }
  uppdatera({ pagar: null, resultat: rader, problem });
}

function registreraFall(del: string, f: BenchmarkFall): void {
  const pos = fallDelar.get(del) ?? { del, fall: [] };
  pos.fall = [...pos.fall.filter((x) => x.id !== f.id), f];
  fallDelar.set(del, pos);
}

function hamtaFall(id: string): ProvFallSvar {
  const s = fallCache.get(id);
  if (!s) throw new Error(`Provfallet ${id} är inte hämtat från servern.`);
  return s;
}

function tol(toleranser: Record<string, number>, id: string, falt: string, standard: number): number {
  const v = toleranser[`${id}:${falt}`];
  return v === undefined || !Number.isFinite(v) ? standard : v;
}

async function korDagsljus(n: number, toleranser: Record<string, number>): Promise<ProvResultatRad[]> {
  const s = hamtaFall(DAGSLJUS_ID);
  const i = s.indata as DagsljusIndataSvar;
  const z = avkodaFalt(i.z_m) as Float32Array;
  const hojd = avkodaFalt(i.sol_hojd_rad) as Float32Array;
  const azimut = avkodaFalt(i.sol_azimut_rad) as Float32Array;
  const { nx, ny } = i;
  const bytes = nx * ny * 4;
  if (webgpuStatus && !ryms(webgpuStatus, bytes)) throw new Error("Fältet ryms inte i adapterns bufferbegränsningar.");
  const solbanor = Array.from(hojd, (h, k) => ({ hojd_rad: h, azimut_rad: azimut[k] as number }));
  const indata = {
    nx,
    ny,
    cell_m: i.cell_m,
    rmax_celler: i.rmax_celler,
    n_riktningar: i.n_riktningar,
    steg_h: i.steg_h,
    z_m: z,
    solbanor,
  };
  const tSkapa = performance.now();
  const motor = await WebGpuDagsljusMotor.skapa();
  if (!motor) throw new Error("WebGPU gick inte att initiera.");
  const skapaS = (performance.now() - tSkapa) / 1000;
  try {
    const forsta = await motor.korSvfOchSoltimmar(indata);
    const tider: number[] = [];
    let senaste = forsta;
    for (let k = 0; k < n; k += 1) {
      senaste = await motor.korSvfOchSoltimmar(indata);
      tider.push(senaste.kortid_ms / 1000);
    }
    const gpuMb = (5 * bytes + solbanor.length * 8 + 32) / (1024 * 1024);
    const ut: ProvResultatRad[] = [];
    const delar: Array<[string, Float32Array]> = [["svf", senaste.svf], ["soltimmar_h", senaste.soltimmar_h]];
    for (const [falt, gpu] of delar) {
      const facit = avkodaFalt(s.facit.falt[falt] as B64) as Float32Array;
      const toleransen = tol(toleranser, DAGSLJUS_ID, falt, s.facit.tolerans[falt] ?? 0);
      const j = jamforFalt(facit, gpu, toleransen);
      const enhet = (s.facit.falt[falt] as FaltFacit).enhet;
      registreraFall("dagsljus", fallFranJamforelse({
        id: `${DAGSLJUS_ID}:${falt}`,
        namn: `${s.namn} (${falt})`,
        tiderSekunder: tider,
        enhet,
        jamforelse: j,
        beskrivning: `GPU (f32) mot server (f64), fält ${falt}`,
        minne: { gpu_buffrar_mb: Math.round(gpuMb * 100) / 100 },
        extra: {
          forsta_koerning_s: forsta.kortid_ms / 1000,
          skapa_motor_s: skapaS,
          server_kortid_s: s.facit.kortid_s,
          facit_beraknad_med: s.facit.beraknad_med,
          berakningsplats: senaste.berakningsplats,
          indata: { nx, ny, n_riktningar: indata.n_riktningar, rmax_celler: indata.rmax_celler },
        },
      }));
      ut.push(radFran(`${DAGSLJUS_ID}`, falt, enhet, tider, forsta.kortid_ms, j, s.facit.kortid_s));
    }
    return ut;
  } finally {
    motor.avsluta();
  }
}

async function korVatten(n: number, toleranser: Record<string, number>): Promise<ProvResultatRad[]> {
  const s = hamtaFall(VATTEN_ID);
  const i = s.indata as VattenIndataSvar;
  const aktiv8 = avkodaFalt(i.aktiv) as Uint8Array;
  const indata: VattenGpuIndata = {
    nx: i.nx,
    ny: i.ny,
    dx: i.dx,
    dy: i.dy,
    dt: i.dt,
    theta: i.theta,
    min_djup: i.min_djup,
    z_bed: avkodaFalt(i.z_bed) as Float32Array,
    manning: avkodaFalt(i.manning) as Float32Array,
    aktiv: Uint32Array.from(aktiv8),
    steg: i.steg,
  };
  const tSkapa = performance.now();
  const motor = await WebGpuVattenMotor.skapa();
  if (!motor) throw new Error("WebGPU gick inte att initiera.");
  const skapaS = (performance.now() - tSkapa) / 1000;
  try {
    const forsta = await motor.kor(indata);
    const tider: number[] = [];
    let senaste = forsta;
    for (let k = 0; k < n; k += 1) {
      senaste = await motor.kor(indata);
      tider.push(senaste.kortid_ms / 1000);
    }
    const ut: ProvResultatRad[] = [];
    const delar: Array<[string, Float32Array]> = [["max_djup_m", senaste.max_djup_m], ["slutdjup_m", senaste.slutdjup_m]];
    for (const [falt, gpu] of delar) {
      const facit = avkodaFalt(s.facit.falt[falt] as B64) as Float32Array;
      const toleransen = tol(toleranser, VATTEN_ID, falt, s.facit.tolerans[falt] ?? 0);
      const j = jamforFalt(facit, gpu, toleransen);
      registreraFall("skyfall", fallFranJamforelse({
        id: `${VATTEN_ID}:${falt}`,
        namn: `${s.namn} (${falt})`,
        tiderSekunder: tider,
        enhet: "m",
        jamforelse: j,
        beskrivning: `GPU (f32) mot samma schema i f64 på servern, fält ${falt}`,
        minne: { gpu_buffrar_mb: Math.round(senaste.gpu_buffrar_mb * 100) / 100 },
        extra: {
          forsta_koerning_s: forsta.kortid_ms / 1000,
          skapa_motor_s: skapaS,
          server_kortid_s: s.facit.kortid_s,
          facit_beraknad_med: s.facit.beraknad_med,
          berakningsplats: senaste.berakningsplats,
          tidssteg: indata.steg.reduce((a, b) => a + b.antal, 0),
          indata: { nx: indata.nx, ny: indata.ny },
        },
      }));
      ut.push(radFran(VATTEN_ID, falt, "m", tider, forsta.kortid_ms, j, s.facit.kortid_s));
    }
    return ut;
  } finally {
    motor.avsluta();
  }
}

function radFran(
  fallId: string,
  falt: string,
  enhet: string,
  tiderS: number[],
  forstaMs: number,
  j: ReturnType<typeof jamforFalt>,
  serverS: number,
): ProvResultatRad {
  return {
    fallId,
    falt,
    enhet,
    medianMs: median(tiderS) * 1000,
    minMs: Math.min(...tiderS) * 1000,
    maxMs: Math.max(...tiderS) * 1000,
    n: tiderS.length,
    forstaMs,
    storstaFel: j.storstaFel,
    medelFel: j.medelFel,
    andelOverTolerans: j.andelOverTolerans,
    tolerans: j.tolerans,
    antalIckeAndliga: j.antalIckeAndliga,
    godkand: Number.isFinite(j.storstaFel) && j.storstaFel <= j.tolerans,
    serverMs: serverS * 1000,
  };
}

/** Dokumentet för nedladdning: körda fall, och fall som inte kunde köras med skäl. */
export function byggDokument(): BenchmarkDokument {
  const delar: Record<string, BenchmarkDel> = {};
  for (const [namn, d] of fallDelar) delar[namn] = { status: "kord", skal: null, fall: d.fall };
  if (Object.keys(delar).length === 0) {
    const skal = webgpuStatus && !webgpuStatus.tillganglig ? webgpuStatus.meddelande : "Inget fall har körts än.";
    delar.dagsljus = { status: "hoppas_over", skal, fall: [hoppatFall(DAGSLJUS_ID, "Dagsljus", skal)] };
    delar.skyfall = { status: "hoppas_over", skal, fall: [hoppatFall(VATTEN_ID, "Vatten 2D", skal)] };
  }
  const nav = typeof navigator === "undefined" ? null : (navigator as Navigator & { deviceMemory?: number });
  return skapaBenchmarkDokument({
    kodversion,
    maskin: {
      cpu: null,
      karnor: nav?.hardwareConcurrency ?? null,
      os: nav?.platform ?? null,
      python: null,
      numpy: null,
      rust_karna: null,
      gpu: webgpuStatus
        ? { tillganglig: webgpuStatus.tillganglig, adapter: webgpuStatus.adapter, begransningar: webgpuStatus.begransningar }
        : null,
      webblasare: nav ? { user_agent: nav.userAgent, sprak: nav.language, enhetsminne_gb: nav.deviceMemory ?? null } : null,
    },
    installning: {
      uppvarmning: 1,
      upprepningar: tillstand.upprepningar,
      tidsmatt: "median av upprepningar, performance.now() runt hela körningen (uppladdning, beräkning, läsning tillbaka)",
      minnesmatt: ["gpu_buffrar_mb: summan av buffertstorlekarna som skapas (beräknad, inte uppmätt VRAM)"],
      toleranser: tillstand.toleranser,
    },
    delar,
  });
}

function ladda_ned(): void {
  try {
    const dok = byggDokument();
    const blob = new Blob([JSON.stringify(dok, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `floden-benchmark-webblasare-${dok.skapad_utc.slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    uppdatera({ fel: `Kunde inte skapa JSON: ${e instanceof Error ? e.message : String(e)}` });
  }
}
