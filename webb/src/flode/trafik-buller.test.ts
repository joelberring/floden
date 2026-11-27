/*
 * Körs med `npm test`. Deterministiska tester för klickbara väglänkar, trafiktabell
 * (Nuläge ÅDT, Prognos-ÅDT, källmärke NVDB/Kommun/Egen, osäkerhetsflagga) samt bevarad
 * 2-klicks manuell vägritning i `bullerflik.ts`.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BULLER_SKISS_BANNER,
  STANDARD_BULLER_PARAM,
  bullerPanelHtml,
  byggBullerBegaranMedTrafik,
  byggBullerVagSegmentFranValda,
  byggTrafikJusteringarPayload,
  effektivAdtForLank,
  projektTrafikUrl,
  tillampaBullerVagKlick,
  tolkaProjektTrafikGeoJson,
  trafikKallmarkeEtikett,
  trafikTabellHtml,
  uppdateraValdTrafikLank,
  valideraValdaTrafikLankar,
  valjEllerAvmarkeraTrafikLank,
  type TrafikLank,
} from "./bullerflik.ts";

const EXEMPEL_GEOJSON = {
  type: "FeatureCollection",
  format: "floden-projekt-trafik/v1",
  koordinatsystem: "EPSG:3011",
  features: [
    {
      type: "Feature",
      id: "nvdb_1",
      geometry: {
        type: "LineString",
        coordinates: [
          [150020, 6580100],
          [150220, 6580100],
        ],
      },
      properties: {
        lank_id: "nvdb_1",
        gatunamn: "Sprängkullsgatan",
        hastighet_km_h: 40,
        adt_fordon_dygn: 11000,
        prognos_adt_fordon_dygn: 13500,
        andel_tung_pct: 8.5,
        matar: 2025,
        adt_kalla: "Trafikverket Lastkajen",
        kallmarke: "NVDB",
        osaker: false,
        har_adt: true,
      },
    },
    {
      type: "Feature",
      id: "kom_2",
      geometry: {
        type: "LineString",
        coordinates: [
          [150100, 6580020],
          [150100, 6580250],
        ],
      },
      properties: {
        lank_id: "kom_2",
        gatunamn: "Vasagatan",
        hastighet_km_h: 30,
        adt_fordon_dygn: 8400,
        prognos_adt_fordon_dygn: null,
        andel_tung_pct: 5.0,
        matar: 2024,
        adt_kalla: "Göteborgs Stad trafikmängdskatalog",
        kallmarke: "Kommun",
        osaker: true,
        har_adt: true,
      },
    },
    {
      type: "Feature",
      id: "lokal_3",
      geometry: {
        type: "LineString",
        coordinates: [
          [150180, 6580050],
          [150180, 6580200],
        ],
      },
      properties: {
        lank_id: "lokal_3",
        gatunamn: "Haga Nygata",
        hastighet_km_h: 30,
        adt_fordon_dygn: null,
        prognos_adt_fordon_dygn: null,
        andel_tung_pct: 10, // Ska ignoreras när ÅDT saknas!
        adt_kalla: "NVDB",
        kallmarke: "NVDB",
        osaker: false,
        har_adt: false,
      },
    },
  ],
};

test("tolkaProjektTrafikGeoJson tolkar väglänkar i EPSG:3011 och hittar aldrig på saknad ÅDT", () => {
  assert.equal(projektTrafikUrl("haga 1"), "/api/projekt/haga%201/trafik");

  const lankar = tolkaProjektTrafikGeoJson(EXEMPEL_GEOJSON);
  assert.equal(lankar.length, 3);
  const [l1, l2, l3] = lankar as [TrafikLank, TrafikLank, TrafikLank];

  assert.equal(l1.lank_id, "nvdb_1");
  assert.equal(l1.adt_fordon_dygn, 11000);
  assert.equal(l1.prognos_adt_fordon_dygn, 13500);
  assert.equal(l1.kallmarke, "NVDB");
  assert.equal(l1.har_adt, true);
  assert.equal(trafikKallmarkeEtikett(l1), "NVDB 2025");

  assert.equal(l2.kallmarke, "Kommun");
  assert.equal(l2.osaker, true);
  assert.equal(trafikKallmarkeEtikett(l2), "Kommun 2024");

  // Hård regel: saknas ÅDT förblir adt, andel_tung_pct, adt_kalla och kallmarke null.
  assert.equal(l3.adt_fordon_dygn, null);
  assert.equal(l3.andel_tung_pct, null);
  assert.equal(l3.adt_kalla, null);
  assert.equal(l3.kallmarke, null);
  assert.equal(l3.har_adt, false);
  assert.equal(trafikKallmarkeEtikett(l3), "ÅDT saknas");
});

test("valjEllerAvmarkeraTrafikLank, redigering, Nuläge/Prognos-växling och begäran till buller.nord2000", () => {
  const lankar = tolkaProjektTrafikGeoJson(EXEMPEL_GEOJSON);
  const [l1, l2, l3] = lankar as [TrafikLank, TrafikLank, TrafikLank];

  let valda = valjEllerAvmarkeraTrafikLank([], l1);
  valda = valjEllerAvmarkeraTrafikLank(valda, l3);
  assert.equal(valda.length, 2);

  // l3 saknar ÅDT -> validering stoppar ärligt utan gissning.
  assert.match(valideraValdaTrafikLankar(valda, "nulage") ?? "", /ÅDT saknas.*Haga Nygata/);

  // Redigera l3 med egen ÅDT + prognos + tung % -> märks som Egen och validerar rent.
  valda = uppdateraValdTrafikLank(valda, "lokal_3", {
    adt_fordon_dygn: 2500,
    prognos_adt_fordon_dygn: 3800,
    andel_tung_pct: 4.5,
  });
  const redigeradL3 = valda.find((v) => v.lank_id === "lokal_3")!;
  assert.equal(redigeradL3.kallmarke, "Egen");
  assert.equal(redigeradL3.adt_kalla, "Egen uppgift");
  assert.equal(valideraValdaTrafikLankar(valda, "nulage"), null);
  assert.equal(valideraValdaTrafikLankar(valda, "prognos"), null);

  // Nuläge vs Prognos väljer rätt ÅDT-kolumn (och faller tillbaka på nuläge när prognos saknas).
  assert.equal(effektivAdtForLank(valda[0]!, "nulage"), 11000);
  assert.equal(effektivAdtForLank(valda[0]!, "prognos"), 13500);
  const medL2 = valjEllerAvmarkeraTrafikLank(valda, l2);
  const valdL2 = medL2.find((v) => v.lank_id === "kom_2")!;
  assert.equal(effektivAdtForLank(valdL2, "prognos"), 8400);

  const segNulage = byggBullerVagSegmentFranValda(valda, "nulage");
  const segPrognos = byggBullerVagSegmentFranValda(valda, "prognos");
  assert.deepEqual(
    segNulage.map((s) => s.adt_fordon_dygn),
    [11000, 2500],
  );
  assert.deepEqual(
    segPrognos.map((s) => s.adt_fordon_dygn),
    [13500, 3800],
  );

  // Begäran till POST /api/analys/buller och POST /api/projekt/<id>/trafik.
  const beg = byggBullerBegaranMedTrafik("haga-1", STANDARD_BULLER_PARAM, valda, {
    scenario: "skarm",
    trafikLage: "prognos",
  });
  assert.equal(beg.omrade_id, "haga-1");
  assert.equal(beg.scenario, "skarm");
  assert.equal(beg.vagsegment.length, 2);
  assert.equal(beg.vagsegment[0]?.adt_fordon_dygn, 13500);

  const sparaPayload = byggTrafikJusteringarPayload(valda);
  assert.equal(sparaPayload.ersatt, false);
  assert.equal(sparaPayload.justeringar.length, 2);
  assert.equal(sparaPayload.justeringar[1]?.kalla, "Egen uppgift");

  // Avmarkering vid nytt klick på samma länk.
  const efterAvmark = valjEllerAvmarkeraTrafikLank(valda, l1);
  assert.deepEqual(
    efterAvmark.map((v) => v.lank_id),
    ["lokal_3"],
  );
});

test("trafikTabellHtml och bullerPanelHtml visar klickbara väglänkar, källmärken, Nuläge/Prognos och bevarar manuell vägritning", () => {
  const lankar = tolkaProjektTrafikGeoJson(EXEMPEL_GEOJSON);
  let valda = valjEllerAvmarkeraTrafikLank([], lankar[0]!);
  valda = valjEllerAvmarkeraTrafikLank(valda, lankar[1]!);
  valda = valjEllerAvmarkeraTrafikLank(valda, lankar[2]!);
  valda = uppdateraValdTrafikLank(valda, "lokal_3", { adt_fordon_dygn: 1800 });

  const tabell = trafikTabellHtml({
    tillgangliga: lankar,
    valda,
    trafikLage: "prognos",
  });
  assert.match(tabell, /data-bl-trafik-lage="nulage"[^>]*aria-pressed="false"/);
  assert.match(tabell, /data-bl-trafik-lage="prognos"[^>]*aria-pressed="true"/);
  assert.match(tabell, /Nuläge ÅDT/);
  assert.match(tabell, /Prognos-ÅDT/);
  assert.match(tabell, /NVDB 2025/);
  assert.match(tabell, /Kommun 2024/);
  assert.match(tabell, /data-bl-kallmarke="Egen"[^>]*>Egen</);
  assert.match(tabell, /Osäker/);
  assert.match(tabell, /id="ar-bl-spara-trafik"/);

  // Full bullerPanelHtml har alltid SKISS-bannern, klickbara väglänkar OCH befintlig 2-klicks Rita väg!
  const panelHtml = bullerPanelHtml({
    param: { ...STANDARD_BULLER_PARAM },
    laddar: false,
    fel: null,
    svar: null,
    scenario: null,
    visaLager: false,
    trafikLankar: lankar,
    valdaTrafikLankar: valda,
    trafikLage: "nulage",
  });
  assert.ok(panelHtml.includes(BULLER_SKISS_BANNER));
  assert.match(panelHtml, /id="ar-bl-trafik-sektion"/);
  assert.match(panelHtml, /id="ar-bl-rita-vag"[^>]*>Rita väg på kartan</);

  // Befintlig 2-klicks manuell vägritning fungerar helt oförändrat.
  const p1 = tillampaBullerVagKlick({ ...STANDARD_BULLER_PARAM }, null, 150010, 6580020);
  assert.equal(p1.klar, false);
  const p2 = tillampaBullerVagKlick({ ...STANDARD_BULLER_PARAM }, p1.ritadPunkt1, 150110, 6580020);
  assert.equal(p2.klar, true);
});
