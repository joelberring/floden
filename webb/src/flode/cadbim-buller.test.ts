/*
 * Körs med `npm test`. Deterministiska tester för Steg 2-panelerna CAD/BIM (U17)
 * och Buller-skissflik (U13) samt roll-etiketter i analyslager.ts.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { PlanDok } from "../karta/plandok.ts";
import { PALETTER, ROLL_ETIKETTER, rollEtikett } from "./analyslager.ts";
import {
  BULLER_ANALYS_URL,
  BULLER_SKISS_BANNER,
  STANDARD_BULLER_PARAM,
  bullerLagerTeckenHtml,
  bullerPanelHtml,
  byggBullerBegaran,
  foreslaVagKoordinater,
  tolkaBullerTal,
  valideraBullerParam,
  valjBullerLager,
  type BullerAnalysSvar,
  type BullerLagerRef,
} from "./bullerflik.ts";
import {
  CAD_EXPORT_FORMAT,
  bytesTillBase64,
  cadBimPanelHtml,
  cadExportUrl,
  cadImportUrl,
  laggInCadImportIDok,
  type CadImportSvar,
} from "./cadbim.ts";

const EXEMPEL_CAD_IMPORT: CadImportSvar = {
  format: "floden-cad-import/v1",
  projekt_id: "demokvarteret",
  filnamn: "kvarter.dxf",
  filtyp: "dxf",
  koordinatsystem: "EPSG:3011",
  hojdsystem: "RH 2000",
  kalla_crs: "EPSG:3006",
  antal_objekt: 4,
  antal_per_typ: { byggnader: 1, markytor: 1, brytlinjer: 1, trad: 1 },
  geojson: { type: "FeatureCollection", features: [] },
  features: [
    {
      type: "Feature",
      id: "hus_1",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [150100, 6580100],
            [150120, 6580100],
            [150120, 6580115],
            [150100, 6580115],
            [150100, 6580100],
          ],
        ],
      },
      properties: { objekt_id: "hus_1", roll: "byggnad", byggnadshojd_m: 14.5 },
    },
    {
      type: "Feature",
      id: "mark_1",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [150130, 6580100],
            [150150, 6580100],
            [150150, 6580120],
            [150130, 6580120],
            [150130, 6580100],
          ],
        ],
      },
      properties: { objekt_id: "mark_1", roll: "mark", niva_rh2000: 11.2 },
    },
    {
      type: "Feature",
      id: "mur_1",
      geometry: {
        type: "LineString",
        coordinates: [
          [150100, 6580095, 10.5],
          [150140, 6580095, 10.8],
        ],
      },
      properties: { objekt_id: "mur_1", roll: "brytlinje", typ: "mur", hojd_m: 3.5 },
    },
    {
      type: "Feature",
      id: "trad_1",
      geometry: { type: "Point", coordinates: [150125, 6580105, 10.2] },
      properties: { objekt_id: "trad_1", roll: "trad" },
    },
  ],
  varningar: ["Ej stödda DXF-entiteter hoppades över (SPLINE: 2)."],
  ohamtade_entiteter: { SPLINE: 2 },
};

const EXEMPEL_BULLER_LAGER = (typ: "bas" | "scenario" | "skillnad"): BullerLagerRef => ({
  id: `bl_test_${typ}`,
  roll: "ekvivalent_ljudniva_dba",
  typ,
  etikett: "Ekvivalent ljudnivå",
  enhet: "dB(A)",
  farg: typ === "skillnad" ? "skillnad" : "fara",
  skala: typ === "skillnad" ? { min: -12, max: 12 } : { min: 40, max: 68 },
  teckenforklaring:
    typ === "skillnad"
      ? { typ: "divergerande", min: -12, max: 12, troskel: 0.5, farg_minskning: "#2b5d8c", farg_okning: "#b4462b" }
      : { typ: "kontinuerlig", min: 40, max: 68, stopp: [...(PALETTER.fara ?? [])] },
  utbredning: { x_min: 150000, y_min: 6580000, x_max: 150060, y_max: 6580060 },
  pixelstorlek_m: 5,
  bredd_px: 12,
  hojd_px: 12,
  tak_maskat: true,
  png: `/api/kartverktyg/demokvarteret/buller/bl_test_${typ}.png`,
});

const EXEMPEL_BULLER_SVAR: BullerAnalysSvar = {
  format: "floden-buller-webb/v1",
  omrade_id: "demokvarteret",
  scenario: "bullerskarm",
  nord2000_konform: false,
  metodstatus: "skiss",
  rapport_sparrad: true,
  sammanfattning: {
    nord2000_konform: false,
    metodstatus: "skiss",
    rapport_sparrad: true,
    laeq_max_dba: 61.4,
    laeq_medel_dba: 48.2,
    andel_over_55_dba_pct: 22.5,
    andel_over_60_dba_pct: 4.0,
    antal_fasadpunkter: 12,
    fasad_laeq_max_dba: 57.9,
    fasad_laeq_medel_dba: 44.3,
    mottagarhojd_m: 2.0,
    raster_cell_m: 5.0,
    antal_bullerskarmar: 1,
    varningar: [],
  },
  fore: {
    nord2000_konform: false,
    metodstatus: "skiss",
    rapport_sparrad: true,
    laeq_max_dba: 62.0,
    laeq_medel_dba: 53.6,
    andel_over_55_dba_pct: 45.0,
    andel_over_60_dba_pct: 10.0,
    antal_fasadpunkter: 12,
    fasad_laeq_max_dba: 59.5,
    fasad_laeq_medel_dba: 51.0,
    mottagarhojd_m: 2.0,
    raster_cell_m: 5.0,
    varningar: [],
  },
  efter: {
    nord2000_konform: false,
    metodstatus: "skiss",
    rapport_sparrad: true,
    laeq_max_dba: 61.4,
    laeq_medel_dba: 48.2,
    andel_over_55_dba_pct: 22.5,
    andel_over_60_dba_pct: 4.0,
    antal_fasadpunkter: 12,
    fasad_laeq_max_dba: 57.9,
    fasad_laeq_medel_dba: 44.3,
    mottagarhojd_m: 2.0,
    raster_cell_m: 5.0,
    antal_bullerskarmar: 1,
    varningar: [],
  },
  jamforelse: {
    delta_laeq_max_dba: -0.6,
    delta_laeq_medel_dba: -5.4,
    delta_andel_over_55_dba_pct: -22.5,
    delta_andel_over_60_dba_pct: -6.0,
    min_delta_dba: -14.7,
    max_delta_dba: 0.0,
    medel_delta_dba: -5.4,
    max_dampning_dba: 14.7,
    antal_jamforda_celler: 136,
    nord2000_konform: false,
    metodstatus: "skiss",
    rapport_sparrad: true,
  },
  fasad_buller: { type: "FeatureCollection", features: [] },
  lager: {
    ekvivalent_ljudniva_dba_fore: EXEMPEL_BULLER_LAGER("bas"),
    ekvivalent_ljudniva_dba_efter: EXEMPEL_BULLER_LAGER("scenario"),
    ekvivalent_ljudniva_dba_skillnad: EXEMPEL_BULLER_LAGER("skillnad"),
  },
  varningar: ["Skiss – ej för beslut: nord2000_konform=False."],
};

test("cadImportUrl, cadExportUrl och CAD_EXPORT_FORMAT täcker alla 5 format", () => {
  assert.equal(cadImportUrl("proj 1"), "/api/projekt/proj%201/cad/importera");
  assert.deepEqual(
    CAD_EXPORT_FORMAT.map((f) => [f.id, f.andelse]),
    [
      ["gpkg", ".gpkg"],
      ["dxf", ".dxf"],
      ["landxml", ".xml"],
      ["obj", ".obj"],
      ["glb", ".glb"],
    ],
  );
  const url = new URL(cadExportUrl("proj-1", "glb", "forslag-a"), "http://localhost");
  assert.equal(url.pathname, "/api/projekt/proj-1/cad/exportera");
  assert.equal(url.searchParams.get("format"), "glb");
  assert.equal(url.searchParams.get("scenario"), "forslag-a");
  assert.equal(bytesTillBase64(new Uint8Array([103, 108, 84, 70])), "Z2xURg==");
});

test("laggInCadImportIDok lägger in byggnader, markytor och 3D-brytlinjer i PlanDok som en ångringsbar grupp", () => {
  const dok = new PlanDok();
  const utfall = laggInCadImportIDok(dok, EXEMPEL_CAD_IMPORT);
  assert.deepEqual(utfall, { antalHus: 1, antalYtor: 1, antalLinjer: 1, antalTrad: 1 });
  assert.equal(dok.ny.size, 1);
  assert.equal(dok.figurer.size, 2);
  const hus = [...dok.ny.values()][0]!;
  assert.equal(hus.hojd_m, 14.5);
  const mur = [...dok.figurer.values()].find((f) => f.typ === "linje")!;
  assert.equal(mur.egenskaper.typ, "mur");
  assert.equal(mur.egenskaper.hojd_m, 3.5);
  assert.equal(mur.punkter[0]?.z, 10.5);

  // Ett enda ångra-steg tar tillbaka hela CAD-importen
  dok.angra();
  assert.equal(dok.ny.size, 0);
  assert.equal(dok.figurer.size, 0);
});

test("cadBimPanelHtml visar uppladdning, IFC-ursprung, varningar, ohämtade entiteter och alla 5 exportlänkar", () => {
  const html = cadBimPanelHtml(
    {
      laddar: false,
      fel: null,
      svar: EXEMPEL_CAD_IMPORT,
      inlagt: { antalHus: 1, antalYtor: 1, antalLinjer: 1, antalTrad: 1 },
      scenario: "forslag-a",
      ursprungE: "150000",
      ursprungN: "6580000",
      ursprungZ: "10",
    },
    "demokvarteret",
  );
  assert.match(html, /id="ar-cadbim-fil"/);
  assert.match(html, /id="ar-cadbim-e0"/);
  assert.match(html, /kvarter\.dxf/);
  assert.match(html, /EPSG:3006 → EPSG:3011/);
  assert.match(html, /SPLINE \(2\)/);
  assert.match(html, /Ej stödda DXF-entiteter/);
  for (const fmt of ["gpkg", "dxf", "landxml", "obj", "glb"]) {
    assert.match(html, new RegExp(`data-cad-export="${fmt}"`));
    assert.match(html, new RegExp(`format=${fmt}&amp;scenario=forslag-a`));
  }
});

test("valideraBullerParam kräver uttrycklig trafik och vägsegment, byggBullerBegaran bygger korrekt payload", () => {
  assert.equal(BULLER_ANALYS_URL, "/api/analys/buller");
  assert.equal(tolkaBullerTal("10,5"), 10.5);
  assert.match(valideraBullerParam({ ...STANDARD_BULLER_PARAM }) ?? "", /ÅDT/);

  const vag = foreslaVagKoordinater({ x_min: 150000, y_min: 6580000, x_max: 150100, y_max: 6580100 });
  const giltig = {
    ...STANDARD_BULLER_PARAM,
    adt_fordon_dygn: 8000,
    hastighet_km_h: 50,
    andel_tung_pct: 12,
    mottagarhojd_m: 2,
    ...vag,
  };
  assert.equal(valideraBullerParam(giltig), null);
  assert.match(valideraBullerParam({ ...giltig, hastighet_km_h: 0 }) ?? "", /hastighet/i);
  assert.match(valideraBullerParam({ ...giltig, andel_tung_pct: 110 }) ?? "", /0 och 100/);
  assert.match(valideraBullerParam({ ...giltig, mottagarhojd_m: 0 }) ?? "", /Mottagarhöjd/);

  const beg = byggBullerBegaran("demokvarteret", giltig, "bullerskarm");
  assert.equal(beg.omrade_id, "demokvarteret");
  assert.equal(beg.scenario, "bullerskarm");
  assert.equal(beg.vagsegment.length, 1);
  assert.equal(beg.vagsegment[0]?.adt_fordon_dygn, 8000);
});

test("bullerPanelHtml visar alltid framträdande SKISS-varningsbanner, trafikfält, LAeq-sammanfattning och före/efter-diff", () => {
  const tomHtml = bullerPanelHtml({
    param: { ...STANDARD_BULLER_PARAM },
    laddar: false,
    fel: null,
    svar: null,
    scenario: null,
    visaLager: false,
  });
  assert.ok(tomHtml.includes(BULLER_SKISS_BANNER));
  assert.match(tomHtml, /id="ar-bl-adt"/);
  assert.match(tomHtml, /id="ar-bl-hast"/);
  assert.match(tomHtml, /id="ar-bl-tung"/);
  assert.match(tomHtml, /id="ar-bl-hojd"/);

  const svarHtml = bullerPanelHtml({
    param: { ...STANDARD_BULLER_PARAM, adt_fordon_dygn: 10000 },
    laddar: false,
    fel: null,
    svar: EXEMPEL_BULLER_SVAR,
    scenario: "bullerskarm",
    visaLager: true,
  });
  assert.ok(svarHtml.includes(BULLER_SKISS_BANNER));
  assert.match(svarHtml, /61,4 dB\(A\)/);
  assert.match(svarHtml, /48,2 dB\(A\)/);
  assert.match(svarHtml, /22,5 %/);
  assert.match(svarHtml, /4,0 %/);
  assert.match(svarHtml, /−5,4 dB\(A\)/);
  assert.match(svarHtml, /−14,7 dB\(A\)/);
  assert.match(svarHtml, /nord2000_konform=false/);
  assert.match(svarHtml, /rapport spärrad/);
});

test("valjBullerLager, bullerLagerTeckenHtml och analyslager stödjer ekvivalent_ljudniva_dba", () => {
  assert.equal(valjBullerLager(EXEMPEL_BULLER_SVAR, true, "nulage")?.typ, "bas");
  assert.equal(valjBullerLager(EXEMPEL_BULLER_SVAR, true, "efter")?.typ, "scenario");
  assert.equal(valjBullerLager(EXEMPEL_BULLER_SVAR, true, "skillnad")?.typ, "skillnad");
  assert.equal(valjBullerLager(EXEMPEL_BULLER_SVAR, false, "nulage"), null);

  const tecken = bullerLagerTeckenHtml(EXEMPEL_BULLER_LAGER("bas"));
  assert.match(tecken, /Ekvivalent ljudnivå \(dB\(A\)\) · SKISS \(ej Nord2000-konform\)/);
  assert.match(tecken, /tak, inte mark/);

  const teckenDiff = bullerLagerTeckenHtml(EXEMPEL_BULLER_LAGER("skillnad"));
  assert.match(teckenDiff, /Blått = tystare efter skärm\/åtgärd/);

  assert.equal(ROLL_ETIKETTER.ekvivalent_ljudniva_dba?.enhet, "dB(A)");
  assert.deepEqual(rollEtikett("ekvivalent_ljudniva_dba", "ekvivalent_ljudniva_dba", ""), {
    etikett: "Ekvivalent ljudnivå",
    enhet: "dB(A)",
  });
});
