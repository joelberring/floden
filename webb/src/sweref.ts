/*
 * SWEREF 99 18 00 (EPSG:3011) <-> WGS 84, Gauss–Krüger enligt Lantmäteriets formler
 * (transversell Mercator, GRS 80, mittmeridian 18°, k0 = 1, X0 = 150 000 m).
 * Används av kartmotorn (karta/kartmotor.ts) för att lägga allt som ritas, rasterbilder och
 * ritade hus, på rätt plats i kartan. Beräkningarna i Floden görs i EPSG:3011 (AGENTS.md), aldrig i
 * klienten. Kontrollerad mot pyproj i sweref.test.ts.
 */

const A = 6378137.0;
const F = 1 / 298.257222101;
const FALSK_N = 0.0;

export interface SwerefZon {
  readonly lon0Grader: number;
  readonly k0: number;
  readonly falskE: number;
}

/** Alla 13 officiella svenska SWEREF 99-zoner (EPSG:3006–3018). */
export const SWEREF99_ZONER: Readonly<Record<number, SwerefZon>> = {
  3006: { lon0Grader: 15.0, k0: 0.9996, falskE: 500000.0 },
  3007: { lon0Grader: 12.0, k0: 1.0, falskE: 150000.0 },
  3008: { lon0Grader: 13.5, k0: 1.0, falskE: 150000.0 },
  3009: { lon0Grader: 15.0, k0: 1.0, falskE: 150000.0 },
  3010: { lon0Grader: 16.5, k0: 1.0, falskE: 150000.0 },
  3011: { lon0Grader: 18.0, k0: 1.0, falskE: 150000.0 },
  3012: { lon0Grader: 14.25, k0: 1.0, falskE: 150000.0 },
  3013: { lon0Grader: 15.75, k0: 1.0, falskE: 150000.0 },
  3014: { lon0Grader: 17.25, k0: 1.0, falskE: 150000.0 },
  3015: { lon0Grader: 18.75, k0: 1.0, falskE: 150000.0 },
  3016: { lon0Grader: 20.25, k0: 1.0, falskE: 150000.0 },
  3017: { lon0Grader: 21.75, k0: 1.0, falskE: 150000.0 },
  3018: { lon0Grader: 23.25, k0: 1.0, falskE: 150000.0 },
};

function hamtaZon(epsg: number): SwerefZon {
  const zon = SWEREF99_ZONER[epsg];
  if (!zon) throw new Error(`Okänd SWEREF 99-zon: EPSG:${epsg}`);
  return zon;
}

const E2 = F * (2 - F);
const N = F / (2 - F);
const A_HAT = (A / (1 + N)) * (1 + (N * N) / 4 + (N * N * N * N) / 64);

const KA = E2;
const KB = (5 * E2 ** 2 - E2 ** 3) / 6;
const KC = (104 * E2 ** 3 - 45 * E2 ** 4) / 120;
const KD = (1237 * E2 ** 4) / 1260;
const BETA = [
  N / 2 - (2 * N ** 2) / 3 + (5 * N ** 3) / 16 + (41 * N ** 4) / 180,
  (13 * N ** 2) / 48 - (3 * N ** 3) / 5 + (557 * N ** 4) / 1440,
  (61 * N ** 3) / 240 - (103 * N ** 4) / 140,
  (49561 * N ** 4) / 161280,
];
const DELTA = [
  N / 2 - (2 * N ** 2) / 3 + (37 * N ** 3) / 96 - N ** 4 / 360,
  N ** 2 / 48 + N ** 3 / 15 - (437 * N ** 4) / 1440,
  (17 * N ** 3) / 480 - (37 * N ** 4) / 840,
  (4397 * N ** 4) / 161280,
];
const AS = E2 + E2 ** 2 + E2 ** 3 + E2 ** 4;
const BS = -(7 * E2 ** 2 + 17 * E2 ** 3 + 30 * E2 ** 4) / 6;
const CS = (224 * E2 ** 3 + 889 * E2 ** 4) / 120;
const DS = -(4279 * E2 ** 4) / 1260;

const GRAD = Math.PI / 180;

/** WGS 84 (lon, lat i grader) till valfri SWEREF 99-zon (standard EPSG:3011, E, N i meter). */
export function wgs84TillSweref(lonGrader: number, latGrader: number, epsg = 3011): [number, number] {
  const zon = hamtaZon(epsg);
  const lat = latGrader * GRAD;
  const dlon = (lonGrader - zon.lon0Grader) * GRAD;
  const s = Math.sin(lat);
  const latStjarna = lat - s * Math.cos(lat) * (KA + KB * s ** 2 + KC * s ** 4 + KD * s ** 6);
  const xi = Math.atan(Math.tan(latStjarna) / Math.cos(dlon));
  const eta = Math.atanh(Math.cos(latStjarna) * Math.sin(dlon));
  let x = xi;
  let y = eta;
  for (let j = 1; j <= 4; j++) {
    const b = BETA[j - 1] as number;
    x += b * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta);
    y += b * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta);
  }
  return [zon.k0 * A_HAT * y + zon.falskE, zon.k0 * A_HAT * x + FALSK_N];
}

/** Valfri SWEREF 99-zon (standard EPSG:3011, E, N i meter) till WGS 84 (lon, lat i grader). */
export function swerefTillWgs84(e: number, n: number, epsg = 3011): [number, number] {
  const zon = hamtaZon(epsg);
  const xi = (n - FALSK_N) / (zon.k0 * A_HAT);
  const eta = (e - zon.falskE) / (zon.k0 * A_HAT);
  let xiP = xi;
  let etaP = eta;
  for (let j = 1; j <= 4; j++) {
    const d = DELTA[j - 1] as number;
    xiP -= d * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta);
    etaP -= d * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta);
  }
  const latStjarna = Math.asin(Math.sin(xiP) / Math.cosh(etaP));
  const dlon = Math.atan(Math.sinh(etaP) / Math.cos(xiP));
  const s = Math.sin(latStjarna);
  const lat = latStjarna + s * Math.cos(latStjarna) * (AS + BS * s ** 2 + CS * s ** 4 + DS * s ** 6);
  return [zon.lon0Grader + dlon / GRAD, lat / GRAD];
}
