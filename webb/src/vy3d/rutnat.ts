/*
 * Rutnätet och detaljnivåerna (LOD), samma regler som servern (src/webb/vy3d.py):
 * nivå n har cellstorlek 1·2^n m, en ruta är 128 × 128 celler, och (nivå, tx, ty) pekar ut rutan
 * med väst-syd-hörnet i (tx, ty)·128·cell (EPSG:3011). Här väljs vilka rutor som ska hämtas för en
 * kameraposition: nära kameran fina rutor, långt bort grova.
 */

export const CELLER = 128;
export const CELL0_M = 1;
export const KVANT = 65534;
export const INGET_VARDE = 65535;

export interface Rut {
  lod: number;
  tx: number;
  ty: number;
}

export interface Utbr {
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
}

export const cellstorlek = (lod: number): number => CELL0_M * 2 ** lod;
export const rutstorlek = (lod: number): number => cellstorlek(lod) * CELLER;
export const rutNyckel = (r: Rut): string => `${r.lod}/${r.tx}/${r.ty}`;

export function rutHorn(r: Rut): { x0: number; y0: number; cell: number } {
  const c = cellstorlek(r.lod);
  return { x0: r.tx * c * CELLER, y0: r.ty * c * CELLER, cell: c };
}

export function rutUtbr(r: Rut): Utbr {
  const { x0, y0 } = rutHorn(r);
  const s = rutstorlek(r.lod);
  return { x_min: x0, y_min: y0, x_max: x0 + s, y_max: y0 + s };
}

export function rutorI(u: Utbr, lod: number): Rut[] {
  const s = rutstorlek(lod);
  const tx0 = Math.floor(u.x_min / s);
  const tx1 = Math.floor((u.x_max - 1e-9) / s);
  const ty0 = Math.floor(u.y_min / s);
  const ty1 = Math.floor((u.y_max - 1e-9) / s);
  const ut: Rut[] = [];
  for (let ty = ty0; ty <= ty1; ty++) for (let tx = tx0; tx <= tx1; tx++) ut.push({ lod, tx, ty });
  return ut;
}

export function maxLod(u: Utbr): number {
  const sida = Math.max(u.x_max - u.x_min, u.y_max - u.y_min, 1);
  return Math.max(0, Math.min(12, Math.ceil(Math.log2(sida / (CELLER * CELL0_M)))));
}

const rorUtbr = (a: Utbr, b: Utbr): boolean => a.x_min < b.x_max && a.x_max > b.x_min && a.y_min < b.y_max && a.y_max > b.y_min;

export interface Kamerapunkt {
  /** Kamerans läge i E, N och höjd över projektets nollnivå (m). */
  e: number;
  n: number;
  z: number;
}

/**
 * Väljer rutor för en kamera: börjar på grövsta nivån och delar en ruta i fyra om kameran är
 * närmare än `delaFaktor` gånger rutans sida. Resultatet täcker utbredningen exakt en gång
 * (ingen punkt i två rutor), och antalet begränsas av `max` genom att den fjärran rutan
 * delas sist (närmast kameran först).
 */
export function valjRutor(u: Utbr, kam: Kamerapunkt, delaFaktor = 0.6, max = 120): Rut[] {
  let aktiva: Rut[] = rutorI(u, maxLod(u)).filter((r) => rorUtbr(rutUtbr(r), u));
  const avstand = (r: Rut): number => {
    const b = rutUtbr(r);
    const dx = Math.max(b.x_min - kam.e, 0, kam.e - b.x_max);
    const dy = Math.max(b.y_min - kam.n, 0, kam.n - b.y_max);
    return Math.hypot(dx, dy, kam.z);
  };
  for (;;) {
    const kandidater = aktiva
      .filter((r) => r.lod > 0 && avstand(r) < delaFaktor * rutstorlek(r.lod))
      .sort((a, b) => avstand(a) - avstand(b));
    const fran = kandidater[0];
    if (!fran) break;
    const barn: Rut[] = [];
    for (const dy of [0, 1]) for (const dx of [0, 1]) barn.push({ lod: fran.lod - 1, tx: fran.tx * 2 + dx, ty: fran.ty * 2 + dy });
    const nya = barn.filter((r) => rorUtbr(rutUtbr(r), u));
    if (aktiva.length - 1 + nya.length > max) break;
    aktiva = [...aktiva.filter((r) => r !== fran), ...nya];
  }
  return aktiva.sort((a, b) => avstand(a) - avstand(b));
}

/** Dekodar en uppskalad uint16 till värde; 65535 är "inget värde". */
export function avkvantisera(q: number, lo: number, hi: number): number {
  if (q === INGET_VARDE) return Number.NaN;
  const span = hi > lo ? hi - lo : 1;
  return lo + (q / KVANT) * span;
}
