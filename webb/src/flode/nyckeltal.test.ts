import assert from "node:assert/strict";
import { test } from "node:test";
import {
  antagandeRader,
  dpcTabellRader,
  flaskhalsPunkter,
  formateraBullerSkarmar,
  formateraMknIntervall,
  intervallForklaring,
  jamforelse,
  kallRad,
  nyckeltal,
  resultatEtikett,
  slutsats,
  tal,
  tolkaSkarmkandidater,
  tolkaTallista,
} from "./nyckeltal.ts";

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
  assert.equal(resultatEtikett({ resultat_etikett: "Potentiell livsmiljö, okänd kvalitet" }), "Potentiell livsmiljö, okänd kvalitet");
  assert.equal(resultatEtikett({ resultat_etikett: null }), "");
  assert.deepEqual(antagandeRader(undefined), []);
  assert.equal(intervallForklaring({ intervall_typ: "faktorer" }), "");
});


test("MKN-intervall och bullerskärmar formateras utan egna beräkningar", () => {
  const mkn = formateraMknIntervall({
    min_dygn_over_grans: 12,
    max_dygn_over_grans: 28,
    min_arsmedel_ug_m3: 18.2,
    max_arsmedel_ug_m3: 24.5,
    min_percentil_90_4_ug_m3: 39.1,
    max_percentil_90_4_ug_m3: 48.0,
    klarar_mkn_garanterat: true,
  });
  assert.match(mkn.status, /Klarar MKN garanterat/);
  assert.equal(mkn.tal[0]?.varde, "12 till 28 dygn");

  const skarm = formateraBullerSkarmar({
    riktvarde_db: 60,
    valda_skarmar: [
      { skarm_id: "Skärm A", marginal_vinst: 5.2, kumulativ_vinst: 5.2, vinst_ensam: 5.2, nya_godkanda: 2 },
    ],
    akustiska_bifurkationer: [{ skuggzon_bifurkation: true }, { skuggzon_bifurkation: false }],
  });
  assert.match(skarm.sammanfattning, /1 skärm valda mot riktvärde 60\s?dB \(1 skuggzonsbifurkationer\)/);
  assert.equal(skarm.rader.length, 1);
  assert.equal(skarm.rader[0]?.skarm_id, "Skärm A");

  const synlig = JSON.stringify([mkn, skarm]);
  assert.doesNotMatch(synlig, /\d\.\d/, "decimalkomma i MKN- och skärmpanelerna");
  assert.deepEqual(tolkaTallista("10, 20.5; 30,2"), [10, 20.5, 30.2]);
  assert.equal(tolkaTallista("abc"), null);
  assert.deepEqual(tolkaSkarmkandidater("A: 1, 2\nB: 3, 4"), { A: [1, 2], B: [3, 4] });
});

test("ekologi: dpcTabellRader sorterar och formaterar dPC_efter och dpc_tabell", () => {
  assert.deepEqual(dpcTabellRader(null), []);
  const rader = dpcTabellRader(
    {
      dPC_efter: [
        { flack_id: 2, area_m2: 4500, dPC_procent: 12.34, dPC_intra_procent: 8.1, dPC_flux_procent: 3.2, dPC_connector_procent: 1.04 },
        { flack_id: 1, area_m2: 18500, dPC_procent: 55.5, dPC_intra_procent: 40.0, dPC_flux_procent: 10.5, dPC_connector_procent: 5.0 },
      ],
    },
    1,
  );
  assert.equal(rader.length, 1);
  assert.equal(rader[0]?.flack_id, "1");
  assert.match(rader[0]?.area ?? "", /^1,85\s?ha$/);
  assert.equal(rader[0]?.dpc_procent, "55,5 %");
  assert.equal(rader[0]?.intra, "40,0 %");
  assert.equal(rader[0]?.flux, "10,5 %");
  assert.equal(rader[0]?.connector, "5,0 %");

  const alt = dpcTabellRader({
    dpc_tabell: [{ flack_id: 3, area_ha: 0.45, dpc_procent: 9.2, dpc_intra: 6.0, dpc_flux: 2.2, dpc_connector: 1.0 }],
  });
  assert.equal(alt.length, 1);
  assert.match(alt[0]?.area ?? "", /^4\u202F500\s?m²$/);
  assert.equal(alt[0]?.dpc_procent, "9,2 %");
});

test("ekologi: flaskhalsPunkter extraherar SWEREF-koordinater för efter och före", () => {
  assert.deepEqual(flaskhalsPunkter(undefined), []);
  const e = {
    storsta_flaskhalsar: {
      fore: [{ e_m: 150010, n_m: 6580020, stromtathet: 0.412 }],
      efter: [{ e_m: 150050, n_m: 6580080, stromtathet: 0.789 }],
    },
  };
  const efter = flaskhalsPunkter(e);
  assert.equal(efter.length, 1);
  assert.equal(efter[0]?.e_m, 150050);
  assert.equal(efter[0]?.n_m, 6580080);
  assert.equal(efter[0]?.stromtathet_text, "0,789");

  const fore = flaskhalsPunkter(e, "fore");
  assert.equal(fore[0]?.e_m, 150010);
});
