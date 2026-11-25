/*
 * Flödets tillstånd per projekt. Lever så länge sidan är öppen (så att ritningen finns kvar när man
 * går mellan stegen); det som ska överleva en omladdning (förslagets fil, valda frågor, beställningen)
 * sparas i sessionStorage. Ingen del av tillståndet är ett beräknat tal.
 */
import type { K9Andring } from "../karta/plandok.ts";
import { PlanDok } from "../karta/plandok.ts";
import type { ProjektInfo } from "../karta/kartapi.ts";
import type { RitlagerDok } from "../karta/ritlager.ts";
import type { ProjektVy } from "../types.ts";
import { lasRegnVal, STANDARD_REGNVAL, type RegnVal } from "./regnval.ts";
import { lasSolVal, STANDARD_SOLVAL, type SolVal } from "./solval.ts";

export type Skede = "skiss" | "planunderlag";
export const MOTTAGARE: ReadonlyArray<{ id: string; namn: string }> = [
  { id: "planhandlaggare", namn: "Planhandläggare" },
  { id: "byggaktor", namn: "Byggaktör" },
  { id: "granskare", namn: "Granskare" },
];

export const TAKFORMER: ReadonlyArray<{ id: string; namn: string }> = [
  { id: "brutet", namn: "Brutet tak" },
  { id: "platt", namn: "Platt tak" },
  { id: "sadel", namn: "Sadeltak" },
  { id: "pulpet", namn: "Pulpettak" },
  { id: "mansard", namn: "Mansardtak" },
];
/** Förvald takform skickas inte alls: okänd takform räknas som brutet tak (docs/VATTEN.md). */
export const FORVALD_TAKFORM = "brutet";

export interface Bestallning {
  skede: Skede;
  mottagare: string;
  syfte: string;
}

export interface KorningLage {
  slug: string;
  text: string;
  klar: boolean;
  fel: string | null;
  startadMs: number;
  /** Sant när körningen gäller läget idag (inget förslag). */
  lage?: boolean;
}

export interface FlodeTillstand {
  projekt: string;
  info: ProjektInfo | null;
  dok: PlanDok;
  /** Takform per nytt hus (id → takform); bara avvikelser från förvalet. */
  takform: Map<string, string>;
  scenario: { slug: string; namn: string } | null;
  namn: string;
  valda: Set<string> | null;
  bestallning: Bestallning;
  /** Avtappning till ledningsnätet i mm/h; null = lösarens standard 0 mm/h (ledningsnätet antas fullt). */
  avtappning: number | null;
  /** Vald regninställning (Standard, 10-årsregn, 100-årsregn, Extremregn eller Eget). */
  regn: RegnVal;
  /** Vald solinställning (datum, brukstid, sammanhängande soltimmar, VSC). */
  sol: SolVal;
  /** Regn+avtappningssignatur för senaste klara körning av läget idag respektive förslag. */
  lageRegnSignatur: string | null;
  ritRegnSignatur: string | null;
  korning: KorningLage | null;
  /** Vyer ur körningen av det ritade förslaget. */
  ritVyer: ProjektVy[];
  /** Vyer ur körningen av läget idag (bara före, U5a). */
  lageVyer: ProjektVy[];
}

const alla = new Map<string, FlodeTillstand>();
const NYCKEL = "floden.flode.v1.";

interface Sparat {
  scenario: { slug: string; namn: string } | null;
  namn: string;
  valda: string[] | null;
  bestallning: Bestallning;
  avtappning?: number | null;
  regn?: RegnVal;
  sol?: SolVal;
}

export const FORVAL_BESTALLNING: Bestallning = {
  skede: "skiss",
  mottagare: "planhandlaggare",
  syfte: "Tidig sållning av frågorna för förslaget.",
};

export function tillstandFor(projekt: string): FlodeTillstand {
  let t = alla.get(projekt);
  if (t) return t;
  t = {
    projekt,
    info: null,
    dok: new PlanDok(),
    takform: new Map(),
    scenario: null,
    namn: "",
    valda: null,
    bestallning: { ...FORVAL_BESTALLNING },
    avtappning: null,
    regn: { ...STANDARD_REGNVAL },
    sol: { ...STANDARD_SOLVAL },
    lageRegnSignatur: null,
    ritRegnSignatur: null,
    korning: null,
    ritVyer: [],
    lageVyer: [],
  };
  try {
    const s = JSON.parse(sessionStorage.getItem(NYCKEL + projekt) ?? "null") as Sparat | null;
    if (s) {
      t.scenario = s.scenario;
      t.namn = s.namn;
      t.valda = s.valda ? new Set(s.valda) : null;
      t.bestallning = { ...FORVAL_BESTALLNING, ...s.bestallning };
      t.avtappning = typeof s.avtappning === "number" && Number.isFinite(s.avtappning) && s.avtappning >= 0 ? s.avtappning : null;
      t.regn = lasRegnVal(s.regn);
      t.sol = lasSolVal(s.sol);
    }
  } catch {
    /* Går det inte att läsa börjar man om; inget går förlorat på servern. */
  }
  alla.set(projekt, t);
  return t;
}

export function sparaTillstand(t: FlodeTillstand): void {
  try {
    const s: Sparat = {
      scenario: t.scenario,
      namn: t.namn,
      valda: t.valda ? [...t.valda] : null,
      bestallning: t.bestallning,
      avtappning: t.avtappning,
      regn: t.regn,
      sol: t.sol,
    };
    sessionStorage.setItem(NYCKEL + t.projekt, JSON.stringify(s));
  } catch {
    /* sessionStorage kan vara avstängt. */
  }
}

/** Förslagets K9-ändringar med taktyp på de nya husen som avviker från förvalet. */
export function andringarMedTak(dok: PlanDok, takform: ReadonlyMap<string, string>): K9Andring[] {
  return dok.tillAndringar().map((a) => {
    const tak = takform.get(a.andring_id);
    if (a.typ !== "byggnad_ny" || !tak || tak === FORVALD_TAKFORM) return a;
    return { ...a, egenskaper: { ...a.egenskaper, takform: tak } };
  });
}

/** Det som skickas till servern vid kontroll och sparande: fria K9-ändringar och ritlagren (K50) med objekten. */
export interface ForslagKropp {
  andringar: K9Andring[];
  ritlager: RitlagerDok;
}

export function forslagKropp(dok: PlanDok, takform: ReadonlyMap<string, string>): ForslagKropp {
  return {
    andringar: dok.tillFriaAndringar(),
    ritlager: dok.tillRitlager((id) => {
      const tak = takform.get(id);
      return tak && tak !== FORVALD_TAKFORM ? { takform: tak } : {};
    }),
  };
}

/** Tar upp takformerna ur ett laddat scenario. */
export function takformFran(andringar: ReadonlyArray<K9Andring>): Map<string, string> {
  const m = new Map<string, string>();
  for (const a of andringar) {
    const tf = a.egenskaper.takform;
    if (a.typ === "byggnad_ny" && typeof tf === "string" && tf !== FORVALD_TAKFORM) m.set(a.andring_id, tf);
  }
  return m;
}

export function skapaDokFor(t: FlodeTillstand, info: ProjektInfo): void {
  t.info = info;
  const bas = new Map(info.byggnader.map((b) => [b.id, b.hojd_m]));
  const gammal = t.dok;
  const dok = new PlanDok((id) => bas.get(id));
  if (!gammal.tom() || gammal.lager.length > 0) dok.taOver(gammal);
  t.dok = dok;
}

/** Avtappningen (mm/h) som ett tal ≥ 0; komma eller punkt accepteras och tom text betyder standard (null). */
export function lasAvtappning(text: string): { varde: number | null; ok: boolean } {
  const rent = text.trim().replace(",", ".");
  if (rent === "") return { varde: null, ok: true };
  const n = Number(rent);
  return Number.isFinite(n) && n >= 0 ? { varde: n, ok: true } : { varde: null, ok: false };
}
