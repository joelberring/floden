import assert from "node:assert/strict";
import { test } from "node:test";
import {
  aktuellPunkt,
  hoppaTillTopp,
  narmasteStegForTid,
  nastaSteg,
  ritaHydrografSvg,
  skapaTidsspelareTillstand,
  tidMinFranSvgX,
  tidsspelareHtml,
  tidsspelareStatusText,
  tillMaxdjup,
  type TidsserieSvar,
} from "./tidsspelare.ts";

const exempelSvar: TidsserieSvar = {
  kontrakt: "floden-tidsserie/v1",
  losare: "vatten.dynamik",
  regntyp: "cds",
  varaktighet_min: 60,
  slut_tid_min: 90,
  total_regn_mm: 55.2,
  topp_tid_min: 22.2,
  tidssteg_tid_min: [0, 20, 45, 90],
  punkter: [
    { tid_min: 0, regn_mm_h: 5, kumulativt_regn_mm: 0, max_djup_m: 0, vat_yta_01m_m2: 0, vat_yta_03m_m2: 0, volym_m3: 0, utflode_kum_m3: 0, max_hastighet_m_s: null },
    { tid_min: 22.2, regn_mm_h: 140, kumulativt_regn_mm: 28, max_djup_m: 0.34, vat_yta_01m_m2: 120, vat_yta_03m_m2: 18, volym_m3: 412, utflode_kum_m3: 10, max_hastighet_m_s: null },
    { tid_min: 45, regn_mm_h: 20, kumulativt_regn_mm: 50, max_djup_m: 0.42, vat_yta_01m_m2: 180, vat_yta_03m_m2: 35, volym_m3: 520, utflode_kum_m3: 80, max_hastighet_m_s: null },
    { tid_min: 90, regn_mm_h: 0, kumulativt_regn_mm: 55.2, max_djup_m: 0.25, vat_yta_01m_m2: 95, vat_yta_03m_m2: 0, volym_m3: 210, utflode_kum_m3: 320, max_hastighet_m_s: null },
  ],
  tidssteg: [
    { index: 0, tid_min: 0, png_url: "/api/korningar/k1/tidssteg/fore/0.png" },
    { index: 1, tid_min: 20, png_url: "/api/korningar/k1/tidssteg/fore/1.png" },
    { index: 2, tid_min: 45, png_url: "/api/korningar/k1/tidssteg/fore/2.png" },
    { index: 3, tid_min: 90, png_url: "/api/korningar/k1/tidssteg/fore/3.png" },
  ],
};

test("tillståndsmaskin: nastaSteg, hoppaTillTopp, tillMaxdjup och aktuellPunkt", () => {
  const start = skapaTidsspelareTillstand();
  assert.deepEqual(start, { spelar: false, aktivtSteg: null, hastighet: 1 });
  assert.equal(aktuellPunkt(exempelSvar, start), null);

  const s0 = nastaSteg(start, 4);
  assert.equal(s0.aktivtSteg, 0);
  const s3 = nastaSteg({ ...s0, aktivtSteg: 3 }, 4);
  assert.equal(s3.aktivtSteg, 0);

  const topp = hoppaTillTopp(exempelSvar, { spelar: true, aktivtSteg: 0, hastighet: 2 });
  assert.equal(topp.spelar, false);
  assert.equal(topp.aktivtSteg, 1); // 20 min är närmast 22,2 min
  assert.equal(topp.hastighet, 2);

  const p = aktuellPunkt(exempelSvar, topp);
  assert.equal(p?.tid_min, 22.2);

  const max = tillMaxdjup({ spelar: true, aktivtSteg: 2, hastighet: 1 });
  assert.equal(max.spelar, false);
  assert.equal(max.aktivtSteg, null);

  assert.equal(nastaSteg(start, 0).aktivtSteg, null);
  assert.equal(narmasteStegForTid(exempelSvar, 44), 2);
});

test("ritaHydrografSvg ritar staplar, mörkblå kurva och vertikal tidslinje", () => {
  const svg = ritaHydrografSvg(exempelSvar, 20, 320, 64);
  assert.match(svg, /^<svg /);
  assert.match(svg, /<rect /);
  assert.match(svg, /stroke="#1a52b8"/);
  assert.match(svg, /data-tid-markor/);
  assert.match(svg, /90 min/);

  // Utan aktivTidMin (Maxdjup) ritas ingen tidslinje
  const utanMarkor = ritaHydrografSvg(exempelSvar, null);
  assert.doesNotMatch(utanMarkor, /data-tid-markor/);

  // Fallback till max_djup_m när volym_m3 är 0 i alla punkter
  const baraDjup: TidsserieSvar = {
    ...exempelSvar,
    punkter: exempelSvar.punkter.map((pt) => ({ ...pt, volym_m3: 0 })),
  };
  assert.match(ritaHydrografSvg(baraDjup, 45), /<polyline /);
});

test("tidMinFranSvgX klampas till [0, slutTidMin]", () => {
  assert.equal(tidMinFranSvgX(0, 320, 90), 0);
  assert.equal(tidMinFranSvgX(6, 320, 90), 0);
  assert.equal(tidMinFranSvgX(314, 320, 90), 90);
  assert.equal(tidMinFranSvgX(500, 320, 90), 90);
  const mitt = tidMinFranSvgX(160, 320, 90);
  assert.ok(Math.abs(mitt - 45) < 0.01);
});

test("tidsspelareHtml och statusrad använder svenskt talformat", () => {
  const htmlSpela = tidsspelareHtml(exempelSvar, { spelar: true, aktivtSteg: 2, hastighet: 2 });
  assert.match(htmlSpela, /⏸ Paus/);
  assert.match(htmlSpela, /2×/);
  assert.match(htmlSpela, /Topp/);
  assert.match(htmlSpela, /Maxdjup/);
  assert.match(htmlSpela, /t = 45 min · Djup 42 cm · Volym 520 m³/);
  assert.match(htmlSpela, /data-tid-slider/);
  assert.match(htmlSpela, /<svg /);

  const htmlMax = tidsspelareHtml(exempelSvar, skapaTidsspelareTillstand());
  assert.match(htmlMax, /▶ Spela/);
  assert.match(htmlMax, /1×/);
  assert.match(htmlMax, /data-tid-handling="maxdjup" aria-pressed="true"/);
  assert.equal(tidsspelareStatusText(exempelSvar, skapaTidsspelareTillstand()), "Maxdjup · Djup 42 cm · Volym 520 m³");
});
