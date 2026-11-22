/* Körs med `npm test`. Lagerpanelens modell: grupper, ordning, opacitet, sparat per projekt, egen vy. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { LagerLista, type Lagring, type LagerDef } from "./lager.ts";
import { giltigKakelmall, lasUnderlag, sparaUnderlag, wmsBrickmall } from "./wms.ts";

function minne(): Lagring & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
}
const def = (id: string, grupp: LagerDef["grupp"], extra: Partial<LagerDef> = {}): LagerDef => ({ id, grupp, namn: id, synlig: true, opacitet: 1, ...extra });

test("synlighet, opacitet och ordning sparas per projekt och läses in igen", () => {
  const m = minne();
  const a = new LagerLista("p1", m);
  a.registrera(def("x", "underlag"));
  a.registrera(def("y", "underlag"));
  a.registrera(def("z", "underlag"));
  a.sattSynlig("y", false);
  a.sattOpacitet("z", 0.4);
  a.flytta("z", -2);
  a.vaxlaHopfalld("geometri");
  assert.deepEqual(a.iGrupp("underlag").map((l) => l.id), ["z", "x", "y"]);

  const b = new LagerLista("p1", m);
  for (const id of ["x", "y", "z"]) b.registrera(def(id, "underlag"));
  assert.deepEqual(b.iGrupp("underlag").map((l) => l.id), ["z", "x", "y"]);
  assert.equal(b.hamta("y")?.synlig, false);
  assert.equal(b.hamta("z")?.opacitet, 0.4);
  assert.equal(b.arHopfalld("geometri"), true);

  const annat = new LagerLista("p2", m);
  annat.registrera(def("y", "underlag"));
  assert.equal(annat.hamta("y")?.synlig, true, "ett annat projekt har egna val");
});

test("dra och släpp flyttar bara inom en grupp", () => {
  const l = new LagerLista("p", minne());
  l.registrera(def("a", "underlag"));
  l.registrera(def("b", "underlag"));
  l.registrera(def("c", "geometri"));
  l.flyttaTill("a", "b", true);
  assert.deepEqual(l.iGrupp("underlag").map((x) => x.id), ["b", "a"]);
  l.flyttaTill("a", "c");
  assert.deepEqual(l.iGrupp("underlag").map((x) => x.id), ["b", "a"]);
  assert.deepEqual(l.iGrupp("geometri").map((x) => x.id), ["c"]);
});

test("lagring som kastar fel stoppar inte kartan (try/catch)", () => {
  const trasig: Lagring = {
    getItem: () => {
      throw new Error("ingen lagring");
    },
    setItem: () => {
      throw new Error("full");
    },
  };
  const l = new LagerLista("p", trasig);
  l.registrera(def("a", "underlag"));
  l.sattSynlig("a", false);
  l.sattOpacitet("a", 0.5);
  assert.equal(l.hamta("a")?.synlig, false);
  const skadad = minne();
  skadad.setItem("floden.kartlager.v1.p", "{inte json");
  assert.doesNotThrow(() => new LagerLista("p", skadad));
  assert.deepEqual(lasUnderlag("p", trasig), []);
  assert.doesNotThrow(() => sparaUnderlag("p", [], trasig));
});

test("ett lager som inte går att tända förblir släckt", () => {
  const l = new LagerLista("p", minne());
  l.registrera(def("orto", "underlag", { ejTillgangligt: "Inget ortofoto hämtat." }));
  l.sattSynlig("orto", true);
  assert.equal(l.hamta("orto")?.synlig, false);
});

test("ett lager kan vara en egen vy (förberett för 3D): visas bara när det är tänt och valt", () => {
  const l = new LagerLista("p", minne());
  const montera = (): { avmontera(): void } => ({ avmontera: () => undefined });
  l.registrera(def("vy3d", "geometri", { synlig: false, egenVy: { id: "vy3d", namn: "3D-vy", montera } }));
  l.registrera(def("karta", "geometri"));
  assert.equal(l.aktivVy, null);
  l.sattAktivVy("karta");
  assert.equal(l.aktivVy, null, "ett vanligt lager är ingen egen vy");
  l.sattSynlig("vy3d", true);
  l.sattAktivVy("vy3d");
  assert.equal((l.aktivVy as LagerDef | null)?.id, "vy3d");
  l.sattSynlig("vy3d", false);
  assert.equal(l.aktivVy, null);
});

test("underlag: brickmall via servern och kontroll av kakelmall, sparat per projekt", () => {
  const mall = wmsBrickmall({ url: "https://exempel.se/wms?x=1", lager: "orto", stod: "3857", version: "1.3.0" });
  assert.ok(mall.startsWith("/api/kartverktyg/wms/bild?"));
  assert.ok(mall.endsWith("&bbox={bbox-epsg-3857}"));
  assert.ok(mall.includes("lager=orto") && mall.includes("crs=3857"));
  assert.equal(giltigKakelmall("https://t.exempel.se/{z}/{x}/{y}.png"), true);
  assert.equal(giltigKakelmall("https://t.exempel.se/{z}/{x}.png"), false);
  assert.equal(giltigKakelmall("javascript:{z}{x}{y}"), false);
  const m = minne();
  sparaUnderlag("p", [{ id: "u1", typ: "wms", namn: "Orto", url: "https://a.se/wms", lager: "orto", stod: "3857", kalla: "a.se" }], m);
  assert.equal(lasUnderlag("p", m)[0]?.namn, "Orto");
  assert.deepEqual(lasUnderlag("q", m), []);
});
