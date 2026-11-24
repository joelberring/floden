/*
 * Egenskaper för ett ritat objekt efter lagrets roll (K50): vilka fält som finns, med enheter, hur ett
 * inskrivet värde blir en ändring av egenskaperna, och hur höjd på hörnen blir en lutande yta (K9
 * `markhojd.lutning`). Ren logik utan webbläsare; panelen i flode/forslag.ts ritar fälten. Allt
 * som visas som tal går genom tal.ts. Servern kontrollerar värdena (karna.ritlager) och avgör vad objektet
 * blir i beräkningen; klienten räknar ingenting av det.
 */
import { formateraHogst } from "../tal.ts";
import type { Punkt } from "./geom.ts";
import type { P3 } from "./linje.ts";
import type { Roll } from "./ritlager.ts";

export type Egenskaper = Record<string, unknown>;

export interface Falt {
  /** Egenskapens nyckel, eller ett klientfält (`hojdlage`, `fall_procent`, `slant_typ` ...). */
  nyckel: string;
  etikett: string;
  slag: "tal" | "val" | "ja_nej";
  /** Enhet som skrivs efter fältet (m, m RH 2000, %, grader, m³). */
  enhet?: string;
  /** Värdet som text (tal med svenskt decimalkomma). Tom när inget är angivet. */
  varde: string;
  alternativ?: ReadonlyArray<{ varde: string; etikett: string }>;
  hjalp?: string;
}

/** Ändring av egenskaperna: `null` tar bort en nyckel. */
export type Patch = Record<string, unknown>;

export interface FigurSamband {
  /** Figurens punkter (för hörnhöjder och tyngdpunkt). */
  punkter: readonly P3[];
}

export const BRYTLINJE_TYPER: ReadonlyArray<{ varde: string; etikett: string }> = [
  { varde: "mur", etikett: "Mur" },
  { varde: "stodmur", etikett: "Stödmur" },
  { varde: "kantsten", etikett: "Kantsten" },
  { varde: "staket", etikett: "Staket" },
  { varde: "bankkant", etikett: "Bankkant" },
];

export const OPPNING_TYPER: ReadonlyArray<{ varde: string; etikett: string }> = [
  { varde: "trumma", etikett: "Trumma" },
  { varde: "port", etikett: "Port" },
  { varde: "passage", etikett: "Passage" },
];

export const HOJDLAGEN: ReadonlyArray<{ varde: string; etikett: string }> = [
  { varde: "niva", etikett: "Ny nivå (m RH 2000)" },
  { varde: "delta", etikett: "Höj eller sänk (m)" },
  { varde: "fall", etikett: "Lutning (fall och riktning)" },
  { varde: "hornhojder", etikett: "Lutning ur hörnens höjder" },
  { varde: "ab", etikett: "Lutning mellan två punkter" },
  { varde: "hojdlinjer", etikett: "Lutning mellan två höjdlinjer" },
];

/** Tolkar ett inskrivet tal (decimalkomma eller punkt, mellanslag som tusentalsavskiljare). */
export function tolkaTal(text: string): number | undefined {
  const t = text.replace(/[\s  ]/g, "").replace(",", ".").replace("−", "-");
  if (t === "" || !/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(t)) return undefined;
  const v = Number(t);
  return Number.isFinite(v) ? v : undefined;
}

const tal = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const visa = (v: unknown, d = 2): string => {
  const t = tal(v);
  return t === undefined ? "" : formateraHogst(t, d);
};
const obj = (v: unknown): Record<string, unknown> | undefined => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined);
const punkt3 = (v: unknown): [number, number, number] | undefined => {
  if (!Array.isArray(v) || v.length < 3) return undefined;
  const [x, y, z] = v as unknown[];
  return typeof x === "number" && typeof y === "number" && typeof z === "number" ? [x, y, z] : undefined;
};

/** Tyngdpunkten av punkterna (medelvärde), som referens för en lutande yta. */
export function tyngdpunkt(punkter: readonly Punkt[]): Punkt {
  if (punkter.length === 0) return { x: 0, y: 0 };
  let x = 0;
  let y = 0;
  for (const p of punkter) {
    x += p.x;
    y += p.y;
  }
  return { x: Math.round((x / punkter.length) * 100) / 100, y: Math.round((y / punkter.length) * 100) / 100 };
}

/** Vilket höjdläge en markyta har, ur egenskaperna. */
export function hojdlage(e: Egenskaper): "niva" | "delta" | "fall" | "hornhojder" | "ab" | "hojdlinjer" | undefined {
  if (tal(e["niva_rh2000"]) !== undefined) return "niva";
  if (tal(e["delta_m"]) !== undefined) return "delta";
  const l = obj(e["lutning"]);
  if (l) {
    if ("punkter" in l) return "hornhojder";
    if ("a" in l) return "ab";
    if ("hojdlinje_a" in l) return "hojdlinjer";
    return "fall";
  }
  return undefined;
}

const SLANT = [
  { varde: "", etikett: "Ingen slänt" },
  { varde: "forhallande", etikett: "Förhållande 1:n" },
  { varde: "bredd", etikett: "Bredd (m)" },
];

const MARKSLAG_VEG = [
  { varde: "", etikett: "Ingen ändring av markslag" },
  { varde: "4", etikett: "Gräs" },
  { varde: "5", etikett: "Skog" },
];

/** Förvalda egenskaper för ett nytt objekt i ett lager: lagrets förval, och för brytlinje och öppning typ. */
export function forvalEgenskaper(roll: Roll, lagerForval: Egenskaper = {}): Egenskaper {
  const e: Egenskaper = { ...lagerForval };
  if (roll === "brytlinje") {
    e["typ"] ??= "mur";
    if (!e["bort"]) e["hojd_m"] ??= 1;
  }
  if (roll === "oppning") {
    e["typ"] ??= "trumma";
    e["bredd_m"] ??= 1;
  }
  return e;
}

/** Fälten som panelen visar för ett objekt med de här egenskaperna. */
export function faltFor(roll: Roll, e: Egenskaper): Falt[] {
  switch (roll) {
    case "mark":
      return markFalt(e);
    case "hardgjort":
      return [{ nyckel: "niva_rh2000", etikett: "Nivå", slag: "tal", enhet: "m RH 2000", varde: visa(e["niva_rh2000"]), hjalp: "Hårdgjord yta; nivån ger ytans markhöjd." }];
    case "vegetation":
      return [
        { nyckel: "hojd_m", etikett: "Växtlighetens höjd", slag: "tal", enhet: "m", varde: visa(e["hojd_m"]) },
        { nyckel: "markslag", etikett: "Markslag", slag: "val", varde: visa(e["markslag"], 0), alternativ: MARKSLAG_VEG },
      ];
    case "vatten":
      return [
        { nyckel: "volym_m3", etikett: "Magasinets volym", slag: "tal", enhet: "m³", varde: visa(e["volym_m3"]), hjalp: "Tomt: en vattenyta. Med volym: ett magasin." },
        { nyckel: "botten_rh2000", etikett: "Botten", slag: "tal", enhet: "m RH 2000", varde: visa(e["botten_rh2000"]) },
      ];
    case "brytlinje": {
      const bort = e["bort"] === true;
      const f: Falt[] = [{ nyckel: "bort", etikett: "Ta bort befintliga brytlinjer längs linjen", slag: "ja_nej", varde: bort ? "ja" : "nej" }];
      if (bort) {
        f.push({ nyckel: "tolerans_m", etikett: "Tolerans", slag: "tal", enhet: "m", varde: visa(e["tolerans_m"]), hjalp: "Hur nära linjen en befintlig brytlinje ska ligga för att tas bort (förval 0,5 m, högst 5 m)." });
        f.push({ nyckel: "typ", etikett: "Bara typen", slag: "val", varde: typText(e["typ"]), alternativ: [{ varde: "", etikett: "Alla typer" }, ...BRYTLINJE_TYPER] });
      } else {
        f.push({ nyckel: "typ", etikett: "Typ", slag: "val", varde: typText(e["typ"]), alternativ: BRYTLINJE_TYPER });
        f.push({ nyckel: "hojd_m", etikett: "Höjd över marken", slag: "tal", enhet: "m", varde: visa(e["hojd_m"]), hjalp: "Vattnet stannar vid linjen tills det når höjden." });
      }
      return f;
    }
    case "oppning":
      return [
        { nyckel: "typ", etikett: "Typ", slag: "val", varde: typText(e["typ"]), alternativ: OPPNING_TYPER },
        { nyckel: "bredd_m", etikett: "Bredd", slag: "tal", enhet: "m", varde: visa(e["bredd_m"]), hjalp: "Murar klipps bort på den här bredden så att vattnet kommer igenom." },
        { nyckel: "botten_rh2000", etikett: "Botten", slag: "tal", enhet: "m RH 2000", varde: visa(e["botten_rh2000"]), hjalp: "Marken sänks till den här nivån längs öppningen." },
      ];
    default:
      return [];
  }
}

const typText = (v: unknown): string => (typeof v === "string" ? v : "");

function markFalt(e: Egenskaper): Falt[] {
  const lage = hojdlage(e);
  // Utan angivet läge visas ett tomt val; annars visade listan "Ny nivå" utan att något fält fanns (och ett val av den gav ingen ändring).
  const alternativ = lage === undefined ? [{ varde: "", etikett: "Välj hur ytan får sin höjd" }, ...HOJDLAGEN] : HOJDLAGEN;
  const f: Falt[] = [{ nyckel: "hojdlage", etikett: "Höjd", slag: "val", varde: lage ?? "", alternativ }];
  const l = obj(e["lutning"]);
  if (lage === "niva") f.push({ nyckel: "niva_rh2000", etikett: "Nivå", slag: "tal", enhet: "m RH 2000", varde: visa(e["niva_rh2000"]) });
  if (lage === "delta") f.push({ nyckel: "delta_m", etikett: "Höjning (negativ för sänkning)", slag: "tal", enhet: "m", varde: visa(e["delta_m"]) });
  if (lage === "fall") {
    const p = punkt3(l?.["punkt"]);
    const fall = tal(l?.["fall"]);
    f.push({ nyckel: "punkt_z", etikett: "Höjd i ytans mitt", slag: "tal", enhet: "m RH 2000", varde: visa(p?.[2]) });
    f.push({ nyckel: "fall_procent", etikett: "Fall", slag: "tal", enhet: "%", varde: fall === undefined ? "" : visa(fall * 100), hjalp: "Positivt fall sjunker i riktningen." });
    f.push({ nyckel: "riktning_grader", etikett: "Riktning (0 = norr, medurs)", slag: "tal", enhet: "grader", varde: visa(l?.["riktning_grader"] ?? 0) });
  }
  if (lage === "hornhojder") f.push({ nyckel: "hornhojder_info", etikett: "Hörnens höjder", slag: "val", varde: "", hjalp: "Markera ett hörn och ange dess höjd; minst tre hörn som inte ligger på en rak linje." });
  const s = obj(e["slant"]);
  const slag = s ? ("forhallande" in s ? "forhallande" : "bredd") : "";
  f.push({ nyckel: "slant_typ", etikett: "Slänt mot omgivande mark", slag: "val", varde: slag, alternativ: SLANT });
  if (slag === "forhallande") f.push({ nyckel: "slant_forhallande", etikett: "Förhållande 1:", slag: "tal", varde: visa(s?.["forhallande"]) });
  if (slag === "bredd") f.push({ nyckel: "slant_bredd_m", etikett: "Släntens bredd", slag: "tal", enhet: "m", varde: visa(s?.["bredd_m"]) });
  return f;
}

/** Svar på en ändring: antingen ändringar av egenskaperna eller ett fel som visas vid fältet. */
export type Andring = { patch: Patch } | { fel: string };

const fel = (text: string): Andring => ({ fel: text });

/**
 * Vad ett inskrivet värde blir för egenskaperna. `text` är fältets text (tal med komma eller punkt, eller
 * valets värde); ett tomt tal tar bort egenskapen. Intervall kontrolleras av servern, förutom det som
 * klienten behöver för att ändringen ska ha en mening.
 */
export function andraFalt(roll: Roll, e: Egenskaper, nyckel: string, text: string, samband: FigurSamband): Andring {
  if (roll === "mark") {
    const r = andraMark(e, nyckel, text, samband);
    if (r) return r;
  }
  if (nyckel === "bort") return { patch: { bort: text === "ja" ? true : null, ...(text === "ja" ? { hojd_m: null } : {}) } };
  if (nyckel === "typ" || nyckel === "markslag") {
    if (text === "") return { patch: { [nyckel]: null } };
    return { patch: { [nyckel]: nyckel === "markslag" ? Number(text) : text } };
  }
  const f = faltFor(roll, e).find((x) => x.nyckel === nyckel);
  if (!f || f.slag !== "tal") return fel("Okänt fält.");
  if (text.trim() === "") return { patch: { [nyckel]: null } };
  const v = tolkaTal(text);
  if (v === undefined) return fel("Skriv ett tal, till exempel 1,5.");
  return { patch: { [nyckel]: v } };
}

function andraMark(e: Egenskaper, nyckel: string, text: string, samband: FigurSamband): Andring | undefined {
  const l = obj(e["lutning"]);
  const rensa: Patch = { niva_rh2000: null, delta_m: null, lutning: null };
  const mitt = tyngdpunkt(samband.punkter);
  const v = tolkaTal(text);
  switch (nyckel) {
    case "hojdlage": {
      const z = samband.punkter.find((p) => p.z !== undefined)?.z ?? 0;
      if (text === "niva") return { patch: { ...rensa, niva_rh2000: tal(e["niva_rh2000"]) ?? z } };
      if (text === "delta") return { patch: { ...rensa, delta_m: tal(e["delta_m"]) ?? 0 } };
      if (text === "fall") return { patch: { ...rensa, lutning: { punkt: [mitt.x, mitt.y, z], fall: 0.02, riktning_grader: 0 } } };
      if (text === "hornhojder") {
        const r = lutningFranHornhojder(samband.punkter);
        return "fel" in r ? { patch: { ...rensa } } : { patch: { ...rensa, lutning: r.lutning } };
      }
      if (text === "ab") {
        const [a, b] = [samband.punkter[0], samband.punkter[samband.punkter.length - 1]];
        if (!a || !b) return fel("Ytan saknar punkter.");
        return { patch: { ...rensa, lutning: { a: [a.x, a.y, a.z ?? 0], b: [b.x, b.y, b.z ?? 0] } } };
      }
      if (text === "hojdlinjer") return fel("Höjdlinjer anges i förslagets lutning (hojdlinje_a och hojdlinje_b, K9) eller via API:t; ritverktyget visar dem men skapar dem inte än.");
      return { patch: { ...rensa } };
    }
    case "punkt_z": {
      if (v === undefined) return fel("Skriv höjden som ett tal, till exempel 12,5.");
      const p = punkt3(l?.["punkt"]);
      return { patch: { lutning: { punkt: [p?.[0] ?? mitt.x, p?.[1] ?? mitt.y, v], fall: tal(l?.["fall"]) ?? 0.02, riktning_grader: tal(l?.["riktning_grader"]) ?? 0 } } };
    }
    case "fall_procent": {
      if (v === undefined) return fel("Skriv fallet i procent, till exempel 2.");
      const p = punkt3(l?.["punkt"]);
      return { patch: { lutning: { punkt: [p?.[0] ?? mitt.x, p?.[1] ?? mitt.y, p?.[2] ?? 0], fall: v / 100, riktning_grader: tal(l?.["riktning_grader"]) ?? 0 } } };
    }
    case "riktning_grader": {
      if (v === undefined) return fel("Skriv riktningen i grader, till exempel 180.");
      const p = punkt3(l?.["punkt"]);
      return { patch: { lutning: { punkt: [p?.[0] ?? mitt.x, p?.[1] ?? mitt.y, p?.[2] ?? 0], fall: tal(l?.["fall"]) ?? 0, riktning_grader: ((v % 360) + 360) % 360 } } };
    }
    case "slant_typ": {
      if (text === "") return { patch: { slant: null } };
      const s = obj(e["slant"]);
      const markslag = tal(s?.["markslag"]);
      const ms = markslag === undefined ? {} : { markslag };
      return { patch: { slant: text === "forhallande" ? { forhallande: tal(s?.["forhallande"]) ?? 3, ...ms } : { bredd_m: tal(s?.["bredd_m"]) ?? 2, ...ms } } };
    }
    case "slant_forhallande":
    case "slant_bredd_m": {
      if (v === undefined || v <= 0) return fel("Skriv ett tal över 0.");
      const s = obj(e["slant"]);
      const markslag = tal(s?.["markslag"]);
      return { patch: { slant: { [nyckel === "slant_forhallande" ? "forhallande" : "bredd_m"]: v, ...(markslag === undefined ? {} : { markslag }) } } };
    }
    default:
      return undefined;
  }
}

/**
 * Lutande yta ur hörnens höjder (K9 `lutning.punkter`): kräver minst tre hörn med höjd (`z`) som inte ligger
 * på en rak linje. Höjderna i m RH 2000.
 */
export function lutningFranHornhojder(punkter: readonly P3[]): { lutning: { punkter: Array<[number, number, number]> } } | { fel: string } {
  const med = punkter.filter((p): p is P3 & { z: number } => p.z !== undefined);
  if (med.length < 3) return { fel: "Ange höjd på minst tre hörn (markera ett hörn och skriv höjden)." };
  const [a, ...rest] = med as [P3, ...P3[]];
  const rak = rest.every((p) => Math.abs((p.x - a.x) * ((rest[0] as P3).y - a.y) - (p.y - a.y) * ((rest[0] as P3).x - a.x)) < 1e-6);
  if (rak) return { fel: "Hörnen med höjd ligger på en rak linje; ytans lutning blir obestämd." };
  return { lutning: { punkter: med.map((p) => [p.x, p.y, p.z]) } };
}

/** Kort rad om vad objektet är (för listan och panelens rubrik). */
export function etikettFor(roll: Roll, e: Egenskaper, typ: "linje" | "yta"): string {
  if (roll === "brytlinje") return e["bort"] === true ? "Ta bort brytlinje" : (BRYTLINJE_TYPER.find((t) => t.varde === e["typ"])?.etikett ?? "Mur eller kant");
  if (roll === "oppning") return OPPNING_TYPER.find((t) => t.varde === e["typ"])?.etikett ?? "Öppning";
  return typ === "linje" ? "Linje" : "Yta";
}
