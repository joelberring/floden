/*
 * Körs med `npm test`. Deterministiska regressionstester för Våg 2 UI-fixar:
 * - W-B4: `#rs-osakerhet` triggar osäkerhetsåtgärden och 3D `avlasning`/`text`/`fel` når DOM (#ar-avl / #ar-hjalp).
 * - W-B5: `SLAGTEXT["lage_idag"]` definierat (aldrig "undefined") och inga "steg 3"/#/fragor-texter i rapportskapa.ts.
 * - W-B8: Ekologi i nuläge (nätverksval + ECA/PC/fläckar) samt domänkorrekt hjälptext (ej hårdkodat "vattendjup").
 * - W-B9: `korning.ts` stämplar serverns faktiska regn-/solsignaturer (inte klientens nuvarande inställning).
 * - Uppdrag 4 + Ekologi i Steg 3: validering av CSV/bakgrundshalt och exakt JSON-payload mot POST /api/projekt/<pid>/rapporter.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import module from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { byggRapportJobbKropp, startaRapportJobb } from "../api.ts";
import type { AvlasningSvar } from "../fragor-typer.ts";
import type { KorningPost, KorningRegnInfo, KorningSolInfo } from "../karta/kartapi.ts";
import type { ProjektVy, RapportAmne } from "../types.ts";
import { startaKorning, startaLageIdag, tillampaKlaraKorningar, type KorningApi } from "./korning.ts";
import { osakerhetHtml } from "./panelhtml.ts";
import {
  behoverLuftForm,
  byggLuftIndata,
  byggRapportValForSkapa,
  knappLage,
  MAX_LUFT_CSV_BYTES,
  skapaPanelHtml,
  SLAGTEXT,
  TOMT_LUFT_FORM,
  underlagHtml,
  valideraLuftIndata,
  valjUnderlag,
  type LuftFormTillstand,
} from "./rapportskapa.ts";
import { medRegnForval, regnSignatur, STANDARD_REGNVAL } from "./regnval.ts";
import { solSignatur, STANDARD_SOLVAL } from "./solval.ts";
import { bedomInstallningsStatus, FORVAL_BESTALLNING, tillstandFor } from "./tillstand.ts";

const FLODE_MAPP = path.dirname(fileURLToPath(import.meta.url));
const ARBETSYTA_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.ts"), "utf-8");
const RAPPORTSKAPA_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "rapportskapa.ts"), "utf-8");

// Ladda rena exporter från arbetsyta.ts direkt i Node 24 genom att stubba enbart webbläsar-/WebGL-beroenden.
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

// ------------------------------------------------------------------ W-B4: #rs-osakerhet + 3D callbacks

test("W-B4: #rs-osakerhet matchar panelhtml.ts och finns i arbetsytans klickselektor (inga #ar-osakerhet)", async () => {
  const html = osakerhetHtml({ pa: false, laddar: false, tal: [], rader: [], fel: [], overfallLager: null });
  const idMatch = html.match(/id="([^"]+)"/);
  assert.equal(idMatch?.[1], "rs-osakerhet");

  const ar = await laddaArbetsytaExports();
  assert.equal(ar.OSAKERHET_KNAPP_ID, "rs-osakerhet");
  const selektorer = ar.ARBETSYTA_KLICK_SELEKTOR.split(",").map((s) => s.trim());
  assert.ok(selektorer.includes("#rs-osakerhet"), "Klickselektorn måste lyssna på #rs-osakerhet");
  assert.ok(!selektorer.includes("#ar-osakerhet"), "Gamla #ar-osakerhet får inte finnas kvar");
  assert.doesNotMatch(ARBETSYTA_KALLTEXT, /ar-osakerhet/, "Inga förekomster av ar-osakerhet får finnas kvar i arbetsyta.ts");
  assert.match(ARBETSYTA_KALLTEXT, /target\.closest<HTMLElement>\(ARBETSYTA_KLICK_SELEKTOR\)/);
  assert.match(ARBETSYTA_KALLTEXT, /if \(m\.id === OSAKERHET_KNAPP_ID\)/);
});

test("W-B4: 3D avlasning, text och fel når DOM-elementen #ar-avl och #ar-hjalp (aldrig () => undefined)", async () => {
  // Kontrollera källkoden så båda skapa3dVy-anropen (dockad 3D och helskärms-3D) är kopplade utan no-op.
  assert.doesNotMatch(ARBETSYTA_KALLTEXT, /avlasning:\s*\(\)\s*=>\s*undefined/);
  assert.doesNotMatch(ARBETSYTA_KALLTEXT, /text:\s*\(\)\s*=>\s*undefined/);
  assert.doesNotMatch(ARBETSYTA_KALLTEXT, /fel:\s*\(\)\s*=>\s*undefined/);
  const kopplade = ARBETSYTA_KALLTEXT.match(/\.\.\.koppla3dAterkoppling\(\)/g) ?? [];
  assert.equal(kopplade.length, 2, "Både vy3dDock och vy3d ska använda koppla3dAterkoppling()");

  const ar = await laddaArbetsytaExports();

  // Mockade DOM-element för #ar-avl och #ar-hjalp
  const avlEl = { hidden: true, innerHTML: "" };
  const hjalpKlasser = new Set<string>();
  const hjalpAttr = new Map<string, string>();
  const hjalpEl = {
    hidden: true,
    textContent: "",
    setAttribute(k: string, v: string) {
      hjalpAttr.set(k, v);
    },
    classList: {
      add(...xs: string[]) {
        for (const x of xs) hjalpKlasser.add(x);
      },
      remove(...xs: string[]) {
        for (const x of xs) hjalpKlasser.delete(x);
      },
      toggle(x: string, force?: boolean) {
        if (force === undefined) {
          if (hjalpKlasser.has(x)) {
            hjalpKlasser.delete(x);
            return false;
          }
          hjalpKlasser.add(x);
          return true;
        }
        if (force) hjalpKlasser.add(x);
        else hjalpKlasser.delete(x);
        return force;
      },
    },
  };

  let treDText: string | null = null;
  let treDFel: string | null = null;
  const avlKoord: Array<[number | null, number | null]> = [];
  const vy = { fore_lager: "skyfall.fore.max_djup", efter_lager: "skyfall.efter.max_djup", skillnad_lager: "skyfall.skillnad.max_djup", lage_idag: false };

  const ritaAvl = (senasteAvl: AvlasningSvar | null, hojdText: string | null) =>
    ar.renderaAvlasningElement(avlEl, {
      senasteAvl,
      vy,
      lage: "3d",
      amne: "vatten",
      visaTak: false,
      treDText,
      hojdText,
    });

  const cb = ar.skapa3dAterkoppling({
    avlasning: (e, n) => {
      avlKoord.push([e, n]);
      if (e !== null && n !== null) {
        ritaAvl(
          {
            e,
            n,
            analys: "skyfall",
            roll: "max_djup",
            varden: [{ lager_id: "skyfall.fore.max_djup", namn: "Maxdjup", etikett: "Maxdjup", typ: "bas", scenario: "fore", enhet: "m", inom: true, tak: false, varde: 0.25 }],
          },
          "Mark +14,2 m",
        );
      } else {
        ritaAvl(null, null);
      }
    },
    sattText: (txt) => {
      treDText = txt;
      ritaAvl(null, null);
    },
    sattFel: (txt) => {
      treDFel = txt;
      ar.renderaHjalpElement(hjalpEl, {
        treDFel,
        sektionAktiv: false,
        sektionText: "",
        matAktiv: false,
        matText: "",
        redigeraAktiv: false,
        meddelande: null,
        verktygText: "",
      });
    },
  });

  // 1. Markhovring i 3D -> når #ar-avl (även i lage === "3d")
  cb.avlasning(150123, 6580456);
  assert.deepEqual(avlKoord[0], [150123, 6580456]);
  assert.equal(avlEl.hidden, false);
  assert.match(avlEl.innerHTML, /25 cm/);
  assert.match(avlEl.innerHTML, /Mark \+14,2 m/);

  // 2. Objekt-/fasadhovring i 3D -> når #ar-avl med text
  cb.text("Fasadpunkt VSC 28,4 %");
  assert.equal(avlEl.hidden, false);
  assert.match(avlEl.innerHTML, /Fasadpunkt VSC 28,4 %/);

  // 3. Lämnar 3D-objekt -> #ar-avl döljs
  cb.text(null);
  assert.equal(avlEl.hidden, true);
  assert.equal(avlEl.innerHTML, "");

  // 4. WebGL-/nätfel i 3D -> når #ar-hjalp med role="alert" och felklass
  cb.fel("WebGL-kontexten kunde inte skapas");
  assert.equal(hjalpEl.hidden, false);
  assert.equal(hjalpAttr.get("role"), "alert");
  assert.ok(hjalpKlasser.has("fel") && hjalpKlasser.has("flode-fel"));
  assert.equal(hjalpEl.textContent, "3D-vy: WebGL-kontexten kunde inte skapas");
});

// ------------------------------------------------------------------ W-B5: lage_idag & steg 2-länkar

test("W-B5: SLAGTEXT['lage_idag'] är definierat och inga 'steg 3'- eller #/fragor-texter finns kvar i rapportskapa.ts", () => {
  assert.equal(SLAGTEXT.lage_idag, "Läget idag");
  assert.doesNotMatch(RAPPORTSKAPA_KALLTEXT, /steg 3/i, "rapportskapa.ts får inte hänvisa till gamla steg 3");
  assert.doesNotMatch(RAPPORTSKAPA_KALLTEXT, /flodeAdress\([^)]*["']fragor["']\)/, "rapportskapa.ts får inte länka till fragor");
  assert.doesNotMatch(RAPPORTSKAPA_KALLTEXT, /#\/[^\s"']*fragor/, "rapportskapa.ts får inte innehålla #/fragor");

  const amne: RapportAmne = {
    id: "skyfall",
    namn: "Skyfall",
    kan_skapas: true,
    forklaring: null,
    mall: true,
    forval_korning: "k-idag",
    forval_slag: "lage_idag",
    korningar: [{ korning_id: "k-idag", fraga_id: "f", skapad: "2026-11-26", slag: "lage_idag", scenario: null }],
  };
  const u = valjUnderlag(amne, null, null, null);
  assert.equal(u.slag, "lage_idag");
  assert.equal(u.korningText, "Körning k-idag: Läget idag.");
  assert.doesNotMatch(u.varning ?? "", /undefined/);
  const html = underlagHtml("goteborg-test", u);
  assert.match(html, /href="#\/p\/goteborg-test\/karta"/);
  assert.match(html, /steg 2 \(Karta &amp; analys\)/);
  assert.doesNotMatch(html, /undefined|\/fragor|steg 3/i);
});

// ------------------------------------------------------------------ W-B8: Ekologi i nuläge & domänkorrekt text

test("W-B8: Ekologi-fliken i nuläge visar mätvärden (ECA/PC/fläckar), nätverksval och domänkorrekt hjälptext", async () => {
  const ar = await laddaArbetsytaExports();

  // Domänkorrekt hjälptext: aldrig "vattendjup" för sol, ekologi eller luft
  for (const amne of ["dagsljus", "ekologi", "luft"] as const) {
    const txt = ar.nulageTipsHtmlForAmne(amne);
    assert.doesNotMatch(txt, /vattendjup|rinnstråk|ovanpå vattnet/i, `${amne} ska inte visa vattendjupstext`);
  }
  assert.match(ar.nulageTipsHtmlForAmne("vatten"), /vattendjup/i);

  // Ekologi nulägesmätvärden
  const ekoHtml = ar.nulageEkologiTalHtml({
    troskel: null,
    siffror: null,
    ekologi: {
      fore: { ECA_m2: 145000, ECA_intervall_m2: [120000, 170000], PC: 0.0412, antal_flackar: 18 },
    },
  });
  assert.match(ekoHtml, /ECA före/);
  assert.match(ekoHtml, /14,50 ha/);
  assert.match(ekoHtml, /PC före/);
  assert.match(ekoHtml, /0,0412/);
  assert.match(ekoHtml, /Habitatfläckar i nuläget/);
  assert.match(ekoHtml, />18</);

  // Null-värde vid hovring på Sol ger "inget värde", inte "torr mark (0 cm)"
  const avlEl = { hidden: true, innerHTML: "" };
  ar.renderaAvlasningElement(avlEl, {
    senasteAvl: {
      e: 150000,
      n: 6580000,
      analys: "sol",
      roll: "soltimmar",
      varden: [{ lager_id: "sol.fore", namn: "Sol", etikett: "Soltimmar", typ: "bas", scenario: "fore", enhet: "h", inom: true, tak: false, varde: null }],
    },
    vy: { fore_lager: "sol.fore", efter_lager: null, skillnad_lager: "sol.fore", lage_idag: true },
    lage: "nulage",
    amne: "dagsljus",
    visaTak: false,
    treDText: null,
    hojdText: null,
  });
  assert.match(avlEl.innerHTML, /inget värde/);
  assert.doesNotMatch(avlEl.innerHTML, /torr mark/);

  // valjVy laddar ekologi även i kalla === "lage" och ritaAnalysPanel renderar natverkVal i nuläge
  assert.doesNotMatch(ARBETSYTA_KALLTEXT, /if\s*\(amne\s*===\s*"ekologi"\s*&&\s*kalla\s*!==\s*"lage"\)/);
  assert.match(ARBETSYTA_KALLTEXT, /\$\{kallVal\}\$\{solBlock\}\$\{natverkVal\}\$\{lagerBlock\}/);
});

// ------------------------------------------------------------------ W-B9: korning.ts stämplar serverns signaturer

const SERVER_STANDARD_REGN: KorningRegnInfo = {
  aterkomsttid_ar: 100,
  varaktighet_min: 60,
  klimatfaktor: 1.25,
  regn_mm: 68.5,
  berakningstyp: "statisk",
  regntyp: "block",
  kalla: "stockholmsregn-2024",
  ar_standard: true,
};

const SERVER_STANDARD_SOL: KorningSolInfo = {
  datum_iso: "2025-03-21",
  start_timme: null,
  slut_timme: null,
  sammanhangande: false,
  inkludera_vsc: false,
  klockslag_utc: null,
  ar_standard: true,
};

function skapaVy(avsnitt: ProjektVy["avsnitt"], lageIdag: boolean): ProjektVy {
  return {
    id: `v-${avsnitt}`,
    avsnitt,
    rubrik: avsnitt,
    korning_id: `k-${avsnitt}`,
    fore_lager: `${avsnitt}.fore`,
    efter_lager: lageIdag ? null : `${avsnitt}.efter`,
    skillnad_lager: lageIdag ? `${avsnitt}.fore` : `${avsnitt}.skillnad`,
    efter_namn: lageIdag ? "Nuläge" : "Förslag",
    andring: "",
    mattnamn: avsnitt,
    lage_idag: lageIdag,
  };
}

test("W-B9: korning.ts stämplar serverns faktiska regn-/solsignatur (inte klientens aktuella regn/sol)", async () => {
  const t = tillstandFor("v2-wb9-korning");
  t.scenario = { slug: "mitt-forslag", namn: "Mitt förslag" };
  // Klienten står på 10-årsregn + avtappning 15 mm/h + midsommar
  t.regn = medRegnForval(STANDARD_REGNVAL, "10ar");
  t.avtappning = 15;
  t.sol = { ...STANDARD_SOLVAL, datumForval: "midsommar" };

  // Serverns klara körningar är däremot gjorda med standardregn (avtappning null) + vårdagjämning
  const serverKorningar = (lageIdag: boolean): KorningPost[] => [
    {
      mall: "skyfall",
      status: { status: "klar" },
      vyer: [skapaVy("vatten", lageIdag)],
      regn: SERVER_STANDARD_REGN,
      avtappning_mm_h: null,
      sol: null,
    },
    {
      mall: "dagsljus",
      status: { status: "klar" },
      vyer: [skapaVy("dagsljus", lageIdag)],
      regn: null,
      avtappning_mm_h: null,
      sol: SERVER_STANDARD_SOL,
    },
  ];

  const mockApi: KorningApi = {
    korLageIdag: async () => ({ lage: "idag", fragor: [] }),
    korScenario: async () => ({ slug: "mitt-forslag", fragor: [] }),
    lageIdagStatus: async () => ({ korningar: serverKorningar(true) }),
    scenarioStatus: async () => ({ korningar: serverKorningar(false) }),
  };

  await startaLageIdag(t, ["vatten.skyfall", "dagsljus.sol"], mockApi);
  assert.equal(t.korning?.klar, true);
  assert.equal(t.lageRegnSignatur, regnSignatur(STANDARD_REGNVAL, null));
  assert.notEqual(t.lageRegnSignatur, regnSignatur(t.regn, t.avtappning));
  assert.equal(t.lageSolSignatur, solSignatur(STANDARD_SOLVAL));
  assert.notEqual(t.lageSolSignatur, solSignatur(t.sol));

  await startaKorning(t, ["vatten.skyfall", "dagsljus.sol"], mockApi);
  assert.equal(t.korning?.klar, true);
  assert.equal(t.ritRegnSignatur, regnSignatur(STANDARD_REGNVAL, null));
  assert.notEqual(t.ritRegnSignatur, regnSignatur(t.regn, t.avtappning));
  assert.equal(t.ritSolSignatur, solSignatur(STANDARD_SOLVAL));
  assert.notEqual(t.ritSolSignatur, solSignatur(t.sol));

  // Klientens ändrade inställningar flaggas då korrekt som utdaterade mot serverns faktiska körning
  const st = bedomInstallningsStatus(t, "vatten");
  assert.equal(st.regnUtdaterat, true);
  assert.equal(st.solUtdaterat, true);

  // Om servern saknar regn-/solmetadata stämplas null (aldrig klientens nuvarande värden)
  tillampaKlaraKorningar(
    t,
    [{ mall: "skyfall", status: { status: "klar" }, vyer: [skapaVy("vatten", true)], regn: null, sol: null }],
    true,
  );
  assert.equal(t.lageRegnSignatur, null);
  assert.equal(t.lageSolSignatur, null);
});

// ------------------------------------------------------------------ Uppdrag 4: Luft i Steg 3 + exakt API-payload

test("Uppdrag 4: Luft-formuläret nekar saknad CSV, tom/negativ/NaN bakgrundshalt och för stor CSV", () => {
  const csv = "tid_s,halt_ug_m3\n0,15.0\n3600,18.0";
  const luftAmne: RapportAmne = {
    id: "luft",
    namn: "Luft",
    kan_skapas: true,
    forklaring: "Ange extern timserie och bakgrundshalt.",
    mall: true,
    forval_korning: null,
    korningar: [],
  };

  const ogiltiga: Array<[string, LuftFormTillstand]> = [
    ["tom CSV före", { ...TOMT_LUFT_FORM, serie_fore_csv: "   ", bakgrund_text: "12" }],
    ["tom bakgrundshalt", { ...TOMT_LUFT_FORM, serie_fore_csv: csv, bakgrund_text: "" }],
    ["negativ bakgrundshalt", { ...TOMT_LUFT_FORM, serie_fore_csv: csv, bakgrund_text: "-0,5" }],
    ["icke-numerisk bakgrundshalt (NaN)", { ...TOMT_LUFT_FORM, serie_fore_csv: csv, bakgrund_text: "abc" }],
    ["CSV före över 5 MB", { ...TOMT_LUFT_FORM, serie_fore_csv: "a".repeat(MAX_LUFT_CSV_BYTES + 1), bakgrund_text: "10" }],
  ];
  for (const [namn, form] of ogiltiga) {
    assert.ok(valideraLuftIndata(form), `Förväntade valideringsfel för ${namn}`);
    assert.equal(byggLuftIndata(form).ok, false, `byggLuftIndata ska neka ${namn}`);
    assert.equal(knappLage(luftAmne, FORVAL_BESTALLNING, false, false, form).aktiv, false, `Knappen ska vara inaktiv för ${namn}`);
  }

  // Noll (0) är giltig explicit bakgrundshalt
  const nollRes = byggLuftIndata({ ...TOMT_LUFT_FORM, serie_fore_csv: csv, bakgrund_text: "0" });
  assert.equal(nollRes.ok, true);
  if (nollRes.ok) {
    assert.equal(nollRes.data.bakgrund_ug_m3, 0);
    assert.equal(nollRes.data.serie_efter_csv, null);
  }
});

test("Uppdrag 4: Steg 3 bygger och skickar exakt payload { amnen: ['luft'], luft: { serie_fore_csv, serie_efter_csv, bakgrund_ug_m3 } }", async () => {
  const luftAmne: RapportAmne = {
    id: "luft",
    namn: "Luft",
    kan_skapas: true,
    forklaring: null,
    mall: true,
    forval_korning: "k-gammal-luft",
    korningar: [{ korning_id: "k-gammal-luft", fraga_id: "LU-01", skapad: "2026-11-26", slag: "egen", scenario: null }],
  };

  // Med befintlig körning och anvandNySerie=false används befintlig körning
  assert.equal(behoverLuftForm(luftAmne, { ...TOMT_LUFT_FORM, anvandNySerie: false }), false);
  const uBef = valjUnderlag(luftAmne, null, null, null, false);
  const valBef = byggRapportValForSkapa(luftAmne, uBef, { ...TOMT_LUFT_FORM, anvandNySerie: false });
  assert.equal(valBef.ok, true);
  if (valBef.ok) {
    assert.equal(valBef.val.korning_id, "k-gammal-luft");
    assert.equal(valBef.val.luft, undefined);
  }

  // När användaren väljer ny timserie (eller när ingen luftkörning finns) skickas luft-objektet utan gammalt korning_id
  const form: LuftFormTillstand = {
    anvandNySerie: true,
    serie_fore_csv: "tid_s,halt_ug_m3\n0,12.0\n3600,19.5",
    serie_efter_csv: "tid_s,halt_ug_m3\n0,11.0\n3600,17.2",
    bakgrund_text: "14,5",
  };
  assert.equal(behoverLuftForm(luftAmne, form), true);
  const uNy = valjUnderlag(luftAmne, null, null, null, true);
  const valNy = byggRapportValForSkapa(luftAmne, uNy, form);
  assert.equal(valNy.ok, true);
  if (!valNy.ok) throw new Error("oväntat fel");

  const kropp = byggRapportJobbKropp(
    "luft",
    { syfte: FORVAL_BESTALLNING.syfte, skede: FORVAL_BESTALLNING.skede, mottagare_roll: FORVAL_BESTALLNING.mottagare },
    valNy.val,
  );
  assert.equal(kropp.amne, "luft");
  assert.deepEqual(kropp.amnen, ["luft"]);
  assert.equal(kropp.korning_id, undefined);
  assert.deepEqual(kropp.luft, {
    serie_fore_csv: "tid_s,halt_ug_m3\n0,12.0\n3600,19.5",
    serie_efter_csv: "tid_s,halt_ug_m3\n0,11.0\n3600,17.2",
    bakgrund_ug_m3: 14.5,
  });

  // Verifiera faktiskt HTTP-anrop via startaRapportJobb (med mockad fetch & document)
  const gammalFetch = globalThis.fetch;
  const gammalDoc = (globalThis as { document?: unknown }).document;
  let skickadUrl = "";
  let skickadKropp: Record<string, unknown> = {};
  (globalThis as { document?: unknown }).document = { cookie: "" };
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    skickadUrl = String(url);
    skickadKropp = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
    return new Response(
      JSON.stringify({
        kontrakt: "K48/v1",
        jobb_id: "j-0123456789ab",
        projekt: "goteborg-test",
        amne: "luft",
        korning_id: "k-ny",
        bestallning_id: "b-0123456789ab",
        status: "ko",
        steg: null,
        steg_nr: 0,
        steg_totalt: 4,
        text: "Väntar på sin tur.",
        rapport_id: null,
        kontroll: null,
        lankar: null,
        fel: null,
        status_url: "/api/projekt/goteborg-test/rapporter/jobb/j-0123456789ab",
      }),
      { status: 202, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;
  try {
    await startaRapportJobb(
      "goteborg-test",
      "luft",
      { syfte: "Luftutredning", skede: "skiss", mottagare_roll: "planhandlaggare" },
      valNy.val,
    );
  } finally {
    globalThis.fetch = gammalFetch;
    if (gammalDoc === undefined) delete (globalThis as { document?: unknown }).document;
    else (globalThis as { document?: unknown }).document = gammalDoc;
  }
  assert.equal(skickadUrl, "/api/projekt/goteborg-test/rapporter");
  assert.deepEqual(skickadKropp.amnen, ["luft"]);
  assert.deepEqual(skickadKropp.luft, {
    serie_fore_csv: "tid_s,halt_ug_m3\n0,12.0\n3600,19.5",
    serie_efter_csv: "tid_s,halt_ug_m3\n0,11.0\n3600,17.2",
    bakgrund_ug_m3: 14.5,
  });

  // Bakgrundsfältet använder type="text" + inputmode="decimal" (inte type="number") så "12,5" bevaras vid omritning
  const formHtml = skapaPanelHtml({
    projekt: "p",
    underlag: uNy,
    amnen: [luftAmne],
    valt: "luft",
    best: FORVAL_BESTALLNING,
    jobb: null,
    fel: null,
    luftForm: form,
  });
  const bakgInputMatch = formHtml.match(/<input[^>]*id="rp-luft-bakgrund"[^>]*>/);
  assert.ok(bakgInputMatch, "Saknar #rp-luft-bakgrund i luftformuläret");
  assert.match(bakgInputMatch[0], /type="text"/);
  assert.match(bakgInputMatch[0], /inputmode="decimal"/);
  assert.doesNotMatch(bakgInputMatch[0], /type="number"/);
  assert.match(bakgInputMatch[0], /value="14,5"/);

  // XSS-escaping i luftformuläret
  const xssHtml = skapaPanelHtml({
    projekt: "p",
    underlag: uNy,
    amnen: [luftAmne],
    valt: "luft",
    best: FORVAL_BESTALLNING,
    jobb: null,
    fel: null,
    luftForm: { ...form, serie_fore_csv: "<script>alert(1)</script>", bakgrund_text: '"><img src=x>' },
  });
  assert.doesNotMatch(xssHtml, /<script>alert\(1\)<\/script>/);
  assert.doesNotMatch(xssHtml, /<img src=x>/);
});
