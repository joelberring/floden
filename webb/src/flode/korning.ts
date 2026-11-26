/*
 * Körningen av det ritade förslaget med de befintliga scenario-API:erna (`kor`, `status`). Lever i
 * modulen, inte i sidan, så att den fortsätter om man byter steg; sidorna prenumererar på läget.
 */
import type { KorningPost, RegnParam, SolParam } from "../karta/kartapi.ts";
import { formatera } from "../tal.ts";
import type { ProjektVy } from "../types.ts";
import { regnSignaturFranKorningar, tillRegnParam } from "./regnval.ts";
import { solSignaturFranKorningar, tillSolParam } from "./solval.ts";
import type { FlodeTillstand } from "./tillstand.ts";

export interface KorningApi {
  korScenario: (
    projekt: string,
    slug: string,
    analyser: string[],
    avtappning_mm_h: number | null,
    regn: RegnParam | null,
    sol: SolParam | null,
  ) => Promise<unknown>;
  korLageIdag: (
    projekt: string,
    analyser: string[],
    avtappning_mm_h: number | null,
    regn: RegnParam | null,
    sol: SolParam | null,
  ) => Promise<unknown>;
  scenarioStatus: (projekt: string, slug: string) => Promise<{ korningar: KorningPost[] }>;
  lageIdagStatus: (projekt: string) => Promise<{ korningar: KorningPost[] }>;
}

async function standardApi(): Promise<KorningApi> {
  return import("../karta/kartapi.ts");
}

type Lyssnare = () => void;
const lyssnare = new Set<Lyssnare>();
const timers = new Map<string, ReturnType<typeof setInterval>>();

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
  if (id !== undefined) clearInterval(id);
  timers.delete(projekt);
}

/**
 * Tillämpar klara serverkörningar på projektets tillstånd. Regn- och solsignaturer beräknas
 * uteslutande ur serverns körningsdata (`regnSignaturFranKorningar` / `solSignaturFranKorningar`),
 * aldrig ur klientens aktuella inställningar (W-B9).
 */
export function tillampaKlaraKorningar(
  t: Pick<
    FlodeTillstand,
    "lageVyer" | "ritVyer" | "lageRegnSignatur" | "ritRegnSignatur" | "lageSolSignatur" | "ritSolSignatur"
  >,
  korningar: readonly KorningPost[],
  lage: boolean,
): void {
  const klara = korningar.filter((x) => x?.status?.status === "klar");
  const vyer = klara.flatMap((x) => x.vyer) as ProjektVy[];
  const harVatten = vyer.some((v) => v.avsnitt === "vatten");
  const harSol = vyer.some((v) => v.avsnitt === "dagsljus");
  const regnSign = harVatten ? regnSignaturFranKorningar(klara) : null;
  const solSign = harSol ? solSignaturFranKorningar(klara) : null;
  if (lage) {
    t.lageVyer = vyer;
    t.lageRegnSignatur = regnSign;
    t.lageSolSignatur = solSign;
  } else {
    t.ritVyer = vyer;
    t.ritRegnSignatur = regnSign;
    t.ritSolSignatur = solSign;
  }
}

/** Startar körningen av förslaget (sparat som scenario) för de valda analyserna. */
export async function startaKorning(
  t: FlodeTillstand,
  analyser: string[],
  api?: KorningApi,
): Promise<void> {
  const sc = t.scenario;
  if (!sc || analyser.length === 0) return;
  const kApi = api ?? (await standardApi());
  const startadMs = Date.now();
  t.korning = { slug: sc.slug, text: "Beräknar … (0 s)", klar: false, fel: null, startadMs };
  t.ritVyer = [];
  signalera();
  try {
    await kApi.korScenario(t.projekt, sc.slug, analyser, t.avtappning, tillRegnParam(t.regn), tillSolParam(t.sol));
  } catch (e) {
    t.korning = { slug: sc.slug, text: "", klar: true, fel: e instanceof Error ? e.message : String(e), startadMs };
    signalera();
    return;
  }
  stoppa(t.projekt);
  timers.set(
    t.projekt,
    setInterval(() => void kolla(t, kApi), 1200),
  );
  await kolla(t, kApi);
}

/** Startar körningen av läget idag (projektets befintliga läge, bara före) för de valda analyserna. */
export async function startaLageIdag(
  t: FlodeTillstand,
  analyser: string[],
  api?: KorningApi,
): Promise<void> {
  if (analyser.length === 0) return;
  const kApi = api ?? (await standardApi());
  const startadMs = Date.now();
  t.korning = { slug: "", text: `Beräknar nuläge … (${formatera(0, 0)} s)`, klar: false, fel: null, startadMs, lage: true };
  t.lageVyer = [];
  signalera();
  try {
    await kApi.korLageIdag(t.projekt, analyser, t.avtappning, tillRegnParam(t.regn), tillSolParam(t.sol));
  } catch (e) {
    t.korning = { slug: "", text: "", klar: true, fel: e instanceof Error ? e.message : String(e), startadMs, lage: true };
    signalera();
    return;
  }
  stoppa(t.projekt);
  timers.set(
    t.projekt,
    setInterval(() => void kolla(t, kApi), 1200),
  );
  await kolla(t, kApi);
}

async function kolla(t: FlodeTillstand, api: KorningApi): Promise<void> {
  const k = t.korning;
  if (!k || k.klar) {
    stoppa(t.projekt);
    return;
  }
  try {
    const st = k.lage ? await api.lageIdagStatus(t.projekt) : await api.scenarioStatus(t.projekt, k.slug);
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
      tillampaKlaraKorningar(t, klara, Boolean(k.lage));
      t.korning = { ...k, klar: true, fel: null, text: `Klart på ${formatera(s, 0)} s.` };
    } else {
      const fr = alla
        .map((x) => x.status.framsteg as { klara: number; totalt: number } | undefined)
        .filter((x): x is { klara: number; totalt: number } => Boolean(x));
      const klara_ = fr.reduce((a, b) => a + b.klara, 0);
      const tot = fr.reduce((a, b) => a + b.totalt, 0);
      t.korning = {
        ...k,
        text:
          tot > 0
            ? `Beräknar … ${formatera(klara_, 0)} av ${formatera(tot, 0)} delar klara (${formatera(s, 0)} s).`
            : `Beräknar … (${formatera(s, 0)} s)`,
      };
    }
  } catch (e) {
    stoppa(t.projekt);
    t.korning = { ...k, klar: true, text: "", fel: e instanceof Error ? e.message : String(e) };
  }
  signalera();
}
