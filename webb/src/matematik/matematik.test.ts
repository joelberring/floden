import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { kontoMenyHtml } from "../flode/huvud.ts";
import { SIDTITLAR, tolkaHash } from "../rutter.ts";
import { settKontoLage, type KontoLage } from "../konto.ts";
import { MATEMATIK } from "./data.generated.ts";
import { sidaMatematik } from "./sida.ts";

const text = (html: string): string => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

test("#/matematik är en egen rutt med titel", () => {
  assert.equal(tolkaHash("#/matematik").sida, "matematik");
  assert.equal(tolkaHash("#/matematik/").sida, "matematik");
  assert.equal(SIDTITLAR.matematik, "Matematik och forskning – Floden");
  // Andra adresser berörs inte.
  assert.equal(tolkaHash("#/om").sida, "om");
  assert.equal(tolkaHash("#/").sida, "start");
});

test("routern kräver inloggning före sidan och ritar den med flödets sidhuvud", () => {
  const k = readFileSync(new URL("../router.ts", import.meta.url), "utf8");
  const ut = k.indexOf('kontoLage().lage === "ut"');
  const mat = k.indexOf('rutt.sida === "matematik"');
  assert.ok(ut > 0 && mat > ut, "inloggningskontrollen ska komma före matematiksidan");
  assert.match(k, /visaMatematik\(sidhuvudEl, vySida\)/);
});

test("kontomenyn har länken bredvid Om Floden", () => {
  for (const lage of [
    { lage: "av" } as KontoLage,
    { lage: "in", namn: "A B", epost: "a@x.se", medlemskap: [], projekt: [] } as KontoLage,
  ]) {
    settKontoLage(lage);
    const html = kontoMenyHtml();
    const om = html.indexOf('href="#/om"');
    const mat = html.indexOf('href="#/matematik"');
    assert.ok(om > 0 && mat > om);
    assert.match(html, /Matematik och forskning/);
  }
  settKontoLage({ lage: "av" });
});

test("sidan visar alla domäner, metoder, formler, status och länk till koden", () => {
  const html = sidaMatematik();
  const t = text(html);
  assert.match(t, /Matematik och forskning/);
  for (const d of ["Vatten", "Dagsljus", "Luft", "Buller", "Ekologi"]) assert.ok(t.includes(d), d);
  assert.equal(MATEMATIK.doman.length, 5);
  for (const d of MATEMATIK.doman) {
    for (const m of d.metoder) {
      assert.ok(t.includes(m.namn), m.namn);
      assert.ok(m.formler.length > 0, `${m.id} saknar formel`);
      for (const f of m.formler) assert.ok(html.includes(`<code class="mat-formel">${f.replace(/&/g, "&amp;")}`) || t.includes(f.slice(0, 12)), f);
    }
  }
  // Formler som läsbar text i <code>.
  assert.match(html, /<code class="mat-formel">dV_u\/dt = r\(t\)·A_eff\(u\)/);
  assert.match(html, /<code class="mat-formel">h\(φ\) = max\(0/);
  assert.match(html, /<code class="mat-formel">G_uv = 2·g_u·g_v/);
  // Länk till koden på GitHub.
  assert.match(html, /href="https:\/\/github\.com\/joelberring\/floden\/blob\/main\/src\/sankgraf\/bifurkation\.py"/);
});

test("statusmärkningen skiljer metodstatus och var metoden nås", () => {
  const html = sidaMatematik();
  assert.match(html, /Metodstatus: skiss/);
  assert.doesNotMatch(html, /Metodstatus: rapport/);
  for (const t of ["I användarflödet", "Fristående modul", "Kommandorad, MCP och testsvit"]) {
    assert.ok(html.includes(t), t);
  }
  // Märkningen följer koden: MKN-intervall och CELF har paneler i extranalyser.ts,
  // medan 3D-lufttransport och Nord2000 är CLI/testsvit och adjungerad luft är fristående.
  const luft = MATEMATIK.doman.find((d) => d.id === "luft")?.metoder ?? [];
  const buller = MATEMATIK.doman.find((d) => d.id === "buller")?.metoder ?? [];
  const ekologi = MATEMATIK.doman.find((d) => d.id === "ekologi")?.metoder ?? [];
  assert.equal(luft.find((m) => m.id === "luft.transport")?.flode, "cli");
  assert.equal(luft.find((m) => m.id === "luft.mkn_intervall")?.flode, "flode");
  assert.equal(buller.find((m) => m.id === "buller.nord2000")?.flode, "cli");
  assert.equal(buller.find((m) => m.id === "buller.celf")?.flode, "flode");
  assert.ok(ekologi.some((m) => m.flode === "flode"));
  assert.equal(luft.find((m) => m.id === "luft.adjungerad")?.flode, "fristaende");
});

test("de öppna frågorna Q1–Q5 finns med underlag", () => {
  const t = text(sidaMatematik());
  for (const q of ["Q1", "Q2", "Q3", "Q4", "Q5"]) assert.ok(t.includes(q), q);
  assert.deepEqual(MATEMATIK.fragor.map((q) => q.id), ["Q1", "Q2", "Q3", "Q4", "Q5"]);
  const q2 = MATEMATIK.fragor.find((q) => q.id === "Q2");
  assert.ok(q2?.underlag.some((u) => u.text.startsWith("tools/exportera_q2_underlag.py") && u.href));
});

test("kända avvikelser och begränsningar visas med orsak och underlag", () => {
  const html = sidaMatematik();
  const t = text(html);
  assert.match(t, /Kända avvikelser och begränsningar/);
  assert.ok(html.includes('data-hopp="mat-avvikelser"'));
  assert.ok(MATEMATIK.avvikelser.length >= 6);
  for (const a of MATEMATIK.avvikelser) {
    assert.ok(t.includes(a.titel), a.titel);
    assert.ok(a.varfor.length > 20, `${a.id} saknar förklaring`);
    assert.ok(a.kravs.length > 10, `${a.id} saknar krav`);
    assert.ok(a.underlag.some((u) => u.href), `${a.id} saknar länk till underlag`);
  }
});

test("ingen persondata i sidan", () => {
  const t = JSON.stringify(MATEMATIK);
  assert.doesNotMatch(t, /Samuel|person X/);
});
