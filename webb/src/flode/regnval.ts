/*
 * Regnväljare för skyfallsberäkningar: rena typer, förval, validering och formatering utan DOM.
 * Klienten räknar aldrig fram regnmängd (mm) ur IDF-parametrar — parametrarna skickas oförändrade
 * till serverns skyfallslösare som står för all fysik och IDF-beräkning (AGENTS.md).
 */
import { esc } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";
import type { RegnForval, RegnParam } from "../karta/kartapi.ts";
import type { RegnPost } from "./nyckeltal.ts";

export type { RegnForval, RegnParam };

export type RegnForvalId = "standard" | "10ar" | "100ar" | "extrem" | "eget";
export type EgetRegnLage = "idf" | "mm";

export interface RegnVal {
  forval: RegnForvalId;
  egetLage: EgetRegnLage;
  aterkomsttid_ar: number | null;
  varaktighet_min: number | null;
  klimatfaktor: number | null;
  regn_mm: number | null;
}

export const STANDARD_REGNVAL: RegnVal = {
  forval: "standard",
  egetLage: "idf",
  aterkomsttid_ar: 100,
  varaktighet_min: 60,
  klimatfaktor: 1.25,
  regn_mm: 100,
};

export interface RegnSnabbval {
  id: RegnForvalId;
  namn: string;
  beskrivning: string;
  param: RegnParam | null;
}

export const REGN_SNABBVAL: ReadonlyArray<RegnSnabbval> = [
  { id: "standard", namn: "Standard", beskrivning: "Projektets förvalda regn", param: null },
  {
    id: "10ar",
    namn: "10-årsregn",
    beskrivning: "10-årsregn · 60 min · kf 1,25",
    param: { aterkomsttid_ar: 10, varaktighet_min: 60, klimatfaktor: 1.25 },
  },
  {
    id: "100ar",
    namn: "100-årsregn",
    beskrivning: "100-årsregn · 60 min · kf 1,25",
    param: { aterkomsttid_ar: 100, varaktighet_min: 60, klimatfaktor: 1.25 },
  },
  {
    id: "extrem",
    namn: "Extremregn",
    beskrivning: "100 mm total nederbörd",
    param: { regn_mm: 100 },
  },
  { id: "eget", namn: "Eget", beskrivning: "Ange återkomsttid eller mm regn", param: null },
];

/** Skapar ett nytt RegnVal med valt snabbval, med bevarade egna fält. */
export function medRegnForval(aktuellt: RegnVal, forval: RegnForvalId): RegnVal {
  return { ...aktuellt, forval };
}

/** Tolkar ett decimaltal med komma eller punkt (tomt → null, ogiltigt → NaN). */
export function tolkaRegnTal(text: string): number | null {
  const rent = text.trim().replace(/[\s\u00a0\u202f]/g, "").replace(",", ".");
  if (rent === "") return null;
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(rent)) return Number.NaN;
  const n = Number(rent);
  return Number.isFinite(n) ? n : Number.NaN;
}

/**
 * Validerar regnvalet mot serverns strikta gränser:
 * - regn_mm i (0, 500]
 * - aterkomsttid_ar i [1, 10000]
 * - varaktighet_min i [5, 2880]
 * - klimatfaktor i [0.5, 3.0]
 * Returnerar feltext på svenska eller null om allt är giltigt.
 */
export function valideraRegnVal(val: RegnVal): string | null {
  if (val.forval !== "eget") return null;
  if (val.egetLage === "mm") {
    const mm = val.regn_mm;
    if (mm === null || !Number.isFinite(mm) || mm <= 0 || mm > 500) {
      return "Regnmängden måste vara större än 0 och högst 500 mm.";
    }
    return null;
  }
  const ar = val.aterkomsttid_ar;
  const min = val.varaktighet_min;
  const kf = val.klimatfaktor;
  if (ar === null || !Number.isFinite(ar) || ar < 1 || ar > 10000) {
    return "Återkomsttiden måste vara mellan 1 och 10 000 år.";
  }
  if (min === null || !Number.isFinite(min) || min < 5 || min > 2880) {
    return "Varaktigheten måste vara mellan 5 och 2 880 minuter.";
  }
  if (kf === null || !Number.isFinite(kf) || kf < 0.5 || kf > 3.0) {
    return "Klimatfaktorn måste vara mellan 0,5 och 3,0.";
  }
  return null;
}

/** Omvandlar regnvalet till JSON-kroppen `regn` för servern (`null` = projektets standard). */
export function tillRegnParam(val: RegnVal): RegnParam | null {
  if (val.forval === "standard") return null;
  const snabb = REGN_SNABBVAL.find((x) => x.id === val.forval);
  if (snabb?.param) return { ...snabb.param };
  if (val.egetLage === "mm") {
    return val.regn_mm !== null && Number.isFinite(val.regn_mm) ? { regn_mm: val.regn_mm } : null;
  }
  return {
    aterkomsttid_ar: val.aterkomsttid_ar,
    varaktighet_min: val.varaktighet_min,
    klimatfaktor: val.klimatfaktor,
  };
}

/** Deterministisk signatur för valt regn och avtappning (för att veta om resultat stämmer med inställningen). */
export function regnSignatur(val: RegnVal, avtappning: number | null): string {
  return JSON.stringify({ regn: tillRegnParam(val), avtappning: avtappning ?? null });
}

/**
 * Enkel, 1-radig etikett över exakt vilket regn kartan visar (ur körningens `antaganden.regn[0]`
 * eller `info.regn_forval`). Inga tal räknas fram i klienten.
 */
export function regnEtikett(r: RegnPost | RegnForval | null | undefined): string | null {
  if (!r) return null;
  const delar: string[] = [];
  if (typeof r.aterkomsttid_ar === "number" && Number.isFinite(r.aterkomsttid_ar)) {
    delar.push(`${formateraHogst(r.aterkomsttid_ar, 0)}-årsregn`);
  }
  if (typeof r.varaktighet_min === "number" && Number.isFinite(r.varaktighet_min)) {
    delar.push(`${formateraHogst(r.varaktighet_min, 0)} min`);
  }
  if (typeof r.klimatfaktor === "number" && Number.isFinite(r.klimatfaktor)) {
    delar.push(`kf ${formateraHogst(r.klimatfaktor, 2)}`);
  }
  const harMm = typeof r.regn_mm === "number" && Number.isFinite(r.regn_mm);
  if (delar.length === 0) {
    if (harMm) return `Regn ${formateraHogst(r.regn_mm as number, 1)} mm`;
    const kalla = ("rapport" in r ? r.rapport : null) || r.kalla;
    return kalla ? String(kalla) : null;
  }
  const grund = delar.join(" · ");
  return harMm ? `${grund} = ${formateraHogst(r.regn_mm as number, 1)} mm` : grund;
}

/** Kort 1-radig sammanfattning av den valda regninställningen (inför körning). */
export function valdRegnText(val: RegnVal, forval: RegnForval | null | undefined, avtappning: number | null): string {
  let bas: string;
  if (val.forval === "standard") {
    const f = regnEtikett(forval);
    bas = f ? `Standard (${f})` : "Standard (projektets förval)";
  } else {
    bas = regnEtikett(tillRegnParam(val)) ?? "Eget regn";
  }
  const avt = avtappning !== null && avtappning > 0 ? ` · avtappning ${formatera(avtappning, 1)} mm/h` : "";
  return `${bas}${avt}`;
}

/** Ren HTML för regnväljaren i sidopanelen. */
export function regnValHtml(
  val: RegnVal,
  forval: RegnForval | null | undefined,
  avtappning: number | null,
  fel: string | null,
  avtappningFel: boolean,
): string {
  const knappar = REGN_SNABBVAL.map(
    (s) =>
      `<button type="button" data-regn-forval="${s.id}" aria-pressed="${val.forval === s.id}" title="${esc(s.beskrivning)}">${esc(s.namn)}</button>`,
  ).join("");
  const numStr = (n: number | null, dec = 2): string => (n === null || !Number.isFinite(n) ? "" : formateraHogst(n, dec));
  const egetDel =
    val.forval === "eget"
      ? `<div class="ar-regn-eget">
          <div class="flode-segment liten" role="group" aria-label="Inmatningssätt">
            <button type="button" data-regn-eget="idf" aria-pressed="${val.egetLage === "idf"}">Återkomsttid (IDF)</button>
            <button type="button" data-regn-eget="mm" aria-pressed="${val.egetLage === "mm"}">Direkt (mm)</button>
          </div>
          ${
            val.egetLage === "mm"
              ? `<div class="flode-falt"><label for="ar-regn-mm">Regnmängd (mm)</label>
                  <input id="ar-regn-mm" type="text" inputmode="decimal" data-regn-falt="regn_mm" value="${esc(numStr(val.regn_mm, 1))}" placeholder="t.ex. 100" autocomplete="off" /></div>`
              : `<div class="ar-regn-grid">
                  <div class="flode-falt"><label for="ar-regn-ar">Återkomsttid (år)</label>
                    <input id="ar-regn-ar" type="text" inputmode="decimal" data-regn-falt="aterkomsttid_ar" value="${esc(numStr(val.aterkomsttid_ar, 0))}" placeholder="100" autocomplete="off" /></div>
                  <div class="flode-falt"><label for="ar-regn-min">Varaktighet (min)</label>
                    <input id="ar-regn-min" type="text" inputmode="decimal" data-regn-falt="varaktighet_min" value="${esc(numStr(val.varaktighet_min, 0))}" placeholder="60" autocomplete="off" /></div>
                  <div class="flode-falt"><label for="ar-regn-kf">Klimatfaktor</label>
                    <input id="ar-regn-kf" type="text" inputmode="decimal" data-regn-falt="klimatfaktor" value="${esc(numStr(val.klimatfaktor, 2))}" placeholder="1,25" autocomplete="off" /></div>
                </div>`
          }
          <div class="flode-falt"><label for="ar-regn-avt">Avtappning till ledningsnät (mm/h, valfritt)</label>
            <input id="ar-regn-avt" type="text" inputmode="decimal" data-regn-falt="avtappning" value="${avtappning === null ? "" : esc(formateraHogst(avtappning, 1))}" placeholder="0 (ledningsnät fullt)" autocomplete="off" /></div>
        </div>`
      : "";
  const felHtml = fel
    ? `<p class="flode-fel flode-liten" role="alert">${esc(fel)}</p>`
    : avtappningFel
      ? `<p class="flode-fel flode-liten" role="alert">Avtappningen måste vara minst 0 mm/h.</p>`
      : "";
  return `<section class="ar-regn" aria-label="Regn">
    <div class="ar-sektion-rad">
      <span class="ar-sektion-rubrik">Regn</span>
      <span class="ar-regn-sammanfattning flode-liten flode-dampad">${esc(valdRegnText(val, forval, avtappning))}</span>
    </div>
    <div class="flode-chips liten ar-regn-chips" role="group" aria-label="Snabbval för regn">${knappar}</div>
    ${egetDel}
    ${felHtml}
  </section>`;
}

/** Läser sparat RegnVal från okänd JSON (t.ex. sessionStorage) med säkra förval. */
export function lasRegnVal(ra: unknown): RegnVal {
  if (!ra || typeof ra !== "object") return { ...STANDARD_REGNVAL };
  const o = ra as Record<string, unknown>;
  const forval = o.forval === "10ar" || o.forval === "100ar" || o.forval === "extrem" || o.forval === "eget" ? o.forval : "standard";
  const egetLage: EgetRegnLage = o.egetLage === "mm" ? "mm" : "idf";
  const num = (v: unknown, def: number | null): number | null => (typeof v === "number" && Number.isFinite(v) ? v : def);
  return {
    forval,
    egetLage,
    aterkomsttid_ar: num(o.aterkomsttid_ar, STANDARD_REGNVAL.aterkomsttid_ar),
    varaktighet_min: num(o.varaktighet_min, STANDARD_REGNVAL.varaktighet_min),
    klimatfaktor: num(o.klimatfaktor, STANDARD_REGNVAL.klimatfaktor),
    regn_mm: num(o.regn_mm, STANDARD_REGNVAL.regn_mm),
  };
}
