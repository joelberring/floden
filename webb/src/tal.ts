/*
 * Tal i gränssnittet: svensk sifferform med decimalkomma, smalt hårt mellanslag (U+202F) som
 * tusentalsavgränsare och riktigt minustecken (U+2212). Intl ger ett vanligt hårt mellanslag
 * (U+00A0) för sv-SE; här byts det mot det smala så att ett tal aldrig bryts över två rader och
 * ser likadant ut överallt. Alla tal som visas ska gå genom de här funktionerna (ingen `toFixed`,
 * ingen `toLocaleString`).
 */

const SMALT_HART_MELLANSLAG = " ";
const fast = new Map<string, Intl.NumberFormat>();

function format(min: number, max: number): Intl.NumberFormat {
  const nyckel = `${min}:${max}`;
  let f = fast.get(nyckel);
  if (!f) {
    f = new Intl.NumberFormat("sv-SE", { minimumFractionDigits: min, maximumFractionDigits: max });
    fast.set(nyckel, f);
  }
  return f;
}

const byt = (text: string): string => text.replace(/[  ]/g, SMALT_HART_MELLANSLAG);

/** Exakt `decimaler` decimaler: 1234,5 med en decimal blir "1 234,5". */
export function formatera(v: number, decimaler: number): string {
  return byt(format(decimaler, decimaler).format(v));
}

/** Högst `decimaler` decimaler (inga avslutande nollor): 2,5 och 3 blir "2,5" och "3". */
export function formateraHogst(v: number, decimaler: number): string {
  return byt(format(0, decimaler).format(v));
}

/** Format med `.format(v)` för kod som redan arbetar med Intl-objekt; högst `decimaler` decimaler. */
export function talFormat(decimaler: number): { format(v: number): string } {
  return { format: (v: number) => formateraHogst(v, decimaler) };
}
