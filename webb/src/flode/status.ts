/*
 * Frågornas statusetiketter: "Floden svarar", "Delvis" eller "Underlag saknas".
 * Källan är `/api/fragekatalog` (frågekatalogen med vad Floden kan belägga) om den finns, annars
 * `/api/fragor` (K20-frågorna). Tolkningen är tolerant: ett okänt eller saknat värde blir
 * "Underlag saknas", aldrig "Floden svarar".
 */
export type Niva = "svarar" | "delvis" | "saknas";

export const NIVA_TEXT: Record<Niva, string> = {
  svarar: "Floden svarar",
  delvis: "Delvis",
  saknas: "Underlag saknas",
};

export interface FlodeFraga {
  id: string;
  /** Ämnesnyckel: skyfall, dagsljus, luft, buller, ekologi. */
  amne: string;
  text: string;
  niva: Niva;
  /** Analysmallar som körs för frågan (K20-analysens doman), t.ex. dagsljus eller vatten. */
  doman: string[];
  /** Det som saknas, när katalogen anger det. */
  saknat?: string;
}

export const AMNEN: ReadonlyArray<{ id: string; namn: string }> = [
  { id: "skyfall", namn: "Skyfall" },
  { id: "dagsljus", namn: "Dagsljus och sol" },
  { id: "luft", namn: "Luft" },
  { id: "buller", namn: "Buller" },
  { id: "ekologi", namn: "Ekologi" },
];

export function etikett(n: Niva): string {
  return NIVA_TEXT[n];
}

/** Katalogens markering (`floden.markning` eller `floden.niva`) → nivå. */
export function nivaFran(floden: unknown): Niva {
  if (!floden || typeof floden !== "object") return "saknas";
  const f = floden as { markning?: unknown; niva?: unknown };
  const v = typeof f.markning === "string" ? f.markning : typeof f.niva === "string" ? f.niva : "";
  if (v === "visat" || v === "kan" || v === "svarar") return "svarar";
  if (v === "delvis") return "delvis";
  return "saknas";
}

/** Katalogens område → ämne (solstudier hör till Dagsljus och sol). */
export function amneFranOmrade(omrade: string): string {
  return omrade === "solstudier" ? "dagsljus" : omrade;
}

/** Ämnet som analysmall (domän) i beräkningarna; ämnen utan beräkning ger tom lista. */
export function domanFranAmne(amne: string): string[] {
  if (amne === "skyfall") return ["vatten"];
  if (amne === "dagsljus") return ["dagsljus"];
  if (amne === "ekologi") return ["ekologi"];
  return [];
}

/** Tolkar `GET /api/fragekatalog`; null när svaret inte har frågor. */
export function tolkaKatalog(data: unknown): FlodeFraga[] | null {
  const lista = (data as { fragor?: unknown } | null)?.fragor;
  if (!Array.isArray(lista)) return null;
  const ut: FlodeFraga[] = [];
  for (const p of lista) {
    if (!p || typeof p !== "object") continue;
    const o = p as Record<string, unknown>;
    if (typeof o.id !== "string" || typeof o.fraga !== "string") continue;
    const amne = amneFranOmrade(typeof o.omrade === "string" ? o.omrade : "");
    const fl = o.floden as { saknat?: unknown } | null | undefined;
    ut.push({
      id: o.id,
      amne,
      text: o.fraga,
      niva: nivaFran(o.floden),
      doman: domanFranAmne(amne),
      saknat: typeof fl?.saknat === "string" && fl.saknat ? fl.saknat : undefined,
    });
  }
  return ut;
}

interface K20Post {
  ref: string;
  beskrivning?: string;
  fraga_id?: string;
  giltig?: boolean;
  analyser?: Array<{ doman?: string | null }>;
  indata_saknas?: string[];
  scenarier?: unknown[];
}

/** Reserv: K20-frågorna ur `GET /api/fragor`. Ämnet följer frågans första analysdomän. */
export function franK20(poster: ReadonlyArray<K20Post>): FlodeFraga[] {
  return poster.map((f) => {
    const doman = [...new Set((f.analyser ?? []).map((a) => a.doman ?? "").filter(Boolean))];
    const amne = doman[0] === "vatten" ? "skyfall" : (doman[0] ?? "");
    const saknas = f.indata_saknas?.length ?? 0;
    const antal = f.scenarier?.length ?? 0;
    let niva: Niva = "saknas";
    if (f.giltig !== false && saknas === 0) niva = "svarar";
    else if (f.giltig !== false && antal > 0 && saknas < antal) niva = "delvis";
    return { id: f.ref, amne, text: f.beskrivning || f.fraga_id || f.ref, niva, doman };
  });
}

export function raknaPerAmne(fragor: ReadonlyArray<FlodeFraga>, amne: string): number {
  return fragor.filter((f) => f.amne === amne).length;
}
