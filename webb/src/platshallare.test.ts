import assert from "node:assert/strict";
import { test } from "node:test";
import { lasKartan, lasKartanText, LAGEN } from "./kartforklaring.ts";
import { settKontoLage } from "./konto.ts";
import { flodeHuvudHtml, SKALSIDOR, type SkalSida } from "./flode/huvud.ts";
import { sidaOmFloden } from "./om/sida.ts";
import { sidaForvaltning, sidaOm } from "./sidor.ts";
import type { ModulInfo, OmradeSammanfattning, ProjektVy } from "./types.ts";

const moduler: ModulInfo[] = [
  { modul_id: "vatten", nr: "01", namn: "Vatten", beskrivning: "Skyfall.", status: "finns_i_demon", status_text: "Finns i demon", finns_i_karnan: true, analystyper: [], kartfarg: "#2b5d8c",
    losare: [{ namn: "vatten.statisk_fyllning", visningsnamn: "Maxdjup vid skyfall", version: "1", doman: "vatten", beskrivning: "x", indataroller: [], valfria_indataroller: [], utdataroller: ["max_djup"], radie_m: null, radie_kalla: "kraver_parametrar" }] },
  { modul_id: "luft", nr: "02", namn: "Luft", beskrivning: "Vind.", status: "finns_ej_forberaknad", status_text: "Finns, men en beräkning tar mer än tio minuter – inte förberäknad i demon", finns_i_karnan: true, analystyper: [], kartfarg: "#6b6b6b" },
  { modul_id: "buller", nr: "03", namn: "Buller", beskrivning: "Ljud.", status: "under_utveckling", status_text: "Under utveckling – metodkontroll pågår (33 av 80 officiella testfall godkända)", finns_i_karnan: false, analystyper: [], kartfarg: "#b4462b" },
];

const vy: ProjektVy = { id: "dagsljus-hojd", avsnitt: "dagsljus", rubrik: "Dagsljus: höjt hus", mattnamn: "Soltimmar på marken", korning_id: "k", fore_lager: "a", efter_lager: "b", skillnad_lager: "c", efter_namn: "Höjt hus", andring: "Ett hus höjs." };

const kontrollfall = [{ omrade_id: "A1_x", kod: "A1", namn: "Enkel sänka", grupp: "A", beskrivning: "Provfall." }] as unknown as OmradeSammanfattning[];

/** Synlig text: taggar och attribut bort. */
function text(html: string): string {
  return html.replace(/<[^>]*>/g, " ").replace(/&[a-z#0-9]+;/g, " ");
}
const PLATSHALLARE = /\[[A-Za-zÅÄÖåäö][^\]]*\]/;

function alla(): Record<string, string> {
  const ut: Record<string, string> = {
    om: sidaOm(moduler, kontrollfall),
    omTom: sidaOm([], []),
    omFloden: sidaOmFloden(moduler, kontrollfall),
    omFlodenTom: sidaOmFloden([], []),
    forvaltning: sidaForvaltning(),
    karta: [...LAGEN.map(([l]) => lasKartan(vy, l)), lasKartanText(null, true)].join(" "),
  };
  ut["huvud-flode"] = flodeHuvudHtml("demokvarteret", "forslag");
  for (const s of Object.keys(SKALSIDOR) as SkalSida[]) ut[`huvud-${s}`] = flodeHuvudHtml(null, null, s);
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

test("förvaltning finns i menyn bara med inloggning och kontodata", () => {
  for (const lage of [{ lage: "av" } as const, { lage: "ut" } as const]) {
    settKontoLage(lage);
    assert.doesNotMatch(flodeHuvudHtml(null, null), /Förvaltning|org-vaxlare/);
    assert.equal(sidaForvaltning(), "");
  }
  settKontoLage({ lage: "in", namn: "Anna Svensson", epost: "a@x.se", medlemskap: [], projekt: [] });
  assert.doesNotMatch(flodeHuvudHtml(null, null), /Förvaltning/);
  settKontoLage({ lage: "in", namn: "Anna Svensson", epost: "a@x.se", medlemskap: [{ organisation: "Staden", forvaltning: "SBK", roll: "medlem" }], projekt: [] });
  assert.match(flodeHuvudHtml(null, null), /href="#\/forvaltning"[^>]*>Förvaltning</);
  assert.match(sidaForvaltning(), /Staden/);
  settKontoLage({ lage: "av" });
});

/** Länkarna i kontomenyn (text) ur ett sidhuvud. */
function menyLankar(html: string): string[] {
  const meny = html.match(/<ul class="flode-meny"[\s\S]*?<\/ul>/)?.[0] ?? "";
  return Array.from(meny.matchAll(/<a [^>]*href="([^"]+)"[^>]*>([^<]+)<\/a>/g)).map((m) => `${m[2]} ${m[1]}`);
}

test("ett skal: samma sidhuvud och meny på alla sidor, med samma punkter", () => {
  settKontoLage({ lage: "av" });
  const forvantat = ["Frågekatalog #/fragor", "Om Floden #/om", "Matematik och forskning #/matematik"];
  const huvuden = [flodeHuvudHtml(null, null), flodeHuvudHtml("demokvarteret", "resultat"), ...(Object.keys(SKALSIDOR) as SkalSida[]).map((s) => flodeHuvudHtml(null, null, s))];
  for (const h of huvuden) {
    assert.deepEqual(menyLankar(h), forvantat);
    assert.match(h, /class="flode-ordmarke" href="#\/"/);
    // Inga rester av det äldre sidhuvudet.
    assert.doesNotMatch(h, /Utredningsfrågor|huvud-nav|meny-knapp|Start<|href="#\/projekt/);
  }
});

test("stegraden finns bara i ett projekt; på övriga sidor markeras sidan i menyn och står efter ordmärket", () => {
  settKontoLage({ lage: "av" });
  assert.match(flodeHuvudHtml("demokvarteret", "fragor"), /class="flode-stegrad"/);
  assert.match(flodeHuvudHtml("demokvarteret", "karta"), /aria-current="page"><span class="flode-steg-nr">2<\/span> Karta &amp; analys/);
  assert.match(flodeHuvudHtml("demokvarteret", "fragor"), /aria-current="page"><span class="flode-steg-nr">3<\/span> Rapport/);
  assert.doesNotMatch(flodeHuvudHtml(null, null), /flode-stegrad/); // projektlistan
  for (const s of Object.keys(SKALSIDOR) as SkalSida[]) {
    const h = flodeHuvudHtml(null, null, s);
    assert.doesNotMatch(h, /flode-stegrad/, s);
    assert.match(h, new RegExp(`/ ${SKALSIDOR[s]}</span>`), s);
  }
  assert.match(flodeHuvudHtml(null, null, "om"), /href="#\/om" aria-current="page">Om Floden</);
});
