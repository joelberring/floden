/*
 * Koordinater och kamerasynk mellan 2D-kartan (MapLibre, Web Mercator) och 3D-vyn.
 *
 * 3D-scenen ligger i meter relativt projektets origo (E0, N0, Z0): x = öster, y = upp, z = söder
 * (så att norr är "bakåt" i bild när man tittar norrut). Alla värden är SWEREF 99 18 00 / RH 2000;
 * ingenting räknas om till annat system.
 *
 * Synk: kartan visar `meterPerPx` meter per skärmpixel i mitten. En perspektivkamera som tittar
 * rakt ner på samma punkt ser samma bredd om avståndet är d = (höjd_px / 2) · mpp / tan(fov / 2).
 * Det omvända ger kartans zoom ur kamerans avstånd. Lutning ändrar inte avståndet till
 * måltavlan, så en lutad vy ger samma mpp i skärmmitten.
 */

export interface Origo {
  e: number;
  n: number;
  z: number;
}

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export const tillScen = (e: number, n: number, z: number, o: Origo): Vec3 => ({ x: e - o.e, y: z - o.z, z: -(n - o.n) });
export const franScen = (p: Vec3, o: Origo): { e: number; n: number; z: number } => ({ e: p.x + o.e, n: -p.z + o.n, z: p.y + o.z });

export const VERKLIG_RADIE = 6378137;
const OMKRETS = 2 * Math.PI * VERKLIG_RADIE;
export const KARTRUTA_PX = 512;

/** Kamerans synfält (lodrätt, grader). Synken mellan kartan och 3D bygger på samma värde. */
export const SYNFALT_GRADER = 40;

/** Meter per skärmpixel vid en MapLibre-zoom på bredden `latGrader`. */
export function meterPerPxFranZoom(zoom: number, latGrader: number): number {
  return (OMKRETS * Math.cos((latGrader * Math.PI) / 180)) / (KARTRUTA_PX * 2 ** zoom);
}

export function zoomFranMeterPerPx(mpp: number, latGrader: number): number {
  return Math.log2((OMKRETS * Math.cos((latGrader * Math.PI) / 180)) / (KARTRUTA_PX * mpp));
}

export interface Kamera {
  /** Måltavla (EN, och höjd) som kameran kretsar kring. */
  mal: { e: number; n: number; z: number };
  /** Avstånd från kameran till måltavlan (m). */
  avstand: number;
  /** Kompassriktning kameran tittar mot, grader (0 = norrut, uppåt i kartan). */
  azimut: number;
  /** Lutning från lodrätt, grader (0 = rakt uppifrån, 90 = horisontellt). */
  lutning: number;
}

export function avstandFranMpp(mpp: number, hojdPx: number, fovGrader: number): number {
  return (hojdPx / 2) * mpp / Math.tan((fovGrader * Math.PI) / 360);
}

export function mppFranAvstand(avstand: number, hojdPx: number, fovGrader: number): number {
  return (avstand * Math.tan((fovGrader * Math.PI) / 360)) / (hojdPx / 2);
}

/** Kamera som ser samma ruta som kartan: rakt uppifrån, norr uppåt. */
export function kameraFranKarta(mitt: { e: number; n: number; z: number }, mpp: number, hojdPx: number, fovGrader: number): Kamera {
  return { mal: { ...mitt }, avstand: avstandFranMpp(mpp, hojdPx, fovGrader), azimut: 0, lutning: 0 };
}

/** Kartans meter per pixel som motsvarar kamerans avstånd (för att sätta kartans zoom vid växling tillbaka). */
export function mppFranKamera(k: Kamera, hojdPx: number, fovGrader: number): number {
  return mppFranAvstand(k.avstand, hojdPx, fovGrader);
}

/** Kamerans position i scenen ur måltavla, avstånd, azimut och lutning. */
export function kameraPosition(k: Kamera, o: Origo): Vec3 {
  const l = (k.lutning * Math.PI) / 180;
  const a = (k.azimut * Math.PI) / 180;
  const mal = tillScen(k.mal.e, k.mal.n, k.mal.z, o);
  // Kameran ligger på motsatt sida av blickriktningen: tittar den norrut (azimut 0) ligger den söder om målet.
  const horisont = k.avstand * Math.sin(l);
  return {
    x: mal.x - horisont * Math.sin(a),
    y: mal.y + k.avstand * Math.cos(l),
    z: mal.z + horisont * Math.cos(a),
  };
}

/** Azimut (kompass) och lutning (från lodrätt) för en kamera i position `p` som tittar mot `mal` (scenkoordinater). */
export function kameraVinklar(p: Vec3, mal: Vec3): { azimut: number; lutning: number; avstand: number } {
  const dx = mal.x - p.x;
  const dy = mal.y - p.y;
  const dz = mal.z - p.z;
  const avstand = Math.hypot(dx, dy, dz);
  const horisont = Math.hypot(dx, dz);
  const lutning = (Math.atan2(horisont, -dy) * 180) / Math.PI;
  // Blickriktningens kompassvinkel: öster (+x) är 90, norr (-z) är 0.
  let azimut = (Math.atan2(dx, -dz) * 180) / Math.PI;
  if (horisont < 1e-9) azimut = 0;
  return { azimut: (azimut + 360) % 360, lutning, avstand };
}
