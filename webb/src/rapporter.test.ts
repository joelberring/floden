import assert from "node:assert/strict";
import { test } from "node:test";

import { rapportLista } from "./sidor.ts";
import type { RapportRad } from "./types.ts";

const rad = (over: Partial<RapportRad>): RapportRad => ({
  rapport_id: "r-0123456789ab",
  titel: "Skissunderlag skyfall – demokvarteret",
  projekt: "demokvarteret",
  amne: "skyfall",
  niva: 1,
  skapad: "2026-11-22T08:00:00+00:00",
  antal_pastaenden: 16,
  antal_fragor: 15,
  korningar: ["demo-vatten"],
  kontroll: { godkand: true, tid: "2026-11-22T08:01:00+00:00", antal_fel: 0 },
  filer: { word: "r-0123456789ab.docx", pdf: "r-0123456789ab.pdf" },
  dataklass: "oppet",
  ...over,
});

test("rapportlistan visar grön kontroll och länkar till Word och PDF", () => {
  const html = rapportLista("demokvarteret", [rad({})]);
  assert.match(html, /Kontrollen godkänd/);
  assert.match(html, /rapport-status ok/);
  assert.match(html, /\/api\/projekt\/demokvarteret\/rapporter\/r-0123456789ab\/fil\/word/);
  assert.match(html, /\/fil\/pdf/);
});

test("underkänd kontroll är röd och utan filer sägs det i klartext", () => {
  const html = rapportLista("demokvarteret", [
    rad({ kontroll: { godkand: false, tid: null, antal_fel: 2 }, filer: { word: null, pdf: null } }),
  ]);
  assert.match(html, /rapport-status fel/);
  assert.match(html, /Kontrollen hittade 2 fel/);
  assert.match(html, /Ingen fil exporterad/);
  assert.doesNotMatch(html, /\/fil\//);
});

test("inga rapporter ger en förklaring", () => {
  assert.match(rapportLista("demokvarteret", []), /Inga rapporter än/);
  assert.match(rapportLista("demokvarteret", undefined), /Inga rapporter än/);
});
