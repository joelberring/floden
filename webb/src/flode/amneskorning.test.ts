/*
 * Körs med `npm test`. Deterministiska tester för Våg 9 Frontend:
 * 1. Oberoende ämneskörning i Steg 2 (`analyserForAktivtAmne`, `korKnappEtiketterForAmne`,
 *    ämnesmedveten validering så `regnFel` inte blockerar Dagsljus och `solFel` inte blockerar Skyfall).
 * 2. Bevarande av andra ämnens vyer, filtrering på startade körningar (oberoende av gamla fel i
 *    `lage-idag.json`) och delfel vid samlad körning (pollingen väntar klart på pågående ämnen och
 *    tillämpar lyckade delresultat samtidigt som delfelet rapporteras).
 * 3. `.flode-segment`-CSS-fix i `flode.css` (stretch + min-height + flex-centrering utan vit höjdglipa).
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import module from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import type { KorningPost, KorningRegnInfo, KorningSolInfo, ProjektInfo, RegnParam, SkyfallMetod, SolParam } from "../karta/kartapi.ts";
import type { ProjektVy } from "../types.ts";
import {
  arPagaende,
  filtreraStartadeKorningar,
  senasteKlaraAvsnitt,
  startaKorning,
  startaLageIdag,
  tillampaKlaraKorningar,
  vidKorning,
  type KorningApi,
} from "./korning.ts";
import { medRegnForval, regnSignatur, STANDARD_REGNVAL, valideraRegnVal } from "./regnval.ts";
import { solSignatur, STANDARD_SOLVAL, valideraSolVal } from "./solval.ts";
import { tillstandFor } from "./tillstand.ts";

const FLODE_MAPP = path.dirname(fileURLToPath(import.meta.url));
const ARBETSYTA_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.ts"), "utf-8");
const FLODE_CSS = fs.readFileSync(path.join(FLODE_MAPP, "flode.css"), "utf-8");

const BROWSER_STUB_SLUT = [
  "/karta/kartapi.ts",
  "/karta/kartmotor.ts",
  "/karta/lagerpanel.ts",
  "/karta/ritlagerpanel.ts",
  "/karta/ritverktyg.ts",
  "/kartsida3d.ts",
  "/flode/kartdel.ts",
  "/flode/resultat.ts",
  "/flode/underlag.ts",
];

const STUB_MODUL = `
export default {};
export const arGrovPekare = () => false;
export const Kartmotor = class {};
export const TOM_RITVY = {};
export const monteraLagerpanel = () => {};
export const monteraRitlagerpanel = () => {};
export const FORVALD_HOJD_M = 9;
export const Ritverktyg = class {};
export const TANGENTER = { valj: "v", hus: "h", yta: "y", linje: "l", angra: "z", laggtill: "a", tabort: "d", klipp: "c", ihop: "j", vand: "r", forenkla: "s", fast: "f", foljkant: "k", ratVinkel: "o" };
export const fasadForklaringHtml = () => "";
export const skapa3dVy = () => ({ montera: () => ({ avmontera() {} }) });
export const VY3D_ID = "vy:3d";
export const BasLager = class {};
export const byggnaderMedForslag = () => [];
export const skapaKarta = () => ({});
export const teckenHtml = () => "";
export const monteraUnderlagForm = () => {};
export const taBortUnderlag = async () => {};
export const bytScenarioNamn = async () => ({});
export const foreslaSkarvatgard = async () => ({});
export const hamtaBifurkationer = async () => ({});
export const hamtaProjektInfo = async () => ({});
export const hamtaScenario = async () => ({});
export const hamtaSektion = async () => ({});
export const hamtaSvfIntervall = async () => ({});
export const hamtaTidsserie = async () => ({});
export const hamtaVattenIntervall = async () => ({});
export const kontrolleraAndringar = async () => ({});
export const lageIdagStatus = async () => ({ korningar: [] });
export const listaScenarier = async () => ({ scenarier: [] });
export const scenarioStatus = async () => ({ korningar: [] });
export const sparaScenario = async () => ({});
export const taBortScenario = async () => ({});
`;

let arbetsytaExportsPromise: Promise<typeof import("./arbetsyta.ts")> | null = null;
function laddaArbetsytaExports(): Promise<typeof import("./arbetsyta.ts")> {
  if (!arbetsytaExportsPromise) {
    module.registerHooks({
      resolve(specifier, context, nextResolve) {
        if (specifier === "maplibre-gl") {
          return { shortCircuit: true, url: "stub:maplibre-gl", format: "module" };
        }
        if (specifier.startsWith(".") && context.parentURL?.endsWith("/flode/arbetsyta.ts")) {
          const resolved = new URL(specifier, context.parentURL).pathname;
          if (BROWSER_STUB_SLUT.some((s) => resolved.endsWith(s))) {
            return { shortCircuit: true, url: `stub:${resolved}`, format: "module" };
          }
        }
        return nextResolve(specifier, context);
      },
      load(url, context, nextLoad) {
        if (url.startsWith("stub:")) {
          return { shortCircuit: true, format: "module", source: STUB_MODUL };
        }
        return nextLoad(url, context);
      },
    });
    arbetsytaExportsPromise = import("./arbetsyta.ts");
  }
  return arbetsytaExportsPromise;
}

const SERVER_REGN_STD: KorningRegnInfo = {
  aterkomsttid_ar: 100,
  varaktighet_min: 60,
  klimatfaktor: 1.25,
  regn_mm: 68.5,
  berakningstyp: "statisk",
  regntyp: "block",
  kalla: "stockholmsregn-2024",
  ar_standard: true,
};

const SERVER_SOL_STD: KorningSolInfo = {
  datum_iso: "2025-03-21",
  start_timme: null,
  slut_timme: null,
  sammanhangande: false,
  inkludera_vsc: false,
  klockslag_utc: null,
  ar_standard: true,
};

function skapaVy(id: string, avsnitt: ProjektVy["avsnitt"], lageIdag: boolean, korningId = `k-${id}`): ProjektVy {
  return {
    id,
    avsnitt,
    rubrik: `${avsnitt}-${id}`,
    korning_id: korningId,
    fore_lager: `${avsnitt}.fore`,
    efter_lager: lageIdag ? null : `${avsnitt}.efter`,
    skillnad_lager: lageIdag ? `${avsnitt}.fore` : `${avsnitt}.skillnad`,
    efter_namn: lageIdag ? "Läget idag" : "Förslag",
    andring: "",
    mattnamn: avsnitt,
    lage_idag: lageIdag,
  };
}

function vantaPaKlarKorning(t: ReturnType<typeof tillstandFor>, timeoutMs = 1500): Promise<void> {
  if (t.korning?.klar) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      avsluta();
      reject(new Error("Timeout i väntan på att körningen ska bli klar."));
    }, timeoutMs);
    const avsluta = vidKorning(() => {
      if (t.korning?.klar) {
        clearTimeout(timer);
        avsluta();
        resolve();
      }
    });
  });
}

// ------------------------------------------------------------------ 1. analyserForAktivtAmne & validering

test("analyserForAktivtAmne mappar aktivt ämne till egna analyser med säker fallback och rätt knappetiketter", async () => {
  const ar = await laddaArbetsytaExports();
  const info: Pick<ProjektInfo, "analyser"> = {
    analyser: [
      { id: "vatten.skyfall", namn: "Skyfall", mall: "vatten", analys: "skyfall", forval: true },
      { id: "dagsljus.sol", namn: "Soltimmar", mall: "dagsljus", analys: "sol", forval: true },
      { id: "dagsljus.vsc", namn: "Fasad VSC", mall: "dagsljus", analys: "vsc", forval: false },
      { id: "ekologi.konnektivitet", namn: "Konnektivitet", mall: "ekologi", analys: "konnektivitet", forval: true },
    ],
  };

  assert.deepEqual(ar.analyserForAktivtAmne(info, "vatten"), ["vatten.skyfall"]);
  assert.deepEqual(ar.analyserForAktivtAmne(info, "dagsljus"), ["dagsljus.sol"]);
  assert.deepEqual(ar.analyserForAktivtAmne(info, "ekologi"), ["ekologi.konnektivitet"]);
  // Ämne utan egen körbar mall ("luft", "mikroklimat", "buller") faller ALDRIG tyst tillbaka på Skyfall
  assert.deepEqual(ar.analyserForAktivtAmne(info, "luft"), []);
  assert.deepEqual(ar.analyserForAktivtAmne(info, "mikroklimat"), []);
  assert.deepEqual(ar.analyserForAktivtAmne(info, "buller"), []);

  // Etiketter: ämnesspecifika när > 1 körbart ämne finns och aktivt ämne har egna analyser
  const solEtik = ar.korKnappEtiketterForAmne(info, "dagsljus");
  assert.equal(solEtik.arFlerAmnen, true);
  assert.equal(solEtik.nulage, "Beräkna dagsljus (nuläge)");
  assert.equal(solEtik.forslag, "Beräkna dagsljus (förslag)");
  assert.equal(solEtik.allaNulage, "Beräkna alla ämnen");

  const vatEtik = ar.korKnappEtiketterForAmne(info, "vatten");
  assert.equal(vatEtik.arFlerAmnen, true);
  assert.equal(vatEtik.nulage, "Beräkna skyfall (nuläge)");

  // Ämne utan egen körbar mall ska inte visa vilseledande "Beräkna luft" eller dubbel "alla ämnen"-knapp
  const luftEtik = ar.korKnappEtiketterForAmne(info, "luft");
  assert.equal(luftEtik.arFlerAmnen, false);
  assert.equal(luftEtik.nulage, "Beräkna nuläge");

  // Enämnesprojekt visar korta standardetiketter utan sekundär "alla ämnen"-knapp och faller aldrig tyst tillbaka på Skyfall
  const baraVatten: Pick<ProjektInfo, "analyser"> = {
    analyser: [{ id: "vatten.skyfall", namn: "Skyfall", mall: "vatten", analys: "skyfall", forval: true }],
  };
  assert.deepEqual(ar.analyserForAktivtAmne(baraVatten, "vatten"), ["vatten.skyfall"]);
  assert.deepEqual(ar.analyserForAktivtAmne(baraVatten, "dagsljus"), []);
  assert.deepEqual(ar.analyserForAktivtAmne(baraVatten, "ekologi"), []);
  assert.equal(ar.korKnappEtiketterForAmne(baraVatten, "vatten").arFlerAmnen, false);
  assert.equal(ar.korKnappEtiketterForAmne(baraVatten, "vatten").nulage, "Beräkna nuläge");

  // Om ett ämne bara har icke-förvalda analyser väljs ändå de matchande (aldrig tom lista)
  const baraVsc: Pick<ProjektInfo, "analyser"> = {
    analyser: [
      { id: "vatten.skyfall", namn: "Skyfall", mall: "vatten", analys: "skyfall", forval: true },
      { id: "dagsljus.vsc", namn: "Fasad VSC", mall: "dagsljus", analys: "vsc", forval: false },
    ],
  };
  assert.deepEqual(ar.analyserForAktivtAmne(baraVsc, "dagsljus"), ["dagsljus.vsc"]);
  assert.deepEqual(ar.analyserForAktivtAmne(null, "vatten"), []);

  // Klickselektorn täcker även den sekundära knappen #ar-kor-alla
  const selektorer = ar.ARBETSYTA_KLICK_SELEKTOR.split(",").map((s) => s.trim());
  assert.ok(selektorer.includes("#ar-kor-alla"), "Klickselektorn måste lyssna på #ar-kor-alla");
});

test("validering: regnFel/avtappningFel hindrar inte ren Dagsljus-körning och solFel hindrar inte ren Skyfall-körning", async () => {
  const ar = await laddaArbetsytaExports();

  const regnFel = valideraRegnVal({ ...STANDARD_REGNVAL, forval: "eget", egetLage: "mm", regn_mm: -5 });
  const solFel = valideraSolVal({ ...STANDARD_SOLVAL, datumForval: "eget", egetDatumIso: "2025-02-30" });
  assert.ok(regnFel, "Förväntade ett regnFel för negativ regnmängd");
  assert.ok(solFel, "Förväntade ett solFel för ogiltigt datum");

  // Ren Dagsljus-körning tillåts trots regnFel och avtappningFel, men blockeras av solFel
  assert.equal(ar.kanStartaKorningForAnalyser(["dagsljus.sol"], regnFel, true, null), true);
  assert.equal(ar.kanStartaKorningForAnalyser(["dagsljus.sol"], null, false, solFel), false);

  // Ren Skyfall-körning tillåts trots solFel, men blockeras av regnFel eller avtappningFel
  assert.equal(ar.kanStartaKorningForAnalyser(["vatten.skyfall"], null, false, solFel), true);
  assert.equal(ar.kanStartaKorningForAnalyser(["vatten.skyfall"], regnFel, false, null), false);
  assert.equal(ar.kanStartaKorningForAnalyser(["vatten.skyfall"], null, true, null), false);

  // Ren Ekologi-körning blockeras varken av regnFel eller solFel
  assert.equal(ar.kanStartaKorningForAnalyser(["ekologi.konnektivitet"], regnFel, true, solFel), true);

  // Samlad körning (Skyfall + Dagsljus) blockeras av både regnFel och solFel
  assert.equal(ar.kanStartaKorningForAnalyser(["vatten.skyfall", "dagsljus.sol"], regnFel, false, null), false);
  assert.equal(ar.kanStartaKorningForAnalyser(["vatten.skyfall", "dagsljus.sol"], null, false, solFel), false);
  assert.equal(ar.kanStartaKorningForAnalyser(["vatten.skyfall", "dagsljus.sol"], null, false, null), true);
  assert.equal(ar.kanStartaKorningForAnalyser([], null, false, null), false);
});

// ------------------------------------------------------------------ 2. Bevara vyer, ignorera gamla fel & delresultat

test("köra Dagsljus ensamt bevarar Skyfall-vyer, skickar inte ogiltigt regn och ignorerar gammalt Skyfall-fel", async () => {
  const t = tillstandFor("v9-dagsljus-oberoende");
  t.scenario = { slug: "forslag-v9", namn: "Förslag V9" };
  // Ogiltigt regn i klienten (t.ex. användaren lämnade Eget regn tomt), giltig solinställning
  t.regn = { ...STANDARD_REGNVAL, forval: "eget", egetLage: "mm", regn_mm: -10 };
  t.avtappning = 12;
  t.skyfallMetod = "2d";
  t.sol = { ...STANDARD_SOLVAL };

  // Redan beräknade Skyfall-vyer finns i tillståndet för både nuläge och förslag
  const skyfallLageVy = skapaVy("vat-lage-1", "vatten", true, "k-vat-1");
  const skyfallRitVy = skapaVy("vat-rit-1", "vatten", false, "k-vat-rit-1");
  const sigVatten = regnSignatur(STANDARD_REGNVAL, null, "graf");
  t.lageVyer = [skyfallLageVy];
  t.lageRegnSignatur = sigVatten;
  t.ritVyer = [skyfallRitVy];
  t.ritRegnSignatur = sigVatten;

  const skickat: Array<{ typ: string; avt: number | null; regn: RegnParam | null; sol: SolParam | null; metod: SkyfallMetod | null | undefined }> = [];
  const dagsljusLageVy = skapaVy("sol-lage-2", "dagsljus", true, "k-sol-2");
  const dagsljusRitVy = skapaVy("sol-rit-2", "dagsljus", false, "k-sol-rit-2");

  const api: KorningApi = {
    korLageIdag: async (_p, _a, avt, regn, sol, metod) => {
      skickat.push({ typ: "lage", avt, regn, sol, metod });
      return { lage: "idag", fragor: [{ mall: "dagsljus", korning: { korning_id: "k-sol-2" } }] };
    },
    korScenario: async (_p, _slug, _a, avt, regn, sol, metod) => {
      skickat.push({ typ: "scenario", avt, regn, sol, metod });
      return { slug: "forslag-v9", fragor: [{ mall: "dagsljus", korning: { korning_id: "k-sol-rit-2" } }] };
    },
    // lage-idag.json innehåller ett GAMMALT Skyfall-fel som inte startades i den här körningen!
    lageIdagStatus: async () => ({
      korningar: [
        {
          mall: "vatten",
          status: { korning_id: "k-vat-gammalt-fel", status: "fel", fel: "Gammalt OOM-fel på Skyfall" },
          vyer: [],
        },
        {
          mall: "dagsljus",
          status: { korning_id: "k-sol-2", status: "klar" },
          vyer: [dagsljusLageVy],
          sol: SERVER_SOL_STD,
        },
      ],
    }),
    scenarioStatus: async () => ({
      korningar: [
        {
          mall: "vatten",
          status: { korning_id: "k-vat-gammalt-fel-sc", status: "fel", fel: "Gammalt Skyfall-fel i scenario" },
          vyer: [],
        },
        {
          mall: "dagsljus",
          status: { korning_id: "k-sol-rit-2", status: "klar" },
          vyer: [dagsljusRitVy],
          sol: SERVER_SOL_STD,
        },
      ],
    }),
  };

  // 1. Nuläge (Dagsljus ensamt)
  await startaLageIdag(t, ["dagsljus.sol"], api);
  assert.equal(t.korning?.klar, true);
  assert.equal(t.korning?.fel, null, "Gammalt Skyfall-fel får inte påverka en ren Dagsljus-körning");
  assert.deepEqual(
    t.lageVyer.map((v) => v.id),
    ["vat-lage-1", "sol-lage-2"],
    "Befintlig Skyfall-vy måste bevaras när Dagsljus körs",
  );
  assert.equal(t.lageRegnSignatur, sigVatten, "Skyfalls regnsignatur måste bevaras");
  assert.equal(t.lageSolSignatur, solSignatur(STANDARD_SOLVAL));
  assert.deepEqual(senasteKlaraAvsnitt("v9-dagsljus-oberoende"), ["dagsljus"]);

  // 2. Förslag (Dagsljus ensamt)
  await startaKorning(t, ["dagsljus.sol"], api);
  assert.equal(t.korning?.klar, true);
  assert.equal(t.korning?.fel, null, "Gammalt Skyfall-fel i scenario får inte blockera Dagsljus");
  assert.deepEqual(
    t.ritVyer.map((v) => v.id),
    ["vat-rit-1", "sol-rit-2"],
    "Befintlig Skyfall-förslagsvy måste bevaras när Dagsljus körs",
  );
  assert.equal(t.ritRegnSignatur, sigVatten);
  assert.equal(t.ritSolSignatur, solSignatur(STANDARD_SOLVAL));

  // Ren Dagsljus-körning skickar inte regn/avtappning/skyfallMetod till backend
  assert.deepEqual(skickat, [
    { typ: "lage", avt: null, regn: null, sol: null, metod: null },
    { typ: "scenario", avt: null, regn: null, sol: null, metod: null },
  ]);

  // 3. Bakåtkompatibel reserv: även om mock-API returnerar {} (inga korning_id) filtrerar mall-reserv bort gammalt vattenfel
  const tReserv = tillstandFor("v9-dagsljus-mock-reserv");
  tReserv.lageVyer = [skyfallLageVy];
  const mockReservApi: KorningApi = {
    korLageIdag: async () => ({}),
    korScenario: async () => ({}),
    lageIdagStatus: async () => ({
      korningar: [
        { mall: "vatten", status: { status: "fel", fel: "Gammalt fel utan id" }, vyer: [] },
        { mall: "dagsljus", status: { status: "klar" }, vyer: [dagsljusLageVy], sol: SERVER_SOL_STD },
      ],
    }),
    scenarioStatus: async () => ({ korningar: [] }),
  };
  await startaLageIdag(tReserv, ["dagsljus.sol"], mockReservApi);
  assert.equal(tReserv.korning?.klar, true);
  assert.equal(tReserv.korning?.fel, null);
  assert.deepEqual(
    tReserv.lageVyer.map((v) => v.id),
    ["vat-lage-1", "sol-lage-2"],
  );
});

test("ren Skyfall-körning skickar inte ogiltig solinställning till API:et", async () => {
  const t = tillstandFor("v9-skyfall-utan-sol");
  t.regn = medRegnForval(STANDARD_REGNVAL, "10ar");
  t.sol = { ...STANDARD_SOLVAL, datumForval: "eget", egetDatumIso: "2025-02-30" };
  let skickatSol: SolParam | null | undefined = undefined;
  const api: KorningApi = {
    korLageIdag: async (_p, _a, _avt, _regn, sol) => {
      skickatSol = sol;
      return { lage: "idag", fragor: [{ mall: "vatten", korning: { korning_id: "k-vat-ok" } }] };
    },
    korScenario: async () => ({}),
    lageIdagStatus: async () => ({
      korningar: [
        {
          mall: "vatten",
          status: { korning_id: "k-vat-ok", status: "klar" },
          vyer: [skapaVy("vat-ok", "vatten", true, "k-vat-ok")],
          regn: SERVER_REGN_STD,
        },
      ],
    }),
    scenarioStatus: async () => ({ korningar: [] }),
  };
  await startaLageIdag(t, ["vatten.skyfall"], api);
  assert.equal(skickatSol, null, "Ogiltig solinställning får inte skickas på en ren Skyfall-körning");
  assert.equal(t.korning?.klar, true);
  assert.equal(t.korning?.fel, null);
});

test("samlad körning (Skyfall + Dagsljus) med Skyfall-fel väntar klart på Dagsljus, tillämpar delresultat och rapporterar felet", async () => {
  const ar = await laddaArbetsytaExports();
  const t = tillstandFor("v9-delresultat-vid-fel");
  t.regn = { ...STANDARD_REGNVAL };
  t.sol = { ...STANDARD_SOLVAL };
  t.lageVyer = [];

  const dagsljusVy = skapaVy("sol-del-1", "dagsljus", true, "k-sol-batch");
  let pollNr = 0;

  const api: KorningApi = {
    intervallMs: 5,
    korLageIdag: async () => ({
      lage: "idag",
      fragor: [
        { mall: "vatten", korning: { korning_id: "k-vat-batch" } },
        { mall: "dagsljus", korning: { korning_id: "k-sol-batch" } },
      ],
    }),
    korScenario: async () => ({}),
    lageIdagStatus: async () => {
      pollNr += 1;
      if (pollNr === 1) {
        // Första pollen: Skyfall har redan kraschat, men Dagsljus kör fortfarande!
        return {
          korningar: [
            {
              mall: "vatten",
              status: {
                korning_id: "k-vat-batch",
                status: "fel",
                fel_forklaring: "Området överskrider cellbudgeten för sänkgraf.",
              },
              vyer: [],
            },
            {
              mall: "dagsljus",
              status: {
                korning_id: "k-sol-batch",
                status: "kor",
                framsteg: { klara: 1, totalt: 2 },
              },
              vyer: [],
            },
          ],
        };
      }
      // Andra pollen: Dagsljus har nu blivit klar!
      return {
        korningar: [
          {
            mall: "vatten",
            status: {
              korning_id: "k-vat-batch",
              status: "fel",
              fel_forklaring: "Området överskrider cellbudgeten för sänkgraf.",
            },
            vyer: [],
          },
          {
            mall: "dagsljus",
            status: {
              korning_id: "k-sol-batch",
              status: "klar",
              framsteg: { klara: 2, totalt: 2 },
            },
            vyer: [dagsljusVy],
            sol: SERVER_SOL_STD,
          },
        ],
      };
    },
    scenarioStatus: async () => ({ korningar: [] }),
  };

  // Starta samlad körning: Poll 1 körs direkt men får INTE avbryta eftersom Dagsljus fortfarande är "kor"
  await startaLageIdag(t, ["vatten.skyfall", "dagsljus.sol"], api);
  assert.equal(pollNr, 1);
  assert.equal(arPagaende(t), true, "Pollingen måste fortsätta vänta på Dagsljus trots att Skyfall redan fått fel");
  assert.equal(t.korning?.klar, false);
  assert.equal(t.korning?.fel, null);

  // Vänta in Poll 2 när Dagsljus når "klar"
  await vantaPaKlarKorning(t);
  assert.ok(pollNr >= 2, "Minst två statuspollar ska ha körts");
  assert.equal(arPagaende(t), false);
  assert.equal(t.korning?.klar, true);

  // Lyckade Dagsljus-vyer är tillämpade och solsignatur är satt
  assert.deepEqual(
    t.lageVyer.map((v) => v.id),
    ["sol-del-1"],
  );
  assert.equal(t.lageSolSignatur, solSignatur(STANDARD_SOLVAL));
  assert.deepEqual(senasteKlaraAvsnitt("v9-delresultat-vid-fel"), ["dagsljus"]);

  // Samtidigt rapporteras tydligt specifikt fel om Skyfall
  assert.ok(t.korning?.fel, "Delfelet för Skyfall ska rapporteras");
  assert.match(t.korning.fel, /Skyfall misslyckades/);
  assert.match(t.korning.fel, /cellbudgeten/);
  assert.match(t.korning.fel, /Dagsljus blev klar/);

  // valjAmneEfterKorning växlar till "dagsljus" när användaren stod på "vatten" utan Skyfall-vy
  assert.equal(ar.valjAmneEfterKorning("vatten", t.lageVyer, senasteKlaraAvsnitt("v9-delresultat-vid-fel")), "dagsljus");
  // Om Skyfall redan hade en vy sedan tidigare hoppar vi inte bort oväntat
  assert.equal(
    ar.valjAmneEfterKorning(
      "vatten",
      [skapaVy("vat-gammal", "vatten", true), dagsljusVy],
      ["dagsljus"],
    ),
    "vatten",
  );

  // Kontrollera att avlyssnaKorning i arbetsyta.ts uppdaterar vyer när senasteKlaraAvsnitt har klara även om fel är satt
  assert.match(ARBETSYTA_KALLTEXT, /senasteKlaraAvsnitt\(projekt\)/);
  assert.match(ARBETSYTA_KALLTEXT, /const harNyaKlara = !t\.korning\.fel \|\| klaraAvsnitt\.length > 0;/);
});

test("filtreraStartadeKorningar och tillampaKlaraKorningar hanterar överlagring av samma ämne utan att tappa övriga", () => {
  const poster: KorningPost[] = [
    { mall: "vatten", status: { korning_id: "k-1", status: "fel", fel: "Gammalt fel" }, vyer: [] },
    { mall: "dagsljus", status: { korning_id: "k-2", status: "klar" }, vyer: [skapaVy("sol-ny", "dagsljus", true, "k-2")] },
  ];
  assert.deepEqual(
    filtreraStartadeKorningar(poster, { korningIds: new Set(["k-2"]), mallar: new Set(["dagsljus"]) }).map((p) => p.mall),
    ["dagsljus"],
  );

  // Omkörning av Skyfall ersätter gammal Skyfall-vy men behåller Dagsljus och Ekologi
  const t = tillstandFor("v9-merge-avsnitt");
  t.lageVyer = [
    skapaVy("vat-1", "vatten", true),
    skapaVy("sol-1", "dagsljus", true),
    skapaVy("eko-1", "ekologi", true),
  ];
  t.lageSolSignatur = solSignatur(STANDARD_SOLVAL);

  tillampaKlaraKorningar(
    t,
    [
      {
        mall: "vatten",
        status: { status: "klar" },
        vyer: [skapaVy("vat-2", "vatten", true)],
        regn: SERVER_REGN_STD,
      },
    ],
    true,
  );

  assert.deepEqual(
    t.lageVyer.map((v) => v.id),
    ["sol-1", "eko-1", "vat-2"],
  );
  assert.equal(t.lageRegnSignatur, regnSignatur(STANDARD_REGNVAL, null));
  assert.equal(t.lageSolSignatur, solSignatur(STANDARD_SOLVAL));
});

// ------------------------------------------------------------------ 3. .flode-segment CSS-fix

test("flode.css: .flode-segment sträcker alla knappar symmetriskt utan hårdlåst height (ingen vit höjdglipa)", () => {
  const segMatch = FLODE_CSS.match(/\.flode-segment\s*\{([^}]+)\}/);
  assert.ok(segMatch, "Saknar .flode-segment-regel i flode.css");
  assert.match(segMatch[1]!, /align-items:\s*stretch/, ".flode-segment måste ha align-items: stretch");

  const btnMatch = FLODE_CSS.match(/\.flode-segment\s+button\s*\{([^}]+)\}/);
  assert.ok(btnMatch, "Saknar .flode-segment button-regel i flode.css");
  assert.match(btnMatch[1]!, /min-height:\s*34px/);
  assert.match(btnMatch[1]!, /height:\s*auto/);
  assert.match(btnMatch[1]!, /padding:\s*5px\s+8px/);
  assert.match(btnMatch[1]!, /line-height:\s*1\.25/);
  assert.match(btnMatch[1]!, /display:\s*flex/);
  assert.match(btnMatch[1]!, /align-items:\s*center/);
  assert.match(btnMatch[1]!, /justify-content:\s*center/);
  assert.match(btnMatch[1]!, /text-align:\s*center/);
  assert.doesNotMatch(btnMatch[1]!, /(?:^|[;\s])height:\s*34px/, ".flode-segment button får inte ha hårdlåst height: 34px");

  const litenMatch = FLODE_CSS.match(/\.flode-segment\.liten\s+button\s*\{([^}]+)\}/);
  assert.ok(litenMatch, "Saknar .flode-segment.liten button-regel i flode.css");
  assert.match(litenMatch[1]!, /min-height:\s*28px/);
  assert.match(litenMatch[1]!, /padding:\s*4px\s+6px/);
  assert.doesNotMatch(litenMatch[1]!, /(?:^|[;\s])height:\s*28px/, ".flode-segment.liten button får inte ha hårdlåst height: 28px");
});
