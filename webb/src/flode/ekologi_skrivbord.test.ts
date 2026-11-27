/* Körs med `npm test`. Steg 2-sektionen "Skrivbordskontroll (skydd & naturvärden)". */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  EKOLOGI_OSL_VARNING,
  MAX_FALTLAGER_BYTES,
  beraknaSkrivbordStatusRader,
  ekologiSkrivbordPanelHtml,
  exporteraFaltchecklistaCsv,
  exporteraFaltchecklistaMarkdown,
  standardSkrivbordLagerVal,
  valideraFaltlagerFil,
  type EkologiFaltchecklistaRad,
  type EkologiFaltlagerOversikt,
  type EkologiSkrivbordSammanfattning,
} from "./ekologi_skrivbord.ts";

const EXEMPEL_SAM: EkologiSkrivbordSammanfattning = {
  skyddad_natur_tillganglig: true,
  artfynd_tillganglig: true,
  vmi_tillganglig: true,
  skyddade_omraden: {
    antal: 2,
    antal_overlappar_karna: 1,
    overlapp_karna_m2: 1450.4,
    minsta_avstand_m: 0,
    antal_natura2000: 1,
  },
  skogliga_varden: {
    antal: 3,
    antal_overlappar_karna: 0,
    overlapp_karna_m2: 0,
    minsta_avstand_m: 320,
    antal_nyckelbiotoper: 2,
    antal_sumpskogar: 1,
  },
  vmi: {
    tillganglig: true,
    antal_polygoner: 0,
    overlapp_karna_m2: 0,
  },
  artfynd_publika: {
    antal_publika_fynd: 5,
    antal_unika_arter: 2,
    antal_rodlistade_arter: 2,
    antal_fridlysta_arter: 1,
    antal_skyddsklassade_filtrerade: 1,
    period_ar: [2016, 2026],
  },
};

const EXEMPEL_FALT: EkologiFaltlagerOversikt = {
  filnamn: "nvi_haga.gpkg",
  antal_objekt: 4,
  antal_polygoner: 2,
  antal_punkter: 2,
  per_nvi_klass: { "2": 1, "3": 1 },
};

const EXEMPEL_CHECKLISTA: EkologiFaltchecklistaRad[] = [
  {
    moment: "Formellt områdesskydd & Natura 2000",
    prioritet: "Hög",
    underlag: "1 skyddat område överlappar planområdet",
    atgard: "Pröva föreskrifter och dispenskrav med Länsstyrelsen.",
  },
  {
    moment: "Artskydd & rödlistade arter (OSL 20 kap. 1 §)",
    prioritet: "Hög",
    underlag: "2 arter i publika fynd; 1 skyddsklassad filtrerad utan koordinater",
    atgard: "Beställ sekretessprövat utdrag via behörig ekolog.",
  },
];

test("beraknaSkrivbordStatusRader ger korrekta trafikljus för alla 5 källor", () => {
  const rader = beraknaSkrivbordStatusRader(EXEMPEL_SAM, EXEMPEL_FALT);
  assert.equal(rader.length, 5);
  assert.deepEqual(
    rader.map((r) => [r.id, r.status]),
    [
      ["skyddade_omraden", "rod"],
      ["skogliga_varden", "gul"],
      ["vmi", "gron"],
      ["publika_fynd", "rod"],
      ["faltlager", "rod"],
    ],
  );
  // Svensk tusentalsform i detaljtexten (inga decimalpunkter)
  assert.match(rader[0]!.detalj, /1\u202f450\u00a0m²/);
  assert.match(rader[3]!.detalj, /1 skyddsklassade filtrerade/);
});

test("beraknaSkrivbordStatusRader hanterar saknade lager ärligt som ej_hamtad utan påhittade värden", () => {
  const rader = beraknaSkrivbordStatusRader(null, null);
  assert.equal(rader.length, 5);
  for (const r of rader) {
    assert.equal(r.status, "ej_hamtad");
    assert.equal(r.tillganglig, false);
  }
  const val = standardSkrivbordLagerVal(null, null);
  assert.deepEqual(val, {
    skyddade_omraden: false,
    skogliga_varden: false,
    vmi: false,
    publika_fynd: false,
    faltlager: false,
  });
});

test("ekologiSkrivbordPanelHtml visar alltid OSL-varning, kryssrutor, uppladdning och XSS-skydd", () => {
  const html = ekologiSkrivbordPanelHtml({
    sammanfattning: EXEMPEL_SAM,
    faltlager: EXEMPEL_FALT,
    faltchecklista: EXEMPEL_CHECKLISTA,
    felmeddelande: "<script>alert(1)</script>",
  });
  assert.match(html, /Skrivbordskontroll \(skydd &amp; naturvärden\)/);
  assert.ok(html.includes(EKOLOGI_OSL_VARNING));
  assert.match(html, /data-osl-varning="true"/);
  assert.match(html, /data-ekologi-skrivbord-lager="skyddade_omraden" checked/);
  assert.match(html, /data-ekologi-skrivbord-lager="faltlager" checked/);
  assert.match(html, /data-ekologi-checklista-export="md"/);
  assert.match(html, /data-ekologi-checklista-export="csv"/);
  assert.match(html, /data-ekologi-faltlager-fil/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);

  // Även helt utan data måste OSL-varningen alltid finnas med
  const tomHtml = ekologiSkrivbordPanelHtml({ sammanfattning: null });
  assert.ok(tomHtml.includes(EKOLOGI_OSL_VARNING));
  assert.match(tomHtml, /data-ekologi-skrivbord-lager="publika_fynd" disabled/);
});

test("valideraFaltlagerFil avvisar fel format, tom fil och filer över 10 MB", () => {
  assert.equal(valideraFaltlagerFil("nvi.gpkg", 1024).ok, true);
  assert.equal(valideraFaltlagerFil("trad.geojson", 2048).ok, true);
  assert.equal(valideraFaltlagerFil("trad.JSON", 512).ok, true);
  assert.equal(valideraFaltlagerFil("nvi.exe", 512).ok, false);
  assert.equal(valideraFaltlagerFil("nvi.shp", 512).ok, false);
  assert.equal(valideraFaltlagerFil("nvi.gpkg", 0).ok, false);
  assert.equal(valideraFaltlagerFil("nvi.gpkg", MAX_FALTLAGER_BYTES + 1).ok, false);
});

test("exporteraFaltchecklistaMarkdown och Csv inkluderar obligatorisk OSL-varning och escapning", () => {
  const md = exporteraFaltchecklistaMarkdown(EXEMPEL_CHECKLISTA, "Göteborg – Haga");
  assert.match(md, /Checklista inför fältinventering \(NVI\) – Göteborg – Haga/);
  assert.ok(md.includes(EKOLOGI_OSL_VARNING));
  assert.match(md, /\| Formellt områdesskydd/);

  const csv = exporteraFaltchecklistaCsv(EXEMPEL_CHECKLISTA);
  assert.match(csv, /^Moment;Prioritet;Underlag;Åtgärd\n/);
  assert.ok(csv.includes(EKOLOGI_OSL_VARNING));
  assert.match(csv, /OSL 20 kap\. 1 §/);
});
