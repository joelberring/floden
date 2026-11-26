/*
 * Körs med `npm test`. Deterministiska tester för O(1) beräkningsomfång och lugn förvarning
 * före körning i Steg 2 (`berakningsomfang.ts` + koppling i `arbetsyta.ts`).
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  berakningsomfangHtml,
  berakningsomfangNiva,
  berakningsomfangRadText,
  berakningsomfangVarningText,
  formateraCeller,
  type BerakningsomfangInfo,
} from "./berakningsomfang.ts";

const FLODE_MAPP = path.dirname(fileURLToPath(import.meta.url));
const ARBETSYTA_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.ts"), "utf-8");

test("normalt läge (ok): visar diskret omfattningsrad utan förvarningsruta", () => {
  const omf: BerakningsomfangInfo = {
    antal_rutor: 2,
    uppskattade_celler: 1_800_000,
    max_sankgraf_celler: 4_500_000,
    andel_av_tak: 0.4,
    niva: "ok",
    meddelande: null,
  };
  assert.equal(berakningsomfangNiva(omf), "ok");
  assert.equal(berakningsomfangRadText(omf), "2 rutor · ~1,8 M celler");
  assert.equal(berakningsomfangVarningText(omf), null);

  const html = berakningsomfangHtml(omf);
  assert.match(html, /id="ar-omfang"/);
  assert.match(html, /2 rutor · ~1,8 M celler/);
  assert.doesNotMatch(html, /ar-omfang-varning/);
  assert.doesNotMatch(html, /1\.8/); // svensk decimalform med komma, aldrig punkt

  // Singularform för 1 ruta och mindre celltal (< 100 000)
  const enRuta: BerakningsomfangInfo = {
    antal_rutor: 1,
    uppskattade_celler: 45_000,
    max_sankgraf_celler: 4_500_000,
    andel_av_tak: 0.01,
    niva: "ok",
    meddelande: null,
  };
  assert.match(berakningsomfangRadText(enRuta), /^1 ruta · ~45\s000 celler$/);
  assert.match(formateraCeller(1_200_000), /^~1,2 M celler$/);
});

test("varning (andel >= 0,75): visar både omfattningsrad och lugn svensk förvarning", () => {
  const omf: BerakningsomfangInfo = {
    antal_rutor: 4,
    uppskattade_celler: 4_000_000,
    max_sankgraf_celler: 4_500_000,
    andel_av_tak: 0.8889,
    niva: "varning",
    meddelande:
      "Området är nära serverns cellgräns (4,0 / 4,5 M celler). Rita en mindre avgränsning eller kör ett ämne i taget.",
  };
  assert.equal(berakningsomfangNiva(omf), "varning");
  const html = berakningsomfangHtml(omf);
  assert.match(html, /4 rutor · ~4,0 M celler/);
  assert.match(html, /id="ar-omfang-varning"/);
  assert.match(html, /class="flode-liten ar-omfang-varning ar-omfang-varning"/);
  assert.match(html, /role="status"/);
  assert.match(html, /nära serverns cellgräns \(4,0 \/ 4,5 M celler\)/);

  // Utan explicit `meddelande` byggs lugn svensk reservtext automatiskt
  const utanMsg: BerakningsomfangInfo = { ...omf, meddelande: null };
  const reserv = berakningsomfangVarningText(utanMsg);
  assert.ok(reserv);
  assert.match(reserv, /nära serverns cellgräns \(4,0 \/ 4,5 M celler\)/);
  assert.match(reserv, /kör ett ämne i taget/);
});

test("över tak (andel > 1,0): visar over_tak-klass och tydlig förvarning (samt XSS-escaping)", () => {
  const omf: BerakningsomfangInfo = {
    antal_rutor: 6,
    uppskattade_celler: 6_000_000,
    max_sankgraf_celler: 4_500_000,
    andel_av_tak: 1.3333,
    niva: "over_tak",
    meddelande: null,
  };
  assert.equal(berakningsomfangNiva(omf), "over_tak");
  const html = berakningsomfangHtml(omf);
  assert.match(html, /6 rutor · ~6,0 M celler/);
  assert.match(html, /ar-omfang-over_tak/);
  assert.match(html, /överstiger serverns cellgräns \(6,0 \/ 4,5 M celler\)/);

  // `medId: false` undviker duplicerade DOM-id i tom-kortet
  const utanId = berakningsomfangHtml(omf, { medId: false });
  assert.doesNotMatch(utanId, /id="ar-omfang/);
  assert.match(utanId, /ar-omfang-over_tak/);

  // HTML-tecken i serverns meddelande escapas säkert
  const xssHtml = berakningsomfangHtml({
    ...omf,
    meddelande: "Varning <script>alert(1)</script>",
  });
  assert.doesNotMatch(xssHtml, /<script>/);
  assert.match(xssHtml, /&lt;script&gt;/);
});

test("projekt utan berakningsomfang (t.ex. demokvarteret) ger tom sträng utan fel", () => {
  assert.equal(berakningsomfangHtml(null), "");
  assert.equal(berakningsomfangHtml(undefined), "");
  assert.equal(
    berakningsomfangHtml({
      antal_rutor: 0,
      uppskattade_celler: 0,
      max_sankgraf_celler: 4_500_000,
      andel_av_tak: 0,
      niva: "ok",
    }),
    "",
  );
  assert.equal(
    berakningsomfangHtml({
      antal_rutor: Number.NaN,
      uppskattade_celler: 1000,
      max_sankgraf_celler: 4_500_000,
      andel_av_tak: 0.1,
      niva: "ok",
    }),
    "",
  );
});

test("arbetsyta.ts kopplar in berakningsomfangHtml nära beräkningsknapparna i Steg 2", () => {
  assert.match(ARBETSYTA_KALLTEXT, /from "\.\/berakningsomfang\.ts"/);
  assert.match(ARBETSYTA_KALLTEXT, /berakningsomfangHtml\(omfangInfo\)/);
  assert.match(ARBETSYTA_KALLTEXT, /berakningsomfangHtml\(omfangInfo, \{ medId: false \}\)/);
});
