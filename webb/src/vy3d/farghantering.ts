/*
 * Färghantering för datafärger. Datafärger (soltimmar, VSC, vatten) är redan sRGB-värden ur
 * teckenförklaringen och ska hamna oförändrade i bilden. three.js (r152+) tolkar annars vertexfärger
 * som linjära och sRGB-kodar dem vid utdata, så mörka färger blir ljusa och urblekta. Därför: av.
 * Importen har den effekten, och renderaren sätts upp med `stallInRenderare`.
 */
import { ColorManagement, LinearSRGBColorSpace, NoToneMapping } from "three";

ColorManagement.enabled = false;

export function stallInRenderare(r: { outputColorSpace: string; toneMapping: number }): void {
  r.outputColorSpace = LinearSRGBColorSpace;
  r.toneMapping = NoToneMapping;
}
