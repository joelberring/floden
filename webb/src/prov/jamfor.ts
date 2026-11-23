/*
 * Jämförelse av ett fält från klientens GPU (f32) mot serverns facit (f64 lagrat som f32).
 * Rena funktioner, ingen GPU: går att testa med `node --test`.
 *
 * Fel = |GPU − facit| i fältets egen enhet. Ett icke-ändligt GPU-värde (NaN, ±oändligt) räknas
 * som över toleransen och redovisas separat; det får aldrig försvinna i ett medelvärde.
 */

export interface FaltJamforelse {
  /** Antal jämförda celler. */
  antal: number;
  /** Största absoluta fel. Oändligt om någon GPU-cell inte är ett ändligt tal. */
  storstaFel: number;
  /** Index (platt) för största felet, eller -1 om inget fel. */
  storstaFelIndex: number;
  /** Medelvärdet av det absoluta felet över de celler där GPU-värdet är ändligt. */
  medelFel: number;
  /** Andel av alla celler där felet är större än toleransen (0–1). Icke-ändliga räknas hit. */
  andelOverTolerans: number;
  antalOverTolerans: number;
  /** Antal GPU-celler som inte är ändliga tal. */
  antalIckeAndliga: number;
  tolerans: number;
}

export class JamforFel extends Error {
  constructor(text: string) {
    super(text);
    this.name = "JamforFel";
  }
}

export function jamforFalt(
  facit: ArrayLike<number>,
  gpu: ArrayLike<number>,
  tolerans: number,
): FaltJamforelse {
  if (facit.length !== gpu.length) {
    throw new JamforFel(`Facit har ${facit.length} celler men GPU-resultatet ${gpu.length}.`);
  }
  if (facit.length === 0) throw new JamforFel("Fälten är tomma.");
  if (!Number.isFinite(tolerans) || tolerans < 0) {
    throw new JamforFel(`Toleransen måste vara ett tal >= 0, fick ${tolerans}.`);
  }
  let storsta = 0;
  let storstaIndex = -1;
  let summa = 0;
  let antalAndliga = 0;
  let over = 0;
  let ickeAndliga = 0;
  for (let i = 0; i < facit.length; i += 1) {
    const f = facit[i] as number;
    const g = gpu[i] as number;
    if (!Number.isFinite(f)) throw new JamforFel(`Facit har ett icke-ändligt värde i cell ${i}.`);
    if (!Number.isFinite(g)) {
      ickeAndliga += 1;
      over += 1;
      storsta = Number.POSITIVE_INFINITY;
      if (storstaIndex < 0) storstaIndex = i;
      continue;
    }
    const fel = Math.abs(g - f);
    summa += fel;
    antalAndliga += 1;
    if (fel > tolerans) over += 1;
    if (fel > storsta) {
      storsta = fel;
      storstaIndex = i;
    }
  }
  return {
    antal: facit.length,
    storstaFel: storsta,
    storstaFelIndex: storstaIndex,
    medelFel: antalAndliga > 0 ? summa / antalAndliga : Number.NaN,
    andelOverTolerans: over / facit.length,
    antalOverTolerans: over,
    antalIckeAndliga: ickeAndliga,
    tolerans,
  };
}

/** Median av tider (valfri enhet). Kastar vid tom lista eller icke-ändliga tal. */
export function median(tider: readonly number[]): number {
  if (tider.length === 0) throw new JamforFel("Ingen tid att ta median av.");
  if (tider.some((t) => !Number.isFinite(t))) throw new JamforFel("Tiderna måste vara ändliga tal.");
  const s = [...tider].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? (s[m] as number) : ((s[m - 1] as number) + (s[m] as number)) / 2;
}

/** Avkodar fält som servern skickar som base64 (little endian, K37 provfall). */
export function avkodaFalt(d: { dtyp: string; form: number[]; base64: string }): Float32Array | Uint8Array {
  const bin = atob(d.base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  const antal = d.form.reduce((a, b) => a * b, 1);
  if (d.dtyp === "float32") {
    if (bytes.length !== antal * 4) throw new JamforFel("Fältets längd stämmer inte med formen.");
    return new Float32Array(bytes.buffer, 0, antal);
  }
  if (d.dtyp === "uint8") {
    if (bytes.length !== antal) throw new JamforFel("Fältets längd stämmer inte med formen.");
    return bytes;
  }
  throw new JamforFel(`Okänd dtyp: ${d.dtyp}`);
}
