/* Körs med `npm test`. Överfallsmarkörerna: exakt omräkning från EPSG:3011, samma text som panelen, lagerhantering. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { wgs84TillSweref } from "../sweref.ts";
import { LagerLista, type Lagring } from "./lager.ts";
import { OVERFALL_LAGER_ID, overfallBeskrivning, overfallLager, overfallTillGeoJson, ritadeOverfall, type OverfallPunkt } from "./overfall.ts";

const p = (nod_id: number, x: number, y: number, extra: Partial<OverfallPunkt> = {}): OverfallPunkt => ({
  nod_id,
  overfall_x: x,
  overfall_y: y,
  marginal_m: 0.0294,
  paverkad_volym_m3: 2308.8,
  instabil: true,
  ...extra,
});
const minne = (): Lagring => {
  const d = new Map<string, string>();
  return { getItem: (k) => d.get(k) ?? null, setItem: (k, v) => void d.set(k, v) };
};

test("punkterna räknas om exakt: tillbaka till SWEREF blir samma E och N inom en millimeter", () => {
  const fc = overfallTillGeoJson([p(5, 151345.5, 6580235.5), p(9, 151352.5, 6580222.5)]);
  assert.equal(fc.features.length, 2);
  for (const [i, [e, n]] of [[151345.5, 6580235.5], [151352.5, 6580222.5]].entries()) {
    const g = fc.features[i]?.geometry as GeoJSON.Point;
    const [lng, lat] = g.coordinates as [number, number];
    const [e2, n2] = wgs84TillSweref(lng, lat);
    assert.ok(Math.abs(e2 - (e as number)) < 1e-3 && Math.abs(n2 - (n as number)) < 1e-3, `${e2} ${n2}`);
    // Demokvarteret ligger vid Stockholm (E 151 km, N 6580 km i EPSG:3011): ungefär 18,02 grader ö, 59,34 grader n.
    assert.ok(lng > 18.0 && lng < 18.05 && lat > 59.33 && lat < 59.35, `${lng} ${lat}`);
  }
});

test("kartan ritar bara instabila punkter med ändliga koordinater", () => {
  const lista = [p(1, 151000, 6580000), p(2, 151001, 6580001, { instabil: false }), p(3, Number.NaN, 6580002), p(4, 151003, Number.POSITIVE_INFINITY)];
  assert.deepEqual(ritadeOverfall(lista).map((x) => x.nod_id), [1]);
  assert.equal(overfallTillGeoJson([]).features.length, 0);
});

test("hovring och klick visar samma värden som panelen listar", () => {
  const punkt = p(5, 151345.5, 6580235.5);
  const text = overfallBeskrivning(punkt);
  assert.equal(text, "Nod 5 (E 151346, N 6580236): marginal 0.029 m, berör 2309 m³");
  const f = overfallTillGeoJson([punkt]).features[0];
  assert.equal(f?.properties?.beskrivning, text);
  assert.equal(f?.properties?.nod_id, 5);
});

test("lagret kan tändas och släckas, sparas per projekt och rensas inte av nya resultat", () => {
  const m = minne();
  const a = new LagerLista("p", m);
  a.registrera(overfallLager(5));
  a.registrera({ id: "res:efter", grupp: "resultat", namn: "Efter", synlig: true, opacitet: 1 });
  assert.equal(a.hamta(OVERFALL_LAGER_ID)?.synlig, true);
  a.sattSynlig(OVERFALL_LAGER_ID, false);
  assert.equal(a.hamta(OVERFALL_LAGER_ID)?.synlig, false);
  a.taBortMed("resultat", "res:"); // som när ett nytt resultat läses in
  assert.ok(a.hamta(OVERFALL_LAGER_ID), "överfallslagret ligger kvar");
  assert.equal(a.hamta("res:efter"), undefined);
  assert.deepEqual(a.iGrupp("resultat").map((l) => l.id), [OVERFALL_LAGER_ID]);
  // Bara 2D: i 3D-panelen visas det inte.
  a.sattVisning("3d");
  assert.deepEqual(a.iGrupp("resultat"), []);
  // Ett val som sparats gäller när analysen körs igen.
  const b = new LagerLista("p", m);
  b.registrera(overfallLager(7));
  assert.equal(b.hamta(OVERFALL_LAGER_ID)?.synlig, false);
});
