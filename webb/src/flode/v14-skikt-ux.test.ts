/*
 * Körs med `npm test`. Deterministiska kontrakt- och enhetstester för Etapp 2 (PR-C):
 * Vänsterställd 6-domäns skikt-UX i Steg 2:
 * 1. Vänsterpanel (`#ar-skikt-panel`) med alla 6 planeringsdomäner ALLTID synliga, ärliga
 *    statusprickar/etiketter (`Klar` / `Kör` / `Skiss`), snabbkryssrutor för kartlager +
 *    opacitetsreglage (`20 %–100 %`), hopfällning på smal skärm och automatisk döljning i Redigera.
 * 2. Ekologi & GYF utan återvändsgränd: aldrig tyst fallback till Skyfall, direkt `GET /api/analys/ekologi`
 *    även utan K20-vy, och automatiskt förvald kommunprofil ur `info.kommun`.
 * 3. Upplyft Mikroklimat (UTCI / T_mrt, 3 en-klicks väderförval + legend, ej `<details>`),
 *    upplyft Buller (`SKISS — ej Nord2000-konform (formell bullerrapport spärrad)` alltid synlig +
 *    2-klicks "Rita väg på kartan") och alltid synlig Vind & Luft med ärlig förklaring.
 * 4. Utvecklarbrus (`extraAnalyserHtml` / `A1_enkel_sanka`) helt rensat ur arbetsytan.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import module from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { OSM_ID } from "../karta/osm.ts";
import { PALETTER } from "./analyslager.ts";
import {
  BULLER_SKISS_BANNER,
  STANDARD_BULLER_PARAM,
  bullerPanelHtml,
  byggBullerVagLinje,
  tillampaBullerVagKlick,
  type BullerAnalysSvar,
} from "./bullerflik.ts";
import {
  STANDARD_VARMESTRESS_PARAM,
  VARMESTRESS_FORVAL,
  forvaldGyfProfilForKommun,
  gyfPanelHtml,
  varmestressPanelHtml,
  type VarmestressAnalysSvar,
} from "./gyf-utci.ts";
import { LUFT_SKISS_NOTIS, luftTomPanelHtml } from "./luftflik.ts";
import { BYGGNADER_ID, HOJDKURVOR_ID } from "./rasterlager.ts";

const FLODE_MAPP = path.dirname(fileURLToPath(import.meta.url));
const ARBETSYTA_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.ts"), "utf-8");
const ARBETSYTA_CSS = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.css"), "utf-8");

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
export const TANGENTER = { valj: "Q", hus: "W", yta: "⇧W", linje: "E", angra: "Z", laggtill: "A", tabort: "S", klipp: "X", ihop: "G", vand: "V", forenkla: "⇧S", fast: "T", foljkant: "F", ratVinkel: "R" };
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

// ------------------------------------------------------------------ 1. Vänster 6-domäns skiktpanel

test("Etapp 2: vänsterställd skiktpanel visar ALLA 6 planeringsdomäner, statusprickar, kartlager och opacitet 20–100 %", async () => {
  const ar = await laddaArbetsytaExports();

  assert.deepEqual(ar.AMNEN_ORDNING, ["vatten", "dagsljus", "mikroklimat", "ekologi", "luft", "buller"]);

  const statusBas = {
    aktivDoman: "vatten" as const,
    pagarK20: false,
    harVyer: new Set<string>(["vatten"]),
    harNmd: false,
    ekologiHarSvar: false,
    gyfLaddar: false,
    gyfHarSvar: false,
    vsLaddar: false,
    vsHarSvar: false,
    bullerLaddar: false,
    bullerHarSvar: false,
  };

  assert.equal(ar.domanStatus("vatten", statusBas).etikett, "Klar");
  assert.equal(ar.domanStatus("dagsljus", statusBas).etikett, "Kör");
  assert.equal(ar.domanStatus("mikroklimat", statusBas).etikett, "Skiss");
  assert.equal(ar.domanStatus("ekologi", statusBas).etikett, "Skiss");
  assert.equal(ar.domanStatus("luft", statusBas).etikett, "Skiss");
  assert.equal(ar.domanStatus("buller", statusBas).etikett, "Skiss");

  // Under pågående beräkning eller efter svar uppdateras status ärligt (Buller behåller alltid Skiss)
  assert.equal(ar.domanStatus("vatten", { ...statusBas, pagarK20: true }).etikett, "Kör");
  assert.equal(ar.domanStatus("mikroklimat", { ...statusBas, vsLaddar: true }).etikett, "Kör");
  assert.equal(ar.domanStatus("mikroklimat", { ...statusBas, vsHarSvar: true }).etikett, "Klar");
  assert.equal(ar.domanStatus("ekologi", { ...statusBas, gyfHarSvar: true }).etikett, "Klar");
  assert.equal(ar.domanStatus("luft", { ...statusBas, harVyer: new Set(["vatten", "luft"]) }).etikett, "Klar");
  assert.equal(ar.domanStatus("buller", { ...statusBas, bullerHarSvar: true }).etikett, "Skiss");

  const html = ar.skiktPanelHtml({
    aktivDoman: "vatten",
    statusIndata: statusBas,
    hopfalld: false,
    basSynlig: { osm: true, hojdkurvor: true, byggnader: true },
    tillgangligaVektorer: ["flodesvagar", "sankor"],
    aktivaVektorer: new Set(["flodesvagar"]),
    rasterOpacitet: 0.85,
  });

  // Alla 6 domäner synliga med data-amne och role="tab" (aldrig hidden på luft!)
  assert.match(html, /role="tablist"[^>]*aria-orientation="vertical"[^>]*id="ar-flikar"/);
  for (const [id, namn] of [
    ["vatten", "Skyfall"],
    ["dagsljus", "Sol &amp; Dagsljus"],
    ["mikroklimat", "Mikroklimat"],
    ["ekologi", "Ekologi &amp; GYF"],
    ["luft", "Vind &amp; Luft"],
    ["buller", "Buller"],
  ] as const) {
    assert.match(html, new RegExp(`role="tab"[^>]*data-amne="${id}"`));
    assert.match(html, new RegExp(namn));
  }
  assert.doesNotMatch(html, /data-amne="luft"[^>]*hidden/);
  assert.match(html, /data-amne="vatten"[^>]*aria-selected="true"[^>]*tabindex="0"/);
  assert.match(html, /data-amne="buller"[^>]*aria-selected="false"[^>]*tabindex="-1"/);

  // Snabbkryssrutor: OSM, Höjdkurvor, Byggnader + tillgängliga Flödesvägar & Sänkor + opacitetsreglage 20–100 %
  assert.match(html, new RegExp(`data-skikt-bas="${OSM_ID}" checked`));
  assert.match(html, new RegExp(`data-skikt-bas="${HOJDKURVOR_ID}" checked`));
  assert.match(html, new RegExp(`data-skikt-bas="${BYGGNADER_ID}" checked`));
  assert.match(html, /data-skikt-vektor="flodesvagar" checked/);
  assert.match(html, /data-skikt-vektor="sankor"/);
  assert.match(html, /id="ar-raster-opacitet"[^>]*min="20"[^>]*max="100"[^>]*value="85"/);

  // Utan flödesvägar/sänkor visas bara de 3 baslagren
  const utanVektor = ar.skiktLagerHtml({
    basSynlig: { osm: false, hojdkurvor: true, byggnader: true },
    tillgangligaVektorer: [],
    aktivaVektorer: new Set(),
  });
  assert.doesNotMatch(utanVektor, /Flödesvägar|Sänkor/);

  // Infällt läge (C5): flikarna syns kvar, blocket göms och fäll-knappen säger vilket ämne som fälls ut
  const hopfalldHtml = ar.skiktPanelHtml({
    aktivDoman: "mikroklimat",
    statusIndata: { ...statusBas, aktivDoman: "mikroklimat" },
    hopfalld: true,
    basSynlig: { osm: true, hojdkurvor: true, byggnader: true },
    tillgangligaVektorer: [],
    aktivaVektorer: new Set(),
    rasterOpacitet: 0.7,
  });
  assert.match(hopfalldHtml, /id="ar-skikt-vaxla"[^>]*aria-expanded="false"[^>]*aria-label="Fäll ut Mikroklimat"/);
  assert.match(hopfalldHtml, /id="ar-skikt-kropp" hidden/);
  assert.match(hopfalldHtml, /role="tab"[^>]*data-amne="mikroklimat"[^>]*aria-selected="true"/);
  assert.match(html, /id="ar-skikt-vaxla"[^>]*aria-expanded="true"[^>]*aria-label="Fäll in skiktpanelen"/);
  assert.match(html, /id="ar-skikt-kropp" data-skikt-amne="vatten"/);

  // Opacitetsklämning [0,20; 1,00]
  assert.equal(ar.klammaRasterOpacitet(0.05), 0.2);
  assert.equal(ar.klammaRasterOpacitet(1.4), 1.0);
  assert.equal(ar.klammaRasterOpacitet(Number.NaN), 0.85);
  assert.equal(ar.klammaRasterOpacitet(0.65), 0.65);

  // Källkodskontroll: #ar-skikt-panel finns och göms i Redigera förslag + klickselektorn har #ar-skikt-vaxla
  assert.match(ARBETSYTA_KALLTEXT, /id="ar-skikt-panel"/);
  assert.match(ARBETSYTA_KALLTEXT, /el\("ar-skikt-panel"\)\.hidden = redigera;/);
  assert.ok(ar.ARBETSYTA_KLICK_SELEKTOR.split(",").includes("#ar-skikt-vaxla"));
});

// ------------------------------------------------------------------ 2. Ekologi & GYF utan återvändsgränd

test("Etapp 2: Ekologi faller ALDRIG tyst tillbaka på Skyfall och GYF förväljer kommunprofil ur info.kommun", async () => {
  const ar = await laddaArbetsytaExports();

  const geoUtanNmd = {
    id: "goteborg-haga",
    har_nmd: false,
    analyser: [
      { id: "vatten.skyfall", namn: "Skyfall", mall: "vatten", analys: "skyfall", forval: true },
      { id: "dagsljus.sol", namn: "Soltimmar", mall: "dagsljus", analys: "sol", forval: true },
    ],
  };
  assert.deepEqual(ar.analyserForAktivtAmne(geoUtanNmd, "ekologi"), []);
  assert.equal(ar.harEkologiUnderlag(geoUtanNmd), false);
  assert.equal(ar.kanStartaKorningForAnalyser(ar.analyserForAktivtAmne(geoUtanNmd, "ekologi"), null, false, null), false);

  const geoMedNmd = { ...geoUtanNmd, har_nmd: true };
  assert.equal(ar.harEkologiUnderlag(geoMedNmd), true);
  assert.equal(ar.harEkologiUnderlag({ id: "demokvarteret", har_nmd: false, analyser: [] }), true);

  // Automatiskt förvald kommunprofil för GYF
  assert.equal(forvaldGyfProfilForKommun("goteborg"), "goteborg");
  assert.equal(forvaldGyfProfilForKommun("goteborg-test"), "goteborg");
  assert.equal(forvaldGyfProfilForKommun(null, "goteborg-haga@v1"), "goteborg");
  assert.equal(forvaldGyfProfilForKommun("malmo"), "malmo");
  assert.equal(forvaldGyfProfilForKommun("stockholm"), "stockholm");
  assert.equal(forvaldGyfProfilForKommun(null, null), "stockholm");

  // GYF-panelen är en direkt öppen <section>, inte hopfälld <details>
  const gyfHtml = gyfPanelHtml({
    profil: "goteborg",
    laddar: false,
    fel: null,
    svar: null,
    scenario: null,
  });
  assert.match(gyfHtml, /<section\b[^>]*id="ar-gyf-panel"/);
  assert.doesNotMatch(gyfHtml, /<details\b[^>]*id="ar-gyf-panel"/);
  assert.match(gyfHtml, /<option value="goteborg" selected>Göteborg<\/option>/);

  // Ekologi-anrop via GET /api/analys/ekologi sker även när ingen K20-vy (ny === null) finns
  assert.match(ARBETSYTA_KALLTEXT, /if \(amne === "ekologi" && harEkologiUnderlag\(info\)\)/);
  assert.match(ARBETSYTA_KALLTEXT, /forvaldGyfProfilForKommun\(info\.kommun, info\.geografi\)/);
  assert.match(ar.EKOLOGI_UTAN_NMD_TEXT, /Geografin saknar NMD-underlag för konnektivitet/);
  assert.match(ar.EKOLOGI_UTAN_NMD_TEXT, /Grönytefaktor \(GYF\)/);
});

// ------------------------------------------------------------------ 3. Upplyft Mikroklimat (UTCI), Buller (Rita väg) & Luft

test("Etapp 2: Mikroklimat (UTCI) är upplyft utan <details> med 3 en-klicks väderförval och legend", () => {
  assert.equal(VARMESTRESS_FORVAL.length, 2);
  const vsSvar: VarmestressAnalysSvar = {
    format: "floden-varmestress-webb/v1",
    omrade_id: "demokvarteret",
    scenario: null,
    metodstatus: "skiss",
    parametrar: { ...STANDARD_VARMESTRESS_PARAM },
    utci_medel_c: 34.1,
    utci_max_c: 36.2,
    tmrt_medel_c: 53.0,
    tmrt_max_c: 61.5,
    utanfor_giltighetsomrade_pct: 0,
    stressklasser: [],
    fore: {
      utci_medel_c: 34.1,
      utci_max_c: 36.2,
      tmrt_medel_c: 53.0,
      tmrt_max_c: 61.5,
      utanfor_giltighetsomrade_pct: 0,
      stressklasser: [],
      varningar: [],
    },
    efter: null,
    jamforelse: null,
    lager: {
      utci_c_fore: {
        id: "vs_utci",
        roll: "utci_c",
        typ: "bas",
        etikett: "Värmestress (UTCI)",
        enhet: "°C",
        farg: "varme",
        skala: { min: 25, max: 42 },
        teckenforklaring: { typ: "kontinuerlig", min: 25, max: 42, stopp: [...(PALETTER.varme ?? [])] },
        utbredning: { x_min: 100, y_min: 200, x_max: 200, y_max: 300 },
        pixelstorlek_m: 1,
        bredd_px: 100,
        hojd_px: 100,
        tak_maskat: true,
        png: "/api/kartverktyg/demokvarteret/varmestress/vs_utci.png",
      },
    },
    varningar: [],
  };

  const html = varmestressPanelHtml({
    param: { ...STANDARD_VARMESTRESS_PARAM },
    laddar: false,
    fel: null,
    svar: vsSvar,
    scenario: null,
    aktivtLagerRoll: "utci_c",
    forval: "varmebolja",
  });

  assert.match(html, /<section\b[^>]*id="ar-vs-panel"/);
  assert.doesNotMatch(html, /<details\b[^>]*id="ar-vs-panel"/);
  assert.match(html, /data-vs-forval="varmebolja"[^>]*aria-pressed="true"[^>]*>Värmebölja · 30 °C, 21 juli kl\. 14</);
  assert.match(html, /data-vs-forval="sommardag"[^>]*aria-pressed="false"[^>]*>Sommardag · 25 °C</);
  assert.match(html, /data-vs-forval="anpassa"[^>]*aria-pressed="false"[^>]*>Anpassa själv</);
  assert.match(html, /data-vs-lager="utci_c" aria-pressed="true">UTCI °C</);
  assert.match(html, /data-vs-lager="t_mrt_c" aria-pressed="false">T_mrt °C</);
  assert.match(html, /class="ar-inline-legend"/);
  assert.match(html, /Värmestress \(UTCI\) \(°C\) · skiss/);
});

test("Etapp 2: Buller behåller SKISS-bannern och stödjer 2-klicks 'Rita väg på kartan' utan handinmatade koordinater", () => {
  const html = bullerPanelHtml({
    param: { ...STANDARD_BULLER_PARAM },
    laddar: false,
    fel: null,
    svar: null as BullerAnalysSvar | null,
    scenario: null,
    visaLager: false,
    ritarVag: false,
  });

  assert.match(html, /<section\b[^>]*id="ar-buller-panel"/);
  assert.doesNotMatch(html, /<details\b[^>]*id="ar-buller-panel"/);
  assert.ok(html.includes(BULLER_SKISS_BANNER));
  assert.match(html, /id="ar-bl-rita-vag"[^>]*>Rita väg på kartan</);

  // 2-klicks vägritning: klick 1 -> startpunkt, klick 2 -> färdigt vägsegment + förhandslinje
  const k1 = tillampaBullerVagKlick({ ...STANDARD_BULLER_PARAM }, null, 150010.24, 6580020.76);
  assert.equal(k1.klar, false);
  assert.deepEqual(k1.ritadPunkt1, { e: 150010.2, n: 6580020.8 });
  assert.equal(k1.fel, null);

  // Sammanfallande punkt 2 avvisas ärligt
  const kSamma = tillampaBullerVagKlick({ ...STANDARD_BULLER_PARAM }, k1.ritadPunkt1, 150010.21, 6580020.79);
  assert.equal(kSamma.klar, false);
  assert.match(kSamma.fel ?? "", /sammanfalla/);

  // Giltig punkt 2 fyller vägsegmentet
  const k2 = tillampaBullerVagKlick({ ...STANDARD_BULLER_PARAM }, k1.ritadPunkt1, 150090.2, 6580020.8);
  assert.equal(k2.klar, true);
  if (!k2.klar) throw new Error("väntade färdig väg");
  assert.equal(k2.param.vag_start_e, 150010.2);
  assert.equal(k2.param.vag_slut_e, 150090.2);
  assert.equal(k2.linje.klar, true);
  assert.equal(k2.linje.meter, 80);
  assert.equal(k2.linje.text, "Väg: 80,0 m");

  const linje = byggBullerVagLinje(0, 0, 120, 0, false);
  assert.equal(linje.text, "Väg: 120 m");
  assert.equal(linje.klar, false);

  // När vägritning är aktiv visas statusprompt i panelen
  const aktivHtml = bullerPanelHtml({
    param: { ...STANDARD_BULLER_PARAM },
    laddar: false,
    fel: null,
    svar: null,
    scenario: null,
    visaLager: false,
    ritarVag: true,
    ritadPunkt1: { e: 150010.2, n: 6580020.8 },
  });
  assert.match(aktivHtml, /id="ar-bl-rita-vag"[^>]*aria-pressed="true"[^>]*>Avbryt vägritning</);
  assert.match(aktivHtml, /slutpunkt/);
});

test("Etapp 2: Vind & Luft visar ärlig förklaring om väder- och trafikunderlag när körningar saknas", () => {
  const html = luftTomPanelHtml({ harMall: false, rapportHref: "#/p/demo/rapport" });
  assert.match(html, /Vind &amp; Luft/);
  assert.match(html, />SKISS</);
  assert.ok(html.includes(LUFT_SKISS_NOTIS));
  assert.match(html, /väderunderlag/i);
  assert.match(html, /trafik- och emissionsunderlag/i);
  assert.match(html, /Steg 3 · Rapport/);
});

// ------------------------------------------------------------------ 4. Rensat utvecklarbrus & CSS-kontrakt

test("Etapp 2: extraAnalyserHtml och A1_enkel_sanka är helt borttagna ur arbetsytan och CSS följer grafisk riktlinje C5 + 390px-krav", () => {
  assert.doesNotMatch(ARBETSYTA_KALLTEXT, /extraAnalyserHtml/, "extraAnalyserHtml får inte monteras i arbetsytan");
  assert.doesNotMatch(ARBETSYTA_KALLTEXT, /kopplaExtraAnalyser/, "kopplaExtraAnalyser får inte köras i arbetsytan");
  assert.doesNotMatch(ARBETSYTA_KALLTEXT, /A1_enkel_sanka/, "Hårdkodat testområde får inte finnas i arbetsytan");

  // C5: stående flikar (48 px) på vänsterkanten och ett uppfällt block (232 px) i ämnets färg
  assert.match(ARBETSYTA_CSS, /\.ar-skikt-flikar\s*\{[^}]*width:\s*48px/);
  assert.match(ARBETSYTA_CSS, /\.ar-skikt-kropp\s*\{[^}]*width:\s*232px/);
  assert.match(ARBETSYTA_CSS, /\.ar-skikt-doman\[data-amne="vatten"\]/);
  assert.match(ARBETSYTA_CSS, /\.ar-karta\.har-skikt-panel\s+\.ar-tecken/);
  assert.match(ARBETSYTA_CSS, /@media\s*\(\s*max-width:\s*860px\s*\)[\s\S]*overflow-x:\s*clip/);
  assert.match(ARBETSYTA_CSS, /\.ar-skikt-doman\[aria-selected="true"\]/);
});
