import assert from "node:assert/strict";
import { test } from "node:test";
import { lasKartan, lasKartanText, LAGEN } from "./kartforklaring.ts";
import { settKontoLage } from "./konto.ts";
import { sidaForvaltning, sidaOm, sidaProjekt, sidaStart, sidhuvud, type SidId } from "./sidor.ts";
import type { ModulInfo, OmradeSammanfattning, ProjektSvar, ProjektVy } from "./types.ts";

const moduler: ModulInfo[] = [
  { modul_id: "vatten", nr: "01", namn: "Vatten", beskrivning: "Skyfall.", status: "finns_i_demon", status_text: "Finns i demon", finns_i_karnan: true, analystyper: [], kartfarg: "#2b5d8c",
    losare: [{ namn: "vatten.statisk_fyllning", visningsnamn: "Maxdjup vid skyfall", version: "1", doman: "vatten", beskrivning: "x", indataroller: [], valfria_indataroller: [], utdataroller: ["max_djup"], radie_m: null, radie_kalla: "kraver_parametrar" }] },
  { modul_id: "luft", nr: "02", namn: "Luft", beskrivning: "Vind.", status: "finns_ej_forberaknad", status_text: "Finns, men en beräkning tar mer än tio minuter – inte förberäknad i demon", finns_i_karnan: true, analystyper: [], kartfarg: "#6b6b6b" },
  { modul_id: "buller", nr: "03", namn: "Buller", beskrivning: "Ljud.", status: "under_utveckling", status_text: "Under utveckling – metodkontroll pågår (33 av 80 officiella testfall godkända)", finns_i_karnan: false, analystyper: [], kartfarg: "#b4462b" },
];

const vy: ProjektVy = { id: "dagsljus-hojd", avsnitt: "dagsljus", rubrik: "Dagsljus: höjt hus", mattnamn: "Soltimmar på marken", korning_id: "k", fore_lager: "a", efter_lager: "b", skillnad_lager: "c", efter_namn: "Höjt hus", andring: "Ett hus höjs." };

const projekt: ProjektSvar = {
  id: "demokvarteret", namn: "Demokvarteret", undertitel: "påhittat kvarter", paahittat: true,
  paahittat_text: "Kvarteret är påhittat.", beskrivning: "Fyra kvarter.",
  andringar: [{ id: "hojd", rubrik: "Höjt hus", text: "Ett hus höjs." }],
  avsnitt: [
    { id: "dagsljus", rubrik: "Dagsljus", ingress: "Soltimmar.", kolumner: [{ id: "hojd", rubrik: "Höjt hus" }],
      matt: [{ namn: "Mark som förlorar mer än 1 timme sol", enhet: "m²", decimaler: 0, varden: { hojd: 1456 } }],
      teknisk: { fraga_id: "f", korning_id: "k", fraga_fil: "x.toml", losare: [{ visningsnamn: "Soltimmar på mark", namn: "dagsljus.soltimmar_mark", version: "2", kodversion: "abc" }], jobb: 54, fran_cache: 0, korda: 54, total_tid_s: 3, koordinatsystem: "SWEREF 99 18 00 (EPSG:3011)", manifest: "run.json" } },
  ],
  vyer: [vy],
};

const kontrollfall = [{ omrade_id: "A1_x", kod: "A1", namn: "Enkel sänka", grupp: "A", beskrivning: "Provfall." }] as unknown as OmradeSammanfattning[];

/** Synlig text: taggar och attribut bort. */
function text(html: string): string {
  return html.replace(/<[^>]*>/g, " ").replace(/&[a-z#0-9]+;/g, " ");
}
const PLATSHALLARE = /\[[A-Za-zÅÄÖåäö][^\]]*\]/;

function alla(): Record<string, string> {
  const ut: Record<string, string> = {
    start: sidaStart(moduler, true),
    startUtanDemo: sidaStart(moduler, false),
    startUtanModuler: sidaStart([], false),
    projekt: sidaProjekt(projekt, moduler),
    projektSaknas: sidaProjekt(null, moduler),
    om: sidaOm(moduler, kontrollfall),
    omTom: sidaOm([], []),
    forvaltning: sidaForvaltning(),
    karta: [...LAGEN.map(([l]) => lasKartan(vy, l)), lasKartanText(null, true)].join(" "),
  };
  for (const s of ["start", "projekt", "fragor", "om", "forvaltning"] as SidId[]) ut[`huvud-${s}`] = sidhuvud(s);
  return ut;
}

test("inga hakparenteser som platshållare i några renderade sidor", () => {
  for (const lage of [{ lage: "av" } as const, { lage: "ut" } as const, { lage: "okand" } as const]) {
    settKontoLage(lage);
    for (const [namn, html] of Object.entries(alla())) {
      assert.doesNotMatch(text(html), PLATSHALLARE, `${namn} (${lage.lage})`);
    }
  }
});

test("hälsning, förvaltning och organisationsväljare finns inte utan inloggning och kontodata", () => {
  for (const lage of [{ lage: "av" } as const, { lage: "ut" } as const]) {
    settKontoLage(lage);
    const start = sidaStart(moduler, true);
    assert.doesNotMatch(start, /God morgon|Hej /);
    assert.doesNotMatch(sidhuvud("start"), /Förvaltning|org-vaxlare/);
    assert.equal(sidaForvaltning(), "");
  }
  settKontoLage({ lage: "in", namn: "Anna Svensson", epost: "a@x.se", medlemskap: [], projekt: [] });
  assert.doesNotMatch(sidhuvud("start"), /Förvaltning/);
  settKontoLage({ lage: "in", namn: "Anna Svensson", epost: "a@x.se", medlemskap: [{ organisation: "Staden", forvaltning: "SBK", roll: "medlem" }], projekt: [] });
  assert.match(sidhuvud("start"), /Förvaltning/);
  assert.match(sidaStart(moduler, true), /Hej Anna\./);
  assert.match(sidaForvaltning(), /Staden/);
  settKontoLage({ lage: "av" });
});

test("samma meny på alla sidor, med de fyra punkterna", () => {
  settKontoLage({ lage: "av" });
  const menyer = (["start", "projekt", "fragor", "om"] as SidId[]).map((s) =>
    Array.from((sidhuvud(s).match(/<nav[\s\S]*<\/nav>/)?.[0] ?? "").matchAll(/<a href="([^"]+)"[^>]*>([^<]+)<\/a>/g)).map((m) => m[2]),
  );
  for (const m of menyer) assert.deepEqual(m, ["Start", "Projekt", "Utredningsfrågor", "Om beräkningarna"]);
});

test("projektsidan visar talen ur svaret och inga knappar utan funktion", () => {
  settKontoLage({ lage: "av" });
  const html = sidaProjekt(projekt, moduler);
  assert.match(text(html), /1\s456 m²/);
  assert.doesNotMatch(html, /disabled/);
  assert.doesNotMatch(html, /Nytt projekt|Ny körning|Bjud in|Skapa på kartan|Byt förvaltning|Stämmer/);
  // Statusen för luft och buller kommer ur modullistan, inte ur sidan.
  assert.match(text(html), /33 av 80 officiella testfall godkända/);
});

test("startsidan: tre steg och ärliga statusar, inga motsägelser", () => {
  settKontoLage({ lage: "av" });
  const t = text(sidaStart(moduler, true));
  assert.match(t, /Välj ett exempel/);
  assert.match(t, /Se före och efter/);
  assert.match(t, /Läs av värden/);
  assert.doesNotMatch(t, /I DRIFT|Geometri|KOMMANDE|K20/i);
});

test("jargong i huvudflödet", () => {
  settKontoLage({ lage: "av" });
  const synligt = [sidaStart(moduler, true), sidaProjekt(projekt, moduler)].map((h) => text(h.replace(/<details[\s\S]*?<\/details>/g, " "))).join(" ");
  assert.doesNotMatch(synligt, /K20|manifest|EPSG|dagsljus\.soltimmar_mark|\bjobb\b/i);
});
