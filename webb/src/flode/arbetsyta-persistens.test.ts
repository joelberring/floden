/* Körs med `npm test`. Persistens av sparade nuläges-/förslagsvyer och hopfällbara kartmenyer. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { KorningPost, KorningRegnInfo, KorningSolInfo, ScenarioPost } from "../karta/kartapi.ts";
import type { ProjektVy } from "../types.ts";
import { medRegnForval, regnSignatur, STANDARD_REGNVAL } from "./regnval.ts";
import { solSignatur, STANDARD_SOLVAL } from "./solval.ts";
import {
  aterladdaSparadeVyer,
  bedomInstallningsStatus,
  FORVAL_UI_LAGE,
  lasArbetsytaUiLage,
  sparaArbetsytaUiLage,
  tillstandFor,
  type ArbetsytaUiLage,
} from "./tillstand.ts";

function skapaVy(id: string, lageIdag: boolean, avsnitt: ProjektVy["avsnitt"] = "vatten"): ProjektVy {
  return {
    id,
    avsnitt,
    korning_id: `k-${id}`,
    fore_lager: avsnitt === "vatten" ? "vatten.fore.max_djup" : "dagsljus.fore.soltimmar",
    efter_lager: lageIdag
      ? null
      : avsnitt === "vatten"
        ? "vatten.efter.max_djup"
        : "dagsljus.efter.soltimmar",
    skillnad_lager: lageIdag
      ? avsnitt === "vatten"
        ? "vatten.fore.max_djup"
        : "dagsljus.fore.soltimmar"
      : avsnitt === "vatten"
        ? "vatten.skillnad.max_djup"
        : "dagsljus.skillnad.soltimmar",
    efter_namn: lageIdag ? "Nuläge" : "Förslag A",
    lage_idag: lageIdag,
  } as ProjektVy;
}

const STANDARD_SERVER_REGN: KorningRegnInfo = {
  aterkomsttid_ar: 100,
  varaktighet_min: 60,
  klimatfaktor: 1.25,
  regn_mm: 68.5,
  berakningstyp: "statisk",
  regntyp: "block",
  kalla: "stockholmsregn-2024",
  ar_standard: true,
};

const STANDARD_SERVER_SOL: KorningSolInfo = {
  datum_iso: "2025-03-21",
  start_timme: null,
  slut_timme: null,
  sammanhangande: false,
  inkludera_vsc: false,
  klockslag_utc: null,
  ar_standard: true,
};

function korning(
  status: string,
  vyer: ProjektVy[],
  extra: Partial<Pick<KorningPost, "mall" | "regn" | "avtappning_mm_h" | "sol">> = {},
): KorningPost {
  return {
    mall: extra.mall ?? "vatten",
    status: { status },
    vyer,
    regn: extra.regn === undefined ? STANDARD_SERVER_REGN : extra.regn,
    avtappning_mm_h: extra.avtappning_mm_h === undefined ? null : extra.avtappning_mm_h,
    sol: extra.sol === undefined ? null : extra.sol,
  };
}

function minnesLagring(): Pick<Storage, "getItem" | "setItem"> {
  const m = new Map<string, string>();
  return {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => {
      m.set(k, v);
    },
  };
}

test("aterladdaSparadeVyer hämtar nuläge och sparat scenario från servern och sätter regnsignatur", async () => {
  const t = tillstandFor("persist-test-1");
  t.lageVyer = [];
  t.ritVyer = [];
  t.scenario = { slug: "forslag-a", namn: "Förslag A" };

  const lageVy = skapaVy("nulage-1", true);
  const forslagVy = skapaVy("forslag-1", false);
  const anrop: string[] = [];

  const res = await aterladdaSparadeVyer(t, {
    lageIdagStatus: async (p) => {
      anrop.push(`lage:${p}`);
      return { korningar: [korning("klar", [lageVy]), korning("pagaende", [skapaVy("ej-klar", true)])] };
    },
    scenarioStatus: async (p, slug) => {
      anrop.push(`scen:${p}:${slug}`);
      return { korningar: [korning("klar", [forslagVy])] };
    },
  });

  assert.deepEqual(anrop.sort(), ["lage:persist-test-1", "scen:persist-test-1:forslag-a"]);
  assert.equal(res.lageVyer.length, 1);
  assert.equal(res.lageVyer[0]?.id, "nulage-1");
  assert.equal(t.lageRegnSignatur, regnSignatur(t.regn, t.avtappning));
  assert.equal(res.ritVyer.length, 1);
  assert.equal(res.ritVyer[0]?.id, "forslag-1");
  assert.equal(t.ritRegnSignatur, regnSignatur(t.regn, t.avtappning));
});

test("aterladdaSparadeVyer hittar senast körda scenario via listaScenarier när t.scenario saknas efter ny inloggning", async () => {
  const t = tillstandFor("persist-test-2");
  t.lageVyer = [];
  t.ritVyer = [];
  t.scenario = null;
  t.namn = "";

  const scenarier: ScenarioPost[] = [
    { slug: "utan-korning", namn: "Utkast", andrad: "2026-11-26T10:00:00Z", antal_andringar: 1, korningar: {} },
    {
      slug: "aldre",
      namn: "Äldre förslag",
      andrad: "2026-11-25T10:00:00Z",
      antal_andringar: 2,
      korningar: { skyfall: { korning_id: "k1", ref: "r1", k9_nyckel: "n1" } },
    },
    {
      slug: "senaste",
      namn: "Senaste förslag",
      andrad: "2026-11-26T09:00:00Z",
      antal_andringar: 3,
      korningar: { skyfall: { korning_id: "k2", ref: "r2", k9_nyckel: "n2" } },
    },
  ];

  let efterfragadSlug: string | null = null;
  await aterladdaSparadeVyer(t, {
    lageIdagStatus: async () => ({ korningar: [] }),
    listaScenarier: async () => ({ scenarier }),
    scenarioStatus: async (_p, slug) => {
      efterfragadSlug = slug;
      return { korningar: [korning("klar", [skapaVy("v-senaste", false)])] };
    },
  });

  assert.equal(efterfragadSlug, "senaste");
  assert.deepEqual(t.scenario, { slug: "senaste", namn: "Senaste förslag" });
  assert.equal(t.namn, "Senaste förslag");
  assert.equal(t.ritVyer.length, 1);
  assert.equal(t.ritVyer[0]?.id, "v-senaste");
});

test("aterladdaSparadeVyer hanterar tomma och felande API-svar utan att krascha och hoppar över redan laddade vyer", async () => {
  const t = tillstandFor("persist-test-3");
  t.lageVyer = [];
  t.ritVyer = [];
  t.scenario = { slug: "trasigt", namn: "Trasigt" };

  const res = await aterladdaSparadeVyer(t, {
    lageIdagStatus: async () => {
      throw new Error("HTTP 404");
    },
    scenarioStatus: async () => {
      throw new Error("Nätverksfel");
    },
  });

  assert.deepEqual(res.lageVyer, []);
  assert.deepEqual(res.ritVyer, []);
  assert.equal(t.lageRegnSignatur, null);

  // Redan laddade vyer ska inte hämtas om
  t.lageVyer = [skapaVy("redan-har", true)];
  let lageAnrop = 0;
  await aterladdaSparadeVyer(t, {
    lageIdagStatus: async () => {
      lageAnrop += 1;
      return { korningar: [] };
    },
    scenarioStatus: async () => ({ korningar: [] }),
  });
  assert.equal(lageAnrop, 0);
});

test("lasArbetsytaUiLage och sparaArbetsytaUiLage sparar och läser minimeringslägen rent", () => {
  const lagr = minnesLagring();
  assert.deepEqual(lasArbetsytaUiLage(lagr), FORVAL_UI_LAGE);

  const sparat: ArbetsytaUiLage = {
    teckenMinimerad: true,
    grundkartaOppen: true,
    panelDold: true,
  };
  sparaArbetsytaUiLage(sparat, lagr);
  assert.deepEqual(lasArbetsytaUiLage(lagr), sparat);

  // Trasig JSON faller tillbaka på förval
  lagr.setItem("floden.arbetsyta.ui.v1", "{trasig json");
  assert.deepEqual(lasArbetsytaUiLage(lagr), FORVAL_UI_LAGE);

  // Saknad lagring faller tillbaka på förval
  assert.deepEqual(lasArbetsytaUiLage(null), FORVAL_UI_LAGE);
});

test("B5: ändrat regn eller avtappning före F5 ger mismatchande signatur mot serverns sparade körning", async () => {
  const t = tillstandFor("persist-test-b5-mismatch");
  t.lageVyer = [];
  t.ritVyer = [];
  t.scenario = { slug: "forslag-b5", namn: "Förslag B5" };
  // Användaren bytte regn till 10-årsregn + avtappning 15 mm/h i sessionStorage innan F5,
  // medan servern har sparad körning med standardregn (0 mm/h ej angiven).
  t.regn = medRegnForval(STANDARD_REGNVAL, "10ar");
  t.avtappning = 15;

  await aterladdaSparadeVyer(t, {
    lageIdagStatus: async () => ({
      korningar: [korning("klar", [skapaVy("nulage-std", true)], { regn: STANDARD_SERVER_REGN, avtappning_mm_h: null })],
    }),
    scenarioStatus: async () => ({
      korningar: [korning("klar", [skapaVy("forslag-std", false)], { regn: STANDARD_SERVER_REGN, avtappning_mm_h: null })],
    }),
  });

  const klientSign = regnSignatur(t.regn, t.avtappning);
  assert.notEqual(t.lageRegnSignatur, klientSign);
  assert.notEqual(t.ritRegnSignatur, klientSign);
  assert.equal(t.lageRegnSignatur, regnSignatur(STANDARD_REGNVAL, null));
  assert.equal(t.ritRegnSignatur, regnSignatur(STANDARD_REGNVAL, null));

  const status = bedomInstallningsStatus(t, "vatten");
  assert.equal(status.regnUtdaterat, true);
  assert.match(status.varningText ?? "", /Valt regn skiljer sig från visat resultat/);
});

test("B5 & B14: oförändrat eget regn + solinställning matchar efter F5, medan ändrad sol före F5 flaggas", async () => {
  const t = tillstandFor("persist-test-b5-match-sol-mismatch");
  t.lageVyer = [];
  t.ritVyer = [];
  t.scenario = null;
  t.regn = medRegnForval(STANDARD_REGNVAL, "10ar");
  t.avtappning = 10;
  // Användaren ändrade solinställning till midsommar före F5, medan servern körde standard (vårdagjämning).
  t.sol = { ...STANDARD_SOLVAL, datumForval: "midsommar" };

  const serverRegn10: KorningRegnInfo = {
    aterkomsttid_ar: 10,
    varaktighet_min: 60,
    klimatfaktor: 1.25,
    regn_mm: 40.2,
    berakningstyp: "statisk",
    regntyp: "cds",
    kalla: "stockholmsregn-2024",
    ar_standard: false,
  };

  await aterladdaSparadeVyer(t, {
    lageIdagStatus: async () => ({
      korningar: [
        korning("klar", [skapaVy("nulage-10", true, "vatten")], { regn: serverRegn10, avtappning_mm_h: 10 }),
        korning("klar", [skapaVy("nulage-sol", true, "dagsljus")], {
          mall: "dagsljus",
          regn: null,
          sol: STANDARD_SERVER_SOL,
        }),
      ],
    }),
    scenarioStatus: async () => ({ korningar: [] }),
  });

  assert.equal(t.lageRegnSignatur, regnSignatur(t.regn, t.avtappning));
  assert.equal(t.lageSolSignatur, solSignatur(STANDARD_SOLVAL));
  assert.notEqual(t.lageSolSignatur, solSignatur(t.sol));

  const status = bedomInstallningsStatus(t, "dagsljus");
  assert.equal(status.regnUtdaterat, false);
  assert.equal(status.solUtdaterat, true);
  assert.match(status.varningText ?? "", /Vald solinställning skiljer sig från visat resultat/);
});

test("B5: saknade regnparametrar från servern ger null-signatur (aldrig sessionStorage-kopia)", async () => {
  const t = tillstandFor("persist-test-b5-saknad-meta");
  t.lageVyer = [];
  t.ritVyer = [];
  t.regn = medRegnForval(STANDARD_REGNVAL, "extrem");
  await aterladdaSparadeVyer(t, {
    lageIdagStatus: async () => ({
      korningar: [korning("klar", [skapaVy("gammal", true)], { regn: null, avtappning_mm_h: null })],
    }),
    scenarioStatus: async () => ({ korningar: [] }),
  });
  assert.equal(t.lageRegnSignatur, null);
  assert.equal(bedomInstallningsStatus(t, "vatten").regnUtdaterat, true);
});
