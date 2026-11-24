/*
 * Tangentbindningar för ritverktygen. Tangenterna ligger under vänster hand och går att byta; en tangent
 * skrivs som den står på tangentbordet ("Q", "§"), med "Shift+" framför för varianten. Bokstäverna läses
 * ur tangentens plats (`event.code`), så att de är desamma oavsett tangentbordets språk. Ctrl/Cmd+Z och
 * Ctrl/Cmd+Skift+Z gäller alltid och går inte att byta.
 *
 * Logik porterad från O-Tid (MIT, © 2026 Joel Berring, joelberring/otid @ b0b952f,
 * apps/web/src/lib/map-project-keys.ts: `bindingOf`, `commandFor`, `withBinding`, `parseKeys`,
 * `serializeKeys`, `keyLabel`, reserverade tangenter). Skillnader: Flodens kommandon (hus, linje, yta,
 * klipp, ...) och förval enligt `docs/design/RITVERKTYG.md`; sparas i webbläsaren under en egen nyckel.
 */
export const KOMMANDON = [
  "valj",
  "hus",
  "linje",
  "yta",
  "laggtill",
  "tabort",
  "klipp",
  "ihop",
  "vand",
  "forenkla",
  "foljkant",
  "fast",
  "ratVinkel",
  "angra",
  "goraOm",
] as const;
export type Kommando = (typeof KOMMANDON)[number];
export type Tangentkarta = Readonly<Record<Kommando, string>>;

/** Förvalet: Q markera, W rita hus, E linje, Skift+W yta, A/S lägg till/ta bort punkt, X klipp, G slå ihop, V vänd, F följ kant, T fäst, Z ångra. */
export const FORVAL: Tangentkarta = {
  valj: "Q",
  hus: "W",
  linje: "E",
  yta: "Shift+W",
  laggtill: "A",
  tabort: "S",
  klipp: "X",
  ihop: "G",
  vand: "V",
  forenkla: "Shift+S",
  foljkant: "F",
  fast: "T",
  ratVinkel: "R",
  angra: "Z",
  goraOm: "Shift+Z",
};

export const LAGRINGSNYCKEL = "floden-ritverktyg-tangenter";

/** Tangenter som redan betyder något annat och inte går att välja. */
const RESERVERADE = new Set(["Enter", "Escape", "Delete", "Backspace", "Tab", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]);

export const arReserverad = (bindning: string): boolean => RESERVERADE.has(bindning.replace(/^Shift\+/, ""));

export interface TangentHandelse {
  key: string;
  code: string;
  shiftKey: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
}

/** Tryckningen som en tangent i kartan, eller inget för en ren Skift/Alt och för tryck med Ctrl/Cmd. */
export function bindningAv(h: TangentHandelse): string | undefined {
  if (h.ctrlKey || h.metaKey || h.altKey) return undefined;
  const bokstav = /^Key([A-Z])$/.exec(h.code)?.[1];
  const bas = bokstav ?? (h.code === "Space" || h.key === " " ? "Space" : [...h.key].length === 1 ? h.key.toUpperCase() : h.key);
  if (!bas || ["Shift", "Alt", "Control", "Meta", "Dead", "CapsLock"].includes(bas)) return undefined;
  return h.shiftKey ? `Shift+${bas}` : bas;
}

export function kommandoFor(karta: Tangentkarta, bindning: string | undefined): Kommando | undefined {
  return bindning ? KOMMANDON.find((k) => karta[k] === bindning) : undefined;
}

/** Ger ett kommando en ny tangent. Hade ett annat kommando den står det utan tangent efteråt (`bortbytt`). */
export function medBindning(karta: Tangentkarta, kommando: Kommando, bindning: string): { karta: Tangentkarta; bortbytt?: Kommando } {
  if (arReserverad(bindning)) return { karta };
  const bortbytt = KOMMANDON.find((k) => k !== kommando && karta[k] === bindning);
  return { karta: { ...karta, ...(bortbytt ? { [bortbytt]: "" } : {}), [kommando]: bindning }, ...(bortbytt ? { bortbytt } : {}) };
}

/** Den sparade uppsättningen: okända kommandon och annat än text hoppas över; det som saknas får förvalet. */
export function lasTangenter(sparad: string | null): Tangentkarta {
  let v: unknown;
  try {
    v = sparad ? JSON.parse(sparad) : undefined;
  } catch {
    v = undefined;
  }
  if (!v || typeof v !== "object") return FORVAL;
  const rad = v as Record<string, unknown>;
  const egna = new Map<Kommando, string>();
  for (const k of KOMMANDON) {
    const b = rad[k];
    if (typeof b !== "string" || b.length > 20 || arReserverad(b)) continue;
    if (b !== FORVAL[k]) egna.set(k, b);
  }
  if (egna.size === 0) return FORVAL;
  const ut: Record<Kommando, string> = { ...FORVAL };
  const sedda = new Set<string>();
  for (const [k, b] of egna) {
    ut[k] = b && sedda.has(b) ? "" : b;
    if (b) sedda.add(b);
  }
  for (const k of KOMMANDON) {
    if (egna.has(k)) continue;
    if (sedda.has(ut[k])) ut[k] = "";
    else sedda.add(ut[k]);
  }
  return ut;
}

/** Det som sparas: bara det som skiljer sig från förvalet, så att ett nytt förval når den som inte valt själv. */
export function sparaTangenter(karta: Tangentkarta): string {
  const egna: Partial<Record<Kommando, string>> = {};
  for (const k of KOMMANDON) if (karta[k] !== FORVAL[k]) egna[k] = karta[k];
  return JSON.stringify(egna);
}

/** Tangenten som den visas: "⇧W". */
export const tangentEtikett = (bindning: string): string => bindning.replace(/^Shift\+/, "⇧");
