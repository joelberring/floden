/*
 * Kontrakts- och beteendetester för Våg 12 (live-fix på floden.berring.se):
 * 1. `korning.ts` tickar väntetiden medan `POST .../lage-idag/kor` pågår (fryser aldrig på 0 s)
 *    och rensar starttimern både vid framgång och fel.
 * 2. `arbetsyta.ts` och `resultat.ts` laddar vektorlager progressivt per lager med AbortController
 *    och synlig status-/felrad (`#ar-lager-not` / `#rs-visa-text`).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { arPagaende, startaLageIdag, vidKorning, type KorningApi } from "./korning.ts";
import { tillstandFor } from "./tillstand.ts";

test("V12: startaLageIdag tickar sekundräknaren medan POST pågår och städar timern", async () => {
  const t = tillstandFor("v12-klocka-test");
  let slappPost!: () => void;
  const postLopt = new Promise<void>((resolve) => {
    slappPost = resolve;
  });
  const texter: string[] = [];
  const avlyssna = vidKorning(() => {
    if (t.korning?.text) texter.push(t.korning.text);
  });

  const api: KorningApi = {
    intervallMs: 20,
    korScenario: async () => ({ fragor: [] }),
    korLageIdag: async () => {
      await postLopt;
      return {
        lage: "idag",
        fragor: [{ mall: "vatten", korning: { korning_id: "k-10ar-1" } }],
      };
    },
    lageIdagStatus: async () => ({
      korningar: [
        {
          mall: "vatten",
          status: { korning_id: "k-10ar-1", status: "klar", framsteg: { klara: 1, totalt: 1 } },
          vyer: [],
        },
      ],
    }),
    scenarioStatus: async () => ({ korningar: [] }),
  };

  try {
    const p = startaLageIdag(t, ["vatten.skyfall"], api);
    assert.equal(arPagaende(t), true);
    assert.match(t.korning?.text ?? "", /Beräknar nuläge … \(0 s\)/);

    // Vänta > 1,1 s så vänteklockan tickar minst en gång innan POST returnerar
    await new Promise((r) => setTimeout(r, 1150));
    assert.ok(
      texter.some((txt) => /Beräknar nuläge … \([1-9]\d* s\)/.test(txt)),
      `Väntade att sekundräknaren skulle ticka över 0 s, fick: ${JSON.stringify(texter)}`,
    );

    slappPost();
    await p;
    assert.equal(arPagaende(t), false);
    assert.equal(t.korning?.klar, true);
    assert.equal(t.korning?.fel, null);
  } finally {
    avlyssna();
  }
});

test("V12: startaLageIdag rensar starttimer och visar tydligt fel om POST kastar", async () => {
  const t = tillstandFor("v12-klocka-fel");
  const api: KorningApi = {
    intervallMs: 20,
    korScenario: async () => ({ fragor: [] }),
    korLageIdag: async () => {
      throw new Error("Servern är upptagen (503)");
    },
    lageIdagStatus: async () => ({ korningar: [] }),
    scenarioStatus: async () => ({ korningar: [] }),
  };

  await startaLageIdag(t, ["vatten.skyfall"], api);
  assert.equal(arPagaende(t), false);
  assert.equal(t.korning?.klar, true);
  assert.match(t.korning?.fel ?? "", /Servern är upptagen/);
});

test("V12: arbetsyta.ts och resultat.ts har progressiv vektorladdning, AbortController och synlig felrad", () => {
  const arSrc = readFileSync(new URL("./arbetsyta.ts", import.meta.url), "utf-8");
  const resSrc = readFileSync(new URL("./resultat.ts", import.meta.url), "utf-8");

  assert.match(arSrc, /new AbortController\(\)/, "arbetsyta.ts ska avbryta inaktuella vektoranrop");
  assert.match(arSrc, /id="ar-lager-not"/, "arbetsyta.ts ska rendera synlig status-/felrad för kartlager");
  assert.match(arSrc, /uppdateraLagerNot\(\)/, "arbetsyta.ts ska uppdatera status-/felraden efter vektorhämtning");
  assert.match(arSrc, /vektorAvbryt\.values\(\)/, "arbetsyta.ts ska avbryta pågående vektoranrop vid avsluta()");

  assert.match(resSrc, /new AbortController\(\)/, "resultat.ts ska avbryta inaktuella vektoranrop");
  assert.match(resSrc, /vektorLaddar/, "resultat.ts ska visa laddningsstatus för vektorlager");
  assert.match(resSrc, /vektorAvbryt\.values\(\)/, "resultat.ts ska avbryta pågående vektoranrop vid avsluta()");
});
