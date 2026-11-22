/*
 * Ingång till 3D-vyn: skapar scenen i en yta och hämtar projektets rutnät. Den här filen och
 * Three.js laddas först när användaren öppnar 3D (dynamisk import), så att kartsidan inte blir
 * tyngre för den som inte använder 3D.
 */
import { Hamtare, type Vy3dInfo } from "./hamta.ts";
import { avstandFranMpp, SYNFALT_GRADER, type Kamera } from "./koord.ts";
import { Vy3d } from "./scen.ts";

export { Hamtare, Vy3d };
export type { Vy3dInfo };
export * from "./scen.ts";

export interface Start {
  /** Kartans mitt (SWEREF), meter per skärmpixel och kartytans höjd i pixlar. */
  e: number;
  n: number;
  mpp: number;
  hojdPx: number;
}

export async function skapaVy3d(yta: HTMLElement, projekt: string): Promise<{ hamtare: Hamtare; info: Vy3dInfo; skapa: (s: Start) => Vy3d }> {
  const hamtare = new Hamtare(projekt);
  const info = await hamtare.info();
  const skapa = (s: Start): Vy3d => {
    const z = (info.hojd.z_min + info.hojd.z_max) / 2;
    const k: Kamera = { mal: { e: s.e, n: s.n, z }, avstand: avstandFranMpp(s.mpp, s.hojdPx, SYNFALT_GRADER), azimut: 0, lutning: 0 };
    return new Vy3d(yta, hamtare, info, k);
  };
  return { hamtare, info, skapa };
}
