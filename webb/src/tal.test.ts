/* Körs med `npm test`. Svensk sifferform i hela gränssnittet: decimalkomma, smalt hårt mellanslag, ingen toFixed. */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { formatera, formateraHogst, talFormat } from "./tal.ts";

test("decimalkomma och smalt hårt mellanslag som tusentalsavgränsare", () => {
  assert.equal(formatera(1234.5, 1), "1 234,5");
  assert.equal(formatera(1234567, 0), "1 234 567");
  assert.equal(formatera(0.359, 3), "0,359");
  assert.equal(formatera(0, 2), "0,00");
  assert.equal(formateraHogst(2.5, 2), "2,5");
  assert.equal(formateraHogst(3, 2), "3");
  assert.equal(talFormat(1).format(12345.67), "12 345,7");
});

test("negativa tal får riktigt minustecken, och inget vanligt hårt mellanslag finns kvar", () => {
  assert.equal(formatera(-0.16, 2), "−0,16");
  for (const v of [1234.5, 99999.99, -1234567.1]) assert.doesNotMatch(formatera(v, 1), / /);
});

// Källfilerna för det som visas i flödet: ingen kod får formatera tal på något annat sätt än tal.ts.
const KATALOGER = ["flode", "karta", "vy3d", "matematik"];
const FILER = ["sidor.ts", "teckenforklaring.ts", "fragor.ts", "kartsida3d.ts", "felvisning.ts", "kontosidor.ts", "kontosidor-html.ts"];

function kallfiler(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const v = join(dir, n);
    if (statSync(v).isDirectory()) return kallfiler(v);
    return v.endsWith(".ts") && !v.endsWith(".test.ts") && !v.endsWith(".generated.ts") ? [v] : [];
  });
}

test("ingen toFixed, toLocaleString eller egen Intl.NumberFormat i det som visas (bara tal.ts får)", () => {
  const rot = new URL(".", import.meta.url).pathname;
  const filer = [...KATALOGER.flatMap((k) => kallfiler(join(rot, k))), ...FILER.map((f) => join(rot, f))];
  const brott: string[] = [];
  for (const f of filer) {
    const text = readFileSync(f, "utf8");
    for (const [i, rad] of text.split("\n").entries()) {
      if (/\.toFixed\(|\.toLocaleString\(|Intl\.NumberFormat/.test(rad)) brott.push(`${f.slice(rot.length)}:${i + 1}`);
    }
  }
  assert.deepEqual(brott, []);
});
