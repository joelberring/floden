/*
 * Körningen av det ritade förslaget med de befintliga scenario-API:erna (`kor`, `status`). Lever i
 * modulen, inte i sidan, så att den fortsätter om man byter steg; sidorna prenumererar på läget.
 */
import { korLageIdag, korScenario, lageIdagStatus, scenarioStatus } from "../karta/kartapi.ts";
import { formatera } from "../tal.ts";
import type { ProjektVy } from "../types.ts";
import { regnSignatur, tillRegnParam } from "./regnval.ts";
import { solSignatur, tillSolParam } from "./solval.ts";
import type { FlodeTillstand } from "./tillstand.ts";

type Lyssnare = () => void;
const lyssnare = new Set<Lyssnare>();
const timers = new Map<string, number>();

export function vidKorning(f: Lyssnare): () => void {
  lyssnare.add(f);
  return () => lyssnare.delete(f);
}

function signalera(): void {
  for (const f of lyssnare) f();
}

export function arPagaende(t: FlodeTillstand): boolean {
  return Boolean(t.korning && !t.korning.klar);
}

function stoppa(projekt: string): void {
  const id = timers.get(projekt);
  if (id !== undefined) window.clearInterval(id);
  timers.delete(projekt);
}

/** Startar körningen av förslaget (sparat som scenario) för de valda analyserna. */
export async function startaKorning(t: FlodeTillstand, analyser: string[]): Promise<void> {
  const sc = t.scenario;
  if (!sc || analyser.length === 0) return;
  const startadMs = Date.now();
  const sign = regnSignatur(t.regn, t.avtappning);
  const solSign = solSignatur(t.sol);
  t.korning = { slug: sc.slug, text: "Beräknar … (0 s)", klar: false, fel: null, startadMs };
  t.ritVyer = [];
  signalera();
  try {
    await korScenario(t.projekt, sc.slug, analyser, t.avtappning, tillRegnParam(t.regn), tillSolParam(t.sol));
  } catch (e) {
    t.korning = { slug: sc.slug, text: "", klar: true, fel: e instanceof Error ? e.message : String(e), startadMs };
    signalera();
    return;
  }
  stoppa(t.projekt);
  timers.set(t.projekt, window.setInterval(() => void kolla(t, sign, solSign), 1200));
  void kolla(t, sign, solSign);
}

/** Startar körningen av läget idag (projektets befintliga läge, bara före) för de valda analyserna. */
export async function startaLageIdag(t: FlodeTillstand, analyser: string[]): Promise<void> {
  if (analyser.length === 0) return;
  const startadMs = Date.now();
  const sign = regnSignatur(t.regn, t.avtappning);
  const solSign = solSignatur(t.sol);
  t.korning = { slug: "", text: `Beräknar nuläge … (${formatera(0, 0)} s)`, klar: false, fel: null, startadMs, lage: true };
  t.lageVyer = [];
  signalera();
  try {
    await korLageIdag(t.projekt, analyser, t.avtappning, tillRegnParam(t.regn), tillSolParam(t.sol));
  } catch (e) {
    t.korning = { slug: "", text: "", klar: true, fel: e instanceof Error ? e.message : String(e), startadMs, lage: true };
    signalera();
    return;
  }
  stoppa(t.projekt);
  timers.set(t.projekt, window.setInterval(() => void kolla(t, sign, solSign), 1200));
  void kolla(t, sign, solSign);
}

async function kolla(t: FlodeTillstand, sign: string, solSign: string): Promise<void> {
  const k = t.korning;
  if (!k || k.klar) {
    stoppa(t.projekt);
    return;
  }
  try {
    const st = k.lage ? await lageIdagStatus(t.projekt) : await scenarioStatus(t.projekt, k.slug);
    const alla = st.korningar;
    const fel = alla.find((x) => x.status.status === "fel");
    const klara = alla.filter((x) => x.status.status === "klar");
    const s = Math.max(0, Math.round((Date.now() - k.startadMs) / 1000));
    if (fel) {
      stoppa(t.projekt);
      const f = fel.status.fel_forklaring ?? fel.status.fel;
      t.korning = { ...k, klar: true, text: "", fel: `Beräkningen misslyckades: ${String(f ?? "okänt fel")}` };
    } else if (alla.length > 0 && klara.length === alla.length) {
      stoppa(t.projekt);
      const vyer = klara.flatMap((x) => x.vyer) as ProjektVy[];
      const harVatten = vyer.some((v) => v.avsnitt === "vatten");
      const harSol = vyer.some((v) => v.avsnitt === "dagsljus");
      if (k.lage) {
        t.lageVyer = vyer;
        if (harVatten) t.lageRegnSignatur = sign;
        if (harSol) t.lageSolSignatur = solSign;
      } else {
        t.ritVyer = vyer;
        if (harVatten) t.ritRegnSignatur = sign;
        if (harSol) t.ritSolSignatur = solSign;
      }
      t.korning = { ...k, klar: true, fel: null, text: `Klart på ${formatera(s, 0)} s.` };
    } else {
      const fr = alla.map((x) => x.status.framsteg as { klara: number; totalt: number } | undefined).filter((x): x is { klara: number; totalt: number } => Boolean(x));
      const klara_ = fr.reduce((a, b) => a + b.klara, 0);
      const tot = fr.reduce((a, b) => a + b.totalt, 0);
      t.korning = { ...k, text: tot > 0 ? `Beräknar … ${formatera(klara_, 0)} av ${formatera(tot, 0)} delar klara (${formatera(s, 0)} s).` : `Beräknar … (${formatera(s, 0)} s)` };
    }
  } catch (e) {
    stoppa(t.projekt);
    t.korning = { ...k, klar: true, text: "", fel: e instanceof Error ? e.message : String(e) };
  }
  signalera();
}
