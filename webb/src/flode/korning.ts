/*
 * Körningen av det ritade förslaget med de befintliga scenario-API:erna (`kor`, `status`). Lever i
 * modulen, inte i sidan, så att den fortsätter om man byter steg; sidorna prenumererar på läget.
 */
import type { KorningPost, RegnParam, SkyfallMetod, SolParam } from "../karta/kartapi.ts";
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
    skyfall_metod?: SkyfallMetod | null,
  ) => Promise<unknown>;
  korLageIdag: (
    projekt: string,
    analyser: string[],
    avtappning_mm_h: number | null,
    regn: RegnParam | null,
    sol: SolParam | null,
    skyfall_metod?: SkyfallMetod | null,
  ) => Promise<unknown>;
  scenarioStatus: (projekt: string, slug: string) => Promise<{ korningar: KorningPost[] }>;
  lageIdagStatus: (projekt: string) => Promise<{ korningar: KorningPost[] }>;
  /** Valfri poll-intervall i ms (främst för deterministiska tester; standard 1200 ms). */
  intervallMs?: number;
}

export interface AktivKorningFilter {
  korningIds: Set<string>;
  mallar: Set<string>;
  analyser: string[];
}

async function standardApi(): Promise<KorningApi> {
  return import("../karta/kartapi.ts");
}

type Lyssnare = () => void;
const lyssnare = new Set<Lyssnare>();
const timers = new Map<string, ReturnType<typeof setInterval>>();
const aktivaFilter = new Map<string, AktivKorningFilter>();
const pagaendeKoll = new Set<string>();
const klaraAvsnittPerProjekt = new Map<string, string[]>();
const senasteRitSlug = new Map<string, string>();

const MALL_ETIKETT: Record<string, string> = {
  vatten: "Skyfall",
  skyfall: "Skyfall",
  dagsljus: "Dagsljus",
  sol: "Dagsljus",
  ekologi: "Ekologi",
  luft: "Luft",
};

function normaliseraMall(mall: string | null | undefined): string | null {
  const m = (mall ?? "").trim().toLowerCase();
  if (!m) return null;
  if (m === "vatten" || m === "skyfall") return "vatten";
  if (m === "dagsljus" || m === "sol") return "dagsljus";
  if (m === "ekologi") return "ekologi";
  if (m === "luft") return "luft";
  return m;
}

function mallEtikett(mall: string | null | undefined): string {
  const k = (mall ?? "").trim().toLowerCase();
  return MALL_ETIKETT[k] ?? (mall?.trim() || "Analys");
}

function mallarFranAnalyser(analyser: readonly string[]): Set<string> {
  const ut = new Set<string>();
  for (const id of analyser) {
    const prefix = id.includes(".") ? id.slice(0, id.indexOf(".")) : id;
    const m = normaliseraMall(prefix);
    if (m) ut.add(m);
  }
  return ut;
}

function extraktStartatFilter(svar: unknown, analyser: readonly string[]): AktivKorningFilter {
  const korningIds = new Set<string>();
  const mallar = new Set<string>();
  if (svar && typeof svar === "object" && Array.isArray((svar as { fragor?: unknown }).fragor)) {
    for (const post of (svar as { fragor: unknown[] }).fragor) {
      if (!post || typeof post !== "object") continue;
      const p = post as { mall?: unknown; korning_id?: unknown; korning?: { korning_id?: unknown } | null };
      const kid = typeof p.korning?.korning_id === "string"
        ? p.korning.korning_id
        : typeof p.korning_id === "string"
          ? p.korning_id
          : null;
      if (kid && kid.trim()) korningIds.add(kid.trim());
      if (typeof p.mall === "string") {
        const m = normaliseraMall(p.mall);
        if (m) mallar.add(m);
      }
    }
  }
  if (mallar.size === 0) {
    for (const m of mallarFranAnalyser(analyser)) mallar.add(m);
  }
  return { korningIds, mallar, analyser: [...analyser] };
}

function explicitKorningId(post: KorningPost): string | null {
  const stId = post?.status?.korning_id;
  if (typeof stId === "string" && stId.trim()) return stId.trim();
  const topId = (post as { korning_id?: unknown })?.korning_id;
  if (typeof topId === "string" && topId.trim()) return topId.trim();
  return null;
}

function matcharMall(post: KorningPost, mallar: ReadonlySet<string>): boolean {
  const m = normaliseraMall(post?.mall);
  if (m && mallar.has(m)) return true;
  if (Array.isArray(post?.vyer)) {
    for (const v of post.vyer) {
      const av = normaliseraMall(v?.avsnitt);
      if (av && mallar.has(av)) return true;
    }
  }
  return false;
}

/**
 * Filtrerar serverns statuslista till de körningar som faktiskt startades i aktuell batch
 * (i första hand via `korning_id`, med bakåtkompatibel reserv via mall/ämne när mock-API saknar id).
 */
export function filtreraStartadeKorningar(
  alla: readonly KorningPost[],
  filter: { korningIds?: ReadonlySet<string>; mallar?: ReadonlySet<string> } | null | undefined,
): KorningPost[] {
  if (!filter) return [...alla];
  const ids = filter.korningIds;
  const mallar = filter.mallar;
  const harIds = Boolean(ids && ids.size > 0);
  const harMallar = Boolean(mallar && mallar.size > 0);
  if (!harIds && !harMallar) return [...alla];
  return alla.filter((post) => {
    if (harIds && ids) {
      const kid = explicitKorningId(post);
      if (kid !== null) return ids.has(kid);
      if (
        Array.isArray(post?.vyer) &&
        post.vyer.some((v) => typeof v?.korning_id === "string" && ids.has(v.korning_id))
      ) {
        return true;
      }
    }
    if (harMallar && mallar) return matcharMall(post, mallar);
    return true;
  });
}

function arPagaendeStatus(status: string | undefined): boolean {
  return status !== "klar" && status !== "fel";
}

function felTextForPost(post: KorningPost): string {
  const f = post.status.fel_forklaring ?? post.status.fel;
  return String(f ?? "okänt fel");
}

function formateraDelFel(felade: readonly KorningPost[], klara: readonly KorningPost[]): string {
  if (klara.length > 0) {
    const felDelar = felade.map((f) => `${mallEtikett(f.mall)} misslyckades: ${felTextForPost(f)}`).join(" · ");
    const klaraNamn = [...new Set(klara.map((c) => mallEtikett(c.mall)))].join(", ");
    return `${felDelar} (${klaraNamn} blev klar och visas på kartan.)`;
  }
  if (felade.length === 1) {
    const f = felade[0]!;
    return `Beräkningen misslyckades (${mallEtikett(f.mall)}): ${felTextForPost(f)}`;
  }
  return `Beräkningen misslyckades: ${felade.map((f) => `${mallEtikett(f.mall)}: ${felTextForPost(f)}`).join(" · ")}`;
}

/** Avsnitt (ämnen) som faktiskt blev klara med vyer i senaste avslutade körningen för projektet. */
export function senasteKlaraAvsnitt(projekt: string): readonly string[] {
  return klaraAvsnittPerProjekt.get(projekt) ?? [];
}

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
 * Tillämpar klara serverkörningar på projektets tillstånd genom att slå ihop nya vyer per `avsnitt`
 * med befintliga vyer för övriga ämnen (så att t.ex. Dagsljus inte raderar tidigare Skyfall-vyer).
 * Regn- och solsignaturer beräknas uteslutande ur serverns körningsdata (`regnSignaturFranKorningar` /
 * `solSignaturFranKorningar`), aldrig ur klientens aktuella inställningar (W-B9).
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
  if (klara.length === 0) return;
  const nyaVyer = klara.flatMap((x) => (Array.isArray(x.vyer) ? x.vyer : [])) as ProjektVy[];
  const uppdateradeAvsnitt = new Set<string>();
  for (const v of nyaVyer) {
    if (v?.avsnitt) uppdateradeAvsnitt.add(v.avsnitt);
  }
  for (const k of klara) {
    const avs = normaliseraMall(k.mall);
    if (avs === "vatten" || avs === "dagsljus" || avs === "ekologi" || avs === "luft") {
      uppdateradeAvsnitt.add(avs);
    }
  }
  const befintliga = lage ? t.lageVyer : t.ritVyer;
  const sammanslagna = [
    ...befintliga.filter((v) => !uppdateradeAvsnitt.has(v.avsnitt)),
    ...nyaVyer,
  ];
  const harVatten = uppdateradeAvsnitt.has("vatten");
  const harSol = uppdateradeAvsnitt.has("dagsljus");
  if (lage) {
    t.lageVyer = sammanslagna;
    if (harVatten) t.lageRegnSignatur = regnSignaturFranKorningar(klara);
    if (harSol) t.lageSolSignatur = solSignaturFranKorningar(klara);
  } else {
    t.ritVyer = sammanslagna;
    if (harVatten) t.ritRegnSignatur = regnSignaturFranKorningar(klara);
    if (harSol) t.ritSolSignatur = solSignaturFranKorningar(klara);
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
  const forraSlug = senasteRitSlug.get(t.projekt);
  if (forraSlug !== undefined && forraSlug !== sc.slug) {
    t.ritVyer = [];
    t.ritRegnSignatur = null;
    t.ritSolSignatur = null;
  }
  senasteRitSlug.set(t.projekt, sc.slug);
  stoppa(t.projekt);
  aktivaFilter.delete(t.projekt);
  klaraAvsnittPerProjekt.set(t.projekt, []);
  t.korning = { slug: sc.slug, text: "Beräknar … (0 s)", klar: false, fel: null, startadMs };
  signalera();
  const mallar = mallarFranAnalyser(analyser);
  const harVatten = mallar.has("vatten");
  const harSol = mallar.has("dagsljus");
  let svar: unknown;
  try {
    svar = await kApi.korScenario(
      t.projekt,
      sc.slug,
      analyser,
      harVatten ? t.avtappning : null,
      harVatten ? tillRegnParam(t.regn, t.skyfallMetod) : null,
      harSol ? tillSolParam(t.sol) : null,
      harVatten ? t.skyfallMetod : null,
    );
  } catch (e) {
    t.korning = { slug: sc.slug, text: "", klar: true, fel: e instanceof Error ? e.message : String(e), startadMs };
    signalera();
    return;
  }
  aktivaFilter.set(t.projekt, extraktStartatFilter(svar, analyser));
  stoppa(t.projekt);
  const intervall = typeof kApi.intervallMs === "number" && kApi.intervallMs > 0 ? kApi.intervallMs : 1200;
  timers.set(
    t.projekt,
    setInterval(() => void kolla(t, kApi), intervall),
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
  stoppa(t.projekt);
  aktivaFilter.delete(t.projekt);
  klaraAvsnittPerProjekt.set(t.projekt, []);
  t.korning = { slug: "", text: `Beräknar nuläge … (${formatera(0, 0)} s)`, klar: false, fel: null, startadMs, lage: true };
  signalera();
  const mallar = mallarFranAnalyser(analyser);
  const harVatten = mallar.has("vatten");
  const harSol = mallar.has("dagsljus");
  let svar: unknown;
  try {
    svar = await kApi.korLageIdag(
      t.projekt,
      analyser,
      harVatten ? t.avtappning : null,
      harVatten ? tillRegnParam(t.regn, t.skyfallMetod) : null,
      harSol ? tillSolParam(t.sol) : null,
      harVatten ? t.skyfallMetod : null,
    );
  } catch (e) {
    t.korning = { slug: "", text: "", klar: true, fel: e instanceof Error ? e.message : String(e), startadMs, lage: true };
    signalera();
    return;
  }
  aktivaFilter.set(t.projekt, extraktStartatFilter(svar, analyser));
  stoppa(t.projekt);
  const intervall = typeof kApi.intervallMs === "number" && kApi.intervallMs > 0 ? kApi.intervallMs : 1200;
  timers.set(
    t.projekt,
    setInterval(() => void kolla(t, kApi), intervall),
  );
  await kolla(t, kApi);
}

async function kolla(t: FlodeTillstand, api: KorningApi): Promise<void> {
  const k = t.korning;
  if (!k || k.klar) {
    stoppa(t.projekt);
    return;
  }
  if (pagaendeKoll.has(t.projekt)) return;
  pagaendeKoll.add(t.projekt);
  const filter = aktivaFilter.get(t.projekt);
  let signaleraEfter = false;
  try {
    const st = k.lage ? await api.lageIdagStatus(t.projekt) : await api.scenarioStatus(t.projekt, k.slug);
    if (t.korning !== k || k.klar) return;
    const relevanta = filtreraStartadeKorningar(st.korningar, filter);
    const pagaende = relevanta.filter((x) => arPagaendeStatus(x.status?.status));
    const felade = relevanta.filter((x) => x.status?.status === "fel");
    const klara = relevanta.filter((x) => x.status?.status === "klar");
    const vantarPaFler =
      relevanta.length === 0 ||
      Boolean(filter && filter.korningIds.size > 0 && relevanta.length < filter.korningIds.size);
    const s = Math.max(0, Math.round((Date.now() - k.startadMs) / 1000));
    if (pagaende.length > 0 || vantarPaFler) {
      const fr = relevanta
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
    } else {
      stoppa(t.projekt);
      aktivaFilter.delete(t.projekt);
      if (klara.length > 0) {
        tillampaKlaraKorningar(t, klara, Boolean(k.lage));
        const avsnitt = [
          ...new Set(klara.flatMap((x) => (Array.isArray(x.vyer) ? x.vyer.map((v) => v.avsnitt) : []))),
        ];
        klaraAvsnittPerProjekt.set(t.projekt, avsnitt);
      } else {
        klaraAvsnittPerProjekt.set(t.projekt, []);
      }
      if (felade.length > 0 && klara.length > 0) {
        t.korning = {
          ...k,
          klar: true,
          text: `Delvis klart på ${formatera(s, 0)} s.`,
          fel: formateraDelFel(felade, klara),
        };
      } else if (felade.length > 0) {
        t.korning = { ...k, klar: true, text: "", fel: formateraDelFel(felade, []) };
      } else {
        t.korning = { ...k, klar: true, fel: null, text: `Klart på ${formatera(s, 0)} s.` };
      }
    }
    signaleraEfter = true;
  } catch (e) {
    if (t.korning === k && !k.klar) {
      stoppa(t.projekt);
      aktivaFilter.delete(t.projekt);
      klaraAvsnittPerProjekt.set(t.projekt, []);
      t.korning = { ...k, klar: true, text: "", fel: e instanceof Error ? e.message : String(e) };
      signaleraEfter = true;
    }
  } finally {
    pagaendeKoll.delete(t.projekt);
  }
  if (signaleraEfter) signalera();
}
