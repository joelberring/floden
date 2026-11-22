/*
 * Läser binärformatet `floden-3d/v1` (se src/webb/vy3d_format.py): "FL3D", rubrikens längd (uint32),
 * rubriken som JSON, därefter delarna som råa talmatriser på 8-byte-gränser. Delarna blir typade
 * vyer över samma buffert (ingen kopiering).
 */

export type Talmatris = Float32Array | Uint8Array | Uint16Array | Uint32Array | Int16Array;

export interface Behallare<H = Record<string, unknown>> {
  huvud: H & { format: string; typ: string; delar: Array<{ namn: string; dtype: string; form: number[]; forskjutning: number }> };
  delar: Record<string, Talmatris>;
}

export class FormatFel extends Error {}

const DTYP: Record<string, { ny: (b: ArrayBuffer, off: number, n: number) => Talmatris; bytes: number }> = {
  f4: { ny: (b, o, n) => new Float32Array(b, o, n), bytes: 4 },
  u1: { ny: (b, o, n) => new Uint8Array(b, o, n), bytes: 1 },
  u2: { ny: (b, o, n) => new Uint16Array(b, o, n), bytes: 2 },
  u4: { ny: (b, o, n) => new Uint32Array(b, o, n), bytes: 4 },
  i2: { ny: (b, o, n) => new Int16Array(b, o, n), bytes: 2 },
};

export function lasBehallare<H = Record<string, unknown>>(buf: ArrayBuffer): Behallare<H> {
  if (buf.byteLength < 8) throw new FormatFel("Svaret är för kort för att vara en 3D-behållare.");
  const dv = new DataView(buf);
  if (dv.getUint8(0) !== 0x46 || dv.getUint8(1) !== 0x4c || dv.getUint8(2) !== 0x33 || dv.getUint8(3) !== 0x44) {
    throw new FormatFel("Inte en floden-3d-behållare (saknar FL3D).");
  }
  const n = dv.getUint32(4, true);
  if (8 + n > buf.byteLength) throw new FormatFel("Rubriken är längre än svaret.");
  const huvud = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, n))) as Behallare<H>["huvud"];
  if (huvud.format !== "floden-3d/v1") throw new FormatFel(`Okänt format ${huvud.format}.`);
  const start = 8 + n;
  const delar: Record<string, Talmatris> = {};
  for (const d of huvud.delar) {
    const typ = DTYP[d.dtype];
    if (!typ) throw new FormatFel(`Okänd dtype ${d.dtype}.`);
    const antal = d.form.length ? d.form.reduce((a, b) => a * b, 1) : 1;
    const off = start + d.forskjutning;
    if (off % typ.bytes !== 0) throw new FormatFel(`Delen ${d.namn} ligger fel justerad.`);
    if (off + antal * typ.bytes > buf.byteLength) throw new FormatFel(`Delen ${d.namn} ligger utanför svaret.`);
    delar[d.namn] = typ.ny(buf, off, antal);
  }
  return { huvud, delar };
}
