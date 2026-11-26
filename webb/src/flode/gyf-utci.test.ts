/*
 * Körs med `npm test`. Deterministiska tester för Steg 2-panelerna Grönytefaktor (GYF, U16)
 * och Värmestress UTCI / T_mrt (U15) samt palettsynk och VektorLagerRef.id?.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import type { VektorLagerRef } from "../fragor-typer.ts";
import { PALETTER, ROLL_ETIKETTER, rollEtikett } from "./analyslager.ts";
import {
  GYF_PROFILER,
  STANDARD_VARMESTRESS_PARAM,
  gyfAnalysUrl,
  gyfPanelHtml,
  gyfUtciPanelerHtml,
  tolkaSvensktTal,
  valideraVarmestressParam,
  valjVarmestressLager,
  varmelagerTeckenHtml,
  varmestressAnalysUrl,
  varmestressPanelHtml,
  type GyfAnalysSvar,
  type VarmestressAnalysSvar,
  type VarmestressLagerRef,
} from "./gyf-utci.ts";

const EXEMPEL_GYF: GyfAnalysSvar = {
  format: "floden-gyf-webb/v1",
  omrade_id: "demokvarteret",
  profil: "stockholm",
  profil_namn: "Stockholms stad – Grönytefaktor för kvartersmark",
  kommun: "Stockholm",
  riktmarke: 0.6,
  scenario: "gron-atgard",
  metodstatus: "skiss",
  fore: {
    profil_id: "stockholm",
    profil_namn: "Stockholms stad – Grönytefaktor för kvartersmark",
    kommun: "Stockholm",
    metodstatus: "skiss",
    riktmarke: 0.6,
    cellstorlek_m: 1.0,
    tomt_celler: 1000,
    tomt_area_m2: 1000,
    grund_eko_effektiv_area_m2: 480,
    tillagg_eko_effektiv_area_m2: 0,
    eko_effektiv_area_m2: 480,
    gyf: 0.48,
    uppfyller_mal: false,
    saknas_till_mal_m2: 120,
    delfaktorer: [],
    tillagg: [],
    flaggade_celler: 0,
    flaggad_area_m2: 0,
    flaggad_andel_pct: 0,
    okanda_koder: [],
    ogiltiga_celler: 0,
    varningar: [],
  },
  efter: {
    profil_id: "stockholm",
    profil_namn: "Stockholms stad – Grönytefaktor för kvartersmark",
    kommun: "Stockholm",
    metodstatus: "skiss",
    riktmarke: 0.6,
    cellstorlek_m: 1.0,
    tomt_celler: 1000,
    tomt_area_m2: 1000,
    grund_eko_effektiv_area_m2: 560,
    tillagg_eko_effektiv_area_m2: 80,
    eko_effektiv_area_m2: 640,
    gyf: 0.64,
    uppfyller_mal: true,
    saknas_till_mal_m2: 0,
    delfaktorer: [
      {
        faktor_id: "vegetation_mark",
        namn: "Vegetation på mark",
        faktor: 1.0,
        antal_celler: 560,
        area_m2: 560,
        andel_av_tomt_pct: 56.0,
        eko_effektiv_area_m2: 560,
        bidrag_gyf: 0.56,
      },
      {
        faktor_id: "hardgjord",
        namn: "Hårdgjord tät yta",
        faktor: 0.0,
        antal_celler: 420,
        area_m2: 420,
        andel_av_tomt_pct: 42.0,
        eko_effektiv_area_m2: 0,
        bidrag_gyf: 0.0,
      },
    ],
    tillagg: [
      {
        tillagg_id: "trad_krontackning",
        namn: "Träd (krontäckning)",
        area_m2: 200,
        faktor: 0.4,
        andel_av_tomt_pct: 20.0,
        eko_effektiv_area_m2: 80,
        bidrag_gyf: 0.08,
      },
    ],
    flaggade_celler: 20,
    flaggad_area_m2: 20,
    flaggad_andel_pct: 2.0,
    okanda_koder: [0],
    ogiltiga_celler: 0,
    varningar: ["Okända/omappade markslagskoder inom tomtgränsen (0)."],
  },
  jamforelse: {
    delta_gyf: 0.16,
    delta_eko_effektiv_area_m2: 160,
    uppfyller_mal: true,
    uppfyller_mal_fore: false,
    uppfyller_mal_efter: true,
    saknas_till_mal_fore_m2: 120,
    saknas_till_mal_m2: 0,
    varningar: [],
  },
  delfaktorer: [
    {
      faktor_id: "vegetation_mark",
      namn: "Vegetation på mark",
      faktor: 1.0,
      antal_celler: 560,
      area_m2: 560,
      andel_av_tomt_pct: 56.0,
      eko_effektiv_area_m2: 560,
      bidrag_gyf: 0.56,
    },
    {
      faktor_id: "hardgjord",
      namn: "Hårdgjord tät yta",
      faktor: 0.0,
      antal_celler: 420,
      area_m2: 420,
      andel_av_tomt_pct: 42.0,
      eko_effektiv_area_m2: 0,
      bidrag_gyf: 0.0,
    },
  ],
  tillagg: [
    {
      tillagg_id: "trad_krontackning",
      namn: "Träd (krontäckning)",
      area_m2: 200,
      faktor: 0.4,
      andel_av_tomt_pct: 20.0,
      eko_effektiv_area_m2: 80,
      bidrag_gyf: 0.08,
    },
  ],
  flaggad_area_m2: 20,
  flaggad_andel_pct: 2.0,
  varningar: ["Okända/omappade markslagskoder inom tomtgränsen (0)."],
};

const EXEMPEL_LAGER = (roll: "utci_c" | "t_mrt_c", typ: "bas" | "scenario" | "skillnad"): VarmestressLagerRef => ({
  id: `vs_test_${roll}_${typ}`,
  roll,
  typ,
  etikett: roll === "utci_c" ? "Värmestress (UTCI)" : "Medelstrålningstemperatur (T_mrt)",
  enhet: "°C",
  farg: typ === "skillnad" ? "skillnad" : "varme",
  skala: typ === "skillnad" ? { min: -4, max: 4 } : { min: 25, max: 42 },
  teckenforklaring:
    typ === "skillnad"
      ? { typ: "divergerande", min: -4, max: 4, troskel: 0.05, farg_minskning: "#2b5d8c", farg_okning: "#b4462b" }
      : { typ: "kontinuerlig", min: 25, max: 42, stopp: [...(PALETTER.varme ?? [])] },
  utbredning: { x_min: 100, y_min: 200, x_max: 400, y_max: 500 },
  pixelstorlek_m: 1,
  bredd_px: 300,
  hojd_px: 300,
  tak_maskat: true,
  png: `/api/kartverktyg/demokvarteret/varmestress/vs_test_${roll}_${typ}.png`,
});

const EXEMPEL_VARMESTRESS: VarmestressAnalysSvar = {
  format: "floden-varmestress-webb/v1",
  omrade_id: "demokvarteret",
  scenario: "gron-atgard",
  metodstatus: "skiss",
  parametrar: { ...STANDARD_VARMESTRESS_PARAM },
  geometri: {
    cellstorlek_m: 1,
    utbredning: { x_min: 100, y_min: 200, x_max: 400, y_max: 500 },
    svf_medel_fore: 0.78,
    solbelyst_andel_fore_pct: 64,
    trad_andel_fore_pct: 12.5,
  },
  utci_medel_c: 34.2,
  utci_max_c: 39.8,
  tmrt_medel_c: 52.1,
  tmrt_max_c: 64.5,
  utanfor_giltighetsomrade_pct: 3.5,
  stressklasser: [
    { klass_id: 0, namn_sv: "Ingen termisk stress", nedre_grans_c: 9, ovre_grans_c: 26, andel: 0.1, andel_pct: 10 },
    { klass_id: 1, namn_sv: "Måttlig värmestress", nedre_grans_c: 26, ovre_grans_c: 32, andel: 0.25, andel_pct: 25 },
    { klass_id: 2, namn_sv: "Stark värmestress", nedre_grans_c: 32, ovre_grans_c: 38, andel: 0.5, andel_pct: 50 },
    { klass_id: 3, namn_sv: "Mycket stark värmestress", nedre_grans_c: 38, ovre_grans_c: 46, andel: 0.15, andel_pct: 15 },
  ],
  fore: {
    utci_medel_c: 36.1,
    utci_max_c: 40.2,
    tmrt_medel_c: 56.4,
    tmrt_max_c: 65.0,
    utanfor_giltighetsomrade_pct: 0,
    stressklasser: [],
    varningar: [],
  },
  efter: {
    utci_medel_c: 34.2,
    utci_max_c: 39.8,
    tmrt_medel_c: 52.1,
    tmrt_max_c: 64.5,
    utanfor_giltighetsomrade_pct: 3.5,
    stressklasser: [],
    varningar: [],
  },
  jamforelse: {
    delta_utci_medel_c: -1.9,
    delta_tmrt_medel_c: -4.3,
    delta_andel_stark_varmestress_pct: -12.0,
    varningar: [],
  },
  lager: {
    utci_c_fore: EXEMPEL_LAGER("utci_c", "bas"),
    t_mrt_c_fore: EXEMPEL_LAGER("t_mrt_c", "bas"),
    utci_c_efter: EXEMPEL_LAGER("utci_c", "scenario"),
    t_mrt_c_efter: EXEMPEL_LAGER("t_mrt_c", "scenario"),
    utci_c_skillnad: EXEMPEL_LAGER("utci_c", "skillnad"),
    t_mrt_c_skillnad: EXEMPEL_LAGER("t_mrt_c", "skillnad"),
  },
  varningar: ["Testvarning för giltighetsgräns."],
};

test("gyfAnalysUrl och varmestressAnalysUrl bygger korrekta GET-query-parametrar", () => {
  const gUrl = new URL(gyfAnalysUrl("proj-1", "malmo", "forslag-a"), "http://localhost");
  assert.equal(gUrl.pathname, "/api/analys/gyf");
  assert.equal(gUrl.searchParams.get("omrade_id"), "proj-1");
  assert.equal(gUrl.searchParams.get("profil"), "malmo");
  assert.equal(gUrl.searchParams.get("scenario"), "forslag-a");

  const vUrl = new URL(
    varmestressAnalysUrl("proj-1", { t_luft_c: 32, relativ_fuktighet_pct: 50, vind_10m_m_s: 2.0 }, "forslag-a"),
    "http://localhost",
  );
  assert.equal(vUrl.pathname, "/api/analys/varmestress");
  assert.equal(vUrl.searchParams.get("omrade_id"), "proj-1");
  assert.equal(vUrl.searchParams.get("t_luft_c"), "32");
  assert.equal(vUrl.searchParams.get("relativ_fuktighet_pct"), "50");
  assert.equal(vUrl.searchParams.get("vind_10m_m_s"), "2");
  assert.equal(vUrl.searchParams.get("scenario"), "forslag-a");
});

test("valideraVarmestressParam och tolkaSvensktTal validerar gränser och svenskt decimalkomma", () => {
  assert.equal(tolkaSvensktTal("1,5"), 1.5);
  assert.equal(tolkaSvensktTal(" 30.0 "), 30);
  assert.equal(tolkaSvensktTal(""), null);
  assert.equal(valideraVarmestressParam({ ...STANDARD_VARMESTRESS_PARAM }), null);
  assert.match(valideraVarmestressParam({ ...STANDARD_VARMESTRESS_PARAM, relativ_fuktighet_pct: 120 }) ?? "", /0 och 100/);
  assert.match(valideraVarmestressParam({ ...STANDARD_VARMESTRESS_PARAM, vind_10m_m_s: -0.5 }) ?? "", /Vindhastighet/);
  assert.match(valideraVarmestressParam({ ...STANDARD_VARMESTRESS_PARAM, solhojd_grad: 95 }) ?? "", /Solhöjd/);
});

test("gyfPanelHtml visar SKISS-märkning, kommunprofiler, GYF mot 0,60, delta, delfaktorer och flaggad yta", () => {
  assert.deepEqual(
    GYF_PROFILER.map((p) => p.namn),
    ["Stockholm", "Göteborg", "Malmö"],
  );
  const html = gyfPanelHtml({
    profil: "stockholm",
    laddar: false,
    fel: null,
    svar: EXEMPEL_GYF,
    scenario: "gron-atgard",
  });
  assert.match(html, /Grönytefaktor \(GYF\)/);
  assert.match(html, />SKISS</);
  assert.match(html, /Stockholm/);
  assert.match(html, /Göteborg/);
  assert.match(html, /Malmö/);
  assert.match(html, /0,64/);
  assert.match(html, /riktmärke 0,60/);
  assert.match(html, /Uppfyller riktmärket 0,60/);
  assert.match(html, /\+0,160/);
  assert.match(html, /Vegetation på mark/);
  assert.match(html, /Träd \(krontäckning\)/);
  assert.match(html, /Flaggad \/ osäker yta:.*20,0 m²/);
  assert.match(html, /Okända\/omappade markslagskoder/);
});

test("varmestressPanelHtml visar SKISS, väderfält, svensk stressklassfördelning, kylning och lagerknappar", () => {
  const html = varmestressPanelHtml({
    param: { ...STANDARD_VARMESTRESS_PARAM },
    laddar: false,
    fel: null,
    svar: EXEMPEL_VARMESTRESS,
    scenario: "gron-atgard",
    aktivtLagerRoll: "utci_c",
  });
  assert.match(html, /Värmestress \(UTCI/);
  assert.match(html, />SKISS</);
  assert.match(html, /id="ar-vs-tluft"/);
  assert.match(html, /id="ar-vs-rh"/);
  assert.match(html, /id="ar-vs-vind"/);
  assert.match(html, /id="ar-vs-solhojd"/);
  assert.match(html, /34,2 °C/);
  assert.match(html, /52,1 °C/);
  assert.match(html, /−1,90 °C.*svalare/);
  assert.match(html, /−4,30 °C/);
  assert.match(html, /Stark värmestress/);
  assert.match(html, /Mycket stark värmestress/);
  assert.match(html, /3,5 %.*giltighetsintervall/);
  assert.match(html, /data-vs-lager="utci_c" aria-pressed="true"/);
  assert.match(html, /data-vs-lager="t_mrt_c" aria-pressed="false"/);
});

test("valjVarmestressLager, varmelagerTeckenHtml och gyfUtciPanelerHtml hanterar före/efter/skillnad", () => {
  assert.equal(valjVarmestressLager(EXEMPEL_VARMESTRESS, "utci_c", "nulage")?.typ, "bas");
  assert.equal(valjVarmestressLager(EXEMPEL_VARMESTRESS, "utci_c", "efter")?.typ, "scenario");
  assert.equal(valjVarmestressLager(EXEMPEL_VARMESTRESS, "t_mrt_c", "skillnad")?.typ, "skillnad");
  assert.equal(valjVarmestressLager(EXEMPEL_VARMESTRESS, null, "nulage"), null);

  const teckenKont = varmelagerTeckenHtml(EXEMPEL_LAGER("utci_c", "bas"));
  assert.match(teckenKont, /Värmestress \(UTCI\) \(°C\) · skiss/);
  assert.match(teckenKont, /tak, inte mark/);

  const teckenDiff = varmelagerTeckenHtml(EXEMPEL_LAGER("t_mrt_c", "skillnad"));
  assert.match(teckenDiff, /Blått = svalare efter åtgärd/);

  const båda = gyfUtciPanelerHtml(
    { profil: "goteborg", laddar: false, fel: null, svar: null, scenario: null },
    { param: { ...STANDARD_VARMESTRESS_PARAM }, laddar: false, fel: null, svar: null, scenario: null, aktivtLagerRoll: null },
  );
  assert.match(båda, /id="ar-gyf-panel"/);
  assert.match(båda, /id="ar-vs-panel"/);
});

test("analyslager har varme-palett och etiketter för utci_c/t_mrt_c; VektorLagerRef.id är valfri", () => {
  assert.equal(PALETTER.varme?.length, 5);
  assert.equal(ROLL_ETIKETTER.utci_c?.enhet, "°C");
  assert.equal(ROLL_ETIKETTER.t_mrt_c?.enhet, "°C");
  assert.deepEqual(rollEtikett("utci_c", "utci_c", ""), { etikett: "Värmestress (UTCI)", enhet: "°C" });
  assert.deepEqual(rollEtikett("t_mrt_c", "t_mrt_c", ""), {
    etikett: "Medelstrålningstemperatur (T_mrt)",
    enhet: "°C",
  });
  // Verifiera på typnivå att VektorLagerRef inte kräver `id` (Våg 6-notis)
  const vref: VektorLagerRef = {
    analys: "skyfall",
    scenario: "fore",
    ruta: null,
    roll: "rannsten_flode",
    etikett: "Rännstenar (1D-flöde)",
    enhet: "m³/s",
    fil: "rannsten_flode.geojson",
    url: "/api/x",
    vektor_url: "/api/v",
  };
  assert.equal(vref.id, undefined);
});
