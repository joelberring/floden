/*
 * SWEREF 99 18 00 (EPSG:3011) <-> WGS 84, Gauss–Krüger enligt Lantmäteriets formler
 * (transversell Mercator, GRS 80, mittmeridian 18°, k0 = 1, X0 = 150 000 m).
 * Används bara för den valfria bakgrundskartan: rastren ritas i SWEREF-koordinater och
 * beräkningarna i Floden görs i EPSG:3011 (AGENTS.md), aldrig i klienten.
 */

const A = 6378137.0;
const F = 1 / 298.257222101;
const LON0 = (18.0 * Math.PI) / 180;
const K0 = 1.0;
const FALSK_E = 150000.0;
const FALSK_N = 0.0;

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

/** WGS 84 (lon, lat i grader) till SWEREF 99 18 00 (E, N i meter). */
export function wgs84TillSweref(lonGrader: number, latGrader: number): [number, number] {
  const lat = latGrader * GRAD;
  const dlon = lonGrader * GRAD - LON0;
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
  return [K0 * A_HAT * y + FALSK_E, K0 * A_HAT * x + FALSK_N];
}

/** SWEREF 99 18 00 (E, N i meter) till WGS 84 (lon, lat i grader). */
export function swerefTillWgs84(e: number, n: number): [number, number] {
  const xi = (n - FALSK_N) / (K0 * A_HAT);
  const eta = (e - FALSK_E) / (K0 * A_HAT);
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
  return [(LON0 + dlon) / GRAD, lat / GRAD];
}
