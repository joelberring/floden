/* Körs med `npm test`. Sektionsverktyg, svartvit SVG-profil, byggnadsklippning, hårkors och CSV. */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  avlasningVidIndex,
  indexFranSvgX,
  ritaSektionSvg,
  SektionVerktyg,
  sektionTillCsv,
  type SektionSvar,
} from "./sektion.ts";

function exempelSektion(medEfter = true): SektionSvar {
  return {
    kontrakt: "floden-sektion/v1",
    epsg: 3011,
    hojdsystem: "RH 2000",
    har_efter: medEfter,
    avstand_m: [0, 5, 10, 15, 20, 25],
    e: [150000, 150005, 150010, 150015, 150020, 150025],
    n: [6580000, 6580000, 6580000, 6580000, 6580000, 6580000],
    mark_fore_m: [12.0, 11.4, 11.2, 11.4, 11.8, 12.1],
    mark_efter_m: medEfter ? [12.0, 11.3, 11.1, 11.4, 11.8, 12.1] : [null, null, null, null, null, null],
    vatten_djup_fore_m: [0, 0.14, 0.24, 0.08, null, 0],
    vatten_djup_efter_m: medEfter ? [0, 0.22, 0.34, 0.12, null, 0] : [null, null, null, null, null, null],
    vatten_yta_fore_m: [null, 11.54, 11.44, 11.48, null, null],
    vatten_yta_efter_m: medEfter ? [null, 11.52, 11.44, 11.52, null, null] : [null, null, null, null, null, null],
    byggnad_fore: [false, false, false, false, true, false],
    byggnad_efter: medEfter ? [false, false, false, false, true, false] : [false, false, false, false, false, false],
    takhojd_fore_m: [null, null, null, null, 30.5, null],
    takhojd_efter_m: medEfter ? [null, null, null, null, 30.5, null] : [null, null, null, null, null, null],
    sammanfattning: {
      langd_m: 25,
      antal_punkter: 6,
      steg_m: 5,
      mark_fore_min_m: 11.2,
      mark_fore_max_m: 12.1,
      mark_efter_min_m: medEfter ? 11.1 : null,
      mark_efter_max_m: medEfter ? 12.1 : null,
      max_djup_fore_m: 0.24,
      max_djup_efter_m: medEfter ? 0.34 : null,
      oversvammad_bredd_002m_fore_m: 15,
      oversvammad_bredd_002m_efter_m: medEfter ? 15 : null,
      oversvammad_bredd_010m_fore_m: 10,
      oversvammad_bredd_010m_efter_m: medEfter ? 15 : null,
    },
  };
}

test("SektionVerktyg hanterar 2-punktslinje, Shift+klick polylinje, preview och Esc", () => {
  const v = new SektionVerktyg();
  let andringar = 0;
  v.vidAndring(() => {
    andringar += 1;
  });

  // Inaktivt verktyg ignorerar klick
  assert.equal(v.klick({ x: 150000, y: 6580000 }), false);
  assert.equal(v.hjalpText(), "");

  v.sattAktiv(true);
  assert.match(v.hjalpText(), /startpunkt/i);

  // Klick 1 sätter startpunkt
  assert.equal(v.klick({ x: 150000, y: 6580000 }), true);
  v.ror({ x: 150003, y: 6580004 });
  const preview = v.linje();
  assert.ok(preview);
  assert.equal(preview.klar, false);
  assert.equal(preview.langdM, 5);
  assert.equal(preview.text, "Sektion: 5,0 m");

  // Shift+klick lägger till mellanpunkt utan att låsa
  assert.equal(v.klick({ x: 150003, y: 6580004 }, { shift: true }), true);
  assert.equal(v.klar, false);
  assert.equal(v.punkter.length, 2);

  // Klick utan Shift låser polylinjen (5 m + 10 m = 15 m)
  assert.equal(v.klick({ x: 150013, y: 6580004 }), true);
  assert.equal(v.klar, true);
  const last = v.linje();
  assert.ok(last);
  assert.equal(last.klar, true);
  assert.equal(last.langdM, 15);
  assert.equal(last.text, "Sektion: 15,0 m");
  assert.deepEqual(v.punkterSomPar(), [
    [150000, 6580000],
    [150003, 6580004],
    [150013, 6580004],
  ]);

  // Esc 1 rensar linjen men håller verktyget aktivt; Esc 2 avslutar verktyget
  assert.equal(v.avbryt(), true);
  assert.equal(v.linje(), null);
  assert.equal(v.aktiv, true);
  assert.equal(v.avbryt(), true);
  assert.equal(v.aktiv, false);
  assert.ok(andringar > 0);
});

test("ritaSektionSvg skalar Y efter mark+vatten, klipper höga byggnader och ritar hårkors", () => {
  const data = exempelSektion(true);
  const svg = ritaSektionSvg(data, { bredd: 680, hojd: 210, aktivIndex: 2 });

  assert.match(svg, /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  assert.match(svg, /<\/svg>$/);

  // Stramt svartvitt krom + vattenfärger före/efter
  assert.match(svg, /fill="#ffffff"/);
  assert.match(svg, /data-sektion="mark-fore"[^>]*stroke="#666666"[^>]*stroke-dasharray="5 3"/);
  assert.match(svg, /data-sektion="mark-efter"[^>]*stroke="#111111"/);
  assert.match(svg, /data-sektion="vatten-fore"[^>]*fill="#7ec4f8"/);
  assert.match(svg, /data-sektion="vatten-efter"[^>]*fill="#1a52b8"/);

  // Hög byggnad (30,5 m) klipps vid toppen med snedmarkering och svensk etikett `Tak 30,5 m`
  assert.match(svg, /data-sektion="byggnad" data-klippt="true"/);
  assert.match(svg, /data-sektion="byggnad-klipp"/);
  assert.match(svg, /Tak 30,5 m/);

  // Y-axeln får INTE vara uppskalad till 30 m (högsta Y-tick ska vara runt 12 m, inte 25–30 m)
  assert.doesNotMatch(svg, />30,0</);
  assert.doesNotMatch(svg, />25,0</);

  // Hårkors ritas vid aktivIndex=2
  assert.match(svg, /data-sektion="harkors"/);

  // Utan efter-scenario: mark före ska vara solid #111111 (ej streckad)
  const svgBaraFore = ritaSektionSvg(exempelSektion(false));
  assert.match(svgBaraFore, /data-sektion="mark-fore"[^>]*stroke="#111111"/);
  assert.doesNotMatch(svgBaraFore, /data-sektion="mark-efter"/);
});

test("avlasningVidIndex och indexFranSvgX ger svensk formatering och synkad indexmappning", () => {
  const data = exempelSektion(true);
  assert.equal(avlasningVidIndex(data, -1), null);
  assert.equal(avlasningVidIndex(data, 99), null);

  const p2 = avlasningVidIndex(data, 2);
  assert.ok(p2);
  assert.equal(p2.avstandM, 10);
  assert.equal(p2.avstandText, "10,0 m");
  assert.equal(p2.markForeText, "11,20 m RH 2000");
  assert.equal(p2.djupForeText, "24 cm (0,24 m)");
  assert.equal(p2.djupEfterText, "34 cm (0,34 m)");
  assert.equal(p2.vattenYtaForeText, "11,44 m RH 2000");

  const p4 = avlasningVidIndex(data, 4);
  assert.ok(p4);
  assert.equal(p4.byggnadFore, true);
  assert.equal(p4.takhojdForeText, "Tak 30,5 m RH 2000");
  assert.equal(p4.djupForeText, "–");

  // X-mappning: vänster plotkant -> 0, höger plotkant -> 5, mitt -> 2 eller 3
  assert.equal(indexFranSvgX(data, 0, 680), 0);
  assert.equal(indexFranSvgX(data, 54, 680), 0);
  assert.equal(indexFranSvgX(data, 662, 680), 5);
  assert.equal(indexFranSvgX(data, 700, 680), 5);
  // x vid 40 % av plotbredden (54 + 0.4 * 608 = 297.2) -> avstånd 10 m -> index 2
  assert.equal(indexFranSvgX(data, 54 + 0.4 * (680 - 54 - 18), 680), 2);
  assert.equal(indexFranSvgX(data, Number.NaN, 680), null);
});

test("sektionTillCsv exporterar alla kolumner med enheter och rätt antal rader", () => {
  const csv = sektionTillCsv(exempelSektion(true));
  const rader = csv.trimEnd().split("\n");
  assert.equal(rader.length, 7); // 1 rubrikrad + 6 datarader
  assert.equal(
    rader[0],
    "avstand_m;e_3011;n_3011;mark_fore_m;mark_efter_m;vatten_djup_fore_m;vatten_djup_efter_m;vatten_yta_fore_m;vatten_yta_efter_m;byggnad_fore;byggnad_efter;takhojd_fore_m;takhojd_efter_m",
  );
  // Rad index 2 (tredje dataraden = rader[3])
  assert.equal(rader[3], "10;150010;6580000;11.2;11.1;0.24;0.34;11.44;11.44;false;false;;");
  // Rad index 4 (byggnad + null djup)
  assert.equal(rader[5], "20;150020;6580000;11.8;11.8;;;;;true;true;30.5;30.5");
});
