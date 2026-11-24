/* Körs med `npm test`. Osäkerhet i resultatpanelen: intervall som [min; max], överfallen och sifferformen. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { BifurkationAnalysSvar, SvfIntervallSvar, VattenIntervallSvar } from "../karta/kartapi.ts";
import { jamforelse, nyckeltal, slutsats, type Amne, type EkologiSvar, type Underlag } from "./nyckeltal.ts";
import { OSAKERHET_NOT, intervallText, osakerhetTal, overfallRader, type OsakerhetSvar } from "./osakerhet.ts";
import { kroppHtml, osakerhetHtml, type OsakerhetVisning } from "./panelhtml.ts";
import { forslagInfoHtml } from "./forslagtext.ts";

const vatten: VattenIntervallSvar = {
  format: "floden-vatten-intervall/v1",
  omrade_id: "demokvarteret",
  klimatfaktor_intervall: [1, 1.25],
  antal_korningar: 12,
  antal_bifurkationsgrenar: 5,
  max_volymfel_rel: 0.006126570967967803,
  sammanfattning: { antal_noder: 14, antal_monoton_garanterad: 9, antal_bifurkationspaverkade: 5, storsta_min_djup_m: 0.3592195510864258, storsta_max_djup_m: 1234.5678 },
};
const svf: SvfIntervallSvar = {
  format: "floden-dagsljus-svf-intervall/v1",
  omrade_id: "demokvarteret",
  osakerhet_byggnad_m: 1,
  antal_riktningar: 16,
  svf_min_medel: 0.7938235642722192,
  svf_max_medel: 0.8123456789,
  bredd_max: 0.0431,
  antal_osakra_celler: 1234,
};
const bifurkation: BifurkationAnalysSvar = {
  format: "floden-bifurkation-felindikator/v1",
  omrade_id: "demokvarteret",
  k_sigma: 2,
  referensregn_mm: 100,
  antal_instabila: 2,
  punkter: [
    { nod_id: 5, primar_ut_till_id: null, alternativ_ut_till_id: 9, overfall_hojd_m: 12.99, alternativ_hojd_m: 13.02, marginal_m: 0.02927112579345703, osakerhet_komb_m: 0.084, sakerhetsmarginal: 0.35, instabil: true, overfall_x: 151345.5, overfall_y: 6580235.5, alternativ_x: 151347, alternativ_y: 6580250.5, paverkad_avrinningsyta_m2: 23088, paverkad_volym_m3: 2308.8000000000065 },
    { nod_id: 6, primar_ut_till_id: null, alternativ_ut_till_id: 9, overfall_hojd_m: 12.98, alternativ_hojd_m: 13.01, marginal_m: 0.5, osakerhet_komb_m: 0.084, sakerhetsmarginal: 6, instabil: false, overfall_x: 151354.5, overfall_y: 6580235.5, alternativ_x: 151353, alternativ_y: 6580250.5, paverkad_avrinningsyta_m2: 3117, paverkad_volym_m3: 311.7 },
  ],
  felindikator: { andel_2d_yta: 0.123456, antal_2d_celler: 100, antal_aktiva_celler: 800, antal_noder_2d: 3, antal_noder_totalt: 14 },
};
const svar: OsakerhetSvar = { vatten, svf, bifurkation, fel: [] };

/** Synlig text: taggar och attribut bort (så att css och id:n inte räknas som tal). */
const text = (html: string): string => html.replace(/<[^>]*>/g, " ").replace(/&[a-z#0-9]+;/g, " ");

test("intervallet skrivs som [min; max] med decimalkomma och enhet", () => {
  assert.equal(intervallText(0.36, 0.44, 2, "m"), "[0,36; 0,44] m");
  assert.equal(intervallText(0.794, 0.812, 3), "[0,794; 0,812]");
  assert.equal(intervallText(1234.5, 2345.6, 1, "m"), "[1 234,5; 2 345,6] m");
});

test("vatten: intervall, garanterade sänkor och känsliga överfall med liten rad under", () => {
  const t = osakerhetTal("vatten", svar);
  assert.equal(t.length, 3);
  assert.equal(t[0]?.etikett, "Största djup före, spann");
  assert.equal(t[0]?.varde, "[0,36; 1 234,57] m");
  assert.equal(t[0]?.under, "Klimatfaktor 1,00 till 1,25, 12 körningar");
  assert.equal(t[1]?.varde, "9 av 14");
  assert.equal(t[1]?.under, "5 påverkas av känsliga överfall");
  assert.equal(t[2]?.varde, "2 st");
  assert.match(t[2]?.under ?? "", /^mät in i fält; 2D-kontroll behövs på 12,3 % av ytan \(3 av 14 sänkor\)$/);
});

test("sol: himmelsandel med intervall och förklaring", () => {
  const t = osakerhetTal("dagsljus", svar);
  assert.equal(t.length, 1);
  assert.equal(t[0]?.varde, "[0,794; 0,812]");
  assert.equal(t[0]?.under, "±1,0 m byggnadshöjd, största bredd 0,043, 1 234 osäkra celler");
});

test("ekologi har ingen egen osäkerhetsrad, och utan svar finns inga rader", () => {
  assert.deepEqual(osakerhetTal("ekologi", svar), []);
  assert.deepEqual(osakerhetTal("vatten", null), []);
  assert.deepEqual(osakerhetTal("vatten", { vatten: null, svf: null, bifurkation: null, fel: [] }), []);
  assert.deepEqual(osakerhetTal("dagsljus", { ...svar, svf: null }), []);
});

test("en del som saknas döljer inte den andra: bara överfallen visas när intervallet inte kom", () => {
  const delvis: OsakerhetSvar = { vatten: null, svf: null, bifurkation, fel: ["Skyfallsintervallet gick inte att hämta: 500"] };
  const t = osakerhetTal("vatten", delvis);
  assert.equal(t.length, 1);
  assert.equal(t[0]?.etikett, "Känsliga överfall");
});

test("överfallen listas bara om de är instabila, med svensk sifferform", () => {
  const rader = overfallRader(bifurkation);
  assert.equal(rader.length, 1);
  assert.equal(rader[0], "Nod 5 (E 151 346, N 6 580 236): marginal 0,029 m, berör 2 309 m³: mät in i fält");
  assert.deepEqual(overfallRader(null), []);
});

const visning = (pa: boolean, amne: Amne): OsakerhetVisning => ({
  pa,
  laddar: false,
  tal: osakerhetTal(amne, svar),
  rader: amne === "vatten" ? overfallRader(bifurkation) : [],
  fel: [],
  overfallLager: amne === "vatten" ? { synlig: true, antal: 1 } : null,
});

test("knappen Visa osäkerhet byter text, och osäkerheten visas bara när den är på", () => {
  const av = osakerhetHtml(visning(false, "vatten"));
  assert.match(av, /Visa osäkerhet/);
  assert.match(av, /aria-pressed="false"/);
  assert.doesNotMatch(av, /\[0,36/);
  const pa = osakerhetHtml(visning(true, "vatten"));
  assert.match(pa, /Dölj osäkerhet/);
  assert.match(pa, /aria-pressed="true"/);
  assert.match(pa, /\[0,36; 1\u202f234,57\] m/);
  assert.match(pa, /id="rs-overfall"/);
  assert.match(pa, /Var bör vi mäta/);
  assert.ok(text(pa).includes(OSAKERHET_NOT));
});

const eko: EkologiSvar = {
  fore: { ECA_m2: 25331.6, ECA_intervall_m2: [1000, 30000], PC: 0.25, PC_intervall: [0.2, 0.3], antal_flackar: 4 },
  efter: { ECA_m2: 25260.7, ECA_intervall_m2: [990, 29000], PC: 0.24, antal_flackar: 4 },
  delta_eca_m2: -70.9,
  delta_eca_procent: -0.28,
};

function underlag(amne: Amne): Underlag {
  return {
    troskel: amne === "vatten" ? 0.01 : 1,
    siffror: { yta_minskning_m2: 1234.56, yta_okning_m2: 3328.4, max_minskning: -1.5, max_okning: 0.16 },
    foreDjup_m: 0.359,
    efterDjup_m: 0.444,
    foreLagrat_m3: 232.4,
    efterLagrat_m3: 468.9,
    ekologi: eko,
  };
}

test("alla renderade paneler (skyfall, sol, ekologi) har decimalkomma och inget tal med decimalpunkt", () => {
  const funna: string[] = [];
  for (const amne of ["vatten", "dagsljus", "ekologi"] as const) {
    for (const pa of [false, true]) {
      const u = underlag(amne);
      const html = kroppHtml({
        slutsats: slutsats(amne, u),
        tal: nyckeltal(amne, u),
        jamforelse: jamforelse(amne, u),
        kalla: amne === "ekologi" ? ["Potentiell livsmiljö, okänd kvalitet", "p saknas i källan; d50 = tröskel/2 (500 effektiva m)."] : [],
        anmarkning: "Regn: Stockholmsregn 2024, 100 år, 60 minuter, klimatfaktor 1,4 = 52,5 mm. Alla tal kommer ur körningen demo-vatten.",
        osakerhet: amne === "ekologi" ? null : visning(pa, amne),
        las: "Färgerna visar hur många timmar solen når marken.",
      });
      const synlig = text(html);
      if (/\d\.\d/.test(synlig)) funna.push(`${amne}/${pa}: ${synlig.match(/.{0,20}\d\.\d.{0,20}/)?.[0]}`);
      // Något tal ska faktiskt finnas, och varje tal med decimaler har komma.
      assert.match(synlig, /\d,\d/, `${amne}/${pa}`);
    }
  }
  assert.deepEqual(funna, []);
});

test("Förslags infopanel: bruttoarea och ytor med svensk sifferform", () => {
  const html = forslagInfoHtml({ antal: 1234, hus: { yta_m2: 1234.5, vaningar: 12, bta_m2: 14814.4 }, kontrollFel: null, fel: [], varningar: [], information: [], sparFel: null, sparatSom: "Mitt förslag" });
  const synlig = text(html);
  assert.doesNotMatch(synlig, /\d\.\d/);
  assert.match(synlig, /Yta 1 235 m², 12 våningar, bruttoarea ungefär 14 814 m²/);
  assert.match(synlig, /1 234 ändringar i förslaget/);
  assert.match(synlig, /Sparat som Mitt förslag/);
});
