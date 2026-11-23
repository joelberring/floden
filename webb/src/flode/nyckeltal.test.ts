import assert from "node:assert/strict";
import { test } from "node:test";
import { antagandeRader, intervallForklaring, jamforelse, kallRad, nyckeltal, slutsats, tal } from "./nyckeltal.ts";

test("saknade tal visas som –, aldrig som noll", () => {
  assert.equal(tal(null, 1, "m"), "–");
  assert.equal(tal(undefined, 1, "m"), "–");
  assert.equal(tal(Number.NaN, 1, "m"), "–");
  assert.match(tal(0, 2, "m"), /^0,00\s?m$/);
});

test("fyra nyckeltal för vatten och sol, alla – utan underlag", () => {
  for (const amne of ["vatten", "dagsljus"] as const) {
    const t = nyckeltal(amne, { troskel: null, siffror: null });
    assert.equal(t.length, 4);
    for (const x of t) assert.equal(x.varde, "–", `${amne}: ${x.etikett}`);
  }
});

test("siffrorna kommer från underlaget", () => {
  const t = nyckeltal("vatten", {
    troskel: 0.01,
    siffror: { yta_minskning_m2: 120, yta_okning_m2: 30, max_minskning: -0.2, max_okning: 0.05 },
    foreDjup_m: 0.36,
    efterDjup_m: 0.4,
  });
  assert.ok(t.some((x) => x.varde.includes("0,36")));
  assert.ok(t.some((x) => x.varde.includes("120")));
});

test("ekologi: sex nyckeltal, alla – utan underlag, och inget räknas fram", () => {
  const tom = nyckeltal("ekologi", { troskel: null, siffror: null });
  assert.equal(tom.length, 6);
  for (const x of tom) assert.equal(x.varde, "–", x.etikett);
  assert.match(slutsats("ekologi", { troskel: null, siffror: null }), /ingen ekologisk konnektivitet/);
});

test("ekologi: ECA med intervall, ΔECA och patchantal kommer ur svaret; hektar från 10 000 m²", () => {
  const u = {
    troskel: null,
    siffror: null,
    ekologi: {
      fore: { ECA_m2: 12000, ECA_intervall_m2: [9000, 15000] as [number, number], PC: 0.25, PC_intervall: [0.2, 0.3] as [number, number], antal_flackar: 4 },
      efter: { ECA_m2: 11742, ECA_intervall_m2: [8800, 14800] as [number, number], PC: 0.24, antal_flackar: 4 },
      delta_eca_m2: -258,
      delta_eca_procent: -2.15,
    },
  };
  const t = nyckeltal("ekologi", u);
  assert.match(t[0]?.varde ?? "", /^1,20\s?ha$/);
  assert.match(t[0]?.under ?? "", /^0,90\s?ha till 1,50\s?ha$/);
  assert.match(t[1]?.varde ?? "", /^1,17\s?ha/);
  assert.match(t[2]?.varde ?? "", /^−?-?258\s?m²$/);
  assert.equal(t[3]?.varde, "0,2500");
  assert.equal(t[3]?.under, "0,2000 till 0,3000");
  assert.equal(t[4]?.varde, "0,2400");
  assert.equal(t[4]?.under, "");
  assert.match(t[5]?.varde ?? "", /^4 och 4$/);
  assert.match(slutsats("ekologi", u), /minskar .* med 258\s?m² \(2,2\s?%\)/);
  const st = jamforelse("ekologi", u).staplar;
  assert.equal(st.length, 2);
  assert.match(st[0]?.varde ?? "", /^1,20\s?ha$/);
});

test("ekologi: en positiv ΔECA får ingen framgångsmening", () => {
  const s = slutsats("ekologi", { troskel: null, siffror: null, ekologi: { delta_eca_m2: 10 } });
  assert.match(s, /kontrollera/);
});

test("ekologi: signalart, källa och antaganden kommer ur svaret och visas som text", () => {
  const e = {
    kalla_rad: "Signalarter: tofsmes, talltita. Källa: Bovin 2020, bil. 2 s. 3-6, tab. 2 s. 8.",
    antaganden: ["p saknas i källan; d50 = tröskel/2 (500 effektiva m).", 5 as unknown as string],
    intervall_typ: "kallbaserat",
  };
  assert.match(kallRad(e), /^Signalarter: tofsmes, talltita\. Källa:/);
  assert.deepEqual(antagandeRader(e), ["p saknas i källan; d50 = tröskel/2 (500 effektiva m)."]);
  assert.match(intervallForklaring(e), /lägsta och högsta värde mellan källans scenarier/);
  assert.equal(kallRad(null), "");
  assert.deepEqual(antagandeRader(undefined), []);
  assert.equal(intervallForklaring({ intervall_typ: "faktorer" }), "");
});
