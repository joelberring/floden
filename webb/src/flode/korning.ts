/*
 * Körningen av det ritade förslaget med de befintliga scenario-API:erna (`kor`, `status`). Lever i
 * modulen, inte i sidan, så att den fortsätter om man byter steg; sidorna prenumererar på läget.
 */
import { korScenario, scenarioStatus } from "../karta/kartapi.ts";
import type { ProjektVy } from "../types.ts";
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
  t.korning = { slug: sc.slug, text: "Beräknar … (0 s)", klar: false, fel: null, startadMs };
  t.ritVyer = [];
  signalera();
  try {
    await korScenario(t.projekt, sc.slug, analyser, t.avtappning);
  } catch (e) {
    t.korning = { slug: sc.slug, text: "", klar: true, fel: e instanceof Error ? e.message : String(e), startadMs };
    signalera();
    return;
  }
  stoppa(t.projekt);
  timers.set(t.projekt, window.setInterval(() => void kolla(t), 1200));
  void kolla(t);
}

async function kolla(t: FlodeTillstand): Promise<void> {
  const k = t.korning;
  if (!k || k.klar) {
    stoppa(t.projekt);
    return;
  }
  try {
    const st = await scenarioStatus(t.projekt, k.slug);
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
      t.ritVyer = klara.flatMap((x) => x.vyer) as ProjektVy[];
      t.korning = { ...k, klar: true, fel: null, text: `Klart på ${s} s.` };
    } else {
      const fr = alla.map((x) => x.status.framsteg as { klara: number; totalt: number } | undefined).filter((x): x is { klara: number; totalt: number } => Boolean(x));
      const klara_ = fr.reduce((a, b) => a + b.klara, 0);
      const tot = fr.reduce((a, b) => a + b.totalt, 0);
      t.korning = { ...k, text: tot > 0 ? `Beräknar … ${klara_} av ${tot} delar klara (${s} s).` : `Beräknar … (${s} s)` };
    }
  } catch (e) {
    stoppa(t.projekt);
    t.korning = { ...k, klar: true, text: "", fel: e instanceof Error ? e.message : String(e) };
  }
  signalera();
}
