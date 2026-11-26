/*
 * Korsvågs frontend-kontraktstester för Våg 6 (U14 2D + 1D-rännsten & B11 sektion)
 * och Våg 7 (U15 Värmestress UTCI/T_mrt & U16 Svensk GYF).
 *
 * Körs direkt i Node via `npm test` utan DOM/WebGL.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import type { LagerPost, ResultatSvar, VektorLagerRef } from "../fragor-typer.ts";
import type { KorningPost, KorningRegnInfo, SkyfallMetod } from "../karta/kartapi.ts";
import type { ProjektVy } from "../types.ts";
import { lagerVal, PALETTER, ROLL_ETIKETTER, rollEtikett } from "./analyslager.ts";
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
import { startaKorning, startaLageIdag, type KorningApi } from "./korning.ts";
import {
  medRegnForval,
  regnSignatur,
  regnSignaturFranKorningar,
  regnValHtml,
  STANDARD_REGNVAL,
  tillRegnParam,
  valdRegnText,
} from "./regnval.ts";
import { sektionVattenLager } from "./sektion.ts";
import { aterladdaSparadeVyer, bedomInstallningsStatus, tillstandFor } from "./tillstand.ts";

const FLODE_MAPP = path.dirname(fileURLToPath(import.meta.url));
const WEBB_SRC_MAPP = path.dirname(FLODE_MAPP);
const ARBETSYTA_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.ts"), "utf-8");
const GYF_UTCI_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "gyf-utci.ts"), "utf-8");
const KARTAPI_KALLTEXT = fs.readFileSync(path.join(WEBB_SRC_MAPP, "karta", "kartapi.ts"), "utf-8");

function skapaVattenVy(id: string, lageIdag: boolean): ProjektVy {
  return {
    id,
    avsnitt: "vatten",
    rubrik: "Skyfall",
    korning_id: `k-${id}`,
    fore_lager: "skyfall.fore.max_djup",
    efter_lager: lageIdag ? null : "skyfall.ritat.max_djup",
    skillnad_lager: lageIdag ? "skyfall.fore.max_djup" : "skillnad.skyfall.max_djup",
    efter_namn: lageIdag ? "Nuläge" : "Förslag",
    andring: "",
    mattnamn: "Vattendjup",
    lage_idag: lageIdag,
  };
}

const REGN_2D_STD: KorningRegnInfo = {
  aterkomsttid_ar: 100,
  varaktighet_min: 60,
  klimatfaktor: 1.25,
  regn_mm: 68.5,
  berakningstyp: "dynamisk",
  regntyp: "block",
  kalla: "stockholmsregn-2024",
  ar_standard: true,
  skyfall_metod: "2d",
};

const EXEMPEL_LAGER = (
  roll: "utci_c" | "t_mrt_c",
  typ: "bas" | "scenario" | "skillnad",
): VarmestressLagerRef => ({
  id: `vs_v8_${roll}_${typ}`,
  roll,
  typ,
  etikett: roll === "utci_c" ? "Värmestress (UTCI)" : "Medelstrålningstemperatur (T_mrt)",
  enhet: "°C",
  farg: typ === "skillnad" ? "skillnad" : "varme",
  skala: typ === "skillnad" ? { min: -4, max: 4 } : { min: 25, max: 42 },
  teckenforklaring:
    typ === "skillnad"
      ? {
          typ: "divergerande",
          min: -4,
          max: 4,
          troskel: 0.05,
          farg_minskning: "#2b5d8c",
          farg_okning: "#b4462b",
        }
      : { typ: "kontinuerlig", min: 25, max: 42, stopp: [...(PALETTER.varme ?? [])] },
  utbredning: { x_min: 150400, y_min: 6580400, x_max: 150520, y_max: 6580460 },
  pixelstorlek_m: 2,
  bredd_px: 60,
  hojd_px: 30,
  tak_maskat: true,
  png: `/api/kartverktyg/proj-v8/varmestress/vs_v8_${roll}_${typ}.png`,
});

const EXEMPEL_GYF_SVAR: GyfAnalysSvar = {
  format: "floden-gyf-webb/v1",
  omrade_id: "proj-v8",
  profil: "stockholm",
  profil_namn: "Stockholms stad – Grönytefaktor för kvartersmark",
  kommun: "Stockholm",
  riktmarke: 0.6,
  scenario: "gronyta-trad",
  metodstatus: "skiss",
  fore: {
    profil_id: "stockholm",
    profil_namn: "Stockholms stad – Grönytefaktor för kvartersmark",
    kommun: "Stockholm",
    metodstatus: "skiss",
    riktmarke: 0.6,
    cellstorlek_m: 2.0,
    tomt_celler: 1200,
    tomt_area_m2: 4800,
    grund_eko_effektiv_area_m2: 2160,
    tillagg_eko_effektiv_area_m2: 0,
    eko_effektiv_area_m2: 2160,
    gyf: 0.45,
    uppfyller_mal: false,
    saknas_till_mal_m2: 720,
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
    cellstorlek_m: 2.0,
    tomt_celler: 1200,
    tomt_area_m2: 4800,
    grund_eko_effektiv_area_m2: 2880,
    tillagg_eko_effektiv_area_m2: 240,
    eko_effektiv_area_m2: 3120,
    gyf: 0.65,
    uppfyller_mal: true,
    saknas_till_mal_m2: 0,
    delfaktorer: [
      {
        faktor_id: "vegetation_mark",
        namn: "Vegetation på mark",
        faktor: 1.0,
        antal_celler: 720,
        area_m2: 2880,
        andel_av_tomt_pct: 60.0,
        eko_effektiv_area_m2: 2880,
        bidrag_gyf: 0.6,
      },
    ],
    tillagg: [
      {
        tillagg_id: "trad_krontackning",
        namn: "Träd (krontäckning)",
        area_m2: 600,
        faktor: 0.4,
        andel_av_tomt_pct: 12.5,
        eko_effektiv_area_m2: 240,
        bidrag_gyf: 0.05,
      },
    ],
    flaggade_celler: 0,
    flaggad_area_m2: 0,
    flaggad_andel_pct: 0,
    okanda_koder: [],
    ogiltiga_celler: 0,
    varningar: [],
  },
  jamforelse: {
    delta_gyf: 0.2,
    delta_eko_effektiv_area_m2: 960,
    uppfyller_mal: true,
    uppfyller_mal_fore: false,
    uppfyller_mal_efter: true,
    saknas_till_mal_fore_m2: 720,
    saknas_till_mal_m2: 0,
    varningar: [],
  },
  delfaktorer: [
    {
      faktor_id: "vegetation_mark",
      namn: "Vegetation på mark",
      faktor: 1.0,
      antal_celler: 720,
      area_m2: 2880,
      andel_av_tomt_pct: 60.0,
      eko_effektiv_area_m2: 2880,
      bidrag_gyf: 0.6,
    },
  ],
  tillagg: [
    {
      tillagg_id: "trad_krontackning",
      namn: "Träd (krontäckning)",
      area_m2: 600,
      faktor: 0.4,
      andel_av_tomt_pct: 12.5,
      eko_effektiv_area_m2: 240,
      bidrag_gyf: 0.05,
    },
  ],
  flaggad_area_m2: 0,
  flaggad_andel_pct: 0,
  varningar: [],
};

const EXEMPEL_VS_SVAR: VarmestressAnalysSvar = {
  format: "floden-varmestress-webb/v1",
  omrade_id: "proj-v8",
  scenario: "gronyta-trad",
  metodstatus: "skiss",
  parametrar: { ...STANDARD_VARMESTRESS_PARAM },
  geometri: {
    cellstorlek_m: 2,
    utbredning: { x_min: 150400, y_min: 6580400, x_max: 150520, y_max: 6580460 },
    svf_medel_fore: 0.81,
    solbelyst_andel_fore_pct: 68,
    trad_andel_fore_pct: 15,
  },
  utci_medel_c: 33.4,
  utci_max_c: 38.9,
  tmrt_medel_c: 50.8,
  tmrt_max_c: 63.2,
  utanfor_giltighetsomrade_pct: 0,
  stressklasser: [
    { klass_id: 1, namn_sv: "Måttlig värmestress", nedre_grans_c: 26, ovre_grans_c: 32, andel: 0.3, andel_pct: 30 },
    { klass_id: 2, namn_sv: "Stark värmestress", nedre_grans_c: 32, ovre_grans_c: 38, andel: 0.6, andel_pct: 60 },
    { klass_id: 3, namn_sv: "Mycket stark värmestress", nedre_grans_c: 38, ovre_grans_c: 46, andel: 0.1, andel_pct: 10 },
  ],
  fore: {
    utci_medel_c: 35.2,
    utci_max_c: 39.6,
    tmrt_medel_c: 55.1,
    tmrt_max_c: 64.0,
    utanfor_giltighetsomrade_pct: 0,
    stressklasser: [],
    varningar: [],
  },
  efter: {
    utci_medel_c: 33.4,
    utci_max_c: 38.9,
    tmrt_medel_c: 50.8,
    tmrt_max_c: 63.2,
    utanfor_giltighetsomrade_pct: 0,
    stressklasser: [],
    varningar: [],
  },
  jamforelse: {
    delta_utci_medel_c: -1.8,
    delta_tmrt_medel_c: -4.3,
    delta_andel_stark_varmestress_pct: -10,
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
  varningar: [],
};

// ---------------------------------------------------------------------------
// 1. Våg 6: skyfallMetod i signatur, körning, F5-återladdning och UI
// ---------------------------------------------------------------------------

test("Våg 6 kontrakt: skyfallMetod ingår i signatur, köranrop, F5-synk och metodväljare", async () => {
  const sigGraf = regnSignatur(STANDARD_REGNVAL, null, "graf");
  const sig2d = regnSignatur(STANDARD_REGNVAL, null, "2d");
  assert.notEqual(sigGraf, sig2d);
  assert.equal(JSON.parse(sig2d).skyfallMetod, "2d");
  assert.equal(JSON.parse(sigGraf).skyfallMetod, "graf");

  // Regnparam i 2D-läge behåller snabbval + regntyp men utan statisk/dynamisk graf-flagga
  const p10 = tillRegnParam(medRegnForval(STANDARD_REGNVAL, "10ar"), "2d");
  assert.deepEqual(p10, {
    aterkomsttid_ar: 10,
    varaktighet_min: 60,
    klimatfaktor: 1.25,
    regntyp: "cds",
  });
  assert.match(valdRegnText(STANDARD_REGNVAL, null, null, "2d"), /2D lokal tröghet/);
  const html2d = regnValHtml(STANDARD_REGNVAL, null, null, null, false, "2d");
  assert.match(html2d, /data-skyfall-metod="2d"[^>]*aria-pressed="true"/);
  assert.match(html2d, /hastighet, fara \(h·v\) och 1D-rännsten \(skiss\)/);

  // KorningPost.skyfall_metod och regn.skyfall_metod rekonstruerar exakt 2D-signatur
  const kPost: KorningPost = {
    mall: "vatten",
    status: { status: "klar" },
    vyer: [skapaVattenVy("v8-2d", true)],
    regn: REGN_2D_STD,
    skyfall_metod: "2d",
    avtappning_mm_h: null,
    sol: null,
  };
  assert.equal(regnSignaturFranKorningar([kPost]), sig2d);

  // F5-återladdning och vidarebefordran till både läget idag och förslag
  const t = tillstandFor("v6-v7-kontrakt-skyfall");
  t.scenario = { slug: "forslag-v8", namn: "Förslag V8" };
  t.regn = { ...STANDARD_REGNVAL };
  t.skyfallMetod = "2d";

  await aterladdaSparadeVyer(t, {
    lageIdagStatus: async () => ({ korningar: [kPost] }),
    scenarioStatus: async () => ({ korningar: [] }),
  });
  assert.equal(t.lageRegnSignatur, sig2d);
  assert.equal(bedomInstallningsStatus(t, "vatten").regnUtdaterat, false);

  t.skyfallMetod = "graf";
  assert.equal(bedomInstallningsStatus(t, "vatten").regnUtdaterat, true);

  t.skyfallMetod = "2d";
  const mottagna: Array<SkyfallMetod | null | undefined> = [];
  const api: KorningApi = {
    korLageIdag: async (_p, _a, _avt, _r, _s, metod) => {
      mottagna.push(metod);
      return { lage: "idag", fragor: [] };
    },
    korScenario: async (_p, _slug, _a, _avt, _r, _s, metod) => {
      mottagna.push(metod);
      return { slug: "forslag-v8", fragor: [] };
    },
    lageIdagStatus: async () => ({ korningar: [kPost] }),
    scenarioStatus: async () => ({
      korningar: [{ ...kPost, vyer: [skapaVattenVy("v8-2d-sc", false)] }],
    }),
  };
  await startaLageIdag(t, ["vatten.skyfall"], api);
  await startaKorning(t, ["vatten.skyfall"], api);
  assert.deepEqual(mottagna, ["2d", "2d"]);
  assert.equal(t.lageRegnSignatur, sig2d);
  assert.equal(t.ritRegnSignatur, sig2d);
});

// ---------------------------------------------------------------------------
// 2. Korsvågs B11-kontrakt: sektionVattenLager skyddar Våg 6 + Våg 7-roller
// ---------------------------------------------------------------------------

test("B11 korsvågskontrakt: sektionVattenLager tillåter BARA max_djup och avvisar Våg 6 + Våg 7-roller", () => {
  const blandadeLager = [
    { id: "skyfall.fore.max_djup", roll: "max_djup", typ: "bas" },
    { id: "skyfall.ritat.max_djup", roll: "max_djup", typ: "scenario" },
    { id: "skillnad.skyfall.max_djup", roll: "max_djup", typ: "skillnad" },
    { id: "skyfall.fore.fara", roll: "fara", typ: "bas" },
    { id: "skyfall.fore.hastighet", roll: "hastighet", typ: "bas" },
    { id: "skyfall.fore.ankomsttid", roll: "ankomsttid", typ: "bas" },
    { id: "mikroklimat.fore.utci_c", roll: "utci_c", typ: "bas" },
    { id: "mikroklimat.fore.t_mrt_c", roll: "t_mrt_c", typ: "bas" },
    { id: "buller.fore.ekvivalent_ljudniva", roll: "ekvivalent_ljudniva", typ: "bas" },
  ];

  // Endast bas + scenario av max_djup passerar
  assert.deepEqual(
    sektionVattenLager(blandadeLager, {
      fore_lager: "skyfall.fore.max_djup",
      efter_lager: "skyfall.ritat.max_djup",
    }),
    { fore_lager: "skyfall.fore.max_djup", efter_lager: "skyfall.ritat.max_djup" },
  );

  // Samtliga icke-max_djup-roller (Våg 6 2D + Våg 7 UTCI/T_mrt/buller) och skillnad blockeras
  for (const nekadId of [
    "skillnad.skyfall.max_djup",
    "skyfall.fore.fara",
    "skyfall.fore.hastighet",
    "skyfall.fore.ankomsttid",
    "mikroklimat.fore.utci_c",
    "mikroklimat.fore.t_mrt_c",
    "buller.fore.ekvivalent_ljudniva",
  ]) {
    assert.deepEqual(
      sektionVattenLager(blandadeLager, { fore_lager: nekadId, efter_lager: nekadId }),
      { fore_lager: null, efter_lager: null },
      `Rollen ${nekadId} ska aldrig skickas som vattenlager till /sektion`,
    );
  }

  // Blandad vy: giltig fore_lager bevaras medan ogiltig efter_lager nollställs
  assert.deepEqual(
    sektionVattenLager(blandadeLager, {
      fore_lager: "skyfall.fore.max_djup",
      efter_lager: "mikroklimat.fore.utci_c",
    }),
    { fore_lager: "skyfall.fore.max_djup", efter_lager: null },
  );

  assert.match(ARBETSYTA_KALLTEXT, /sektionVattenLager\(r\?\.lager,\s*vy\)/);
});

// ---------------------------------------------------------------------------
// 3. Våg 7: GYF + Värmestress URL, RBAC (skrivlös GET för lasare) & SKISS
// ---------------------------------------------------------------------------

test("Våg 7 kontrakt: GYF och Värmestress använder skrivlös GET (lasare-RBAC) och tydlig SKISS-märkning", () => {
  // Query-URL:er kodar omrade_id, profil/väder och valfritt scenario utan sidskrivning
  const gUrl = new URL(gyfAnalysUrl("proj/v8 med mellanslag", "goteborg", "gronyta-trad"), "http://localhost");
  assert.equal(gUrl.pathname, "/api/analys/gyf");
  assert.equal(gUrl.searchParams.get("omrade_id"), "proj/v8 med mellanslag");
  assert.equal(gUrl.searchParams.get("profil"), "goteborg");
  assert.equal(gUrl.searchParams.get("scenario"), "gronyta-trad");

  const gUtanSc = new URL(gyfAnalysUrl("proj-v8", "malmo", null), "http://localhost");
  assert.equal(gUtanSc.searchParams.get("scenario"), null);

  const vUrl = new URL(
    varmestressAnalysUrl(
      "proj-v8",
      { t_luft_c: 31.5, relativ_fuktighet_pct: 40, vind_10m_m_s: 1.5, solhojd_grad: 52 },
      "gronyta-trad",
    ),
    "http://localhost",
  );
  assert.equal(vUrl.pathname, "/api/analys/varmestress");
  assert.equal(vUrl.searchParams.get("omrade_id"), "proj-v8");
  assert.equal(vUrl.searchParams.get("t_luft_c"), "31.5");
  assert.equal(vUrl.searchParams.get("relativ_fuktighet_pct"), "40");
  assert.equal(vUrl.searchParams.get("vind_10m_m_s"), "1.5");
  assert.equal(vUrl.searchParams.get("solhojd_grad"), "52");
  assert.equal(vUrl.searchParams.get("scenario"), "gronyta-trad");

  // RBAC-kontrakt i källkod: Steg 2-panelerna anropar skrivlös GET (lasJson utan POST-mutation),
  // medan kartapi.ts tillhandahåller både skrivlös GET (hamta*) och explicit POST (berakna*).
  assert.match(GYF_UTCI_KALLTEXT, /lasJson<GyfAnalysSvar>\(\s*gyfAnalysUrl\(/);
  assert.match(GYF_UTCI_KALLTEXT, /lasJson<VarmestressAnalysSvar>\(\s*varmestressAnalysUrl\(/);
  assert.doesNotMatch(GYF_UTCI_KALLTEXT, /method:\s*"POST"/);
  assert.match(KARTAPI_KALLTEXT, /export const hamtaGyfAnalys[\s\S]*lasJson\(gyfAnalysUrl\(/);
  assert.match(KARTAPI_KALLTEXT, /export const beraknaGyfAnalys[\s\S]*post\("\/api\/analys\/gyf"/);
  assert.match(KARTAPI_KALLTEXT, /export const hamtaVarmestressAnalys[\s\S]*lasJson\(varmestressAnalysUrl\(/);
  assert.match(KARTAPI_KALLTEXT, /export const beraknaVarmestressAnalys[\s\S]*post\("\/api\/analys\/varmestress"/);
  assert.match(ARBETSYTA_KALLTEXT, /kopplaGyfUtciPaneler\(/);

  // SKISS-märkning, kommunprofiler, takmaskning och svensk talvalidering
  assert.deepEqual(
    GYF_PROFILER.map((p) => p.id),
    ["stockholm", "goteborg", "malmo"],
  );
  assert.equal(tolkaSvensktTal("29,5"), 29.5);
  assert.equal(valideraVarmestressParam({ ...STANDARD_VARMESTRESS_PARAM }), null);
  assert.ok(valideraVarmestressParam({ ...STANDARD_VARMESTRESS_PARAM, relativ_fuktighet_pct: 101 }));
  assert.ok(valideraVarmestressParam({ ...STANDARD_VARMESTRESS_PARAM, vind_10m_m_s: -0.1 }));

  const gyfHtml = gyfPanelHtml({
    profil: "stockholm",
    laddar: false,
    fel: null,
    svar: EXEMPEL_GYF_SVAR,
    scenario: "gronyta-trad",
  });
  assert.match(gyfHtml, />SKISS</);
  assert.match(gyfHtml, /0,65/);
  assert.match(gyfHtml, /\+0,200/);

  const vsHtml = varmestressPanelHtml({
    param: { ...STANDARD_VARMESTRESS_PARAM },
    laddar: false,
    fel: null,
    svar: EXEMPEL_VS_SVAR,
    scenario: "gronyta-trad",
    aktivtLagerRoll: "utci_c",
  });
  assert.match(vsHtml, />SKISS</);
  assert.match(vsHtml, /33,4 °C/);
  assert.match(vsHtml, /−1,80 °C.*svalare/);
  assert.match(vsHtml, /data-vs-lager="utci_c" aria-pressed="true"/);

  const kombinerad = gyfUtciPanelerHtml(
    { profil: "stockholm", laddar: false, fel: null, svar: EXEMPEL_GYF_SVAR, scenario: "gronyta-trad" },
    {
      param: { ...STANDARD_VARMESTRESS_PARAM },
      laddar: false,
      fel: null,
      svar: EXEMPEL_VS_SVAR,
      scenario: "gronyta-trad",
      aktivtLagerRoll: "t_mrt_c",
    },
  );
  assert.match(kombinerad, /id="ar-gyf-panel"/);
  assert.match(kombinerad, /id="ar-vs-panel"/);
  assert.equal((kombinerad.match(/>SKISS</g) ?? []).length, 2);

  const aktivtLager = valjVarmestressLager(EXEMPEL_VS_SVAR, "utci_c", "efter");
  assert.equal(aktivtLager?.typ, "scenario");
  assert.equal(aktivtLager?.tak_maskat, true);
  assert.match(varmelagerTeckenHtml(aktivtLager as VarmestressLagerRef), /· skiss/);
  assert.match(varmelagerTeckenHtml(aktivtLager as VarmestressLagerRef), /tak, inte mark/);
});

// ---------------------------------------------------------------------------
// 4. Korsvågs VektorLagerRef utan `id` samt palett-/rollsynk Våg 6 + Våg 7
// ---------------------------------------------------------------------------

test("VektorLagerRef kräver inte id (rannsten_flode) och analyslager synkar Våg 6 + Våg 7-roller", () => {
  // Backendens `_vektorlager()` levererar objekt utan `id`; kontraktet får aldrig kräva `id`.
  const rannstenRef: VektorLagerRef = {
    analys: "skyfall",
    scenario: "bas",
    ruta: "mm_150200_6580200",
    roll: "rannsten_flode",
    etikett: "Rännstenar (1D-flöde)",
    enhet: "m³/s",
    fil: "rannsten_flode.geojson",
    url: "/api/korningar/k-v8/fil/skyfall/bas/mm_150200_6580200/rannsten_flode.geojson",
    vektor_url: "/api/korningar/k-v8/vektor/bas/rannsten_flode",
  };
  const delvisResultat: Pick<ResultatSvar, "vektorlager"> = {
    vektorlager: [rannstenRef],
  };
  assert.equal(delvisResultat.vektorlager?.[0]?.id, undefined);
  assert.equal(delvisResultat.vektorlager?.[0]?.roll, "rannsten_flode");
  assert.equal("id" in (delvisResultat.vektorlager?.[0] ?? {}), false);

  // Paletter och svenska etiketter för Våg 6 (fara, ankomsttid, rannsten_flode) + Våg 7 (utci_c, t_mrt_c)
  assert.equal(PALETTER.fara?.length, 5);
  assert.equal(PALETTER.varme?.length, 5);
  for (const roll of ["fara", "ankomsttid", "rannsten_flode", "utci_c", "t_mrt_c"] as const) {
    assert.ok(ROLL_ETIKETTER[roll], `ROLL_ETIKETTER saknar rollen ${roll}`);
  }
  assert.deepEqual(rollEtikett("fara"), { etikett: "Fara (djup × hastighet)", enhet: "m²/s" });
  assert.deepEqual(rollEtikett("utci_c"), { etikett: "Värmestress (UTCI)", enhet: "°C" });
  assert.deepEqual(rollEtikett("t_mrt_c"), { etikett: "Medelstrålningstemperatur (T_mrt)", enhet: "°C" });

  // Sortering av Våg 6-roller i Skyfall-fliken: max_djup alltid först
  const lp = (id: string, roll: string, primar = false): LagerPost =>
    ({
      id,
      analys: "skyfall",
      roll,
      scenario: "bas",
      typ: "bas",
      etikett: roll,
      enhet: "",
      doman: "vatten",
      primar,
      kontroll: false,
      harledd: roll === "fara",
    }) as LagerPost;
  const val = lagerVal(
    [
      lp("skyfall.bas.fara", "fara"),
      lp("skyfall.bas.hastighet", "hastighet"),
      lp("skyfall.bas.max_djup", "max_djup", true),
      lp("skyfall.bas.ankomsttid", "ankomsttid"),
    ],
    { fore_lager: "skyfall.bas.max_djup", efter_lager: null },
    "vatten",
  );
  assert.equal(val[0]?.roll, "max_djup");
  assert.deepEqual(
    val.map((v) => v.roll).sort(),
    ["ankomsttid", "fara", "hastighet", "max_djup"],
  );
});
