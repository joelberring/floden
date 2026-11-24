/* Körs med `npm test`. Egenskaper per roll: fält med enheter, inskrivna värden, lutning ur hörnhöjder. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { andraFalt, faltFor, forvalEgenskaper, hojdlage, lutningFranHornhojder, tolkaTal } from "./egenskaper.ts";
import { byggFastIndex, fastAt } from "./snap.ts";

const ruta = [{ x: 0, y: 0, z: 10 }, { x: 10, y: 0, z: 10 }, { x: 10, y: 10, z: 11 }, { x: 0, y: 10 }];

test("tal tolkas med decimalkomma, mellanslag och minustecken", () => {
  assert.equal(tolkaTal("1,5"), 1.5);
  assert.equal(tolkaTal(" 1 234,25 "), 1234.25);
  assert.equal(tolkaTal("−0,5"), -0.5);
  assert.equal(tolkaTal("abc"), undefined);
  assert.equal(tolkaTal(""), undefined);
});

test("förval: mur får typ och höjd, öppning typ och bredd; ta bort-läge har ingen höjd", () => {
  assert.deepEqual(forvalEgenskaper("brytlinje"), { typ: "mur", hojd_m: 1 });
  assert.deepEqual(forvalEgenskaper("oppning"), { typ: "trumma", bredd_m: 1 });
  assert.deepEqual(forvalEgenskaper("brytlinje", { bort: true }), { bort: true, typ: "mur" });
});

test("fält per roll med enheter och formaterade värden", () => {
  const mur = faltFor("brytlinje", { typ: "mur", hojd_m: 1.25 });
  assert.deepEqual(mur.map((f) => [f.nyckel, f.enhet ?? ""]), [["bort", ""], ["typ", ""], ["hojd_m", "m"]]);
  assert.equal(mur[2]!.varde, "1,25");
  const bort = faltFor("brytlinje", { bort: true, tolerans_m: 0.5 });
  assert.deepEqual(bort.map((f) => f.nyckel), ["bort", "tolerans_m", "typ"]);
  const op = faltFor("oppning", { typ: "trumma", bredd_m: 2, botten_rh2000: 4.5 });
  assert.deepEqual(op.map((f) => [f.nyckel, f.enhet ?? ""]), [["typ", ""], ["bredd_m", "m"], ["botten_rh2000", "m RH 2000"]]);
  assert.equal(faltFor("vatten", {}).find((f) => f.nyckel === "volym_m3")!.enhet, "m³");
});

test("ett tal skrivs in och ändrar egenskapen; fel tal ger ett fel vid fältet", () => {
  const r = andraFalt("brytlinje", { typ: "mur", hojd_m: 1 }, "hojd_m", "1,8", { punkter: [] });
  assert.deepEqual(r, { patch: { hojd_m: 1.8 } });
  assert.ok("fel" in andraFalt("brytlinje", {}, "hojd_m", "hög", { punkter: [] }));
  assert.deepEqual(andraFalt("brytlinje", { hojd_m: 1 }, "hojd_m", "", { punkter: [] }), { patch: { hojd_m: null } });
  assert.deepEqual(andraFalt("brytlinje", { typ: "mur", hojd_m: 1 }, "bort", "ja", { punkter: [] }), { patch: { bort: true, hojd_m: null } });
});

test("mark: höjdläget byter och tar bort de andra formerna (exakt en får finnas)", () => {
  const s = { punkter: ruta };
  assert.equal(hojdlage({ niva_rh2000: 5 }), "niva");
  const a = andraFalt("mark", { niva_rh2000: 5 }, "hojdlage", "delta", s);
  assert.deepEqual(a, { patch: { niva_rh2000: null, delta_m: 0, lutning: null } });
  const b = andraFalt("mark", {}, "hojdlage", "fall", s);
  assert.ok("patch" in b);
  const lut = (b as { patch: { lutning: { punkt: number[]; fall: number } } }).patch.lutning;
  assert.deepEqual(lut.punkt, [5, 5, 10]);
  assert.equal(lut.fall, 0.02);
});

test("mark: fall anges i procent och sparas som m/m, riktning som azimut", () => {
  const e = { lutning: { punkt: [5, 5, 10], fall: 0.02, riktning_grader: 0 } };
  const r = andraFalt("mark", e, "fall_procent", "3,5", { punkter: ruta }) as { patch: { lutning: { fall: number; riktning_grader: number } } };
  assert.equal(r.patch.lutning.fall, 0.035);
  const f = faltFor("mark", e);
  assert.deepEqual(f.filter((x) => x.nyckel === "fall_procent" || x.nyckel === "riktning_grader").map((x) => [x.varde, x.enhet]), [["2", "%"], ["0", "grader"]]);
  const rik = andraFalt("mark", e, "riktning_grader", "-90", { punkter: ruta }) as { patch: { lutning: { riktning_grader: number } } };
  assert.equal(rik.patch.lutning.riktning_grader, 270);
});

test("mark: slänt som förhållande 1:n eller bredd", () => {
  const a = andraFalt("mark", { niva_rh2000: 5 }, "slant_typ", "forhallande", { punkter: ruta }) as { patch: { slant: Record<string, number> } };
  assert.deepEqual(a.patch.slant, { forhallande: 3 });
  const b = andraFalt("mark", { niva_rh2000: 5, slant: { forhallande: 3 } }, "slant_forhallande", "2", { punkter: ruta });
  assert.deepEqual(b, { patch: { slant: { forhallande: 2 } } });
  assert.deepEqual(andraFalt("mark", { slant: { bredd_m: 2 } }, "slant_typ", "", { punkter: ruta }), { patch: { slant: null } });
});

test("lutning ur hörnhöjder kräver tre hörn med höjd som inte ligger på en rak linje", () => {
  const r = lutningFranHornhojder(ruta);
  assert.deepEqual("lutning" in r && r.lutning.punkter, [[0, 0, 10], [10, 0, 10], [10, 10, 11]]);
  assert.ok("fel" in lutningFranHornhojder([ruta[0]!, ruta[1]!]));
  assert.ok("fel" in lutningFranHornhojder([{ x: 0, y: 0, z: 1 }, { x: 5, y: 0, z: 2 }, { x: 10, y: 0, z: 3 }]));
});

test("fästindex: en öppen linje har ingen sträcka från sista till första punkten", () => {
  const linje = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }];
  const oppen = byggFastIndex([{ id: "l", ringar: [linje], oppna: true }]);
  const sluten = byggFastIndex([{ id: "l", ringar: [linje] }]);
  assert.equal(fastAt(oppen, { punkt: { x: 5, y: 5.2 }, rackvidd: 0.5 }), undefined);
  assert.equal(fastAt(sluten, { punkt: { x: 5, y: 5.2 }, rackvidd: 0.5 })?.typ, "kant");
  assert.equal(oppen.strackor.length, 2);
});
