/* Körs med `npm test`. Uppdrag 14: 2D-metodväljare, signaturbyte, F5-återladdning och B11-sektionsskydd. */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import type { LagerPost } from "../fragor-typer.ts";
import type { KorningPost, KorningRegnInfo, SkyfallMetod } from "../karta/kartapi.ts";
import type { ProjektVy } from "../types.ts";
import { lagerVal, PALETTER, rollEtikett } from "./analyslager.ts";
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
const ARBETSYTA_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.ts"), "utf-8");

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

const SERVER_REGN_2D_STD: KorningRegnInfo = {
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

test("metodväljaren i Steg 2 visar Graf (snabb) och 2D lokal tröghet (fördjupad) samt 2D-notis", () => {
  const htmlGraf = regnValHtml(STANDARD_REGNVAL, null, null, null, false, "graf");
  assert.match(htmlGraf, /data-skyfall-metod="graf"[^>]*aria-pressed="true"[^>]*>Graf \(snabb\)</);
  assert.match(htmlGraf, /data-skyfall-metod="2d"[^>]*aria-pressed="false"[^>]*>2D lokal tröghet \(fördjupad\)</);
  assert.match(htmlGraf, /data-regn-berakningstyp="statisk"/);
  assert.doesNotMatch(htmlGraf, /1D-rännsten \(skiss\)/);

  const html2d = regnValHtml(STANDARD_REGNVAL, null, null, null, false, "2d");
  assert.match(html2d, /data-skyfall-metod="graf"[^>]*aria-pressed="false"/);
  assert.match(html2d, /data-skyfall-metod="2d"[^>]*aria-pressed="true"/);
  assert.match(html2d, /tar längre tid/);
  assert.match(html2d, /hastighet, fara \(h·v\) och 1D-rännsten \(skiss\)/);
  assert.doesNotMatch(html2d, /data-regn-berakningstyp=/);

  assert.equal(valdRegnText(STANDARD_REGNVAL, null, null, "2d"), "Standard (projektets förval) · 2D lokal tröghet");
  assert.equal(tillRegnParam(STANDARD_REGNVAL, "2d"), null);
  assert.deepEqual(tillRegnParam(medRegnForval(STANDARD_REGNVAL, "10ar"), "2d"), {
    aterkomsttid_ar: 10,
    varaktighet_min: 60,
    klimatfaktor: 1.25,
    regntyp: "cds",
  });

  // Klickselektorn och hanteraren i arbetsyta.ts lyssnar på [data-skyfall-metod]
  assert.match(ARBETSYTA_KALLTEXT, /\[data-skyfall-metod\]/);
  assert.match(ARBETSYTA_KALLTEXT, /m\.dataset\.skyfallMetod/);
});

test("metodbyte mellan graf och 2d ändrar signatur och markerar visat resultat som utdaterat (inkl. F5)", async () => {
  const sigGraf = regnSignatur(STANDARD_REGNVAL, null, "graf");
  const sig2d = regnSignatur(STANDARD_REGNVAL, null, "2d");
  assert.notEqual(sigGraf, sig2d);

  // 1. Förberäknad projektvy (graf) blir utdaterad när användaren väljer 2D
  const tProj = tillstandFor("skyfall-2d-proj");
  tProj.lageVyer = [];
  tProj.ritVyer = [];
  tProj.skyfallMetod = "graf";
  assert.equal(bedomInstallningsStatus(tProj, "vatten", [{ avsnitt: "vatten" }]).regnUtdaterat, false);

  tProj.skyfallMetod = "2d";
  const stUtd = bedomInstallningsStatus(tProj, "vatten", [{ avsnitt: "vatten" }]);
  assert.equal(stUtd.regnUtdaterat, true);
  assert.match(stUtd.varningText ?? "", /2D/);

  // 2. F5-återladdning mot en klar 2D-körning matchar skyfallMetod === "2d" och flaggar byte till "graf"
  const tF5 = tillstandFor("skyfall-2d-f5");
  tF5.lageVyer = [];
  tF5.ritVyer = [];
  tF5.scenario = null;
  tF5.regn = { ...STANDARD_REGNVAL, berakningstyp: "statisk", regntyp: "cds" };
  tF5.skyfallMetod = "2d";

  const korning2d: KorningPost = {
    mall: "vatten",
    status: { status: "klar" },
    vyer: [skapaVattenVy("nulage-2d", true)],
    regn: SERVER_REGN_2D_STD,
    skyfall_metod: "2d",
    avtappning_mm_h: null,
    sol: null,
  };
  assert.equal(regnSignaturFranKorningar([korning2d]), sig2d);

  await aterladdaSparadeVyer(tF5, {
    lageIdagStatus: async () => ({ korningar: [korning2d] }),
    scenarioStatus: async () => ({ korningar: [] }),
  });
  assert.equal(tF5.lageRegnSignatur, sig2d);
  assert.equal(bedomInstallningsStatus(tF5, "vatten").regnUtdaterat, false);

  // Byte tillbaka till "graf" markerar 2D-resultatet som utdaterat
  tF5.skyfallMetod = "graf";
  assert.equal(bedomInstallningsStatus(tF5, "vatten").regnUtdaterat, true);
});

test("korning.ts skickar skyfall_metod='2d' vidare till både nuläge och förslag", async () => {
  const t = tillstandFor("skyfall-2d-korning");
  t.scenario = { slug: "forslag-2d", namn: "Förslag 2D" };
  t.regn = { ...STANDARD_REGNVAL };
  t.skyfallMetod = "2d";

  const mottagnaMetoder: Array<SkyfallMetod | null | undefined> = [];
  const api: KorningApi = {
    korLageIdag: async (_p, _a, _avt, _r, _s, metod) => {
      mottagnaMetoder.push(metod);
      return { lage: "idag", fragor: [] };
    },
    korScenario: async (_p, _slug, _a, _avt, _r, _s, metod) => {
      mottagnaMetoder.push(metod);
      return { slug: "forslag-2d", fragor: [] };
    },
    lageIdagStatus: async () => ({
      korningar: [
        {
          mall: "vatten",
          status: { status: "klar" },
          vyer: [skapaVattenVy("l-2d", true)],
          regn: SERVER_REGN_2D_STD,
          skyfall_metod: "2d",
          avtappning_mm_h: null,
          sol: null,
        },
      ],
    }),
    scenarioStatus: async () => ({
      korningar: [
        {
          mall: "vatten",
          status: { status: "klar" },
          vyer: [skapaVattenVy("s-2d", false)],
          regn: SERVER_REGN_2D_STD,
          skyfall_metod: "2d",
          avtappning_mm_h: null,
          sol: null,
        },
      ],
    }),
  };

  await startaLageIdag(t, ["vatten.skyfall"], api);
  await startaKorning(t, ["vatten.skyfall"], api);
  assert.deepEqual(mottagnaMetoder, ["2d", "2d"]);
  assert.equal(t.lageRegnSignatur, regnSignatur(STANDARD_REGNVAL, null, "2d"));
  assert.equal(t.ritRegnSignatur, regnSignatur(STANDARD_REGNVAL, null, "2d"));
});

test("B11-sektionsskydd: sektionVattenLager skickar bara max_djup och aldrig fara, hastighet eller ankomsttid", () => {
  const lager = [
    { id: "skyfall.fore.max_djup", roll: "max_djup", typ: "bas" },
    { id: "skyfall.ritat.max_djup", roll: "max_djup", typ: "scenario" },
    { id: "skillnad.skyfall.max_djup", roll: "max_djup", typ: "skillnad" },
    { id: "skyfall.fore.fara", roll: "fara", typ: "bas" },
    { id: "skyfall.ritat.fara", roll: "fara", typ: "scenario" },
    { id: "skyfall.fore.hastighet", roll: "hastighet", typ: "bas" },
    { id: "skyfall.ritat.hastighet", roll: "hastighet", typ: "scenario" },
    { id: "skyfall.fore.ankomsttid", roll: "ankomsttid", typ: "bas" },
  ];

  // max_djup (bas + scenario) tillåts
  assert.deepEqual(
    sektionVattenLager(lager, { fore_lager: "skyfall.fore.max_djup", efter_lager: "skyfall.ritat.max_djup" }),
    { fore_lager: "skyfall.fore.max_djup", efter_lager: "skyfall.ritat.max_djup" },
  );

  // fara, hastighet, ankomsttid och skillnad ger null
  for (const [fore, efter] of [
    ["skyfall.fore.fara", "skyfall.ritat.fara"],
    ["skyfall.fore.hastighet", "skyfall.ritat.hastighet"],
    ["skyfall.fore.ankomsttid", null],
    ["skillnad.skyfall.max_djup", "skyfall.ritat.fara"],
  ] as const) {
    assert.deepEqual(sektionVattenLager(lager, { fore_lager: fore, efter_lager: efter }), {
      fore_lager: null,
      efter_lager: null,
    });
  }

  // arbetsyta.ts använder alltid sektionVattenLager innan hamtaSektion
  assert.match(ARBETSYTA_KALLTEXT, /sektionVattenLager\(r\?\.lager,\s*vy\)/);
});

test("fara-rollen har svensk etikett, palett och sorteras under Skyfall efter max_djup", () => {
  assert.equal(PALETTER.fara?.length, 5);
  assert.deepEqual(rollEtikett("fara"), {
    etikett: "Fara (djup × hastighet)",
    enhet: "m²/s",
  });

  const lp = (id: string, roll: string, primar = false): LagerPost =>
    ({
      id,
      analys: "skyfall",
      roll,
      scenario: "fore",
      typ: "bas",
      etikett: roll,
      enhet: "",
      doman: "vatten",
      primar,
      kontroll: false,
      harledd: roll === "fara",
    }) as LagerPost;

  const roller = lagerVal(
    [
      lp("skyfall.fore.hastighet", "hastighet"),
      lp("skyfall.fore.fara", "fara"),
      lp("skyfall.fore.max_djup", "max_djup", true),
      lp("skyfall.fore.ankomsttid", "ankomsttid"),
    ],
    { fore_lager: "skyfall.fore.max_djup", efter_lager: null },
    "vatten",
  );
  assert.equal(roller[0]?.roll, "max_djup");
  assert.ok(roller.some((r) => r.roll === "fara" && r.etikett === "Fara (djup × hastighet)" && r.enhet === "m²/s"));
});
