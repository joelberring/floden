/* Körs med `npm test`. Luft-flik i steg 4: synlighet, lager med enheter, skiss-märkning och nyckeltal. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { LagerPost, MatvardeTabell } from "../fragor-typer.ts";
import type { ProjektVy } from "../types.ts";
import {
  harLuftKorningar,
  LUFT_SKISS_NOTIS,
  luftFlikKnappHtml,
  luftLagerVal,
  luftNyckeltal,
  luftPanelHtml,
} from "./luftflik.ts";

const lp = (id: string, roll: string, scenario: string, typ: LagerPost["typ"], extra: Partial<LagerPost> = {}): LagerPost =>
  ({
    id,
    analys: "luft",
    roll,
    scenario,
    typ,
    etikett: roll,
    enhet: "",
    doman: "luft",
    primar: false,
    kontroll: false,
    harledd: false,
    ...extra,
  }) as LagerPost;

const LUFT_VY = {
  id: "luft-nytt",
  avsnitt: "luft",
  fore_lager: "luft.bas.komfortklass",
  efter_lager: "luft.nytt.komfortklass",
  skillnad_lager: "skillnad.luft.komfortklass",
} as ProjektVy;

const LUFT_LAGER: LagerPost[] = [
  lp("luft.bas.arsmedel", "arsmedel", "bas", "bas"),
  lp("luft.nytt.arsmedel", "arsmedel", "nytt", "scenario"),
  lp("luft.bas.forstarkning", "forstarkning", "bas", "bas"),
  lp("luft.nytt.forstarkning", "forstarkning", "nytt", "scenario"),
  lp("luft.bas.faraklass", "faraklass", "bas", "bas"),
  lp("luft.nytt.faraklass", "faraklass", "nytt", "scenario"),
  lp("luft.bas.komfortklass", "komfortklass", "bas", "bas", { primar: true }),
  lp("luft.nytt.komfortklass", "komfortklass", "nytt", "scenario", { primar: true }),
  lp("skillnad.luft.komfortklass", "komfortklass", "nytt", "skillnad", { fore: "bas", efter: "nytt" }),
];

test("Luft-fliken syns bara när projektet har luftkörningar (dold i demokvarteret)", () => {
  assert.equal(harLuftKorningar([{ avsnitt: "vatten" }, { avsnitt: "dagsljus" }, { avsnitt: "ekologi" }]), false);
  assert.equal(harLuftKorningar([{ avsnitt: "vatten" }, { avsnitt: "luft" }]), true);
  assert.match(luftFlikKnappHtml(false, false), /hidden/);
  assert.doesNotMatch(luftFlikKnappHtml(true, false), /hidden/);
  assert.match(luftFlikKnappHtml(true, true), /aria-selected="true">Luft</);
});

test("luftLagerVal sorterar de fyra luftlagren och fyller i svenska etiketter och enheter", () => {
  const roller = luftLagerVal(LUFT_LAGER, LUFT_VY);
  assert.deepEqual(
    roller.map((r) => [r.roll, r.enhet]),
    [
      ["komfortklass", "klass"],
      ["faraklass", "klass"],
      ["forstarkning", "gånger"],
      ["arsmedel", "µg/m³"],
    ],
  );
  assert.equal(roller[0]?.etikett, "Vindkomfort (klass)");
  assert.equal(roller[2]?.etikett, "Vindförstärkning på gånghöjd");
  assert.equal(roller[3]?.etikett, "Årsmedelhalt");
});

test("luftNyckeltal formaterar tal ur körningen och MKN-intervall utan påhittade värden", () => {
  const tom = luftNyckeltal([], "bas", "nytt");
  assert.equal(tom.length, 4);
  for (const x of tom) assert.equal(x.varde, "–");

  const tabeller: MatvardeTabell[] = [
    {
      analys: "luft_vind",
      losare: "luft.vindkomfort",
      kolumner: ["max_forstarkning", "andel_fara_procent"],
      rader: [
        { scenario: "bas", ruta: null, fran_cache: false, kortid_s: 1, varden: { max_forstarkning: 1.35, andel_fara_procent: 0.4 } },
        { scenario: "nytt", ruta: null, fran_cache: false, kortid_s: 1, varden: { max_forstarkning: 1.82, andel_fara_procent: 2.6, max_arsmedel_ug_m3: 22.4 } },
      ],
    },
  ];
  const tal = luftNyckeltal(tabeller, "bas", "nytt", {
    min_dygn_over_grans: 14,
    max_dygn_over_grans: 29,
    min_arsmedel_ug_m3: 18.5,
    max_arsmedel_ug_m3: 24.2,
    min_percentil_90_4_ug_m3: 38.0,
    max_percentil_90_4_ug_m3: 46.5,
    klarar_mkn_garanterat: true,
  });
  assert.equal(tal.length, 7);
  assert.equal(tal[0]?.varde, "1,35 gånger");
  assert.equal(tal[1]?.varde, "1,82 gånger");
  assert.equal(tal[2]?.varde, "22,4 µg/m³");
  assert.equal(tal[3]?.varde, "2,6 %");
  assert.equal(tal[4]?.varde, "14 till 29 dygn");
});

test("luftPanelHtml märker metoden som skiss, listar enheter och har inga decimalpunkter", () => {
  const roller = luftLagerVal(LUFT_LAGER, LUFT_VY);
  const html = luftPanelHtml({
    korningId: "luft-demo-1",
    roller,
    tal: [
      { etikett: "Max vindförstärkning före", varde: "1,35 gånger" },
      { etikett: "Årsmedelhalt max", varde: "22,4 µg/m³" },
    ],
    las: "Färgerna visar luftmiljö på gånghöjd.",
  });
  assert.match(html, /class="flode-skiss-marke">Skiss</);
  assert.ok(html.includes(LUFT_SKISS_NOTIS));
  assert.match(html, /Vindkomfort \(klass\) \(klass\)|Vindkomfort \(klass\)/);
  assert.match(html, /Vindförstärkning på gånghöjd \(gånger\)/);
  assert.match(html, /Årsmedelhalt \(µg\/m³\)/);
  assert.match(html, /Alla tal kommer ur körningen luft-demo-1\./);
  const synlig = html.replace(/<[^>]*>/g, " ");
  assert.doesNotMatch(synlig, /\d\.\d/);
});
