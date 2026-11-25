/*
 * Färgskalor i 3D: samma skalor som 2D-lagren (servern skickar dem som `teckenforklaring`),
 * så att en färg betyder samma värde i kartan och i 3D. Portat från src/webb/resultatlager.py
 * (`_sekventiell`, `_vatten`, `_skillnad`). Ett test håller de tre fallen mot servern.
 */
import type { Teckenforklaring } from "../fragor-typer.ts";

export type Rgba = [number, number, number, number];

export function hexTillRgb(hex: string): [number, number, number] {
  return [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
}

/** Färgen för ett värde enligt teckenförklaringen; alfa 0 där inget ska ritas. */
export function fargFor(tf: Teckenforklaring, v: number): Rgba {
  if (!Number.isFinite(v)) return [0, 0, 0, 0];
  if (tf.typ === "kontinuerlig") {
    const stopp = tf.stopp.map(hexTillRgb);
    const norm = tf.max <= tf.min ? 0 : (v - tf.min) / (tf.max - tf.min);
    const t = Math.min(1, Math.max(0, norm)) * (stopp.length - 1);
    const i0 = Math.min(Math.floor(t), stopp.length - 2);
    const f = t - i0;
    const a = stopp[i0] as [number, number, number];
    const b = stopp[i0 + 1] as [number, number, number];
    return [0, 1, 2].map((k) => Math.round(a[k] as number * (1 - f) + (b[k] as number) * f)).concat([255]) as Rgba;
  }
  if (tf.typ === "klasser") {
    let farg: string | null = null;
    for (const k of tf.klasser) if (v >= k.fran) farg = k.farg;
    const forsta = tf.klasser[0];
    if (farg === null || !forsta || v < forsta.fran) return [0, 0, 0, 0];
    return [...hexTillRgb(farg), 255] as Rgba;
  }
  if (tf.typ === "kategorier") {
    const k = tf.kategorier.find((x) => x.varde === Math.round(v));
    return k ? ([...hexTillRgb(k.farg), 255] as Rgba) : [0, 0, 0, 0];
  }
  if (!(Math.abs(v) > tf.troskel)) return [0, 0, 0, 0];
  const mag = tf.max > 0 ? Math.min(1, Math.abs(v) / tf.max) : 0;
  const m = mag * 0.75 + 0.25;
  const vit = [245, 245, 245];
  const mal = hexTillRgb(v < 0 ? tf.farg_minskning : tf.farg_okning);
  return [0, 1, 2].map((k) => Math.round((vit[k] as number) * (1 - m) + (mal[k] as number) * m)).concat([255]) as Rgba;
}

/** Skala för VSC (procent): 40 % är det mesta en lodrät fasad kan få under en jämn molnhimmel. */
export const VSC_TECKENFORKLARING: Teckenforklaring = {
  typ: "kontinuerlig",
  min: 0,
  max: 40,
  stopp: ["#00204d", "#31446b", "#666970", "#958f78", "#cbba69", "#ffea46"],
};

export function vscSkillnadsskala(storst: number): Teckenforklaring {
  return { typ: "divergerande", min: -storst, max: storst, troskel: 0.05, farg_minskning: "#2b5d8c", farg_okning: "#b4462b" };
}

/** Fyller en RGBA-textur (uint8) ur uppskalade cellvärden (uint16) med lagrets färgskala. */
export function fargaCeller(q: Uint16Array, lo: number, hi: number, tf: Teckenforklaring, ut: Uint8ClampedArray | Uint8Array): void {
  const span = hi > lo ? hi - lo : 1;
  // En uppslagstabell per körning vore snabbare, men 128 x 128 celler per ruta räcker utan.
  for (let i = 0; i < q.length; i++) {
    const v = q[i] === 65535 ? Number.NaN : lo + ((q[i] as number) / 65534) * span;
    const c = fargFor(tf, v);
    ut[i * 4] = c[0];
    ut[i * 4 + 1] = c[1];
    ut[i * 4 + 2] = c[2];
    ut[i * 4 + 3] = c[3];
  }
}

/**
 * Vertexfärger (0..1, samma RGB som `fargFor`, inte linjäriserade) för en punkt per värde. Punkt utan
 * värde ritas grå, aldrig som ett värde. Renderaren har färghantering av och utdata i linjärt läge,
 * så det som står här är exakt det som hamnar i bilden.
 */
export function punktFarger(tf: Teckenforklaring, varden: ArrayLike<number>): Float32Array {
  const ut = new Float32Array(varden.length * 3);
  for (let i = 0; i < varden.length; i++) {
    const v = varden[i] as number;
    const c = fargFor(tf, v);
    const rgb: [number, number, number] = c[3] === 0 ? (Number.isFinite(v) ? [235, 235, 235] : [154, 154, 154]) : [c[0], c[1], c[2]];
    ut[i * 3] = rgb[0] / 255;
    ut[i * 3 + 1] = rgb[1] / 255;
    ut[i * 3 + 2] = rgb[2] / 255;
  }
  return ut;
}
