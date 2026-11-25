/* Körs med `npm test`. Analyslager: roller per ämne, före/efter/skillnad, färgskalor och vektorlager. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { LagerPost } from "../fragor-typer.ts";
import type { ProjektVy } from "../types.ts";
import {
  amneForDoman, djupFarg, flodesvagBeskrivning, forvaldNyckel, gransPos, gransVarde, GRANSER, lagerVal, tillrinningSkala, flodesBredd, PALETTER, rannstenBeskrivning, rannstenSkala, rollEtikett, sankaBeskrivning, skalFarg, valText, varFarg, vektorScenario, vektorTeckenHtml, vektorTillKarta, vektorUrl, vektorVal, visaHtml, vyMedVal, type VektorSvar,
} from "./analyslager.ts";

const lp = (id: string, analys: string, roll: string, scenario: string, typ: LagerPost["typ"], extra: Partial<LagerPost> = {}): LagerPost =>
  ({ id, analys, roll, scenario, typ, etikett: roll, enhet: "", doman: "dagsljus", primar: false, kontroll: false, harledd: false, ...extra }) as LagerPost;

const LAGER: LagerPost[] = [
  lp("sol.bas.soltimmar", "sol", "soltimmar", "bas", "bas", { etikett: "Soltimmar på marken", enhet: "h", primar: true }),
  lp("sol.hojd.soltimmar", "sol", "soltimmar", "hojd", "scenario", { etikett: "Soltimmar på marken", enhet: "h", primar: true }),
  lp("sol.nytt.soltimmar", "sol", "soltimmar", "nytt", "scenario", { etikett: "Soltimmar på marken", enhet: "h", primar: true }),
  lp("skillnad.sol_hojd", "sol", "soltimmar", "hojd", "skillnad", { fore: "bas", efter: "hojd" }),
  lp("sol.bas.svf", "sol", "svf", "bas", "bas", { etikett: "Andel himmel som syns" }),
  lp("sol.hojd.svf", "sol", "svf", "hojd", "scenario", { etikett: "Andel himmel som syns" }),
  lp("skillnad.sol.svf.hojd", "sol", "svf", "hojd", "skillnad", { fore: "bas", efter: "hojd", harledd: true }),
  lp("sol.bas.trunkering", "sol", "trunkering", "bas", "bas", { etikett: "Kontroll: avskuren horisont", kontroll: true }),
  lp("sol.hojd.trunkering", "sol", "trunkering", "hojd", "scenario", { etikett: "Kontroll: avskuren horisont", kontroll: true }),
  lp("vatten.fore.max_djup", "skyfall", "max_djup", "fore", "bas", { doman: "vatten", etikett: "Största vattendjup", enhet: "m", primar: true }),
];
const VY = { fore_lager: "sol.bas.soltimmar", efter_lager: "sol.hojd.soltimmar", skillnad_lager: "skillnad.sol_hojd" } as ProjektVy;

test("domän blir ämne; geometri hör till skyfall och okänt ger inget ämne", () => {
  assert.equal(amneForDoman("vatten"), "vatten");
  assert.equal(amneForDoman("geometri"), "vatten");
  assert.equal(amneForDoman("dagsljus"), "dagsljus");
  assert.equal(amneForDoman("luft"), "luft");
  assert.equal(amneForDoman("buller"), "buller");
  assert.equal(amneForDoman("okant"), null);
  assert.equal(amneForDoman(undefined), null);
});

test("rollerna i ett ämne: mått före kontroller, frågans egen roll först, bara den förändring vyn visar", () => {
  const v = lagerVal(LAGER, VY, "dagsljus");
  assert.deepEqual(v.map((x) => x.roll), ["soltimmar", "svf", "trunkering"]);
  const sol = v[0];
  assert.equal(sol?.fore, "sol.bas.soltimmar");
  assert.equal(sol?.efter, "sol.hojd.soltimmar");
  assert.equal(sol?.skillnad, "skillnad.sol_hojd");
  assert.equal(sol?.harledd, false);
  const svf = v[1];
  assert.equal(svf?.skillnad, "skillnad.sol.svf.hojd");
  assert.equal(svf?.harledd, true);
  assert.equal(v[2]?.kontroll, true);
  assert.equal(v[2]?.skillnad, null);
});

test("en annan förändring ger andra lager; ämnen hålls isär", () => {
  const nytt = { ...VY, efter_lager: "sol.nytt.soltimmar", skillnad_lager: "x" } as ProjektVy;
  const v = lagerVal(LAGER, nytt, "dagsljus");
  assert.equal(v[0]?.efter, "sol.nytt.soltimmar");
  assert.equal(v[0]?.skillnad, null); // ingen jämförelse bas-nytt i den här listan
  assert.deepEqual(lagerVal(LAGER, { fore_lager: "vatten.fore.max_djup", efter_lager: null }, "vatten").map((x) => x.roll), ["max_djup"]);
  assert.deepEqual(lagerVal(LAGER, VY, "luft"), []);
});

test("vyn med vald roll byter lager men inte körning; läget idag får bara före", () => {
  const svf = lagerVal(LAGER, VY, "dagsljus")[1] ?? null;
  const ny = vyMedVal({ ...VY, id: "v", korning_id: "k" } as ProjektVy, svf);
  assert.equal(ny.fore_lager, "sol.bas.svf");
  assert.equal(ny.efter_lager, "sol.hojd.svf");
  assert.equal(ny.skillnad_lager, "skillnad.sol.svf.hojd");
  assert.equal(ny.korning_id, "k");
  const kontroll = lagerVal(LAGER, VY, "dagsljus")[2] ?? null;
  assert.equal(vyMedVal(VY, kontroll).skillnad_lager, "sol.bas.trunkering"); // ingen skillnad: före används
  const idag = vyMedVal({ ...VY, lage_idag: true } as ProjektVy, svf);
  assert.equal(idag.efter_lager, null);
  assert.equal(idag.skillnad_lager, "sol.bas.svf");
  assert.equal(vyMedVal(VY, null), VY);
});

test("förvald roll är den vars lager vyn redan visar", () => {
  assert.equal(forvaldNyckel(lagerVal(LAGER, VY, "dagsljus"), VY), "sol|soltimmar");
});

test("texten under väljaren säger när skillnaden saknas, är härledd eller lagret är en kontroll", () => {
  const [sol, svf, kontroll] = lagerVal(LAGER, VY, "dagsljus");
  assert.match(valText(svf!, "skillnad", false), /efter minus före/);
  assert.match(valText(kontroll!, "fore", false), /inte ett mått/);
  assert.match(valText({ ...sol!, skillnad: null }, "skillnad", false), /ingen skillnad/);
  assert.equal(valText(sol!, "fore", false), "Soltimmar på marken (h).");
});

test("väljaren visas bara när det finns något att välja och escapar texten", () => {
  const v = lagerVal(LAGER, VY, "dagsljus");
  const html = visaHtml(v, "sol|soltimmar", vektorVal("dagsljus", [{ losare: "dagsljus.vsc_fasad" }]), new Set(["vsc"]), "Text <b>");
  assert.match(html, /<optgroup label="Mått">/);
  assert.match(html, /<optgroup label="Kontroller">/);
  assert.match(html, /<option value="sol\|soltimmar" selected>/);
  assert.match(html, /data-vektor="vsc" checked/);
  assert.match(html, /Text &lt;b&gt;/);
  assert.equal(visaHtml(v.slice(0, 1), null, [], new Set(), ""), "");
  assert.match(visaHtml(v.slice(0, 1), null, vektorVal("vatten", [{ losare: "vatten.dynamik" }]), new Set(), ""), /Sänkor med volym/);
});

test("vilka vektorlager som finns följer lösarna", () => {
  assert.deepEqual(vektorVal("vatten", [{ losare: "vatten.statisk_fyllning" }]).map((x) => x.slag), ["sankor", "flodesvagar"]);
  assert.deepEqual(vektorVal("vatten", [{ losare: "geometri.lutning" }]), []);
  assert.deepEqual(vektorVal("dagsljus", [{ losare: "dagsljus.soltimmar_mark" }]), []);
  assert.deepEqual(vektorVal("dagsljus", [{ losare: "dagsljus.vsc_fasad" }]).map((x) => x.slag), ["vsc"]);
  assert.deepEqual(vektorVal("ekologi", [{ losare: "vatten.dynamik" }]), []);
});

test("vektorlagrets scenario följer läget", () => {
  assert.equal(vektorScenario(LAGER, VY, "fore"), "bas");
  assert.equal(vektorScenario(LAGER, VY, "svep"), "bas");
  assert.equal(vektorScenario(LAGER, VY, "efter"), "hojd");
  assert.equal(vektorScenario(LAGER, VY, "skillnad"), "hojd");
  assert.equal(vektorScenario(LAGER, { fore_lager: "sol.bas.soltimmar", efter_lager: null }, "skillnad"), "bas");
  assert.equal(vektorUrl("k 1", "efter", "sankor"), "/api/korningar/k%201/vektor/efter/sankor");
});

test("färgskalor: ändpunkter, mitt, klamring och klassgränser", () => {
  const tid = PALETTER.tid as readonly string[];
  assert.equal(skalFarg(tid, 0), "#ffffcc");
  assert.equal(skalFarg(tid, 1), "#4d004b");
  assert.equal(skalFarg(tid, 2), "#4d004b");
  assert.equal(skalFarg(tid, -1), "#ffffcc");
  assert.equal(skalFarg(tid, 0.5), "#fd8d3c");
  assert.equal(skalFarg(["#000000", "#ffffff"], 0.5), "#808080");
  assert.equal(varFarg(tid, 5, 5, 5), "#ffffcc"); // lo = hi: hela skalan i en färg
  assert.equal(varFarg(tid, Number.NaN, 0, 1), "#ffffcc");
  assert.equal(djupFarg(0.01), null);
  assert.equal(djupFarg(0.02), "#cfe8ff");
  assert.equal(djupFarg(0.15), "#7ec4f8");
  assert.equal(djupFarg(0.29), "#2f8be6");
  assert.equal(djupFarg(0.35), "#1a52b8");
  assert.equal(djupFarg(0.5), "#182b7a");
  assert.equal(djupFarg(1.0), "#3b1363");
  assert.equal(djupFarg(3), "#3b1363");
});

const SANKOR: VektorSvar = {
  slag: "sankor",
  scenario: "fore",
  korning_id: "k",
  meta: { antal_totalt: 14, visade: 2 },
  features: [
    { geometry: { type: "MultiPolygon", coordinates: [[[[151000, 6580000], [151010, 6580000], [151010, 6580010], [151000, 6580000]]]] }, properties: { nod_id: 4, kapacitet_m3: 120.46, djup_m: 0.35, yta_m2: 500, avrinningsyta_m2: 4000, slutsanka: false, mitt: [151005, 6580005] } },
    { geometry: { type: "MultiPolygon", coordinates: [[[[151100, 6580000], [151110, 6580000], [151110, 6580010], [151100, 6580000]]]] }, properties: { nod_id: 5, kapacitet_m3: 3.4, djup_m: 0.01, yta_m2: 20, avrinningsyta_m2: 80, slutsanka: true, mitt: [151105, 6580005] } },
  ],
};

test("sänkor blir ytor i WGS 84 med djupfärg och etikett med volym", () => {
  const k = vektorTillKarta(SANKOR);
  assert.equal(k.fc.features.length, 2);
  const f = k.fc.features[0] as GeoJSON.Feature<GeoJSON.MultiPolygon>;
  assert.equal(f.properties?.farg, "#1a52b8");
  const [lng, lat] = (f.geometry.coordinates[0]?.[0]?.[0] ?? [0, 0]) as [number, number];
  assert.ok(lng > 17 && lng < 19 && lat > 59 && lat < 60, `${lng} ${lat}`);
  assert.deepEqual(k.etiketter.map((e) => e.text), ["120 m³", "3,4 m³"]);
  const liten = vektorTillKarta({ ...SANKOR, features: [{ ...(SANKOR.features[0] as VektorSvar["features"][number]), properties: { ...SANKOR.features[0]?.properties, kapacitet_m3: 0.4 } }] });
  assert.equal(liten.etiketter.length, 0); // under 1 m³: ingen etikett
  assert.equal(k.fc.features[1]?.properties?.farg, "#cfe8ff"); // djup under lägsta klass: ljusaste klassen, inte ingen figur
});

test("beskrivningarna använder decimalkomma, enheter och flaggar slutna områden", () => {
  const t = sankaBeskrivning(SANKOR.features[1]?.properties ?? {});
  assert.match(t, /rymmer 3,4 m³/);
  assert.match(t, /Slutet område: vattnet har ingen väg ut\./);
  assert.doesNotMatch(t, /\d\.\d/);
  assert.match(flodesvagBeskrivning({ tillrinning_m2: 1234, ordning: 3, langd_m: 448, lutning: 0.0034 }), /^Flödesväg: tillrinning 1\s234 m², Strahler-ordning 3, längd 448 m, lutning 0,3 %\.$/);
});

test("flödesvägar färgas och breddas efter logaritmen av tillrinningen, och teckenförklaringen säger det", () => {
  const svar: VektorSvar = {
    slag: "flodesvagar", scenario: "efter", korning_id: "k", meta: { antal_totalt: 2, visade: 2 },
    features: [
      { geometry: { type: "LineString", coordinates: [[151000, 6580000], [151010, 6580000]] }, properties: { flodesvag_id: 1, tillrinning_m2: 500, ordning: 1, langd_m: 10, lutning: 0.01 } },
      { geometry: { type: "LineString", coordinates: [[151000, 6580010], [151010, 6580010]] }, properties: { flodesvag_id: 2, tillrinning_m2: 50000, ordning: 3, langd_m: 10, lutning: 0.01 } },
    ],
  };
  assert.deepEqual(tillrinningSkala(svar), { lo: 500, hi: 50000 });
  const k = vektorTillKarta(svar);
  assert.equal(k.fc.features[0]?.properties?.farg, "#c6dbef");
  assert.equal(k.fc.features[1]?.properties?.farg, "#08306b");
  assert.equal(k.fc.features[0]?.properties?.bredd, flodesBredd(500, 500, 50000));
  assert.ok(Number(k.fc.features[1]?.properties?.bredd) > Number(k.fc.features[0]?.properties?.bredd));
  assert.match(String(k.fc.features[1]?.properties?.beskrivning), /Strahler-ordning 3/);
  const html = vektorTeckenHtml(svar);
  assert.match(html, /Flödesvägar: tillrinning \(m²\)/);
  assert.doesNotMatch(html, /största av|gångtid/);
});

test("reglagen för minsta tillrinning och volym är logaritmiska och går att läsa av", () => {
  const g = GRANSER.flodesvagar;
  assert.ok(g);
  assert.equal(gransVarde(g, 0), 100);
  assert.equal(gransVarde(g, 100), 10000);
  assert.equal(gransVarde(g, 50), 1000);
  assert.equal(gransPos(g, 500), Math.round(100 * Math.log(5) / Math.log(100)));
  assert.match(visaHtml([], null, [{ slag: "flodesvagar", namn: "Flödesvägar", hjalp: "" }], new Set(["flodesvagar"]), ""), /data-grans="flodesvagar"/);
});

test("fasadpunkter färgas efter VSC, saknat värde blir grått, och delvisa lager säger det", () => {
  const svar: VektorSvar = {
    slag: "vsc", scenario: "bas", korning_id: "k", meta: { antal_totalt: 30000, visade: 20000 },
    features: [
      { geometry: { type: "Point", coordinates: [151000, 6580000] }, properties: { vsc_procent: 0, z_m: 3, status: "ok" } },
      { geometry: { type: "Point", coordinates: [151001, 6580000] }, properties: { vsc_procent: 100, z_m: 6, status: "ok" } },
      { geometry: { type: "Point", coordinates: [151002, 6580000] }, properties: { vsc_procent: null, z_m: 9, status: "avskuren" } },
    ],
  };
  const k = vektorTillKarta(svar);
  assert.deepEqual(k.fc.features.map((f) => f.properties?.farg), ["#00204d", "#ffea46", "#9e9e9e"]);
  assert.match(String(k.fc.features[2]?.properties?.beskrivning), /VSC saknas \(avskuren\)/);
  assert.match(vektorTeckenHtml(svar), /De 20\s?000 största av 30\s?000 visas/);
});

test("ankomsttid (tid_till_0_1m.tif) och 1D-rännstenar har svenska etiketter, enheter och vektorstöd", () => {
  assert.deepEqual(rollEtikett("ankomsttid", "ankomsttid", ""), {
    etikett: "Ankomsttid (tid till 0,1 m)",
    enhet: "min",
  });
  assert.deepEqual(rollEtikett("rannsten_flode", "", ""), {
    etikett: "Rännstenar (1D-flöde)",
    enhet: "m³/s",
  });
  const ankomstLager: LagerPost[] = [
    lp("vatten.fore.max_djup", "skyfall", "max_djup", "fore", "bas", { doman: "vatten", etikett: "Största vattendjup", enhet: "m", primar: true }),
    lp("vatten.fore.ankomsttid", "skyfall", "ankomsttid", "fore", "bas", { doman: "vatten", etikett: "ankomsttid", enhet: "" }),
  ];
  const roller = lagerVal(ankomstLager, { fore_lager: "vatten.fore.max_djup", efter_lager: null }, "vatten");
  const ank = roller.find((r) => r.roll === "ankomsttid");
  assert.equal(ank?.etikett, "Ankomsttid (tid till 0,1 m)");
  assert.equal(ank?.enhet, "min");

  assert.deepEqual(vektorVal("vatten", [{ losare: "vatten.2d" }]).map((x) => x.slag), ["rannsten_flode"]);
  assert.deepEqual(vektorVal("vatten", [{ losare: "vatten.dynamik" }, { losare: "vatten.hybrid" }]).map((x) => x.slag), ["sankor", "flodesvagar", "rannsten_flode"]);

  const rannstenSvar: VektorSvar = {
    slag: "rannsten_flode",
    scenario: "fore",
    korning_id: "k2",
    meta: { antal_totalt: 2, visade: 2 },
    features: [
      { geometry: { type: "LineString", coordinates: [[151000, 6580000], [151020, 6580000]] }, properties: { flode_m3_s: 0.015, djup_m: 0.08, langd_m: 20 } },
      { geometry: { type: "LineString", coordinates: [[151020, 6580000], [151040, 6580000]] }, properties: { flode_m3_s: 0.125, djup_m: 0.16, langd_m: 20 } },
    ],
  };
  assert.deepEqual(rannstenSkala(rannstenSvar), { lo: 0.015, hi: 0.125 });
  const k = vektorTillKarta(rannstenSvar);
  assert.equal(k.fc.features.length, 2);
  assert.ok(Number(k.fc.features[1]?.properties?.bredd) > Number(k.fc.features[0]?.properties?.bredd));
  const beskriv = rannstenBeskrivning({ flode_m3_s: 0.125, djup_m: 0.16, langd_m: 20 });
  assert.equal(beskriv, "Rännsten (1D): toppflöde 0,125 m³/s, djup 0,16 m, längd 20 m.");
  assert.doesNotMatch(beskriv, /\d\.\d/);
  const html = vektorTeckenHtml(rannstenSvar);
  assert.match(html, /Rännstenar \(1D\): toppflöde \(m³\/s\)/);
  assert.match(html, /0,015/);
  assert.match(html, /0,125 m³\/s/);
});
