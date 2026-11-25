import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { flodeHuvudHtml } from "../flode/huvud.ts";
import { settKontoLage } from "../konto.ts";
import { SIDTITLAR, tolkaHash } from "../rutter.ts";
import type { OmradeSammanfattning } from "../types.ts";
import { OM } from "./data.generated.ts";
import { faktorText, omFlodenHtml, sidaOmFloden, talText } from "./sida.ts";

const text = (html: string): string => html.replace(/<[^>]+>/g, " ").replace(/&[a-z#0-9]+;/g, " ").replace(/\s+/g, " ");
const kontrollfall = [{ omrade_id: "A1_x", kod: "A1", namn: "Enkel sänka", grupp: "A", beskrivning: "Provfall." }] as unknown as OmradeSammanfattning[];

test("#/om är Om Floden: rutt, titel, meny och router", () => {
  assert.equal(tolkaHash("#/om").sida, "om");
  assert.equal(SIDTITLAR.om, "Om Floden – Floden");
  settKontoLage({ lage: "av" });
  assert.match(flodeHuvudHtml(null, null, "om"), /href="#\/om" aria-current="page">Om Floden</);
  const k = readFileSync(new URL("../router.ts", import.meta.url), "utf8");
  assert.match(k, /sidaOmFloden\(moduler, omraden\)/);
  assert.match(k, /kopplaOm\(vySida\)/);
});

test("sidan har rubrikerna och alla fem ämnen med metodstatus och testat", () => {
  const html = sidaOmFloden([], kontrollfall);
  const t = text(html);
  for (const r of [
    "Om Floden",
    "Vad Floden kan nu",
    "Arbetsflödet",
    "Plan",
    "Jämförelse med andra verktyg",
    "A. Uppmätt i Floden",
    "B. Offentliga uppgifter om andra verktyg",
    "C. Det som inte är mätt",
    "D. Skillnader som går att belägga",
    "E. Vad andra verktyg har som Floden saknar",
  ]) {
    assert.ok(t.includes(r), r);
  }
  assert.deepEqual(OM.amnen.map((a) => a.namn), ["Skyfall", "Dagsljus och sol", "Ekologi", "Luft", "Buller"]);
  for (const a of OM.amnen) {
    assert.ok(a.metoder.length > 0 && a.testat.length > 0 && a.begransningar.length > 0, a.id);
    assert.match(html, new RegExp(`id="om-amne-${a.id}"`));
    for (const m of a.metoder) assert.ok(["skiss", "rapport"].includes(m.status), m.id);
  }
  assert.match(html, /data-status="skiss"/);
  assert.ok(t.includes("Metodstatus: Alla metoder är skisser"));
  // Nord2000 i webbens svenska sifferform.
  assert.ok(t.includes("55 av 80"));
  // De äldre avsnitten finns kvar under jämförelsen.
  assert.ok(t.includes("Kontroll av beräkningarna") && t.includes("Enkel sänka"));
});

test("alla tal går genom tal.ts: decimalkomma, smalt hårt mellanslag och bara mätta faktorer", () => {
  const t = text(omFlodenHtml());
  assert.ok(t.includes("cirka 79 gånger"), "WebGPU dagsljus");
  assert.ok(t.includes("cirka 25 gånger"), "WebGPU vatten");
  assert.ok(t.includes("cirka 1,2 till 1,8 gånger"), "hybrid");
  assert.ok(t.includes("4 242,6 ms"), "tusentalsavgränsare U+202F");
  assert.ok(t.includes("0,55 s"), "körtid EA1");
  assert.equal(talText({ varde: 10, prefix: "mer än", enhet: "minuter", decimaler: 0 }), "mer än 10 minuter");
  assert.equal(talText({ godkanda: 55, av: 80 }), "55 av 80");
  assert.equal(faktorText(1.2, 1.8), "1,2 till 1,8");
  assert.equal(faktorText(78.7, 78.7), "79");
  assert.doesNotMatch(omFlodenHtml(), /\u00a0/); // inget vanligt hårt mellanslag (U+00A0)
});

test("jämförelsen skiljer uppmätt, publicerat och ej mätt, och påstår ingen faktor mot en produkt", () => {
  const t = text(omFlodenHtml());
  assert.ok(t.includes("Det finns ingen jämförande mätning av samma fall i Floden och i något av de andra verktygen"));
  assert.ok(t.includes("Floden har inte kört något av verktygen"));
  // Namn på verktygen finns, med länk till tillverkarens egen sida.
  for (const v of ["SCALGO Live", "MIKE 21", "InfoWorks ICM", "ClimateStudio", "Accelerad", "SoundPLAN", "CadnaA", "Circuitscape", "Linkage Mapper", "Conefor"]) {
    assert.ok(t.includes(v), v);
  }
  for (const x of OM.andra.rader) {
    assert.ok(x.lankar.length > 0 && x.lankar.every((l) => l.url.startsWith("https://")), x.id);
    assert.ok(x.pris.length > 0 && x.kortid.length > 0 && x.licens.length > 0, x.id);
  }
  // Ingen jämförelse "snabbare än" utom i tillverkarnas egna uppgifter och i förklaringen att den inte görs.
  const utanAndra = JSON.stringify({ ...OM, andra: null, ejMatt: null });
  assert.doesNotMatch(utanAndra, /snabbare än|fortare än|bättre än/i);
  // Uppmätta tal har källa i repot.
  for (const u of OM.uppmatt) assert.ok(u.kalla.href, u.id);
  assert.ok(OM.saknas.length >= 5 && OM.saknas.some((s) => /Validering/.test(s.rubrik)));
});

test("planen har 5 till 10 punkter och ingen persondata finns på sidan", () => {
  assert.ok(OM.plan.length >= 5 && OM.plan.length <= 10);
  const t = text(sidaOmFloden([], []));
  assert.doesNotMatch(t, /Samuel|@[a-z]+\.[a-z]/i);
});
