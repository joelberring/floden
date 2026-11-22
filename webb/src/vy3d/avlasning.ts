/*
 * Avläsning i 3D: var pekaren träffar marken, och vad värdet där är. Marken träffas genom att
 * följa strålen över höjdfältet (inga miljontals trianglar att testa). Värdena läses av servern
 * med samma anrop som i 2D (`/api/korningar/<id>/avlasning`); fasadpunkter bär sina värden själva.
 */
import type { Origo, Vec3 } from "./koord.ts";
import { franScen } from "./koord.ts";

export interface Traff {
  e: number;
  n: number;
  z: number;
  avstand: number;
}

/**
 * Första punkten där strålen går under terrängen. `hojd(e, n)` ger RH 2000 (NaN = ingen terräng),
 * `ursprung` och `riktning` är i scenkoordinater (riktning normaliserad). Stegar med `steg` m och
 * förfinar träffen med halvering.
 */
export function straleMotHojdfalt(ursprung: Vec3, riktning: Vec3, hojd: (e: number, n: number) => number, o: Origo, maxAvstand = 5000, steg = 2): Traff | null {
  const punkt = (t: number): { e: number; n: number; z: number; under: boolean } => {
    const p = franScen({ x: ursprung.x + riktning.x * t, y: ursprung.y + riktning.y * t, z: ursprung.z + riktning.z * t }, o);
    const h = hojd(p.e, p.n);
    return { ...p, under: Number.isFinite(h) && p.z <= h };
  };
  let forra = 0;
  for (let t = steg; t <= maxAvstand; t += steg) {
    const p = punkt(t);
    if (p.under) {
      let lo = forra;
      let hi = t;
      for (let i = 0; i < 24; i++) {
        const mitt = (lo + hi) / 2;
        if (punkt(mitt).under) hi = mitt;
        else lo = mitt;
      }
      const s = punkt(hi);
      return { e: s.e, n: s.n, z: s.z, avstand: hi };
    }
    forra = t;
  }
  return null;
}

const TAL = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });

export interface FasadVarden {
  vsc: number | null;
  fore: number | null;
  skillnad: number | null;
  vaning: number;
  byggnad: string;
}

/** Text för en fasadpunkt: värdena som lösaren räknat, i klarspråk. */
export function fasadText(f: FasadVarden, lage: "fore" | "efter" | "skillnad" | "annat"): string {
  const pct = (v: number | null): string => (v === null ? "saknas" : `${TAL.format(v)} %`);
  const delar = [`Fasadpunkt, våning ${f.vaning}, byggnad ${f.byggnad.slice(0, 8)}…`];
  if (lage === "skillnad") {
    delar.push(`Dagsljus (VSC): efter ${pct(f.vsc)}, före ${pct(f.fore)}`);
    delar.push(f.skillnad === null ? "Skillnad: punkten finns inte före (ny våning eller nytt hus)" : `Skillnad: ${f.skillnad > 0 ? "+" : ""}${TAL.format(f.skillnad)} procentenheter`);
  } else {
    delar.push(`Dagsljus (VSC): ${pct(f.vsc)}`);
  }
  return delar.join(". ");
}

export function byggnadText(id: string, hojd: number, status: number): string {
  const s = ["", " (nytt hus)", " (höjt hus)", " (rivs)"][status] ?? "";
  return `Byggnad ${id.slice(0, 8)}…${s}, ${TAL.format(hojd)} m hög. Tak: inget markvärde.`;
}
