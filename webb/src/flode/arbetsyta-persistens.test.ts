/* Körs med `npm test`. Persistens av sparade nuläges-/förslagsvyer och hopfällbara kartmenyer. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { KorningPost, ScenarioPost } from "../karta/kartapi.ts";
import type { ProjektVy } from "../types.ts";
import { regnSignatur } from "./regnval.ts";
import {
  aterladdaSparadeVyer,
  FORVAL_UI_LAGE,
  lasArbetsytaUiLage,
  sparaArbetsytaUiLage,
  tillstandFor,
  type ArbetsytaUiLage,
} from "./tillstand.ts";

function skapaVy(id: string, lageIdag: boolean): ProjektVy {
  return {
    id,
    avsnitt: "vatten",
    korning_id: `k-${id}`,
    fore_lager: "vatten.fore.max_djup",
    efter_lager: lageIdag ? null : "vatten.efter.max_djup",
    skillnad_lager: lageIdag ? "vatten.fore.max_djup" : "vatten.skillnad.max_djup",
    efter_namn: lageIdag ? "Nuläge" : "Förslag A",
    lage_idag: lageIdag,
  } as ProjektVy;
}

function korning(status: string, vyer: ProjektVy[]): KorningPost {
  return { mall: "skyfall", status: { status }, vyer };
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
